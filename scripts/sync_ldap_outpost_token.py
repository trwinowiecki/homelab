#!/usr/bin/env python3
"""Sync LDAP_OUTPOST_TOKEN from identity/.env to Authentik's outpost service account."""

from __future__ import annotations

import argparse
import shlex
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from identity_oauth import is_placeholder, load_env  # noqa: E402

SHELL_SCRIPT = """
from authentik.core.models import Token, User
from authentik.outposts.models import Outpost
import os

key = os.environ["LDAP_OUTPOST_TOKEN"]
o = Outpost.objects.filter(name="homelab-ldap-outpost").first()
if not o:
    print("skip: homelab-ldap-outpost not found")
else:
    user = User.objects.filter(username=o.user_identifier).first()
    if not user:
        raise SystemExit("outpost service account missing")
    Token.objects.update_or_create(
        identifier=o.token_identifier,
        defaults={"key": key, "intent": "api", "user": user},
    )
    print("ldap outpost token synced")
"""


def sync_remote(pve_ssh: str, ct_id: str, compose_dir: str, token: str) -> int:
    # Token goes through stdin into a mode-0600 env file. It must not appear
    # in ssh/pct/docker argv, where ps would show it.
    inner = (
        "umask 077 && tmp=$(mktemp) && trap 'rm -f \"$tmp\"' EXIT && cat > \"$tmp\" && "
        f"cd {shlex.quote(compose_dir)} && "
        "docker compose exec -T --env-file \"$tmp\" "
        "authentik-server ak shell -c "
        + shlex.quote(SHELL_SCRIPT.strip())
    )
    result = subprocess.run(
        ["ssh", pve_ssh, f"pct exec {ct_id} -- bash -lc {shlex.quote(inner)}"],
        input=f"LDAP_OUTPOST_TOKEN={token}\n",
        text=True,
        capture_output=True,
    )
    if result.stdout:
        print(result.stdout.strip())
    if result.stderr:
        # authentik shell logs to stderr; only fail on non-zero exit
        for line in result.stderr.splitlines():
            if "ERROR" in line or "Traceback" in line:
                print(line, file=sys.stderr)
    return result.returncode


def sync_local(compose_dir: Path, token: str) -> int:
    import os

    os.environ["LDAP_OUTPOST_TOKEN"] = token
    # Local path: run via docker compose exec
    result = subprocess.run(
        [
            "docker",
            "compose",
            "exec",
            "-T",
            "-e",
            "LDAP_OUTPOST_TOKEN",
            "authentik-server",
            "ak",
            "shell",
            "-c",
            SHELL_SCRIPT.strip(),
        ],
        cwd=compose_dir,
        text=True,
        capture_output=True,
    )
    if result.stdout:
        print(result.stdout.strip())
    return result.returncode


def main() -> int:
    parser = argparse.ArgumentParser(description="Sync LDAP outpost token into Authentik.")
    parser.add_argument("--identity-env", type=Path, default=ROOT / "identity" / ".env")
    parser.add_argument("--local", action="store_true")
    parser.add_argument("--pve-ssh", default="root@192.168.1.200")
    parser.add_argument("--ct", default="100")
    parser.add_argument("--compose-dir", default="/mnt/config/identity")
    args = parser.parse_args()

    token = load_env(args.identity_env).get("LDAP_OUTPOST_TOKEN", "")
    if is_placeholder(token):
        print("skip ldap outpost token sync: LDAP_OUTPOST_TOKEN unset")
        return 0

    if args.local:
        return sync_local(ROOT / "identity", token)
    return sync_remote(args.pve_ssh, args.ct, args.compose_dir, token)


if __name__ == "__main__":
    raise SystemExit(main())
