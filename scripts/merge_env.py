#!/usr/bin/env python3
"""Merge .env with .env.example and fill missing API keys/secrets automatically.

Priority for each key:
  1. Existing value in .env (if not a placeholder)
  2. Value from config volumes under FOLDER_FOR_DATA
  3. Value from arrstack.auto.env
  4. Auto-generate (for *arr API keys, Bindery, Mixarr secret, ABS password)

Does not auto-fill VPN, usenet, or indexer credentials.
"""
from __future__ import annotations

import argparse
import re
import secrets
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

sys.path.insert(0, str(ROOT / "scripts"))
from arrstack_api_keys import (  # noqa: E402
    KEY_SOURCES,
    PLACEHOLDER,
    auto_env_path,
    bare_value,
    generate_qbit_api_key,
    load_auto_env_assignments,
    load_env_folder_for_data,
    parse_env_assignments,
    parse_env_value,
    read_keys_from_data_dir,
)

# Keys we generate when still empty/placeholder after disk lookup.
AUTO_GENERATE_HEX16 = frozenset(
    {
        "SONARR_API_KEY",
        "RADARR_API_KEY",
        "LIDARR_API_KEY",
        "PROWLARR_API_KEY",
        "BAZARR_API_KEY",
        "BINDERY_API_KEY",
        "SEERR_API_KEY",
    }
)
AUTO_GENERATE_QBIT = frozenset({"QBITTORRENT_API_KEY"})
AUTO_GENERATE_HEX32 = frozenset({"MIXARR_SESSION_SECRET", "MEALIE_OG_SYNC_TOKEN"})
AUTO_GENERATE_PASSWORD = frozenset({"AUDIOBOOKSHELF_PASSWORD"})

# Pulled from disk only — never invented.
DISK_ONLY_KEYS = frozenset(KEY_SOURCES) - AUTO_GENERATE_HEX16


def is_placeholder(value: str) -> bool:
    return bare_value(value) in PLACEHOLDER


def format_value(value: str) -> str:
    if " #" in value:
        raise ValueError(f"refusing to write value with inline comment: {value!r}")
    cleaned = parse_env_value(value)
    if " " in cleaned:
        return f'"{cleaned}"'
    return cleaned


def generate_for(name: str) -> str:
    if name in AUTO_GENERATE_QBIT:
        return generate_qbit_api_key()
    if name in AUTO_GENERATE_HEX32:
        return secrets.token_hex(32)
    if name in AUTO_GENERATE_PASSWORD:
        return secrets.token_urlsafe(18)
    if name in AUTO_GENERATE_HEX16:
        return secrets.token_hex(16)
    return ""


def collect_disk_keys(env_file: Path, data_dir: Path | None) -> dict[str, str]:
    out: dict[str, str] = {}
    if data_dir and data_dir.is_dir():
        out.update(read_keys_from_data_dir(data_dir))
        out.update(load_auto_env_assignments(data_dir))
    return {k: v for k, v in out.items() if v and not is_placeholder(v)}


def resolve_value(
    name: str,
    current: str,
    disk: dict[str, str],
) -> tuple[str, str]:
    """Return (value, source) for one env var."""
    if current and not is_placeholder(current):
        return current, "kept"

    if name in disk and disk[name]:
        return disk[name], "disk"

    if name in AUTO_GENERATE_HEX16 | AUTO_GENERATE_HEX32 | AUTO_GENERATE_PASSWORD | AUTO_GENERATE_QBIT:
        return generate_for(name), "generated"

    if name in DISK_ONLY_KEYS:
        return current, "unchanged"

    return current, "unchanged"


def merge_lines(
    example_text: str,
    current: dict[str, str],
    disk: dict[str, str],
) -> tuple[list[str], list[tuple[str, str, str]]]:
    """Build merged file lines; return (lines, changes) where change is (key, source, value)."""
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
        cur = current.get(name, example_default)
        value, source = resolve_value(name, cur, disk)
        if source != "kept" and source != "unchanged":
            changes.append((name, source, value))
        elif bare_value(cur) != bare_value(value):
            changes.append((name, source, value))

        prefix = line[: line.index(name)]
        lines.append(f"{prefix}{name}={format_value(value)}")

    # Preserve extra keys from .env not in example (legacy vars).
    extras = [k for k in sorted(current) if k not in seen]
    if extras:
        lines.append("")
        lines.append("# --- keys present in .env but not in .env.example ---")
        for name in extras:
            lines.append(f"{name}={current[name]}")

    return lines, changes


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Merge .env with .env.example and auto-fill API keys.",
    )
    parser.add_argument(
        "--env-file",
        type=Path,
        default=ROOT / ".env",
        help="Your .env to update (default: ./.env)",
    )
    parser.add_argument(
        "--example",
        type=Path,
        default=ROOT / ".env.example",
        help="Template (default: ./.env.example)",
    )
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=None,
        help="FOLDER_FOR_DATA override for reading live keys",
    )
    parser.add_argument(
        "--import-auto-env",
        type=Path,
        default=None,
        help="Merge keys from arrstack.auto.env (e.g. pulled from CT 100)",
    )
    parser.add_argument(
        "--write",
        action="store_true",
        help="Write merged .env (creates timestamped .env.bak first)",
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
    if not env_path.is_file():
        print(f"WARN: {env_path} missing — will create from example")
        current: dict[str, str] = {}
    else:
        current = parse_env_assignments(env_path.read_text())

    data_dir = args.data_dir
    if data_dir is None and env_path.is_file():
        data_dir = load_env_folder_for_data(env_path)
    disk = collect_disk_keys(env_path, data_dir)
    if args.import_auto_env and args.import_auto_env.is_file():
        disk.update(
            {
                k: v
                for k, v in parse_env_assignments(args.import_auto_env.read_text()).items()
                if v and not is_placeholder(v)
            }
        )

    example_text = example_path.read_text()
    lines, changes = merge_lines(example_text, current, disk)

    if not changes:
        print("No changes needed — .env already matches example with all keys set.")
        return 0

    print(f"{'KEY':<28} {'SOURCE':<10} ACTION")
    print("-" * 60)
    for name, source, value in changes:
        display = value if len(value) < 20 else f"{value[:8]}…{value[-4:]}"
        print(f"{name:<28} {source:<10} {display}")

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

    env_path.write_text("\n".join(lines) + "\n")
    print(f"Updated {env_path}")

    from check_env import validate_env_file  # noqa: E402

    errors = validate_env_file(env_path)
    if errors:
        print("ERROR: merged .env failed validation:", file=sys.stderr)
        for err in errors:
            print(f"  {err}", file=sys.stderr)
        return 1

    print()
    print("Next: review VPN/usenet placeholders, then ./deploy.sh arrstack")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
