#!/usr/bin/env python3
"""Validate .env before deploy — catches absorbed inline comments and bad shapes."""
from __future__ import annotations

import argparse
import ipaddress
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from arrstack_api_keys import PLACEHOLDER, parse_env_assignments, parse_env_value  # noqa: E402

CIDR_KEYS = ("LOCAL_SUBNET", "DOCKER_SUBNET")
IP_KEYS = ("LOCAL_DOCKER_IP", "DOCKER_GATEWAY")
PORT_KEY_RE = re.compile(r"^(WEBUI_PORT_|.*_PORT$)")

OPTIONAL_PLACEHOLDER_KEYS = (
    "SABNZBD_API_KEY",
    "USENET_HOST",
    "USENET_USERNAME",
    "USENET_PASSWORD",
    "NZBGEEK_API_KEY",
    "DRUNKENSLUG_API_KEY",
)


def absorbed_comment_errors(text: str) -> list[str]:
    errors: list[str] = []
    for lineno, line in enumerate(text.splitlines(), start=1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        m = re.match(r"^([A-Za-z_][A-Za-z0-9_]*)=(.*)$", stripped)
        if not m:
            continue
        name, raw = m.group(1), m.group(2)
        if " #" in raw:
            errors.append(
                f"line {lineno}: {name} has inline comment absorbed into value "
                f"(move comment to its own line above)"
            )
        if raw.startswith('"') and raw.endswith('"') and "#" in raw[1:-1]:
            errors.append(
                f"line {lineno}: {name} has comment inside quoted value "
                f"(Docker Compose preserves it verbatim)"
            )
    return errors


def credential_errors(assignments: dict[str, str], *, allow_placeholders: bool) -> list[str]:
    if allow_placeholders:
        return []

    errors: list[str] = []
    vpn_type = parse_env_value(assignments.get("VPN_TYPE", "openvpn")).lower()

    if vpn_type == "openvpn":
        for key in ("VPN_USERNAME", "VPN_PASSWORD"):
            value = parse_env_value(assignments.get(key, ""))
            if not value or value in PLACEHOLDER:
                errors.append(f"{key} is unset or placeholder (required for VPN_TYPE=openvpn)")
    elif vpn_type == "wireguard":
        for key in ("WIREGUARD_PRIVATE_KEY", "WIREGUARD_ADDRESSES"):
            value = parse_env_value(assignments.get(key, ""))
            if not value or value in PLACEHOLDER:
                errors.append(f"{key} is unset or placeholder (required for VPN_TYPE=wireguard)")
    else:
        errors.append(f"VPN_TYPE={vpn_type!r} is not openvpn or wireguard")

    return errors


def credential_warnings(assignments: dict[str, str]) -> list[str]:
    warnings: list[str] = []
    for key in OPTIONAL_PLACEHOLDER_KEYS:
        value = parse_env_value(assignments.get(key, ""))
        if not value or value in PLACEHOLDER:
            warnings.append(f"{key} is unset or placeholder (optional until usenet/indexers are configured)")
    return warnings


def validate_env_file(path: Path, *, allow_placeholders: bool = False) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []
    text = path.read_text()
    errors.extend(absorbed_comment_errors(text))
    assignments = parse_env_assignments(text)

    for key in CIDR_KEYS:
        value = assignments.get(key, "")
        if not value:
            continue
        try:
            ipaddress.ip_network(value, strict=False)
        except ValueError:
            errors.append(f"{key}={value!r} is not a valid CIDR")

    for key in IP_KEYS:
        value = assignments.get(key, "")
        if not value:
            continue
        try:
            ipaddress.ip_address(value)
        except ValueError:
            errors.append(f"{key}={value!r} is not a valid IP address")

    for key, value in assignments.items():
        if not PORT_KEY_RE.match(key):
            continue
        if not value:
            continue
        try:
            port = int(value)
        except ValueError:
            errors.append(f"{key}={value!r} is not an integer port")
            continue
        if port < 1 or port > 65535:
            errors.append(f"{key}={port} is out of range 1-65535")

    errors.extend(credential_errors(assignments, allow_placeholders=allow_placeholders))
    warnings.extend(credential_warnings(assignments))

    return errors, warnings


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate arrstack .env before deploy.")
    parser.add_argument("--env-file", type=Path, default=ROOT / ".env")
    parser.add_argument(
        "--allow-placeholders",
        action="store_true",
        help="Skip hard errors for VPN credentials still at CHANGE_ME (for .env.example)",
    )
    args = parser.parse_args()

    path = args.env_file.resolve()
    if not path.is_file():
        print(f"ERROR: {path} not found", file=sys.stderr)
        return 1

    errors, warnings = validate_env_file(path, allow_placeholders=args.allow_placeholders)

    if warnings:
        print(f"WARN: {path}:", file=sys.stderr)
        for warn in warnings:
            print(f"  {warn}", file=sys.stderr)

    if errors:
        print(f"ERROR: {path} failed validation:", file=sys.stderr)
        for err in errors:
            print(f"  {err}", file=sys.stderr)
        return 1

    print(f"OK: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
