#!/usr/bin/env python3
"""Accept Alertmanager-format POSTs from vmalert and forward to Discord."""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

WEBHOOK = os.environ.get("DISCORD_WEBHOOK_URL", "")
LISTEN = os.environ.get("LISTEN_ADDR", "0.0.0.0")
PORT = int(os.environ.get("LISTEN_PORT", "8080"))
MAX_ATTEMPTS = 3


def log(msg: str, **fields: object) -> None:
    rec = {"msg": msg, **fields}
    print(json.dumps(rec), flush=True)


def discord_content(alert: dict) -> str:
    labels = alert.get("labels") or {}
    ann = alert.get("annotations") or {}
    name = labels.get("alertname", "alert")
    container = labels.get("container", "")
    status = (alert.get("status") or "").lower()
    if not status:
        ends = alert.get("endsAt") or ""
        status = "resolved" if ends and not ends.startswith("0001-") else "firing"
    source = labels.get("source") or "vmalert"
    prefix = f"{source} RESOLVED" if status == "resolved" else source
    desc = ann.get("description") or ann.get("summary") or json.dumps(labels, sort_keys=True)
    extra = f" container=`{container}`" if container else ""
    return f"**{prefix}: {name}**{extra}\n{desc}"


def post_discord(content: str) -> None:
    body = json.dumps({"content": content[:1900]}).encode()
    delay = 1.0
    last_err: Exception | None = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        req = urllib.request.Request(
            WEBHOOK,
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                resp.read()
                return
        except urllib.error.HTTPError as err:
            last_err = err
            if err.code in (429, 500, 502, 503, 504) and attempt < MAX_ATTEMPTS:
                time.sleep(delay)
                delay *= 2
                continue
            log("discord_http_error", code=err.code, attempt=attempt)
            return
        except Exception as err:  # noqa: BLE001 — last-resort drop
            last_err = err
            if attempt < MAX_ATTEMPTS:
                time.sleep(delay)
                delay *= 2
                continue
            log("discord_error", error=str(err), attempt=attempt)
            return
    if last_err:
        log("discord_dropped", error=str(last_err))


def parse_alerts(raw: bytes) -> list[dict]:
    data = json.loads(raw.decode() or "[]")
    if isinstance(data, dict):
        alerts = data.get("alerts")
        if isinstance(alerts, list):
            return [a for a in alerts if isinstance(a, dict)]
        if "labels" in data:
            return [data]
        return []
    if isinstance(data, list):
        return [a for a in data if isinstance(a, dict)]
    return []


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt: str, *args: object) -> None:
        log("http", message=fmt % args)

    def _ok(self) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b'{"status":"ok"}')

    def do_GET(self) -> None:  # noqa: N802
        if self.path in ("/health", "/-/healthy", "/"):
            self._ok()
            return
        self.send_error(404)

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length") or "0")
        raw = self.rfile.read(length) if length else b"[]"
        try:
            alerts = parse_alerts(raw)
        except json.JSONDecodeError:
            self.send_error(400, "invalid json")
            return
        for alert in alerts:
            post_discord(discord_content(alert))
        self._ok()


def main() -> None:
    if not WEBHOOK or "CHANGE_ME" in WEBHOOK:
        log("missing_webhook")
        sys.exit(1)
    httpd = ThreadingHTTPServer((LISTEN, PORT), Handler)
    log("listening", addr=LISTEN, port=PORT)
    httpd.serve_forever()


if __name__ == "__main__":
    main()
