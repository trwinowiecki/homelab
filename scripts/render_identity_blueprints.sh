#!/usr/bin/env bash
# Render Authentik blueprints (seed secrets first if needed).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
IDENTITY_ENV="${1:-$ROOT/identity/.env}"
ARR_ENV="${2:-$ROOT/.env}"

python3 "$ROOT/scripts/merge_identity_env.py" --env-file "$IDENTITY_ENV" --write
python3 "$ROOT/scripts/render_identity_blueprints.py" "$IDENTITY_ENV" "$ARR_ENV"
