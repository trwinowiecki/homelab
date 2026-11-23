#!/usr/bin/env python3
"""Apply rendered Authentik blueprints on CT 100 (or local identity compose)."""

from __future__ import annotations

import argparse
import shlex
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

BLUEPRINT_ORDER = (
    "homelab/blueprint-groups.yaml",
    "homelab/blueprint-flow-ldap.yaml",
    "homelab/blueprint-flow-recovery.yaml",
    "homelab/blueprint-flow-enrollment.yaml",
    "homelab/blueprint-flow-login.yaml",
    "homelab/blueprint-events.yaml",
    "homelab/blueprint-events-discord.yaml",
    "homelab/blueprint-proxy.yaml",
    "homelab/blueprint-oidc.yaml",
    "homelab/blueprint-ldap.yaml",
    "homelab/blueprint-pangolin.yaml",
)

OPTIONAL_BLUEPRINTS = frozenset(
    {
        "homelab/blueprint-pangolin.yaml",
        "homelab/blueprint-events-discord.yaml",
    }
)


def blueprint_files(compose_dir: Path | None = None) -> tuple[str, ...]:
    """Return blueprints to apply; skip optional files that were not rendered."""
    authentik_dir = (compose_dir / "authentik") if compose_dir is not None else (
        ROOT / "identity" / "authentik"
    )
    order = []
    for bp in BLUEPRINT_ORDER:
        name = Path(bp).name
        if bp in OPTIONAL_BLUEPRINTS and not (authentik_dir / name).is_file():
            continue
        order.append(bp)
    return tuple(order)


def run(cmd: list[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
    print("+", " ".join(cmd))
    return subprocess.run(cmd, check=check, text=True, capture_output=True)


def apply_remote(pve_ssh: str, ct_id: str, compose_dir: str) -> int:
    blueprints = blueprint_files()
    apply_cmds = " && ".join(
        f"docker compose exec -T authentik-server ak apply_blueprint {bp}"
        for bp in blueprints
    )
    inner = (
        f"cd {compose_dir} && "
        "for i in $(seq 1 30); do "
        "docker compose exec -T authentik-server ak version >/dev/null 2>&1 && break; "
        "sleep 2; "
        "done && "
        f"{apply_cmds}"
    )
    result = run(
        ["ssh", pve_ssh, f"pct exec {ct_id} -- bash -lc {shlex.quote(inner)}"],
        check=False,
    )
    if result.stdout:
        print(result.stdout)
    if result.stderr:
        print(result.stderr, file=sys.stderr)
    return result.returncode


def apply_local(compose_dir: Path) -> int:
    for _ in range(30):
        probe = run(
            ["docker", "compose", "exec", "-T", "authentik-server", "ak", "version"],
            check=False,
        )
        if probe.returncode == 0:
            break
        time.sleep(2)
    else:
        print("ERROR: authentik-server not ready", file=sys.stderr)
        return 1

    for bp in blueprint_files(compose_dir):
        result = run(
            ["docker", "compose", "exec", "-T", "authentik-server", "ak", "apply_blueprint", bp],
            check=False,
        )
        if result.stdout:
            print(result.stdout[-2000:] if len(result.stdout) > 2000 else result.stdout)
        if result.stderr:
            print(result.stderr[-2000:] if len(result.stderr) > 2000 else result.stderr, file=sys.stderr)
        if result.returncode != 0:
            return result.returncode
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Apply homelab Authentik blueprints.")
    parser.add_argument("--local", action="store_true", help="Use local identity compose")
    parser.add_argument("--pve-ssh", default="root@192.168.1.200")
    parser.add_argument("--ct", default="100")
    parser.add_argument("--compose-dir", default="/mnt/config/identity")
    args = parser.parse_args()

    if args.local:
        return apply_local(ROOT / "identity")
    return apply_remote(args.pve_ssh, args.ct, args.compose_dir)


if __name__ == "__main__":
    raise SystemExit(main())
