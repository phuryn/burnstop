"""burnstop prompt-side dispatcher: `/goal` auto-arm.

Registered on ``UserPromptExpansion``, which fires BEFORE slash-command expansion
and carries ``command_name`` / ``command_arguments``. When a ``/goal`` is starting
and the session isn't already armed, we auto-arm a default budget
(``BURNSTOP_GOAL_DEFAULT`` / config / $50) so a runaway goal can't burn unbounded
spend. Explicit budgets are never clobbered.

(The `/budget` commands do NOT go through here: current Claude Code fires
``UserPromptSubmit`` on the raw `/budget-x` text *before* expansion and does not
honor a hook blocking a slash command, so the commands run the CLI directly via
Bash instead. See docs/spikes.md.)

Stdlib only, Python 3.8+.
"""
from __future__ import annotations

import contextlib
import io
import json
import os
import sys

import cli
import hook


def _run_cli(args):
    """Run the CLI in-process (captures output; we don't need it for auto-arm)."""
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
        try:
            cli.main(args)
        except SystemExit:
            pass
    return buf.getvalue().strip()


def _already_armed(session_id):
    if hook.parse_budget(os.environ.get("BURNSTOP_BUDGET")):
        return True
    return bool(session_id) and hook.arm_path(session_id).is_file()


def handle_user_prompt_expansion(payload):
    """Auto-arm the default budget when a /goal is starting (unless already armed)."""
    if payload.get("command_name") != "goal":
        return None
    session_id = payload.get("session_id")
    if _already_armed(session_id):
        return None  # respect an explicit budget
    args = (["--session", session_id] if session_id else []) + ["arm", hook.goal_default()]
    _run_cli(args)
    return None  # side-effect only; let the goal expansion proceed unchanged


def main():
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        return 0
    if payload.get("hook_event_name") == "UserPromptExpansion":
        handle_user_prompt_expansion(payload)
    return 0


if __name__ == "__main__":
    sys.exit(main())
