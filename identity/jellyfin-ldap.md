# Jellyfin LDAP (Authentik)

LDAP is applied automatically by `./deploy.sh jellyfin` via `jellyfin/bootstrap.yml` and
`scripts/bootstrap/jellyfin.py`. No manual plugin form on the server.

Jellyfin runs on **CT 104**. The LDAP outpost stays on **CT 100** and is reached by IP.

## Authentik side

Handled by `./deploy.sh identity`:

- LDAP provider + `jellyfin_users` group — `identity/authentik/blueprint-ldap.yaml.tmpl`
- LDAP outpost container on CT 100 (`authentik-ldap-outpost`), published as
  `<arrstack-host>:3389` / `:6636` on the host
- Bind token in `identity/.env` → `LDAP_JELLYFIN_BIND_TOKEN`

See [integrations.goauthentik.io/media/jellyfin](https://integrations.goauthentik.io/media/jellyfin/).

## Jellyfin side (bootstrap)

| Setting | Value |
| --- | --- |
| LDAP Server | `<arrstack-host>` (CT 100 host bind of the outpost) |
| LDAP Port | `3389` (plain LDAP) |
| Bind user | `cn=ldap_service_account,ou=users,dc=ldap,dc=goauthentik,dc=io` |
LDAP bind uses an Authentik **app password** (`LDAP_JELLYFIN_BIND_TOKEN`), not the API token intent.
`./deploy.sh identity` applies the provider object permission and syncs the bind password.
| Base DN | `dc=ldap,dc=goauthentik,dc=io` |
| Search filter | `(memberOf=cn=jellyfin_users,ou=groups,dc=ldap,dc=goauthentik,dc=io)` |
| Enable User Creation | on |
| Library access | all folders for new LDAP users |

Re-apply after token rotation:

```bash
./deploy.sh jellyfin
```

## Access model

- **Household + friends:** Authentik users in `jellyfin_users` — same username/password on TVs as Mealie (LDAP bind, not browser SSO).
- **Break-glass:** keep one local Jellyfin admin **not** in LDAP (`admin` by default).
- **Friends:** `jellyfin_users` only — no Pangolin VPN role.
- **Pangolin public Jellyfin:** SSO stays off (`pangolin/site/blueprint.yaml`).
- **Forgot password:** Jellyfin LDAP `PasswordResetUrl` points at Authentik `homelab-recovery-flow` (ask-admin message, not email). Admin sends a **Create Recovery Link** from Authentik to actually reset.
- **MFA:** TVs stay password-only. The LDAP provider uses `homelab-ldap-authentication-flow` (password only). Browser MFA on `admin`/`vpn` must not be on that bind flow — otherwise Jellyfin returns invalid credentials after you enroll TOTP.

## Verify

1. Log out of Jellyfin (`https://jellyfin.<lan-domain>`).
2. Sign in with an Authentik username in `jellyfin_users` (username, not email).
3. On failure, check Dashboard → Logs for LDAP plugin entries; confirm `<arrstack-host>:3389`
   is reachable from CT 104.
