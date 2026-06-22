"""burnstop hook entrypoint — the part Claude Code actually runs.

Registered as a Claude Code ``PreToolUse`` hook (matcher ``*``). Every tool
call passes through here first; since an agent can't make progress without
eventually calling a tool, this is a universal choke point. On each call it
recomputes the session's token spend from the transcripts (parent + subagents)
and, if the armed budget is exceeded or the agent is looping, prints
``{"continue": false}`` to halt the run **cleanly**. No proxy, no API key, no
network, no OAuth forwarding — 100% native, ToS-safe.

It is also registered on ``SubagentStop`` purely for symmetry/accounting; it
never blocks there (a finished subagent can't be un-run).

Budget resolution (most specific wins):
  1. ``BURNSTOP_BUDGET`` env var  -> caps the WHOLE session (baseline 0).
  2. arm file ``~/.claude/burnstop/<session_id>.json`` -> caps everything
     AFTER the arm point (baseline = spend at arm time). Written by
     ``cli.py arm`` from inside the session.
  3. neither -> burnstop stays completely out of the way (exit 0).

Known, bounded limitation: spend is read from completed turns, so the hook is
~one turn behind, and a subagent already mid-run can't be killed (only the
parent halts / new subagent spawns are blocked). Set a conservative cap and a
per-subagent ``maxTurns`` to bound the overshoot. See AGENTS.md.

Stdlib only, Python 3.8+.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import meter  # same-dir import: Python puts the script's dir on sys.path[0]

WARN_PCT = float(os.environ.get("BURNSTOP_WARN_PCT", "0.8"))
LOOP_THRESHOLD = int(os.environ.get("BURNSTOP_LOOP", "5"))


def arm_path(session_id):
    """Per-session arm config. Keyed by session_id so concurrent sessions never
    collide, and kept out of any repo (lives under ``~/.claude``)."""
    return Path.home() / ".claude" / "burnstop" / f"{session_id}.json"


def config_path():
    """Global burnstop config (holds the /goal auto-arm default)."""
    return Path.home() / ".claude" / "burnstop" / "config.json"


def read_config():
    path = config_path()
    if path.is_file():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
    return {}


def goal_default():
    """Budget auto-armed on /goal. Resolution: env > config file > built-in $50."""
    env = os.environ.get("BURNSTOP_GOAL_DEFAULT")
    if env:
        return env
    return read_config().get("goal_default", "$50")


def parse_budget(value):
    """Parse a budget into ``(amount, unit)`` or ``None``. Forgiving about form.

    Dollars (a ``$`` anywhere, or a ``usd`` suffix): ``$1`` / ``1$`` / ``$ 1`` /
    ``$1.50`` / ``1.50$`` / ``2.50usd`` -> ``(float, "usd")``.
    Tokens: ``200k`` / ``200K`` / ``5m`` / ``5 M`` / ``1.5m`` / ``50_000`` -> ``(int, "tok")``.
    """
    if value is None:
        return None
    # case-insensitive; drop separators and spaces so "$ 1" / "5 M" / "1,250,000" all work
    text = str(value).strip().lower().replace("_", "").replace(",", "").replace(" ", "")
    if not text:
        return None

    if "$" in text or text.endswith("usd"):
        try:
            amount = float(text.replace("$", "").replace("usd", ""))
        except ValueError:
            return None
        return (round(amount, 4), "usd") if amount > 0 else None

    mult = 1
    if text.endswith("k"):
        mult, text = 1_000, text[:-1]
    elif text.endswith("m"):
        mult, text = 1_000_000, text[:-1]
    try:
        n = int(round(float(text) * mult))
    except ValueError:
        return None
    return (n, "tok") if n > 0 else None


def resolve_budget(session_id):
    """Return ``(cap, baseline, source, unit)`` or ``(None, 0, None, None)``."""
    env = parse_budget(os.environ.get("BURNSTOP_BUDGET"))
    if env:
        return env[0], 0, "env", env[1]
    path = arm_path(session_id)
    if path.is_file():
        try:
            cfg = json.loads(path.read_text(encoding="utf-8"))
            return float(cfg["cap"]), float(cfg.get("baseline", 0)), "file", cfg.get("unit", "tok")
        except (OSError, ValueError, KeyError, TypeError):
            return None, 0, None, None
    return None, 0, None, None


def fmt_tokens(n):
    """Compact token count: ``850``, ``1.5k``, ``200k``, ``1.50M``, ``119.14M``.

    Thousands in ``k`` (up to one decimal), millions in ``M`` with two decimals.
    """
    n = float(n)
    if n >= 1_000_000:
        return f"{n / 1_000_000:.2f}M"
    if n >= 1_000:
        v = n / 1_000
        return f"{v:.0f}k" if v == int(v) else f"{v:.1f}k"
    return f"{n:,.0f}"


def fmt(amount, unit):
    """Human-friendly budget value: ``$1.23``, ``200k tok``, ``1.50M tok``."""
    if unit == "usd":
        return f"${amount:,.2f}"
    return f"{fmt_tokens(amount)} tok"


def _emit(obj):
    sys.stdout.write(json.dumps(obj))
    sys.stdout.flush()


def evaluate(spent, cap, unit, signatures):
    """Pure verdict (no I/O). ``spent`` is already net of baseline.

    Returns ``("halt", reason)`` | ``("warn", message)`` | ``("allow", None)``.
    """
    if meter.is_looping(signatures, LOOP_THRESHOLD):
        return (
            "halt",
            f"burnstop: loop detected: the last {LOOP_THRESHOLD} tool calls were "
            f"identical. Halting (spent {fmt(spent, unit)} this session).",
        )
    if spent >= cap:
        return (
            "halt",
            f"burnstop: budget exhausted: {fmt(spent, unit)} / {fmt(cap, unit)} this "
            f"session (incl. subagents). Halting.",
        )
    if spent >= cap * WARN_PCT:
        return (
            "warn",
            f"burnstop: {fmt(spent, unit)} / {fmt(cap, unit)} ({spent / cap:.0%}) "
            f"approaching budget.",
        )
    return ("allow", None)


def render(verdict, message, event):
    """Turn a verdict into the hook-output dict for ``event`` (or None to allow).

    The halt is event-specific on purpose:
      * ``Stop`` / ``SubagentStop`` -> top-level ``continue: false``. This is the
        ONLY reliable session stop, and it beats an active ``/goal`` (which is
        itself a Stop hook returning "continue"): a top-level ``continue: false``
        takes precedence over any event-specific "continue" decision, so the
        goal cannot resurrect the loop.
      * ``PreToolUse`` -> a permission **deny** (freezes the agent mid-turn so it
        can't keep spending before the next Stop), plus ``continue: false`` as a
        belt-and-suspenders in case the running build honors it here too.
    """
    if verdict == "allow":
        return None
    if verdict == "warn":
        return {"systemMessage": message}
    # verdict == "halt"
    if event == "PreToolUse":
        return {
            "continue": False,
            "stopReason": message,
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": message,
            },
        }
    return {"continue": False, "stopReason": message}


def _is_burnstop_cli(payload):
    """True if this PreToolUse is a Bash call running burnstop's own CLI.

    The /budget commands run `python .../burnstop/cli.py ...` via Bash; we must
    never block those, so you can always check status or disarm even when the
    session is over budget.
    """
    if payload.get("tool_name") != "Bash":
        return False
    cmd = ((payload.get("tool_input") or {}).get("command") or "").lower()
    return "burnstop" in cmd and "cli.py" in cmd


def main():
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        return 0  # nothing parseable -> never interfere

    session_id = payload.get("session_id") or os.environ.get("CLAUDE_CODE_SESSION_ID")
    if not session_id:
        return 0

    event = payload.get("hook_event_name", "PreToolUse")
    if event == "PreToolUse" and _is_burnstop_cli(payload):
        return 0  # never block burnstop's own management commands

    cap, baseline, _source, unit = resolve_budget(session_id)
    if not cap:
        return 0  # not armed -> stay out of the way

    spend = meter.session_spend(session_id)
    raw = spend["total_cost"] if unit == "usd" else spend["total"]
    spent = max(0, raw - baseline)
    signatures = meter.recent_tool_signatures(session_id)

    verdict, message = evaluate(spent, cap, unit, signatures)
    out = render(verdict, message, event)
    if out is not None:
        _emit(out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
