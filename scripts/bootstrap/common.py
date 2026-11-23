"""Shared helpers for arrstack bootstrap scripts."""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from arrstack_api_keys import (
    PLACEHOLDER,
    load_auto_env_assignments,
    load_env_folder_for_data,
    parse_env_assignments,
    read_keys_from_data_dir,
)

ROOT = Path(__file__).resolve().parents[2]


def env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def load_env_file(path: Path) -> dict[str, str]:
    if not path.is_file():
        return {}
    return parse_env_assignments(path.read_text())


def bootstrap_host(env_file: Path) -> str:
    """Host for API calls from the Docker host (CT 100), not from containers."""
    explicit = env("ARRSTACK_BOOTSTRAP_HOST").strip()
    if explicit:
        return explicit
    assignments = load_env_file(env_file)
    configured = assignments.get("ARRSTACK_BOOTSTRAP_HOST", "").strip()
    if configured:
        return configured
    # Published ports bind 127.0.0.1 on CT 100; LOCAL_DOCKER_IP is for LAN clients only.
    return "127.0.0.1"


def service_url(env_file: Path, port_var: str, default_port: int) -> str:
    assignments = load_env_file(env_file)
    host = bootstrap_host(env_file)
    port = assignments.get(port_var, env(port_var, str(default_port)))
    return f"http://{host}:{port}".rstrip("/")


def resolved_api_keys(env_file: Path | None = None) -> dict[str, str]:
    names = (
        "SONARR_API_KEY",
        "RADARR_API_KEY",
        "LIDARR_API_KEY",
        "PROWLARR_API_KEY",
        "BAZARR_API_KEY",
        "BINDERY_API_KEY",
        "SABNZBD_API_KEY",
        "AUDIOBOOKSHELF_API_KEY",
        "SEERR_API_KEY",
    )
    out = {n: env(n) for n in names}
    missing = [n for n, v in out.items() if not v or v in PLACEHOLDER]
    if not missing:
        return out

    env_path = env_file or Path(os.environ.get("ARRSTACK_ENV_FILE", ROOT / ".env"))
    data_dir = os.environ.get("FOLDER_FOR_DATA", "").strip()
    if not data_dir and env_path.is_file():
        loaded = load_env_folder_for_data(env_path)
        data_dir = str(loaded) if loaded else ""
    if data_dir:
        auto = load_auto_env_assignments(Path(data_dir))
        for name in list(missing):
            if name in auto and auto[name] not in PLACEHOLDER:
                out[name] = auto[name]
    missing = [n for n, v in out.items() if not v or v in PLACEHOLDER]
    if missing and env_path.is_file():
        file_env = load_env_file(env_path)
        for name in list(missing):
            value = file_env.get(name, "")
            if value and value not in PLACEHOLDER:
                out[name] = value
    missing = [n for n, v in out.items() if not v or v in PLACEHOLDER]
    if not missing:
        return out
    if not data_dir:
        return out

    disk = read_keys_from_data_dir(Path(data_dir))
    for name in missing:
        if name in disk:
            out[name] = disk[name]
    return out


def request_json(
    method: str,
    url: str,
    api_key: str = "",
    body: dict | list | None = None,
    *,
    headers: dict[str, str] | None = None,
) -> object:
    data = None
    req_headers = dict(headers or {})
    if api_key:
        req_headers.setdefault("X-Api-Key", api_key)
    if body is not None:
        req_headers.setdefault("Content-Type", "application/json")
        data = json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, headers=req_headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read()
            if not raw:
                return None
            return json.loads(raw)
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:300]
        print(f"WARN: HTTP {exc.code} {method} {url}: {detail}")
        return None


def request_form(
    url: str,
    api_key: str,
    fields: list[tuple[str, str]],
    *,
    method: str = "POST",
) -> int:
    body = urllib.parse.urlencode(fields, doseq=True).encode()
    auth_url = f"{url}{'&' if '?' in url else '?'}apikey={urllib.parse.quote(api_key)}"
    headers = {
        "X-API-KEY": api_key,
        "Content-Type": "application/x-www-form-urlencoded",
    }
    req = urllib.request.Request(auth_url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:300]
        print(f"WARN: HTTP {exc.code} {method} {url}: {detail}")
        return exc.code


def request_raw(
    method: str,
    url: str,
    *,
    headers: dict[str, str] | None = None,
    body: bytes | None = None,
) -> tuple[int, bytes]:
    req = urllib.request.Request(url, data=body, headers=headers or {}, method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()


def wait_http(url: str, timeout_s: int = 120) -> bool:
    import time

    deadline = time.time() + timeout_s
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=5) as resp:
                if resp.status < 500:
                    return True
        except (urllib.error.URLError, TimeoutError):
            pass
        time.sleep(2)
    return False
