#!/usr/bin/bash
# LVM thin-pool pager for pve. Reads Data% / Meta% from lvs, not df.
# Webhook: /etc/pve/priv/discord-webhook (mode 0600, single line URL).
set -u

WEBHOOK_FILE="${WEBHOOK_FILE:-/etc/pve/priv/discord-webhook}"
STATE_FILE="${STATE_FILE:-/var/lib/thinpool-alert.state}"
VG="${VG:-pve}"
LV="${LV:-data}"

DATA_WARN="${DATA_WARN:-80}"
META_WARN="${META_WARN:-70}"
DATA_RECOVER="${DATA_RECOVER:-75}"
META_RECOVER="${META_RECOVER:-65}"
REMINDER_SECS="${REMINDER_SECS:-21600}"
LVS_FAIL_SECS="${LVS_FAIL_SECS:-3600}"

now_epoch() { date +%s; }

discord() {
  local content="$1"
  local url
  url="$(tr -d '\r\n' < "$WEBHOOK_FILE" 2>/dev/null || true)"
  if [[ -z "$url" ]]; then
    echo "thinpool-alert: missing webhook file $WEBHOOK_FILE" >&2
    return 1
  fi
  curl -sS -o /dev/null -w "%{http_code}" -X POST "$url" \
    -H "Content-Type: application/json" \
    --data "$(printf '{"content":%s}' "$(python3 -c 'import json,sys; print(json.dumps(sys.argv[1]))' "$content")")" \
    || true
}

read_state() {
  data_over=0
  meta_over=0
  last_remind=0
  last_lvs_fail=0
  # shellcheck disable=SC1090
  [[ -f "$STATE_FILE" ]] && . "$STATE_FILE"
}

write_state() {
  cat > "$STATE_FILE" <<EOF
data_over=$data_over
meta_over=$meta_over
last_remind=$last_remind
last_lvs_fail=$last_lvs_fail
EOF
}

read_state

lvs_out="$("${LVS:-/usr/sbin/lvs}" --noheadings --units g -o data_percent,metadata_percent "${VG}/${LV}" 2>/dev/null || true)"
if [[ -z "$lvs_out" ]]; then
  epoch="$(now_epoch)"
  if (( epoch - last_lvs_fail >= LVS_FAIL_SECS )); then
    discord "**pve thin-pool:** could not read pool \`${VG}/${LV}\` via lvs. Check LVM. This is not silence."
    last_lvs_fail="$epoch"
    write_state
  fi
  exit 0
fi

# lvs prints two numbers, possibly with spaces and a trailing g-less percent.
data_pct="$(echo "$lvs_out" | awk '{print $1}' | tr -d ' ')"
meta_pct="$(echo "$lvs_out" | awk '{print $2}' | tr -d ' ')"
epoch="$(now_epoch)"
last_lvs_fail=0

data_high=$(awk -v d="$data_pct" -v w="$DATA_WARN" 'BEGIN { print (d+0 >= w+0) ? 1 : 0 }')
meta_high=$(awk -v d="$meta_pct" -v w="$META_WARN" 'BEGIN { print (d+0 >= w+0) ? 1 : 0 }')
data_ok=$(awk -v d="$data_pct" -v w="$DATA_RECOVER" 'BEGIN { print (d+0 < w+0) ? 1 : 0 }')
meta_ok=$(awk -v d="$meta_pct" -v w="$META_RECOVER" 'BEGIN { print (d+0 < w+0) ? 1 : 0 }')

msg="**pve thin-pool** \`${VG}/${LV}\` data=${data_pct}% (warn ${DATA_WARN}%) metadata=${meta_pct}% (warn ${META_WARN}%). df does not see this. A full pool fails writes on every LXC."

if [[ "$data_high" == "1" || "$meta_high" == "1" ]]; then
  should_page=0
  if [[ "$data_over" != "1" && "$data_high" == "1" ]]; then should_page=1; fi
  if [[ "$meta_over" != "1" && "$meta_high" == "1" ]]; then should_page=1; fi
  if [[ "$data_over" == "1" || "$meta_over" == "1" ]]; then
    if (( epoch - last_remind >= REMINDER_SECS )); then should_page=1; fi
  fi
  if [[ "$should_page" == "1" ]]; then
    discord "$msg Do not autoextend from this script."
    last_remind="$epoch"
  fi
  [[ "$data_high" == "1" ]] && data_over=1
  [[ "$meta_high" == "1" ]] && meta_over=1
else
  if [[ "$data_over" == "1" && "$data_ok" == "1" ]] || [[ "$meta_over" == "1" && "$meta_ok" == "1" ]]; then
    if [[ "$data_ok" == "1" && "$meta_ok" == "1" ]]; then
      discord "**pve thin-pool recovered** \`${VG}/${LV}\` data=${data_pct}% metadata=${meta_pct}%."
      data_over=0
      meta_over=0
      last_remind=0
    fi
  fi
fi

write_state
exit 0
