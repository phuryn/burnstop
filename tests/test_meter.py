"""Tests for meter.py — transcript parsing, dedupe, subagent inclusion, loops.

Uses a temp projects root (never touches the user's real ~/.claude).
"""
import json
import tempfile
import unittest
from pathlib import Path

import meter


def write_jsonl(path, records):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        for rec in records:
            fh.write(json.dumps(rec) + "\n")


def turn(mid, i=100, o=50, cr=0, cc=0, sid="S", sidechain=False, model="claude-opus-4-8"):
    return {
        "type": "assistant",
        "sessionId": sid,
        "isSidechain": sidechain,
        "message": {
            "id": mid,
            "model": model,
            "usage": {
                "input_tokens": i,
                "output_tokens": o,
                "cache_read_input_tokens": cr,
                "cache_creation_input_tokens": cc,
            },
        },
    }


def tool_use(name, inp, sid="S"):
    return {
        "type": "assistant",
        "sessionId": sid,
        "message": {"id": None, "content": [{"type": "tool_use", "name": name, "input": inp}]},
    }


class SpendBase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.sid = "05c0311f"
        self.proj = self.root / "c--proj"
        self.main = self.proj / f"{self.sid}.jsonl"

    def tearDown(self):
        self._tmp.cleanup()

    def spend(self):
        return meter.session_spend(self.sid, root=self.root)


class TestSpend(SpendBase):
    def test_basic_sum(self):
        write_jsonl(self.main, [turn("m1", 100, 50, sid=self.sid), turn("m2", 200, 20, sid=self.sid)])
        s = self.spend()
        self.assertEqual(s["total"], 370)
        self.assertEqual(s["turns"], 2)

    def test_dedupe_by_message_id_last_wins(self):
        # streaming writes the same message id several times; only the last is final
        write_jsonl(self.main, [turn("m1", 10, 5, sid=self.sid), turn("m1", 100, 50, sid=self.sid)])
        s = self.spend()
        self.assertEqual(s["total"], 150)
        self.assertEqual(s["turns"], 1)

    def test_cache_tokens_counted(self):
        write_jsonl(self.main, [turn("m1", 0, 0, cr=1000, cc=500, sid=self.sid)])
        self.assertEqual(self.spend()["total"], 1500)

    def test_subagent_inline(self):
        write_jsonl(self.main, [turn("m1", 100, 0, sid=self.sid),
                                turn("s1", 40, 10, sid=self.sid, sidechain=True)])
        s = self.spend()
        self.assertEqual(s["total"], 150)
        self.assertEqual(s["subagent_total"], 50)

    def test_subagent_separate_file(self):
        write_jsonl(self.main, [turn("m1", 100, 0, sid=self.sid)])
        write_jsonl(self.proj / "subagents" / "agent-x.jsonl",
                    [turn("s1", 40, 10, sid=self.sid, sidechain=True)])
        s = self.spend()
        self.assertEqual(s["total"], 150)
        self.assertEqual(s["subagent_total"], 50)

    def test_other_session_ignored(self):
        write_jsonl(self.main, [turn("m1", 100, 0, sid=self.sid)])
        write_jsonl(self.proj / "subagents" / "agent-y.jsonl",
                    [turn("z1", 999, 0, sid="OTHER", sidechain=True)])
        self.assertEqual(self.spend()["total"], 100)

    def test_dedupe_across_parent_and_subagent_file(self):
        # same message id appears in both places -> counted once
        write_jsonl(self.main, [turn("dup", 100, 0, sid=self.sid, sidechain=True)])
        write_jsonl(self.proj / "subagents" / "agent-z.jsonl",
                    [turn("dup", 100, 0, sid=self.sid, sidechain=True)])
        self.assertEqual(self.spend()["total"], 100)

    def test_no_transcript_is_zero(self):
        self.assertEqual(self.spend()["total"], 0)


class TestLoop(SpendBase):
    def test_is_looping_identical(self):
        self.assertTrue(meter.is_looping([("Bash", "h")] * 5, 5))

    def test_not_looping_when_varied(self):
        sigs = [("Bash", "a"), ("Bash", "b")] * 3
        self.assertFalse(meter.is_looping(sigs, 5))

    def test_below_threshold_never_loops(self):
        self.assertFalse(meter.is_looping([("Bash", "a")] * 3, 5))

    def test_signatures_from_transcript(self):
        write_jsonl(self.main, [tool_use("Bash", {"command": "ls"}, sid=self.sid)] * 6)
        sigs = meter.recent_tool_signatures(self.sid, root=self.root, limit=12)
        self.assertEqual(len(sigs), 6)
        self.assertTrue(meter.is_looping(sigs, 5))

    def test_distinct_inputs_not_a_loop(self):
        write_jsonl(self.main, [tool_use("Bash", {"command": f"echo {i}"}, sid=self.sid) for i in range(6)])
        sigs = meter.recent_tool_signatures(self.sid, root=self.root)
        self.assertFalse(meter.is_looping(sigs, 5))


if __name__ == "__main__":
    unittest.main()
