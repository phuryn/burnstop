"""Tests for dispatch.py — the /goal auto-arm (the only thing it does now).

cli.main is mocked so these stay offline.
"""
import os
import unittest
from unittest import mock

import dispatch
import hook


class TestGoalDefaultResolution(unittest.TestCase):
    def test_default_is_50(self):
        with mock.patch.dict(os.environ, {}, clear=False), \
                mock.patch.object(hook, "read_config", return_value={}):
            os.environ.pop("BURNSTOP_GOAL_DEFAULT", None)
            self.assertEqual(hook.goal_default(), "$50")

    def test_env_overrides(self):
        with mock.patch.dict(os.environ, {"BURNSTOP_GOAL_DEFAULT": "$7"}):
            self.assertEqual(hook.goal_default(), "$7")

    def test_config_sets_default(self):
        with mock.patch.dict(os.environ, {}, clear=False), \
                mock.patch.object(hook, "read_config", return_value={"goal_default": "$100"}):
            os.environ.pop("BURNSTOP_GOAL_DEFAULT", None)
            self.assertEqual(hook.goal_default(), "$100")


class TestGoalAutoArm(unittest.TestCase):
    def test_non_goal_ignored(self):
        with mock.patch.object(dispatch.cli, "main") as m:
            self.assertIsNone(dispatch.handle_user_prompt_expansion({"command_name": "explain"}))
        m.assert_not_called()

    def test_goal_auto_arms_default_when_unarmed(self):
        with mock.patch.object(dispatch, "_already_armed", return_value=False), \
                mock.patch.object(dispatch.hook, "goal_default", return_value="$50"), \
                mock.patch.object(dispatch.cli, "main") as m:
            out = dispatch.handle_user_prompt_expansion({"command_name": "goal", "session_id": "S"})
        self.assertIsNone(out)  # side-effect only, lets the goal proceed
        m.assert_called_once_with(["--session", "S", "arm", "$50"])

    def test_goal_uses_configured_default(self):
        with mock.patch.object(dispatch, "_already_armed", return_value=False), \
                mock.patch.object(dispatch.hook, "goal_default", return_value="$100"), \
                mock.patch.object(dispatch.cli, "main") as m:
            dispatch.handle_user_prompt_expansion({"command_name": "goal", "session_id": "S"})
        m.assert_called_once_with(["--session", "S", "arm", "$100"])

    def test_goal_does_not_clobber_explicit_budget(self):
        with mock.patch.object(dispatch, "_already_armed", return_value=True), \
                mock.patch.object(dispatch.cli, "main") as m:
            dispatch.handle_user_prompt_expansion({"command_name": "goal", "session_id": "S"})
        m.assert_not_called()


if __name__ == "__main__":
    unittest.main()
