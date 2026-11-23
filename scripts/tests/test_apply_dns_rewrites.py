"""Tests for adguard/apply-dns-rewrites.py."""

from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "adguard" / "apply-dns-rewrites.py"

spec = importlib.util.spec_from_file_location("apply_dns_rewrites", SCRIPT)
assert spec and spec.loader
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

parse_desired = mod.parse_desired
render_rewrites = mod.render_rewrites
replace_filtering_rewrites_block = mod.replace_filtering_rewrites_block


class ApplyDnsRewritesTests(unittest.TestCase):
    def test_parse_desired(self) -> None:
        text = (
            "domain: home.example.com\n"
            "target: 10.0.0.1\n"
            "rewrites:\n"
            "  - \"*.home.example.com\"\n"
            "  - sonarr.home.example.com\n"
            "  - radarr\n"
        )
        domain, target, hosts = parse_desired(text)
        self.assertEqual(domain, "home.example.com")
        self.assertEqual(target, "10.0.0.1")
        self.assertEqual(
            hosts,
            ["*.home.example.com", "sonarr.home.example.com", "radarr"],
        )

    def test_render_wildcard_fqdn(self) -> None:
        block = render_rewrites(
            "home.example.com",
            "10.0.0.1",
            ["*.home.example.com", "sonarr"],
        )
        self.assertIn('domain: "*.home.example.com"', block)
        self.assertIn("domain: sonarr.home.example.com", block)
        self.assertNotIn("domain: *.home.example.com\n", block)

    def test_render_and_replace_filtering_block(self) -> None:
        block = render_rewrites("home.example.com", "10.0.0.1", ["sonarr"])
        config = (
            "filtering:\n"
            "  blocking_mode: default\n"
            "  rewrites: []\n"
            "  safe_fs_patterns:\n"
            "    - /opt/AdGuardHome/userfilters/*\n"
        )
        updated = replace_filtering_rewrites_block(config, block)
        self.assertIn("domain: sonarr.home.example.com", updated)
        self.assertIn("answer: 10.0.0.1", updated)
        self.assertIn("enabled: true", updated)
        self.assertNotIn("rewrites: []", updated)


if __name__ == "__main__":
    unittest.main()
