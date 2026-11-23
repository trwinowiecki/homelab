"""Tests for Jellyfin bootstrap helpers."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from bootstrap.jellyfin import (  # noqa: E402
    LDAP_AUTH_PROVIDER,
    LDAP_CONFIG_NAME,
    LDAP_TMPL,
    find_package,
    is_ldap_provider,
    merge_plugin_repositories,
    package_index,
    policy_with_ldap_provider,
    render_ldap_config,
    upsert_env_key,
)
from minyaml import load as minyaml_load, loads as minyaml_loads  # noqa: E402


class JellyfinBootstrapTests(unittest.TestCase):
    def test_bootstrap_yaml_loads(self) -> None:
        spec = minyaml_loads((ROOT / "jellyfin" / "bootstrap.yml").read_text())
        self.assertIn("repositories", spec)
        self.assertIn("plugins", spec)
        self.assertIn("ldap", spec)
        self.assertIn("taylor", spec["ldap"]["provider_users"])
        self.assertEqual(spec["ldap"]["local_admin"], "admin")
        guids = {p["guid"] for p in spec["plugins"]}
        self.assertIn("958aad66-3784-4d2a-b89a-a7b6fab6e25c", guids)

    def test_merge_plugin_repositories_adds_new_url(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            system_xml = Path(tmp) / "system.xml"
            system_xml.write_text(
                """<?xml version="1.0" encoding="utf-8"?>
<ServerConfiguration>
  <PluginRepositories>
    <RepositoryInfo>
      <Name>Jellyfin Stable</Name>
      <Url>https://repo.jellyfin.org/files/plugin/manifest.json</Url>
      <Enabled>true</Enabled>
    </RepositoryInfo>
  </PluginRepositories>
</ServerConfiguration>
"""
            )
            changed = merge_plugin_repositories(
                system_xml,
                [{"name": "Intro Skipper", "url": "https://intro-skipper.org/manifest.json"}],
            )
            self.assertTrue(changed)
            text = system_xml.read_text()
            self.assertIn("intro-skipper.org", text)

    def test_render_ldap_config_writes_xml(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = Path(tmp)
            (config / "data" / "plugins" / "configurations").mkdir(parents=True)
            env = {"JELLYFIN_CONFIG_DIR": str(config), "LDAP_JELLYFIN_BIND_TOKEN": "test-token"}
            identity_env = {}
            spec = minyaml_loads((ROOT / "jellyfin" / "bootstrap.yml").read_text())
            render_ldap_config(env, identity_env, spec)
            out = config / "data" / "plugins" / "configurations" / LDAP_CONFIG_NAME
            self.assertTrue(out.is_file())
            body = out.read_text()
            self.assertIn(str(spec["ldap"]["server"]), body)
            self.assertIn("test-token", body)
            self.assertIn("jellyfin_users", body)
            self.assertIn("homelab-recovery-flow", body)

    def test_ldap_template_exists(self) -> None:
        self.assertTrue(LDAP_TMPL.is_file())

    def test_find_package_matches_ldap_by_name(self) -> None:
        packages = [
            {
                "guid": "958aad6637844d2ab89aa7b6fab6e25c",
                "name": "LDAP Authentication",
                "versions": [{"version": "5.0.0.0"}],
            }
        ]
        by_guid, by_name = package_index(packages)
        want = {"name": "LDAP-Auth", "guid": "958aad66-3784-4d2a-b89a-a7b6fab6e25c"}
        pkg = find_package(want, by_guid, by_name)
        self.assertIsNotNone(pkg)
        self.assertEqual(pkg["name"], "LDAP Authentication")

    def test_policy_with_ldap_provider(self) -> None:
        policy = policy_with_ldap_provider({"IsAdministrator": True, "AuthenticationProviderId": "Default"})
        self.assertTrue(policy["IsAdministrator"])
        self.assertEqual(policy["AuthenticationProviderId"], LDAP_AUTH_PROVIDER)
        self.assertTrue(is_ldap_provider(LDAP_AUTH_PROVIDER))
        self.assertFalse(is_ldap_provider("Jellyfin.Server.Implementations.Users.DefaultAuthenticationProvider"))

    def test_upsert_env_key_replaces_and_appends(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / ".env"
            path.write_text("FOO=1\nBAR=2\n")
            upsert_env_key(path, "BAR", "3")
            upsert_env_key(path, "BAZ", "4")
            text = path.read_text()
            self.assertIn("FOO=1\n", text)
            self.assertIn("BAR=3\n", text)
            self.assertIn("BAZ=4\n", text)
            self.assertNotIn("BAR=2", text)

    def test_package_version_reads_versions_array(self) -> None:
        from bootstrap.jellyfin import package_version

        self.assertEqual(
            package_version({"versions": [{"version": "14.0.0.0"}]}),
            "14.0.0.0",
        )


if __name__ == "__main__":
    unittest.main()
