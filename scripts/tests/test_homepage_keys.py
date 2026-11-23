#!/usr/bin/env python3
"""Tests for Homepage key sync helpers."""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from arrstack_api_keys import (  # noqa: E402
    HOMEPAGE_KEY_ALIASES,
    apply_homepage_aliases,
    build_auto_env_assignments,
    generate_qbit_api_key,
    read_jellyfin_api_key_from_seerr,
    read_qbit_conf,
    read_seerr_api_key,
    write_qbit_conf,
)


class QbitConfTests(unittest.TestCase):
    def test_generate_qbit_api_key_format(self) -> None:
        key = generate_qbit_api_key()
        self.assertTrue(key.startswith("qbt_"))
        self.assertEqual(len(key), 32)

    def test_write_and_read_qbit_conf(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "qBittorrent.conf"
            self.assertTrue(write_qbit_conf(path, "qbt_abcdefghijklmnopqrstuvwxyz12"))
            self.assertEqual(read_qbit_conf(path), "qbt_abcdefghijklmnopqrstuvwxyz12")
            self.assertFalse(
                write_qbit_conf(path, "qbt_abcdefghijklmnopqrstuvwxyz12"),
            )

    def test_write_qbit_conf_updates_existing_line(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "qBittorrent.conf"
            path.write_text("[Preferences]\nWebUI\\APIKey=old_key\n")
            self.assertTrue(write_qbit_conf(path, "qbt_newkeyabcdefghijklmnopqrst"))
            self.assertEqual(read_qbit_conf(path), "qbt_newkeyabcdefghijklmnopqrst")


class SeerrJsonTests(unittest.TestCase):
    def test_read_seerr_and_jellyfin_keys(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            settings = {
                "main": {"apiKey": "seerr-main-key"},
                "jellyfin": {"apiKey": "jellyfin-widget-key"},
            }
            (data_dir / "jellyseerr").mkdir()
            (data_dir / "jellyseerr" / "settings.json").write_text(
                json.dumps(settings),
            )
            self.assertEqual(read_seerr_api_key(data_dir), "seerr-main-key")
            self.assertEqual(
                read_jellyfin_api_key_from_seerr(data_dir),
                "jellyfin-widget-key",
            )


class HomepageAliasTests(unittest.TestCase):
    def test_apply_homepage_aliases(self) -> None:
        out = {"SONARR_API_KEY": "abc123", "BAZARR_API_KEY": "def456"}
        changed: list[str] = []
        apply_homepage_aliases(out, changed)
        self.assertEqual(out["HOMEPAGE_VAR_SONARR_API_KEY"], "abc123")
        self.assertEqual(out["HOMEPAGE_VAR_BAZARR_API_KEY"], "def456")
        self.assertIn("HOMEPAGE_VAR_SONARR_API_KEY", changed)

    def test_build_auto_env_assignments_emits_aliases_and_seerr(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            (data_dir / "jellyseerr").mkdir()
            (data_dir / "jellyseerr" / "settings.json").write_text(
                json.dumps(
                    {
                        "main": {"apiKey": "seerr-key"},
                        "jellyfin": {"apiKey": "jf-key"},
                    },
                ),
            )
            (data_dir / "bazarr" / "config").mkdir(parents=True)
            (data_dir / "bazarr" / "config" / "config.yaml").write_text(
                "auth:\n  apikey: bazarr-key\n",
            )

            assignments, changed = build_auto_env_assignments(data_dir, {})

            self.assertEqual(assignments["SEERR_API_KEY"], "seerr-key")
            self.assertEqual(assignments["JELLYFIN_API_KEY"], "jf-key")
            self.assertEqual(assignments["BAZARR_API_KEY"], "bazarr-key")
            self.assertEqual(
                assignments[HOMEPAGE_KEY_ALIASES["SEERR_API_KEY"]],
                "seerr-key",
            )
            self.assertIn("HOMEPAGE_VAR_BAZARR_API_KEY", changed)

    def test_truenas_key_from_env_file_is_aliased(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            env_file = Path(tmp) / ".env"
            env_file.write_text("TRUENAS_API_KEY=tn-key-123\n")
            assignments, changed = build_auto_env_assignments(
                data_dir,
                {},
                env_file=env_file,
            )
            self.assertEqual(assignments["TRUENAS_API_KEY"], "tn-key-123")
            self.assertEqual(assignments["HOMEPAGE_VAR_TRUENAS_API_KEY"], "tn-key-123")
            self.assertIn("HOMEPAGE_VAR_TRUENAS_API_KEY", changed)


if __name__ == "__main__":
    raise SystemExit(unittest.main())
