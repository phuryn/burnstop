"""burnstop.meter — read-only token accounting for a Claude Code session.

Sums token usage from Claude Code's own local JSONL transcripts for a given
session, INCLUDING dispatched subagents, with **zero writes**. The transcripts
(`~/.claude/projects/<project>/<session_id>.jsonl` and sibling
`.../subagents/*.jsonl`) are Claude Code's canonical record, so burnstop never
maintains a ledger it could corrupt — it recomputes spend live on each call.
That also sidesteps read-modify-write races when the parent and several
subagents fire hooks concurrently: every hook just reads.

Schema notes (verified against real transcripts, June 2026):
- One JSON object per line. Assistant API responses carry ``message.usage`` with
  ``input_tokens`` / ``output_tokens`` / ``cache_read_input_tokens`` /
  ``cache_creation_input_tokens``.
- Claude Code writes multiple records per API response; only the LAST record for
  a given ``message.id`` has the final tallies. Dedupe by message id, keep last
  (same invariant as phuryn/claude-usage's scanner — don't sum across records
  that share a message id).
- Subagent ("sidechain") turns carry ``isSidechain: true`` and the SAME parent
  ``sessionId``; they may live inline in the parent transcript or in a sibling
  ``subagents/*.jsonl``. Both are summed; dedupe by message id makes the union
  safe even if a turn appears in both places.

Stdlib only, Python 3.8+.
"""
from __future__ import annotations

import glob
import hashlib
import json
import os
from pathlib import Path

VERSION = "1.0.0"

# The four billable token fields. A token budget is compared against their sum.
TOKEN_KEYS = (
    "input_tokens",
    "output_tokens",
    "cache_read_input_tokens",
    "cache_creation_input_tokens",
)

# Per-MTok USD list prices (Anthropic, June 2026). cache_read = 0.1x input,
# cache_write (5m creation) = 1.25x input — same structure as phuryn/claude-usage.
# A dollar budget needs per-model pricing because a turn's cost depends on which
# model ran it: Opus output is 5x Haiku's. Cost is therefore computed PER TURN
# (each turn knows its own model) and summed; aggregating tokens first and
# applying one price is wrong for sessions that span models.
PRICING = {
    "opus":   {"input": 5.0,  "output": 25.0, "cache_read": 0.50, "cache_write": 6.25},
    "sonnet": {"input": 3.0,  "output": 15.0, "cache_read": 0.30, "cache_write": 3.75},
    "haiku":  {"input": 1.0,  "output": 5.0,  "cache_read": 0.10, "cache_write": 1.25},
    "fable":  {"input": 10.0, "output": 50.0, "cache_read": 1.00, "cache_write": 12.50},
    "mythos": {"input": 10.0, "output": 50.0, "cache_read": 1.00, "cache_write": 12.50},
}

# Order matters only for readability; keys are matched as substrings of the
# model id (handles date-suffixed ids like claude-opus-4-8-20260601).
_PRICING_KEYS = ("opus", "sonnet", "haiku", "fable", "mythos")


def get_pricing(model):
    """Resolve a model id to its price row, or None for unknown / local / 3rd-party
    models (which then cost $0 — intentional, so they aren't billed at Anthropic
    rates and never trip a dollar budget)."""
    if not model:
        return None
    low = model.lower()
    for key in _PRICING_KEYS:
        if key in low:
            return PRICING[key]
    return None


def turn_cost(usage, model):
    """USD cost of one turn, priced by its own model. Unknown model -> $0."""
    price = get_pricing(model)
    if not price or not isinstance(usage, dict):
        return 0.0
    return (
        int(usage.get("input_tokens") or 0) * price["input"]
        + int(usage.get("output_tokens") or 0) * price["output"]
        + int(usage.get("cache_read_input_tokens") or 0) * price["cache_read"]
        + int(usage.get("cache_creation_input_tokens") or 0) * price["cache_write"]
    ) / 1_000_000


def projects_root(custom=None):
    """Where Claude Code stores per-session transcripts.

    Override order: explicit arg > ``CLAUDE_PROJECTS_DIR`` env > default
    ``~/.claude/projects``. The env override exists so tests never touch the
    user's real transcripts.
    """
    if custom:
        return Path(custom)
    env = os.environ.get("CLAUDE_PROJECTS_DIR")
    if env:
        return Path(env)
    return Path.home() / ".claude" / "projects"


def _iter_records(path):
    """Yield parsed JSON objects from a JSONL file, skipping blank/bad lines."""
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    yield json.loads(line)
                except json.JSONDecodeError:
                    continue
    except OSError:
        return


def find_transcripts(session_id, root=None):
    """All JSONL files that may hold turns for ``session_id``.

    The main transcript is ``<project>/<session_id>.jsonl`` in any project dir;
    subagent turns may also live in a sibling ``subagents/*.jsonl``. We return
    both and let :func:`session_spend` filter by ``sessionId`` and dedupe.
    """
    base = projects_root(root)
    paths = []
    for main in glob.glob(str(base / "*" / f"{session_id}.jsonl")):
        paths.append(main)
        sub = Path(main).parent / "subagents"
        if sub.is_dir():
            paths.extend(str(p) for p in sorted(sub.glob("*.jsonl")))
    return paths


def turn_tokens(usage):
    """Sum the billable token fields of one ``message.usage`` block."""
    if not isinstance(usage, dict):
        return 0
    return sum(int(usage.get(k) or 0) for k in TOKEN_KEYS)


def session_spend(session_id, root=None, transcripts=None):
    """Recompute cumulative token spend for a session, incl. subagents.

    Returns a dict; ``total`` is the number a budget is compared against.
    Dedupes by ``message.id`` (keep last) so streaming re-writes and turns that
    appear in both the parent and a subagents file are counted once.
    """
    paths = transcripts if transcripts is not None else find_transcripts(session_id, root)

    last = {}        # message_id -> (usage, is_sidechain, model)
    no_id_total = 0  # usage records lacking a message id (rare) — counted raw
    for path in paths:
        for rec in _iter_records(path):
            sid = rec.get("sessionId")
            if sid and sid != session_id:
                continue  # a subagents/ file can hold a different session
            msg = rec.get("message") or {}
            usage = msg.get("usage")
            if not usage:
                continue
            mid = msg.get("id")
            sidechain = bool(rec.get("isSidechain"))
            if mid:
                last[mid] = (usage, sidechain, msg.get("model"))
            else:
                no_id_total += turn_tokens(usage)

    total = no_id_total
    total_cost = 0.0
    sub_total = 0
    sub_cost = 0.0
    turns = 0
    by_model = {}
    for usage, sidechain, model in last.values():
        toks = turn_tokens(usage)
        cost = turn_cost(usage, model)
        total += toks
        total_cost += cost
        turns += 1
        if sidechain:
            sub_total += toks
            sub_cost += cost
        key = model or "unknown"
        by_model[key] = by_model.get(key, 0) + toks

    return {
        "session_id": session_id,
        "total": total,
        "total_cost": round(total_cost, 6),
        "subagent_total": sub_total,
        "subagent_cost": round(sub_cost, 6),
        "turns": turns,
        "by_model": by_model,
    }


def recent_tool_signatures(session_id, root=None, transcripts=None, limit=12):
    """Ordered ``(tool_name, input_hash)`` of recent tool calls, for loop
    detection. Reads ``tool_use`` blocks from assistant messages in file order
    (the main transcript is chronological)."""
    paths = transcripts if transcripts is not None else find_transcripts(session_id, root)
    sigs = []
    for path in paths:
        for rec in _iter_records(path):
            sid = rec.get("sessionId")
            if sid and sid != session_id:
                continue
            content = (rec.get("message") or {}).get("content")
            if not isinstance(content, list):
                continue
            for block in content:
                if isinstance(block, dict) and block.get("type") == "tool_use":
                    digest = hashlib.sha256(
                        json.dumps(block.get("input", {}), sort_keys=True, default=str).encode()
                    ).hexdigest()[:12]
                    sigs.append((block.get("name", ""), digest))
    return sigs[-limit:]


def is_looping(signatures, threshold=5):
    """True when the last ``threshold`` tool calls are byte-identical — the
    structural-loop signature that drives the classic overnight token burn."""
    if threshold < 2 or len(signatures) < threshold:
        return False
    return len(set(signatures[-threshold:])) == 1
