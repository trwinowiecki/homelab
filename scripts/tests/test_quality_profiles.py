#!/usr/bin/env python3
"""Low (720p) profile is declared in Recyclarr and kept by the prune allowlist."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from prune_arr_quality_profiles import ALLOWLISTS  # noqa: E402


class LowProfileTests(unittest.TestCase):
    def test_recyclarr_declares_low_720p_on_both_apps(self) -> None:
        text = (ROOT / "arrstack" / "recyclarr.yml").read_text()
        self.assertGreaterEqual(text.count("name: Low (720p)"), 2)
        self.assertIn("until_quality: Bluray-720p", text)
        self.assertIn("WEBDL-720p", text)
        self.assertIn("47435ece6b99a0b477caf360e79ba0bb", text)  # Sonarr x265 (HD)
        self.assertIn("dc98083864ea246d05a42df0d05f81cc", text)  # Radarr x265 (HD)

    def test_prune_allowlist_keeps_low_720p(self) -> None:
        self.assertIn("Low (720p)", ALLOWLISTS["sonarr"])
        self.assertIn("Low (720p)", ALLOWLISTS["radarr"])


if __name__ == "__main__":
    raise SystemExit(unittest.main())
