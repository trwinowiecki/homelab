# Identity stack (Authentik + Caddy)

LAN SSO and HTTPS for arrstack admin UIs. Deployed as a **separate** Compose project on CT 100
(next to `obsv/`), not inside `docker-compose.yaml`.

## What it does

| Layer | Role |
| --- | --- |
| **Caddy** | `https://*.<lan-domain>` TLS + routes to containers |
| **Authentik** | OIDC IdP, forward-auth for *arr admin UIs, LDAP outpost for Jellyfin |
| **AdGuard rewrites** | `*.<lan-domain>` → `<arrstack-host>` (split-horizon; not public DNS) |
| **Pangolin** | `auth.<domain>` public tunnel (SSO off) + Pangolin OIDC login to Authentik |

Jellyfin is deployed via `./deploy.sh jellyfin` on **CT 104** (`jellyfin/` compose project).
Caddy on CT 100 reverse-proxies `jellyfin.<lan-domain>` to `<jellyfin-host>:8096`.
Pangolin Jellyfin resource stays `sso-enabled: false`. See [identity/jellyfin-ldap.md](../../identity/jellyfin-ldap.md).

## First-time setup

```bash
cp identity/.env.example identity/.env
# Fill AUTHENTIK_SECRET_KEY, AUTHENTIK_POSTGRESQL__PASSWORD, AUTHENTIK_BOOTSTRAP_*
openssl rand -base64 36   # AUTHENTIK_SECRET_KEY
openssl rand -base64 24   # AUTHENTIK_POSTGRESQL__PASSWORD

# Or merge an existing identity/.env that has drifted:
python3 scripts/merge_identity_env.py --dry-run    # preview
python3 scripts/merge_identity_env.py --write      # merge + auto-fill secrets (backs up .env)

./deploy.sh identity adguard arrstack
```

`./deploy.sh identity` automatically:

1. Merges `identity/.env` with `identity/.env.example` and fills OAuth/LDAP secrets (`scripts/merge_identity_env.py`)
2. Renders Authentik blueprints from `identity/authentik/*.yaml.tmpl`
3. Applies blueprints on CT 100 (groups, Caddy forward-auth, OIDC apps, LDAP outpost, optional Pangolin)
4. Syncs LDAP outpost token and restarts `ldap-outpost`
5. Syncs Mealie + Grafana + Audiobookshelf OIDC flags into root `.env` and `obsv/.env`
6. Prints remaining manual steps (`scripts/print_identity_manual_steps.py`)

Then:

1. **`./deploy.sh arrstack obsv`** — restart Mealie/Grafana; run Audiobookshelf/Portainer OAuth bootstrap.
2. Open `https://auth.<lan-domain>` — log in as **`akadmin`** with `AUTHENTIK_BOOTSTRAP_PASSWORD`.
3. Add users to `admin`, `household`, `jellyfin_users`, or `vpn` (Directory → Users / Groups), or send an invitation (see [user-provisioning.md](user-provisioning.md)). The next `admin`/`vpn` login enrolls MFA.
4. Test `https://sonarr.<lan-domain>` (forward-auth; `admin` group only).
5. Complete manual steps from deploy output (Pangolin OIDC redirect URI).
6. `./deploy.sh jellyfin` for LDAP + plugins on the Docker Jellyfin instance.

### Bootstrap admin password

`AUTHENTIK_BOOTSTRAP_*` applies **only on first Postgres init**. To reset later on CT 100:

```bash
cd /mnt/config/identity
printf '%s\n%s\n' "$AUTHENTIK_BOOTSTRAP_PASSWORD" "$AUTHENTIK_BOOTSTRAP_PASSWORD" \
  | docker compose exec -T authentik-server ak changepassword akadmin
```

### Blueprint files

| File | Purpose |
| --- | --- |
| `identity/authentik/blueprint-groups.yaml` | `admin`, `household`, `jellyfin_users`, `vpn` |
| `identity/authentik/blueprint-flow-ldap.yaml` | Password-only LDAP bind for Jellyfin (no TOTP) |
| `identity/authentik/blueprint-flow-recovery.yaml` | Admin recovery links; public Forgot password is not email |
| `identity/authentik/blueprint-flow-enrollment.yaml` | Invitation-only sign-up (not `admin` / `vpn`) |
| `identity/authentik/blueprint-flow-login.yaml` | One-step login, 30-day session, MFA for `admin`/`vpn`, IP reputation |
| `identity/authentik/blueprint-events.yaml` | Failed-login notifications in the Authentik UI |
| `identity/authentik/blueprint-events-discord.yaml.tmpl` | Same alerts to Discord (when `AUTHENTIK_DISCORD_WEBHOOK_URL` is set) |
| `identity/authentik/blueprint-proxy.yaml.tmpl` | Domain forward-auth for Caddy (implicit consent) |
| `identity/authentik/blueprint-oidc.yaml.tmpl` | Mealie, Grafana, Audiobookshelf, Portainer OAuth |
| `identity/authentik/blueprint-ldap.yaml.tmpl` | LDAP provider + outpost token for Jellyfin |
| `identity/authentik/blueprint-pangolin.yaml.tmpl` | Pangolin OIDC (when `OAUTH_PANGOLIN_REDIRECT_URI` is set) |

Re-apply after editing templates:

```bash
./scripts/render_identity_blueprints.sh identity/.env .env
./deploy.sh identity
```

Or on CT 100 only:

```bash
python3 scripts/apply_identity_blueprints.py
```

### Still manual (cannot fully automate)

| Item | Why |
| --- | --- |
| **Pangolin OIDC redirect URI** | Pangolin assigns `/auth/idp/N/oidc/callback` when you create the IdP — paste into `identity/.env` → `OAUTH_PANGOLIN_REDIRECT_URI`, then re-run `./deploy.sh identity` |
| **Invitations** | Create per person in Authentik (Directory → Invitations). See [user-provisioning.md](user-provisioning.md). |
| **Password recovery links** | No SMTP. Admin → User → Create Recovery Link. |
| **Discord login alerts** | Optional `AUTHENTIK_DISCORD_WEBHOOK_URL` in `identity/.env` (same webhook as `obsv/.env`) |

Portainer OAuth URLs are applied by arrstack bootstrap; set `PORTAINER_OAUTH_ENABLED=true` in `.env` only after testing. Audiobookshelf OIDC is applied by bootstrap when `AUDIOBOOKSHELF_OIDC_ENABLED=true` (synced from identity deploy).

Jellyfin LDAP is automated by `./deploy.sh jellyfin` — see [identity/jellyfin-ldap.md](../../identity/jellyfin-ldap.md).

See also [household-oidc.md](household-oidc.md), [pangolin-oidc.md](pangolin-oidc.md), [user-provisioning.md](user-provisioning.md).

## TLS (Let's Encrypt via Cloudflare)

Caddy issues a publicly-trusted wildcard for `*.<lan-domain>` with Let's Encrypt DNS-01
against Cloudflare. Browsers do not need a local CA. Public `*.<domain>` certs still come
from Traefik on the VPS (HTTP-01).

1. Add `<domain>` to Cloudflare (Free plan). Keep only these A records, all **DNS-only**
   (grey cloud): `pangolin`, `jellyfin`, `auth`, `seerr` → `172.234.196.180`.
2. Delete a zone-wide `*`, `home`, or `*.home` if Cloudflare imported them. Do not orange-cloud
   any record — that breaks Traefik HTTP-01 and split-horizon LAN names.
3. At Namecheap, set Custom nameservers to the two values Cloudflare shows. See
   [Namecheap's Cloudflare guide](https://www.namecheap.com/support/knowledgebase/article.aspx/9607/2210/how-to-set-up-dns-records-for-your-domain-in-a-cloudflare-account/)
   (ignore the paid CNAME/`www` path).
4. Create a zone-scoped API token with **Zone:DNS:Edit** and **Zone:Zone:Read**. Put it in
   `identity/.env` as `CLOUDFLARE_API_TOKEN`. `IDENTITY_ACME_DNS=true` and `CADDY_ACME_EMAIL`
   are required.
5. `./deploy.sh identity` builds Caddy with the current Cloudflare DNS module (stock
   `caddybuilds` images reject Cloudflare's `cfat_` / `cfut_` tokens). Confirm with
   `echo | openssl s_client -connect <arrstack-host>:443 -servername sonarr.<lan-domain>`
   — issuer should be Let's Encrypt, CN `*.<lan-domain>`.

Do **not** add public A records for `*.<lan-domain>` — only AdGuard LAN rewrites.
`auth.<domain>` in the Caddyfile stays `tls internal`; Pangolin already terminates public TLS.

## Remote access (Pangolin VPN)

Admin UIs (`*.<lan-domain>`) are **not** published on Pangolin. Reach them over the Pangolin **client VPN** with split-horizon DNS.

### Prerequisites

1. Log into **`https://pangolin.<domain>`** (self-hosted server, not Pangolin Cloud).
2. VPN connected; your user has a **site** assigned (settings grayed out until then).
3. Your email is listed under `private-resources.users` in `pangolin/site/blueprint.yaml`.

### Mobile client setup (off home WiFi)

1. Connect VPN.
2. Enable **Aliases (Override DNS)** — the "see docs" warning is expected.
3. Enable **DNS Over Tunnel**; set upstream DNS to **`<adguard-host>`** (AdGuard IP, not a hostname).
4. Optional: **Match Domains** → `*.<lan-domain>` so only homelab DNS uses AdGuard.

Without DNS Over Tunnel, `*.<lan-domain>` cannot resolve correctly off-LAN. If Namecheap still
has a zone-wide `*` A, those names hit the VPS (`172.234.196.180`) and Pangolin returns **404** for
unpublished hosts like Sonarr — the same failure as a phone on home WiFi that skips AdGuard (F-31).
After explicit public A records only, LAN names are NXDOMAIN publicly; verify with
`./scripts/check_home_dns_public.sh`.

| URL | Needs DNS Over Tunnel? |
| --- | --- |
| `https://auth.<domain>` | No (public Pangolin resource) |
| `https://jellyfin.<domain>` | No (public Jellyfin) |
| `https://seerr.<domain>` | No (public Seerr; Jellyfin login) |
| `https://sonarr.<lan-domain>` | **Yes** (or AdGuard on LAN) |

**On home WiFi:** disconnect Pangolin (or use DNS Over Tunnel). Turn off Android Private DNS /
iCloud Private Relay so the phone uses AdGuard for `*.<lan-domain>`.

Do not test the `<lan-cidr>` private resource while on home WiFi — overlapping CIDR steals routes.

See [docs/hosts/pangolin.md](../hosts/pangolin.md) for architecture.

Public `auth.<domain>` uses the same login flow. After several failed attempts from one IP, Authentik shows a lockout message (reputation policy). Failed logins notify the `admin` group in the Authentik UI (and Discord if `AUTHENTIK_DISCORD_WEBHOOK_URL` is set). There is no email-based reset.

## Break-glass

- Authentik down: `ssh` to CT 100, `curl -s http://127.0.0.1:8989` still reaches Sonarr if arrstack is up.
- Pangolin: keep a local Pangolin admin account.
- Jellyfin: keep one local admin not in LDAP.

## F-14 (IPv6)

CT 100 has public IPv6 with raw ports removed (arrstack UIs bind `127.0.0.1`). Verify AT&T gateway blocks inbound IPv6 to the LAN:

```bash
./scripts/check_ipv6_exposure.sh
```
