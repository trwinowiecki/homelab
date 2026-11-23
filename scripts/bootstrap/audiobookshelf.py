"""Apply Audiobookshelf settings from arrstack/audiobookshelf/bootstrap.yml."""
from __future__ import annotations

import json
import sys
from pathlib import Path

from arrstack_api_keys import (
    auto_env_path,
    load_auto_env_assignments,
    load_env_folder_for_data,
    parse_env_assignments,
    write_auto_env_file,
)

from bootstrap.common import load_env_file, request_json, request_raw, service_url

ROOT = Path(__file__).resolve().parents[2]
BOOT = ROOT / "arrstack" / "audiobookshelf" / "bootstrap.yml"

sys.path.insert(0, str(ROOT / "scripts"))
from identity_oauth import OAUTH_APPS, auth_base_url, internal_auth_base_docker, is_placeholder, issuer_url, load_env  # noqa: E402


def base_url(env_file: Path) -> str:
    return service_url(env_file, "WEBUI_PORT_AUDIOBOOKSHELF", 13378).rstrip("/")


def is_initialized(base: str) -> bool:
    try:
        status, raw = request_raw("GET", f"{base}/status")
    except OSError:
        return False
    if status != 200:
        return False
    data = json.loads(raw)
    return bool(data.get("isInit"))


def ensure_root_user(base: str, env_file: Path, *, dry_run: bool) -> bool:
    if is_initialized(base):
        return True
    env_vars = load_env_file(env_file)
    username = env_vars.get("AUDIOBOOKSHELF_USERNAME", "root")
    password = env_vars.get("AUDIOBOOKSHELF_PASSWORD", "")
    if not password or password in {"CHANGE_ME", "change_me"}:
        print("skip audiobookshelf init: AUDIOBOOKSHELF_PASSWORD not set")
        return False
    body = json.dumps({"newRoot": {"username": username, "password": password}}).encode()
    if dry_run:
        print("audiobookshelf: would create root user")
        return True
    status, _ = request_raw(
        "POST",
        f"{base}/init",
        headers={"Content-Type": "application/json"},
        body=body,
    )
    if status == 200:
        print("audiobookshelf: created root user")
        return True
    print(f"audiobookshelf: init failed with HTTP {status}")
    return False


def login(base: str, env_file: Path) -> str:
    env_vars = load_env_file(env_file)
    username = env_vars.get("AUDIOBOOKSHELF_USERNAME", "root")
    password = env_vars.get("AUDIOBOOKSHELF_PASSWORD", "")
    body = json.dumps({"username": username, "password": password}).encode()
    status, raw = request_raw(
        "POST",
        f"{base}/login",
        headers={"Content-Type": "application/json"},
        body=body,
    )
    if status != 200:
        print(
            "audiobookshelf: login failed "
            f"(HTTP {status}) — check AUDIOBOOKSHELF_USERNAME/PASSWORD in .env "
            "match the account created in the UI (default username is root)"
        )
        return ""
    data = json.loads(raw)
    user = data.get("user") or {}
    return user.get("accessToken") or user.get("token") or ""


def normalize_api_keys_list(data: object) -> list[dict]:
    if isinstance(data, dict):
        keys = data.get("apiKeys")
        if isinstance(keys, list):
            return keys
        return []
    if isinstance(data, list):
        return data
    return []


def extract_created_api_key(created: object) -> str:
    if not isinstance(created, dict):
        return ""
    if created.get("key"):
        return str(created["key"])
    api_key_obj = created.get("apiKey")
    if isinstance(api_key_obj, dict):
        return str(api_key_obj.get("apiKey") or api_key_obj.get("key") or "")
    return ""


def persist_api_key(env_file: Path, api_key: str) -> None:
    data_dir = load_env_folder_for_data(env_file)
    if data_dir is None:
        return
    existing = load_auto_env_assignments(data_dir)
    if existing.get("AUDIOBOOKSHELF_API_KEY") == api_key:
        return
    existing["AUDIOBOOKSHELF_API_KEY"] = api_key
    env_vars = load_env_file(env_file)
    puid = int(env_vars.get("PUID", "10000") or "10000")
    pgid = int(env_vars.get("PGID", "10000") or "10000")
    write_auto_env_file(auto_env_path(data_dir), existing, uid=puid, gid=pgid)
    print("audiobookshelf: persisted AUDIOBOOKSHELF_API_KEY to arrstack.auto.env")


def ensure_api_key(base: str, env_file: Path, *, dry_run: bool) -> str:
    env_vars = load_env_file(env_file)
    existing = env_vars.get("AUDIOBOOKSHELF_API_KEY", "")
    if existing and existing not in {"CHANGE_ME", "change_me"}:
        return existing

    data_dir = load_env_folder_for_data(env_file)
    if data_dir is not None:
        auto = load_auto_env_assignments(data_dir)
        if auto.get("AUDIOBOOKSHELF_API_KEY"):
            return auto["AUDIOBOOKSHELF_API_KEY"]

    token = login(base, env_file)
    if not token:
        print("audiobookshelf: login failed")
        return ""

    keys = normalize_api_keys_list(
        request_json(
            "GET",
            f"{base}/api/api-keys",
            "",
            headers={"Authorization": f"Bearer {token}"},
        )
    )
    bootstrap_exists = any(
        isinstance(entry, dict) and entry.get("name") == "arrstack-bootstrap"
        for entry in keys
    )
    if bootstrap_exists:
        print(
            "audiobookshelf: arrstack-bootstrap API key exists but secret is not "
            "retrievable — set AUDIOBOOKSHELF_API_KEY in arrstack.auto.env or "
            "delete the key in Settings → API Keys and re-run bootstrap"
        )
        return ""

    if dry_run:
        print("audiobookshelf: would create API key")
        return ""

    me = request_json(
        "GET",
        f"{base}/api/me",
        "",
        headers={"Authorization": f"Bearer {token}"},
    ) or {}
    user_id = me.get("id", "")
    created = request_json(
        "POST",
        f"{base}/api/api-keys",
        "",
        body={"name": "arrstack-bootstrap", "userId": user_id, "isActive": True},
        headers={"Authorization": f"Bearer {token}"},
    ) or {}
    api_key = extract_created_api_key(created)
    if api_key:
        persist_api_key(env_file, api_key)
        print("audiobookshelf: created API key")
    else:
        print("audiobookshelf: failed to create API key")
    return api_key


def ensure_libraries(base: str, api_key: str, libraries: list[dict], *, dry_run: bool) -> None:
    existing = request_json(
        "GET",
        f"{base}/api/libraries",
        "",
        headers={"Authorization": f"Bearer {api_key}"},
    )
    if isinstance(existing, dict):
        libs = existing.get("libraries", [])
    elif isinstance(existing, list):
        libs = existing
    else:
        libs = []
    existing_names = {entry.get("name") for entry in libs}

    for lib in libraries:
        name = lib["name"]
        if name in existing_names:
            continue
        body = {
            "name": name,
            "folders": [{"fullPath": lib["path"]}],
            "mediaType": lib.get("media_type", "book"),
            "provider": lib.get("provider", "audible"),
            "icon": lib.get("icon", "audiobookshelf"),
            "settings": {"autoScanCronExpression": None},
        }
        if dry_run:
            print(f"audiobookshelf: would create library {name}")
            continue
        result = request_json(
            "POST",
            f"{base}/api/libraries",
            "",
            body=body,
            headers={"Authorization": f"Bearer {api_key}"},
        )
        if result:
            print(f"audiobookshelf: created library {name}")
        else:
            print(f"audiobookshelf: failed to create library {name}")


def ensure_oidc(base: str, token: str, env_file: Path, spec: dict, *, dry_run: bool) -> None:
    oidc_spec = spec.get("oidc") or {}
    if not oidc_spec.get("enabled"):
        return

    env_vars = load_env_file(env_file)
    if str(env_vars.get("AUDIOBOOKSHELF_OIDC_ENABLED", "false")).lower() != "true":
        print("audiobookshelf: OIDC skipped (set AUDIOBOOKSHELF_OIDC_ENABLED=true after identity deploy)")
        return

    identity_env = load_env(ROOT / "identity" / ".env")
    app_spec = OAUTH_APPS["audiobookshelf"]
    secret = identity_env.get(app_spec["secret_env"], "")
    lan = identity_env.get("IDENTITY_LAN_DOMAIN") or env_vars.get("IDENTITY_LAN_DOMAIN", "")
    if is_placeholder(secret) or not lan:
        print("audiobookshelf: skip OIDC — identity secrets or IDENTITY_LAN_DOMAIN missing")
        return

    slug = app_spec["authentik_slug"]
    issuer = issuer_url(lan, slug)
    public = auth_base_url(lan)
    internal = internal_auth_base_docker()
    current = request_json(
        "GET",
        f"{base}/api/auth-settings",
        "",
        headers={"Authorization": f"Bearer {token}"},
    )
    if not isinstance(current, dict):
        print("audiobookshelf: could not read auth-settings")
        return

    payload = dict(current)
    payload["authOpenIDIssuerURL"] = issuer
    payload["authOpenIDAuthorizationURL"] = f"{public}/application/o/authorize/"
    payload["authOpenIDTokenURL"] = f"{internal}/application/o/token/"
    payload["authOpenIDUserInfoURL"] = f"{internal}/application/o/{slug}/userinfo/"
    payload["authOpenIDJwksURL"] = f"{internal}/application/o/{slug}/jwks/"
    payload["authOpenIDClientID"] = app_spec["client_id"]
    payload["authOpenIDClientSecret"] = secret
    payload["authOpenIDTokenSigningAlgorithm"] = "RS256"
    payload["authOpenIDMatchExistingBy"] = oidc_spec.get("match_users_by", "username")
    payload["authOpenIDAutoRegister"] = bool(oidc_spec.get("auto_register", True))
    payload["authOpenIDAutoLaunch"] = bool(oidc_spec.get("auto_launch", False))
    payload["authOpenIDButtonText"] = oidc_spec.get("button_text", "Login with Authentik")
    payload["authOpenIDSubfolderForRedirectURLs"] = ""

    methods = list(payload.get("authActiveAuthMethods") or [])
    if "local" not in methods:
        methods.append("local")
    if "openid" not in methods:
        methods.append("openid")
    payload["authActiveAuthMethods"] = methods

    if dry_run:
        print("audiobookshelf: would configure OpenID Connect")
        return

    result = request_json(
        "PATCH",
        f"{base}/api/auth-settings",
        "",
        body=payload,
        headers={"Authorization": f"Bearer {token}"},
    )
    if result:
        print("audiobookshelf: configured OpenID Connect (restart container to load OIDC strategy)")
    else:
        print("audiobookshelf: failed to update auth-settings")


def apply(spec: dict, env_file: Path, *, dry_run: bool = False) -> None:
    base = base_url(env_file)
    try:
        initialized = is_initialized(base)
    except OSError:
        if dry_run:
            print("audiobookshelf: service unreachable (dry-run)")
            for lib in spec.get("libraries", []):
                print(f"audiobookshelf: would create library {lib['name']}")
            return
        print("skip audiobookshelf: service unreachable")
        return

    if not initialized:
        if not ensure_root_user(base, env_file, dry_run=dry_run):
            return
    api_key = ensure_api_key(base, env_file, dry_run=dry_run)
    if not api_key:
        if dry_run and "libraries" in spec:
            for lib in spec["libraries"]:
                print(f"audiobookshelf: would create library {lib['name']}")
        return
    if "libraries" in spec:
        ensure_libraries(base, api_key, spec["libraries"], dry_run=dry_run)
    token = login(base, env_file)
    if token:
        ensure_oidc(base, token, env_file, spec, dry_run=dry_run)
