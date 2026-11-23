#!/usr/bin/env python3
"""Watch VictoriaLogs for error/warn spikes and new signatures; page discord-relay."""
from __future__ import annotations

import json
import os
import re
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from levels import extract_level  # noqa: F401  — imported for tests / local use

CONFIG_PATH = Path(os.environ.get("LOG_ALERTS_CONFIG", "/app/log-alerts.yaml"))
STATE_PATH = Path(os.environ.get("LOG_WATCH_STATE", "/var/lib/log-watch/state.json"))
VLOGS_URL = os.environ.get("VLOGS_URL", "http://victoria-logs:9428")
RELAY_URL = os.environ.get("RELAY_URL", "http://discord-relay:8080")
TZ_NAME = os.environ.get("TZ", "America/New_York")
LISTEN = os.environ.get("LISTEN_ADDR", "0.0.0.0")
PORT = int(os.environ.get("LISTEN_PORT", "8081"))
LOOP_SECS = int(os.environ.get("LOOP_SECS", "60"))

UUID_RE = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
    re.I,
)
HASH_RE = re.compile(r"\b[0-9a-f]{16,}\b", re.I)
PATH_RE = re.compile(r"(?:/[A-Za-z0-9._-]+)+")
NUM_RE = re.compile(r"\d+")
SPACE_RE = re.compile(r"\s+")
DURATION_RE = re.compile(r"^(\d+)([smhd])$")
UNITS = {"s": 1, "m": 60, "h": 3600, "d": 86400}


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


def load_alerts_config(path: str | Path) -> dict[str, Any]:
    data = load_yaml(Path(path))
    data.setdefault("defaults", {})
    data.setdefault("containers", {})
    return data


def container_settings(cfg: dict[str, Any], name: str) -> dict[str, Any]:
    defaults = dict(cfg.get("defaults") or {})
    extra = (cfg.get("containers") or {}).get(name) or {}
    merged = {**defaults, **extra}
    return merged


def is_muted(cfg: dict[str, Any], name: str) -> bool:
    return bool(container_settings(cfg, name).get("mute"))


def is_ignored(cfg: dict[str, Any], name: str, message: str) -> bool:
    patterns = container_settings(cfg, name).get("ignore") or []
    return any(re.search(str(pat), message or "", re.I) for pat in patterns)


def spike_exceeded(count: int, threshold: int) -> bool:
    return count >= threshold


def parse_duration(value: Any) -> int:
    if isinstance(value, int):
        return value
    text = str(value).strip()
    match = DURATION_RE.fullmatch(text)
    if not match:
        raise ValueError(f"invalid duration: {value!r}")
    return int(match.group(1)) * UNITS[match.group(2)]


def digest_due(
    *,
    now_hour: int,
    now_minute: int,
    last_digest_date: str,
    today: str,
    digest_hour: int,
    digest_minute: int,
) -> bool:
    if last_digest_date == today:
        return False
    return (now_hour, now_minute) >= (digest_hour, digest_minute)


def normalize_signature(msg: str) -> str:
    text = (msg or "").lower()
    text = UUID_RE.sub("*", text)
    text = HASH_RE.sub("*", text)
    text = PATH_RE.sub("/*", text)
    text = NUM_RE.sub("*", text)
    text = SPACE_RE.sub(" ", text).strip()
    return text[:240]


def new_signatures(
    current: dict[str, int],
    known: set[str],
    min_count: int,
) -> dict[str, int]:
    return {
        key: count
        for key, count in current.items()
        if key not in known and count >= min_count
    }


def vlogs_query(query: str, *, limit: int = 2000) -> list[dict[str, Any]]:
    url = VLOGS_URL.rstrip("/") + "/select/logsql/query"
    body = urllib.parse.urlencode({"query": query, "limit": str(limit)}).encode()
    req = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    with urllib.request.urlopen(req, timeout=25) as resp:
        raw = resp.read().decode()
    rows: list[dict[str, Any]] = []
    for line in raw.splitlines():
        if not line.strip():
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(item, dict):
            rows.append(item)
    return rows


def post_relay(alertname: str, container: str, description: str) -> None:
    payload = {
        "alerts": [
            {
                "status": "firing",
                "labels": {
                    "alertname": alertname,
                    "container": container,
                    "source": "log-watch",
                },
                "annotations": {"description": description},
            }
        ]
    }
    data = json.dumps(payload).encode()
    req = urllib.request.Request(
        RELAY_URL.rstrip("/") + "/",
        data=data,
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            resp.read()
    except urllib.error.URLError as err:
        log("relay_error", error=str(err), alertname=alertname)


def load_state() -> dict[str, Any]:
    if not STATE_PATH.is_file():
        return {"signatures": {}, "last_digest": ""}
    try:
        data = json.loads(STATE_PATH.read_text())
    except (json.JSONDecodeError, OSError):
        return {"signatures": {}, "last_digest": ""}
    if not isinstance(data, dict):
        return {"signatures": {}, "last_digest": ""}
    data.setdefault("signatures", {})
    data.setdefault("last_digest", "")
    return data


def save_state(state: dict[str, Any]) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2, sort_keys=True))
    tmp.replace(STATE_PATH)


def collect_level(
    cfg: dict[str, Any],
    level: str,
    window: str,
) -> tuple[dict[str, int], dict[str, dict[str, int]]]:
    """Return per-container counts and per-container signature counts."""
    rows = vlogs_query(f"_time:{window} level:{level}")
    counts: dict[str, int] = {}
    signatures: dict[str, dict[str, int]] = {}
    for row in rows:
        container = str(row.get("container") or "unknown")
        if is_muted(cfg, container):
            continue
        message = str(row.get("message") or row.get("_msg") or "")
        if is_ignored(cfg, container, message):
            continue
        counts[container] = counts.get(container, 0) + 1
        sig = f"{container}|{normalize_signature(message)}"
        bucket = signatures.setdefault(container, {})
        bucket[sig] = bucket.get(sig, 0) + 1
    return counts, signatures


def run_once(cfg: dict[str, Any], *, now: datetime | None = None) -> None:
    tz = ZoneInfo(TZ_NAME)
    now = now or datetime.now(tz)
    defaults = cfg.get("defaults") or {}
    window = str(defaults.get("error_spike_window", "5m"))
    lookback = str(defaults.get("signature_lookback", "7d"))
    min_count = int(defaults.get("new_signature_min_count", 3))
    include_warn = bool(defaults.get("include_warn_in_digest", True))
    digest_hour = int(defaults.get("digest_hour", 8))
    digest_minute = int(defaults.get("digest_minute", 0))

    state = load_state()
    first_run = not state.get("signatures")
    known = set(state.get("signatures") or {})

    error_counts, error_sigs = collect_level(cfg, "error", window)
    _, warn_sigs = collect_level(cfg, "warn", lookback) if include_warn else ({}, {})
    _, error_lookback = collect_level(cfg, "error", lookback)

    for container, count in sorted(error_counts.items()):
        threshold = int(container_settings(cfg, container).get("error_spike_threshold", 15))
        if spike_exceeded(count, threshold):
            post_relay(
                "ContainerLogErrorSpike",
                container,
                f"{container} logged {count} error lines in {window} (threshold {threshold}).",
            )

    current: dict[str, int] = {}
    for bucket in error_lookback.values():
        current.update(bucket)
    fresh = new_signatures(current, known, min_count)
    if not first_run:
        for sig, count in sorted(fresh.items()):
            container = sig.split("|", 1)[0]
            post_relay(
                "ContainerNewErrorSignature",
                container,
                f"New error signature ({count} in {lookback}): `{sig}`",
            )
    now_iso = now.isoformat()
    signatures = dict(state.get("signatures") or {})
    for sig, count in current.items():
        entry = signatures.get(sig) or {"first_seen": now_iso, "count": 0}
        entry["last_seen"] = now_iso
        entry["count"] = int(entry.get("count") or 0) + count
        signatures[sig] = entry

    today = now.date().isoformat()
    if digest_due(
        now_hour=now.hour,
        now_minute=now.minute,
        last_digest_date=str(state.get("last_digest") or ""),
        today=today,
        digest_hour=digest_hour,
        digest_minute=digest_minute,
    ):
        lines = ["Daily error/warn digest"]
        containers = sorted(set(error_lookback) | set(warn_sigs))
        if not containers:
            lines.append("No error or warn lines in lookback.")
        for container in containers:
            lines.append(f"**{container}**")
            top_err = sorted(
                (error_lookback.get(container) or {}).items(),
                key=lambda item: item[1],
                reverse=True,
            )[:5]
            for sig, count in top_err:
                lines.append(f"- error x{count}: `{sig}`")
            if include_warn:
                top_warn = sorted(
                    (warn_sigs.get(container) or {}).items(),
                    key=lambda item: item[1],
                    reverse=True,
                )[:3]
                for sig, count in top_warn:
                    lines.append(f"- warn x{count}: `{sig}`")
        post_relay("ContainerLogDigest", "", "\n".join(lines))
        state["last_digest"] = today

    state["signatures"] = signatures
    save_state(state)


def loop() -> None:
    while True:
        try:
            cfg = load_alerts_config(CONFIG_PATH)
            run_once(cfg)
        except Exception as err:  # noqa: BLE001
            log("loop_error", error=str(err))
        time.sleep(LOOP_SECS)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt: str, *args: object) -> None:
        log("http", message=fmt % args)

    def do_GET(self) -> None:  # noqa: N802
        if self.path.split("?", 1)[0] in ("/health", "/-/healthy", "/"):
            body = b"ok\n"
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        self.send_error(404)


def main() -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    thread = threading.Thread(target=loop, name="log-watch", daemon=True)
    thread.start()
    httpd = ThreadingHTTPServer((LISTEN, PORT), Handler)
    log("listening", addr=LISTEN, port=PORT)
    httpd.serve_forever()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(0)
