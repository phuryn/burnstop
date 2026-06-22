"""Tests for hook.py — budget parsing (tokens + dollars), resolution precedence,
the pure verdict (`evaluate`), and per-event rendering (`render`).

The render tests pin the key correctness fact: a halt is a top-level
`continue: false` on Stop/SubagentStop (the only reliable stop, and the one that
beats `/goal`), and a permission `deny` on PreToolUse.
"""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import hook


class TestParseBudget(unittest.TestCase):
    def test_tokens_k(self):
        self.assertEqual(hook.parse_budget("200k"), (200_000, "tok"))

    def test_tokens_m(self):
        self.assertEqual(hook.parse_budget("1.5m"), (1_500_000, "tok"))

    def test_tokens_plain(self):
        self.assertEqual(hook.parse_budget("50000"), (50_000, "tok"))

    def test_tokens_underscores_and_commas(self):
        self.assertEqual(hook.parse_budget("50_000"), (50_000, "tok"))
        self.assertEqual(hook.parse_budget("1,250,000"), (1_250_000, "tok"))

    def test_dollars(self):
        self.assertEqual(hook.parse_budget("$1"), (1.0, "usd"))

    def test_dollars_cents(self):
        self.assertEqual(hook.parse_budget("$0.10"), (0.10, "usd"))

    def test_dollars_usd_suffix(self):
        self.assertEqual(hook.parse_budget("2.50usd"), (2.50, "usd"))

    def test_bad(self):
        self.assertIsNone(hook.parse_budget("abc"))

    def test_dollar_zero(self):
        self.assertIsNone(hook.parse_budget("$0"))

    def test_zero(self):
        self.assertIsNone(hook.parse_budget("0"))

    def test_none(self):
        self.assertIsNone(hook.parse_budget(None))


class TestResolveBudget(unittest.TestCase):
    def test_env_tokens(self):
        with mock.patch.dict(os.environ, {"BURNSTOP_BUDGET": "200k"}):
            self.assertEqual(hook.resolve_budget("S"), (200_000, 0, "env", "tok"))

    def test_env_dollars(self):
        with mock.patch.dict(os.environ, {"BURNSTOP_BUDGET": "$1"}):
            self.assertEqual(hook.resolve_budget("S"), (1.0, 0, "env", "usd"))

    def test_unarmed_is_none(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("BURNSTOP_BUDGET", None)
            with mock.patch.object(hook, "arm_path", return_value=Path("/no/such/file.json")):
                self.assertIsNone(hook.resolve_budget("nope")[0])

    def test_arm_file_dollars(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "S.json"
            p.write_text(json.dumps({"cap": 1.0, "baseline": 0.25, "unit": "usd"}))
            with mock.patch.dict(os.environ, {}, clear=False):
                os.environ.pop("BURNSTOP_BUDGET", None)
                with mock.patch.object(hook, "arm_path", return_value=p):
                    self.assertEqual(hook.resolve_budget("S"), (1.0, 0.25, "file", "usd"))


class TestEvaluate(unittest.TestCase):
    def test_allow_tokens(self):
        self.assertEqual(hook.evaluate(1000, 10000, "tok", [])[0], "allow")

    def test_halt_tokens(self):
        verdict, msg = hook.evaluate(12000, 10000, "tok", [])
        self.assertEqual(verdict, "halt")
        self.assertIn("budget", msg.lower())

    def test_warn_tokens(self):
        self.assertEqual(hook.evaluate(8500, 10000, "tok", [])[0], "warn")

    def test_halt_dollars_message_formatting(self):
        verdict, msg = hook.evaluate(0.11, 0.10, "usd", [])
        self.assertEqual(verdict, "halt")
        self.assertIn("$0.11", msg)
        self.assertIn("$0.10", msg)

    def test_dollar_one_trips(self):
        self.assertEqual(hook.evaluate(1.5, 1.0, "usd", [])[0], "halt")

    def test_dollar_warn_band(self):
        self.assertEqual(hook.evaluate(0.09, 0.10, "usd", [])[0], "warn")

    def test_dollar_under_allows(self):
        self.assertEqual(hook.evaluate(0.04, 0.10, "usd", [])[0], "allow")

    def test_loop_halts_even_under_budget(self):
        sigs = [("Bash", "x")] * hook.LOOP_THRESHOLD
        verdict, msg = hook.evaluate(0.001, 1.0, "usd", sigs)
        self.assertEqual(verdict, "halt")
        self.assertIn("loop", msg.lower())


class TestRender(unittest.TestCase):
    def test_allow_is_none(self):
        self.assertIsNone(hook.render("allow", None, "PreToolUse"))

    def test_warn_systemmessage(self):
        self.assertEqual(hook.render("warn", "m", "Stop"), {"systemMessage": "m"})

    def test_halt_stop_is_continue_false(self):
        out = hook.render("halt", "m", "Stop")
        self.assertFalse(out["continue"])
        self.assertEqual(out["stopReason"], "m")
        self.assertNotIn("hookSpecificOutput", out)

    def test_halt_subagentstop_is_continue_false(self):
        out = hook.render("halt", "m", "SubagentStop")
        self.assertFalse(out["continue"])

    def test_halt_pretooluse_denies_and_continues_false(self):
        out = hook.render("halt", "deny me", "PreToolUse")
        self.assertFalse(out["continue"])  # belt
        hso = out["hookSpecificOutput"]      # and suspenders
        self.assertEqual(hso["hookEventName"], "PreToolUse")
        self.assertEqual(hso["permissionDecision"], "deny")
        self.assertEqual(hso["permissionDecisionReason"], "deny me")


class TestBurnstopCliExemption(unittest.TestCase):
    """The fuse must never block burnstop's own /budget commands, so you can
    always check or disarm even when the session is over budget."""

    def test_burnstop_cli_bash_is_exempt(self):
        payload = {"tool_name": "Bash", "tool_input": {"command": 'python "C:/GitHub/burnstop/cli.py" disarm'}}
        self.assertTrue(hook._is_burnstop_cli(payload))

    def test_normal_bash_not_exempt(self):
        self.assertFalse(hook._is_burnstop_cli({"tool_name": "Bash", "tool_input": {"command": "ls -la"}}))

    def test_non_bash_not_exempt(self):
        self.assertFalse(hook._is_burnstop_cli({"tool_name": "Edit", "tool_input": {}}))

    def test_burnstop_word_without_cli_not_exempt(self):
        self.assertFalse(hook._is_burnstop_cli({"tool_name": "Bash", "tool_input": {"command": "echo burnstop"}}))


class TestForgivingBudgetParse(unittest.TestCase):
    """All the dollar/token forms a user might type."""

    def test_dollar_forms(self):
        for raw in ("$1", "1$", "$ 1", "$1.00", "1.00$", "1usd", "1 usd"):
            self.assertEqual(hook.parse_budget(raw), (1.0, "usd"), raw)

    def test_dollar_cents(self):
        self.assertEqual(hook.parse_budget("$0.10"), (0.10, "usd"))
        self.assertEqual(hook.parse_budget("1.50$"), (1.50, "usd"))

    def test_token_k_both_cases(self):
        self.assertEqual(hook.parse_budget("200k"), (200_000, "tok"))
        self.assertEqual(hook.parse_budget("200K"), (200_000, "tok"))

    def test_token_m_forms(self):
        for raw in ("5m", "5M", "5 M", "5 m"):
            self.assertEqual(hook.parse_budget(raw), (5_000_000, "tok"), raw)

    def test_token_decimal_and_plain(self):
        self.assertEqual(hook.parse_budget("1.5m"), (1_500_000, "tok"))
        self.assertEqual(hook.parse_budget("1,250,000"), (1_250_000, "tok"))


class TestFmtTokens(unittest.TestCase):
    def test_under_thousand(self):
        self.assertEqual(hook.fmt_tokens(850), "850")

    def test_round_thousands(self):
        self.assertEqual(hook.fmt_tokens(200_000), "200k")

    def test_fractional_thousands(self):
        self.assertEqual(hook.fmt_tokens(1_500), "1.5k")

    def test_millions_two_decimals(self):
        self.assertEqual(hook.fmt_tokens(1_000_000), "1.00M")
        self.assertEqual(hook.fmt_tokens(119_138_475), "119.14M")

    def test_fmt_wraps_with_unit(self):
        self.assertEqual(hook.fmt(200_000, "tok"), "200k tok")
        self.assertEqual(hook.fmt(1.5, "usd"), "$1.50")


if __name__ == "__main__":
    unittest.main()
