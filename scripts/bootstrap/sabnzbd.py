"""Apply SABnzbd settings from arrstack/sabnzbd/bootstrap.yml."""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from arrstack_api_keys import PLACEHOLDER

from bootstrap.common import env, load_env_file, resolved_api_keys, service_url

ROOT = Path(__file__).resolve().parents[2]
BOOT = ROOT / "arrstack" / "sabnzbd" / "bootstrap.yml"


def base_url(env_file: Path) -> tuple[str, str]:
    keys = resolved_api_keys(env_file)
    key = keys.get("SABNZBD_API_KEY", "")
    if not key or key in PLACEHOLDER:
        print("skip sabnzbd: SABNZBD_API_KEY not set (SABnzbd has not written sabnzbd.ini yet)")
        return "", ""
    url = env("SABNZBD_URL") or service_url(env_file, "WEBUI_PORT_SABNZBD", 8100)
    return url.rstrip("/"), key


def _assigned(env_map: dict[str, str], name: str) -> str:
    value = env(name) or env_map.get(name, "")
    value = value.strip()
    if not value or value in PLACEHOLDER:
        return ""
    return value


def _truthy(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes", "on"}


def access_settings(env_map: dict[str, str]) -> list[tuple[str, str]]:
    """Hostnames and networks allowed to call the SABnzbd API."""
    domain = _assigned(env_map, "IDENTITY_LAN_DOMAIN")
    hosts = ["sabnzbd", "localhost", "127.0.0.1"]
    if domain:
        hosts.append(f"sabnzbd.{domain}")
    # local_ranges is protected in SABnzbd and ignores API writes. Private
    # addresses, including the Docker bridge, are already treated as local.
    return [("host_whitelist", ",".join(hosts))]


def server_settings(spec: dict, env_map: dict[str, str]) -> tuple[str, list[tuple[str, str]]] | None:
    """Return (server name, SABnzbd set_config fields). None when provider creds are unset."""
    server = spec.get("server") or {}
    host = _assigned(env_map, server.get("host_env", "USENET_HOST"))
    username = _assigned(env_map, server.get("username_env", "USENET_USERNAME"))
    password = _assigned(env_map, server.get("password_env", "USENET_PASSWORD"))
    if not host or not username or not password:
        return None
    port = _assigned(env_map, server.get("port_env", "USENET_PORT")) or "563"
    ssl_raw = _assigned(env_map, server.get("ssl_env", "USENET_SSL"))
    ssl = "1" if (not ssl_raw or _truthy(ssl_raw)) else "0"
    connections = _assigned(env_map, server.get("connections_env", "USENET_CONNECTIONS"))
    if not connections:
        connections = str(server.get("connections_default", 50))
    if not connections.isdigit():
        raise ValueError(f"USENET connections must be a number, got {connections!r}")
    name = str(server.get("name") or "usenet")
    settings = [
        ("host", host),
        ("port", port),
        ("timeout", "60"),
        ("username", username),
        ("password", password),
        ("connections", connections),
        ("ssl", ssl),
        ("enable", "1"),
        ("displayname", name),
    ]
    if ssl == "1":
        # 3 = Strict: verify the certificate and that it belongs to this host.
        settings.append(("ssl_verify", "3"))
    return name, settings


def sab_get(base: str, api_key: str, params: list[tuple[str, str]]) -> dict | None:
    query = urllib.parse.urlencode([("output", "json"), ("apikey", api_key), *params])
    url = f"{base}/api?{query}"
    req = urllib.request.Request(url, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            payload = json.loads(resp.read().decode() or "null")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:300]
        section = dict(params).get("section", "api")
        print(f"WARN: SABnzbd HTTP {exc.code} section={section}: {detail}")
        return None
    except urllib.error.URLError as exc:
        print(f"WARN: SABnzbd unreachable: {exc.reason}")
        return None
    if isinstance(payload, dict) and payload.get("status") is False:
        section = dict(params).get("section", "api")
        print(f"WARN: SABnzbd section={section}: {payload.get('error')}")
        return None
    return payload if isinstance(payload, dict) else {}


def _set_keyword(base: str, api_key: str, keyword: str, value: str, *, dry_run: bool) -> bool:
    if dry_run:
        print(f"sabnzbd: would set misc {keyword}={value}")
        return True
    result = sab_get(
        base,
        api_key,
        [
            ("mode", "set_config"),
            ("section", "misc"),
            ("keyword", keyword),
            ("value", value),
        ],
    )
    if result is None:
        print(f"sabnzbd: failed to set {keyword}")
        return False
    print(f"sabnzbd: set {keyword}={value}")
    return True


def _host_path(container_path: str, env_map: dict[str, str]) -> Path | None:
    """Map a SABnzbd container path back to the CT path bootstrap can mkdir."""
    if container_path.startswith("/downloads/"):
        downloads = _assigned(env_map, "FOLDER_FOR_DOWNLOADS")
        if not downloads:
            return None
        return Path(downloads) / container_path.removeprefix("/downloads/").lstrip("/")
    if container_path.startswith("/data/"):
        media = _assigned(env_map, "FOLDER_FOR_MEDIA")
        if not media:
            return None
        return Path(media) / container_path.removeprefix("/data/").lstrip("/")
    return None


def download_dir_paths(spec: dict, env_map: dict[str, str]) -> list[Path]:
    """Host paths for SABnzbd incomplete/complete and each category folder."""
    incomplete = str(spec.get("incomplete_dir") or "/downloads/usenet/incomplete")
    complete = str(spec.get("complete_dir") or "/downloads/usenet/complete")
    paths: list[Path] = []
    incomplete_host = _host_path(incomplete, env_map)
    complete_host = _host_path(complete, env_map)
    if incomplete_host is not None:
        paths.append(incomplete_host)
    if complete_host is not None:
        paths.append(complete_host)
        for category in spec.get("categories") or []:
            name = str(category.get("dir") or category["name"])
            paths.append(complete_host / name)
    return paths


def ensure_download_dirs(spec: dict, env_map: dict[str, str], *, dry_run: bool) -> None:
    """Create the usenet tree as PUID:PGID. SAB cannot mkdir under a root-owned /downloads."""
    paths = download_dir_paths(spec, env_map)
    if not paths:
        print("sabnzbd: skip mkdir (FOLDER_FOR_DOWNLOADS unset)")
        return
    if dry_run:
        print(f"sabnzbd: would create {len(paths)} download dirs")
        return
    puid = int(_assigned(env_map, "PUID") or "10000")
    pgid = int(_assigned(env_map, "PGID") or "10000")
    for path in paths:
        path.mkdir(parents=True, exist_ok=True)
        try:
            os.chown(path, puid, pgid)
        except PermissionError:
            print(f"sabnzbd: could not chown {path} to {puid}:{pgid}")
    for parent in {path.parent for path in paths}:
        try:
            os.chown(parent, puid, pgid)
        except PermissionError:
            print(f"sabnzbd: could not chown {parent} to {puid}:{pgid}")
    print(f"sabnzbd: ensured {len(paths)} download dirs")


def apply(spec: dict, env_file: Path, *, dry_run: bool = False) -> None:
    env_map = load_env_file(env_file)
    ensure_download_dirs(spec, env_map, dry_run=dry_run)
    base, api_key = base_url(env_file)
    if not base or not api_key:
        return

    ok = True
    for keyword, spec_key in (("complete_dir", "complete_dir"), ("download_dir", "incomplete_dir")):
        folder = spec.get(spec_key, "")
        if folder:
            ok = _set_keyword(base, api_key, keyword, str(folder), dry_run=dry_run) and ok

    for keyword, value in access_settings(env_map):
        ok = _set_keyword(base, api_key, keyword, value, dry_run=dry_run) and ok

    for keyword, value in (spec.get("performance") or {}).items():
        ok = _set_keyword(base, api_key, str(keyword), str(value), dry_run=dry_run) and ok

    for keyword, value in (spec.get("cleanup") or {}).items():
        ok = _set_keyword(base, api_key, str(keyword), str(value), dry_run=dry_run) and ok

    built = server_settings(spec, env_map)
    if built is None:
        print("sabnzbd: skip server (USENET_HOST / USENET_USERNAME / USENET_PASSWORD unset)")
    else:
        name, settings = built
        if dry_run:
            print(f"sabnzbd: would set server {name} ({len(settings)} fields)")
        else:
            result = sab_get(
                base,
                api_key,
                [("mode", "set_config"), ("section", "servers"), ("name", name), *settings],
            )
            if result is None:
                print(f"sabnzbd: failed to set server {name}")
                ok = False
            else:
                print(f"sabnzbd: set server {name}")

    for category in spec.get("categories") or []:
        cat_name = str(category["name"])
        cat_dir = str(category.get("dir") or cat_name)
        if dry_run:
            print(f"sabnzbd: would set category {cat_name} -> {cat_dir}")
            continue
        result = sab_get(
            base,
            api_key,
            [
                ("mode", "set_config"),
                ("section", "categories"),
                ("name", cat_name),
                ("dir", cat_dir),
                ("priority", "0"),
            ],
        )
        if result is None:
            print(f"sabnzbd: failed to set category {cat_name}")
            ok = False
        else:
            print(f"sabnzbd: set category {cat_name}")

    if not ok:
        raise RuntimeError("SABnzbd bootstrap did not apply cleanly")
