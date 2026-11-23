#!/usr/bin/env bash
# Discord pager when homelab-backup.service fails.
# Webhook: /etc/pve/priv/discord-webhook (mode 0600, single line URL).
set -u

WEBHOOK_FILE="${WEBHOOK_FILE:-/etc/pve/priv/discord-webhook}"
FAILED_UNIT="${1:-homelab-backup.service}"
JOURNAL_LINES="${JOURNAL_LINES:-12}"

discord() {
  local content="$1"
  local url
  url="$(tr -d '\r\n' < "$WEBHOOK_FILE" 2>/dev/null || true)"
  if [[ -z "$url" ]]; then
    echo "backup-alert: missing webhook file $WEBHOOK_FILE" >&2
    return 1
  fi
  curl -sS -o /dev/null -w "%{http_code}" -X POST "$url" \
    -H "Content-Type: application/json" \
    --data "$(printf '{"content":%s}' "$(python3 -c 'import json,sys; print(json.dumps(sys.argv[1]))' "$content")")" \
    || true
}

journal_tail() {
  journalctl -u "$FAILED_UNIT" -n "$JOURNAL_LINES" --no-pager 2>/dev/null \
    | tail -n "$JOURNAL_LINES" \
    | sed 's/`/'\''/g' \
    || true
}

tail_text="$(journal_tail)"
if [[ -n "$tail_text" ]]; then
  msg="**Backup failed:** \`${FAILED_UNIT}\`

\`\`\`
${tail_text}
\`\`\`"
else
  msg="**Backup failed:** \`${FAILED_UNIT}\` (no journal lines). Check \`systemctl status ${FAILED_UNIT}\` on pve."
fi

discord "$msg"
exit 0
