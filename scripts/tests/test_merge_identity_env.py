"""Tests for scripts/merge_identity_env.py."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
import sys

sys.path.insert(0, str(ROOT / "scripts"))

from merge_identity_env import merge_lines  # noqa: E402


class TestMergeIdentityEnv(unittest.TestCase):
    def test_preserves_existing_secrets_and_adds_missing_keys(self) -> None:
        example = """\
TIMEZONE=America/New_York
AUTHENTIK_SECRET_KEY=CHANGE_ME
LDAP_OUTPOST_TOKEN=
OAUTH_PANGOLIN_REDIRECT_URI=
"""
        current = {
            "TIMEZONE": "America/Chicago",
            "AUTHENTIK_SECRET_KEY": "real-secret",
            "LDAP_OUTPOST_TOKEN": "ldap-token",
            "LEGACY_VAR": "keep-me",
        }
        lines, changes = merge_lines(example, current)
        text = "\n".join(lines)
        self.assertIn("TIMEZONE=America/Chicago", text)
        self.assertIn("AUTHENTIK_SECRET_KEY=real-secret", text)
        self.assertIn("LDAP_OUTPOST_TOKEN=ldap-token", text)
        self.assertIn("OAUTH_PANGOLIN_REDIRECT_URI=", text)
        self.assertIn("LEGACY_VAR=keep-me", text)

    def test_generates_placeholder_authentik_secret(self) -> None:
        example = "AUTHENTIK_SECRET_KEY=CHANGE_ME\n"
        lines, changes = merge_lines(example, {})
        text = "\n".join(lines)
        self.assertTrue(text.startswith("AUTHENTIK_SECRET_KEY="))
        self.assertNotIn("CHANGE_ME", text)
        self.assertEqual(changes[0][0], "AUTHENTIK_SECRET_KEY")
        self.assertEqual(changes[0][1], "generated")


if __name__ == "__main__":
    unittest.main()
