#!/usr/bin/bash
# CT 100 rootfs pager for pve. df inside the guest, not thin-pool Data%.
# Webhook: /etc/pve/priv/discord-webhook (mode 0600, single line URL).
set -u

WEBHOOK_FILE="${WEBHOOK_FILE:-/etc/pve/priv/discord-webhook}"
STATE_FILE="${STATE_FILE:-/var/lib/ct100-disk-alert.state}"
CTID="${CTID:-100}"
WARN_PCT="${WARN_PCT:-85}"
RECOVER_PCT="${RECOVER_PCT:-80}"
REMINDER_SECS="${REMINDER_SECS:-21600}"
DF_FAIL_SECS="${DF_FAIL_SECS:-3600}"

now_epoch() { date +%s; }

discord() {
  local content="$1"
  local url
  url="$(tr -d '\r\n' < "$WEBHOOK_FILE" 2>/dev/null || true)"
  if [[ -z "$url" ]]; then
    echo "ct100-disk-alert: missing webhook file $WEBHOOK_FILE" >&2
    return 1
  fi
  curl -sS -o /dev/null -w "%{http_code}" -X POST "$url" \
    -H "Content-Type: application/json" \
    --data "$(printf '{"content":%s}' "$(python3 -c 'import json,sys; print(json.dumps(sys.argv[1]))' "$content")")" \
    || true
}

read_state() {
  over=0
  last_remind=0
  last_df_fail=0
  # shellcheck disable=SC1090
  [[ -f "$STATE_FILE" ]] && . "$STATE_FILE"
}

write_state() {
  cat > "$STATE_FILE" <<EOF
over=$over
last_remind=$last_remind
last_df_fail=$last_df_fail
EOF
}

read_state

df_line="$(pct exec "$CTID" -- df -P / 2>/dev/null | awk 'NR==2 {print $2, $3, $4, $5}')"
if [[ -z "$df_line" ]]; then
  epoch="$(now_epoch)"
  if (( epoch - last_df_fail >= DF_FAIL_SECS )); then
    discord "**CT ${CTID} disk:** could not read \`df /\` inside the guest. Check arrstack."
    last_df_fail="$epoch"
    write_state
  fi
  exit 0
fi

read -r size_kb used_kb avail_kb use_pct <<<"$df_line"
use_pct="${use_pct%%%}"
epoch="$(now_epoch)"
last_df_fail=0

high=$(awk -v u="$use_pct" -v w="$WARN_PCT" 'BEGIN { print (u+0 >= w+0) ? 1 : 0 }')
ok=$(awk -v u="$use_pct" -v w="$RECOVER_PCT" 'BEGIN { print (u+0 < w+0) ? 1 : 0 }')

msg="**CT ${CTID} rootfs** \`/\` at ${use_pct}% (warn ${WARN_PCT}%). Used ${used_kb}K / ${size_kb}K, avail ${avail_kb}K. Thin-pool Data% can lag until \`pct fstrim ${CTID}\`."

if [[ "$high" == "1" ]]; then
  should_page=0
  if [[ "$over" != "1" ]]; then should_page=1; fi
  if [[ "$over" == "1" && $(( epoch - last_remind )) -ge REMINDER_SECS ]]; then should_page=1; fi
  if [[ "$should_page" == "1" ]]; then
    discord "$msg Check /arrstack/downloads and docker logs."
    last_remind="$epoch"
  fi
  over=1
else
  if [[ "$over" == "1" && "$ok" == "1" ]]; then
    discord "**CT ${CTID} rootfs recovered** at ${use_pct}%."
    over=0
    last_remind=0
  fi
fi

write_state
exit 0
