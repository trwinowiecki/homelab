#!/usr/bin/env python3
"""Homepage tiles talk to TrueNAS over websocket and Grafana by container name."""
from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SERVICES = (ROOT / "arrstack" / "homepage" / "services.yaml.tmpl").read_text()


class HomepageServicesTests(unittest.TestCase):
    def test_truenas_widget_uses_websocket_api(self) -> None:
        chunk = SERVICES.split("type: truenas", 1)[1][:280]
        self.assertIn("version: 2", chunk)
        self.assertNotIn("version: 1", chunk)
        self.assertIn("url: https://${SITE_TRUENAS_IP}", SERVICES)
        self.assertIn("enablePools: true", SERVICES)

    def test_grafana_widget_uses_container_dns(self) -> None:
        self.assertIn("url: http://grafana:3000", SERVICES)
        self.assertIn("siteMonitor: http://grafana:3000/api/health", SERVICES)
        self.assertNotIn("172.28.10.1:3800", SERVICES)
        self.assertIn("siteMonitor: http://dozzle:8080", SERVICES)
        self.assertNotIn("172.28.10.1:8880", SERVICES)

    def test_jellyfin_uses_site_placeholders(self) -> None:
        self.assertIn("${SITE_JELLYFIN_IP}", SERVICES)
        self.assertIn("https://jellyfin.${SITE_LAN_DOMAIN}", SERVICES)


if __name__ == "__main__":
    raise SystemExit(unittest.main())
