#!/usr/bin/env python3
"""Tests for NZBGeek indexer body and SABnzbd server settings."""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from arrstack_api_keys import read_sab_ini  # noqa: E402
from bootstrap.prowlarr import build_indexer_body  # noqa: E402
from bootstrap.sabnzbd import access_settings, download_dir_paths, ensure_download_dirs, server_settings  # noqa: E402


NZBGEEK = {
    "name": "NZBGeek",
    "type": "newznab",
    "base_url": "https://api.nzbgeek.info",
    "api_key_env": "NZBGEEK_API_KEY",
    "categories": [2000, 5000, 3000, 7000],
    "priority": 25,
}

FRUGAL = {
    "server": {
        "name": "frugal",
        "host_env": "USENET_HOST",
        "port_env": "USENET_PORT",
        "username_env": "USENET_USERNAME",
        "password_env": "USENET_PASSWORD",
        "ssl_env": "USENET_SSL",
        "connections_env": "USENET_CONNECTIONS",
        "connections_default": 50,
    }
}


class SabnzbdIniTests(unittest.TestCase):
    def test_reads_api_key_after_preamble(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "sabnzbd.ini"
            path.write_text(
                "__version__ = 19\n"
                "__encoding__ = utf-8\n"
                "[misc]\n"
                "config_lock = 0\n"
                "api_key = abcdef\n"
                "complete_dir = Downloads/complete\n"
                "[logging]\n"
                "log_level = 1\n"
            )
            self.assertEqual(read_sab_ini(path), "abcdef")


class NewznabIndexerTests(unittest.TestCase):
    def test_categories_and_api_key_from_env_file(self) -> None:
        os.environ.pop("NZBGEEK_API_KEY", None)
        with tempfile.TemporaryDirectory() as tmp:
            env_file = Path(tmp) / ".env"
            env_file.write_text("NZBGEEK_API_KEY=test-key\n")
            body = build_indexer_body(NZBGEEK, [], 1, [], env_file)
        self.assertIsNotNone(body)
        assert body is not None
        fields = {field["name"]: field["value"] for field in body["fields"]}
        self.assertEqual(fields["baseUrl"], "https://api.nzbgeek.info")
        self.assertEqual(fields["apiKey"], "test-key")
        self.assertEqual(fields["categories"], [2000, 5000, 3000, 7000])

    def test_missing_api_key_skips(self) -> None:
        os.environ.pop("NZBGEEK_API_KEY", None)
        with tempfile.TemporaryDirectory() as tmp:
            env_file = Path(tmp) / ".env"
            env_file.write_text("NZBGEEK_API_KEY=\n")
            body = build_indexer_body(NZBGEEK, [], 1, [], env_file)
        self.assertIsNone(body)


class SabnzbdServerTests(unittest.TestCase):
    def test_server_settings_from_env_map(self) -> None:
        name, settings = server_settings(
            FRUGAL,
            {
                "USENET_HOST": "news.example.com",
                "USENET_PORT": "563",
                "USENET_USERNAME": "user",
                "USENET_PASSWORD": "secret",
                "USENET_SSL": "true",
                "USENET_CONNECTIONS": "50",
            },
        ) or ("", [])
        self.assertEqual(name, "frugal")
        values = dict(settings)
        self.assertEqual(values["host"], "news.example.com")
        self.assertEqual(values["port"], "563")
        self.assertEqual(values["ssl"], "1")
        self.assertEqual(values["ssl_verify"], "3")
        self.assertEqual(values["connections"], "50")
        self.assertEqual(values["password"], "secret")
        self.assertNotIn("apikey", values)

    def test_connections_default_when_unset(self) -> None:
        _name, settings = server_settings(
            FRUGAL,
            {
                "USENET_HOST": "news.example.com",
                "USENET_USERNAME": "user",
                "USENET_PASSWORD": "secret",
            },
        ) or ("", [])
        self.assertEqual(dict(settings)["connections"], "50")
        self.assertEqual(dict(settings)["port"], "563")

    def test_missing_provider_skips(self) -> None:
        self.assertIsNone(server_settings(FRUGAL, {"USENET_HOST": "CHANGE_ME"}))

    def test_access_settings_include_container_and_lan_names(self) -> None:
        values = dict(
            access_settings(
                {
                    "IDENTITY_LAN_DOMAIN": "home.example",
                    "DOCKER_SUBNET": "172.28.10.0/24",
                    "LOCAL_SUBNET": "<lan-cidr>",
                }
            )
        )
        self.assertEqual(
            values["host_whitelist"],
            "sabnzbd,localhost,127.0.0.1,sabnzbd.home.example",
        )
        self.assertNotIn("local_ranges", values)

    def test_download_dirs_created_for_categories(self) -> None:
        spec = {
            "categories": [
                {"name": "tv", "dir": "tv"},
                {"name": "movies", "dir": "movies"},
            ]
        }
        with tempfile.TemporaryDirectory() as tmp:
            env_map = {
                "FOLDER_FOR_DOWNLOADS": tmp,
                "PUID": str(os.getuid()),
                "PGID": str(os.getgid()),
            }
            paths = download_dir_paths(spec, env_map)
            self.assertIn(Path(tmp) / "usenet/complete/tv", paths)
            ensure_download_dirs(spec, env_map, dry_run=False)
            self.assertTrue((Path(tmp) / "usenet/incomplete").is_dir())
            self.assertTrue((Path(tmp) / "usenet/complete/movies").is_dir())

    def test_complete_dir_on_media_share(self) -> None:
        spec = {
            "complete_dir": "/data/usenet/complete",
            "incomplete_dir": "/downloads/usenet/incomplete",
            "categories": [{"name": "tv", "dir": "tv"}],
        }
        with tempfile.TemporaryDirectory() as downloads, tempfile.TemporaryDirectory() as media:
            env_map = {
                "FOLDER_FOR_DOWNLOADS": downloads,
                "FOLDER_FOR_MEDIA": media,
                "PUID": str(os.getuid()),
                "PGID": str(os.getgid()),
            }
            paths = download_dir_paths(spec, env_map)
            self.assertIn(Path(downloads) / "usenet/incomplete", paths)
            self.assertIn(Path(media) / "usenet/complete/tv", paths)
            self.assertNotIn(Path(downloads) / "usenet/complete/tv", paths)
            ensure_download_dirs(spec, env_map, dry_run=False)
            self.assertTrue((Path(media) / "usenet/complete/tv").is_dir())
            self.assertTrue((Path(downloads) / "usenet/incomplete").is_dir())

    def test_bad_connections_rejected(self) -> None:
        with self.assertRaises(ValueError):
            server_settings(
                FRUGAL,
                {
                    "USENET_HOST": "news.example.com",
                    "USENET_USERNAME": "user",
                    "USENET_PASSWORD": "secret",
                    "USENET_CONNECTIONS": "lots",
                },
            )


if __name__ == "__main__":
    unittest.main()
