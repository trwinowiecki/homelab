#!/usr/bin/env bash
# Thin-pool-only textfile collector for node_exporter on pve.
# Writes atomically to /var/lib/prometheus/node-exporter/lvm.prom
set -eu
export LC_ALL=C

OUT_DIR="${OUT_DIR:-/var/lib/prometheus/node-exporter}"
OUT="${OUT_DIR}/lvm.prom"
TMP="${OUT}.$$"

mkdir -p "$OUT_DIR"

{
  echo "# HELP node_lvm_thin_pools_allocated percentage of allocated thin pool data"
  echo "# TYPE node_lvm_thin_pools_allocated gauge"
  echo "# HELP node_lvm_thin_pools_metadata percentage of allocated thin pool metadata"
  echo "# TYPE node_lvm_thin_pools_metadata gauge"

  "${LVS:-/usr/sbin/lvs}" --noheadings --separator=$'\t' --select 'lv_attr=~^t' --units b --nosuffix \
    --options lv_uuid,vg_name,data_percent,metadata_percent 2>/dev/null \
    | while IFS=$'\t' read -r uuid vg data meta; do
        uuid=${uuid// /}
        vg=${vg// /}
        data=${data// /}
        meta=${meta// /}
        [ -n "$uuid" ] || continue
        echo "node_lvm_thin_pools_allocated{uuid=\"${uuid}\", vgroup=\"${vg}\"} ${data}"
        echo "node_lvm_thin_pools_metadata{uuid=\"${uuid}\", vgroup=\"${vg}\"} ${meta}"
      done
} > "$TMP"

mv "$TMP" "$OUT"
