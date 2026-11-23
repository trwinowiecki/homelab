#!/usr/bin/env python3
"""Tests for SSD orphan cleanup, including usenet completed folders."""
from __future__ import annotations

import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def load_cleanup():
    path = ROOT / "scripts" / "qbit-ssd-cleanup.py"
    spec = importlib.util.spec_from_file_location("qbit_ssd_cleanup", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class UsenetOrphanTests(unittest.TestCase):
    def test_queue_storage_is_tracked(self) -> None:
        cleanup = load_cleanup()
        paths = cleanup.sab_queue_dirs(
            {
                "queue": {
                    "slots": [
                        {"storage": "/downloads/usenet/complete/tv/Show.S01E01"},
                        {"storage": ""},
                        {"filename": "not-a-path"},
                    ]
                }
            }
        )
        self.assertEqual(paths, {"/downloads/usenet/complete/tv/Show.S01E01"})

    def test_usenet_complete_orphan_skipped_when_sab_unknown(self) -> None:
        cleanup = load_cleanup()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            show = root / "usenet/complete/tv/Show.S01E01"
            show.mkdir(parents=True)
            cleanup.DOWNLOADS_ROOT = root
            found = cleanup.scan_orphans(set(), include_usenet=False)
            self.assertEqual(found, [])
            found = cleanup.scan_orphans(set(), include_usenet=True)
            self.assertEqual(found, [show])


class UnpackAbortBlocklistTests(unittest.TestCase):
    def test_releases_a_single_local_abort(self) -> None:
        cleanup = load_cleanup()
        records = [
            {"id": 5, "sourceTitle": "Show.S01E01", "message": "Aborted, cannot be completed - https://sabnzbd.org/not-complete"},
            {"id": 6, "sourceTitle": "Bad.Release", "message": "RAR files failed to verify"},
        ]
        self.assertEqual(cleanup.blocklist_ids_to_release(records, {"Show.S01E01": 1}), [5])

    def test_keeps_a_release_that_already_failed_twice(self) -> None:
        cleanup = load_cleanup()
        records = [
            {"id": 5, "sourceTitle": "Show.S01E01", "message": "https://sabnzbd.org/not-complete"},
        ]
        self.assertEqual(cleanup.blocklist_ids_to_release(records, {"Show.S01E01": 2}), [])

    def test_search_ids_follow_the_released_records(self) -> None:
        cleanup = load_cleanup()
        records = [
            {"id": 5, "episodeIds": [10, 11], "movieId": None},
            {"id": 8, "episodeIds": [], "movieId": 3},
        ]
        episodes, movies = cleanup.search_ids_for_blocklist(records, {5, 8})
        self.assertEqual(episodes, [10, 11])
        self.assertEqual(movies, [3])


if __name__ == "__main__":
    unittest.main()
