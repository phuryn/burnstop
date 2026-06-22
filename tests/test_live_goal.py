"""Live integration test (REAL LLM calls) - DOES NOT run on CI.

Gated behind BURNSTOP_LIVE=1 and a `claude` CLI on PATH. It proves the one thing
unit tests can't: that burnstop's Stop-hook `continue:false` actually halts a
session AND overrides a /goal-style block.

Why it touches user settings: hooks loaded via `--settings` or a project
`.claude/settings.json` do NOT fire in `claude -p` (only user-scope settings are
trusted headlessly). So the test temporarily injects GATED hooks into
`~/.claude/settings.json` and restores the file afterward. Every test hook is a
**no-op unless `BURNSTOP_TEST=1`** (which only the child `claude -p` run sets),
so it can never affect any other session. The block fixture
(`tests/fixtures/_gated_block.py`) simulates an unmet `/goal` by returning
`decision: block`.

Run it yourself (it will not run on GitHub):

    BURNSTOP_LIVE=1 python -m unittest tests.test_live_goal -v
"""
import json
import os
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
HOOK = REPO / "hook.py"
BLOCK = REPO / "tests" / "fixtures" / "_gated_block.py"
SETTINGS = Path.home() / ".claude" / "settings.json"

LIVE = os.environ.get("BURNSTOP_LIVE") == "1" and shutil.which("claude") is not None


def _cmd(script):
    return f'"{sys.executable}" "{script}"'


@unittest.skipUnless(LIVE, "live test: set BURNSTOP_LIVE=1 with `claude` on PATH")
class TestOverridesGoalBlock(unittest.TestCase):
    def setUp(self):
        self._backup = SETTINGS.read_text(encoding="utf-8") if SETTINGS.is_file() else None
        settings = json.loads(self._backup) if self._backup else {}
        hooks = settings.setdefault("hooks", {})
        hooks["PreToolUse"] = [{"matcher": "*", "hooks": [{"type": "command", "command": _cmd(HOOK)}]}]
        hooks["Stop"] = [
            {"hooks": [{"type": "command", "command": _cmd(HOOK)}]},          # burnstop
            {"hooks": [{"type": "command", "command": _cmd(BLOCK)}]},          # goal sim
        ]
        SETTINGS.parent.mkdir(parents=True, exist_ok=True)
        SETTINGS.write_text(json.dumps(settings, indent=2), encoding="utf-8")

    def tearDown(self):
        if self._backup is None:
            SETTINGS.unlink(missing_ok=True)
        else:
            SETTINGS.write_text(self._backup, encoding="utf-8")

    def _num_turns(self, budget, max_turns=4):
        env = dict(os.environ, BURNSTOP_TEST="1", BURNSTOP_BUDGET=budget)
        proc = subprocess.run(
            [
                "claude", "-p", "Write a two-line poem about the sea.",
                "--max-turns", str(max_turns), "--dangerously-skip-permissions",
                "--output-format", "json",
            ],
            capture_output=True, text=True, env=env, timeout=300,
        )
        results = [ln for ln in proc.stdout.splitlines() if '"type":"result"' in ln]
        self.assertTrue(results, f"no result line; stderr={proc.stderr[:400]}")
        return json.loads(results[-1])["num_turns"]

    def test_burnstop_overrides_goal_block(self):
        # control: burnstop can't trip (huge budget) -> the goal-block forces the full leash
        control = self._num_turns("$999", max_turns=4)
        # test: burnstop trips early -> its continue:false must override the block
        test = self._num_turns("$0.30", max_turns=4)
        self.assertGreater(control, 1, "goal-block didn't force continuation (do user hooks fire in -p?)")
        self.assertLess(test, control, "burnstop continue:false did NOT override the goal block")


if __name__ == "__main__":
    unittest.main()
