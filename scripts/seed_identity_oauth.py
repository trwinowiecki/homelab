#!/usr/bin/env python3
"""Generate Authentik OAuth/LDAP secrets in identity/.env when missing.

Prefer scripts/merge_identity_env.py — it reorders keys from .env.example and fills
placeholders. This script only upserts missing token keys in place (legacy helper).
"""

from __future__ import annotations

import argparse
import re
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from identity_oauth import TOKEN_ENV_VARS, generate_secret, is_placeholder, load_env  # noqa: E402


def upsert_env_lines(text: str, updates: dict[str, str]) -> str:
    lines = text.splitlines()
    seen: set[str] = set()
    out: list[str] = []
    for line in lines:
        m = re.match(r"^([A-Za-z_][A-Za-z0-9_]*)=(.*)$", line.strip())
        if m and m.group(1) in updates:
            key = m.group(1)
            prefix = line[: line.index(key)]
            out.append(f"{prefix}{key}={updates[key]}")
            seen.add(key)
        else:
            out.append(line)
    missing = [k for k in updates if k not in seen]
    if missing:
        if out and out[-1].strip():
            out.append("")
        out.append("# --- authentik OAuth/LDAP (seed_identity_oauth.py) ---")
        for key in missing:
            out.append(f"{key}={updates[key]}")
    return "\n".join(out) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="Seed identity OAuth/LDAP secrets.")
    parser.add_argument("--env-file", type=Path, default=ROOT / "identity" / ".env")
    parser.add_argument("--write", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    env_path = args.env_file.resolve()
    if not env_path.is_file():
        print(f"ERROR: {env_path} not found", file=sys.stderr)
        return 1

    current = load_env(env_path)
    updates: dict[str, str] = {}
    for key in TOKEN_ENV_VARS:
        val = current.get(key, "")
        if is_placeholder(val):
            updates[key] = generate_secret()

    if not updates:
        print("identity OAuth/LDAP secrets already set.")
        return 0

    print("Will generate:")
    for key in sorted(updates):
        print(f"  {key}")

    if args.dry_run or not args.write:
        print("Dry run — pass --write to update identity/.env")
        return 0

    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    backup = env_path.with_name(f".env.bak.{stamp}")
    shutil.copy2(env_path, backup)
    print(f"Backup: {backup}")

    new_text = upsert_env_lines(env_path.read_text(), updates)
    env_path.write_text(new_text)
    print(f"Updated {env_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
