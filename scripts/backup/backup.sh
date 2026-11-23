#!/usr/bin/env bash
#
# Daily restic of irreplaceable homelab state. Runs on pve as root.
# Does not dump Docker overlay2, torrent incompletes, or the media library.
#
set -euo pipefail

ENV_FILE=${ENV_FILE:-/root/homelab-backup/restic.env}
TEXTFILE_DIR=${TEXTFILE_DIR:-/var/lib/prometheus/node-exporter}
BACKUP_START_EPOCH="$(date +%s)"

if [[ ! -f $ENV_FILE ]]; then
  echo "ERROR: $ENV_FILE not found" >&2
  exit 1
fi
# shellcheck disable=SC1090
set -a
source "$ENV_FILE"
set +a

if [[ ! -f ${RESTIC_PASSWORD_FILE:?} ]]; then
  echo "ERROR: RESTIC_PASSWORD_FILE missing: $RESTIC_PASSWORD_FILE" >&2
  exit 1
fi
if [[ ! -d ${RESTIC_REPOSITORY:?} ]]; then
  echo "ERROR: restic repo not mounted: $RESTIC_REPOSITORY" >&2
  exit 1
fi

lxc_root() {
  local pid
  pid=$(lxc-info -n "$1" -H -p 2>/dev/null || true)
  if [[ -z $pid || $pid == -1 ]]; then
    echo "WARN: CT $1 is not running; skipping its paths" >&2
    return 1
  fi
  echo "/proc/${pid}/root"
}

restic_ok() {
  # restic exits 3 when some files were unreadable; still a usable snapshot.
  local rc=0
  restic backup "$@" || rc=$?
  if [[ $rc -eq 0 || $rc -eq 3 ]]; then
    return 0
  fi
  return "$rc"
}

write_textfile_metrics() {
  local pangolin_ok="$1"
  local end_epoch duration tmp out
  end_epoch="$(date +%s)"
  duration=$(( end_epoch - BACKUP_START_EPOCH ))
  mkdir -p "$TEXTFILE_DIR"
  out="${TEXTFILE_DIR}/homelab-backup.prom"
  tmp="${out}.$$"
  {
    echo "# HELP homelab_backup_last_success_timestamp_seconds Unix time of last successful backup"
    echo "# TYPE homelab_backup_last_success_timestamp_seconds gauge"
    echo "homelab_backup_last_success_timestamp_seconds ${end_epoch}"
    echo "# HELP homelab_backup_duration_seconds Wall-clock seconds for the last successful backup run"
    echo "# TYPE homelab_backup_duration_seconds gauge"
    echo "homelab_backup_duration_seconds ${duration}"
    echo "# HELP homelab_backup_pangolin_ok 1 if the optional Pangolin VPS leg succeeded, else 0"
    echo "# TYPE homelab_backup_pangolin_ok gauge"
    echo "homelab_backup_pangolin_ok ${pangolin_ok}"
  } > "$tmp"
  mv "$tmp" "$out"
}

echo "==> $(date -Is) homelab backup starting"

# `|| true` so a stopped guest does not abort the rest of the run under set -e.
ct100=$(lxc_root 100 || true)
ct102=$(lxc_root 102 || true)
ct103=$(lxc_root 103 || true)
ct104=$(lxc_root 104 || true)

if [[ -n $ct100 && -d ${ct100}/arrstack/config ]]; then
  restic_ok --one-file-system \
    --tag arrstack \
    --exclude tdarr-node \
    "${ct100}/arrstack/config"
fi

# Authentik Postgres is on the CT 100 SSD, outside /arrstack/config. A live
# data-directory copy is inconsistent, so dump it. Caddy and media files are
# copied as-is; redis is cache.
if [[ -n $ct100 ]]; then
  ident_sql=$(mktemp)
  if pct exec 100 -- docker exec -u postgres authentik-postgresql \
      pg_dump --no-owner --no-acl authentik >"$ident_sql"; then
    restic_ok --stdin --stdin-filename authentik.sql --tag identity <"$ident_sql" \
      || echo "WARN: authentik dump was not stored" >&2
  else
    echo "WARN: authentik pg_dump failed; identity database not in this snapshot" >&2
  fi
  rm -f "$ident_sql"
  if [[ -d ${ct100}/arrstack/identity ]]; then
    restic_ok --one-file-system \
      --tag identity-files \
      --exclude postgresql \
      --exclude redis \
      "${ct100}/arrstack/identity"
  fi
fi

# Docker Jellyfin on CT 104 (production). Native CT 101 was destroyed 2026-09-29.
if [[ -n $ct104 && -d ${ct104}/arrstack/jellyfin/config ]]; then
  restic_ok --one-file-system \
    --tag jellyfin-docker \
    --exclude transcodes \
    --exclude cache \
    "${ct104}/arrstack/jellyfin/config"
fi

if [[ -n $ct102 ]]; then
  restic_ok --one-file-system \
    --tag adguard \
    "${ct102}/opt/AdGuardHome/AdGuardHome.yaml" \
    "${ct102}/opt/AdGuardHome/data"
fi

if [[ -n $ct103 ]]; then
  restic_ok --one-file-system \
    --tag newt \
    "${ct103}/opt/pangolin-site"
fi

restic_ok --one-file-system \
  --tag pve \
  /etc/pve \
  /etc/fstab \
  /etc/network/interfaces

if [[ -d /mnt/lxc_shares/metrion/config ]]; then
  restic_ok --one-file-system \
    --tag compose \
    --exclude taylor \
    /mnt/lxc_shares/metrion/config
fi

if [[ -d /mnt/lxc_shares/metrion/backups/staging ]]; then
  restic_ok \
    --tag truenas-config \
    /mnt/lxc_shares/metrion/backups/staging
fi

PANGOLIN_OK=0
# Pangolin VPS is optional: pve may not have a key. Skip rather than fail the job.
if ssh -o BatchMode=yes -o ConnectTimeout=8 -o StrictHostKeyChecking=accept-new \
     <pangolin-vps> true 2>/dev/null; then
  pangolin_rc=0
  ssh -o BatchMode=yes -o ConnectTimeout=15 <pangolin-vps> \
    'tar -C /opt/pangolin -cf - config' \
    | restic backup --stdin --stdin-filename pangolin-config.tar --tag pangolin \
    || pangolin_rc=$?
  if [[ $pangolin_rc -eq 0 || $pangolin_rc -eq 3 ]]; then
    PANGOLIN_OK=1
  else
    echo "WARN: Pangolin VPS backup failed with exit code ${pangolin_rc}" >&2
  fi
else
  echo "WARN: cannot SSH to Pangolin VPS; skipping /opt/pangolin/config"
fi

echo "==> forget + prune"
restic forget --keep-daily 7 --keep-weekly 4 --keep-monthly 6 --prune

echo "==> restic check (structural)"
restic check

write_textfile_metrics "$PANGOLIN_OK"

echo "==> $(date -Is) homelab backup finished"
restic snapshots --compact
