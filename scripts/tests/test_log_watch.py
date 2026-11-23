#!/usr/bin/env python3
"""Tests for log-level extraction, signatures, and log-watch alerting rules."""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "obsv" / "log-watch"))
sys.path.insert(0, str(ROOT / "scripts"))

from levels import extract_level  # noqa: E402
from watch import (  # noqa: E402
    container_settings,
    digest_due,
    is_ignored,
    is_muted,
    load_alerts_config,
    new_signatures,
    normalize_signature,
    parse_duration,
    spike_exceeded,
)


class LevelExtractionTests(unittest.TestCase):
    def test_arr_brackets(self) -> None:
        self.assertEqual(
            extract_level("[Error] ImportApprovedEpisodes: Couldn't import episode"),
            "error",
        )
        self.assertEqual(
            extract_level("[Warn] DownloadService: Download failed"),
            "warn",
        )
        self.assertEqual(extract_level("[Info] Bootstrap: Starting Sonarr"), "info")
        self.assertEqual(extract_level("[Fatal] App: aborted"), "error")

    def test_json_level_fields(self) -> None:
        self.assertEqual(
            extract_level('{"level":"error","logger":"authentik","event":"failed"}'),
            "error",
        )
        self.assertEqual(
            extract_level('{"level":"warning","ts":1.0,"msg":"caddy upstream"}'),
            "warn",
        )
        self.assertEqual(
            extract_level('{"lvl":"ERRO","msg":"zap style"}'),
            "error",
        )
        self.assertEqual(
            extract_level('{"severity":"INFO","message":"ok"}'),
            "info",
        )

    def test_logfmt(self) -> None:
        self.assertEqual(
            extract_level('ts=2026-10-30T00:00:00Z level=error msg="failed to dial"'),
            "error",
        )
        self.assertEqual(
            extract_level('component=http lvl=warn status=502'),
            "warn",
        )

    def test_go_tokens_and_gluetun(self) -> None:
        self.assertEqual(
            extract_level("2026-10-30T00:00:00Z ERR vpn: authentication failed"),
            "error",
        )
        self.assertEqual(
            extract_level("2026-10-30T00:00:00Z WRN firewall: rule already exists"),
            "warn",
        )
        self.assertEqual(
            extract_level("INFO [healthcheck] healthy"),
            "info",
        )
        self.assertEqual(
            extract_level("ERROR [vpn] restarting VPN because it failed to pass the healthcheck"),
            "error",
        )

    def test_plain_words(self) -> None:
        self.assertEqual(extract_level("ERROR: bind: address already in use"), "error")
        self.assertEqual(extract_level("WARN something looks off"), "warn")

    def test_stderr_traceback_only(self) -> None:
        self.assertEqual(
            extract_level("Traceback (most recent call last):", stream="stderr"),
            "error",
        )
        self.assertEqual(
            extract_level("routine health ping", stream="stderr"),
            "unknown",
        )
        self.assertEqual(extract_level("hello from stdout"), "unknown")

    def test_vector_yaml_keeps_level_off_stream_fields(self) -> None:
        text = (ROOT / "obsv" / "vector.yaml").read_text()
        self.assertIn(".level =", text)
        self.assertNotIn("_stream_fields=host,container,compose_service,image,network_mode,stream,level", text)
        self.assertIn(
            "_stream_fields=host,container,compose_service,image,network_mode,stream",
            text,
        )


class SignatureTests(unittest.TestCase):
    def test_strips_volatile_tokens(self) -> None:
        sig = normalize_signature(
            "GET /api/v3/series/12 failed uuid=aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee "
            "hash=0123456789abcdef0123456789abcdef path=/data/media/tv/Show/file.mkv"
        )
        self.assertNotIn("12", sig)
        self.assertNotIn("aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee", sig)
        self.assertNotIn("/data/media", sig)
        self.assertEqual(
            normalize_signature("GET /api/v3/series/99 failed"),
            normalize_signature("GET /api/v3/series/12 failed"),
        )


class ConfigRuleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.cfg = load_alerts_config(ROOT / "obsv" / "log-alerts.yaml")

    def test_repo_yaml_has_defaults_and_mutes_self(self) -> None:
        self.assertGreaterEqual(self.cfg["defaults"]["error_spike_threshold"], 1)
        self.assertTrue(is_muted(self.cfg, "log-watch"))
        self.assertTrue(is_muted(self.cfg, "vector"))

    def test_ignore_and_mute_and_spike(self) -> None:
        cfg = {
            "defaults": {"error_spike_threshold": 10, "error_spike_window": "5m"},
            "containers": {
                "gluetun": {
                    "error_spike_threshold": 50,
                    "ignore": ["healthcheck failed"],
                },
                "recyclarr": {"mute": True},
            },
        }
        self.assertTrue(is_ignored(cfg, "gluetun", "ERROR healthcheck failed again"))
        self.assertFalse(is_ignored(cfg, "gluetun", "ERROR authentication failed"))
        self.assertTrue(is_muted(cfg, "recyclarr"))
        self.assertFalse(is_muted(cfg, "sonarr"))
        self.assertEqual(container_settings(cfg, "gluetun")["error_spike_threshold"], 50)
        self.assertTrue(spike_exceeded(10, 10))
        self.assertFalse(spike_exceeded(9, 10))

    def test_parse_duration_and_digest_due(self) -> None:
        self.assertEqual(parse_duration("5m"), 300)
        self.assertEqual(parse_duration("7d"), 7 * 86400)
        self.assertTrue(
            digest_due(
                now_hour=8,
                now_minute=0,
                last_digest_date="2026-10-29",
                today="2026-10-30",
                digest_hour=8,
                digest_minute=0,
            )
        )
        self.assertFalse(
            digest_due(
                now_hour=8,
                now_minute=0,
                last_digest_date="2026-10-30",
                today="2026-10-30",
                digest_hour=8,
                digest_minute=0,
            )
        )

    def test_new_signatures_respects_known_and_min_count(self) -> None:
        known = {"sonarr|http * failed"}
        current = {
            "sonarr|http * failed": 20,
            "sonarr|disk full at /*": 4,
            "sonarr|rare blip": 1,
        }
        new = new_signatures(current, known, min_count=3)
        self.assertEqual(new, {"sonarr|disk full at /*": 4})


class ConfigParseTests(unittest.TestCase):
    def test_log_alerts_yaml_parses(self) -> None:
        with tempfile.TemporaryDirectory():
            cfg = load_alerts_config(ROOT / "obsv" / "log-alerts.yaml")
        self.assertIn("defaults", cfg)
        self.assertIn("containers", cfg)


if __name__ == "__main__":
    raise SystemExit(unittest.main())
