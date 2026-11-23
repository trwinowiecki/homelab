#!/usr/bin/env python3
"""Tests for library-exporter join, stale classification, and Prometheus output."""
from __future__ import annotations

import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "obsv" / "library-exporter"))

from exporter import (  # noqa: E402
    datasets_from_query,
    is_stale,
    jsonrpc_take_result,
    load_exporter_config,
    prom_escape,
    render_metrics,
    scrape_all,
    scrape_truenas,
    truenas_bytes,
    watch_index,
)


NOW = datetime(2026, 10, 30, tzinfo=timezone.utc)


class WatchJoinTests(unittest.TestCase):
    def test_merges_tvdb_and_tmdb_across_users(self) -> None:
        items = [
            {
                "Name": "Kids Show",
                "ProviderIds": {"Tvdb": "111", "Tmdb": "222"},
                "UserData": {
                    "PlayCount": 0,
                    "LastPlayedDate": None,
                },
            },
            {
                "Name": "Kids Show",
                "ProviderIds": {"Tvdb": "111"},
                "UserData": {
                    "PlayCount": 3,
                    "LastPlayedDate": "2026-10-01T00:00:00Z",
                },
            },
            {
                "Name": "Old Movie",
                "ProviderIds": {"Tmdb": "999"},
                "UserData": {"PlayCount": 1, "LastPlayedDate": "2024-01-01T00:00:00.0000000Z"},
            },
        ]
        index = watch_index(items)
        self.assertEqual(index[("tvdb", "111")]["play_count"], 3)
        self.assertTrue(index[("tvdb", "111")]["last_played"].startswith("2026-10-01"))
        self.assertEqual(index[("tmdb", "222")]["play_count"], 0)
        self.assertEqual(index[("tmdb", "999")]["play_count"], 1)


class StaleTests(unittest.TestCase):
    def test_never_watched_is_stale(self) -> None:
        self.assertTrue(is_stale("", NOW, 180))
        self.assertTrue(is_stale(None, NOW, 180))

    def test_recent_is_not_stale_old_is(self) -> None:
        self.assertFalse(is_stale("2026-10-01T00:00:00Z", NOW, 180))
        self.assertTrue(is_stale("2024-01-01T00:00:00Z", NOW, 180))


class TrueNasBytesTests(unittest.TestCase):
    def test_parsed_and_rawvalue(self) -> None:
        self.assertEqual(truenas_bytes({"parsed": 1024, "rawvalue": "1024"}), 1024)
        self.assertEqual(truenas_bytes({"rawvalue": "2048"}), 2048)
        self.assertEqual(truenas_bytes(4096), 4096)
        self.assertEqual(truenas_bytes(None), 0)


class MetricsTests(unittest.TestCase):
    def test_prom_escape_and_library_lines(self) -> None:
        self.assertEqual(prom_escape('Kid\'s "Show"\\n'), 'Kid\'s \\"Show\\"\\\\n')
        items = [
            {
                "app": "sonarr",
                "kind": "series",
                "title": 'Bluey',
                "profile": "Low (720p)",
                "id": "123",
                "bytes": 50_000_000_000,
                "stale": True,
                "watched": False,
                "link": "https://sonarr.home.example.com/series/bluey",
            }
        ]
        datasets = [
            {
                "dataset": "metrion/zinthos/media",
                "used": 100,
                "usedbysnapshots": 0,
                "available": 50,
            }
        ]
        body = render_metrics(items, datasets, up=1)
        self.assertIn('library_item_bytes{app="sonarr"', body)
        self.assertIn('title="Bluey"', body)
        self.assertIn('stale="true"', body)
        self.assertIn('truenas_dataset_usedbysnapshots_bytes{dataset="metrion/zinthos/media"} 0', body)
        self.assertIn("library_exporter_up 1", body)


class TrueNasRpcTests(unittest.TestCase):
    def test_jsonrpc_skips_notifications_and_returns_result(self) -> None:
        messages = [
            {"jsonrpc": "2.0", "method": "ready"},
            {"jsonrpc": "2.0", "id": 1, "result": True},
            {"jsonrpc": "2.0", "id": 2, "result": [{"name": "metrion/zinthos/media"}]},
        ]
        self.assertTrue(jsonrpc_take_result(messages, 1))
        rows = jsonrpc_take_result(messages, 2)
        self.assertEqual(rows[0]["name"], "metrion/zinthos/media")

    def test_jsonrpc_error_raises(self) -> None:
        with self.assertRaises(RuntimeError):
            jsonrpc_take_result([{"id": 1, "error": {"message": "nope"}}], 1)

    def test_datasets_from_query_filters_and_parses(self) -> None:
        rows = [
            {
                "name": "metrion/zinthos/media",
                "used": {"parsed": 100},
                "usedbysnapshots": {"rawvalue": "0"},
                "available": 50,
            },
            {"name": "boot-pool", "used": 1, "usedbysnapshots": 0, "available": 2},
        ]
        out = datasets_from_query(rows, ["metrion/zinthos/media"])
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["used"], 100)
        self.assertEqual(out[0]["usedbysnapshots"], 0)
        self.assertEqual(out[0]["available"], 50)


class ScrapeIsolationTests(unittest.TestCase):
    def test_scrape_truenas_uses_jsonrpc_not_rest(self) -> None:
        calls: list[tuple] = []

        def fake_rpc(base: str, api_key: str, method: str, params: list, tls_verify: bool):
            calls.append((base, api_key, method, params, tls_verify))
            return [
                {
                    "name": "metrion/zinthos/media",
                    "used": 10,
                    "usedbysnapshots": 0,
                    "available": 5,
                }
            ]

        import exporter as exp

        orig = exp.truenas_jsonrpc
        exp.truenas_jsonrpc = fake_rpc  # type: ignore[assignment]
        try:
            out = scrape_truenas(
                {
                    "url": "https://<truenas-host>",
                    "tls_verify": False,
                    "datasets": ["metrion/zinthos/media"],
                },
                "tn-key",
            )
        finally:
            exp.truenas_jsonrpc = orig
        self.assertEqual(calls[0][2], "pool.dataset.query")
        self.assertEqual(out[0]["dataset"], "metrion/zinthos/media")

    def test_scrape_all_keeps_truenas_when_jellyfin_fails(self) -> None:
        import exporter as exp

        def boom(*_a, **_k):
            raise RuntimeError("HTTP Error 401: Unauthorized")

        def fake_truenas(*_a, **_k):
            return [{"dataset": "metrion/zinthos", "used": 1, "usedbysnapshots": 0, "available": 2}]

        orig = (
            exp.scrape_jellyfin,
            exp.scrape_sonarr,
            exp.scrape_radarr,
            exp.scrape_truenas,
            exp.load_exporter_config,
            exp.load_auto_env,
            exp.TRUENAS_API_KEY,
        )
        exp.load_exporter_config = lambda _p: {  # type: ignore[assignment]
            "stale_days": 180,
            "sonarr": {},
            "radarr": {},
            "jellyfin": {},
            "truenas": {},
        }
        exp.load_auto_env = lambda _p: {}  # type: ignore[assignment]
        exp.scrape_jellyfin = boom  # type: ignore[assignment]
        exp.scrape_sonarr = lambda *_a, **_k: []  # type: ignore[assignment]
        exp.scrape_radarr = lambda *_a, **_k: []  # type: ignore[assignment]
        exp.scrape_truenas = fake_truenas  # type: ignore[assignment]
        exp.TRUENAS_API_KEY = "tn-key"
        try:
            body, up = scrape_all()
        finally:
            (
                exp.scrape_jellyfin,
                exp.scrape_sonarr,
                exp.scrape_radarr,
                exp.scrape_truenas,
                exp.load_exporter_config,
                exp.load_auto_env,
                exp.TRUENAS_API_KEY,
            ) = orig
        self.assertEqual(up, 1)
        self.assertIn("truenas_dataset_used_bytes", body)
        self.assertIn("library_exporter_up 1", body)


if __name__ == "__main__":
    raise SystemExit(unittest.main())
