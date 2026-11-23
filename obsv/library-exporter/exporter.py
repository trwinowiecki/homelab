#!/usr/bin/env python3
"""Prometheus exporter for Sonarr/Radarr size, Jellyfin watch state, TrueNAS datasets."""
from __future__ import annotations

import base64
import json
import os
import socket
import ssl
import struct
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

LISTEN = os.environ.get("LISTEN_ADDR", "0.0.0.0")
PORT = int(os.environ.get("LISTEN_PORT", "9103"))
CONFIG_PATH = Path(os.environ.get("LIBRARY_EXPORTER_CONFIG", "/app/library-exporter.yaml"))
AUTO_ENV_PATH = Path(os.environ.get("AUTO_ENV_PATH", "/arrstack/config/arrstack.auto.env"))
TRUENAS_API_KEY = os.environ.get("TRUENAS_API_KEY", "")

_cache: dict[str, Any] = {"body": b"library_exporter_up 0\n", "fetched_at": 0.0}


def log(msg: str, **fields: object) -> None:
    print(json.dumps({"msg": msg, **fields}), flush=True)


def load_yaml(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    try:
        import yaml  # type: ignore
    except ImportError:
        scripts = Path(__file__).resolve().parents[2] / "scripts"
        sys.path.insert(0, str(scripts))
        from minyaml import loads as minyaml_loads  # type: ignore

        data = minyaml_loads(text)
    else:
        data = yaml.safe_load(text)
    if not isinstance(data, dict):
        raise ValueError(f"config must be a mapping: {path}")
    return data


def load_exporter_config(path: str | Path) -> dict[str, Any]:
    data = load_yaml(Path(path))
    data.setdefault("stale_days", 180)
    data.setdefault("scrape_interval_secs", 3600)
    data.setdefault("sonarr", {})
    data.setdefault("radarr", {})
    data.setdefault("jellyfin", {})
    data.setdefault("truenas", {})
    data["truenas"].setdefault("tls_verify", False)
    return data


def parse_env_assignments(text: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        out[key] = value.strip().strip('"').strip("'")
    return out


def load_auto_env(path: Path) -> dict[str, str]:
    if not path.is_file():
        return {}
    return parse_env_assignments(path.read_text(encoding="utf-8", errors="replace"))


def http_json(
    url: str,
    headers: dict[str, str] | None = None,
    *,
    timeout: int = 60,
    tls_verify: bool = True,
) -> Any:
    ctx = ssl.create_default_context()
    if not tls_verify:
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
    req = urllib.request.Request(url, headers=headers or {})
    with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
        return json.loads(resp.read().decode())


def parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    text = value.strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    # Jellyfin sometimes uses 7-digit fractional seconds.
    if "." in text:
        head, rest = text.split(".", 1)
        frac = ""
        tz = ""
        for idx, char in enumerate(rest):
            if char.isdigit():
                frac += char
            else:
                tz = rest[idx:]
                break
        frac = (frac + "000000")[:6]
        text = f"{head}.{frac}{tz}"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def is_stale(last_played: str | None, now: datetime, stale_days: int) -> bool:
    parsed = parse_dt(last_played)
    if parsed is None:
        return True
    return (now - parsed).days >= stale_days


def watch_index(items: list[dict[str, Any]]) -> dict[tuple[str, str], dict[str, Any]]:
    index: dict[tuple[str, str], dict[str, Any]] = {}
    for item in items:
        pids = item.get("ProviderIds") or {}
        keys: list[tuple[str, str]] = []
        if pids.get("Tvdb"):
            keys.append(("tvdb", str(pids["Tvdb"])))
        if pids.get("Tmdb"):
            keys.append(("tmdb", str(pids["Tmdb"])))
        user = item.get("UserData") or {}
        play_count = int(user.get("PlayCount") or 0)
        last = str(user.get("LastPlayedDate") or "")
        for key in keys:
            prev = index.get(key, {"play_count": 0, "last_played": ""})
            last_played = last if last > str(prev["last_played"]) else str(prev["last_played"])
            index[key] = {
                "play_count": max(int(prev["play_count"]), play_count),
                "last_played": last_played,
            }
    return index


def truenas_bytes(field: Any) -> int:
    if field is None:
        return 0
    if isinstance(field, bool):
        return int(field)
    if isinstance(field, (int, float)):
        return int(field)
    if isinstance(field, dict):
        if field.get("parsed") is not None:
            try:
                return int(field["parsed"])
            except (TypeError, ValueError):
                pass
        raw = field.get("rawvalue")
        if raw is not None:
            try:
                return int(float(raw))
            except (TypeError, ValueError):
                return 0
    return 0


def prom_escape(value: str) -> str:
    return (value or "").replace("\\", "\\\\").replace("\n", " ").replace('"', '\\"')


def render_metrics(
    items: list[dict[str, Any]],
    datasets: list[dict[str, Any]],
    *,
    up: int,
) -> str:
    lines = [
        "# HELP library_item_bytes Size on disk for a library item",
        "# TYPE library_item_bytes gauge",
    ]
    for item in items:
        labels = (
            f'app="{prom_escape(str(item.get("app", "")))}",'
            f'kind="{prom_escape(str(item.get("kind", "")))}",'
            f'title="{prom_escape(str(item.get("title", "")))}",'
            f'profile="{prom_escape(str(item.get("profile", "")))}",'
            f'id="{prom_escape(str(item.get("id", "")))}",'
            f'stale="{str(bool(item.get("stale"))).lower()}",'
            f'watched="{str(bool(item.get("watched"))).lower()}",'
            f'link="{prom_escape(str(item.get("link", "")))}"'
        )
        lines.append(f"library_item_bytes{{{labels}}} {int(item.get('bytes') or 0)}")
    lines.extend(
        [
            "# HELP truenas_dataset_used_bytes Dataset used bytes",
            "# TYPE truenas_dataset_used_bytes gauge",
        ]
    )
    for ds in datasets:
        name = prom_escape(str(ds.get("dataset", "")))
        lines.append(f'truenas_dataset_used_bytes{{dataset="{name}"}} {int(ds.get("used") or 0)}')
    lines.extend(
        [
            "# HELP truenas_dataset_usedbysnapshots_bytes Dataset bytes held by snapshots",
            "# TYPE truenas_dataset_usedbysnapshots_bytes gauge",
        ]
    )
    for ds in datasets:
        name = prom_escape(str(ds.get("dataset", "")))
        lines.append(
            f'truenas_dataset_usedbysnapshots_bytes{{dataset="{name}"}} {int(ds.get("usedbysnapshots") or 0)}'
        )
    lines.extend(
        [
            "# HELP truenas_dataset_available_bytes Dataset available bytes",
            "# TYPE truenas_dataset_available_bytes gauge",
        ]
    )
    for ds in datasets:
        name = prom_escape(str(ds.get("dataset", "")))
        lines.append(
            f'truenas_dataset_available_bytes{{dataset="{name}"}} {int(ds.get("available") or 0)}'
        )
    lines.extend(
        [
            "# HELP library_exporter_up 1 if the last scrape succeeded",
            "# TYPE library_exporter_up gauge",
            f"library_exporter_up {int(up)}",
            "",
        ]
    )
    return "\n".join(lines)


def profile_map(profiles: list[dict[str, Any]]) -> dict[int, str]:
    out: dict[int, str] = {}
    for profile in profiles:
        try:
            out[int(profile["id"])] = str(profile.get("name") or "")
        except (KeyError, TypeError, ValueError):
            continue
    return out


def arr_headers(api_key: str) -> dict[str, str]:
    return {"X-Api-Key": api_key}


def scrape_sonarr(
    cfg: dict[str, Any],
    api_key: str,
    watched: dict[tuple[str, str], dict[str, Any]],
    now: datetime,
    stale_days: int,
) -> list[dict[str, Any]]:
    if not api_key:
        return []
    base = str(cfg.get("url") or "http://sonarr:8989").rstrip("/")
    public = str(cfg.get("public_url") or "https://sonarr.<lan-domain>").rstrip("/")
    series = http_json(f"{base}/api/v3/series", arr_headers(api_key))
    profiles = http_json(f"{base}/api/v3/qualityprofile", arr_headers(api_key))
    names = profile_map(profiles if isinstance(profiles, list) else [])
    items: list[dict[str, Any]] = []
    if not isinstance(series, list):
        return items
    for show in series:
        tvdb = str(show.get("tvdbId") or "")
        stats = show.get("statistics") or {}
        size = int(stats.get("sizeOnDisk") or 0)
        watch = watched.get(("tvdb", tvdb), {})
        play_count = int(watch.get("play_count") or 0)
        last = str(watch.get("last_played") or "")
        slug = show.get("titleSlug") or ""
        items.append(
            {
                "app": "sonarr",
                "kind": "series",
                "title": str(show.get("title") or ""),
                "profile": names.get(int(show.get("qualityProfileId") or 0), ""),
                "id": tvdb,
                "bytes": size,
                "stale": is_stale(last, now, stale_days),
                "watched": play_count > 0,
                "link": f"{public}/series/{slug}" if slug else public,
            }
        )
    return items


def scrape_radarr(
    cfg: dict[str, Any],
    api_key: str,
    watched: dict[tuple[str, str], dict[str, Any]],
    now: datetime,
    stale_days: int,
) -> list[dict[str, Any]]:
    if not api_key:
        return []
    base = str(cfg.get("url") or "http://radarr:7878").rstrip("/")
    public = str(cfg.get("public_url") or "https://radarr.<lan-domain>").rstrip("/")
    movies = http_json(f"{base}/api/v3/movie", arr_headers(api_key))
    profiles = http_json(f"{base}/api/v3/qualityprofile", arr_headers(api_key))
    names = profile_map(profiles if isinstance(profiles, list) else [])
    items: list[dict[str, Any]] = []
    if not isinstance(movies, list):
        return items
    for movie in movies:
        tmdb = str(movie.get("tmdbId") or "")
        size = int(movie.get("sizeOnDisk") or 0)
        if not size:
            size = int((movie.get("movieFile") or {}).get("size") or 0)
        watch = watched.get(("tmdb", tmdb), {})
        play_count = int(watch.get("play_count") or 0)
        last = str(watch.get("last_played") or "")
        slug = movie.get("titleSlug") or ""
        items.append(
            {
                "app": "radarr",
                "kind": "movie",
                "title": str(movie.get("title") or ""),
                "profile": names.get(int(movie.get("qualityProfileId") or 0), ""),
                "id": tmdb,
                "bytes": size,
                "stale": is_stale(last, now, stale_days),
                "watched": play_count > 0,
                "link": f"{public}/movie/{slug}" if slug else public,
            }
        )
    return items


def scrape_jellyfin(cfg: dict[str, Any], api_key: str) -> list[dict[str, Any]]:
    if not api_key:
        return []
    base = str(cfg.get("url") or "http://192.168.1.204:8096").rstrip("/")
    headers = {"X-Emby-Token": api_key}
    users = http_json(f"{base}/Users", headers)
    if not isinstance(users, list):
        return []
    wanted = cfg.get("watchers") or ["all"]
    wanted_set = {str(name).lower() for name in wanted}
    items: list[dict[str, Any]] = []
    for user in users:
        name = str(user.get("Name") or "")
        if "all" not in wanted_set and name.lower() not in wanted_set:
            continue
        uid = user.get("Id")
        if not uid:
            continue
        data = http_json(
            f"{base}/Users/{uid}/Items?IncludeItemTypes=Movie,Series&Recursive=true&Fields=ProviderIds,UserData",
            headers,
        )
        found = data.get("Items") if isinstance(data, dict) else data
        if isinstance(found, list):
            items.extend(found)
    return items


def jsonrpc_take_result(messages: list[Any], call_id: int) -> Any:
    for msg in messages:
        if not isinstance(msg, dict) or msg.get("id") != call_id:
            continue
        err = msg.get("error")
        if err:
            raise RuntimeError(f"truenas rpc error: {err}")
        return msg.get("result")
    raise RuntimeError(f"truenas rpc missing id {call_id}")


def datasets_from_query(rows: Any, wanted: list[str]) -> list[dict[str, Any]]:
    if not isinstance(rows, list):
        return []
    out: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        name = str(row.get("name") or row.get("id") or "")
        if wanted and name not in wanted:
            continue
        out.append(
            {
                "dataset": name,
                "used": truenas_bytes(row.get("used")),
                "usedbysnapshots": truenas_bytes(row.get("usedbysnapshots")),
                "available": truenas_bytes(row.get("available")),
            }
        )
    return out


def _recv_exact(sock: socket.socket, n: int) -> bytes:
    buf = b""
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise RuntimeError("truenas websocket closed")
        buf += chunk
    return buf


def _ws_send(sock: socket.socket, opcode: int, payload: bytes) -> None:
    header = bytearray([0x80 | opcode])
    n = len(payload)
    if n < 126:
        header.append(0x80 | n)
    elif n < 65536:
        header.append(0x80 | 126)
        header.extend(struct.pack("!H", n))
    else:
        header.append(0x80 | 127)
        header.extend(struct.pack("!Q", n))
    mask = os.urandom(4)
    header.extend(mask)
    masked = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
    sock.sendall(header + masked)


def _ws_recv(sock: socket.socket) -> tuple[int, bytes]:
    while True:
        hdr = _recv_exact(sock, 2)
        opcode = hdr[0] & 0x0F
        masked = bool(hdr[1] & 0x80)
        length = hdr[1] & 0x7F
        if length == 126:
            length = struct.unpack("!H", _recv_exact(sock, 2))[0]
        elif length == 127:
            length = struct.unpack("!Q", _recv_exact(sock, 8))[0]
        mask = _recv_exact(sock, 4) if masked else b""
        payload = _recv_exact(sock, length)
        if masked:
            payload = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
        if opcode == 0x9:
            _ws_send(sock, 0xA, payload)
            continue
        if opcode == 0xA:
            continue
        if opcode == 0x8:
            raise RuntimeError("truenas websocket close")
        return opcode, payload


def _ws_connect(base: str, tls_verify: bool) -> socket.socket:
    parsed = urllib.parse.urlparse(base)
    host = parsed.hostname or "192.168.1.202"
    use_tls = parsed.scheme != "http"
    port = parsed.port or (443 if use_tls else 80)
    raw = socket.create_connection((host, port), timeout=30)
    sock: socket.socket = raw
    if use_tls:
        ctx = ssl.create_default_context()
        if not tls_verify:
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
        sock = ctx.wrap_socket(raw, server_hostname=host)
    key = base64.b64encode(os.urandom(16)).decode("ascii")
    path = "/api/current"
    req = (
        f"GET {path} HTTP/1.1\r\n"
        f"Host: {host}\r\n"
        "Upgrade: websocket\r\n"
        "Connection: Upgrade\r\n"
        f"Sec-WebSocket-Key: {key}\r\n"
        "Sec-WebSocket-Version: 13\r\n"
        "\r\n"
    )
    sock.sendall(req.encode("ascii"))
    buf = b""
    while b"\r\n\r\n" not in buf:
        chunk = sock.recv(4096)
        if not chunk:
            raise RuntimeError("truenas websocket handshake closed")
        buf += chunk
    status_line = buf.split(b"\r\n", 1)[0].decode("ascii", errors="replace")
    if " 101 " not in status_line:
        raise RuntimeError(f"truenas websocket handshake failed: {status_line}")
    return sock


def truenas_jsonrpc(
    base: str,
    api_key: str,
    method: str,
    params: list[Any] | None,
    tls_verify: bool,
) -> Any:
    sock = _ws_connect(base, tls_verify)
    try:

        def call(call_id: int, rpc_method: str, rpc_params: list[Any]) -> Any:
            payload = {"jsonrpc": "2.0", "id": call_id, "method": rpc_method, "params": rpc_params}
            _ws_send(sock, 0x1, json.dumps(payload).encode())
            while True:
                opcode, raw = _ws_recv(sock)
                if opcode not in (0x1, 0x2):
                    continue
                msg = json.loads(raw.decode())
                if isinstance(msg, dict) and msg.get("id") == call_id:
                    return jsonrpc_take_result([msg], call_id)

        if call(1, "auth.login_with_api_key", [api_key]) is not True:
            raise RuntimeError("truenas api key rejected")
        return call(2, method, list(params or []))
    finally:
        try:
            _ws_send(sock, 0x8, b"")
        except OSError:
            pass
        sock.close()


def scrape_truenas(cfg: dict[str, Any], api_key: str) -> list[dict[str, Any]]:
    if not api_key:
        return []
    base = str(cfg.get("url") or "https://192.168.1.202").rstrip("/")
    tls_verify = bool(cfg.get("tls_verify", False))
    wanted = [str(name) for name in (cfg.get("datasets") or [])]
    rows = truenas_jsonrpc(base, api_key, "pool.dataset.query", [], tls_verify)
    return datasets_from_query(rows, wanted)


def scrape_all() -> tuple[str, int]:
    cfg = load_exporter_config(CONFIG_PATH)
    env = load_auto_env(AUTO_ENV_PATH)
    now = datetime.now(timezone.utc)
    stale_days = int(cfg.get("stale_days") or 180)
    failures = 0

    def run(source: str, fn: Any, default: Any) -> Any:
        nonlocal failures
        try:
            return fn()
        except Exception as err:  # noqa: BLE001
            log("scrape_error", source=source, error=str(err))
            failures += 1
            return default

    jf_items = run(
        "jellyfin",
        lambda: scrape_jellyfin(cfg.get("jellyfin") or {}, env.get("JELLYFIN_API_KEY", "")),
        [],
    )
    watched = watch_index(jf_items if isinstance(jf_items, list) else [])
    items: list[dict[str, Any]] = []
    items.extend(
        run(
            "sonarr",
            lambda: scrape_sonarr(
                cfg.get("sonarr") or {},
                env.get("SONARR_API_KEY", ""),
                watched,
                now,
                stale_days,
            ),
            [],
        )
    )
    items.extend(
        run(
            "radarr",
            lambda: scrape_radarr(
                cfg.get("radarr") or {},
                env.get("RADARR_API_KEY", ""),
                watched,
                now,
                stale_days,
            ),
            [],
        )
    )
    datasets = run(
        "truenas",
        lambda: scrape_truenas(cfg.get("truenas") or {}, TRUENAS_API_KEY),
        [],
    )
    up = 0 if failures == 4 else 1
    return render_metrics(items, datasets, up=up), up


def metrics_body() -> bytes:
    cfg = load_exporter_config(CONFIG_PATH)
    interval = int(cfg.get("scrape_interval_secs") or 3600)
    now = time.time()
    if now - float(_cache["fetched_at"]) < interval and _cache["body"]:
        return _cache["body"]
    text, _up = scrape_all()
    _cache["body"] = text.encode()
    _cache["fetched_at"] = now
    return _cache["body"]


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt: str, *args: object) -> None:
        log("http", message=fmt % args)

    def do_GET(self) -> None:  # noqa: N802
        path = self.path.split("?", 1)[0]
        if path == "/metrics":
            body = metrics_body()
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; version=0.0.4")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if path in ("/health", "/-/healthy", "/"):
            body = b"ok\n"
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        self.send_error(404)


def main() -> None:
    httpd = ThreadingHTTPServer((LISTEN, PORT), Handler)
    log("listening", addr=LISTEN, port=PORT)
    httpd.serve_forever()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(0)
