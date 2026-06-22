"""Pricing + dollar-cost tests — the 'different models cost differently' cases.

Covers per-model resolution, per-turn cost, mixed-model session cost (the reason
cost is computed per turn, not on aggregate tokens), subagent cost inclusion, and
the small-budget boundaries ($0.10 / $1) the user asked about.
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


def turn(mid, model, i=0, o=0, cr=0, cc=0, sid="S", sidechain=False):
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


class TestGetPricing(unittest.TestCase):
    def test_opus(self):
        self.assertIs(meter.get_pricing("claude-opus-4-8"), meter.PRICING["opus"])

    def test_opus_date_suffixed(self):
        self.assertIs(meter.get_pricing("claude-opus-4-8-20260601"), meter.PRICING["opus"])

    def test_sonnet(self):
        self.assertIs(meter.get_pricing("claude-sonnet-4-6"), meter.PRICING["sonnet"])

    def test_haiku(self):
        self.assertIs(meter.get_pricing("claude-haiku-4-5-20251001"), meter.PRICING["haiku"])

    def test_fable(self):
        self.assertIs(meter.get_pricing("claude-fable-5"), meter.PRICING["fable"])

    def test_mythos(self):
        self.assertIs(meter.get_pricing("claude-mythos-5"), meter.PRICING["mythos"])

    def test_unknown_is_none(self):
        for model in ("gpt-4o", "gemma-2", "glm-4.6", "", None):
            self.assertIsNone(meter.get_pricing(model))


class TestTurnCost(unittest.TestCase):
    def test_opus_output_5x_haiku(self):
        usage = {"output_tokens": 1_000_000}
        self.assertAlmostEqual(meter.turn_cost(usage, "claude-opus-4-8"), 25.0)
        self.assertAlmostEqual(meter.turn_cost(usage, "claude-haiku-4-5"), 5.0)
        # the whole reason dollar budgets need per-model pricing:
        self.assertAlmostEqual(
            meter.turn_cost(usage, "claude-opus-4-8") / meter.turn_cost(usage, "claude-haiku-4-5"),
            5.0,
        )

    def test_opus_input(self):
        self.assertAlmostEqual(meter.turn_cost({"input_tokens": 1_000_000}, "claude-opus-4-8"), 5.0)

    def test_sonnet_output(self):
        self.assertAlmostEqual(meter.turn_cost({"output_tokens": 1_000_000}, "claude-sonnet-4-6"), 15.0)

    def test_opus_cache_rates(self):
        self.assertAlmostEqual(meter.turn_cost({"cache_read_input_tokens": 1_000_000}, "claude-opus-4-8"), 0.50)
        self.assertAlmostEqual(meter.turn_cost({"cache_creation_input_tokens": 1_000_000}, "claude-opus-4-8"), 6.25)

    def test_unknown_model_is_free(self):
        self.assertEqual(meter.turn_cost({"output_tokens": 1_000_000}, "gpt-4o"), 0.0)


class TestSessionCost(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.sid = "abc123"
        self.main = self.root / "c--proj" / f"{self.sid}.jsonl"

    def tearDown(self):
        self._tmp.cleanup()

    def spend(self):
        return meter.session_spend(self.sid, root=self.root)

    def test_mixed_model_cost_sums_per_turn(self):
        # opus 1M output ($25) + sonnet 1M output ($15) = $40, NOT 2M * one price
        write_jsonl(self.main, [
            turn("m1", "claude-opus-4-8", o=1_000_000, sid=self.sid),
            turn("m2", "claude-sonnet-4-6", o=1_000_000, sid=self.sid),
        ])
        s = self.spend()
        self.assertAlmostEqual(s["total_cost"], 40.0)
        self.assertEqual(s["total"], 2_000_000)

    def test_subagent_cost_included(self):
        write_jsonl(self.main, [
            turn("m1", "claude-opus-4-8", o=1_000_000, sid=self.sid),
            turn("s1", "claude-haiku-4-5", o=1_000_000, sid=self.sid, sidechain=True),
        ])
        s = self.spend()
        self.assertAlmostEqual(s["total_cost"], 30.0)       # 25 opus + 5 haiku
        self.assertAlmostEqual(s["subagent_cost"], 5.0)

    def test_ten_cent_boundary(self):
        # opus output is $25/MTok -> $0.10 == 4,000 output tokens exactly
        write_jsonl(self.main, [turn("m1", "claude-opus-4-8", o=4_000, sid=self.sid)])
        self.assertAlmostEqual(self.spend()["total_cost"], 0.10, places=6)

    def test_one_dollar_boundary(self):
        # $1.00 of opus output == 40,000 tokens
        write_jsonl(self.main, [turn("m1", "claude-opus-4-8", o=40_000, sid=self.sid)])
        self.assertAlmostEqual(self.spend()["total_cost"], 1.00, places=6)

    def test_unknown_model_costs_zero_but_counts_tokens(self):
        write_jsonl(self.main, [turn("m1", "glm-4.6", o=1_000_000, sid=self.sid)])
        s = self.spend()
        self.assertEqual(s["total_cost"], 0.0)
        self.assertEqual(s["total"], 1_000_000)


if __name__ == "__main__":
    unittest.main()
