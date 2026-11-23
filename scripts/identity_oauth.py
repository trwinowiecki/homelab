"""Authentik OAuth/LDAP secret names and URL helpers for identity blueprints."""

from __future__ import annotations

import secrets
from pathlib import Path

PLACEHOLDER = frozenset({"", "CHANGE_ME", "change_me"})

# Fixed OAuth client IDs (slugs). Secrets are generated into identity/.env.
OAUTH_APPS: dict[str, dict[str, str]] = {
    "mealie": {
        "client_id": "homelab-mealie",
        "secret_env": "AUTHENTIK_OIDC_MEALIE_CLIENT_SECRET",
        "redirect_path": "/api/auth/oauth/callback",
        "host_prefix": "mealie",
        "authentik_slug": "mealie",
    },
    "grafana": {
        "client_id": "homelab-grafana",
        "secret_env": "AUTHENTIK_OIDC_GRAFANA_CLIENT_SECRET",
        "redirect_path": "/login/generic_oauth",
        "host_prefix": "grafana",
        "authentik_slug": "grafana",
    },
    "audiobookshelf": {
        "client_id": "homelab-audiobookshelf",
        "secret_env": "AUTHENTIK_OIDC_AUDIOBOOKSHELF_CLIENT_SECRET",
        "redirect_path": "/auth/openid/callback",
        "host_prefix": "audiobookshelf",
        "authentik_slug": "audiobookshelf",
    },
    "portainer": {
        "client_id": "homelab-portainer",
        "secret_env": "AUTHENTIK_OIDC_PORTAINER_CLIENT_SECRET",
        "redirect_path": "/",
        "host_prefix": "portainer",
        "authentik_slug": "portainer",
    },
    "pangolin": {
        "client_id": "homelab-pangolin",
        "secret_env": "AUTHENTIK_OIDC_PANGOLIN_CLIENT_SECRET",
        "redirect_path": "",
        "host_prefix": "pangolin",
        "authentik_slug": "pangolin",
    },
}

TOKEN_ENV_VARS = (
    "LDAP_OUTPOST_TOKEN",
    "LDAP_JELLYFIN_BIND_TOKEN",
    *tuple(app["secret_env"] for app in OAUTH_APPS.values()),
)

OPTIONAL_OAUTH_ENV = (
    "OAUTH_PANGOLIN_REDIRECT_URI",
    "AUTHENTIK_OIDC_PANGOLIN_CLIENT_SECRET",
)


def load_env(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    if not path.is_file():
        return out
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        out[key.strip()] = val.strip().strip('"')
    return out


def is_placeholder(value: str) -> bool:
    return value in PLACEHOLDER


def generate_secret() -> str:
    return secrets.token_urlsafe(48)


def auth_base_url(lan_domain: str) -> str:
    return f"https://auth.{lan_domain}"


def app_url(lan_domain: str, host_prefix: str) -> str:
    return f"https://{host_prefix}.{lan_domain}"


def oauth_redirect_uri(lan_domain: str, host_prefix: str, redirect_path: str) -> str:
    return f"{app_url(lan_domain, host_prefix)}{redirect_path}"


def issuer_url(lan_domain: str, slug: str) -> str:
    return f"{auth_base_url(lan_domain)}/application/o/{slug}/"


def well_known_url(lan_domain: str, slug: str) -> str:
    return f"{issuer_url(lan_domain, slug)}.well-known/openid-configuration"


def internal_auth_base_docker() -> str:
    """Authentik HTTP URL on the mediastack/identity Docker network."""
    return "http://authentik-server:9000"


def internal_auth_base_host(lan_ip: str) -> str:
    """Authentik HTTP URL via host-published port (obsv stack)."""
    return f"http://{lan_ip}:9000"


def blueprint_context(identity_env: dict[str, str], arr_env: dict[str, str]) -> dict[str, str]:
    lan = identity_env.get("IDENTITY_LAN_DOMAIN") or arr_env.get("IDENTITY_LAN_DOMAIN", "")
    public = identity_env.get("IDENTITY_PUBLIC_DOMAIN") or arr_env.get("IDENTITY_PUBLIC_DOMAIN", "")
    if not lan:
        raise ValueError("IDENTITY_LAN_DOMAIN is required")

    ctx: dict[str, str] = {
        "IDENTITY_LAN_DOMAIN": lan,
        "IDENTITY_PUBLIC_DOMAIN": public,
        "AUTH_BASE_URL": auth_base_url(lan),
        "COOKIE_DOMAIN": lan,
    }
    for name, spec in OAUTH_APPS.items():
        prefix = spec["host_prefix"].upper().replace("-", "_")
        slug = spec["authentik_slug"]
        ctx[f"OAUTH_{prefix}_CLIENT_ID"] = spec["client_id"]
        redirect_path = spec["redirect_path"]
        if redirect_path:
            ctx[f"OAUTH_{prefix}_REDIRECT_URI"] = oauth_redirect_uri(
                lan, spec["host_prefix"], redirect_path
            )
        ctx[f"OAUTH_{prefix}_ISSUER"] = issuer_url(lan, slug)
        secret_key = spec["secret_env"]
        ctx[secret_key] = identity_env.get(secret_key, "")
    if public:
        ctx["AUTH_PUBLIC_BASE_URL"] = f"https://auth.{public}"
    ctx["OAUTH_PANGOLIN_REDIRECT_URI"] = identity_env.get("OAUTH_PANGOLIN_REDIRECT_URI", "")
    webhook = identity_env.get("AUTHENTIK_DISCORD_WEBHOOK_URL", "").strip()
    if webhook and not webhook.rstrip("/").endswith("/slack"):
        webhook = webhook.rstrip("/") + "/slack"
    ctx["AUTHENTIK_DISCORD_WEBHOOK_URL"] = webhook
    for key in TOKEN_ENV_VARS:
        if key not in ctx:
            ctx[key] = identity_env.get(key, "")
    return ctx
