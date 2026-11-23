#!/usr/bin/env bash
# Replace CLOUDFLARE_API_TOKEN in identity/.env (and re-encrypt with SOPS).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

ENV_FILE=identity/.env
[[ -f $ENV_FILE ]] || { echo "ERROR: missing $ENV_FILE — decrypt first: ./scripts/secrets.sh decrypt" >&2; exit 1; }

if [[ -n ${CLOUDFLARE_API_TOKEN:-} ]]; then
  token=$CLOUDFLARE_API_TOKEN
else
  printf 'New Cloudflare API token (input hidden): '
  stty -echo
  read -r token
  stty echo
  printf '\n'
fi

[[ -n $token && $token != *CHANGE_ME* ]] || { echo "ERROR: empty or placeholder token" >&2; exit 1; }

python3 - "$ENV_FILE" "$token" <<'PY'
import re, sys, shutil
from datetime import datetime, timezone
from pathlib import Path
path = Path(sys.argv[1])
token = sys.argv[2]
text = path.read_text()
stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
shutil.copy2(path, path.with_name(f".env.bak.{stamp}"))
if re.search(r"^CLOUDFLARE_API_TOKEN=", text, re.M):
    text = re.sub(r"^CLOUDFLARE_API_TOKEN=.*$", f"CLOUDFLARE_API_TOKEN={token}", text, count=1, flags=re.M)
else:
    text = text.rstrip() + f"\nCLOUDFLARE_API_TOKEN={token}\n"
path.write_text(text)
path.chmod(0o600)
print(f"updated {path}")
PY

if command -v sops >/dev/null && [[ -f ${SOPS_AGE_KEY_FILE:-.age/workstation.key} ]]; then
  ./scripts/secrets.sh encrypt
  echo "Re-encrypted identity/.env → identity/.env.sops.env"
else
  echo "WARN: sops/age unavailable — encrypt later with ./scripts/secrets.sh encrypt"
fi

echo "Next: ./deploy.sh identity   then delete the old token in Cloudflare."
