"""Test fixture: a Stop hook that ALWAYS blocks the stop.

This simulates an unmet `/goal` (which is itself a prompt-based Stop hook that
returns `decision: block` to force the agent to keep going). The live
integration tests register this alongside burnstop's Stop hook to prove that
burnstop's top-level `continue: false` overrides a goal-style block and halts
the session anyway. Bounded in tests by `--max-turns` so it can't loop forever.
"""
import json
import sys

try:
    sys.stdin.read()  # drain the hook payload so nothing hangs
except Exception:
    pass

print(json.dumps({"decision": "block", "reason": "Not done yet. Keep working on the task."}))
