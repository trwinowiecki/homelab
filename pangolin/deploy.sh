#!/usr/bin/env bash
#
# Render the Pangolin server config from .env and deploy it to the VPS.
#
# Idempotent and non-destructive: it never touches config/db, config/key, or
# config/letsencrypt on the remote, so state and certificates survive a redeploy.
#
#   ./deploy.sh          render, push, and bring the stack up
#   ./deploy.sh --render only render into ./.rendered for inspection
#
set -euo pipefail

cd "$(dirname "$0")"

RENDER_ONLY=false
[[ ${1:-} == "--render" ]] && RENDER_ONLY=true

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
for var in PANGOLIN_DASHBOARD_DOMAIN PANGOLIN_BASE_DOMAIN PANGOLIN_ACME_EMAIL \
           SERVER_SECRET PANGOLIN_SSH_HOST PANGOLIN_REMOTE_DIR \
           PANGOLIN_VERSION GERBIL_VERSION TRAEFIK_VERSION BADGER_VERSION; do
  [[ -z ${!var:-} ]] && MISSING+=("$var")
done
if [[ ${#MISSING[@]} -gt 0 ]]; then
  echo "ERROR: unset in $ENV_FILE: ${MISSING[*]}" >&2
  exit 1
fi

########################################
# Render
########################################

STAGE=.rendered
rm -rf "$STAGE"
mkdir -p "$STAGE/config/traefik"

cp server/docker-compose.yml "$STAGE/docker-compose.yml"

# Substitution is restricted to these four names, and any other ${...} left in a
# template is an error rather than a silently empty value. Python rather than
# envsubst because envsubst is not installed everywhere.
render() {
  python3 - "$1" "$2" <<'PY'
import os, re, sys

allowed = (
    "PANGOLIN_DASHBOARD_DOMAIN",
    "PANGOLIN_BASE_DOMAIN",
    "PANGOLIN_ACME_EMAIL",
    "BADGER_VERSION",
)
src, dst = sys.argv[1], sys.argv[2]
text = open(src).read()

def sub(match):
    name = match.group(1)
    if name not in allowed:
        sys.exit(f"{src}: unexpected variable ${{{name}}}")
    return os.environ[name]

open(dst, "w").write(re.sub(r"\$\{(\w+)\}", sub, text))
PY
}

render server/config/config.yml.tmpl "$STAGE/config/config.yml"
render server/config/traefik/traefik_config.yml.tmpl "$STAGE/config/traefik/traefik_config.yml"
render server/config/traefik/dynamic_config.yml.tmpl "$STAGE/config/traefik/dynamic_config.yml"

# Compose reads these on the VPS. Written 0600 because of SERVER_SECRET.
cat > "$STAGE/.env" <<EOF
PANGOLIN_VERSION=${PANGOLIN_VERSION}
GERBIL_VERSION=${GERBIL_VERSION}
TRAEFIK_VERSION=${TRAEFIK_VERSION}
SERVER_SECRET=${SERVER_SECRET}
EOF
chmod 600 "$STAGE/.env"

echo "Rendered into $STAGE/"
if [[ $RENDER_ONLY == true ]]; then
  exit 0
fi

########################################
# Deploy
########################################

echo "Checking Docker on ${PANGOLIN_SSH_HOST}..."
ssh "$PANGOLIN_SSH_HOST" 'command -v docker >/dev/null 2>&1' || {
  echo "Docker not present. Installing..."
  ssh "$PANGOLIN_SSH_HOST" 'curl -fsSL https://get.docker.com | sh'
}

echo "Creating ${PANGOLIN_REMOTE_DIR}..."
ssh "$PANGOLIN_SSH_HOST" \
  "mkdir -p ${PANGOLIN_REMOTE_DIR}/config/{db,letsencrypt,traefik/logs}"

echo "Syncing configuration..."
rsync -av \
  "$STAGE/docker-compose.yml" "$STAGE/.env" \
  "$PANGOLIN_SSH_HOST:${PANGOLIN_REMOTE_DIR}/"
rsync -av \
  "$STAGE/config/config.yml" \
  "$PANGOLIN_SSH_HOST:${PANGOLIN_REMOTE_DIR}/config/"
rsync -av \
  "$STAGE/config/traefik/traefik_config.yml" \
  "$STAGE/config/traefik/dynamic_config.yml" \
  "$PANGOLIN_SSH_HOST:${PANGOLIN_REMOTE_DIR}/config/traefik/"

echo "Starting stack..."
ssh "$PANGOLIN_SSH_HOST" \
  "cd ${PANGOLIN_REMOTE_DIR} && docker compose up -d && docker compose ps"

cat <<EOF

Deployed. Next:

  1. Watch the logs for the one-time setup token:
       ssh ${PANGOLIN_SSH_HOST} 'cd ${PANGOLIN_REMOTE_DIR} && docker compose logs pangolin | grep -i token'
  2. Register the admin account:
       https://${PANGOLIN_DASHBOARD_DOMAIN}/auth/initial-setup
  3. Create a site named 'homelab', then put its NEWT_ID and NEWT_SECRET into
     site/.env and deploy the connector per pangolin/README.md.
EOF
