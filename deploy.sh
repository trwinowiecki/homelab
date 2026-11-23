#!/usr/bin/env bash
#
# Non-destructive deploy from this repo to the homelab.
#
# Never runs restart_no_sudo.sh / restart.sh. Those prune containers, volumes,
# networks, and (on success) images. This script rsyncs config and
# starts services; it does not prune.
#
#   ./deploy.sh obsv         # observability stack on CT 100
#   ./deploy.sh identity     # Authentik + Caddy (LAN SSO / HTTPS)
#   ./deploy.sh jellyfin     # Jellyfin media server on CT 104 (isolated compose)
#   ./deploy.sh arrstack     # media compose file + restart scripts (compose up -d)
#   ./deploy.sh pve          # node_exporter, LVM textfile, thin-pool cron, webhook file
#   ./deploy.sh adguard      # filter lists onto CT 102 (brief DNS/DHCP stop)
#   ./deploy.sh obsv pve     # typical first ship
#   ./deploy.sh --dry-run obsv pve
#
set -euo pipefail

cd "$(dirname "$0")"

PVE_SSH="${PVE_SSH:-root@192.168.1.200}"
ARRSTACK_CTID="${ARRSTACK_CTID:-100}"
JELLYFIN_CTID="${JELLYFIN_CTID:-104}"
ADGUARD_CTID="${ADGUARD_CTID:-102}"
ADGUARD_DIR="${ADGUARD_DIR:-/opt/AdGuardHome}"
# Host path of the TrueNAS config share (CT 100 / CT 104 see this as /mnt/config).
CONFIG_ON_PVE="${CONFIG_ON_PVE:-/mnt/lxc_shares/metrion/config}"
CONFIG_IN_CT="${CONFIG_IN_CT:-/mnt/config}"
OBSV_DATA="${OBSV_DATA:-/arrstack/obsv}"
IDENTITY_CADDY_CA_SRC="${IDENTITY_CADDY_CA_SRC:-/arrstack/identity/caddy-data/caddy/pki/authorities/local/root.crt}"
IDENTITY_CADDY_CA_DST="${IDENTITY_CADDY_CA_DST:-/arrstack/identity/caddy-ca/root.crt}"
IDENTITY_DATA="${IDENTITY_DATA:-/arrstack/identity}"
JELLYFIN_DATA="${JELLYFIN_DATA:-/arrstack/jellyfin}"
PUID="${PUID:-10000}"
PGID="${PGID:-10000}"

DRY_RUN=false
TARGETS=()

# Decrypt SOPS secrets and render site templates when age key is available.
# Plaintext .env files already present are left alone if decrypt is skipped.
prepare_secrets() {
  if [[ -f secrets/site.sops.env ]]; then
    if command -v sops >/dev/null 2>&1 && { [[ -f ${SOPS_AGE_KEY_FILE:-.age/workstation.key} ]] || [[ -f /etc/homelab/age.key ]]; }; then
      if [[ $DRY_RUN == true ]]; then
        echo "DRY: ./scripts/secrets.sh decrypt"
      else
        ./scripts/secrets.sh decrypt
      fi
    else
      echo "WARN: SOPS encrypted secrets present but sops/age key unavailable — using existing plaintext if any"
    fi
  fi
  if [[ -f secrets/site.env ]]; then
    # shellcheck disable=SC1091
    set -a
    # shellcheck source=/dev/null
    source secrets/site.env
    set +a
    PVE_SSH="${PVE_SSH:-${SITE_PVE_SSH:-root@192.168.1.200}}"
    ARRSTACK_CTID="${ARRSTACK_CTID:-${SITE_ARRSTACK_CTID:-100}}"
    JELLYFIN_CTID="${JELLYFIN_CTID:-${SITE_JELLYFIN_CTID:-104}}"
    ADGUARD_CTID="${ADGUARD_CTID:-${SITE_ADGUARD_CTID:-102}}"
  fi
  if [[ -f adguard/dns-rewrites.yaml.tmpl || -f pangolin/site/blueprint.yaml.tmpl ]]; then
    if [[ $DRY_RUN == true ]]; then
      echo "DRY: python3 scripts/render_site_templates.py"
    else
      python3 scripts/render_site_templates.py
    fi
  fi
}

usage() {
  sed -n '2,15p' "$0" | sed 's/^# \?//'
  exit "${1:-0}"
}

for arg in "$@"; do
  case "$arg" in
    -h|--help) usage 0 ;;
    --dry-run) DRY_RUN=true ;;
    obsv|identity|arrstack|jellyfin|pve|adguard) TARGETS+=("$arg") ;;
    *)
      echo "ERROR: unknown argument: $arg" >&2
      usage 1
      ;;
  esac
done

if [[ ${#TARGETS[@]} -eq 0 ]]; then
  echo "ERROR: name at least one target." >&2
  usage 1
fi

run() {
  if [[ $DRY_RUN == true ]]; then
    printf 'DRY: '
    printf '%q ' "$@"
    printf '\n'
    return 0
  fi
  "$@"
}

ssh_pve() {
  run ssh "$PVE_SSH" "$@"
}

# Run a command as root inside CT 100 (arrstack / identity / obsv). Quoting: the
# remote bash -lc string is a single argument to pct exec.
pct() {
  local cmd=$1
  run ssh "$PVE_SSH" "pct exec ${ARRSTACK_CTID} -- bash -lc $(printf '%q' "$cmd")"
}

# Same for CT 104 (Jellyfin Docker).
pct_jellyfin() {
  local cmd=$1
  run ssh "$PVE_SSH" "pct exec ${JELLYFIN_CTID} -- bash -lc $(printf '%q' "$cmd")"
}

need_file() {
  [[ -f $1 ]] || { echo "ERROR: missing $1" >&2; exit 1; }
}

webhook_from_obsv_env() {
  need_file obsv/.env
  # shellcheck disable=SC1091
  set -a
  source obsv/.env
  set +a
  if [[ -z ${DISCORD_WEBHOOK_URL:-} || $DISCORD_WEBHOOK_URL == *CHANGE_ME* ]]; then
    echo "ERROR: DISCORD_WEBHOOK_URL is unset or still CHANGE_ME in obsv/.env" >&2
    exit 1
  fi
}

sync_identity_caddy_ca() {
  echo "Publishing Caddy internal CA for OIDC containers..."
  pct "mkdir -p \"\$(dirname '${IDENTITY_CADDY_CA_DST}')\" && \
    if [ -f '${IDENTITY_CADDY_CA_SRC}' ]; then \
      cp -f '${IDENTITY_CADDY_CA_SRC}' '${IDENTITY_CADDY_CA_DST}' && chmod 644 '${IDENTITY_CADDY_CA_DST}'; \
    else \
      echo 'WARN: ${IDENTITY_CADDY_CA_SRC} not found — run ./deploy.sh identity first'; \
    fi"
}

########################################
# obsv → CT 100 via the config share
########################################

deploy_obsv() {
  echo "==> obsv"
  need_file obsv/.env
  need_file obsv/docker-compose.yaml
  webhook_from_obsv_env

  echo "Validating compose locally..."
  if [[ $DRY_RUN == false ]]; then
    docker compose -f obsv/docker-compose.yaml --env-file obsv/.env config >/dev/null
  else
    echo "DRY: docker compose -f obsv/docker-compose.yaml --env-file obsv/.env config"
  fi

  ssh_pve "mkdir -p ${CONFIG_ON_PVE}/obsv"
  # Data dirs live on the LXC SSD, not the CIFS share and not on pve.
  # Grafana provisioning is copied off CIFS because that share is 0770 and
  # Grafana runs as uid 472.
  pct "mkdir -p ${OBSV_DATA}/victoria-logs ${OBSV_DATA}/vector-buffer ${OBSV_DATA}/victoria-metrics ${OBSV_DATA}/grafana ${OBSV_DATA}/grafana-provisioning ${OBSV_DATA}/grafana-dashboards ${OBSV_DATA}/log-watch && touch ${OBSV_DATA}/deploy-markers.log && touch /arrstack/config/arrstack.auto.env && chown -R ${PUID}:${PGID} ${OBSV_DATA}/victoria-logs ${OBSV_DATA}/vector-buffer ${OBSV_DATA}/victoria-metrics ${OBSV_DATA}/deploy-markers.log ${OBSV_DATA}/log-watch && chown -R 472:472 ${OBSV_DATA}/grafana ${OBSV_DATA}/grafana-provisioning ${OBSV_DATA}/grafana-dashboards"

  echo "Syncing obsv/ → ${PVE_SSH}:${CONFIG_ON_PVE}/obsv/"
  run rsync -a --delete \
    --exclude '.env' \
    --exclude '__pycache__/' \
    --exclude '*.pyc' \
    obsv/ "${PVE_SSH}:${CONFIG_ON_PVE}/obsv/"
  run rsync -a --chmod=600 obsv/.env "${PVE_SSH}:${CONFIG_ON_PVE}/obsv/.env"

  pct "rm -rf ${OBSV_DATA}/grafana-provisioning ${OBSV_DATA}/grafana-dashboards && mkdir -p ${OBSV_DATA}/grafana-provisioning ${OBSV_DATA}/grafana-dashboards && cp -a ${CONFIG_IN_CT}/obsv/grafana/provisioning/. ${OBSV_DATA}/grafana-provisioning/ && cp -a ${CONFIG_IN_CT}/obsv/grafana/dashboards/. ${OBSV_DATA}/grafana-dashboards/ && chown -R 472:472 ${OBSV_DATA}/grafana ${OBSV_DATA}/grafana-provisioning ${OBSV_DATA}/grafana-dashboards"

  echo "Starting obsv (compose up -d --build)..."
  pct "cd ${CONFIG_IN_CT}/obsv && docker compose config >/dev/null && docker compose up -d --build && docker compose ps"
}

########################################
# identity → CT 100 (Authentik + Caddy)
########################################

deploy_identity() {
  echo "==> identity"
  need_file identity/.env
  need_file identity/docker-compose.yaml
  need_file .env

  echo "Rendering Caddyfile and Authentik blueprints..."
  if [[ $DRY_RUN == false ]]; then
    chmod +x scripts/render_identity_caddy.sh scripts/render_identity_caddy.py scripts/render_identity_blueprints.sh scripts/check_ipv6_exposure.sh
    chmod +x scripts/merge_identity_env.py scripts/seed_identity_oauth.py scripts/render_identity_blueprints.py scripts/apply_identity_blueprints.py scripts/sync_identity_oauth_env.py scripts/sync_ldap_outpost_token.py scripts/sync_jellyfin_ldap_bind.py
    ./scripts/render_identity_blueprints.sh identity/.env .env
    ./scripts/render_identity_caddy.sh identity/.env .env
    docker compose -f identity/docker-compose.yaml --env-file identity/.env config >/dev/null
  else
    echo "DRY: ./scripts/render_identity_blueprints.sh identity/.env .env"
    echo "DRY: ./scripts/render_identity_caddy.sh identity/.env .env"
    echo "DRY: docker compose -f identity/docker-compose.yaml --env-file identity/.env config"
  fi

  ssh_pve "mkdir -p ${CONFIG_ON_PVE}/identity"
  echo "Syncing identity/ → ${PVE_SSH}:${CONFIG_ON_PVE}/identity/"
  run rsync -a --delete \
    --exclude '.env' \
    --exclude 'caddy/Caddyfile' \
    identity/ "${PVE_SSH}:${CONFIG_ON_PVE}/identity/"
  run rsync -a --chmod=600 identity/.env "${PVE_SSH}:${CONFIG_ON_PVE}/identity/.env"
  run rsync -a identity/caddy/Caddyfile "${PVE_SSH}:${CONFIG_ON_PVE}/identity/caddy/Caddyfile"

  pct "mkdir -p ${IDENTITY_DATA}/{caddy-data,caddy-config,postgresql,redis,authentik-media,authentik-templates,authentik-certs,authentik-blueprints}"
  pct "rm -rf ${IDENTITY_DATA}/authentik-blueprints/homelab && cp ${CONFIG_IN_CT}/identity/authentik/blueprint-*.yaml ${IDENTITY_DATA}/authentik-blueprints/ && chmod 0644 ${IDENTITY_DATA}/authentik-blueprints/*.yaml"

  echo "Ensuring mediastack Docker network exists..."
  pct "docker network inspect mediastack >/dev/null 2>&1 || docker network create --driver bridge --subnet ${SITE_MEDIASTACK_SUBNET:-172.28.10.0/24} --gateway ${SITE_MEDIASTACK_GATEWAY:-172.28.10.1} mediastack"

  echo "Starting identity (compose up -d --build)..."
  pct "cd ${CONFIG_IN_CT}/identity && docker compose config >/dev/null && docker compose up -d --build && docker compose ps"

  if [[ $DRY_RUN == false ]]; then
    sync_identity_caddy_ca
  else
    echo "DRY: sync_identity_caddy_ca"
  fi

  if [[ $DRY_RUN == false ]]; then
    echo "Applying Authentik blueprints..."
    python3 scripts/apply_identity_blueprints.py --pve-ssh "${PVE_SSH}" --ct 100 --compose-dir "${CONFIG_IN_CT}/identity"
    echo "Syncing Jellyfin LDAP bind password..."
    python3 scripts/sync_jellyfin_ldap_bind.py --identity-env identity/.env --pve-ssh "${PVE_SSH}" --ct 100 --compose-dir "${CONFIG_IN_CT}/identity"
    echo "Syncing LDAP outpost token..."
    python3 scripts/sync_ldap_outpost_token.py --identity-env identity/.env --pve-ssh "${PVE_SSH}" --ct 100 --compose-dir "${CONFIG_IN_CT}/identity"
    pct "cd ${CONFIG_IN_CT}/identity && docker compose restart ldap-outpost"
    echo "Syncing OIDC env into .env and obsv/.env..."
    python3 scripts/sync_identity_oauth_env.py --identity-env identity/.env --write
    python3 scripts/print_identity_manual_steps.py
    echo ""
    echo "Next: ./deploy.sh arrstack obsv  # restart Mealie/Grafana with synced OIDC env"
  else
    echo "DRY: python3 scripts/apply_identity_blueprints.py"
    echo "DRY: python3 scripts/sync_jellyfin_ldap_bind.py"
    echo "DRY: python3 scripts/sync_ldap_outpost_token.py"
    echo "DRY: python3 scripts/sync_identity_oauth_env.py --write"
  fi
}

########################################
# jellyfin → CT 104 (dedicated Docker guest; not on arrstack)
########################################

deploy_jellyfin() {
  echo "==> jellyfin (CT ${JELLYFIN_CTID})"
  need_file jellyfin/.env.example
  need_file jellyfin/docker-compose.yaml
  need_file jellyfin/bootstrap.yml

  if [[ ! -f jellyfin/.env ]]; then
    echo "ERROR: missing jellyfin/.env (copy from jellyfin/.env.example)" >&2
    exit 1
  fi

  echo "Validating compose locally..."
  if [[ $DRY_RUN == false ]]; then
    docker compose -f jellyfin/docker-compose.yaml --env-file jellyfin/.env config >/dev/null
  else
    echo "DRY: docker compose -f jellyfin/docker-compose.yaml --env-file jellyfin/.env config"
  fi

  ssh_pve "mkdir -p ${CONFIG_ON_PVE}/jellyfin"
  echo "Syncing jellyfin/ → ${PVE_SSH}:${CONFIG_ON_PVE}/jellyfin/"
  run rsync -a --delete \
    --exclude '.env' \
    --exclude '__pycache__/' \
    --exclude '*.pyc' \
    jellyfin/ "${PVE_SSH}:${CONFIG_ON_PVE}/jellyfin/"
  run rsync -a --chmod=600 jellyfin/.env "${PVE_SSH}:${CONFIG_ON_PVE}/jellyfin/.env"

  run rsync -a --chmod=755 \
    scripts/apply_jellyfin_bootstrap.py \
    scripts/minyaml.py \
    scripts/arrstack_api_keys.py \
    "${PVE_SSH}:${CONFIG_ON_PVE}/scripts/"
  run rsync -a --chmod=755 \
    scripts/bootstrap/jellyfin.py \
    scripts/bootstrap/common.py \
    "${PVE_SSH}:${CONFIG_ON_PVE}/scripts/bootstrap/"

  pct_jellyfin "mkdir -p ${JELLYFIN_DATA}/config/plugins/configurations ${JELLYFIN_DATA}/config/data ${JELLYFIN_DATA}/config/cache ${JELLYFIN_DATA}/config/log && chown -R ${PUID}:${PGID} ${JELLYFIN_DATA}"

  echo "Starting jellyfin on CT ${JELLYFIN_CTID} (compose up -d)..."
  pct_jellyfin "cd ${CONFIG_IN_CT}/jellyfin && \
    docker compose -f docker-compose.yaml config >/dev/null && \
    docker compose -f docker-compose.yaml up -d && docker compose ps"

  if [[ $DRY_RUN == false ]]; then
    echo "Applying Jellyfin bootstrap (plugins + LDAP)..."
    pct_jellyfin "python3 ${CONFIG_IN_CT}/scripts/apply_jellyfin_bootstrap.py --local \
      --env-file ${CONFIG_IN_CT}/jellyfin/.env \
      --identity-env-file ${CONFIG_IN_CT}/identity/.env"
  else
    echo "DRY: python3 scripts/apply_jellyfin_bootstrap.py --local (on CT ${JELLYFIN_CTID})"
  fi
}

########################################
# arrstack → same share, compose up only
########################################

deploy_arrstack() {
  echo "==> arrstack"
  need_file docker-compose.yaml
  need_file .env
  need_file .env.example

  echo "Validating compose locally..."
  if [[ $DRY_RUN == false ]]; then
    docker compose -f docker-compose.yaml --env-file .env config >/dev/null
    python3 scripts/check_env.py --env-file .env
  else
    echo "DRY: docker compose -f docker-compose.yaml --env-file .env config"
    echo "DRY: python3 scripts/check_env.py --env-file .env"
  fi

  echo "Syncing compose, restart scripts, and arrstack declarative configs..."
  run rsync -a \
    docker-compose.yaml \
    .env.example \
    restart_no_sudo.sh \
    restart.sh \
    "${PVE_SSH}:${CONFIG_ON_PVE}/"

  echo "Syncing .env → ${PVE_SSH}:${CONFIG_ON_PVE}/.env"
  run rsync -a --chmod=600 .env "${PVE_SSH}:${CONFIG_ON_PVE}/.env"

  ssh_pve "mkdir -p ${CONFIG_ON_PVE}/arrstack ${CONFIG_ON_PVE}/scripts"
  run rsync -a arrstack/ "${PVE_SSH}:${CONFIG_ON_PVE}/arrstack/"
  run rsync -a --chmod=755 \
    scripts/apply_arrstack_bootstrap.py \
    scripts/check_env.py \
    scripts/merge_env.py \
    scripts/prune_arr_quality_profiles.py \
    scripts/seed_arrstack_keys.py \
    scripts/sync_arrstack_api_keys.py \
    scripts/arrstack_api_keys.py \
    scripts/identity_oauth.py \
    scripts/minyaml.py \
    scripts/og_resolver.py \
    scripts/sync_mealie_ourgroceries.py \
    scripts/mealie_og_sync_server.py \
    scripts/qbit-ssd-cleanup.py \
    "${PVE_SSH}:${CONFIG_ON_PVE}/scripts/"
  run rsync -a scripts/mealie-og-sync/ "${PVE_SSH}:${CONFIG_ON_PVE}/scripts/mealie-og-sync/"
  run rsync -a --chmod=755 scripts/bootstrap/ "${PVE_SSH}:${CONFIG_ON_PVE}/scripts/bootstrap/"

  echo "Seeding deterministic API keys into config volumes..."
  pct "python3 ${CONFIG_IN_CT}/scripts/seed_arrstack_keys.py \
    --env-file ${CONFIG_IN_CT}/.env"

  if [[ $DRY_RUN == false ]]; then
    sync_identity_caddy_ca
  fi

  echo "Syncing API keys to arrstack.auto.env on CT ${ARRSTACK_CTID}..."
  pct "python3 ${CONFIG_IN_CT}/scripts/sync_arrstack_api_keys.py \
    --env-file ${CONFIG_IN_CT}/.env"

  echo "Installing declarative configs into arrstack data dirs on CT ${ARRSTACK_CTID}..."
  pct "FOLDER_FOR_DATA=\$(grep -E '^FOLDER_FOR_DATA=' ${CONFIG_IN_CT}/.env | cut -d= -f2- | tr -d '\r' | xargs) && \
    mkdir -p \"\${FOLDER_FOR_DATA}\"/audiobookshelf/{config,metadata} \"\${FOLDER_FOR_DATA}\"/bindery \"\${FOLDER_FOR_DATA}\"/configarr \"\${FOLDER_FOR_DATA}\"/homepage \"\${FOLDER_FOR_DATA}\"/mealie \"\${FOLDER_FOR_DATA}\"/mealie-og-sync \"\${FOLDER_FOR_DATA}\"/mixarr \"\${FOLDER_FOR_DATA}\"/recyclarr && \
    cp -f ${CONFIG_IN_CT}/arrstack/recyclarr.yml \"\${FOLDER_FOR_DATA}\"/recyclarr/recyclarr.yml && \
    cp -f ${CONFIG_IN_CT}/arrstack/mealie-og-sync.yaml \"\${FOLDER_FOR_DATA}\"/mealie-og-sync/mealie-og-sync.yaml && \
    cp -n ${CONFIG_IN_CT}/arrstack/mealie-og-sync/credentials.env.example \"\${FOLDER_FOR_DATA}\"/mealie-og-sync/credentials.env 2>/dev/null || true && \
    cp -f ${CONFIG_IN_CT}/arrstack/configarr/config.yml \"\${FOLDER_FOR_DATA}\"/configarr/config.yml && \
    cp -f ${CONFIG_IN_CT}/arrstack/homepage/*.yaml \"\${FOLDER_FOR_DATA}\"/homepage/ && \
    chown -R ${PUID}:${PGID} \"\${FOLDER_FOR_DATA}\"/recyclarr \"\${FOLDER_FOR_DATA}\"/configarr \"\${FOLDER_FOR_DATA}\"/homepage \"\${FOLDER_FOR_DATA}\"/bindery \"\${FOLDER_FOR_DATA}\"/mixarr \"\${FOLDER_FOR_DATA}\"/audiobookshelf 2>/dev/null || true && \
    FOLDER_FOR_DOWNLOADS=\$(grep -E '^FOLDER_FOR_DOWNLOADS=' ${CONFIG_IN_CT}/.env | cut -d= -f2- | tr -d '\r' | xargs) && \
    mkdir -p \"\${FOLDER_FOR_DOWNLOADS}\"/usenet/{complete,incomplete} \"\${FOLDER_FOR_DOWNLOADS}\"/usenet/complete/{tv,movies,music,audio} && \
    chown -R ${PUID}:${PGID} \"\${FOLDER_FOR_DOWNLOADS}\"/usenet && \
    if [ -d \"\${FOLDER_FOR_DATA}\"/jellyseerr ]; then chown -R 1000:1000 \"\${FOLDER_FOR_DATA}\"/jellyseerr; fi && \
    install -m 0755 ${CONFIG_IN_CT}/scripts/qbit-ssd-cleanup.py /usr/local/sbin/qbit-ssd-cleanup.py"

  echo "Removing retired containers that may still hold ports (Jellyseerr → Seerr migration)..."
  pct "for c in jellyseerr buildarr readarr mylar whisparr unpackerr; do docker rm -f \"\$c\" 2>/dev/null || true; done"

  echo "Starting Gluetun, then remaining arrstack services..."
  pct "cd ${CONFIG_IN_CT} && \
    FOLDER_FOR_DATA=\$(grep -E '^FOLDER_FOR_DATA=' .env | cut -d= -f2- | tr -d '\r' | xargs) && \
    if [ -f \"\${FOLDER_FOR_DATA}/arrstack.auto.env\" ]; then set -a && . \"\${FOLDER_FOR_DATA}/arrstack.auto.env\" && set +a; fi && \
    docker compose up -d gluetun && \
    GLUETUN_OK=0 && \
    for i in \$(seq 1 30); do \
      h=\$(docker inspect gluetun --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}starting{{end}}' 2>/dev/null || echo missing); \
      if [ \"\$h\" = healthy ]; then GLUETUN_OK=1; echo gluetun healthy; break; fi; \
      sleep 2; \
    done && \
    if [ \"\$GLUETUN_OK\" -eq 0 ]; then \
      echo 'WARN: gluetun not healthy after 60s — continuing without qbittorrent'; \
      echo '      Fix VPN_USERNAME/VPN_PASSWORD in /mnt/config/.env (Nord service credentials)'; \
    fi && \
    docker compose config >/dev/null && \
    if [ \"\$GLUETUN_OK\" -eq 0 ]; then \
      docker compose up -d --remove-orphans \$(docker compose config --services | grep -vx qbittorrent); \
    else \
      docker compose up -d --remove-orphans; \
    fi && \
    docker compose ps"

  echo "Post-up: wait for APIs, re-sync keys, apply declarative bootstrap..."
  pct "cd ${CONFIG_IN_CT} && \
    FOLDER_FOR_DATA=\$(grep -E '^FOLDER_FOR_DATA=' .env | cut -d= -f2- | tr -d '\r' | xargs) && \
    python3 scripts/sync_arrstack_api_keys.py --env-file .env && \
    if [ -f \"\${FOLDER_FOR_DATA}/arrstack.auto.env\" ]; then set -a && . \"\${FOLDER_FOR_DATA}/arrstack.auto.env\" && set +a; fi && \
    docker compose up -d && \
    python3 scripts/apply_arrstack_bootstrap.py --env-file .env --wait --wait-timeout 120 \
      --only prowlarr,sabnzbd,bazarr,bindery,audiobookshelf,portainer && \
    if grep -qE '^AUDIOBOOKSHELF_OIDC_ENABLED=true' .env 2>/dev/null; then \
      echo 'Restarting audiobookshelf to load OIDC strategy...'; \
      docker restart audiobookshelf; \
    fi && \
    echo '==> recyclarr sync (TRaSH quality profiles + custom formats)' && \
    docker compose run --rm recyclarr sync && \
    echo '==> prune stale quality profiles' && \
    python3 scripts/prune_arr_quality_profiles.py --env-file .env && \
    echo '==> seerr profile sync (HD + 4K)' && \
    python3 scripts/apply_arrstack_bootstrap.py --env-file .env --only seerr --sync-profiles && \
    echo '==> configarr (root folders + download clients)' && \
    docker compose --profile tools run --rm --no-deps configarr"
}

########################################
# pve hypervisor: node_exporter + crons
########################################

deploy_pve() {
  echo "==> pve"
  need_file obsv/host/thinpool-alert.sh
  need_file obsv/host/lvm-textfile.sh
  need_file obsv/host/ct100-disk-alert.sh
  need_file obsv/host/guest-fstrim.sh
  need_file scripts/qbit-ssd-cleanup.py
  need_file scripts/backup/backup.sh
  need_file scripts/backup/backup-alert.sh
  need_file scripts/backup/homelab-backup.service
  need_file scripts/backup/homelab-backup-alert@.service
  need_file scripts/backup/homelab-backup.timer
  webhook_from_obsv_env

  echo "Installing prometheus-node-exporter if missing..."
  if [[ $DRY_RUN == true ]]; then
    echo "DRY: apt-get install prometheus-node-exporter (if needed)"
    echo "DRY: systemctl enable --now prometheus-node-exporter"
  else
    ssh "$PVE_SSH" 'bash -s' <<'REMOTE'
set -euo pipefail
if ! dpkg -s prometheus-node-exporter >/dev/null 2>&1; then
  apt-get update
  DEBIAN_FRONTEND=noninteractive apt-get install -y prometheus-node-exporter
fi
mkdir -p /var/lib/prometheus/node-exporter
systemctl enable --now prometheus-node-exporter
REMOTE
  fi

  echo "Installing host alert/fstrim scripts and webhook file (URL not printed)..."
  run rsync -a --chmod=755 obsv/host/thinpool-alert.sh \
    "${PVE_SSH}:/usr/local/sbin/thinpool-alert.sh"
  run rsync -a --chmod=755 obsv/host/lvm-textfile.sh \
    "${PVE_SSH}:/usr/local/sbin/lvm-textfile.sh"
  run rsync -a --chmod=755 obsv/host/ct100-disk-alert.sh \
    "${PVE_SSH}:/usr/local/sbin/ct100-disk-alert.sh"
  run rsync -a --chmod=755 obsv/host/guest-fstrim.sh \
    "${PVE_SSH}:/usr/local/sbin/guest-fstrim.sh"
  run rsync -a --chmod=755 scripts/qbit-ssd-cleanup.py \
    "${PVE_SSH}:/usr/local/sbin/qbit-ssd-cleanup.py"

  if [[ $DRY_RUN == true ]]; then
    echo "DRY: write ${PVE_SSH}:/etc/pve/priv/discord-webhook (0600)"
    echo "DRY: write /etc/cron.d/thinpool-alert"
    echo "DRY: write /etc/cron.d/lvm-textfile"
    echo "DRY: write /etc/cron.d/ct100-disk-alert"
    echo "DRY: write /etc/cron.d/guest-fstrim"
    echo "DRY: write /etc/cron.d/qbit-ssd-cleanup"
    echo "DRY: weekly guest-fstrim cron reclaims thin-pool blocks (LXC rootfs has no discard=on knob)"
  else
    # stdin to ssh so the URL never appears in the remote argv.
    ssh "$PVE_SSH" 'install -m 0600 /dev/null /etc/pve/priv/discord-webhook && cat > /etc/pve/priv/discord-webhook' \
      <<<"$DISCORD_WEBHOOK_URL"
    ssh "$PVE_SSH" 'printf "%s\n" "PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin" "*/15 * * * * root /usr/local/sbin/thinpool-alert.sh" > /etc/cron.d/thinpool-alert && chmod 644 /etc/cron.d/thinpool-alert'
    ssh "$PVE_SSH" 'printf "%s\n" "PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin" "* * * * * root /usr/local/sbin/lvm-textfile.sh" > /etc/cron.d/lvm-textfile && chmod 644 /etc/cron.d/lvm-textfile'
    ssh "$PVE_SSH" 'printf "%s\n" "PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin" "*/15 * * * * root /usr/local/sbin/ct100-disk-alert.sh" > /etc/cron.d/ct100-disk-alert && chmod 644 /etc/cron.d/ct100-disk-alert'
    ssh "$PVE_SSH" 'printf "%s\n" "PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin" "17 3 * * 0 root /usr/local/sbin/guest-fstrim.sh" > /etc/cron.d/guest-fstrim && chmod 644 /etc/cron.d/guest-fstrim'
    ssh "$PVE_SSH" 'printf "%s\n" "PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin" "*/20 * * * * root pct exec 100 -- python3 /usr/local/sbin/qbit-ssd-cleanup.py" > /etc/cron.d/qbit-ssd-cleanup && chmod 644 /etc/cron.d/qbit-ssd-cleanup'
    ssh "$PVE_SSH" '/usr/local/sbin/lvm-textfile.sh'
    ssh "$PVE_SSH" 'mkdir -p /mnt/lxc_shares/metrion/config/scripts'
    run rsync -a --chmod=755 scripts/qbit-ssd-cleanup.py \
      "${PVE_SSH}:/mnt/lxc_shares/metrion/config/scripts/qbit-ssd-cleanup.py"
    pct "install -m 0755 /mnt/config/scripts/qbit-ssd-cleanup.py /usr/local/sbin/qbit-ssd-cleanup.py"
  fi

  echo "Installing homelab backup scripts and systemd units on pve..."
  if [[ $DRY_RUN == true ]]; then
    echo "DRY: rsync scripts/backup/* → ${PVE_SSH}:/root/homelab-backup/"
    echo "DRY: install homelab-backup.service homelab-backup-alert@.service homelab-backup.timer"
    echo "DRY: systemctl daemon-reload && systemctl enable --now homelab-backup.timer"
  else
    ssh "$PVE_SSH" 'mkdir -p /root/homelab-backup'
    run rsync -a --chmod=755 \
      scripts/backup/backup.sh \
      scripts/backup/backup-alert.sh \
      "${PVE_SSH}:/root/homelab-backup/"
    run rsync -a \
      scripts/backup/homelab-backup.service \
      scripts/backup/homelab-backup-alert@.service \
      scripts/backup/homelab-backup.timer \
      "${PVE_SSH}:/etc/systemd/system/"
    ssh "$PVE_SSH" 'systemctl daemon-reload && systemctl enable homelab-backup.timer && systemctl start homelab-backup.timer && systemctl is-active homelab-backup.timer'
  fi

  echo "Create a PVEAuditor API token and put PVE_TOKEN_VALUE in obsv/.env — see docs/observability/layer1.md"
  echo "Proxmox notification matcher and TrueNAS Slack-compat webhook are still one-time UI/pvesh steps — see docs/observability/layer1.md"
}

########################################
# adguard → CT 102 filter lists
########################################

deploy_adguard() {
  echo "==> adguard"
  need_file adguard/filters.yaml
  need_file adguard/apply-filters.py

  echo "Validating filters.yaml locally..."
  if [[ $DRY_RUN == false ]]; then
    python3 adguard/apply-filters.py \
      --desired adguard/filters.yaml \
      --dry-run >/dev/null
  else
    echo "DRY: python3 adguard/apply-filters.py --desired adguard/filters.yaml --dry-run"
  fi

  echo "Syncing filter config to CT ${ADGUARD_CTID}..."
  ssh_pve "mkdir -p /tmp/adguard-deploy"
  run rsync -a adguard/filters.yaml adguard/apply-filters.py adguard/dns-rewrites.yaml adguard/apply-dns-rewrites.py \
    "${PVE_SSH}:/tmp/adguard-deploy/"

  if [[ $DRY_RUN == true ]]; then
    echo "DRY: pct push ${ADGUARD_CTID} /tmp/adguard-deploy/filters.yaml ${ADGUARD_DIR}/filters.yaml"
    echo "DRY: pct push ${ADGUARD_CTID} /tmp/adguard-deploy/apply-dns-rewrites.py /usr/local/sbin/adguard-apply-dns-rewrites.py"
    echo "DRY: stop AdGuardHome, apply filters, check-config, start AdGuardHome"
    return 0
  fi

  ssh "$PVE_SSH" "pct push ${ADGUARD_CTID} /tmp/adguard-deploy/filters.yaml ${ADGUARD_DIR}/filters.yaml"
  ssh "$PVE_SSH" "pct push ${ADGUARD_CTID} /tmp/adguard-deploy/apply-filters.py /usr/local/sbin/adguard-apply-filters.py"
  ssh "$PVE_SSH" "pct push ${ADGUARD_CTID} /tmp/adguard-deploy/dns-rewrites.yaml ${ADGUARD_DIR}/dns-rewrites.yaml"
  ssh "$PVE_SSH" "pct push ${ADGUARD_CTID} /tmp/adguard-deploy/apply-dns-rewrites.py /usr/local/sbin/adguard-apply-dns-rewrites.py"
  ssh "$PVE_SSH" "pct exec ${ADGUARD_CTID} -- chmod 644 ${ADGUARD_DIR}/filters.yaml ${ADGUARD_DIR}/dns-rewrites.yaml"
  ssh "$PVE_SSH" "pct exec ${ADGUARD_CTID} -- chmod 755 /usr/local/sbin/adguard-apply-filters.py /usr/local/sbin/adguard-apply-dns-rewrites.py"

  echo "Stopping AdGuard Home briefly to apply filter lists (DNS/DHCP blip)..."
  remote_cmd=$(cat <<EOF
set -euo pipefail
CFG=${ADGUARD_DIR}/AdGuardHome.yaml
BAK=${ADGUARD_DIR}/AdGuardHome.yaml.bak-deploy
systemctl stop AdGuardHome
# Always snapshot the live config first. The apply script only refreshes its
# own backup when filters change, so a later failure could restore a stale file.
cp -a "\$CFG" "\$BAK"
cleanup() {
  local rc=\$?
  echo "adguard deploy failed; restoring \$BAK and starting AdGuardHome" >&2
  cp -a "\$BAK" "\$CFG" || true
  systemctl start AdGuardHome || true
  exit "\$rc"
}
trap cleanup EXIT
python3 /usr/local/sbin/adguard-apply-filters.py --desired ${ADGUARD_DIR}/filters.yaml --config "\$CFG" --backup "\$BAK"
python3 /usr/local/sbin/adguard-apply-dns-rewrites.py --desired ${ADGUARD_DIR}/dns-rewrites.yaml --config "\$CFG"
${ADGUARD_DIR}/AdGuardHome -c "\$CFG" -w ${ADGUARD_DIR} --check-config
trap - EXIT
systemctl start AdGuardHome
systemctl is-active AdGuardHome
EOF
)
  ssh "$PVE_SSH" "pct exec ${ADGUARD_CTID} -- bash -lc $(printf '%q' "$remote_cmd")"
}

prepare_secrets

for target in "${TARGETS[@]}"; do
  case "$target" in
    obsv) deploy_obsv ;;
    identity) deploy_identity ;;
    jellyfin) deploy_jellyfin ;;
    arrstack) deploy_arrstack ;;
    pve) deploy_pve ;;
    adguard) deploy_adguard ;;
  esac
done

echo
if [[ $DRY_RUN == true ]]; then
  echo "Dry run only. Nothing was copied or started."
else
  echo "Done: ${TARGETS[*]}"
fi
