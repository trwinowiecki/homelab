# Household app OIDC (Mealie, Audiobookshelf, Grafana, Portainer)

Create one OAuth2/OIDC application per service in Authentik. After login, the Authentik user dashboard (`https://auth.<lan-domain>/if/user/`) launches these apps. Redirect URIs:

| App | Redirect URI |
| --- | --- |
| Mealie | `https://mealie.<lan-domain>/api/auth/oauth/callback` |
| Audiobookshelf | `https://audiobookshelf.<lan-domain>/auth/openid/callback` (+ mobile redirect per Authentik docs) |
| Grafana | `https://grafana.<lan-domain>/login/generic_oauth` |
| Portainer | Settings → Authentication → OAuth → redirect URL shown in UI |

## Mealie

Set in root `.env` (synced by `./deploy.sh identity`):

```
MEALIE_OIDC_ENABLED=true
MEALIE_OIDC_CONFIGURATION_URL=https://auth.<lan-domain>/application/o/mealie/.well-known/openid-configuration
MEALIE_OIDC_CLIENT_ID=...
MEALIE_OIDC_CLIENT_SECRET=...
```

Mealie fetches the discovery document server-side. `./deploy.sh identity` still copies Caddy's
local root CA to `/arrstack/identity/caddy-ca/root.crt` (mode 644) for the leftover `tls internal`
listener on `auth.<domain>`. LAN `*.<lan-domain>` is a public Let's Encrypt wildcard.
Redeploy `./deploy.sh arrstack` after identity changes.

## Audiobookshelf

Bootstrap sets the public authorize URL and internal Docker token/userinfo/JWKS URLs (same pattern as
Portainer). Audiobookshelf must be **restarted** after bootstrap to register the OIDC passport
strategy (`./deploy.sh arrstack` does this automatically).

## Grafana

Set in `obsv/.env` (synced by `./deploy.sh identity`). Token/userinfo use `http://<LOCAL_DOCKER_IP>:9000`
so Grafana avoids Caddy's internal CA. `GF_SERVER_ROOT_URL` must match the public URL or Authentik
rejects the redirect URI. Anonymous access is disabled when OIDC is synced; Caddy no longer
forward-auths Grafana — login is handled in Grafana via Authentik OIDC.

Then `./deploy.sh obsv`.

## Portainer

Settings → Authentication → OAuth — use Authentik provider URLs; disable local auth only after testing.

Bootstrap sets **Authorization URL** to `https://auth.<lan-domain>/...` (browser) and **token/userinfo URLs** to `http://authentik-server:9000/...` (server-side inside Docker — Portainer cannot verify Caddy's internal TLS CA). If OAuth fails with "Unauthorized", check `docker logs portainer` for `x509: certificate signed by unknown authority` and re-run `./deploy.sh arrstack` to refresh OAuth settings.
