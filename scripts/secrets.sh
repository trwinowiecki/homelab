#!/usr/bin/env bash
#
# Encrypt / decrypt / edit SOPS-managed secrets for this repo.
#
# Plaintext siblings are gitignored. Encrypted files (*.sops.env, *.sops.yaml)
# are committed. Age private keys live in .age/ (workstation) or
# /etc/homelab/age.key (deploy agent).
#
#   ./scripts/secrets.sh encrypt   # plaintext → *.sops.env
#   ./scripts/secrets.sh decrypt   # *.sops.env → plaintext (0600)
#   ./scripts/secrets.sh edit PATH # sops edit on an encrypted file
#   ./scripts/secrets.sh status    # list which secrets are present
#
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

export SOPS_AGE_KEY_FILE="${SOPS_AGE_KEY_FILE:-$ROOT/.age/workstation.key}"
if [[ ! -f $SOPS_AGE_KEY_FILE && -f /etc/homelab/age.key ]]; then
  export SOPS_AGE_KEY_FILE=/etc/homelab/age.key
fi

need_sops() {
  command -v sops >/dev/null || {
    echo "ERROR: sops not on PATH (try: nix develop, or nix shell nixpkgs#sops nixpkgs#age)" >&2
    exit 1
  }
}

# Pairs: plaintext_path|encrypted_path
SECRET_MAP=(
  "secrets/site.env|secrets/site.sops.env"
  ".env|.env.sops.env"
  "obsv/.env|obsv/.env.sops.env"
  "identity/.env|identity/.env.sops.env"
  "jellyfin/.env|jellyfin/.env.sops.env"
  "pangolin/.env|pangolin/.env.sops.env"
  "pangolin/site/.env|pangolin/site/.env.sops.env"
  "arrstack/configarr/secrets.yml|arrstack/configarr/secrets.sops.yml"
  "arrstack/mealie-og-sync/credentials.env|arrstack/mealie-og-sync/credentials.sops.env"
)

encrypt_one() {
  local plain=$1 enc=$2
  if [[ ! -f $plain ]]; then
    echo "skip encrypt (missing plaintext): $plain"
    return 0
  fi
  mkdir -p "$(dirname "$enc")"
  # sops encrypts in place to stdout when using --filename-override for rules
  local itype otype
  case "$enc" in
    *.sops.env) itype=dotenv; otype=dotenv ;;
    *.sops.yml|*.sops.yaml) itype=yaml; otype=yaml ;;
    *) itype=binary; otype=binary ;;
  esac
  sops --encrypt --input-type "$itype" --output-type "$otype" --filename-override "$enc" "$plain" >"${enc}.tmp"
  mv "${enc}.tmp" "$enc"
  echo "encrypted $plain → $enc"
}

decrypt_one() {
  local plain=$1 enc=$2
  if [[ ! -f $enc ]]; then
    echo "skip decrypt (missing encrypted): $enc"
    return 0
  fi
  mkdir -p "$(dirname "$plain")"
  umask 077
  local itype otype
  case "$enc" in
    *.sops.env) itype=dotenv; otype=dotenv ;;
    *.sops.yml|*.sops.yaml) itype=yaml; otype=yaml ;;
    *) itype=binary; otype=binary ;;
  esac
  sops --decrypt --input-type "$itype" --output-type "$otype" "$enc" >"${plain}.tmp"
  mv "${plain}.tmp" "$plain"
  chmod 600 "$plain"
  echo "decrypted $enc → $plain"
}

cmd_encrypt() {
  need_sops
  local pair plain enc
  for pair in "${SECRET_MAP[@]}"; do
    plain=${pair%%|*}
    enc=${pair##*|}
    encrypt_one "$plain" "$enc"
  done
}

cmd_decrypt() {
  need_sops
  if [[ ! -f $SOPS_AGE_KEY_FILE ]]; then
    echo "ERROR: age key not found at $SOPS_AGE_KEY_FILE" >&2
    exit 1
  fi
  local pair plain enc
  for pair in "${SECRET_MAP[@]}"; do
    plain=${pair%%|*}
    enc=${pair##*|}
    decrypt_one "$plain" "$enc"
  done
}

cmd_edit() {
  need_sops
  local path=${1:?usage: secrets.sh edit PATH.sops.env}
  sops "$path"
}

cmd_status() {
  local pair plain enc
  printf '%-50s %-10s %-10s\n' PATH PLAIN ENC
  for pair in "${SECRET_MAP[@]}"; do
    plain=${pair%%|*}
    enc=${pair##*|}
    printf '%-50s %-10s %-10s\n' \
      "$plain" \
      "$( [[ -f $plain ]] && echo yes || echo no )" \
      "$( [[ -f $enc ]] && echo yes || echo no )"
  done
  echo "SOPS_AGE_KEY_FILE=$SOPS_AGE_KEY_FILE ($( [[ -f $SOPS_AGE_KEY_FILE ]] && echo present || echo MISSING ))"
}

usage() {
  sed -n '2,14p' "$0" | sed 's/^# \?//'
  exit "${1:-0}"
}

case "${1:-}" in
  encrypt) cmd_encrypt ;;
  decrypt) cmd_decrypt ;;
  edit) shift; cmd_edit "$@" ;;
  status) cmd_status ;;
  -h|--help|"") usage 0 ;;
  *) echo "ERROR: unknown command: $1" >&2; usage 1 ;;
esac
