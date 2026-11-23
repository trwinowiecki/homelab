#!/usr/bin/env python3
"""Sync Authentik OIDC URLs/secrets from identity/.env into app .env files."""

from __future__ import annotations

import argparse
import re
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from identity_oauth import (  # noqa: E402
    OAUTH_APPS,
    auth_base_url,
    internal_auth_base_docker,
    internal_auth_base_host,
    is_placeholder,
    issuer_url,
    load_env,
    well_known_url,
)


def upsert(lines: list[str], updates: dict[str, str]) -> list[str]:
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
    for key, val in updates.items():
        if key not in seen:
            out.append(f"{key}={val}")
    return out


def write_env(path: Path, updates: dict[str, str], *, write: bool, dry_run: bool) -> None:
    if not updates:
        return
    if not path.is_file():
        if not write or dry_run:
            print(f"would create {path}: {', '.join(sorted(updates))}")
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("")
        path.chmod(0o600)
        lines = []
    else:
        lines = path.read_text().splitlines()
    merged = upsert(lines, updates)
    print(f"{path.name}: {', '.join(sorted(updates))}")
    if dry_run or not write:
        return
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    backup = path.with_name(f".env.bak.{stamp}")
    shutil.copy2(path, backup)
    path.write_text("\n".join(merged) + "\n")


def mealie_updates(identity: dict[str, str], lan: str) -> dict[str, str]:
    secret = identity.get("AUTHENTIK_OIDC_MEALIE_CLIENT_SECRET", "")
    if is_placeholder(secret):
        return {}
    slug = OAUTH_APPS["mealie"]["authentik_slug"]
    return {
        "MEALIE_OIDC_ENABLED": "true",
        "MEALIE_OIDC_CONFIGURATION_URL": well_known_url(lan, slug),
        "MEALIE_OIDC_CLIENT_ID": OAUTH_APPS["mealie"]["client_id"],
        "MEALIE_OIDC_CLIENT_SECRET": secret,
    }


def grafana_updates(identity: dict[str, str], lan: str, arr: dict[str, str]) -> dict[str, str]:
    secret = identity.get("AUTHENTIK_OIDC_GRAFANA_CLIENT_SECRET", "")
    if is_placeholder(secret):
        return {}
    slug = OAUTH_APPS["grafana"]["authentik_slug"]
    base = auth_base_url(lan)
    host_ip = identity.get("LOCAL_DOCKER_IP") or arr.get("LOCAL_DOCKER_IP", "")
    if not host_ip:
        return {}
    internal = internal_auth_base_host(host_ip)
    return {
        "GF_AUTH_OIDC_ENABLED": "true",
        "GF_AUTH_OIDC_CLIENT_ID": OAUTH_APPS["grafana"]["client_id"],
        "GF_AUTH_OIDC_CLIENT_SECRET": secret,
        "GF_AUTH_OIDC_AUTH_URL": f"{base}/application/o/authorize/",
        "GF_AUTH_OIDC_TOKEN_URL": f"{internal}/application/o/token/",
        "GF_AUTH_OIDC_API_URL": f"{internal}/application/o/{slug}/userinfo/",
        "GF_AUTH_OIDC_SIGNOUT_URL": f"{base}/application/o/{slug}/end-session/",
        "GF_AUTH_OIDC_AUTO_LOGIN": "true",
        "GF_AUTH_ANONYMOUS_ENABLED": "false",
        "GF_SERVER_ROOT_URL": f"https://grafana.{lan}/",
    }


def audiobookshelf_updates(identity: dict[str, str]) -> dict[str, str]:
    secret = identity.get("AUTHENTIK_OIDC_AUDIOBOOKSHELF_CLIENT_SECRET", "")
    if is_placeholder(secret):
        return {}
    return {"AUDIOBOOKSHELF_OIDC_ENABLED": "true"}


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Sync OIDC vars into host-side state files (not the gitignored SOPS plaintext by default)"
    )
    parser.add_argument("--identity-env", type=Path, default=ROOT / "identity" / ".env")
    parser.add_argument("--arr-env", type=Path, default=ROOT / ".env")
    parser.add_argument("--obsv-env", type=Path, default=ROOT / "obsv" / ".env")
    parser.add_argument(
        "--arr-oauth-env",
        type=Path,
        default=None,
        help="Host-side file for generated Mealie/ABS OIDC vars (default: FOLDER_FOR_DATA/arrstack.oauth.env)",
    )
    parser.add_argument(
        "--obsv-oauth-env",
        type=Path,
        default=ROOT / "obsv" / "oauth.auto.env",
        help="Host-side file for generated Grafana OIDC vars (gitignored)",
    )
    parser.add_argument(
        "--write-repo-env",
        action="store_true",
        help="Legacy: also upsert into repo .env / obsv/.env (discouraged; drifts from SOPS)",
    )
    parser.add_argument("--write", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    identity = load_env(args.identity_env)
    arr = load_env(args.arr_env)
    lan = identity.get("IDENTITY_LAN_DOMAIN", "")
    if not lan:
        lan = arr.get("IDENTITY_LAN_DOMAIN", "")
    if not lan:
        print("ERROR: IDENTITY_LAN_DOMAIN not set", file=sys.stderr)
        return 1

    arr_updates = mealie_updates(identity, lan)
    arr_updates.update(audiobookshelf_updates(identity))
    grafana = grafana_updates(identity, lan, arr)

    arr_oauth = args.arr_oauth_env
    if arr_oauth is None:
        data = arr.get("FOLDER_FOR_DATA", "").strip()
        arr_oauth = Path(data) / "arrstack.oauth.env" if data else ROOT / "arrstack.oauth.env"

    # Always prefer host-side state files so the git checkout stays aligned with SOPS.
    write_env(arr_oauth, arr_updates, write=args.write, dry_run=args.dry_run)
    write_env(args.obsv_oauth_env, grafana, write=args.write, dry_run=args.dry_run)

    if args.write_repo_env:
        write_env(args.arr_env, arr_updates, write=args.write, dry_run=args.dry_run)
        write_env(args.obsv_env, grafana, write=args.write, dry_run=args.dry_run)

    if not args.write and not args.dry_run:
        print("Dry run — pass --write to update host-side oauth env files")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
