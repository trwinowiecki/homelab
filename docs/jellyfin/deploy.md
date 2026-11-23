# Jellyfin stack deploy

Jellyfin runs as a **separate** Compose project on **CT 104** (`jellyfin-docker`,
`<jellyfin-host>`), not inside `docker-compose.yaml` and not on CT 100. The native CT 101
guest was destroyed 2026-09-29 after soak.

## First-time setup

```bash
cp jellyfin/.env.example jellyfin/.env
# Set JELLYFIN_ADMIN_PASSWORD / JELLYFIN_LOCAL_ADMIN_PASSWORD,
# LDAP_JELLYFIN_BIND_TOKEN (same as identity/.env)
# JELLYFIN_PUBLISHED_SERVER_URL=https://jellyfin.<domain>
# LOCAL_DOCKER_IP=<jellyfin-host>

./deploy.sh jellyfin identity adguard
```

`identity` renders Caddy with `reverse_proxy <jellyfin-host>:8096`. `adguard` keeps
`jellyfin.<lan-domain>` → `<arrstack-host>` (TLS still terminates on CT 100).

### GPU (CT 104)

On pve, CT 104 has Quick Sync (sole holder):

```
dev0: /dev/dri/renderD128,gid=10000
```

Restart CT 104 once after changing the device line.

`encoding.xml` on the CT 104 config volume (`/arrstack/jellyfin/config/encoding.xml`)
sets `HardwareAccelerationType` to `vaapi`, turns tone mapping on, and hardware-decodes
H.264, HEVC, MPEG-2, VC-1, VP8, and VP9. `/dev/dri` is already passed into the container.
Jellyfin reads that file at startup, so a restart is required before transcodes use the
iGPU. Dolby Vision Profile 5 has no HDR10 base layer; Quick Sync cannot tone-map it.
Sonarr `WEB-2160p` and Radarr `UHD Bluray + WEB` score TRaSH **DV (w/o HDR fallback)**
at `-10000` so those releases are not grabbed.

### Migration history

CT 101 (native LXC at `<legacy-jellyfin-host>`) was destroyed 2026-09-29. Rollback is no longer
possible. The copy procedure is kept in [docs/runbooks/jellyfin-migrate.md](../runbooks/jellyfin-migrate.md).

## What `./deploy.sh jellyfin` does

1. Validates `docker compose config` locally.
2. rsyncs `jellyfin/` to `/mnt/config/jellyfin` on the config share.
3. Creates `/arrstack/jellyfin/config` on the CT 104 LXC SSD.
4. `docker compose up -d` on **CT 104** (`JELLYFIN_CTID=104`).
5. Runs `scripts/bootstrap/jellyfin.py` — plugin catalogs, installs, LDAP config.

Image is `jellyfin/jellyfin:12.0` (official). linuxserver has not published 12.0; the
compose file preserves the linuxserver volume layout (`/config` + `/config/data`) so
bootstrap and the migrate tar mapping stay the same.

## Declarative config

| Path | Applied by |
| --- | --- |
| `jellyfin/bootstrap.yml` | `scripts/bootstrap/jellyfin.py` |
| `jellyfin/branding.xml` | ElegantFin custom CSS (`branding.xml` on the config volume) |
| `jellyfin/plugin-config/*.xml` | Plugin settings under `config/data/plugins/configurations/` |
| `jellyfin/ldap-auth.xml.tmpl` | same (bind token from `identity/.env`) |

Plugins for 12.0: Fanart, File Transformation, Intro Skipper, LDAP-Auth. Media Bar and
Ratings are omitted until authors ship 12.0 builds.

LDAP binds to Authentik's outpost at `<arrstack-host>:3389` (published from CT 100). Keep one
local admin account as break-glass (not in `jellyfin_users`).

## URLs

| Audience | URL |
| --- | --- |
| LAN (household) | `https://jellyfin.<lan-domain>` → Caddy on `.201` → CT 104 `.204` |
| Remote / apps | `https://jellyfin.<domain>` (Pangolin → CT 104 `.204`) |
| Direct HTTP on CT 104 | `http://<jellyfin-host>:8096` |

Pangolin SSO stays **off** for Jellyfin (native apps cannot complete browser SSO).

## Keep running across arrstack restarts

Jellyfin no longer lives on CT 100, so arrstack `restart_no_sudo.sh` image prunes cannot
delete its image. Leave CT 104 up independently.
