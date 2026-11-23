#!/usr/bin/env python3
"""Merge DNS rewrites from adguard/dns-rewrites.yaml into AdGuardHome.yaml.

Self-contained (no PyYAML / minyaml) — same constraint as apply-filters.py when
pushed to CT 102 as /usr/local/sbin/adguard-apply-dns-rewrites.py.

AdGuard Home v0.107+ stores rewrites under filtering.rewrites (not dns.rewrites).
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path


def parse_desired(text: str) -> tuple[str, str, list[str]]:
    domain = ""
    target = ""
    hosts: list[str] = []
    in_rewrites = False

    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped == "rewrites:":
            in_rewrites = True
            continue
        if in_rewrites:
            if stripped.startswith("- "):
                host = stripped[2:].strip().strip("\"'")
                hosts.append(host)
            continue
        if stripped.startswith("domain:"):
            domain = stripped.split(":", 1)[1].strip()
        elif stripped.startswith("target:"):
            target = stripped.split(":", 1)[1].strip()

    if not domain or not target:
        raise SystemExit("dns-rewrites.yaml must set domain: and target:")
    if not hosts:
        raise SystemExit("dns-rewrites.yaml must list at least one entry under rewrites:")
    return domain, target, hosts


def render_rewrites(domain: str, target: str, hosts: list[str]) -> str:
    lines = ["  rewrites:"]
    for host in hosts:
        fqdn = host if "." in host else f"{host}.{domain}"
        # Quote so YAML does not treat leading * as an alias (*.home.example).
        domain_yaml = f'"{fqdn}"' if fqdn.startswith("*") else fqdn
        lines.append(f"    - domain: {domain_yaml}")
        lines.append(f"      answer: {target}")
        lines.append("      enabled: true")
    return "\n".join(lines) + "\n"


def replace_filtering_rewrites_block(config_text: str, new_rewrites: str) -> str:
    """Replace filtering.rewrites in AdGuardHome.yaml."""
    if "filtering:" not in config_text:
        raise SystemExit("AdGuardHome.yaml has no top-level filtering: block")

    # Remove mistaken legacy inserts under dns: (older script versions).
    config_text = re.sub(
        r"^dns:\n  rewrites:\n(?:    - domain: .+\n      answer: .+\n      enabled: true\n)+",
        "dns:\n",
        config_text,
        count=1,
        flags=re.MULTILINE,
    )

    inline = re.compile(r"^  rewrites: \[\]\s*$", re.MULTILINE)
    if inline.search(config_text):
        return inline.sub(new_rewrites.rstrip(), config_text, count=1)

    # Existing AdGuard rewrites may quote wildcard domains.
    block = re.compile(
        r"^  rewrites:\n(?:    - domain: .+\n      answer: .+\n(?:      enabled: (?:true|false)\n)?)+",
        re.MULTILINE,
    )
    if block.search(config_text):
        return block.sub(new_rewrites.rstrip(), config_text, count=1)

    # Insert before safe_fs_patterns inside filtering (stable anchor).
    anchor = re.compile(r"^  safe_fs_patterns:", re.MULTILINE)
    match = anchor.search(config_text)
    if not match:
        raise SystemExit("filtering.safe_fs_patterns anchor not found in AdGuardHome.yaml")
    pos = match.start()
    return config_text[:pos] + new_rewrites + config_text[pos:]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--desired",
        type=Path,
        required=True,
        help="Path to dns-rewrites.yaml",
    )
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    domain, target, hosts = parse_desired(args.desired.read_text())
    new_block = render_rewrites(domain, target, hosts)

    config_text = args.config.read_text()
    updated = replace_filtering_rewrites_block(config_text, new_block)

    if args.dry_run:
        print(updated)
        return 0

    args.config.write_text(updated)
    print(f"Updated filtering.rewrites in {args.config}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
