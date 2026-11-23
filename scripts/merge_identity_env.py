#!/usr/bin/env python3
"""Merge identity/.env with identity/.env.example and fill missing secrets.

Priority for each key:
  1. Existing value in identity/.env (if not a placeholder)
  2. Default from identity/.env.example
  3. Auto-generate Authentik bootstrap secrets and OAuth/LDAP tokens

Does not overwrite real secrets. Creates a timestamped .env.bak before --write.
"""
from __future__ import annotations

import argparse
import base64
import re
import secrets
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from arrstack_api_keys import bare_value, parse_env_assignments  # noqa: E402
from identity_oauth import TOKEN_ENV_VARS, generate_secret, is_placeholder  # noqa: E402

IDENTITY_DIR = ROOT / "identity"
DEFAULT_ENV = IDENTITY_DIR / ".env"
DEFAULT_EXAMPLE = IDENTITY_DIR / ".env.example"

AUTO_GENERATE_BASE64 = frozenset(
    {
        "AUTHENTIK_SECRET_KEY",
        "AUTHENTIK_POSTGRESQL__PASSWORD",
        "AUTHENTIK_BOOTSTRAP_PASSWORD",
    }
)
AUTO_GENERATE_TOKEN = frozenset(TOKEN_ENV_VARS)


def generate_base64_secret(nbytes: int = 27) -> str:
    return base64.b64encode(secrets.token_bytes(nbytes)).decode()


def format_value(value: str) -> str:
    cleaned = bare_value(value)
    if " " in cleaned:
        return f'"{cleaned}"'
    return cleaned


def resolve_value(name: str, current: str, example_default: str) -> tuple[str, str]:
    if current and not is_placeholder(current):
        return current, "kept"
    if example_default and not is_placeholder(example_default):
        return bare_value(example_default), "example"
    if name in AUTO_GENERATE_BASE64:
        return generate_base64_secret(), "generated"
    if name in AUTO_GENERATE_TOKEN:
        return generate_secret(), "generated"
    return current or example_default, "unchanged"


def merge_lines(
    example_text: str,
    current: dict[str, str],
) -> tuple[list[str], list[tuple[str, str, str]]]:
    lines: list[str] = []
    changes: list[tuple[str, str, str]] = []
    seen: set[str] = set()

    for line in example_text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            lines.append(line)
            continue
        m = re.match(r"^([A-Za-z_][A-Za-z0-9_]*)=(.*)$", stripped)
        if not m:
            lines.append(line)
            continue

        name, example_default = m.group(1), m.group(2)
        seen.add(name)
        cur = current.get(name, "")
        value, source = resolve_value(name, cur, example_default)
        if source in {"generated", "example"} or bare_value(cur) != bare_value(value):
            changes.append((name, source, value))

        prefix = line[: line.index(name)]
        lines.append(f"{prefix}{name}={format_value(value)}")

    extras = [k for k in sorted(current) if k not in seen]
    if extras:
        lines.append("")
        lines.append("# --- keys present in .env but not in .env.example ---")
        for name in extras:
            lines.append(f"{name}={current[name]}")

    return lines, changes


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Merge identity/.env with identity/.env.example.",
    )
    parser.add_argument(
        "--env-file",
        type=Path,
        default=DEFAULT_ENV,
        help="identity/.env to update (default: identity/.env)",
    )
    parser.add_argument(
        "--example",
        type=Path,
        default=DEFAULT_EXAMPLE,
        help="Template (default: identity/.env.example)",
    )
    parser.add_argument(
        "--write",
        action="store_true",
        help="Write merged identity/.env (creates timestamped .env.bak first)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print summary only (default if --write not set)",
    )
    args = parser.parse_args()

    env_path = args.env_file.resolve()
    example_path = args.example.resolve()
    if not example_path.is_file():
        print(f"ERROR: {example_path} not found", file=sys.stderr)
        return 1

    current: dict[str, str] = {}
    if env_path.is_file():
        current = parse_env_assignments(env_path.read_text())

    example_text = example_path.read_text()
    lines, changes = merge_lines(example_text, current)
    merged_text = "\n".join(lines) + "\n"

    if env_path.is_file():
        existing_text = env_path.read_text()
        if existing_text == merged_text:
            print("No changes needed — identity/.env already matches example with all keys set.")
            return 0
    elif not changes:
        print("No changes needed — identity/.env already matches example with all keys set.")
        return 0

    if not changes:
        print("Reordering identity/.env to match identity/.env.example (values unchanged).")

    print(f"{'KEY':<40} {'SOURCE':<10} ACTION")
    print("-" * 70)
    for name, source, value in changes:
        display = value if len(value) < 20 else f"{value[:8]}…{value[-4:]}"
        print(f"{name:<40} {source:<10} {display}")

    if args.dry_run or not args.write:
        print()
        print("Dry run — no files written. Re-run with --write to apply.")
        print(f"Would write {len(lines)} lines to {env_path}")
        return 0

    if env_path.is_file():
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        backup = env_path.with_name(f".env.bak.{stamp}")
        shutil.copy2(env_path, backup)
        print(f"Backup: {backup}")

    env_path.parent.mkdir(parents=True, exist_ok=True)
    env_path.write_text(merged_text)
    print(f"Updated {env_path}")
    print()
    print("Next: ./deploy.sh identity")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
