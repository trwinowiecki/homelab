#!/usr/bin/env python3
"""Apply Jellyfin plugins, repositories, and LDAP from jellyfin/bootstrap.yml."""

from __future__ import annotations

import argparse
import json
import os
import secrets
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from minyaml import loads as minyaml_loads  # noqa: E402

from bootstrap.common import load_env_file, wait_http  # noqa: E402

BOOT = ROOT / "jellyfin" / "bootstrap.yml"
PLUGIN_CONFIG_SRC = ROOT / "jellyfin" / "plugin-config"
LDAP_TMPL = ROOT / "jellyfin" / "ldap-auth.xml.tmpl"

PLACEHOLDER = frozenset({"", "CHANGE_ME", "change_me"})

# Jellyfin.Plugin.LDAP_Auth v22+ stores config as LDAP-Auth.xml under data/plugins/configurations.
LDAP_CONFIG_NAME = "LDAP-Auth.xml"
LDAP_AUTH_PROVIDER = "Jellyfin.Plugin.LDAP_Auth.LdapAuthenticationProviderPlugin"

BRANDING_SRC = ROOT / "jellyfin" / "branding.xml"

INTRO_SKIPPER_CONFIG = "IntroSkipper.xml"

STATIC_PLUGIN_CONFIGS = {
    "Jellyfin.Plugin.FileTransformation.xml": "Jellyfin.Plugin.FileTransformation.xml",
    INTRO_SKIPPER_CONFIG: INTRO_SKIPPER_CONFIG,
}


def jellyfin_base_url(env: dict[str, str]) -> str:
    host = env.get("JELLYFIN_BOOTSTRAP_HOST", "").strip()
    if not host:
        host = env.get("LOCAL_DOCKER_IP", "127.0.0.1").strip() or "127.0.0.1"
    port = env.get("WEBUI_PORT_JELLYFIN", "8096")
    return f"http://{host}:{port}"


def emby_auth_header(token: str = "") -> str:
    header = (
        'MediaBrowser Client="homelab-bootstrap", '
        'Device="homelab-bootstrap", DeviceId="homelab-bootstrap", '
        'Version="12.0.0"'
    )
    if token:
        header = f'{header}, Token="{token}"'
    return header


def jellyfin_request(
    method: str,
    url: str,
    *,
    token: str = "",
    body: dict | None = None,
) -> tuple[int, object | bytes]:
    headers = {"Authorization": emby_auth_header(token)}
    data = None
    if body is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            raw = resp.read()
            if not raw:
                return resp.status, None
            try:
                return resp.status, json.loads(raw)
            except json.JSONDecodeError:
                return resp.status, raw
    except urllib.error.HTTPError as exc:
        detail = exc.read()
        try:
            parsed = json.loads(detail) if detail else None
        except json.JSONDecodeError:
            parsed = detail
        return exc.code, parsed
    except urllib.error.URLError as exc:
        return 0, str(exc)


def startup_complete(base: str) -> bool:
    status, data = jellyfin_request("GET", f"{base}/System/Info/Public")
    if status != 200 or not isinstance(data, dict):
        return False
    return bool(data.get("StartupWizardCompleted"))


def complete_startup(base: str, env: dict[str, str]) -> bool:
    if startup_complete(base):
        return True
    user = env.get("JELLYFIN_ADMIN_USER", "admin").strip()
    password = env.get("JELLYFIN_ADMIN_PASSWORD", "").strip()
    if not password or password in PLACEHOLDER:
        print("skip jellyfin bootstrap: startup wizard incomplete and JELLYFIN_ADMIN_PASSWORD unset")
        return False

    status, _ = jellyfin_request(
        "POST",
        f"{base}/Startup/User",
        body={"Name": user, "Password": password},
    )
    if status not in (200, 204):
        print(f"WARN: Startup/User returned HTTP {status}")
        return False
    status, _ = jellyfin_request("POST", f"{base}/Startup/Complete", body={})
    if status not in (200, 204):
        print(f"WARN: Startup/Complete returned HTTP {status}")
        return False
    print("jellyfin: completed startup wizard")
    return wait_http(f"{base}/System/Info/Public", timeout_s=60)


def authenticate(base: str, env: dict[str, str], *, quiet: bool = False) -> str:
    api_key = env.get("JELLYFIN_API_KEY", "").strip()
    if api_key and api_key not in PLACEHOLDER:
        status, data = jellyfin_request("GET", f"{base}/System/Info", token=api_key)
        if status == 200:
            return api_key

    candidates = [
        (env.get("JELLYFIN_ADMIN_USER", "admin").strip(), env.get("JELLYFIN_ADMIN_PASSWORD", "").strip()),
        (
            env.get("JELLYFIN_LOCAL_ADMIN_USER", "admin").strip(),
            env.get("JELLYFIN_LOCAL_ADMIN_PASSWORD", "").strip(),
        ),
    ]
    seen: set[tuple[str, str]] = set()
    for user, password in candidates:
        if not user or not password or password in PLACEHOLDER:
            continue
        if (user, password) in seen:
            continue
        seen.add((user, password))
        status, data = jellyfin_request(
            "POST",
            f"{base}/Users/AuthenticateByName",
            body={"Username": user, "Pw": password},
        )
        if status == 200 and isinstance(data, dict):
            token = str((data.get("AccessToken") or "")).strip()
            if token:
                if not quiet:
                    print("jellyfin: authenticated as admin")
                return token

    if not quiet:
        print("skip jellyfin bootstrap: no valid JELLYFIN_API_KEY or admin password")
    return ""


STATE_KEYS = (
    "JELLYFIN_API_KEY",
    "JELLYFIN_LOCAL_ADMIN_USER",
    "JELLYFIN_LOCAL_ADMIN_PASSWORD",
)


def state_path(env: dict[str, str]) -> Path:
    # Lives on the CT 104 SSD, next to Jellyfin's own config. deploy.sh
    # overwrites jellyfin/.env from the repo on every deploy, so a key
    # written only there is lost and the next run mints another one.
    return config_dir(env) / "homelab-bootstrap.env"


def merge_state(env: dict[str, str]) -> None:
    path = state_path(env)
    if not path.is_file():
        return
    saved = load_env_file(path)
    for key in STATE_KEYS:
        current = env.get(key, "").strip()
        saved_val = saved.get(key, "").strip()
        if saved_val and (not current or current in PLACEHOLDER):
            env[key] = saved_val


def remember_secret(env: dict[str, str], env_file: Path, name: str, value: str) -> None:
    upsert_env_key(env_file, name, value)
    path = state_path(env)
    path.parent.mkdir(parents=True, exist_ok=True)
    upsert_env_key(path, name, value)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def upsert_env_key(path: Path, name: str, value: str) -> None:
    if not path.is_file():
        path.write_text(f"{name}={value}\n")
        return
    lines = path.read_text().splitlines()
    found = False
    out: list[str] = []
    for line in lines:
        stripped = line.strip()
        if stripped.startswith(f"{name}="):
            out.append(f"{name}={value}")
            found = True
        else:
            out.append(line)
    if not found:
        if out and out[-1] != "":
            out.append("")
        out.append(f"{name}={value}")
    path.write_text("\n".join(out) + "\n")


def policy_with_ldap_provider(policy: dict) -> dict:
    updated = dict(policy)
    updated["AuthenticationProviderId"] = LDAP_AUTH_PROVIDER
    return updated


def is_ldap_provider(provider_id: str) -> bool:
    return "LDAP_Auth" in provider_id or provider_id.endswith("LdapAuthenticationProviderPlugin")


def create_api_key(base: str, token: str) -> str:
    status, data = jellyfin_request(
        "POST",
        f"{base}/Auth/Keys?app=homelab-bootstrap",
        token=token,
    )
    if status == 200 and isinstance(data, dict):
        key = str(data.get("AccessToken") or "").strip()
        if key:
            print("jellyfin: minted bootstrap API key")
            return key
    return ""


def config_dir(env: dict[str, str]) -> Path:
    return Path(env.get("JELLYFIN_CONFIG_DIR", "/arrstack/jellyfin/config"))


def plugin_config_dir(env: dict[str, str]) -> Path:
    # Jellyfin reads plugin XML from config/data/plugins/configurations (not config/plugins/...).
    return config_dir(env) / "data" / "plugins" / "configurations"


def installed_plugin_guids(env: dict[str, str]) -> set[str]:
    roots = [
        config_dir(env) / "plugins",
        config_dir(env) / "data" / "plugins",
    ]
    guids: set[str] = set()
    for plugins_root in roots:
        if not plugins_root.is_dir():
            continue
        for meta in plugins_root.glob("*/meta.json"):
            try:
                data = json.loads(meta.read_text())
            except (OSError, json.JSONDecodeError):
                continue
            guid = str(data.get("guid") or "").strip().lower()
            if guid:
                guids.add(normalize_guid(guid))
    return guids


def merge_plugin_repositories(system_xml: Path, repositories: list[dict]) -> bool:
    if not system_xml.is_file():
        print(f"WARN: missing {system_xml}")
        return False

    ET.register_namespace("xsi", "http://www.w3.org/2001/XMLSchema-instance")
    ET.register_namespace("xsd", "http://www.w3.org/2001/XMLSchema")

    tree = ET.parse(system_xml)
    root = tree.getroot()

    repos_parent = root.find("PluginRepositories")
    if repos_parent is None:
        repos_parent = ET.SubElement(root, "PluginRepositories")

    existing_urls = {
        (child.findtext("Url") or "").strip()
        for child in repos_parent.findall("RepositoryInfo")
    }

    changed = False
    for repo in repositories:
        url = str(repo.get("url") or "").strip()
        name = str(repo.get("name") or url).strip()
        if not url or url in existing_urls:
            continue
        info = ET.SubElement(repos_parent, "RepositoryInfo")
        ET.SubElement(info, "Name").text = name
        ET.SubElement(info, "Url").text = url
        ET.SubElement(info, "Enabled").text = "true"
        existing_urls.add(url)
        changed = True
        print(f"jellyfin: added plugin repository {name}")

    if changed:
        tree.write(system_xml, encoding="utf-8", xml_declaration=True)
    return changed


def normalize_guid(guid: str) -> str:
    return guid.replace("-", "").strip().lower()


def package_version(pkg: dict) -> str:
    version = str(pkg.get("version") or "").strip()
    if version:
        return version
    versions = pkg.get("versions")
    if isinstance(versions, list) and versions:
        first = versions[0]
        if isinstance(first, dict):
            return str(first.get("version") or first.get("VersionNumber") or "").strip()
    return ""


def fetch_packages(
    base: str,
    token: str,
    *,
    quiet: bool = False,
    retries: int = 1,
    retry_delay_s: float = 0,
) -> list[dict]:
    for attempt in range(retries):
        status, data = jellyfin_request("GET", f"{base}/Packages", token=token)
        if status == 200 and isinstance(data, list):
            return [pkg for pkg in data if isinstance(pkg, dict)]
        if attempt + 1 < retries:
            time.sleep(retry_delay_s)
            continue
        if not quiet:
            print(f"WARN: GET /Packages failed (HTTP {status})")
    return []


def wait_for_jellyfin_ready(base: str, env: dict[str, str], timeout_s: int = 180) -> str:
    """Wait until Jellyfin accepts auth and serves the plugin catalog."""
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        if not wait_http(f"{base}/System/Info/Public", timeout_s=5):
            time.sleep(2)
            continue
        token = authenticate(base, env, quiet=True)
        if not token:
            time.sleep(3)
            continue
        packages = fetch_packages(base, token, quiet=True, retries=3, retry_delay_s=2)
        if packages:
            print("jellyfin: authenticated as admin")
            return token
        time.sleep(3)
    print("WARN: Jellyfin API not ready (auth or plugin catalog)")
    return ""


def install_plugin(base: str, token: str, pkg: dict) -> bool:
    name = str(pkg.get("name") or "").strip()
    version = package_version(pkg)
    guid = str(pkg.get("guid") or pkg.get("assemblyGuid") or "").strip()
    if not name or not version:
        print(f"WARN: install {name or 'plugin'} missing version in catalog")
        return False
    query = urllib.parse.urlencode(
        {
            "AssemblyGuid": guid,
            "Version": version,
            "UpdateClass": "Plugin",
            "VersionMB": version,
        }
    )
    url = f"{base}/Packages/Installed/{urllib.parse.quote(name)}?{query}"
    status, _ = jellyfin_request("POST", url, token=token, body={})
    if status in (200, 204):
        print(f"jellyfin: installed plugin {name} {version}")
        return True
    print(f"WARN: install {name} failed (HTTP {status})")
    return False


def package_index(packages: list[dict]) -> tuple[dict[str, dict], dict[str, dict]]:
    by_guid: dict[str, dict] = {}
    by_name: dict[str, dict] = {}
    for pkg in packages:
        guid = normalize_guid(str(pkg.get("guid") or pkg.get("assemblyGuid") or ""))
        name = str(pkg.get("name") or "").strip().lower()
        if guid:
            by_guid[guid] = pkg
        if name:
            by_name[name] = pkg
    return by_guid, by_name


def find_package(
    want: dict,
    by_guid: dict[str, dict],
    by_name: dict[str, dict],
) -> dict | None:
    guid = normalize_guid(str(want.get("guid") or ""))
    name = str(want.get("name") or "").strip().lower()
    if guid and guid in by_guid:
        return by_guid[guid]
    if name and name in by_name:
        return by_name[name]
    # LDAP plugin is listed as "LDAP Authentication" in the stable catalog.
    if name == "ldap-auth" and "ldap authentication" in by_name:
        return by_name["ldap authentication"]
    return None


def ensure_plugins(
    base: str,
    token: str,
    env: dict[str, str],
    desired: list[dict],
) -> bool:
    installed = installed_plugin_guids(env)
    missing = [
        p
        for p in desired
        if normalize_guid(str(p.get("guid") or "")) not in installed
    ]
    if not missing:
        print("jellyfin: all plugins already present")
        return False

    packages = fetch_packages(base, token, retries=5, retry_delay_s=3)
    by_guid, by_name = package_index(packages)

    changed = False
    for want in missing:
        guid = normalize_guid(str(want.get("guid") or ""))
        name = str(want.get("name") or "").strip()
        pkg = find_package(want, by_guid, by_name)
        if not pkg:
            packages = fetch_packages(base, token, retries=3, retry_delay_s=2)
            by_guid, by_name = package_index(packages)
            pkg = find_package(want, by_guid, by_name)
        if not pkg:
            print(f"WARN: plugin {name} ({guid}) not found in catalog")
            continue
        if install_plugin(base, token, pkg):
            changed = True
    return changed


def copy_branding(env: dict[str, str]) -> None:
    if not BRANDING_SRC.is_file():
        return
    dest = config_dir(env) / "branding.xml"
    shutil.copy2(BRANDING_SRC, dest)
    print("jellyfin: wrote branding.xml")


def copy_static_plugin_configs(env: dict[str, str]) -> None:
    dest = plugin_config_dir(env)
    dest.mkdir(parents=True, exist_ok=True)
    for src_name, dest_name in STATIC_PLUGIN_CONFIGS.items():
        src = PLUGIN_CONFIG_SRC / src_name
        if not src.is_file():
            continue
        target = dest / dest_name
        shutil.copy2(src, target)
        print(f"jellyfin: wrote plugin config {dest_name}")


def _password_reset_url(
    env: dict[str, str], identity_env: dict[str, str], ldap: dict
) -> str:
    explicit = str(ldap.get("password_reset_url") or "").strip()
    if explicit:
        return explicit
    lan = (
        identity_env.get("IDENTITY_LAN_DOMAIN")
        or env.get("IDENTITY_LAN_DOMAIN")
        or "<lan-domain>"
    ).strip()
    return f"https://auth.{lan}/if/flow/homelab-recovery-flow/"


def render_ldap_config(env: dict[str, str], identity_env: dict[str, str], spec: dict) -> None:
    bind_token = env.get("LDAP_JELLYFIN_BIND_TOKEN", "").strip()
    if not bind_token or bind_token in PLACEHOLDER:
        bind_token = identity_env.get("LDAP_JELLYFIN_BIND_TOKEN", "").strip()
    if not bind_token or bind_token in PLACEHOLDER:
        print("WARN: LDAP_JELLYFIN_BIND_TOKEN unset — skipping LDAP plugin config")
        return

    if not LDAP_TMPL.is_file():
        print(f"WARN: missing {LDAP_TMPL}")
        return

    ldap = spec.get("ldap") or {}
    replacements = {
        "{{LDAP_SERVER}}": str(ldap.get("server") or "authentik-ldap-outpost"),
        "{{LDAP_PORT}}": str(ldap.get("port") or 3389),
        "{{LDAP_USE_SSL}}": "true" if ldap.get("use_ssl") else "false",
        "{{LDAP_BIND_USER}}": str(ldap.get("bind_user") or ""),
        "{{LDAP_BIND_PASSWORD}}": bind_token,
        "{{LDAP_BASE_DN}}": str(ldap.get("base_dn") or ""),
        "{{LDAP_SEARCH_FILTER}}": str(ldap.get("search_filter") or ""),
        "{{LDAP_SEARCH_ATTRIBUTES}}": str(ldap.get("search_attributes") or ""),
        "{{LDAP_UID_ATTRIBUTE}}": str(ldap.get("uid_attribute") or "uid"),
        "{{LDAP_USERNAME_ATTRIBUTE}}": str(ldap.get("username_attribute") or "cn"),
        "{{CREATE_USERS_FROM_LDAP}}": (
            "true" if ldap.get("create_users_from_ldap", True) else "false"
        ),
        "{{ENABLE_ALL_FOLDERS}}": (
            "true" if ldap.get("enable_all_folders", True) else "false"
        ),
        "{{PASSWORD_RESET_URL}}": _password_reset_url(env, identity_env, ldap),
    }
    text = LDAP_TMPL.read_text()
    for key, val in replacements.items():
        text = text.replace(key, val)

    dest = plugin_config_dir(env)
    dest.mkdir(parents=True, exist_ok=True)
    (dest / LDAP_CONFIG_NAME).write_text(text)
    print(f"jellyfin: wrote {LDAP_CONFIG_NAME}")


def list_users(base: str, token: str) -> list[dict]:
    status, data = jellyfin_request("GET", f"{base}/Users", token=token)
    if status != 200 or not isinstance(data, list):
        print(f"WARN: GET /Users failed (HTTP {status})")
        return []
    return [user for user in data if isinstance(user, dict)]


def ensure_local_admin(
    base: str,
    token: str,
    env: dict[str, str],
    spec: dict,
    env_file: Path,
) -> None:
    ldap = spec.get("ldap") or {}
    name = str(ldap.get("local_admin") or env.get("JELLYFIN_LOCAL_ADMIN_USER") or "admin").strip()
    if not name:
        return
    users = {str(user.get("Name") or ""): user for user in list_users(base, token)}
    if name in users:
        print(f"jellyfin: local admin {name} already present")
        return

    password = env.get("JELLYFIN_LOCAL_ADMIN_PASSWORD", "").strip()
    if not password or password in PLACEHOLDER:
        password = secrets.token_urlsafe(24)
        remember_secret(env, env_file, "JELLYFIN_LOCAL_ADMIN_USER", name)
        remember_secret(env, env_file, "JELLYFIN_LOCAL_ADMIN_PASSWORD", password)
        env["JELLYFIN_LOCAL_ADMIN_USER"] = name
        env["JELLYFIN_LOCAL_ADMIN_PASSWORD"] = password
        print(f"jellyfin: wrote {name} break-glass password to {env_file.name}")

    status, data = jellyfin_request(
        "POST",
        f"{base}/Users/New",
        token=token,
        body={"Name": name, "Password": password},
    )
    if status not in (200, 204) or not isinstance(data, dict):
        print(f"WARN: create local admin {name} failed (HTTP {status})")
        return
    print(f"jellyfin: created local admin {name}")


def ensure_auth_providers(base: str, token: str, spec: dict) -> None:
    ldap = spec.get("ldap") or {}
    local_admin = str(ldap.get("local_admin") or "admin").strip()
    wanted = [str(name).strip() for name in (ldap.get("provider_users") or []) if str(name).strip()]
    if local_admin and local_admin in wanted:
        print(f"WARN: refusing to switch local admin {local_admin} to LDAP")
        wanted = [name for name in wanted if name != local_admin]
    if not wanted:
        return

    users = {str(user.get("Name") or ""): user for user in list_users(base, token)}
    for name in wanted:
        user = users.get(name)
        if not user:
            print(f"WARN: Jellyfin user {name} not found; skip LDAP provider switch")
            continue
        user_id = str(user.get("Id") or "").strip()
        policy = user.get("Policy")
        if not user_id or not isinstance(policy, dict):
            print(f"WARN: Jellyfin user {name} missing id/policy")
            continue
        current = str(policy.get("AuthenticationProviderId") or "")
        if is_ldap_provider(current):
            print(f"jellyfin: {name} already uses LDAP auth")
            continue
        status, _ = jellyfin_request(
            "POST",
            f"{base}/Users/{user_id}/Policy",
            token=token,
            body=policy_with_ldap_provider(policy),
        )
        if status in (200, 204):
            print(f"jellyfin: switched {name} to LDAP auth")
        else:
            print(f"WARN: switch {name} to LDAP failed (HTTP {status})")


def restart_jellyfin_container(dry_run: bool) -> None:
    if dry_run:
        print("DRY: docker restart jellyfin")
        return
    subprocess.run(["docker", "restart", "jellyfin"], check=True)
    print("jellyfin: container restarted")


def load_bootstrap() -> dict:
    return minyaml_loads(BOOT.read_text())


def apply(env_file: Path, *, identity_env_file: Path | None = None, dry_run: bool = False) -> int:
    if not BOOT.is_file():
        print(f"ERROR: missing {BOOT}", file=sys.stderr)
        return 1

    env = load_env_file(env_file)
    merge_state(env)
    identity_env = load_env_file(identity_env_file or ROOT / "identity" / ".env")
    spec = load_bootstrap()
    base = jellyfin_base_url(env)

    if not wait_http(f"{base}/health", timeout_s=120) and not wait_http(
        f"{base}/System/Info/Public", timeout_s=30
    ):
        print(f"WARN: Jellyfin not reachable at {base}")
        return 1

    if dry_run:
        print(f"DRY: would bootstrap jellyfin at {base}")
        return 0

    if not complete_startup(base, env):
        return 1

    system_xml = config_dir(env) / "system.xml"
    repos_changed = merge_plugin_repositories(system_xml, spec.get("repositories") or [])

    if repos_changed:
        # Plugin repositories are only loaded at Jellyfin startup.
        restart_jellyfin_container(dry_run=False)

    token = wait_for_jellyfin_ready(base, env)
    if not token:
        return 1
    existing_key = env.get("JELLYFIN_API_KEY", "").strip()
    if not existing_key or existing_key in PLACEHOLDER:
        minted = create_api_key(base, token)
        if minted:
            remember_secret(env, env_file, "JELLYFIN_API_KEY", minted)
            env["JELLYFIN_API_KEY"] = minted
            token = minted

    plugins_changed = ensure_plugins(base, token, env, spec.get("plugins") or [])

    if plugins_changed:
        restart_jellyfin_container(dry_run=False)
        if not wait_for_jellyfin_ready(base, env, timeout_s=180):
            print("WARN: Jellyfin slow after plugin install restart")

    copy_static_plugin_configs(env)
    copy_branding(env)
    render_ldap_config(env, identity_env, spec)
    restart_jellyfin_container(dry_run=dry_run)
    token = wait_for_jellyfin_ready(base, env)
    if token:
        ensure_local_admin(base, token, env, spec, env_file)
        ensure_auth_providers(base, token, spec)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Apply Jellyfin bootstrap (plugins + LDAP).")
    parser.add_argument(
        "--env-file",
        type=Path,
        default=ROOT / "jellyfin" / ".env",
        help="Jellyfin env file",
    )
    parser.add_argument(
        "--identity-env-file",
        type=Path,
        default=ROOT / "identity" / ".env",
        help="Identity env (LDAP bind token)",
    )
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    return apply(args.env_file, identity_env_file=args.identity_env_file, dry_run=args.dry_run)


if __name__ == "__main__":
    raise SystemExit(main())
