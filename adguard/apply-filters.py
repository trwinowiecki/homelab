#!/usr/bin/env python3
"""Replace the top-level `filters:` block in AdGuardHome.yaml.

Does not parse or rewrite the rest of the file, so the bcrypt admin hash, DHCP
settings, and anything else stay byte-identical. Requires no PyYAML.
"""

from __future__ import annotations

import argparse
import re
import shutil
import sys
from pathlib import Path

REQUIRED = ("enabled", "url", "name", "id")


def _top_level_span(text: str, key: str) -> tuple[int, int]:
    prefix = key + ":"
    lines = text.splitlines(keepends=True)
    start = None
    for i, line in enumerate(lines):
        if start is None:
            if line.startswith(prefix):
                start = i
            continue
        if line.strip() and not line.startswith((" ", "\t", "#")):
            return start, i
    if start is None:
        raise SystemExit(f"no top-level '{key}:' block found")
    return start, len(lines)


def extract_top_level_block(text: str, key: str) -> str:
    start, end = _top_level_span(text, key)
    return "".join(text.splitlines(keepends=True)[start:end])


def replace_top_level_block(text: str, key: str, new_block: str) -> str:
    if not new_block.endswith("\n"):
        new_block += "\n"
    lines = text.splitlines(keepends=True)
    start, end = _top_level_span(text, key)
    return "".join(lines[:start]) + new_block + "".join(lines[end:])


def parse_scalar(raw: str):
    value = raw.split("#", 1)[0].strip()
    if value in ("true", "yes"):
        return True
    if value in ("false", "no"):
        return False
    if re.fullmatch(r"-?\d+", value):
        return int(value)
    if (value.startswith('"') and value.endswith('"')) or (
        value.startswith("'") and value.endswith("'")
    ):
        return value[1:-1]
    return value


def parse_filters(block: str) -> list[dict]:
    entries: list[dict] = []
    current: dict | None = None
    for line in block.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or stripped == "filters:":
            continue
        if stripped.startswith("- "):
            if current:
                entries.append(current)
            current = {}
            stripped = stripped[2:].strip()
            if ":" not in stripped:
                raise SystemExit(f"malformed list item: {line!r}")
            key, raw = stripped.split(":", 1)
            current[key.strip()] = parse_scalar(raw)
            continue
        if current is None or ":" not in stripped:
            raise SystemExit(f"malformed filter line: {line!r}")
        key, raw = stripped.split(":", 1)
        current[key.strip()] = parse_scalar(raw)
    if current:
        entries.append(current)
    return entries


def validate(entries: list[dict]) -> None:
    if not entries:
        raise SystemExit("filters.yaml has no filter entries")
    urls: set[str] = set()
    ids: set[int] = set()
    for entry in entries:
        missing = [k for k in REQUIRED if k not in entry]
        if missing:
            raise SystemExit(f"filter missing {missing}: {entry}")
        if entry["url"] in urls:
            raise SystemExit(f"duplicate url: {entry['url']}")
        if entry["id"] in ids:
            raise SystemExit(f"duplicate id: {entry['id']}")
        urls.add(entry["url"])
        ids.add(entry["id"])


def render_filters(entries: list[dict]) -> str:
    lines = ["filters:"]
    for entry in entries:
        enabled = "true" if entry["enabled"] else "false"
        lines.append(f"  - enabled: {enabled}")
        lines.append(f"    url: {entry['url']}")
        lines.append(f"    name: {entry['name']}")
        lines.append(f"    id: {entry['id']}")
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--desired", required=True, type=Path)
    parser.add_argument("--config", type=Path)
    parser.add_argument("--backup", type=Path)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="validate and print the new filters block without writing",
    )
    args = parser.parse_args()

    desired_text = args.desired.read_text()
    entries = parse_filters(extract_top_level_block(desired_text, "filters"))
    validate(entries)
    new_block = render_filters(entries)

    if args.dry_run:
        sys.stdout.write(new_block)
        return 0
    if args.config is None:
        raise SystemExit("--config is required unless --dry-run")

    config_text = args.config.read_text()
    updated = replace_top_level_block(config_text, "filters", new_block)
    if updated == config_text:
        print(f"filters already match {args.desired}")
        return 0

    if args.backup:
        shutil.copy2(args.config, args.backup)
        print(f"backup={args.backup}")

    tmp = args.config.with_name(args.config.name + ".tmp")
    tmp.write_text(updated)
    tmp.chmod(args.config.stat().st_mode)
    tmp.replace(args.config)
    print(f"wrote {args.config} ({len(entries)} filters)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
