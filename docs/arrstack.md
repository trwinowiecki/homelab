# Arrstack (approach A)

Declarative media automation stack on CT 100. Jellyfin is a **separate** Compose project under
`jellyfin/` on **CT 104** (production). See [jellyfin/deploy.md](jellyfin/deploy.md).
Observability is a **separate** Compose project under `obsv/`.

## Core services (default `docker compose up -d`)

| Service | Role |
| --- | --- |
| Gluetun + qBittorrent | Torrent downloads (VPN only) |
| SABnzbd | Usenet downloads (direct, no VPN) |
| Sonarr / Radarr / Lidarr | TV, movies, music |
| Prowlarr + FlareSolverr | Indexers |
| Bazarr | Subtitles |
| Seerr | Requests (replaces Jellyseerr) |
| Recyclarr | TRaSH Guides sync for Sonarr/Radarr |
| Configarr | Download clients, root folders, copy-not-hardlink |
| Bindery | Audiobook acquisition (Readarr replacement) |
| Audiobookshelf | Phone playback |
| Mixarr | Lidarr music discovery |
| Mealie | Recipe manager and meal planner (shopping via OurGroceries) |
| Homepage | Dashboard tiles (YAML in git) |
| Portainer | Container restart / stack management |

**Parked:** Tdarr (`docker compose --profile tdarr up -d`).

**Removed:** Buildarr, Jellyseerr image, Readarr, Mylar, Whisparr, Unpackerr.

## Storage (F-04)

Torrent downloads live on CT 100 SSD (`/arrstack/downloads`) and stay there while seeding.
Library is CIFS (`/mnt/media`). Usenet completes onto that share (`/mnt/media/usenet/complete`,
container path `/data/usenet/complete`); the RAR parts stay on the SSD until unpack finishes.
Configarr enables hardlinks. Sonarr/Radarr hardlink a usenet import on the share, and copy
a torrent import because `/downloads` and `/data` are different filesystems.

Download layout:

```
/arrstack/downloads/torrents/{complete,incomplete}
/arrstack/downloads/torrents/complete/{tv-sonarr,radarr,lidarr,audio}
/arrstack/downloads/usenet/incomplete
/mnt/media/usenet/complete/{tv,movies,music,audio}
```

qBittorrent categories (configure in WebUI): `tv-sonarr`, `radarr`, `lidarr`, and **`audio`**
each save under `/downloads/torrents/complete/<category>` (container paths).

SABnzbd categories (configure in UI or after first boot): `tv`, `movies`, `music`, `audio`
mapped to folders under `/data/usenet/complete`. Direct unpack runs one job at a time and
pauses downloading while that unpack runs.

### Books (Bindery + Audiobookshelf)

Same two-stage pattern as Sonarr/Radarr: download on SSD, library on NAS.

| Stage | Path |
| --- | --- |
| SSD download | `/arrstack/downloads/torrents/complete/audio` (qBit category `audio`) |
| NAS library | `$FOLDER_FOR_MEDIA/media/books` (host), `/books` in Bindery and Audiobookshelf containers |

Bindery imports (copy) from the completed download into `/books`. Audiobookshelf scans the
same folder for playback. Epubs and audiobooks share `media/books` (Bindery Storyteller layout).

If a Bindery import fails with a path-remap error, fix the download client in Bindery Settings
(or re-run bootstrap), then use **Queue → Retry import** on failed items — no re-grab needed.
Bootstrap sets `path_remap` to map host download paths (`FOLDER_FOR_DOWNLOADS`) to `/downloads`
inside the Bindery container when qBittorrent reports host paths.

If content was previously under `media/audio`, move it to `media/books` on the NAS once.

## Network

Single bridge `mediastack` (`172.28.10.0/24`, gateway `172.28.10.1`). *ARR apps and
declarative sidecars reach each other via **container DNS** (`http://sonarr:8989`, etc.).
qBittorrent shares Gluetun's network namespace, so download clients use `gluetun:8200`.
Do not use `172.28.10.1` — UI ports bind `127.0.0.1` only (Caddy is the LAN front door).

Only qBittorrent uses `network_mode: service:gluetun`. SABnzbd is on `mediastack` with
its own `ports:` — usenet over SSL does not need the VPN.

qBittorrent must bind BitTorrent to Gluetun's `tun0` (`Session\Interface=tun0` in
`qbittorrent/qBittorrent/qBittorrent.conf`, or WebUI *Connection → Network interface*).
Leaving the interface unset binds `6881` to the Docker bridge (`eth0` /
`172.28.10.x`). Gluetun's kill-switch then drops UDP trackers and DHT with
`Operation not permitted`, and torrents sit in `metaDL` with zero peers. Do not pin
`10.5.0.2`; that VPN address can change on reconnect. After a Gluetun recreate, confirm
the listen socket is still on `tun0` (`docker exec gluetun awk '$2 ~ /:1AE1$/ {print}' /proc/net/udp`).

## Declarative config (in git)

| Path | Applied by |
| --- | --- |
| `arrstack/recyclarr.yml` | `recyclarr` container (daily cron) |
| `arrstack/configarr/config.yml` | `docker compose run --rm configarr` |
| `arrstack/prowlarr/bootstrap.yml` | `scripts/apply_arrstack_bootstrap.py` |
| `arrstack/sabnzbd/bootstrap.yml` | `scripts/apply_arrstack_bootstrap.py` |
| `arrstack/bazarr/bootstrap.yml` | `scripts/apply_arrstack_bootstrap.py` |
| `arrstack/bindery/bootstrap.yml` | `scripts/apply_arrstack_bootstrap.py` |
| `arrstack/audiobookshelf/bootstrap.yml` | `scripts/apply_arrstack_bootstrap.py` |
| `arrstack/seerr/bootstrap.yml` | `scripts/apply_arrstack_bootstrap.py` |
| `arrstack/homepage/*.yaml` | copied to `${FOLDER_FOR_DATA}/homepage` on deploy |

Copy `.env.example` → `.env`, or merge an existing `.env` that has drifted:

```bash
python3 scripts/merge_env.py --dry-run    # preview
python3 scripts/merge_env.py --write      # merge + auto-fill keys (backs up .env)
```

To reuse keys already on CT 100 instead of generating new ones:

```bash
ssh root@<pve-host> 'pct exec 100 -- python3 /mnt/config/scripts/sync_arrstack_api_keys.py --env-file /mnt/config/.env'
ssh root@<pve-host> 'pct exec 100 -- cat /arrstack/config/arrstack.auto.env' > /tmp/arrstack.auto.env
python3 scripts/merge_env.py --import-auto-env /tmp/arrstack.auto.env --write
```

Copy `.env.example` → `.env`. API keys are **deterministic**: set once in `.env`
(`openssl rand -hex 16`), seeded into config volumes by `scripts/seed_arrstack_keys.py`,
and synced to `arrstack.auto.env` for sidecars. Never commit `.env`.

**Manual UI only:** Jellyfin admin login for Seerr first admin, Mixarr
(Lidarr link, session-cookie auth).

### API keys (automated)

`scripts/seed_arrstack_keys.py` runs on `./deploy.sh arrstack` **before** `compose up`.
It writes `.env` keys into each app's config volume (`config.xml` for *arr,
`config/config.yaml` for Bazarr) and `${FOLDER_FOR_DATA}/arrstack.auto.env`.
Deploy sources `arrstack.auto.env` before `compose up` so Servarr `__AUTH__APIKEY`
env vars match even when `.env` still has placeholders.

`scripts/sync_arrstack_api_keys.py` re-reads keys from disk after containers boot
and updates `arrstack.auto.env` (Homepage vars, Mixarr `SESSION_SECRET`, etc.).

| Env var | Seeded from `.env` into |
| --- | --- |
| `SONARR_API_KEY` | `sonarr/config.xml` + `SONARR__AUTH__APIKEY` |
| `RADARR_API_KEY` | `radarr/config.xml` + `RADARR__AUTH__APIKEY` |
| `LIDARR_API_KEY` | `lidarr/config.xml` + `LIDARR__AUTH__APIKEY` |
| `PROWLARR_API_KEY` | `prowlarr/config.xml` + `PROWLARR__AUTH__APIKEY` |
| `BAZARR_API_KEY` | `bazarr/config/config.yaml` |
| `BINDERY_API_KEY` | compose `BINDERY_API_KEY` (seed-only) |
| `SABNZBD_API_KEY` | read from `sabnzbd/sabnzbd.ini` after SAB first boot |
| `QBITTORRENT_API_KEY` | `qbittorrent/qBittorrent/qBittorrent.conf` (`WebUI\APIKey`) |
| `SEERR_API_KEY` | read from `jellyseerr/settings.json` → `main.apiKey` |
| `JELLYFIN_API_KEY` | read from `jellyseerr/settings.json` → `jellyfin.apiKey` (or `.env` override) |
| `PORTAINER_API_KEY` | minted by bootstrap from `PORTAINER_ADMIN_PASSWORD`, or paste once |
| `SESSION_SECRET` | auto-generated once for Mixarr |
| `AUDIOBOOKSHELF_API_KEY` | created by bootstrap script, stored in `arrstack.auto.env` |
| `HOMEPAGE_VAR_*` | aliases of the above + Grafana creds from `obsv/.env` + `TRUENAS_API_KEY` |

Set `AUDIOBOOKSHELF_USERNAME` / `AUDIOBOOKSHELF_PASSWORD` in `.env` for Audiobookshelf
first-run (`POST /init`).

`./deploy.sh arrstack` runs seed → sync → compose up → sync → bootstrap automatically.

`apply_arrstack_bootstrap.py` also reads keys from config volumes when env vars
are empty or still `CHANGE_ME`. On each deploy it **refreshes** stored API keys in
Seerr (`settings.json`), Prowlarr (Applications), and Bindery (Prowlarr instance)
when those services already exist — seed alone only updates the *arr apps themselves.

### Homepage (widgets + health)

`arrstack/homepage/` is copied to `${FOLDER_FOR_DATA}/homepage` on deploy:

| File | Role |
| --- | --- |
| `services.yaml` | Tiles, service widgets, Docker health, HTTP siteMonitor |
| `settings.yaml` | Theme; `statusStyle: dot` for health indicators |
| `docker.yaml` | Local Docker socket for container running/unhealthy status |

Widget API keys are **not** in YAML — Homepage reads `HOMEPAGE_VAR_*` from
`arrstack.auto.env` (synced on deploy). Grafana admin password is pulled from
`sibling` `obsv/.env` on CT 100 (`GRAFANA_ADMIN_PASSWORD`).

**First-time / manual:**

- **Jellyfin widget:** needs `jellyfin.apiKey` in Seerr settings (created during Seerr
  Jellyfin setup). Optional `JELLYFIN_API_KEY` in `.env` overrides sync.
- **Portainer widget:** set `PORTAINER_ADMIN_PASSWORD` in `.env` once so bootstrap can
  mint `PORTAINER_API_KEY`, or paste a token from Portainer → My account → Access tokens.
- **Portainer OAuth:** bootstrap applies OAuth settings using `PORTAINER_ADMIN_PASSWORD` or an existing `PORTAINER_API_KEY` in `arrstack.auto.env`. Redirect URI is `https://portainer.<lan-domain>/` (Portainer 2.x); Caddy bypasses forward-auth when `?code=` is present.
- **qBittorrent widget:** requires qBittorrent ≥ 5.2. Seed writes `WebUI\APIKey` to conf;
  restart `qbittorrent` after first seed if the container was already running.

Tiles without official widgets (Mixarr, Bindery, Dozzle, Homepage) still show Docker +
HTTP health only.

## Deploy

From this repo:

```bash
docker compose config >/dev/null          # validate locally
./deploy.sh arrstack                      # rsync to CT 100, compose up -d
```

`./deploy.sh arrstack` syncs `docker-compose.yaml`, `.env`, restart scripts, and `arrstack/` configs.
Edit `.env` in this repo, then deploy — rsync replaces `/mnt/config/.env` atomically (safe on CIFS).

Avoid `restart_no_sudo.sh` for small changes (it prunes volumes/networks and may run
`docker image prune -a`).

### Gluetun unhealthy (qBittorrent will not start)

Only qBittorrent depends on Gluetun. Everything else (SAB, *ARR, Seerr, etc.) can run
without it.

Common causes:

0. **`FIREWALL_OUTBOUND_SUBNETS` / `bad bits after slash` in `docker logs gluetun`** — Gluetun
   crash-loops before VPN connects. Usually `LOCAL_SUBNET` has an inline comment absorbed into
   the value (e.g. from quoting `<lan-cidr> # comment`). Put comments on their own line
   above the variable; run `python3 scripts/check_env.py --env-file .env` before deploy.
1. **`.env` quoting** — quote only when the value itself contains a space (e.g.
   `SERVER_COUNTRIES="United States"`). Never put inline comments inside quoted values; Docker
   Compose preserves quoted content verbatim. Unquoted inline comments are stripped by Compose
   but break `source .env` in shell scripts — prefer comments on the line above.
2. **`AUTH_FAILED` in `docker logs gluetun`** — Nord rejected the service credential
   pair. Update `VPN_USERNAME` / `VPN_PASSWORD` in `/mnt/config/.env` with fresh Nord
   **service credentials** (not login email/password). Regenerating creds in Nord's
   dashboard invalidates the old password immediately.
   ```bash
   docker logs gluetun 2>&1 | tail -20   # look for AUTH_FAILED
   docker compose up -d --force-recreate gluetun
   ```
3. **Stale server cache** — if OpenVPN hangs at `UDPv4 link remote` (no AUTH_FAILED):
   ```bash
   docker compose stop gluetun qbittorrent
   rm -f /arrstack/config/gluetun/servers.json
   docker compose up -d gluetun
   docker logs -f gluetun
   docker compose up -d qbittorrent
   ```
4. **UDP blocked** — set `OPENVPN_PROTOCOL=tcp` in `.env` and recreate Gluetun.

Deploy waits up to 60s for Gluetun, then continues without qBittorrent if still unhealthy.

## First-time / migration notes

### Seerr (from Jellyseerr)

- Image: `ghcr.io/seerr-team/seerr:latest`, port `5055`, reuses `${FOLDER_FOR_DATA}/jellyseerr`.
- Published on both `127.0.0.1:5055` (bootstrap on CT 100) and
  `${LOCAL_DOCKER_IP}:5055` (Pangolin Newt for `https://seerr.<domain>`).
  LAN humans use `https://seerr.<lan-domain>` (Caddy).
- **Port conflict:** if deploy fails with `Bind for 0.0.0.0:5055 failed`, the old Jellyseerr
  container is still running. Remove it: `docker rm -f jellyseerr`, then re-run deploy.
  (`./deploy.sh arrstack` does this automatically.)
- Seerr runs as UID **1000**. Before first start:  
  `chown -R 1000:1000 /arrstack/config/jellyseerr`  
  (`deploy.sh arrstack` does this when the dir exists.)
- Complete Seerr first-run with Jellyfin admin login at `http://<jellyfin-host>:8096` (one-time;
  end users still sign in via Jellyfin afterward).
- Set `SEERR_API_KEY` in `.env` (`openssl rand -hex 16`). Deploy seeds it into
  `jellyseerr/settings.json` when that file exists and passes it to the container as `API_KEY`.
- After initialization, `./deploy.sh arrstack` syncs main settings (`applicationUrl` =
  `https://seerr.<domain>`, `localLogin: false`) and four Sonarr/Radarr service entries
  (HD + 4K) from `arrstack/seerr/bootstrap.yml` with TRaSH profile names.
- Friends bookmark **`https://seerr.<domain>`** and log in with their Jellyfin account
  (Pangolin SSO off). Do not point `seerr.<domain>` at Caddy on LAN — hairpin through Pangolin
  for a valid LE cert, same as Jellyfin.

### TRaSH quality profiles (Recyclarr)

Recyclarr v8 syncs guide-backed profiles from [arrstack/recyclarr.yml](../arrstack/recyclarr.yml).
Each deploy runs `recyclarr sync`, then prunes any other quality profiles:

| App | Profiles kept |
| --- | --- |
| Sonarr | `WEB-1080p`, `WEB-2160p`, `Any`, `Low (720p)` |
| Radarr | `HD Bluray + WEB`, `UHD Bluray + WEB`, `Any`, `Low (720p)` |

`Low (720p)` is a manual Recyclarr profile (not TRaSH-backed). Assign it per item in
Sonarr/Radarr or Seerr's advanced request options. Changing an existing item to this
profile does **not** shrink files — Sonarr/Radarr never downgrade. Delete the files and
re-search. After media snapshots are excluded (F-37), that delete frees NAS space immediately.

Seerr HD/4K routing (after init):

| Seerr instance | Quality profile |
| --- | --- |
| Sonarr (HD) | `WEB-1080p` |
| Sonarr 4K | `WEB-2160p` |
| Radarr (HD) | `HD Bluray + WEB` |
| Radarr 4K | `UHD Bluray + WEB` |

Profiles still assigned to existing series/movies are not deleted (prune logs a warning).

### Recyclarr

After API keys are synced to `arrstack.auto.env` (automatic on deploy):

```bash
docker compose run --rm recyclarr sync
```

### Configarr

One-shot apply (download clients + root folders):

```bash
docker compose run --rm configarr
```

Set `SABNZBD_API_KEY` via sync after SABnzbd first boot, or leave empty until then.

### Prowlarr + SABnzbd + Bazarr + Bindery + Audiobookshelf bootstrap

`./deploy.sh arrstack` runs this automatically after services are up. Manual run on CT 100:

```bash
python3 /mnt/config/scripts/apply_arrstack_bootstrap.py --env-file /mnt/config/.env --wait
```

Indexer API keys (`NZBGEEK_API_KEY`, etc.) are optional until you purchase indexers.
NZBGeek is enabled in `arrstack/prowlarr/bootstrap.yml` and skipped when `NZBGEEK_API_KEY` is empty.

### Bindery

Registers Prowlarr (syncs indexers), adds `/books` root folder (NAS `media/books`), and
qBittorrent download client with category `audio` from `arrstack/bindery/bootstrap.yml`.
Set `BINDERY_API_KEY` in `.env`.

### Audiobookshelf

Creates root user, API key, and Audiobooks library at `/books` (same NAS folder as Bindery)
from `arrstack/audiobookshelf/bootstrap.yml`. Set `AUDIOBOOKSHELF_USERNAME` /
`AUDIOBOOKSHELF_PASSWORD` in `.env`.

### Mixarr

Set `MIXARR_SESSION_SECRET` (`openssl rand -hex 32`) and point Mixarr at Lidarr in its UI.

## Recipes and shopping

**Mealie** (`https://mealie.<lan-domain>`) is the self-hosted recipe manager:
URL import, meal calendar, and a staging shopping list for meal-plan ingredients. Data lives in
`${FOLDER_FOR_DATA}/mealie` — include it in homelab backups.

**OurGroceries** (hosted, [ourgroceries.com](https://www.ourgroceries.com)) is the household
shopping system of record: per-store lists, item-to-store memory, staples (cat food, pharmacy,
etc.), and custom in-store item order. Mealie does not replace OurGroceries for day-to-day shopping.

### First-time Mealie setup

1. Set `MEALIE_ALLOW_SIGNUP=true` in `.env`, deploy (`./deploy.sh arrstack`), create the admin
   account in the Mealie UI.
2. Set `MEALIE_ALLOW_SIGNUP=false`, redeploy.
3. In Mealie, create a shopping list named e.g. `Weekly Meals (staging)` for meal-plan exports.

### OurGroceries household setup

1. Create one list per store (e.g. `Meijer`, `Trader Joes`).
2. Settings → **When adding an item with no category** → **Leave it uncategorized** (for custom
   walking-path order via drag-and-drop).
3. Seed staples on the correct store lists; OurGroceries remembers which list each item belongs to.
4. Install the OurGroceries app on household phones.

### Mealie → OurGroceries sync

The `mealie-og-sync` container exposes a LAN-only HTTP trigger on
`${WEBUI_PORT_MEALIE_OG_SYNC}` (default `9926`). It pushes unchecked items from the Mealie
staging list to OurGroceries, using **OG item history** for store routing (chicken → Meijer if
that is where you always buy it). YAML only needs `default_list` for brand-new items plus
optional `overrides`.

**One-time setup**

1. Mealie → Profile → API Tokens → create a long-lived token → `MEALIE_API_TOKEN` in `.env`.
2. Copy `arrstack/mealie-og-sync/credentials.env.example` →
   `${FOLDER_FOR_DATA}/mealie-og-sync/credentials.env` and set your OurGroceries email
   and password. **Use this file, not root `.env`** — `$` in passwords breaks Docker
   Compose when credentials live in the main `.env`.
3. Copy `arrstack/mealie-og-sync.yaml.example` → `arrstack/mealie-og-sync.yaml` (set
   `default_list` to your main store).
4. Set `MEALIE_OG_SYNC_TOKEN` (`openssl rand -hex 24`) in `.env` if it is still `CHANGE_ME`.
   Deploy does not generate it.

**Phone bookmark:** `https://sync.<lan-domain>/sync?token=<MEALIE_OG_SYNC_TOKEN>` (also on
Homepage as **Sync Groceries**, behind Authentik). The host port is bound to `127.0.0.1`, so
`http://<LAN-IP>:9926` does not answer. The sync service logs the path only, not the query string.

CLI: `python3 scripts/sync_mealie_ourgroceries.py --env-file /mnt/config/.env`

### Weekly workflow

1. Plan meals in Mealie.
2. Add the week's meals to the Mealie staging shopping list; adjust quantities and remove pantry
   items.
3. Tap **Sync Groceries** on Homepage or open the phone bookmark — items land on the correct OG
   store lists; staging list clears.
4. Add non-meal items directly in OurGroceries anytime during the week.
5. Shop from the per-store OurGroceries list in your saved item order.

## Usenet shopping list

You need **two** paid pieces:

1. **Provider** (backbone / block account) — SSL usenet servers. Examples: Eweka,
   Newshosting, UsenetExpress. Prioritize retention and completion rate.
2. **Indexer** (Newznab API) — what Prowlarr searches. Examples: NZBGeek, DrunkenSlug,
   NZBPlanet.

Put provider credentials in `.env` (`USENET_HOST`, `USENET_PORT`, `USENET_USERNAME`,
`USENET_PASSWORD`, `USENET_SSL`, `USENET_CONNECTIONS`) and the indexer key in
`NZBGEEK_API_KEY`. `./deploy.sh arrstack` adds the SABnzbd server (SSL on port 563,
strict certificate check, 50 connections) and categories (`tv`, `movies`, `music`,
`audio`), registers NZBGeek in Prowlarr at indexer priority 1, and adds SABnzbd as
download-client priority 1 in Sonarr, Radarr, and Lidarr. Those apps prefer Usenet
and wait 60 minutes before grabbing a torrent. qBittorrent stays at priority 2.
After a successful import, Sonarr, Radarr, and Lidarr delete the SABnzbd job and its
files. A local unpack abort (`sabnzbd.org/not-complete`) is blocklisted and re-searched;
when that was the only release, `qbit-ssd-cleanup.py` clears the blocklist once and
searches again. A second failure of the same release stays blocklisted. The cleanup
script also removes a completed usenet folder left on the SSD once the same file is
already on the NAS and SABnzbd's queue no longer references it.

Do not commit provider or indexer credentials to git.

## Compose profiles

| Profile | Services |
| --- | --- |
| *(default)* | Core stack above |
| `tdarr` | Tdarr server + node (parked until QSV transcode on CT 100) |

## Related docs

- `AGENTS.md` — repo conventions, deploy model, secrets warning
- `docs/observability/deploy.md` — separate observability stack (`./deploy.sh obsv`)
