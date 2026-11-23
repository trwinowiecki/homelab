#!/usr/bin/env python3
"""Apply declarative arrstack settings from arrstack/*/bootstrap.yml."""
from __future__ import annotations

import argparse
import sys
import urllib.parse
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from minyaml import loads  # noqa: E402

from bootstrap import audiobookshelf, bazarr, bindery, portainer, prowlarr, sabnzbd, seerr  # noqa: E402
from bootstrap.common import wait_http, service_url, resolved_api_keys  # noqa: E402

SERVICES = {
    "prowlarr": (prowlarr.BOOT, prowlarr.apply),
    "sabnzbd": (sabnzbd.BOOT, sabnzbd.apply),
    "bazarr": (bazarr.BOOT, bazarr.apply),
    "bindery": (bindery.BOOT, bindery.apply),
    "audiobookshelf": (audiobookshelf.BOOT, audiobookshelf.apply),
    "seerr": (seerr.BOOT, seerr.apply),
    "portainer": (None, portainer.apply),
}

READINESS = {
    "prowlarr": ("WEBUI_PORT_PROWLARR", 9696, "/ping"),
    "sabnzbd": ("WEBUI_PORT_SABNZBD", 8100, "/api?mode=version"),
    "bazarr": ("WEBUI_PORT_BAZARR", 6767, "/api/system/status"),
    "bindery": ("WEBUI_PORT_BINDERY", 8787, "/api/v1/health"),
    "audiobookshelf": ("WEBUI_PORT_AUDIOBOOKSHELF", 13378, "/status"),
    "seerr": ("WEBUI_PORT_SEERR", 5055, "/api/v1/status"),
    "portainer": ("WEBUI_PORT_PORTAINER", 9000, "/api/system/status"),
}


def wait_for_services(env_file: Path, only: list[str], timeout_s: int) -> None:
    keys = resolved_api_keys(env_file)
    for name in only:
        if name not in READINESS:
            continue
        port_var, default_port, path = READINESS[name]
        url = f"{service_url(env_file, port_var, default_port)}{path}"
        if name == "bazarr":
            key = keys.get("BAZARR_API_KEY", "")
            if key:
                url = f"{url}?apikey={urllib.parse.quote(key)}"
        if wait_http(url, timeout_s=timeout_s):
            print(f"ready: {name}")
        else:
            print(f"WARN: {name} not ready at {url}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Apply arrstack bootstrap YAML configs.")
    parser.add_argument(
        "--env-file",
        type=Path,
        default=Path(ROOT / ".env"),
        help="Read service URLs and credentials from this file",
    )
    parser.add_argument(
        "--only",
        type=str,
        default="",
        help="Comma-separated services (prowlarr,sabnzbd,bazarr,bindery,audiobookshelf,seerr,portainer)",
    )
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--wait",
        action="store_true",
        help="Poll service health endpoints before applying",
    )
    parser.add_argument(
        "--sync-profiles",
        action="store_true",
        help="Seerr only: sync HD/4K quality profiles and root folders from bootstrap.yml",
    )
    parser.add_argument("--wait-timeout", type=int, default=120)
    args = parser.parse_args()

    env_file = args.env_file.resolve()
    selected = [s.strip() for s in args.only.split(",") if s.strip()] or list(SERVICES)

    if args.wait:
        wait_for_services(env_file, selected, args.wait_timeout)

    exit_code = 0
    for name in selected:
        boot_path, apply_fn = SERVICES.get(name, (None, None))
        if apply_fn is None:
            print(f"unknown service: {name}", file=sys.stderr)
            return 1
        if boot_path is not None and not boot_path.is_file():
            print(f"skip {name}: {boot_path} missing")
            continue
        spec = {} if boot_path is None else (loads(boot_path.read_text()) or {})
        try:
            if name == "seerr":
                apply_fn(
                    spec,
                    env_file,
                    dry_run=args.dry_run,
                    sync_profiles=args.sync_profiles,
                )
            else:
                apply_fn(spec, env_file, dry_run=args.dry_run)
        except Exception as exc:
            print(f"ERROR: {name} bootstrap failed: {exc}", file=sys.stderr)
            exit_code = 1

    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
