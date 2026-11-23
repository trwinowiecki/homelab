"""Tests for identity Caddyfile rendering."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
import sys

sys.path.insert(0, str(ROOT / "scripts"))
from render_identity_caddy import load_env, render  # noqa: E402


class RenderIdentityCaddyTests(unittest.TestCase):
    def test_load_env_skips_comments(self) -> None:
        with tempfile.NamedTemporaryFile("w", delete=False) as f:
            f.write("# comment\nFOO=bar\n")
            path = Path(f.name)
        self.assertEqual(load_env(path)["FOO"], "bar")
        path.unlink()

    def test_render_produces_sonarr_block(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            identity_env = Path(tmp) / "identity.env"
            arr_env = Path(tmp) / "arr.env"
            identity_env.write_text(
                "IDENTITY_LAN_DOMAIN=home.example.com\n"
                "IDENTITY_PUBLIC_DOMAIN=example.com\n"
                "IDENTITY_ACME_DNS=false\n"
            )
            arr_env.write_text(
                "WEBUI_PORT_QBITTORRENT=8200\n"
                "WEBUI_PORT_MEALIE_OG_SYNC=9926\n"
                "WEBUI_PORT_DOZZLE=8880\n"
                "WEBUI_PORT_GRAFANA=3800\n"
            )
            out = Path(tmp) / "Caddyfile"
            rc = render(identity_env, arr_env, out=out)
            self.assertEqual(rc, 0)
            text = out.read_text()
            self.assertIn("sonarr.home.example.com", text)
            self.assertIn("forward_auth authentik-server:9000", text)
            self.assertIn("@oauth_callback query code=*", text)
            self.assertIn("/api/auth/oauth/*", text)
            self.assertIn("X-Forwarded-Proto https", text)
            # qBit shares gluetun's netns and publishes 127.0.0.1:8200 on the host,
            # so host.docker.internal (docker0) cannot reach it.
            self.assertIn("reverse_proxy gluetun:8200", text)
            self.assertNotIn("reverse_proxy host.docker.internal:8200", text)
            self.assertIn("reverse_proxy dozzle:8080", text)
            self.assertIn("reverse_proxy grafana:3000", text)
            self.assertIn("reverse_proxy mealie-og-sync:9926", text)
            self.assertNotIn("reverse_proxy host.docker.internal", text)
            self.assertIn("tls internal", text)
            self.assertIn("*.home.example.com", text)
            self.assertNotIn("acme_dns cloudflare", text)

    def test_render_acme_uses_cloudflare_dns(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            identity_env = Path(tmp) / "identity.env"
            arr_env = Path(tmp) / "arr.env"
            identity_env.write_text(
                "IDENTITY_LAN_DOMAIN=home.example.com\n"
                "IDENTITY_PUBLIC_DOMAIN=example.com\n"
                "IDENTITY_ACME_DNS=true\n"
                "CADDY_ACME_EMAIL=admin@example.com\n"
                "CLOUDFLARE_API_TOKEN=test-token\n"
            )
            arr_env.write_text(
                "WEBUI_PORT_QBITTORRENT=8200\n"
                "WEBUI_PORT_MEALIE_OG_SYNC=9926\n"
                "WEBUI_PORT_DOZZLE=8880\n"
                "WEBUI_PORT_GRAFANA=3800\n"
            )
            out = Path(tmp) / "Caddyfile"
            rc = render(identity_env, arr_env, out=out)
            self.assertEqual(rc, 0)
            text = out.read_text()
            self.assertIn("acme_dns cloudflare {env.CLOUDFLARE_API_TOKEN}", text)
            self.assertIn("email admin@example.com", text)
            self.assertIn("*.home.example.com", text)
            self.assertIn("sonarr.home.example.com", text)
            self.assertNotIn("test-token", text)
            self.assertNotIn("tls internal\n\treverse_proxy sonarr", text)
            self.assertIn("auth.example.com", text)
            self.assertIn("tls internal", text)

    def test_render_acme_requires_token(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            identity_env = Path(tmp) / "identity.env"
            arr_env = Path(tmp) / "arr.env"
            identity_env.write_text(
                "IDENTITY_LAN_DOMAIN=home.example.com\n"
                "IDENTITY_PUBLIC_DOMAIN=example.com\n"
                "IDENTITY_ACME_DNS=true\n"
                "CADDY_ACME_EMAIL=admin@example.com\n"
            )
            arr_env.write_text("WEBUI_PORT_QBITTORRENT=8200\n")
            out = Path(tmp) / "Caddyfile"
            rc = render(identity_env, arr_env, out=out)
            self.assertEqual(rc, 1)
            self.assertFalse(out.is_file())


if __name__ == "__main__":
    unittest.main()
