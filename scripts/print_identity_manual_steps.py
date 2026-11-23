#!/usr/bin/env python3
"""Print remaining manual OIDC/LDAP steps after ./deploy.sh identity."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from identity_oauth import OAUTH_APPS, auth_base_url, is_placeholder, issuer_url, load_env  # noqa: E402


def main() -> int:
    identity = load_env(ROOT / "identity" / ".env")
    lan = identity.get("IDENTITY_LAN_DOMAIN", "<lan-domain>")
    public = identity.get("IDENTITY_PUBLIC_DOMAIN", "<domain>")
    base = auth_base_url(lan)
    pangolin_spec = OAUTH_APPS["pangolin"]
    pangolin_redirect = identity.get("OAUTH_PANGOLIN_REDIRECT_URI", "").strip()

    print("=== Automated by ./deploy.sh identity ===")
    print("- RBAC groups (admin, household, jellyfin_users, vpn)")
    print("- Homelab login (one-step password, 30-day session, MFA for admin/vpn)")
    print("- Recovery flow (admin recovery links; Forgot password is not email)")
    print("- Invitation enrollment (household / friends; not admin or vpn)")
    print("- Caddy forward-auth proxy (domain level, implicit consent)")
    print("- Let's Encrypt wildcard for *.<lan-domain> (Cloudflare DNS-01)")
    print("- OAuth apps: Mealie, Grafana (+ .env sync), Audiobookshelf, Portainer")
    print("- LDAP provider + outpost token sync")
    print("- Portainer OAuth settings (bootstrap; set PORTAINER_OAUTH_ENABLED=true to switch)")
    print("- Jellyfin LDAP + plugins (./deploy.sh jellyfin)")
    print("- Failed-login notifications (Authentik UI; Discord if webhook set)")
    print()
    print("=== After identity deploy ===")
    print("  ./deploy.sh arrstack obsv   # Mealie + Grafana + Audiobookshelf OIDC env/bootstrap")
    print()
    print("=== Still manual ===")
    print()
    if pangolin_redirect:
        print("1. Pangolin OIDC — Authentik blueprint applied; paste into Pangolin Admin:")
        print(f"   Client ID:     {pangolin_spec['client_id']}")
        print(f"   Client secret: identity/.env → {pangolin_spec['secret_env']}")
        print(f"   Auth URL:      https://auth.{public}/application/o/authorize/")
        print(f"   Token URL:     https://auth.{public}/application/o/token/")
        print("   Do NOT auto-provision VPN roles for household users (use Authentik vpn group).")
    else:
        print("1. Pangolin OIDC (redirect URI chicken-and-egg)")
        print("   a. Pangolin Server Admin → Identity Providers → Add OAuth2/OIDC")
        print("   b. Copy Pangolin Redirect URL into identity/.env → OAUTH_PANGOLIN_REDIRECT_URI")
        print("   c. Re-run ./deploy.sh identity (applies blueprint-pangolin.yaml)")
        print("   d. Paste Authentik client ID/secret into Pangolin")
        if not is_placeholder(identity.get(pangolin_spec["secret_env"], "")):
            print(f"      Client ID: {pangolin_spec['client_id']}")
            print(f"      Secret:    identity/.env → {pangolin_spec['secret_env']}")
    print()
    print("2. Portainer — after OAuth login works, optionally set PORTAINER_OAUTH_ENABLED=true")
    print(f"   Redirect URI: https://portainer.{lan}/")
    print(f"   (bootstrap pre-fills OAuth URLs from {base})")
    print()
    print("3. Pangolin VPN — mobile client (see docs/identity/deploy.md)")
    print("   Override DNS + DNS Over Tunnel → <adguard-host> (AdGuard)")
    print("   Match Domains: *.<lan-domain> (recommended)")
    print()
    print("4. Operator MFA — next admin/vpn login enrolls TOTP or a passkey, then backup codes")
    print(f"   User settings: {base}/if/user/")
    print("   Break-glass akadmin stays password-only unless you add it to the admin group")
    print()
    print("5. Invitations — Directory → Tokens & App passwords → Invitations (or Directory → Invitations)")
    print(f"   Flow: homelab-enrollment-flow")
    print("   Custom attributes for household:")
    print("     groups: [household, jellyfin_users]")
    print("   Custom attributes for friends:")
    print("     groups: [jellyfin_users]")
    print("   Never put admin or vpn on an invitation")
    print()
    print("6. Password reset — Directory → Users → user → Create Recovery Link")
    print(f"   Jellyfin Forgot password → {base}/if/flow/homelab-recovery-flow/")
    if not identity.get("AUTHENTIK_DISCORD_WEBHOOK_URL", "").strip():
        print()
        print("7. Optional Discord login-failure alerts: identity/.env → AUTHENTIK_DISCORD_WEBHOOK_URL")
        print("   (same webhook as obsv/.env DISCORD_WEBHOOK_URL), then ./deploy.sh identity")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
