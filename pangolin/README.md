# Pangolin — remote access for Jellyfin and the LAN

Identity-aware reverse proxy plus WireGuard VPN, self-hosted on a public VPS. It gives two
different kinds of access from one control plane:

- **Friends** open `https://jellyfin.<domain>` in a browser or a Jellyfin app. No client, no
  account here — Jellyfin's own login is the gate. Traffic always transits the VPS.
- **You** run the Pangolin client and reach AdGuard and the rest of `<lan-cidr>`. That path is
  peer-to-peer when NAT allows and only relays through the VPS when hole punching fails.

Nothing is exposed on the AT&T gateway. Newt dials out from the LAN, so there are no inbound
firewall rules or port forwards at home.

## Why this is not in `docker-compose.yaml`

The MediaStack in the repo root runs on CT 100 and is deployed to `/mnt/config`. Pangolin's server
half runs on a VPS instead, and its site half (Newt) must reach LAN addresses directly rather than
going through Gluetun. Keeping them separate avoids both problems.

## Layout

| Path | Runs where | Purpose |
| --- | --- | --- |
| `server/` | VPS `172.234.196.180` | Pangolin, Gerbil, Traefik |
| `site/` | CT 103 `newt` (`<newt-host>`) | Newt connector and the resource blueprint |
| `deploy.sh` | this workstation | Renders templates and pushes `server/` to the VPS |
| `site/deploy.sh` | this workstation | Pushes the connector and blueprint to CT 103 |
| `.env`, `site/.env` | not committed | Domains, ACME email, `SERVER_SECRET`, site credentials |

`server/config/*.tmpl` are templates because Traefik's static config cannot mix a file with
environment variables, and Pangolin's `config.yml` does not interpolate. `deploy.sh` renders them
with `envsubst` so every per-install value lives in one `.env`. To recreate this deployment
elsewhere, change `.env` and run `./deploy.sh` — no file in `server/` needs editing.

`SERVER_SECRET` is the one secret that never lands in a rendered file: Pangolin reads it from the
environment, so `config.yml` omits `server.secret` entirely.

## First deploy

```bash
cd pangolin
cp .env.example .env
openssl rand -hex 32          # paste into SERVER_SECRET
$EDITOR .env                  # also set PANGOLIN_ACME_EMAIL
./deploy.sh --render          # optional: inspect .rendered/ first
./deploy.sh
```

Prerequisites, all already true for this deployment:

- Explicit A records for `pangolin`, `jellyfin`, `auth`, and `seerr` point at the VPS. There is no zone-wide `*` record.
- The Linode Cloud Firewall allows inbound TCP 80, 443, 22 and UDP 51820, 21820.

Then register the admin account. Pangolin prints a one-time setup token on first boot:

```bash
ssh <pangolin-vps> 'cd /opt/pangolin && docker compose logs pangolin | grep -i token'
```

Visit `https://pangolin.<domain>/auth/initial-setup` and use it. The first certificate can take
a minute; a warning on the very first load is normal.

## Connecting the LAN

Newt runs on **CT 103 `newt`** (`<newt-host>`), a dedicated unprivileged LXC on pve built for this
purpose — 1 core, 1 GB RAM, 8 GB rootfs, Debian 13, `nesting=1,keyctl=1` so Docker works, `onboot=1`.
It is deliberately not on CT 100: the connector needs direct LAN routing, and Gluetun's namespace
would break that. The compose uses `network_mode: host` and `NET_ADMIN` so Olm clients get a site
(hole punch / `newt/wg/get-config`); Jellyfin HTTPS alone does not prove the client VPN path.

In the dashboard, create a site (**Sites → Add Site → Newt**) named `homelab`. Pangolin generates a
`NEWT_ID` and `NEWT_SECRET`. Then:

```bash
cd pangolin/site
cp .env.example .env
$EDITOR .env          # NEWT_ID, NEWT_SECRET
./deploy.sh
```

Newt applies `blueprint.yaml` on connect and keeps reapplying it. That file — not the dashboard — is
the source of truth for the Jellyfin resource, AdGuard, and the LAN range. Dashboard edits to those
resources get reverted, which is the intended behaviour; change the blueprint and restart Newt.

## Things worth knowing

- **Jellyfin SSO is deliberately disabled.** Pangolin's browser auth cannot be completed by native
  Jellyfin clients, so turning it on would break everything but the web player. See the comment in
  `site/blueprint.yaml`.
- **Bandwidth is the real constraint.** Every friend's stream exits the Nanode, which includes 1 TB
  of transfer per month; 1080p runs roughly 3–4 GB per hour. Cap remote streaming quality in
  Jellyfin if that gets close.
- **Jellyfin, Seerr, and Authentik are public.** Friends use `https://jellyfin.<domain>` and
  `https://seerr.<domain>` (Jellyfin login inside each app; Pangolin SSO off). `https://auth.<domain>` is the Authentik UI through the tunnel, also with Pangolin SSO off. Sonarr, Radarr,
  qBittorrent, Portainer, and Proxmox are not published. Reach them over the client VPN via the
  `home-lan` private resource.
- **Do not edit files under `/opt/pangolin` on the VPS.** The next `./deploy.sh` overwrites them.
  Runtime state (`config/db`, `config/key`, `config/letsencrypt`) is never touched by a deploy.

## Updating

Bump the pinned versions in `.env`, then redeploy:

```bash
./deploy.sh
```

Newt is updated by changing `NEWT_VERSION` in `site/.env` and running `docker compose up -d`.
