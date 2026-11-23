"""Ensure Portainer API access token and optional OAuth settings from Authentik."""

from __future__ import annotations

import json
import sys
from pathlib import Path

from arrstack_api_keys import (
    PLACEHOLDER,
    apply_homepage_aliases,
    auto_env_path,
    bare_value,
    load_auto_env_assignments,
    load_env_folder_for_data,
    parse_env_assignments,
    write_auto_env_file,
)

from bootstrap.common import request_json, request_raw, service_url

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from identity_oauth import OAUTH_APPS, auth_base_url, is_placeholder, issuer_url, load_env  # noqa: E402

TOKEN_DESCRIPTION = "homepage"


def portainer_auth_headers(token: str) -> dict[str, str]:
    """Portainer access tokens (ptr_…) use X-API-Key; JWTs use Bearer."""
    if token.startswith("ptr_"):
        return {"X-API-Key": token}
    return {"Authorization": f"Bearer {token}"}


def resolve_admin_token(
    base_url: str,
    env_vars: dict[str, str],
    data_dir: Path | None,
) -> str:
    admin_password = bare_value(env_vars.get("PORTAINER_ADMIN_PASSWORD", ""))
    if admin_password and admin_password not in PLACEHOLDER:
        jwt = portainer_jwt(base_url, admin_password)
        if jwt:
            return jwt

    for source in (
        bare_value(env_vars.get("PORTAINER_API_KEY", "")),
        bare_value(load_auto_env_assignments(data_dir).get("PORTAINER_API_KEY", ""))
        if data_dir
        else "",
    ):
        if source and source not in PLACEHOLDER:
            return source
    return ""


def persist_portainer_key(env_file: Path, api_key: str, *, dry_run: bool) -> None:
    data_dir = load_env_folder_for_data(env_file)
    if data_dir is None:
        print("skip portainer: FOLDER_FOR_DATA not found")
        return

    existing = load_auto_env_assignments(data_dir)
    if existing.get("PORTAINER_API_KEY") == api_key:
        return

    assignments = dict(existing)
    assignments["PORTAINER_API_KEY"] = api_key
    changed: list[str] = ["PORTAINER_API_KEY"]
    apply_homepage_aliases(assignments, changed)

    if dry_run:
        print("portainer: would persist PORTAINER_API_KEY to arrstack.auto.env")
        return

    env_vars = parse_env_assignments(env_file.read_text()) if env_file.is_file() else {}
    puid = int(bare_value(env_vars.get("PUID", "10000")) or "10000")
    pgid = int(bare_value(env_vars.get("PGID", "10000")) or "10000")
    write_auto_env_file(auto_env_path(data_dir), assignments, uid=puid, gid=pgid)
    print("portainer: persisted PORTAINER_API_KEY to arrstack.auto.env")


def portainer_jwt(base_url: str, admin_password: str) -> str:
    status, raw = request_raw(
        "POST",
        f"{base_url}/api/auth",
        headers={"Content-Type": "application/json"},
        body=json.dumps({"Username": "admin", "Password": admin_password}).encode(),
    )
    if status != 200:
        return ""
    try:
        return str(json.loads(raw).get("jwt") or "").strip()
    except json.JSONDecodeError:
        return ""


def create_access_token(base_url: str, admin_password: str) -> str:
    jwt = portainer_jwt(base_url, admin_password)
    if not jwt:
        return ""

    result = request_json(
        "POST",
        f"{base_url}/api/users/1/tokens",
        headers={
            "Authorization": f"Bearer {jwt}",
            "Content-Type": "application/json",
        },
        body={"description": TOKEN_DESCRIPTION},
    )
    if not isinstance(result, dict):
        return ""
    return str(result.get("rawAPIKey") or "").strip()


def configure_oauth(base_url: str, token: str, arr_env: dict[str, str], identity_env: dict[str, str], *, dry_run: bool) -> bool:
    spec = OAUTH_APPS["portainer"]
    secret = identity_env.get(spec["secret_env"], "")
    lan = identity_env.get("IDENTITY_LAN_DOMAIN") or arr_env.get("IDENTITY_LAN_DOMAIN", "")
    if is_placeholder(secret) or not lan:
        return False

    base = auth_base_url(lan)
    redirect = f"https://portainer.{lan}{spec['redirect_path']}"
    # Portainer exchanges the code server-side; it cannot verify Caddy's internal TLS CA.
    # Browser still uses the public Authorization URL; token/userinfo use Docker DNS.
    internal_auth = "http://authentik-server:9000"
    oauth_settings = {
        "ClientID": spec["client_id"],
        "ClientSecret": secret,
        "AuthorizationURI": f"{base}/application/o/authorize/",
        "AccessTokenURI": f"{internal_auth}/application/o/token/",
        "ResourceURI": f"{internal_auth}/application/o/userinfo/",
        "RedirectURI": redirect,
        "UserIdentifier": "preferred_username",
        "Scopes": "openid profile email",
        "OAuthAutoCreateUsers": True,
        "SSO": True,
        "AuthStyle": 0,
    }
    enable_oauth = bare_value(arr_env.get("PORTAINER_OAUTH_ENABLED", "false")).lower() == "true"
    body: dict[str, object] = {"OAuthSettings": oauth_settings}
    if enable_oauth:
        body["AuthenticationMethod"] = 3

    if dry_run:
        print("portainer: would configure OAuth settings from identity/.env")
        return True

    current = request_json(
        "GET",
        f"{base_url}/api/settings",
        headers=portainer_auth_headers(token),
    )
    if isinstance(current, dict):
        merged = dict(current.get("OAuthSettings") or {})
        merged.update(oauth_settings)
        body = {"OAuthSettings": merged}
        if enable_oauth:
            body["AuthenticationMethod"] = 3

    status, raw = request_raw(
        "PUT",
        f"{base_url}/api/settings",
        headers={
            **portainer_auth_headers(token),
            "Content-Type": "application/json",
        },
        body=json.dumps(body).encode(),
    )
    if status in (200, 204):
        if enable_oauth:
            print("portainer: OAuth enabled (PORTAINER_OAUTH_ENABLED=true)")
        else:
            print(
                "portainer: OAuth settings saved (local auth still default; "
                "set PORTAINER_OAUTH_ENABLED=true after testing)"
            )
        return True
    detail = raw.decode("utf-8", "replace")[:200] if raw else ""
    print(f"portainer: failed to save OAuth settings (HTTP {status}): {detail}")
    return False


def apply(spec: dict, env_file: Path, *, dry_run: bool = False) -> None:
    del spec  # unused — no bootstrap.yml for Portainer

    env_vars = parse_env_assignments(env_file.read_text()) if env_file.is_file() else {}
    identity_env = load_env(ROOT / "identity" / ".env")
    base_url = service_url(env_file, "WEBUI_PORT_PORTAINER", 9000).rstrip("/")
    data_dir = load_env_folder_for_data(env_file)

    admin_token = resolve_admin_token(base_url, env_vars, data_dir)
    if admin_token:
        configure_oauth(base_url, admin_token, env_vars, identity_env, dry_run=dry_run)
    elif not dry_run:
        print(
            "skip portainer OAuth: set PORTAINER_ADMIN_PASSWORD or PORTAINER_API_KEY "
            "to apply OAuth settings"
        )

    if data_dir is not None:
        existing = load_auto_env_assignments(data_dir)
        current = bare_value(existing.get("PORTAINER_API_KEY", ""))
        if current and current not in PLACEHOLDER:
            print("portainer: PORTAINER_API_KEY already set")
            return

    pasted = bare_value(env_vars.get("PORTAINER_API_KEY", ""))
    if pasted and pasted not in PLACEHOLDER:
        persist_portainer_key(env_file, pasted, dry_run=dry_run)
        return

    admin_password = bare_value(env_vars.get("PORTAINER_ADMIN_PASSWORD", ""))
    if not admin_password or admin_password in PLACEHOLDER:
        print(
            "skip portainer: set PORTAINER_API_KEY in arrstack.auto.env or "
            "PORTAINER_ADMIN_PASSWORD in .env to mint a Homepage token"
        )
        return

    api_key = create_access_token(base_url, admin_password)
    if not api_key:
        print(
            "skip portainer: could not create access token — verify "
            "PORTAINER_ADMIN_PASSWORD or paste PORTAINER_API_KEY once"
        )
        return

    persist_portainer_key(env_file, api_key, dry_run=dry_run)
