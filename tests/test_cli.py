"""Tests for cli.py — arm (tokens + dollars), reset re-baselining, disarm.

Uses a temp projects dir (real meter) and patches `arm_path` to a temp file, so
nothing touches the user's real ~/.claude.
"""
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import cli


def write_turn(projroot, sid, mid, tokens):
    path = Path(projroot) / "c--proj" / f"{sid}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    rec = {
        "type": "assistant",
        "sessionId": sid,
        "isSidechain": False,
        "message": {
            "id": mid,
            "model": "claude-opus-4-8",
            "usage": {
                "input_tokens": tokens,
                "output_tokens": 0,
                "cache_read_input_tokens": 0,
                "cache_creation_input_tokens": 0,
            },
        },
    }
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec) + "\n")


class CliBase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
        self.proj = self.tmp / "projects"
        self.proj.mkdir()
        self.armfile = self.tmp / "arm.json"
        self.sid = "S1"

        env = mock.patch.dict(
            os.environ,
            {"CLAUDE_CODE_SESSION_ID": self.sid, "CLAUDE_PROJECTS_DIR": str(self.proj)},
            clear=False,
        )
        env.start()
        self.addCleanup(env.stop)
        os.environ.pop("BURNSTOP_BUDGET", None)

        ap = mock.patch.object(cli, "arm_path", return_value=self.armfile)
        ap.start()
        self.addCleanup(ap.stop)

        # keep CLI prints out of the test output
        out = mock.patch("sys.stdout", new_callable=io.StringIO)
        out.start()
        self.addCleanup(out.stop)


class TestArm(CliBase):
    def test_arm_tokens_sets_baseline_to_current_spend(self):
        write_turn(self.proj, self.sid, "m1", 1000)
        self.assertEqual(cli.main(["arm", "200k"]), 0)
        cfg = json.loads(self.armfile.read_text())
        self.assertEqual(cfg["cap"], 200000)
        self.assertEqual(cfg["unit"], "tok")
        self.assertEqual(cfg["baseline"], 1000)

    def test_arm_dollars(self):
        self.assertEqual(cli.main(["arm", "$1"]), 0)
        cfg = json.loads(self.armfile.read_text())
        self.assertEqual(cfg["cap"], 1.0)
        self.assertEqual(cfg["unit"], "usd")

    def test_arm_bad_budget_errors(self):
        self.assertEqual(cli.main(["arm", "banana"]), 2)
        self.assertFalse(self.armfile.exists())


class TestReset(CliBase):
    def test_reset_rebaselines_to_new_spend(self):
        write_turn(self.proj, self.sid, "m1", 1000)
        cli.main(["arm", "200k"])
        self.assertEqual(json.loads(self.armfile.read_text())["baseline"], 1000)
        write_turn(self.proj, self.sid, "m2", 5000)  # 5k more spent
        self.assertEqual(cli.main(["reset"]), 0)
        cfg = json.loads(self.armfile.read_text())
        self.assertEqual(cfg["baseline"], 6000)  # spend-since now starts from 6k
        self.assertEqual(cfg["cap"], 200000)     # cap unchanged

    def test_reset_without_arm_errors(self):
        self.assertEqual(cli.main(["reset"]), 2)


class TestDisarm(CliBase):
    def test_disarm_removes_file(self):
        write_turn(self.proj, self.sid, "m1", 10)
        cli.main(["arm", "200k"])
        self.assertTrue(self.armfile.exists())
        self.assertEqual(cli.main(["disarm"]), 0)
        self.assertFalse(self.armfile.exists())


class TestDefault(CliBase):
    def test_set_default_writes_config(self):
        cfg = self.tmp / "config.json"
        with mock.patch.object(cli.hook, "config_path", return_value=cfg):
            self.assertEqual(cli.main(["default", "$100"]), 0)
            self.assertEqual(json.loads(cfg.read_text())["goal_default"], "$100")

    def test_show_default_when_unset(self):
        cfg = self.tmp / "missing.json"
        with mock.patch.object(cli.hook, "config_path", return_value=cfg):
            self.assertEqual(cli.main(["default"]), 0)  # built-in, no file needed

    def test_bad_default_errors(self):
        cfg = self.tmp / "config.json"
        with mock.patch.object(cli.hook, "config_path", return_value=cfg):
            self.assertEqual(cli.main(["default", "banana"]), 2)
            self.assertFalse(cfg.exists())


if __name__ == "__main__":
    unittest.main()
