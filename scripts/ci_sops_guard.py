#!/usr/bin/env python3
"""CI guard: encrypted SOPS files must look encrypted; plaintext secret paths must not be tracked."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

FORBIDDEN = [
    ".env",
    "obsv/.env",
    "identity/.env",
    "jellyfin/.env",
    "pangolin/.env",
    "pangolin/site/.env",
    "secrets/site.env",
    "arrstack/configarr/secrets.yml",
    "arrstack/mealie-og-sync/credentials.env",
]


def tracked() -> set[str]:
    out = subprocess.check_output(["git", "ls-files"], cwd=ROOT, text=True)
    return {line.strip() for line in out.splitlines() if line.strip()}


def main() -> int:
    files = tracked()
    bad = [p for p in FORBIDDEN if p in files]
    if bad:
        print("ERROR: plaintext secret paths are tracked:", file=sys.stderr)
        for p in bad:
            print(f"  {p}", file=sys.stderr)
        return 1

    sops_files = sorted(p for p in files if ".sops." in p)
    if not sops_files:
        print("WARN: no *.sops.* files tracked yet")
    for rel in sops_files:
        text = (ROOT / rel).read_text(encoding="utf-8", errors="replace")
        if "sops:" not in text and "sops_mac" not in text and "ENC[" not in text:
            print(f"ERROR: {rel} does not look like a SOPS file", file=sys.stderr)
            return 1
        print(f"ok sops: {rel}")
    print("sops guard passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
