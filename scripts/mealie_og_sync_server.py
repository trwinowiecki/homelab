#!/usr/bin/env python3
"""LAN HTTP trigger for Mealie → OurGroceries sync."""
from __future__ import annotations

import hmac
import os
import sys
from html import escape
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from sync_mealie_ourgroceries import (  # noqa: E402
    SyncSettings,
    render_html_summary,
    run_sync_async,
)


class SyncHandler(BaseHTTPRequestHandler):
    expected_token = os.environ.get("MEALIE_OG_SYNC_TOKEN", "").strip()
    server_version = "mealie-og-sync/1.0"

    def log_message(self, fmt: str, *args) -> None:
        # The sync token is a query parameter. Log the path only.
        path = urlparse(self.path).path
        sys.stderr.write("%s - %s %s\n" % (self.address_string(), self.command, path))

    def _send_html(self, status: int, body: str) -> None:
        encoded = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def _send_text(self, status: int, body: str) -> None:
        encoded = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def _token_ok(self) -> bool:
        query = parse_qs(urlparse(self.path).query)
        supplied = (query.get("token") or [""])[0]
        if not self.expected_token or not supplied:
            return False
        return hmac.compare_digest(supplied, self.expected_token)

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path == "/":
            self._send_html(
                200,
                """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Sync groceries</title>
</head>
<body>
  <h1>Mealie → OurGroceries sync</h1>
  <p>Bookmark <code>/sync?token=YOUR_TOKEN</code> on this host to push the Mealie staging list to OurGroceries.</p>
</body>
</html>""",
            )
            return

        if path != "/sync":
            self._send_text(404, "Not found\n")
            return

        if not self._token_ok():
            self._send_text(403, "Forbidden\n")
            return

        settings = SyncSettings(
            mealie_base_url=os.environ.get("MEALIE_BASE_URL", "http://mealie:9000").strip(),
            mealie_api_token=os.environ.get("MEALIE_API_TOKEN", "").strip(),
            mealie_staging_list=os.environ.get(
                "MEALIE_STAGING_LIST",
                "Weekly Meals (staging)",
            ).strip(),
            og_email=os.environ.get("OURGROCERIES_EMAIL", "").strip(),
            og_password=os.environ.get("OURGROCERIES_PASSWORD", "").strip(),
            config_path=Path(
                os.environ.get(
                    "MEALIE_OG_SYNC_CONFIG",
                    "/config/mealie-og-sync.yaml",
                ),
            ),
            keep_staging=False,
        )

        try:
            import asyncio

            stats, items = asyncio.run(run_sync_async(settings))
            html = render_html_summary(stats, len(items))
            status = 200 if not stats.errors else 207
            self._send_html(status, html)
        except Exception as exc:  # noqa: BLE001 — return error page to phone
            self._send_html(
                500,
                f"""<!DOCTYPE html>
<html lang="en"><body>
<h1>Sync failed</h1><p>{escape(str(exc))}</p>
</body></html>""",
            )


def main() -> None:
    host = os.environ.get("MEALIE_OG_SYNC_HOST", "0.0.0.0")
    port = int(os.environ.get("MEALIE_OG_SYNC_PORT", "9926"))
    if not os.environ.get("MEALIE_OG_SYNC_TOKEN", "").strip():
        raise SystemExit("MEALIE_OG_SYNC_TOKEN is required")
    server = ThreadingHTTPServer((host, port), SyncHandler)
    print(f"mealie-og-sync listening on {host}:{port}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
