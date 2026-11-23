#!/usr/bin/env python3
"""Render identity/authentik/*.yaml.tmpl blueprints from env."""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from identity_oauth import blueprint_context, load_env  # noqa: E402

AUTHENTIK_DIR = ROOT / "identity" / "authentik"
STATIC_BLUEPRINTS = (
    "blueprint-groups.yaml",
    "blueprint-flow-ldap.yaml",
    "blueprint-flow-recovery.yaml",
    "blueprint-flow-enrollment.yaml",
    "blueprint-flow-login.yaml",
    "blueprint-events.yaml",
)


def render_template(text: str, ctx: dict[str, str]) -> str:
    out = text
    for key, val in ctx.items():
        out = out.replace(f"{{{{{key}}}}}", val)
    missing = set(re.findall(r"\{\{([A-Z0-9_]+)\}\}", out))
    if missing:
        raise ValueError(f"unset blueprint template vars: {', '.join(sorted(missing))}")
    return out


def render_all(identity_env: Path, arr_env: Path) -> int:
    ctx = blueprint_context(load_env(identity_env), load_env(arr_env))
    for key in (
        "LDAP_OUTPOST_TOKEN",
        "LDAP_JELLYFIN_BIND_TOKEN",
        "AUTHENTIK_OIDC_MEALIE_CLIENT_SECRET",
        "AUTHENTIK_OIDC_GRAFANA_CLIENT_SECRET",
        "AUTHENTIK_OIDC_AUDIOBOOKSHELF_CLIENT_SECRET",
        "AUTHENTIK_OIDC_PORTAINER_CLIENT_SECRET",
    ):
        if not ctx.get(key):
            print(
                f"ERROR: {key} missing — run: python3 scripts/merge_identity_env.py --write",
                file=sys.stderr,
            )
            return 1

    pangolin_redirect = ctx.get("OAUTH_PANGOLIN_REDIRECT_URI", "").strip()
    pangolin_out = AUTHENTIK_DIR / "blueprint-pangolin.yaml"
    discord_webhook = ctx.get("AUTHENTIK_DISCORD_WEBHOOK_URL", "").strip()
    discord_out = AUTHENTIK_DIR / "blueprint-events-discord.yaml"

    written = 0
    for tmpl in sorted(AUTHENTIK_DIR.glob("*.yaml.tmpl")):
        if tmpl.name == "blueprint-pangolin.yaml.tmpl":
            if not pangolin_redirect:
                if pangolin_out.is_file():
                    pangolin_out.unlink()
                    print("Skipped blueprint-pangolin.yaml (OAUTH_PANGOLIN_REDIRECT_URI unset)")
                continue
            if not ctx.get("AUTHENTIK_OIDC_PANGOLIN_CLIENT_SECRET"):
                print(
                    "ERROR: AUTHENTIK_OIDC_PANGOLIN_CLIENT_SECRET missing for Pangolin blueprint",
                    file=sys.stderr,
                )
                return 1
        if tmpl.name == "blueprint-events-discord.yaml.tmpl":
            if not discord_webhook:
                if discord_out.is_file():
                    discord_out.unlink()
                    print("Skipped blueprint-events-discord.yaml (AUTHENTIK_DISCORD_WEBHOOK_URL unset)")
                continue
        out = AUTHENTIK_DIR / tmpl.name.replace(".yaml.tmpl", ".yaml")
        out.write_text(render_template(tmpl.read_text(), ctx))
        print(f"Wrote {out.relative_to(ROOT)}")
        written += 1

    for name in STATIC_BLUEPRINTS:
        src = AUTHENTIK_DIR / name
        if src.is_file():
            print(f"Using static {src.relative_to(ROOT)}")
            written += 1

    if written == 0:
        print("ERROR: no blueprint files found", file=sys.stderr)
        return 1
    return 0


def main() -> int:
    identity_env = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "identity" / ".env"
    arr_env = Path(sys.argv[2]) if len(sys.argv) > 2 else ROOT / ".env"
    return render_all(identity_env, arr_env)


if __name__ == "__main__":
    raise SystemExit(main())
