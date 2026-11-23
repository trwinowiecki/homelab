#!/usr/bin/env python3
"""Remove Sonarr/Radarr quality profiles not in the TRaSH allowlist."""
from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from bootstrap.common import request_raw, resolved_api_keys, service_url  # noqa: E402

ALLOWLISTS: dict[str, frozenset[str]] = {
    "sonarr": frozenset({"WEB-1080p", "WEB-2160p", "Any", "Low (720p)"}),
    "radarr": frozenset({"HD Bluray + WEB", "UHD Bluray + WEB", "Any", "Low (720p)"}),
}

APPS: dict[str, tuple[str, int, str]] = {
    "sonarr": ("SONARR_API_KEY", 8989, "Sonarr"),
    "radarr": ("RADARR_API_KEY", 7878, "Radarr"),
}


def list_profiles(base_url: str, api_key: str) -> list[dict]:
    status, raw = request_raw(
        "GET",
        f"{base_url}/api/v3/qualityprofile",
        headers={"X-Api-Key": api_key},
    )
    if status != 200:
        print(f"ERROR: GET qualityprofile failed HTTP {status}", file=sys.stderr)
        return []
    data = json.loads(raw)
    return data if isinstance(data, list) else []


def delete_profile(base_url: str, api_key: str, profile_id: int) -> tuple[bool, str]:
    try:
        status, raw = request_raw(
            "DELETE",
            f"{base_url}/api/v3/qualityprofile/{profile_id}",
            headers={"X-Api-Key": api_key},
        )
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        return False, f"HTTP {exc.code}: {body[:200]}"
    if status in (200, 204):
        return True, ""
    return False, f"HTTP {status}: {raw[:200]}"


def prune_app(
    app: str,
    env_file: Path,
    *,
    dry_run: bool,
) -> int:
    key_name, default_port, label = APPS[app]
    keys = resolved_api_keys(env_file)
    api_key = keys.get(key_name, "")
    if not api_key:
        print(f"skip {app}: missing {key_name}")
        return 0

    port_var = f"WEBUI_PORT_{label.upper()}"
    base = service_url(env_file, port_var, default_port)
    allow = ALLOWLISTS[app]
    profiles = list_profiles(base, api_key)
    if not profiles:
        print(f"skip {app}: no profiles returned")
        return 0

    removed = 0
    warned = 0
    for profile in profiles:
        name = profile.get("name", "")
        pid = profile.get("id")
        if name in allow:
            continue
        if pid is None:
            continue
        if dry_run:
            print(f"{app}: would delete profile id={pid} name={name!r}")
            removed += 1
            continue
        ok, err = delete_profile(base, api_key, int(pid))
        if ok:
            print(f"{app}: deleted profile id={pid} name={name!r}")
            removed += 1
        else:
            print(f"{app}: WARN could not delete {name!r} (id={pid}): {err}")
            warned += 1

    print(f"{app}: kept {len(allow)} profile(s), removed {removed}, warnings {warned}")
    return 0 if warned == 0 else 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Prune non-TRaSH quality profiles.")
    parser.add_argument("--env-file", type=Path, default=ROOT / ".env")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--app",
        choices=sorted(APPS),
        action="append",
        help="Limit to sonarr and/or radarr (default: both)",
    )
    args = parser.parse_args()

    env_file = args.env_file.resolve()
    targets = args.app or sorted(APPS)
    for app in targets:
        prune_app(app, env_file, dry_run=args.dry_run)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
