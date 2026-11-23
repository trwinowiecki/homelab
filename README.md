# Homelab

Personal Docker Compose configuration for a media automation stack, observability layer, DNS, and remote access. Derived from [geekau/mediastack](https://github.com/geekau/mediastack) but heavily customized — **do not use** [docs/upstream-mediastack.md](docs/upstream-mediastack.md) as a description of what runs here.

## Host topology

| Host | Role |
| --- | --- |
| **pve** (`<pve-host>`) | Proxmox VE hypervisor; node_exporter, backup timer, thin-pool alerts |
| **CT 100** arrstack (`<arrstack-host>`) | Media automation Docker stack + identity/obsv |
| **CT 104** jellyfin-docker (`<jellyfin-host>`) | Jellyfin 12.0 (Docker Compose production) |
| **CT 102** adguard (`<adguard-host>`) | AdGuard Home DNS + DHCP |
| **CT 103** newt (`<newt-host>`) | Pangolin site connector |
| **truenas** (`<truenas-host>`) | TrueNAS SCALE — media + config CIFS shares, ZFS snapshots, restic repo |
| **Linode VPS** | Pangolin server (outbound tunnel; no inbound home ports) |

Config is edited in this repo and deployed to `/mnt/config` on CT 100 via `./deploy.sh`.

## Deploy order (first time)

1. **TrueNAS** — create `metrion/zinthos/{media,config,backups}` datasets; mount CIFS on pve.
2. **AdGuard** — `./deploy.sh adguard`; complete the setup wizard and DHCP cutover ([docs/hosts/adguard.md](docs/hosts/adguard.md)).
3. **Arrstack** — copy `.env.example` → `.env`, fill credentials; `./deploy.sh arrstack`.
4. **Identity** — copy `identity/.env.example` → `identity/.env`; `./deploy.sh identity adguard`.
5. **Jellyfin** — copy `jellyfin/.env.example` → `jellyfin/.env`; `./deploy.sh jellyfin`. See [docs/jellyfin/deploy.md](docs/jellyfin/deploy.md).
6. **Observability + pve layer** — fill `obsv/.env`; `./deploy.sh obsv pve`.
7. **Pangolin** — separate from root deploy; see [pangolin/README.md](pangolin/README.md). Wire OIDC IdP per [docs/identity/deploy.md](docs/identity/deploy.md).
8. **Backups** — `./deploy.sh pve` installs the restic timer; store the restic password in your password manager ([docs/runbooks/backup-and-restore.md](docs/runbooks/backup-and-restore.md)).

## Credentials to obtain

| Variable / secret | Where | Required when |
| --- | --- | --- |
| `VPN_USERNAME`, `VPN_PASSWORD` | Nord Account → NordVPN → Service credentials | Before arrstack deploy (qBittorrent VPN) |
| `DISCORD_WEBHOOK_URL` | Discord channel → Integrations → Webhooks | Before obsv/pve deploy |
| `GRAFANA_ADMIN_PASSWORD` | You choose | Before obsv deploy |
| `PVE_TOKEN_VALUE` | Proxmox UI → API tokens (PVEAuditor) | Before obsv deploy (guest metrics) |
| `LINODE_TOKEN` | Linode API token (`account:read_only`) | Optional — transfer monitoring |
| Usenet provider + indexer keys | After purchase | Optional — SABnzbd + Prowlarr Newznab |
| `AUTHENTIK_*` in `identity/.env` | You generate (`openssl rand`) | Before `./deploy.sh identity` |
| Pangolin OIDC + `auth.<domain>` | [docs/identity/deploy.md](docs/identity/deploy.md) | Remote VPN login via Authentik |

`*arr` API keys and Mixarr's `SESSION_SECRET` are written to `arrstack.auto.env` on deploy when left at `CHANGE_ME`. Set `AUDIOBOOKSHELF_PASSWORD` yourself before the first Audiobookshelf bootstrap.

## Secrets and site values

Live credentials are **SOPS-encrypted** (`*.sops.env`) with age. Site-specific domain, LAN/VPS
IPs, and household emails live in `secrets/site.sops.env`. Plaintext siblings are gitignored.

```bash
nix develop                 # age, sops, ansible, gitleaks, …
cp secrets/site.env.example secrets/site.env   # first clone
# decrypt existing encrypted files (needs .age/workstation.key):
./scripts/secrets.sh decrypt
./scripts/secrets.sh encrypt                   # after editing plaintext
```

`./deploy.sh` decrypts and renders `*.tmpl` files before syncing. See [docs/secrets-rotation.md](docs/secrets-rotation.md).

## Day-to-day deploy

Workstation (or the CT 105 deploy agent at 02:00 America/New_York):

```bash
./deploy.sh arrstack          # media stack + declarative bootstrap + recyclarr + configarr
./deploy.sh identity          # Authentik + Caddy (after arrstack)
./deploy.sh jellyfin          # Jellyfin (isolated; plugins + LDAP bootstrap)
./deploy.sh obsv              # observability stack on CT 100
./deploy.sh pve               # node_exporter, crons, backup timer (manual — DNS-safe)
./deploy.sh adguard           # filter lists onto CT 102 (manual — brief DNS blip)
./deploy.sh --dry-run obsv pve  # print commands, change nothing
```

Manual early deploy from GitHub: Actions → **deploy-now**. From the LAN:
`ssh deployer homelab-deploy --now [targets]`. Host substrate: `infra/` (Ansible).

Validate locally before shipping:

```bash
docker compose -f docker-compose.yaml --env-file .env config >/dev/null
python3 scripts/check_env.py --env-file .env
python3 -m unittest discover -s scripts/tests
```

## Documentation map

| Doc | Contents |
| --- | --- |
| [docs/identity/deploy.md](docs/identity/deploy.md) | Authentik, Caddy, Pangolin OIDC, Jellyfin LDAP |
| [docs/jellyfin/deploy.md](docs/jellyfin/deploy.md) | Jellyfin compose project, plugins, LDAP bootstrap |
| [docs/runbooks/jellyfin-migrate.md](docs/runbooks/jellyfin-migrate.md) | Historical CT 101 → CT 104 migration (CT 101 destroyed) |
| [docs/arrstack.md](docs/arrstack.md) | Services, paths, first-time UI steps, usenet shopping list |
| [docs/observability/deploy.md](docs/observability/deploy.md) | Observability stack deploy + fire-drill |
| [docs/runbooks/backup-and-restore.md](docs/runbooks/backup-and-restore.md) | What is backed up, restore examples |
| [docs/hosts/](docs/hosts/) | Per-host inventories (pve, truenas, adguard, pangolin, …) |
| [AGENTS.md](AGENTS.md) | Conventions for agents/operators working in this repo |
| [docs/upstream-mediastack.md](docs/upstream-mediastack.md) | Upstream reference only — not this deployment |

## What is in the arrstack

Sonarr, Radarr, Lidarr, Prowlarr, FlareSolverr, Bazarr, Gluetun, qBittorrent (VPN-only via Gluetun), SABnzbd, Seerr, Bindery, Audiobookshelf, Mixarr, Mealie, mealie-og-sync, Recyclarr, Configarr, Homepage, Portainer. Tdarr is parked behind `docker compose --profile tdarr up -d`. Jellyfin is a **separate** Compose project under `jellyfin/` on **CT 104**. See [docs/jellyfin/deploy.md](docs/jellyfin/deploy.md).
