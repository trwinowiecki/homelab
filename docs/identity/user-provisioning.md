# How users get access (Authentik → apps)

Authentik is the **identity source of truth**. Each app handles login differently — OIDC does not automatically mean "create a user everywhere."

## Groups in Authentik

| Group | Purpose |
| --- | --- |
| `admin` | Forward-auth admin UIs, Portainer OAuth, Grafana; Authentik superuser; also Pangolin OIDC |
| `household` | Mealie and Audiobookshelf OIDC |
| `jellyfin_users` | Jellyfin via LDAP only — **not** Pangolin VPN |
| `vpn` | Pangolin OIDC / client VPN without making someone an admin |

Add users in Authentik (Directory → Users), then assign groups — or send an **invitation** (below). Notes on each group live in Authentik **Directory → Groups → Attributes → `notes`**.

Typical assignments: you → `admin` + `household` + `jellyfin_users` (VPN comes with `admin`); household → `household` + `jellyfin_users`; friends → `jellyfin_users` only; trusted remote helper → `vpn` (plus other groups as needed).

## Self-service (account editing)

Household users should bookmark **[https://auth.<lan-domain>/if/user/](https://auth.<lan-domain>/if/user/)** (gear icon after login). From there they can:

- Change display name, email, and password
- Enroll TOTP / passkeys / backup codes (optional unless they are in `admin` or `vpn`)
- Revoke sessions on a lost phone

`admin` members are Authentik superusers and land in the Admin UI; the same user page is still under the user menu. The Authentik dashboard lists apps they can open (Mealie, Audiobookshelf, Grafana, Portainer, Homepage).

## MFA

| Who | What happens |
| --- | --- |
| `admin` or `vpn` | Next browser login must enroll TOTP or a passkey, then static backup codes |
| `household` / `jellyfin_users` only | Password is enough; they can add MFA themselves in User settings |
| Jellyfin (TVs / apps) | LDAP password only — bind uses `homelab-ldap-authentication-flow`, not the browser MFA flow |
| `akadmin` | Break-glass. Stays password-only unless you add it to `admin` |

Keep the existing `ak changepassword akadmin` path in [deploy.md](deploy.md) if you lock yourself out.

## Password reset

There is **no Authentik SMTP**. "Forgot password?" on the login page (and Jellyfin's LDAP reset URL) does **not** email a link. It tells the person to ask you.

To actually reset a password:

1. Authentik Admin → Directory → Users → the user → **Create Recovery Link**
2. Text or otherwise send that URL (it opens `homelab-recovery-flow` and lets them set a new password)

You can still set a password directly on the user object. Do not enable a public email-reset flow until SMTP exists — `auth.<domain>` is on the internet.

## Invitations (household and friends)

Use this instead of typing someone else's password.

1. Authentik Admin → Directory → Invitations → Create
2. **Flow:** `homelab-enrollment-flow` (Homelab enrollment)
3. Single-use, set an expiry
4. **Custom attributes** (YAML/JSON) choose groups:

Household (Mealie + Audiobookshelf + Jellyfin):

```yaml
groups:
  - household
  - jellyfin_users
```

Friends (Jellyfin / Seerr only):

```yaml
groups:
  - jellyfin_users
```

Never put `admin` or `vpn` on an invitation. Create those users yourself and assign groups by hand.

The invitee opens the link, picks username/password, and is signed in. They will not be forced through MFA. If groups did not apply, assign them on the user in Directory → Users.

## Per-app behavior

| App | How access works | User created in app? |
| --- | --- | --- |
| **Sonarr, Radarr, …** | Caddy forward-auth; must be in `admin` | No — Authentik session only |
| **Mealie** | OIDC login | Yes — on first OIDC login (env-driven) |
| **Grafana** | OIDC login | Yes — on first OAuth sign-in |
| **Audiobookshelf** | OIDC login | Yes — bootstrap enables auto-register |
| **Portainer** | OAuth login | Yes — `OAuthAutoCreateUsers` in bootstrap |
| **Jellyfin** | LDAP bind to Authentik outpost | Jellyfin user created on first LDAP login |
| **Pangolin** | Separate org user database | **Manual or policy** — see below |

## Pangolin is different

Pangolin OIDC proves identity to Authentik, but Pangolin still needs a **user record in its organization**.

1. **Identifier Path** (Pangolin IdP → Token Configuration): set to `preferred_username` or `email`, **not** `sub`.  
   Default `sub` produces opaque hashes like `42887ddf…` that you cannot pre-provision.

2. **Pre-create the user** (recommended for admin VPN access):
   - Pangolin Server Admin → your Organization → Users → Create User
   - Type: **External**
   - Identity Provider: your Authentik IdP
   - Username: must match Authentik `preferred_username` (e.g. `taylor`) or email if using `email`

3. **Or** enable **Auto Provision Users** + an **Organization Policy** that maps Authentik `admin` / `vpn` to Pangolin roles.  
   Do **not** auto-provision `household` / `jellyfin_users` into VPN roles — keep `pangolin/site/blueprint.yaml` `private-resources.users` as the allowlist. Authentik `vpn` only allows OIDC login; LAN resources still need that email (or a Pangolin role) on the private-resource grants.

Error `User with username <hash> is unprovisioned` → Identifier Path is still `sub`, or no matching external user exists in the Pangolin org.

## Portainer OAuth callback

Portainer 2.x redirects to `https://portainer.<lan-domain>/` (root), not `/api/v2/auth/oauth/verify`.  
Caddy must bypass forward-auth when `?code=` is present so the Portainer UI can POST to `/api/auth/oauth/validate`.
