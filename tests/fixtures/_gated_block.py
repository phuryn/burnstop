"""Stop hook that blocks the stop (forces continue) ONLY when BURNSTOP_TEST=1.

Simulates an unmet /goal for the live test child, while staying a complete
no-op for any other session that happens to load it from user settings (so it
can never trap the developer's own session). Used by the live integration test
to prove burnstop's top-level continue:false overrides a goal-style block.
"""
import json
import os
import sys

try:
    sys.stdin.read()
except Exception:
    pass

if os.environ.get("BURNSTOP_TEST") == "1":
    print(json.dumps({"decision": "block", "reason": "Not done yet. Add one more short stanza."}))
# otherwise: no output -> allow the stop (no effect on any real session)
