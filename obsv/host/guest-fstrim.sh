#!/usr/bin/bash
# Weekly fstrim for LXC guests on the pve thin pool.
set -euo pipefail

CTIDS=(100 104)
LOG="${LOG:-/var/log/guest-fstrim.log}"

{
  echo "=== $(date -Is) guest-fstrim ==="
  for id in "${CTIDS[@]}"; do
    if pct status "$id" 2>/dev/null | grep -q running; then
      echo "-- CT $id --"
      pct fstrim "$id" || echo "fstrim failed for CT $id"
    else
      echo "CT $id not running, skip"
    fi
  done
  echo "pool data%: $(lvs --noheadings -o data_percent pve/data 2>/dev/null | tr -d ' ')"
} >>"$LOG" 2>&1
