#!/usr/bin/env python3
"""Apply Jellyfin bootstrap on CT 104 (or locally)."""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from bootstrap.jellyfin import apply  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Apply jellyfin/bootstrap.yml")
    parser.add_argument(
        "--env-file",
        type=Path,
        default=ROOT / "jellyfin" / ".env",
    )
    parser.add_argument(
        "--identity-env-file",
        type=Path,
        default=ROOT / "identity" / ".env",
    )
    parser.add_argument("--pve-ssh", default="root@192.168.1.200")
    parser.add_argument("--ct", type=int, default=104)
    parser.add_argument(
        "--compose-dir",
        default="/mnt/config/jellyfin",
        help="Path inside CT 104",
    )
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--local",
        action="store_true",
        help="Run against jellyfin/.env on this host (CT 104)",
    )
    args = parser.parse_args()

    if args.local:
        return apply(
            args.env_file,
            identity_env_file=args.identity_env_file,
            dry_run=args.dry_run,
        )

    remote_cmd = (
        f"python3 /mnt/config/scripts/apply_jellyfin_bootstrap.py --local "
        f"--env-file {args.compose_dir}/.env "
        f"--identity-env-file /mnt/config/identity/.env"
    )
    if args.dry_run:
        print(f"DRY: pct exec {args.ct} -- {remote_cmd}")
        return 0
    ssh_cmd = [
        "ssh",
        args.pve_ssh,
        "pct",
        "exec",
        str(args.ct),
        "--",
        "bash",
        "-lc",
        remote_cmd,
    ]
    return subprocess.call(ssh_cmd)


if __name__ == "__main__":
    raise SystemExit(main())
