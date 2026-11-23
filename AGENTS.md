# AGENTS.md

## What this repo is

A personal customization of the [geekau/mediastack](https://github.com/geekau/mediastack) project: a
Docker Compose media automation stack (*ARR apps + downloaders behind a VPN). Most of the tree is
configuration. Helper programs live under `scripts/` (and `adguard/`, `obsv/*/`), with tests in
`scripts/tests/` (`python3 -m unittest discover -s scripts/tests`).

Key files for the media stack:

| File / path | Role |
| --- | --- |
| `docker-compose.yaml` | Core stack (default profile). Tdarr is profile `tdarr`. |
| `.env` / `.env.sops.env` | Compose values. Plaintext `.env` is gitignored; commit the SOPS sibling. |
| `secrets/site.sops.env` | Domain, LAN/VPS IPs, household emails — rendered into `*.tmpl` on deploy. |
| `arrstack/recyclarr.yml` | TRaSH Guides sync for Sonarr/Radarr (Recyclarr sidecar). |
| `arrstack/configarr/config.yml` | Download clients, root folders, hardlink on the media share (Configarr). |
| `arrstack/prowlarr/bootstrap.yml` | Prowlarr apps, indexers, FlareSolverr — `scripts/apply_arrstack_bootstrap.py`. |
| `scripts/seed_arrstack_keys.py` | Seeds deterministic API keys from `.env` into config volumes + `arrstack.auto.env`. |
| `scripts/sync_arrstack_api_keys.py` | Syncs keys from config volumes to `arrstack.auto.env` on deploy. |
| `arrstack/bazarr/bootstrap.yml` | Bazarr Sonarr/Radarr links, languages, providers — same bootstrap script. |
| `arrstack/bindery/bootstrap.yml` | Bindery Prowlarr sync, root folder, download client — same bootstrap script. |
| `arrstack/audiobookshelf/bootstrap.yml` | Audiobookshelf init, API key, library — same bootstrap script. |
| `arrstack/homepage/*.yaml` | Homepage tiles, widgets, Docker health — copied on `./deploy.sh arrstack`. |
| Mealie (`docker-compose.yaml`) | Self-hosted recipe manager and meal planner; shopping lists are staging only — OurGroceries owns shopping. |
| `mealie-og-sync` (`docker-compose.yaml`) | Phone bookmark sync: Mealie staging list → OurGroceries store lists via `scripts/sync_mealie_ourgroceries.py`. |
| `arrstack/mealie-og-sync.yaml` | Default store + optional overrides; OG history handles per-item routing. |
| `restart_no_sudo.sh` | Nuclear redeploy (prune + image cleanup). Prefer `./deploy.sh arrstack`. |
| `docs/arrstack.md` | Stack overview, usenet shopping list, first-time steps. |

Observability is a **separate** Compose project under `obsv/`. Do not add Vector, VictoriaLogs,
VictoriaMetrics, Grafana, Dozzle, monocker, vmalert, or the Discord relay to `docker-compose.yaml`.
See `docs/observability/deploy.md`. Ship it with `./deploy.sh obsv`.

Identity (Authentik + Caddy) is a **separate** Compose project under `identity/`. LAN HTTPS and
forward-auth for admin UIs; OIDC for household apps; LDAP outpost for Jellyfin. See
`docs/identity/deploy.md`. Ship with `./deploy.sh identity` after `./deploy.sh arrstack`.

Jellyfin is a **separate** Compose project under `jellyfin/` on **CT 104**
(`jellyfin-docker`, `<jellyfin-host>`). The native CT 101 guest was destroyed after soak.
Ship with `./deploy.sh jellyfin`. Do not merge Jellyfin into arrstack's `docker-compose.yaml`
or run it on CT 100.
node_exporter stays on the pve host (`./deploy.sh pve`); do not run it inside CT 100.

`docs/upstream-mediastack.md` is the unmodified 1,282-line upstream MediaStack documentation. It describes many
applications (Traefik, Authentik, Jellyfin, Plex, Headscale, CrowdSec, Grafana, and others) that are
**not** in this `docker-compose.yaml`. Treat it as reference material for the upstream project, not
as a description of this deployment. Do not assume a service exists because the upstream doc mentions it.
The root [README.md](README.md) is the entry point for this homelab.

## The deployment lives at `/mnt/config`, not here

Both restart scripts begin with `FOLDER_FOR_YAMLS=/mnt/config` and `cd` there before doing anything.
Editing files in this workspace has **no effect** on the running stack until they are copied to
`/mnt/config`. When asked to change the stack, edit here and tell the user the files need to be
synced to `/mnt/config`, or ask whether they want you to copy them.

The restart scripts optionally `cp` legacy mediastack files when present in `/mnt/config`
(`headplane-config.yaml`, `headscale-config.yaml`, `traefik-*.yaml`, `crowdsec-acquis.yaml`).
They are not in this repo; missing files are skipped (no abort).

## Deploying

Use `./deploy.sh <target>` from this repo. It never runs `restart_no_sudo.sh`.
Most targets rsync via pve (`root@<pve-host>`) and act on CT 100; `adguard`
pushes filter config into CT 102 instead.

| Command | What it does |
| --- | --- |
| `./deploy.sh obsv` | Observability stack + `obsv/.env` |
| `./deploy.sh identity` | Authentik + Caddy on CT 100 + AdGuard DNS rewrites (if `adguard` in same run) |
| `./deploy.sh jellyfin` | Jellyfin on CT 104 (isolated compose + plugin/LDAP bootstrap) |
| `./deploy.sh arrstack` | Compose, restart scripts, `arrstack/` configs, seed keys, `compose up -d`, bootstrap |
| `./deploy.sh pve` | node_exporter, LVM textfile, thin-pool cron, webhook file on the hypervisor |
| `./deploy.sh adguard` | `adguard/filters.yaml` + `dns-rewrites.yaml` onto CT 102, brief restart |
| `./deploy.sh --dry-run obsv pve` | Print commands, change nothing |

`restart_no_sudo.sh` remains the nuclear option: it prunes stopped containers, unused
volumes, unused networks, and — if every arrstack service comes up — `docker image prune -a -f`.
Do not run it to pick up a small config change. `./deploy.sh arrstack` is the path for that.

`restart.sh` is the older sudo-based copy and has drifted from `restart_no_sudo.sh`.
Neither copies `buildarr.yml` anymore (Buildarr removed; use Recyclarr + Configarr).

Validate changes without deploying:

```bash
docker compose config >/dev/null
python3 scripts/check_env.py --env-file .env
python3 -m unittest discover -s scripts/tests
```

Most variables in `docker-compose.yaml` use `${VAR:?err}`. Gluetun's server-selection and
WireGuard fields are optional and are not marked required. Run the checks above after any edit
to a compose file or `.env`.

## Secrets: read before touching git

Committed secrets are **SOPS** files (`*.sops.env`, `secrets/site.sops.env`). Age private keys
live in `.age/` (gitignored) or `/etc/homelab/age.key` on the deploy agent. Decrypt with
`./scripts/secrets.sh decrypt` before deploying.

`.gitignore` covers plaintext `.env`, `.env.bak.*`, `**/secrets.yml`, `**/arrstack.auto.env`,
`**/credentials.env`, `secrets/site.env`, rendered site templates, and Authentik blueprints that
embed client secrets or the Discord webhook. Never `git add` those.

The **private** local history (`pre-gitops-baseline` / old `init`) must never be pushed — a
`.git/hooks/pre-push` blocks it. The public GitHub remote is a separate orphan history.

`./deploy.sh` decrypts SOPS, renders `*.tmpl`, then rsyncs plaintext `.env` to `/mnt/config/.env`
(atomic replace; safe on CIFS). API keys go to `${FOLDER_FOR_DATA}/arrstack.auto.env` instead.
Generated OIDC values go to host-side `arrstack.oauth.env` / `obsv/oauth.auto.env`, not back into
the SOPS-managed `.env` files.

When writing documentation, commit messages, or chat output, refer to these by variable name
(`VPN_PASSWORD`, `api_key`) rather than reproducing the values. See [docs/secrets-rotation.md](docs/secrets-rotation.md).

## Network topology

One bridge network, `mediastack`, on `172.28.10.0/24` with gateway `172.28.10.1`.

Only `qbittorrent` uses `network_mode: "service:gluetun"`. SABnzbd is on `mediastack`
with its own `ports:` (usenet over SSL does not need VPN).

- qBittorrent's WebUI, listen port, and Gluetun's control server are published on `gluetun`,
  bound to `127.0.0.1`. Gluetun's HTTP proxy and Shadowsocks server are off. Caddy reaches the
  WebUI via `gluetun:WEBUI_PORT_QBITTORRENT` on `mediastack` (qBit has no Docker DNS name;
  `host.docker.internal` cannot hit a loopback bind).
- Other arrstack UIs bind `127.0.0.1` on the host. Humans use **Caddy** at `https://<app>.<lan-domain>`
  (AdGuard split-horizon DNS). Pangolin terminates public TLS for Jellyfin, Seerr, and `auth.<domain>`.
- Grafana and Dozzle join `mediastack` so Caddy can proxy them by name. Their host ports, and
  VictoriaLogs / VictoriaMetrics, bind `127.0.0.1` only.

Declarative sidecars (`recyclarr.yml`, `configarr/config.yml`, bootstrap scripts) reach *ARR apps on
the `mediastack` Docker network (`http://sonarr:8989`, etc.). qBittorrent download clients use
`gluetun:WEBUI_PORT_QBITTORRENT` (same netns as Gluetun; host gateway `172.28.10.1` is unreachable
because UI ports bind `127.0.0.1`).

## Conventions in `docker-compose.yaml`

- Every service is preceded by a boxed `###...##` comment block naming the image maintainer, the
  service's function, and a documentation URL. Match this format when adding a service.
- Configurable values come from `.env`. Required ones use `${VAR:?err}`. Do not hardcode ports,
  paths, UIDs, or timezones inline.
- Standard volume layout: `${FOLDER_FOR_MEDIA}:/data`, `${FOLDER_FOR_DOWNLOADS}:/downloads`,
  `${FOLDER_FOR_DATA}/<service>:/config`. Tdarr deviates and mounts subpaths.
- LinuxServer.io images carry theme.park mods (`DOCKER_MODS`, `TP_THEME`, `TP_COMMUNITY_THEME`).
- `homepage` and `portainer` stay on `mediastack` (never `network_mode: service:gluetun`). Both
  mount the Docker socket.
- New `${...}` variables must be added to `.env`, and any new `${FOLDER_FOR_DATA}` subdirectory must
  be added to the `mkdir -p` brace expansion in both restart scripts, or the deploy will create it
  as root-owned and the container will fail on permissions.

## Known-incomplete configuration

- API keys live in `${FOLDER_FOR_DATA}/arrstack.auto.env` (auto-synced); VPN creds stay in `/mnt/config/.env`.
- `SABNZBD_API_KEY` and usenet provider/indexer credentials are filled in after purchase.
- Mixarr `SESSION_SECRET` is auto-generated in `arrstack.auto.env` on first sync.
- Seerr reuses the `jellyseerr` config volume; ownership must be `1000:1000` (see `docs/arrstack.md`).

Compose images are pinned as `tag@sha256:…`. Renovate opens version/digest PRs; the deploy agent
rolls them out at 02:00. Host substrate (LXC guests, mounts, VPS baseline) lives under `infra/`.
