#!/usr/bin/env bash
# Render identity/caddy/Caddyfile — wrapper for deploy.sh
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
exec python3 "$ROOT/scripts/render_identity_caddy.py" "${1:-$ROOT/identity/.env}" "${2:-$ROOT/.env}"
