"""Tests for identity blueprint rendering."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from apply_identity_blueprints import blueprint_files  # noqa: E402
from identity_oauth import blueprint_context  # noqa: E402
from render_identity_blueprints import render_template  # noqa: E402


class TestRenderIdentityBlueprints(unittest.TestCase):
    def test_renders_proxy_and_oidc(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            identity_env = Path(tmp) / "identity.env"
            arr_env = Path(tmp) / "arr.env"
            identity_env.write_text(
                "\n".join(
                    [
                        "IDENTITY_LAN_DOMAIN=home.example.com",
                        "IDENTITY_PUBLIC_DOMAIN=example.com",
                        "AUTHENTIK_OIDC_MEALIE_CLIENT_SECRET=mealie-secret",
                        "AUTHENTIK_OIDC_GRAFANA_CLIENT_SECRET=grafana-secret",
                        "AUTHENTIK_OIDC_AUDIOBOOKSHELF_CLIENT_SECRET=abs-secret",
                        "AUTHENTIK_OIDC_PORTAINER_CLIENT_SECRET=portainer-secret",
                        "LDAP_OUTPOST_TOKEN=ldap-outpost-token",
                        "LDAP_JELLYFIN_BIND_TOKEN=jellyfin-bind-token",
                    ]
                )
                + "\n"
            )
            arr_env.write_text("IDENTITY_LAN_DOMAIN=home.example.com\n")

            ctx = blueprint_context(
                dict(line.split("=", 1) for line in identity_env.read_text().splitlines() if "=" in line),
                dict(line.split("=", 1) for line in arr_env.read_text().splitlines() if "=" in line),
            )
            proxy_tmpl = (ROOT / "identity" / "authentik" / "blueprint-proxy.yaml.tmpl").read_text()
            text = render_template(proxy_tmpl, ctx)
            self.assertIn("mode: forward_domain", text)
            self.assertIn("https://auth.home.example.com", text)
            self.assertIn('cookie_domain: "home.example.com"', text)
            self.assertIn("implicit-consent", text)

            oidc_tmpl = (ROOT / "identity" / "authentik" / "blueprint-oidc.yaml.tmpl").read_text()
            oidc = render_template(oidc_tmpl, ctx)
            self.assertIn("homelab-mealie", oidc)
            self.assertIn("https://mealie.home.example.com/api/auth/oauth/callback", oidc)
            self.assertIn("meta_launch_url: \"https://mealie.home.example.com\"", oidc)

    def test_groups_include_vpn_and_notes(self) -> None:
        text = (ROOT / "identity" / "authentik" / "blueprint-groups.yaml").read_text()
        self.assertIn("name: admin", text)
        self.assertIn("name: household", text)
        self.assertIn("name: jellyfin_users", text)
        self.assertIn("name: vpn", text)
        self.assertIn("notes:", text)
        pangolin = (ROOT / "identity" / "authentik" / "blueprint-pangolin.yaml.tmpl").read_text()
        self.assertIn("[name, admin]", pangolin)
        self.assertIn("[name, vpn]", pangolin)

    def test_login_flow_blueprint_is_static(self) -> None:
        text = (ROOT / "identity" / "authentik" / "blueprint-flow-login.yaml").read_text()
        self.assertIn("session_duration: days=30", text)
        self.assertIn("enable_remember_me: true", text)
        self.assertIn("password_stage:", text)
        self.assertIn("slug: homelab-authentication-flow", text)
        self.assertIn("domain: authentik-default", text)
        self.assertIn("recovery_flow:", text)
        self.assertIn("homelab-recovery-flow", text)
        self.assertIn("flow_user_settings:", text)
        self.assertIn("default-user-settings-flow", text)
        self.assertIn("homelab-operator-needs-mfa", text)
        self.assertIn("not_configured_action: configure", text)
        self.assertIn("reputationpolicy", text)
        ldap = (ROOT / "identity" / "authentik" / "blueprint-ldap.yaml.tmpl").read_text()
        self.assertIn("mfa_support: false", ldap)
        self.assertIn("homelab-ldap-authentication-flow", ldap)
        ldap_flow = (ROOT / "identity" / "authentik" / "blueprint-flow-ldap.yaml").read_text()
        self.assertIn("slug: homelab-ldap-authentication-flow", ldap_flow)
        self.assertIn("require_outpost", ldap_flow)
        self.assertNotIn("authenticator", ldap_flow)

    def test_recovery_and_enrollment_blueprints(self) -> None:
        recovery = (ROOT / "identity" / "authentik" / "blueprint-flow-recovery.yaml").read_text()
        self.assertIn("slug: homelab-recovery-flow", recovery)
        self.assertIn("is_restored", recovery)
        self.assertIn("homelab-recovery-ask-admin", recovery)
        enroll = (ROOT / "identity" / "authentik" / "blueprint-flow-enrollment.yaml").read_text()
        self.assertIn("slug: homelab-enrollment-flow", enroll)
        self.assertIn("continue_flow_without_invitation: false", enroll)
        events = (ROOT / "identity" / "authentik" / "blueprint-events.yaml").read_text()
        self.assertIn("action: login_failed", events)

    def test_discord_webhook_appends_slack_when_set(self) -> None:
        ctx = blueprint_context(
            {
                "IDENTITY_LAN_DOMAIN": "home.example.com",
                "AUTHENTIK_DISCORD_WEBHOOK_URL": "https://discord.com/api/webhooks/1/abc",
            },
            {},
        )
        self.assertEqual(
            ctx["AUTHENTIK_DISCORD_WEBHOOK_URL"],
            "https://discord.com/api/webhooks/1/abc/slack",
        )
        empty = blueprint_context({"IDENTITY_LAN_DOMAIN": "home.example.com"}, {})
        self.assertEqual(empty["AUTHENTIK_DISCORD_WEBHOOK_URL"], "")

    def test_optional_blueprints_skipped_when_missing(self) -> None:
        files = blueprint_files()
        self.assertIn("homelab/blueprint-flow-login.yaml", files)
        self.assertIn("homelab/blueprint-flow-recovery.yaml", files)
        self.assertIn("homelab/blueprint-events.yaml", files)
        discord = ROOT / "identity" / "authentik" / "blueprint-events-discord.yaml"
        if discord.is_file():
            self.assertIn("homelab/blueprint-events-discord.yaml", files)
        else:
            self.assertNotIn("homelab/blueprint-events-discord.yaml", files)


if __name__ == "__main__":
    unittest.main()
