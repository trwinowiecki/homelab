#!/usr/bin/env bash
#
# Push the scrubbed orphan tree at ../homelab-public to a NEW public GitHub repo.
# Never push the private gitops-refactor / pre-gitops-baseline history.
#
# Prerequisites:
#   gh auth login -h github.com -p ssh
#   ../homelab-public exists with the orphan commit (created during GitOps setup)
#
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PUBLIC="${PUBLIC_DIR:-$ROOT/../homelab-public}"
OWNER="${GITHUB_OWNER:-trwinowiecki}"
NAME="${GITHUB_REPO:-homelab}"

command -v gh >/dev/null || { echo "ERROR: gh not on PATH (nix develop / nix shell nixpkgs#gh)" >&2; exit 1; }
gh auth status -h github.com >/dev/null

[[ -d $PUBLIC/.git ]] || { echo "ERROR: missing $PUBLIC — rebuild the orphan tree first" >&2; exit 1; }

cd "$PUBLIC"
# Refuse if this tree somehow contains the private init commit
if git cat-file -e 6f81bfae569b1a16c2ada13f5be8bc0281460c23 2>/dev/null; then
  echo "ERROR: public tree contains private init commit — abort" >&2
  exit 1
fi

nix shell nixpkgs#gitleaks -c gitleaks detect --source . --no-git --no-banner

if gh repo view "$OWNER/$NAME" >/dev/null 2>&1; then
  echo "repo $OWNER/$NAME already exists"
else
  gh repo create "$OWNER/$NAME" --public --description "Homelab GitOps: media stack, identity, observability (SOPS secrets)" --disable-wiki
fi

git remote remove origin 2>/dev/null || true
git remote add origin "git@github.com:${OWNER}/${NAME}.git"
git push -u origin main

# Branch protection (best-effort; needs admin)
gh api -X PUT "repos/${OWNER}/${NAME}/branches/main/protection" \
  -H "Accept: application/vnd.github+json" \
  -f required_status_checks='{"strict":true,"contexts":["validate"]}' \
  -F enforce_admins=true \
  -F required_pull_request_reviews='{"required_approving_review_count":0}' \
  -F restrictions= \
  -F allow_force_pushes=false \
  -F allow_deletions=false \
  -F required_linear_history=true \
  -F required_signatures=true \
  || echo "WARN: branch protection API call failed — set it in the GitHub UI"

# Enable secret scanning / push protection (public repos get scanning; push protection may need UI)
gh api -X PATCH "repos/${OWNER}/${NAME}" \
  -f security_and_analysis='{"secret_scanning":{"status":"enabled"},"secret_scanning_push_protection":{"status":"enabled"}}' \
  || echo "WARN: could not enable secret scanning via API"

# Create homelab environment for deploy-now workflow
gh api -X PUT "repos/${OWNER}/${NAME}/environments/homelab" -f wait_timer=0 || true

echo
echo "Published: https://github.com/${OWNER}/${NAME}"
echo "Next: install Renovate GitHub App on the repo; fill infra/roles/deployer allowed_signers;"
echo "      set deployer_repo_url; rotate Cloudflare: ./scripts/rotate_cloudflare_token.sh"
