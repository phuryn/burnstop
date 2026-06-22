"""burnstop CLI — arm/disarm a session budget, check status, install the hook.

Run from *inside* a Claude Code session so it can read ``CLAUDE_CODE_SESSION_ID``
(the env var Claude Code exports to every child process). That is what makes
mid-session arming work: a plain shell can't know which session it belongs to,
but a command launched inside the session inherits the id automatically.

    python cli.py arm 200k        # cap everything from now (incl. subagents)
    python cli.py status          # spend vs cap for this session
    python cli.py disarm          # remove the cap
    python cli.py install         # write the PreToolUse/SubagentStop hook into settings.json
    python cli.py --version

Stdlib only, Python 3.8+.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import hook
import meter
from hook import arm_path, fmt, parse_budget, resolve_budget

HOOK_PATH = str(Path(__file__).resolve().with_name("hook.py"))
DISPATCH_PATH = str(Path(__file__).resolve().with_name("dispatch.py"))
COMMAND_DIR = Path(__file__).resolve().parent / "commands"


def _session_id(explicit=None):
    return explicit or os.environ.get("CLAUDE_CODE_SESSION_ID")


def _need_session(sid):
    if not sid:
        print(
            "burnstop: no session id. Run this inside a Claude Code session "
            "(CLAUDE_CODE_SESSION_ID is unset here), or pass --session <id>.",
            file=sys.stderr,
        )
        return False
    return True


def cmd_arm(args):
    sid = _session_id(args.session)
    if not _need_session(sid):
        return 2
    parsed = parse_budget(args.budget)
    if not parsed:
        print(
            f"burnstop: can't parse budget {args.budget!r} "
            f"(try $1, $0.50, 200k, 1.5m, 50000).",
            file=sys.stderr,
        )
        return 2
    cap, unit = parsed
    spend = meter.session_spend(sid)
    baseline = spend["total_cost"] if unit == "usd" else spend["total"]
    path = arm_path(sid)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "cap": cap,
                "baseline": baseline,
                "unit": unit,
                "armed_at": datetime.now(timezone.utc).isoformat(),
                "session_id": sid,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"burnstop armed: {fmt(cap, unit)} from now (baseline {fmt(baseline, unit)}); session {sid[:8]}.")
    print("  everything later THIS SESSION, incl. subagents, counts toward the cap.")
    return 0


def cmd_disarm(args):
    sid = _session_id(args.session)
    if not _need_session(sid):
        return 2
    path = arm_path(sid)
    if path.is_file():
        path.unlink()
        print(f"burnstop disarmed; session {sid[:8]}.")
    else:
        print(f"burnstop: session {sid[:8]} was not armed.")
    return 0


def cmd_reset(args):
    """Re-baseline the current cap to now: the spend-since counter goes back to 0,
    the cap and unit are kept. Use to give a fresh allowance mid-session (e.g.,
    starting a new goal) without re-typing the budget."""
    sid = _session_id(args.session)
    if not _need_session(sid):
        return 2
    path = arm_path(sid)
    if not path.is_file():
        print(f"burnstop: session {sid[:8]} is not armed via `arm`; nothing to reset.", file=sys.stderr)
        return 2
    try:
        cfg = json.loads(path.read_text(encoding="utf-8"))
        cap, unit = float(cfg["cap"]), cfg.get("unit", "tok")
    except (OSError, ValueError, KeyError):
        print(f"burnstop: arm file for {sid[:8]} is unreadable.", file=sys.stderr)
        return 2
    spend = meter.session_spend(sid)
    cfg["baseline"] = spend["total_cost"] if unit == "usd" else spend["total"]
    cfg["armed_at"] = datetime.now(timezone.utc).isoformat()
    path.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
    print(f"burnstop reset: spend-since back to 0, cap {fmt(cap, unit)} kept; session {sid[:8]}.")
    return 0


def cmd_default(args):
    """Show or set the budget auto-armed on /goal (persisted globally)."""
    if args.budget is None:
        current = hook.read_config().get("goal_default")
        print(f"burnstop /goal auto-arm default: {current or '$50 (built-in)'}")
        return 0
    parsed = parse_budget(args.budget)
    if not parsed:
        print(f"burnstop: can't parse {args.budget!r} (try $50, 1m, 500k).", file=sys.stderr)
        return 2
    path = hook.config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    cfg = hook.read_config()
    cfg["goal_default"] = args.budget
    path.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
    print(f"burnstop: /goal will auto-arm {fmt(*parsed)} by default.")
    return 0


def cmd_status(args):
    sid = _session_id(args.session)
    if not _need_session(sid):
        return 2
    spend = meter.session_spend(sid)
    cap, baseline, source, unit = resolve_budget(sid)
    print(f"session    {sid}")
    print(
        f"spend      {fmt(spend['total'], 'tok')} / ${spend['total_cost']:,.2f}  "
        f"({spend['turns']} turns, {fmt(spend['subagent_total'], 'tok')} from subagents)"
    )
    if cap:
        raw = spend["total_cost"] if unit == "usd" else spend["total"]
        used = max(0, raw - baseline)
        pct = used / cap if cap else 0
        print(f"budget     {fmt(used, unit)} / {fmt(cap, unit)} ({pct:.0%}) since arm")
        print(f"remaining  {fmt(max(0, cap - used), unit)}")
    else:
        print("budget     not armed (this session isn't capped)")
    print("")
    print("set one    /budget-arm $1   (dollars)   or   /budget-arm 200k   (tokens)")
    print("clear      /budget-disarm           goals  /goal auto-arms $50 (change: /budget-default $100)")
    return 0


def _settings_path(scope):
    if scope == "project":
        return Path.cwd() / ".claude" / "settings.json"
    return Path.home() / ".claude" / "settings.json"


def cmd_install(args):
    """Merge the burnstop hooks into settings.json (idempotent)."""
    path = _settings_path(args.scope)
    path.parent.mkdir(parents=True, exist_ok=True)
    settings = {}
    if path.is_file():
        try:
            settings = json.loads(path.read_text(encoding="utf-8")) or {}
        except (OSError, ValueError):
            print(f"burnstop: {path} exists but isn't valid JSON; fix it first.", file=sys.stderr)
            return 2

    # Claude Code runs hook commands through bash (even on Windows), and bash eats
    # backslashes in C:\Python313\python.exe -> "command not found". Use forward
    # slashes: bash accepts C:/Python313/python.exe, and this is a no-op on POSIX.
    py = sys.executable.replace("\\", "/")
    hooks = settings.setdefault("hooks", {})

    # hook.py = the fuse (Stop halts and beats /goal; PreToolUse denies mid-turn;
    # SubagentStop reconciles subagent spend). dispatch.py = /goal auto-arm via
    # UserPromptExpansion. (No UserPromptSubmit: current Claude Code doesn't honor
    # a hook blocking a slash command, so /budget runs the CLI directly instead.)
    events = [
        ("PreToolUse", "*", HOOK_PATH),
        ("Stop", None, HOOK_PATH),
        ("SubagentStop", None, HOOK_PATH),
        ("UserPromptExpansion", None, DISPATCH_PATH),
    ]
    changed = False
    # clean slate: drop any prior burnstop entries across ALL events (repairs old
    # paths and removes events we no longer register, like UserPromptSubmit).
    for ev in list(hooks.keys()):
        kept = [g for g in hooks[ev] if not any("burnstop" in h.get("command", "").lower() for h in g.get("hooks", []))]
        if len(kept) != len(hooks[ev]):
            changed = True
        if kept:
            hooks[ev] = kept
        else:
            del hooks[ev]
    # add the current burnstop hooks
    for event, matcher, script in events:
        command = f'{py} "{script.replace(chr(92), "/")}"'
        group = {"hooks": [{"type": "command", "command": command}]}
        if matcher is not None:
            group["matcher"] = matcher
        hooks.setdefault(event, []).append(group)
        changed = True

    # install the /budget* slash commands. Each one instructs the agent to run
    # the CLI and show the output. We do NOT rely on a hook intercepting the
    # prompt: current Claude Code fires UserPromptSubmit on the raw "/budget-x"
    # before expansion, so the sentinel was never matched. Running the CLI as a
    # Bash command is reliable; the PreToolUse hook exempts burnstop's own CLI,
    # so /budget works even when the session is over budget.
    cli_fwd = str(Path(__file__).resolve()).replace("\\", "/")
    commands = {
        "budget": ("status", "burnstop - show this session's budget status"),
        "budget-arm": ("arm $ARGUMENTS", "burnstop - cap this session's budget from now ($ or tokens)"),
        "budget-status": ("status", "burnstop - show spend vs cap (tokens and $)"),
        "budget-reset": ("reset", "burnstop - fresh allowance, keep the cap"),
        "budget-disarm": ("disarm", "burnstop - remove the budget cap"),
        "budget-default": ("default $ARGUMENTS", "burnstop - set the budget auto-armed on /goal"),
    }
    cmd_dir = (Path.cwd() if args.scope == "project" else Path.home()) / ".claude" / "commands"
    cmd_dir.mkdir(parents=True, exist_ok=True)
    for name, (action, desc) in commands.items():
        body = (
            f"---\ndescription: {desc}\n---\n\n"
            "Run this exact command with the Bash tool and show ONLY its raw output "
            "to the user (no preamble, no commentary):\n\n"
            f'`{py} "{cli_fwd}" {action}`\n'
        )
        (cmd_dir / f"{name}.md").write_text(body, encoding="utf-8")
    changed = True

    path.write_text(json.dumps(settings, indent=2), encoding="utf-8")
    print(f"burnstop: installed hooks + /budget commands + /goal auto-arm in {path}")
    print(f"  commands in {cmd_dir} (/budget-arm, -status, -reset, -disarm, -default)")
    print("  RESTART Claude Code (or open a new session) so the hooks take effect.")
    print("  then:  /budget-arm $1   (or just /goal ... and it auto-arms $50)")
    return 0


def build_parser():
    parser = argparse.ArgumentParser(prog="burnstop", description="Native token-budget fuse for Claude Code.")
    parser.add_argument("--version", action="version", version=f"burnstop {meter.VERSION}")
    parser.add_argument("--session", help="session id override (default: $CLAUDE_CODE_SESSION_ID)")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_arm = sub.add_parser("arm", help="cap everything from now on (incl. subagents)")
    p_arm.add_argument("budget", help="token budget, e.g. 200k / 1.5m / 50000")
    p_arm.set_defaults(func=cmd_arm)

    sub.add_parser("disarm", help="remove the cap for this session").set_defaults(func=cmd_disarm)
    sub.add_parser("reset", help="re-baseline: spend-since back to 0, keep the cap").set_defaults(func=cmd_reset)
    sub.add_parser("status", help="show spend vs cap for this session").set_defaults(func=cmd_status)

    p_default = sub.add_parser("default", help="show/set the budget auto-armed on /goal")
    p_default.add_argument("budget", nargs="?", help="e.g. $100 / 1m (omit to show current)")
    p_default.set_defaults(func=cmd_default)

    p_install = sub.add_parser("install", help="write the hook into settings.json")
    p_install.add_argument("--scope", choices=("user", "project"), default="user",
                           help="user = ~/.claude/settings.json (default); project = ./.claude/settings.json")
    p_install.set_defaults(func=cmd_install)
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
