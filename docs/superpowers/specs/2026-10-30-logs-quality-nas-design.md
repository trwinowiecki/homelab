# Log alerts, Low (720p) profile, NAS stats, and library space

Date: 2026-10-30
Status: implemented in-repo; deploy separately
Implements: F-37 (media snapshots), F-38 (log watch), F-39 (Low 720p), F-40 (NAS homepage + library space)

## Problem

Four related gaps:

1. Deleting media in Sonarr/Radarr does not free TrueNAS space. Recursive ZFS snapshot tasks on `metrion/zinthos` include `media`. Media is re-downloadable, so those snapshots should not exist.
2. Vector already ships every CT 100 container log to VictoriaLogs. Nothing pages on error or warn lines. Lifecycle alerts (unhealthy, exit, restart storm) already exist.
3. Recyclarr only syncs WEB-1080p / WEB-2160p / Any (and the Radarr HD/UHD equivalents). Kids shows and other low-care titles still grab 1080p/2160p.
4. Homepage has no TrueNAS tile. There is no library-size view that joins *arr `sizeOnDisk` with Jellyfin watch state and ZFS used-vs-snapshot space.

## Non-goals

- Snapshotting or backing up media.
- Auto-deleting library items (Janitorr/Maintainerr). The Grafana dashboard is read-only.
- Extra Kids root folders, tags, or Seerr per-user default profiles.
- Vector on CT 104 (Jellyfin). Log-watch is CT 100 only; Jellyfin is a follow-up.
- Second Discord webhook. Everything pages through the existing `discord-relay`.
- TrueNAS Apps, copyparty (F-32), or new NAS mounts on arrstack.

## Architecture

```
metrion/zinthos  --recursive snapshot--> config + backups
                 --exclude--> media

docker_logs --> Vector (.level) --> VictoriaLogs
                                   |--> log-watch --> discord-relay
                                   |--> Grafana Errors dashboard

Sonarr/Radarr/Jellyfin/TrueNAS --> library-exporter --> VictoriaMetrics
                                                      --> Grafana Library space

TrueNAS REST API --> Homepage widget (arrstack)
                 --> library-exporter (obsv)
```

Every new behavior is driven by a YAML file in git. Secrets stay in gitignored `.env` files.

## 0. Media snapshots

Source of truth: `scripts/backup/truenas-snapshots.yaml`.

- Dataset `metrion/zinthos`, recursive, same ladder as today (6h/2d, daily/14d, weekly/8w).
- `exclude: metrion/zinthos/media`.
- `scripts/backup/truenas_snapshots.py` reconciles TrueNAS `pool.snapshottask` by `naming_schema`: create, update (`exclude`, lifetime, schedule, enabled), delete tasks no longer declared.
- `scripts/backup/setup-truenas-snapshots.sh` still does NFS + config tarball cron, then calls the Python reconciler over SSH.
- `--purge-media-snapshots` is opt-in. Dry-run `zfs destroy -nv` first, require typing `YES`, then destroy `metrion/zinthos/media@auto-*` only. Never the default path.

After purge, deleting a file in Sonarr/Radarr with "delete files" frees pool space immediately.

## 1. Log watching

Vector `docker_logs_norm` sets `.level` to `error`, `warn`, `info`, or `unknown`. Order:

1. JSON `level` / `lvl` / `severity`
2. logfmt `level=` / `lvl=` / `severity=`
3. *arr-style `[Error]` / `[Warn]` / `[Fatal]` / `[Info]`
4. Go-style `ERR` / `WRN` / `FTL` tokens
5. Plain `ERROR` / `WARN` / `FATAL` words
6. stderr is a weak hint only: unmatched stderr stays `unknown` unless the line looks like a traceback

`.level` is a regular field, not a `_stream_fields` entry.

Config: `obsv/log-alerts.yaml` (`defaults` plus per-container `mute`, `ignore` regexes, threshold overrides).

Service: `obsv/log-watch/` (Python, 64m). Every minute query VictoriaLogs LogsQL. Page `discord-relay` in Alertmanager format with `source: log-watch` on:

- error count in the window above threshold
- a new signature (normalized message) not seen in the lookback window

Daily digest: top error and warn signatures per container. Known signatures persist under `/arrstack/obsv/log-watch/`.

Grafana: VictoriaLogs datasource + "Errors and warnings" dashboard. Grafana image is built locally so the VictoriaMetrics logs plugin is in the image (no runtime plugin download).

## 2. Low (720p) profile

`arrstack/recyclarr.yml` adds a manual Recyclarr profile named `Low (720p)` on both Sonarr and Radarr.

- Allowed: Bluray-720p, WEB 720p group, HDTV-720p, then SD fallbacks.
- `upgrade.allowed: true`, `until_quality` at the 720p group/quality used as cutoff.
- TRaSH Unwanted CFs (BR-DISK, LQ, LQ Release Title) at guide scores (`-10000`).
- TRaSH `x265 (HD)` scored **positive** on this profile only (smaller encodes). Guide default is `-10000`; override to `100`.
- Assign the profile per item in Sonarr/Radarr or Seerr advanced options. No extra root folders.
- `scripts/prune_arr_quality_profiles.py` allowlists the new name so deploy does not delete it.
- Changing an existing item to this profile does not shrink files. Delete and re-search.

## 3. Homepage TrueNAS widget

Manual one-time step: TrueNAS UI → Credentials → Users → a dedicated `homepage` user with `Readonly Admin` + API key (shown once).

- Root `.env`: `TRUENAS_API_KEY`. Synced to `HOMEPAGE_VAR_TRUENAS_API_KEY` in `arrstack.auto.env`.
- `obsv/.env`: same `TRUENAS_API_KEY` for the exporter.
- `arrstack/homepage/services.yaml` Storage group, widget `type: truenas`, `version: 1` (SCALE 25.04 REST), `enablePools: true`, `nasType: scale`, `url: https://<truenas-host>`.
- TLS: TrueNAS uses a self-signed/private cert. Homepage widget URL stays HTTPS. If the tile fails on TLS, follow `docs/hosts/truenas.md` (trust the UI cert or use HTTP if the API answers on 80). Library-exporter verifies TLS via a YAML flag defaulting to false for this LAN box.

## 4. Library space visualizer

Service: `obsv/library-exporter/` (Python, 64m). Config: `obsv/library-exporter.yaml`.

Scrapes (interval ~1h, also on `/metrics`):

- Sonarr `/api/v3/series` (`statistics.sizeOnDisk`, quality profile, tvdbId)
- Radarr `/api/v3/movie` (`sizeOnDisk` / `movieFile.size`, quality profile, tmdbId)
- Jellyfin items + per-user `UserData` (PlayCount, LastPlayedDate), joined on TVDB/TMDB
- TrueNAS `GET /api/v2.0/pool/dataset` for `used`, `usedbysnapshots`, `available`

API keys for *arr/Jellyfin: mount `/arrstack/config/arrstack.auto.env` read-only. TrueNAS key from `obsv/.env`.

Prometheus gauges (homelab cardinality is fine):

- `library_item_bytes{app,kind,title,profile,id,stale,watched}`
- `truenas_dataset_used_bytes{dataset}`
- `truenas_dataset_usedbysnapshots_bytes{dataset}`
- `truenas_dataset_available_bytes{dataset}`
- `library_exporter_up`

Grafana dashboard "Library space": largest items, large+stale, size by profile, dataset used vs snapshot-held vs free. Media `usedbysnapshots` should be ~0 after item 0. No delete buttons.

## Placement

| Piece | Where |
| --- | --- |
| Snapshot YAML + reconciler | `scripts/backup/`, run over SSH to TrueNAS |
| log-watch, library-exporter, Grafana plugin image | `obsv/` Compose on CT 100 |
| Recyclarr profile | `arrstack/recyclarr.yml`, prune allowlist |
| Homepage widget | `arrstack/homepage/services.yaml` |
| Secrets | gitignored `.env` / `obsv/.env` / `arrstack.auto.env` |

Ship with `./deploy.sh obsv` and `./deploy.sh arrstack`. Snapshot script is manual (`scripts/backup/setup-truenas-snapshots.sh`), not a deploy target.

## Tests

- `scripts/tests/test_truenas_snapshots.py` — reconcile create/update/delete, exclude field, purge name filter
- `scripts/tests/test_log_watch.py` — level fixtures, signature normalize, ignore/mute, spike threshold
- `scripts/tests/test_library_exporter.py` — ID join, stale classification, Prometheus escaping
- Homepage alias test for `HOMEPAGE_VAR_TRUENAS_API_KEY`
- Prune allowlist includes `Low (720p)`

## Follow-up

Vector + log-watch coverage for CT 104 Jellyfin. Not in this spec.
