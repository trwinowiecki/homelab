#!/usr/bin/env python3
"""Render identity/caddy/Caddyfile from template + env files."""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load_env(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    if not path.is_file():
        return out
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        out[key.strip()] = val.strip().strip('"')
    return out


def render(env_path: Path, arr_env_path: Path, tmpl: Path | None = None, out: Path | None = None) -> int:
    tmpl = tmpl or ROOT / "identity" / "caddy" / "Caddyfile.tmpl"
    out = out or ROOT / "identity" / "caddy" / "Caddyfile"
    if not tmpl.is_file():
        print(f"ERROR: missing {tmpl}", file=sys.stderr)
        return 1

    env = {**load_env(arr_env_path), **load_env(env_path)}

    identity_lan = env.get("IDENTITY_LAN_DOMAIN")
    identity_public = env.get("IDENTITY_PUBLIC_DOMAIN")
    if not identity_lan or not identity_public:
        print("ERROR: IDENTITY_LAN_DOMAIN and IDENTITY_PUBLIC_DOMAIN required", file=sys.stderr)
        return 1

    # qbit port required from arr .env. Dozzle, Grafana, and mealie-og-sync
    # are reached by Docker DNS name, not a published host port.
    if not env.get("WEBUI_PORT_QBITTORRENT"):
        print("ERROR: WEBUI_PORT_QBITTORRENT unset in .env", file=sys.stderr)
        return 1

    acme = env.get("IDENTITY_ACME_DNS", "false").lower() == "true"
    if acme:
        email = env.get("CADDY_ACME_EMAIL", "").strip()
        token = env.get("CLOUDFLARE_API_TOKEN", "").strip()
        if not email:
            print("ERROR: CADDY_ACME_EMAIL required when IDENTITY_ACME_DNS=true", file=sys.stderr)
            return 1
        if not token:
            print(
                "ERROR: CLOUDFLARE_API_TOKEN required when IDENTITY_ACME_DNS=true",
                file=sys.stderr,
            )
            return 1
        # Token is read by Caddy from the container env, not baked into the Caddyfile.
        env["CADDY_GLOBAL_OPTIONS"] = (
            f"email {email}\n\tacme_dns cloudflare {{env.CLOUDFLARE_API_TOKEN}}"
        )
        env["TLS_DIRECTIVE"] = ""
    else:
        env["TLS_DIRECTIVE"] = "tls internal"
        env["CADDY_GLOBAL_OPTIONS"] = ""

    text = tmpl.read_text()
    for key, val in env.items():
        text = text.replace(f"{{${key}}}", val)

    # envsubst-style {$VAR} leftovers from template
    missing = set(re.findall(r"\{\$([A-Z0-9_]+)\}", text))
    if missing:
        print(f"ERROR: unset template vars: {', '.join(sorted(missing))}", file=sys.stderr)
        return 1

    out.write_text(text)
    print(f"Wrote {out}")
    return 0


def main() -> int:
    env_path = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "identity" / ".env"
    arr_env_path = Path(sys.argv[2]) if len(sys.argv) > 2 else ROOT / ".env"
    return render(env_path, arr_env_path)


if __name__ == "__main__":
    raise SystemExit(main())
