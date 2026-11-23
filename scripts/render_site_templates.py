#!/usr/bin/env python3
"""Render site-specific templates from secrets/site.env (or SITE_* in the environment)."""

from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# template_path → output_path (relative to repo root)
TEMPLATES: list[tuple[str, str]] = [
    ("adguard/dns-rewrites.yaml.tmpl", "adguard/dns-rewrites.yaml"),
    ("pangolin/site/blueprint.yaml.tmpl", "pangolin/site/blueprint.yaml"),
    ("obsv/scrape.yml.tmpl", "obsv/scrape.yml"),
    ("obsv/library-exporter.yaml.tmpl", "obsv/library-exporter.yaml"),
    ("arrstack/homepage/services.yaml.tmpl", "arrstack/homepage/services.yaml"),
    ("arrstack/seerr/bootstrap.yml.tmpl", "arrstack/seerr/bootstrap.yml"),
    ("jellyfin/bootstrap.yml.tmpl", "jellyfin/bootstrap.yml"),
]


def load_env(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    if not path.is_file():
        return out
    for line in path.read_text().splitlines():
        raw = line.strip()
        if not raw or raw.startswith("#") or "=" not in raw:
            continue
        key, _, val = raw.partition("=")
        out[key.strip()] = val.strip().strip('"').strip("'")
    return out


def site_vars(site_env: Path) -> dict[str, str]:
    merged = {k: v for k, v in os.environ.items() if k.startswith("SITE_")}
    merged.update(load_env(site_env))
    required = [
        "SITE_DOMAIN",
        "SITE_LAN_DOMAIN",
        "SITE_ARRSTACK_IP",
        "SITE_ADGUARD_IP",
        "SITE_JELLYFIN_IP",
        "SITE_HOUSEHOLD_EMAIL_1",
    ]
    missing = [k for k in required if not merged.get(k)]
    if missing:
        raise SystemExit(f"missing site vars: {', '.join(missing)} (decrypt secrets/site.env first)")
    if "SITE_HOUSEHOLD_EMAIL_2" not in merged:
        merged["SITE_HOUSEHOLD_EMAIL_2"] = merged["SITE_HOUSEHOLD_EMAIL_1"]
    # Convenience aliases used in templates
    merged.setdefault("SITE_LAN_CIDR", merged.get("SITE_LAN_CIDR", "<lan-cidr>"))
    return merged


# Only substitute SITE_* placeholders. Other ${FOO} tokens (Homepage, Compose)
# are left untouched.
_SITE_VAR = re.compile(r"\$\{(SITE_[A-Z0-9_]+)\}")


def render(text: str, mapping: dict[str, str]) -> str:
    def repl(m: re.Match[str]) -> str:
        key = m.group(1)
        if key not in mapping:
            raise KeyError(key)
        return mapping[key]

    return _SITE_VAR.sub(repl, text)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--site-env", type=Path, default=ROOT / "secrets" / "site.env")
    parser.add_argument("--check", action="store_true", help="render to stdout / verify only")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    mapping = site_vars(args.site_env)
    for tmpl_rel, out_rel in TEMPLATES:
        tmpl = ROOT / tmpl_rel
        out = ROOT / out_rel
        if not tmpl.is_file():
            print(f"skip missing template: {tmpl_rel}", file=sys.stderr)
            continue
        try:
            rendered = render(tmpl.read_text(), mapping)
        except KeyError as exc:
            print(f"ERROR: {tmpl_rel} references unset ${{{exc.args[0]}}}", file=sys.stderr)
            return 1
        if args.check or args.dry_run:
            print(f"ok {tmpl_rel} → {out_rel} ({len(rendered)} bytes)")
            continue
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(rendered)
        print(f"wrote {out_rel}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
