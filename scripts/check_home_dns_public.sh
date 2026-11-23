#!/usr/bin/env bash
# Fail if LAN split-horizon names still resolve publicly (Namecheap wildcard leak).
# After deleting the zone-wide * A at Namecheap, sonarr.<lan-domain> must be NXDOMAIN
# on public resolvers while jellyfin/auth/seerr/pangolin remain on the VPS.
set -euo pipefail

VPS_IP="${VPS_IP:-172.234.196.180}"
DOH="${DOH:-https://cloudflare-dns.com/dns-query}"

query_a() {
  local name=$1
  python3 - "$DOH" "$name" <<'PY'
import json, sys, urllib.parse, urllib.request
doh, name = sys.argv[1], sys.argv[2]
url = doh + "?" + urllib.parse.urlencode({"name": name, "type": "A"})
req = urllib.request.Request(url, headers={"Accept": "application/dns-json"})
with urllib.request.urlopen(req, timeout=10) as r:
    data = json.loads(r.read().decode())
status = data.get("Status")
answers = [a.get("data") for a in (data.get("Answer") or []) if a.get("type") == 1]
print(status)
print(" ".join(answers))
PY
}

fail=0

echo "==> Public DNS must NOT answer LAN hosts (expect NXDOMAIN / status 3)"
for name in sonarr.<lan-domain> seerr.<lan-domain> homepage.<lan-domain>; do
  mapfile -t out < <(query_a "$name")
  status=${out[0]:-}
  answers=${out[1]:-}
  if [[ $status == 3 ]] || [[ -z $answers ]]; then
    echo "  OK  $name → NXDOMAIN/empty (status=$status)"
  else
    echo "  FAIL $name → $answers (status=$status) — delete Namecheap * A / home records"
    fail=1
  fi
done

echo "==> Public hosts must still point at VPS ${VPS_IP}"
for name in pangolin.<domain> jellyfin.<domain> auth.<domain> seerr.<domain>; do
  mapfile -t out < <(query_a "$name")
  status=${out[0]:-}
  answers=${out[1]:-}
  if [[ $answers == *"$VPS_IP"* ]]; then
    echo "  OK  $name → $answers"
  else
    echo "  FAIL $name → '$answers' (status=$status) — add explicit A ${name%%.<domain>} → ${VPS_IP}"
    fail=1
  fi
done

if [[ $fail -ne 0 ]]; then
  echo
  echo "Namecheap Advanced DNS for <domain> should look like:"
  echo "  A  pangolin   ${VPS_IP}"
  echo "  A  jellyfin   ${VPS_IP}"
  echo "  A  auth       ${VPS_IP}"
  echo "  A  seerr      ${VPS_IP}"
  echo "  (delete Host=* and any home / *.home records)"
  exit 1
fi

echo "==> Public split-horizon hygiene OK"
