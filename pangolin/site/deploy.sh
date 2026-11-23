#!/usr/bin/env bash
#
# Push the Newt connector and its blueprint to the site host (CT 103) and
# restart it. Idempotent; safe to re-run after editing blueprint.yaml.
#
set -euo pipefail

cd "$(dirname "$0")"

ENV_FILE=${ENV_FILE:-.env}
if [[ ! -f $ENV_FILE ]]; then
  echo "ERROR: $ENV_FILE not found. Copy .env.example to .env and fill it in." >&2
  exit 1
fi

set -a
# shellcheck disable=SC1090
source "$ENV_FILE"
set +a

MISSING=()
for var in PANGOLIN_DASHBOARD_DOMAIN NEWT_ID NEWT_SECRET NEWT_VERSION \
           SITE_SSH_HOST SITE_REMOTE_DIR; do
  [[ -z ${!var:-} ]] && MISSING+=("$var")
done
if [[ ${#MISSING[@]} -gt 0 ]]; then
  echo "ERROR: unset in $ENV_FILE: ${MISSING[*]}" >&2
  exit 1
fi

echo "Creating ${SITE_REMOTE_DIR} on ${SITE_SSH_HOST}..."
ssh "$SITE_SSH_HOST" "mkdir -p ${SITE_REMOTE_DIR}"

echo "Syncing connector..."
rsync -av docker-compose.yml blueprint.yaml "$SITE_SSH_HOST:${SITE_REMOTE_DIR}/"
rsync -av --chmod=600 "$ENV_FILE" "$SITE_SSH_HOST:${SITE_REMOTE_DIR}/.env"

echo "Starting connector..."
ssh "$SITE_SSH_HOST" \
  "cd ${SITE_REMOTE_DIR} && docker compose up -d && sleep 5 && docker compose ps"

echo
echo "Blueprint applied by Newt. Check it took effect:"
echo "  ssh ${SITE_SSH_HOST} 'cd ${SITE_REMOTE_DIR} && docker compose logs newt | tail -30'"
