#!/usr/bin/env python3
"""Write API keys and Mixarr secret to ${FOLDER_FOR_DATA}/arrstack.auto.env."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from arrstack_api_keys import (  # noqa: E402
    auto_env_path,
    bare_value,
    build_auto_env_assignments,
    load_auto_env_assignments,
    load_env_folder_for_data,
    parse_env_assignments,
    write_auto_env_file,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Sync API keys from config volumes into arrstack.auto.env.",
    )
    parser.add_argument(
        "--env-file",
        type=Path,
        default=Path(".env"),
        help="Read FOLDER_FOR_DATA / PUID / PGID from this file (default: ./.env)",
    )
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=None,
        help="FOLDER_FOR_DATA override",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print changes without writing",
    )
    args = parser.parse_args()

    env_path = args.env_file.resolve()
    if not env_path.is_file():
        print(f"ERROR: {env_path} not found", file=sys.stderr)
        return 1

    base = parse_env_assignments(env_path.read_text())
    data_dir = args.data_dir or load_env_folder_for_data(env_path)
    if data_dir is None or not data_dir.is_dir():
        print(f"ERROR: FOLDER_FOR_DATA not found via {env_path}", file=sys.stderr)
        return 1

    output_path = auto_env_path(data_dir)
    existing = load_auto_env_assignments(data_dir)
    assignments, changed = build_auto_env_assignments(data_dir, existing, env_file=env_path)

    if not changed:
        print(f"{output_path} already up to date")
        return 0

    api_keys = [k for k in changed if k.endswith("_API_KEY")]
    if api_keys:
        print(f"Synced {len(api_keys)} API key(s) from {data_dir}")
    other = [k for k in changed if not k.endswith("_API_KEY")]
    if other:
        print(f"Updated: {', '.join(other)}")

    if args.dry_run:
        print(f"Would write {len(assignments)} var(s) to {output_path}")
        return 0

    puid = int(bare_value(base.get("PUID", "10000")) or "10000")
    pgid = int(bare_value(base.get("PGID", "10000")) or "10000")
    write_auto_env_file(output_path, assignments, uid=puid, gid=pgid)
    print(f"Updated {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
