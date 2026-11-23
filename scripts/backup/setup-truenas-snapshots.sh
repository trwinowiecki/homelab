#!/usr/bin/env bash
#
# Idempotent TrueNAS setup for backups: NFS export, restic directory, ZFS
# snapshot ladder (media excluded), and a nightly config tarball into staging/.
# Run from a host that can SSH as taylor@<truenas-host> with passwordless sudo.
#
# Snapshot tasks are declared in truenas-snapshots.yaml and reconciled (create,
# update, delete) rather than "create if missing".
#
# Opt-in reclaim of space held by existing media snapshots:
#   ./scripts/backup/setup-truenas-snapshots.sh --purge-media-snapshots
# That dry-runs zfs destroy -nv, then requires typing YES (or pass --yes).
# It is never default. Destroys every snapshot under metrion/zinthos/media.
#
set -euo pipefail

SSH_HOST=${SSH_HOST:-taylor@<truenas-host>}
ROOT=$(cd "$(dirname "$0")/../.." && pwd)
PY="$ROOT/scripts/backup/truenas_snapshots.py"
YAML="$ROOT/scripts/backup/truenas-snapshots.yaml"
REMOTE_PY=/tmp/homelab-truenas_snapshots.py
REMOTE_JSON=/tmp/homelab-truenas-snapshots.json

PURGE=false
PURGE_YES=false
for arg in "$@"; do
  case "$arg" in
    --purge-media-snapshots) PURGE=true ;;
    --yes) PURGE_YES=true ;;
    *)
      echo "Usage: $0 [--purge-media-snapshots] [--yes]" >&2
      exit 2
      ;;
  esac
done

ssh_truenas() {
  ssh -o BatchMode=yes "$SSH_HOST" "$@"
}

echo "==> enable NFS share + nightly config export"
ssh_truenas 'sudo bash -s' << 'EOF'
set -euo pipefail

echo "==> enable NFS share for metrion/zinthos/backups"
midclt call sharing.nfs.update 1 '{"enabled": true}' >/dev/null

echo "==> nightly TrueNAS config export into backups/staging"
python3 - << 'PY'
import json, subprocess

def call(method, payload=None):
    cmd = ["midclt", "call", method]
    if payload is not None:
        cmd.append(json.dumps(payload))
    out = subprocess.check_output(cmd, text=True)
    return json.loads(out) if out.strip() else None

jobs = call("cronjob.query") or []
if any(j.get("comment") == "homelab-truenas-config-export" or
       j.get("description") == "homelab-truenas-config-export" for j in jobs):
    print("cron already present")
else:
    payload = {
        "enabled": True,
        "schedule": {"minute": "15", "hour": "3", "dom": "*", "month": "*", "dow": "*"},
        "user": "root",
        "command": "mkdir -p /mnt/metrion/zinthos/backups/staging && tar -C /data -czf /mnt/metrion/zinthos/backups/staging/truenas-config.tar.gz freenas-v1.db pwenc_secret",
        "description": "homelab-truenas-config-export",
        "stderr": False,
        "stdout": False,
    }
    try:
        call("cronjob.create", payload)
        print("created cronjob")
    except subprocess.CalledProcessError:
        payload.pop("description", None)
        payload["comment"] = "homelab-truenas-config-export"
        try:
            call("cronjob.create", payload)
            print("created cronjob (comment)")
        except subprocess.CalledProcessError:
            print("WARN: cronjob.create failed; run the tar by hand", file=__import__("sys").stderr)
            raise
PY

echo "==> first config export now"
mkdir -p /mnt/metrion/zinthos/backups/staging /mnt/metrion/zinthos/backups/restic
tar -C /data -czf /mnt/metrion/zinthos/backups/staging/truenas-config.tar.gz freenas-v1.db pwenc_secret
chmod 750 /mnt/metrion/zinthos/backups/restic /mnt/metrion/zinthos/backups/staging
echo "done"
EOF

echo "==> reconcile snapshot tasks from truenas-snapshots.yaml"
python3 "$PY" --config "$YAML" --dump-json | ssh_truenas "cat > $REMOTE_JSON"
ssh_truenas "cat > $REMOTE_PY" < "$PY"
ssh_truenas "sudo python3 $REMOTE_PY --apply --config-json $REMOTE_JSON"

if [[ $PURGE == true ]]; then
  echo "==> dry-run destroy of metrion/zinthos/media@auto-* snapshots"
  ssh_truenas "sudo python3 $REMOTE_PY --purge-media-snapshots --dry-run --config-json $REMOTE_JSON"
  if [[ $PURGE_YES == true ]]; then
    confirm=YES
  else
    echo
    echo "Type YES to destroy those snapshots (not undoable):"
    if [[ -r /dev/tty ]]; then
      read -r confirm < /dev/tty
    else
      echo "ERROR: purge requires a tty or --yes" >&2
      exit 1
    fi
  fi
  if [[ $confirm != YES ]]; then
    echo "aborted"
    exit 1
  fi
  ssh_truenas "sudo python3 $REMOTE_PY --purge-media-snapshots --yes --config-json $REMOTE_JSON"
fi
