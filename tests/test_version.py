"""Version parity: meter.VERSION must equal the top CHANGELOG heading.

Mirrors phuryn/claude-usage's parity guard — the CHANGELOG is the canonical
version and the auto-tag workflow trusts it, so a drift here would mistag a
release.
"""
import re
import unittest
from pathlib import Path

import meter


ROOT = Path(__file__).resolve().parent.parent


class TestVersionParity(unittest.TestCase):
    def test_version_matches_top_changelog_heading(self):
        changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
        match = re.search(r"^## v(\d+\.\d+\.\d+)", changelog, re.MULTILINE)
        self.assertIsNotNone(match, "CHANGELOG.md has no '## vX.Y.Z' heading")
        self.assertEqual(
            meter.VERSION,
            match.group(1),
            "meter.VERSION must equal the top CHANGELOG heading (bump them together).",
        )

    def test_plugin_manifest_version_matches(self):
        import json

        plugin = json.loads((ROOT / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))
        self.assertEqual(
            meter.VERSION,
            plugin["version"],
            ".claude-plugin/plugin.json version must equal meter.VERSION.",
        )


if __name__ == "__main__":
    unittest.main()
