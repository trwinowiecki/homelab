# Pangolin → Authentik OIDC (community edition)

After Authentik is running and `auth.<domain>` is tunneled (see `pangolin/site/blueprint.yaml`):

## Semi-automated workflow

1. Pangolin VPS: Server Admin → Identity Providers → Add → OAuth2/OIDC.
2. Copy Pangolin's **Redirect URL** (e.g. `https://pangolin.<domain>/auth/idp/1/oidc/callback`).
3. Paste into `identity/.env`:
  ```
   OAUTH_PANGOLIN_REDIRECT_URI=https://pangolin.<domain>/auth/idp/1/oidc/callback
  ```
4. `./deploy.sh identity` — applies `blueprint-pangolin.yaml` (Authentik provider + app).
5. Paste into Pangolin IdP settings:
  - Authorization URL: `https://auth.<domain>/application/o/authorize/`
  - Token URL: `https://auth.<domain>/application/o/token/`
  - Client ID: `homelab-pangolin`
  - Client secret: `AUTHENTIK_OIDC_PANGOLIN_CLIENT_SECRET` from `identity/.env`
6. Leave **Encryption Key** empty (Pangolin cannot decode encrypted ID tokens).
7. **Identifier Path** (Token Configuration): set to `preferred_username` (or `email`) — **not** `sub`.
8. Pre-create an **External** user in your Pangolin org with username matching Authentik (`taylor`, etc.), or use Auto Provision + org policy (`admin` / `vpn` only).
9. **Do not** auto-provision household/jellyfin users into VPN roles. Use Authentik `vpn` for extra people who should get the client.
10. Keep a **local Pangolin admin** account as break-glass.
11. `pangolin/site/blueprint.yaml` `private-resources.users` remains an explicit allowlist.

See [user-provisioning.md](user-provisioning.md) for how Authentik groups map to each app.

## Troubleshooting

### Pangolin login hangs after Authentik / "registering"

Pangolin exchanges the OAuth code server-side at the **Token URL**. From the VPS Pangolin container:

```bash
docker exec pangolin curl -sS -o /dev/null -w '%{http_code}\n' https://auth.<domain>/application/o/token/
```

Expect **405** (GET is not allowed). SSL errors or **404** mean `auth.<domain>` is not routed correctly.

**Common cause:** CT 103 blueprint missing the `auth` public resource. Traefik then serves `TRAEFIK DEFAULT CERT` and 404 for `auth.<domain>`. Ensure [`pangolin/site/blueprint.yaml`](../../pangolin/site/blueprint.yaml) includes the `auth:` block and redeploy:

```bash
cd pangolin/site && ./deploy.sh
# or rsync blueprint.yaml to CT 103 and: docker compose restart newt
```

Verify Traefik has an `Authentik-router` for `auth.<domain>` and the cert is Let's Encrypt (not `TRAEFIK DEFAULT CERT`).

### Authentik access for Pangolin OIDC

The Pangolin application is bound to **admin** or **vpn** (`policy_engine_mode: any`). `admin` keeps working for you; add others to `vpn` instead of making them Authentik superusers. They still need a Pangolin org user and a grant on `private-resources` (email in `pangolin/site/blueprint.yaml`, or a Pangolin role).

### `unprovisioned` / hash username

Pangolin received Authentik's opaque `sub` claim instead of a readable username.

1. Pangolin IdP → **Identifier Path** → `preferred_username`
2. Organization → Users → Create **External** user → username `taylor` (match Authentik)
3. Retry login

### `State mismatch`

Pangolin stored one OAuth `state` value but the callback returned another. Common causes:

1. **Stale session** — log out of Pangolin, clear site data for `pangolin.<domain>`, retry in one private window (single tab; do not refresh the callback URL).
2. **Redirect URI drift** — Authentik provider redirect must exactly match Pangolin's callback (`identity/.env` → `OAUTH_PANGOLIN_REDIRECT_URI` → `./deploy.sh identity`).
3. **Double callback** — Pangolin logs show `validate-callback` twice; wait for the first redirect to finish.

After changing IdP settings or redirect URI, start a **fresh** login (old authorization codes are one-time use).

Friends in Authentik (`jellyfin_users`) must not receive Pangolin VPN access.