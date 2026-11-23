#!/usr/bin/env python3
"""Fail if high-signal decrypted secrets/site identifiers appear outside secret files."""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

SKIP_SUFFIXES = (".sops.env", ".sops.yml", ".sops.yaml", ".png", ".jpg", ".webp")
SKIP_NAMES = {
    ".env",
    "site.env",
    "credentials.env",
    "secrets.yml",
    "arrstack.oauth.env",
    "oauth.auto.env",
}
SKIP_NAME_PREFIXES = (".env.bak.",)
SKIP_NAME_SUBSTRINGS = (".bak.",)
SKIP_PARTS = {".git", ".age", "__pycache__", ".rendered"}
# Rendered from site templates — local only, gitignored
SKIP_RENDERED = {
    "adguard/dns-rewrites.yaml",
    "pangolin/site/blueprint.yaml",
    "obsv/scrape.yml",
    "obsv/library-exporter.yaml",
    "arrstack/homepage/services.yaml",
    "arrstack/seerr/bootstrap.yml",
    "jellyfin/bootstrap.yml",
    "identity/caddy/Caddyfile",
    "identity/authentik/blueprint-proxy.yaml",
    "identity/authentik/blueprint-oidc.yaml",
    "identity/authentik/blueprint-ldap.yaml",
    "identity/authentik/blueprint-pangolin.yaml",
    "identity/authentik/blueprint-events-discord.yaml",
}

SECRET_KEY_PARTS = (
    "PASSWORD",
    "TOKEN",
    "SECRET",
    "API_KEY",
    "PRIVATE",
    "WEBHOOK",
    "WIREGUARD",
    "CREDENTIAL",
)
SITE_KEYS = (
    "SITE_DOMAIN",
    "SITE_LAN_DOMAIN",
    "SITE_HOUSEHOLD_EMAIL_1",
    "SITE_HOUSEHOLD_EMAIL_2",
    "SITE_PANGOLIN_VPS_IP",
    "IDENTITY_LAN_DOMAIN",
    "IDENTITY_PUBLIC_DOMAIN",
)


def load_env(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    if not path.is_file():
        return out
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        raw = line.strip()
        if not raw or raw.startswith("#") or "=" not in raw:
            continue
        k, _, v = raw.partition("=")
        out[k.strip()] = v.strip().strip('"').strip("'")
    return out


def denylist() -> list[str]:
    values: list[str] = []
    for rel in (
        "secrets/site.env",
        ".env",
        "identity/.env",
        "obsv/.env",
        "jellyfin/.env",
        "pangolin/.env",
        "pangolin/site/.env",
    ):
        env = load_env(ROOT / rel)
        for key, val in env.items():
            if not val or len(val) < 8:
                continue
            upper = val.upper()
            if upper in {"TRUE", "FALSE", "UTC", "CHANGE_ME"} or "CHANGE_ME" in upper:
                continue
            if key in SITE_KEYS or any(p in key.upper() for p in SECRET_KEY_PARTS):
                values.append(val)
    return sorted(set(values), key=len, reverse=True)


def iter_files(staged: bool) -> list[Path]:
    if staged:
        out = subprocess.check_output(
            ["git", "diff", "--cached", "--name-only", "--diff-filter=ACMR"],
            cwd=ROOT,
            text=True,
        )
        return [ROOT / line for line in out.splitlines() if line]
    return [p for p in ROOT.rglob("*") if p.is_file()]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--staged", action="store_true")
    args = parser.parse_args()
    needles = denylist()
    if not needles:
        print("denylist empty (decrypt secrets first for a full check)")
        return 0
    hits: list[str] = []
    for path in iter_files(args.staged):
        rel = path.relative_to(ROOT).as_posix()
        if any(p in SKIP_PARTS for p in path.parts):
            continue
        if rel in SKIP_RENDERED or path.name in SKIP_NAMES or path.name.endswith(SKIP_SUFFIXES):
            continue
        if path.name.startswith(SKIP_NAME_PREFIXES) or any(s in path.name for s in SKIP_NAME_SUBSTRINGS):
            continue
        if ".sops." in path.name or path.name.endswith(".example"):
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for needle in needles:
            if needle in text:
                hits.append(f"{rel} contains denylisted value (len={len(needle)})")
                break
    if hits:
        print("ERROR: secret/site denylist violations:", file=sys.stderr)
        for h in hits:
            print(f"  {h}", file=sys.stderr)
        return 1
    print(f"ok: checked against {len(needles)} denylist values")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
