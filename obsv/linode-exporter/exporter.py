#!/usr/bin/env python3
"""Prometheus exporter for Linode monthly network transfer (account/transfer)."""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

TOKEN = os.environ.get("LINODE_TOKEN", "")
LISTEN = os.environ.get("LISTEN_ADDR", "0.0.0.0")
PORT = int(os.environ.get("LISTEN_PORT", "9102"))
API = "https://api.linode.com/v4/account/transfer"

_cache = {"up": 0, "quota": 0.0, "used": 0.0, "billable": 0.0, "error": "starting"}


def log(msg: str, **fields: object) -> None:
    print(json.dumps({"msg": msg, **fields}), flush=True)


def fetch() -> None:
    if not TOKEN or "CHANGE_ME" in TOKEN:
        # Stay "up" so LinodeExporterDown does not page; transfer alerts are no-ops at quota=0.
        _cache.update(up=1, quota=0.0, used=0.0, billable=0.0, error="token_unset")
        return
    req = urllib.request.Request(API, headers={"Authorization": f"Bearer {TOKEN}"})
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            data = json.loads(resp.read().decode())
        _cache.update(
            up=1,
            quota=float(data.get("quota") or 0),
            used=float(data.get("used") or 0),
            billable=float(data.get("billable") or 0),
            error="",
        )
    except Exception as err:  # noqa: BLE001
        log("linode_fetch_error", error=str(err))
        _cache.update(up=0, error=str(err)[:120])


def metrics_body() -> bytes:
    fetch()
    lines = [
        "# HELP linode_exporter_up 1 if the Linode API was scraped successfully",
        "# TYPE linode_exporter_up gauge",
        f"linode_exporter_up {_cache['up']}",
        "# HELP linode_account_transfer_quota_gb Monthly transfer quota in GB",
        "# TYPE linode_account_transfer_quota_gb gauge",
        f"linode_account_transfer_quota_gb {_cache['quota']}",
        "# HELP linode_account_transfer_used_gb Monthly transfer used in GB",
        "# TYPE linode_account_transfer_used_gb gauge",
        f"linode_account_transfer_used_gb {_cache['used']}",
        "# HELP linode_account_transfer_billable_gb Transfer over quota in GB",
        "# TYPE linode_account_transfer_billable_gb gauge",
        f"linode_account_transfer_billable_gb {_cache['billable']}",
        "",
    ]
    return "\n".join(lines).encode()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt: str, *args: object) -> None:
        log("http", message=fmt % args)

    def do_GET(self) -> None:  # noqa: N802
        if self.path.split("?", 1)[0] in ("/metrics", "/health", "/"):
            body = metrics_body() if self.path.startswith("/metrics") else b"ok\n"
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; version=0.0.4")
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
