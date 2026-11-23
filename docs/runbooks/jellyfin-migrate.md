# Jellyfin migration — CT 101 LXC → CT 104 Docker

**Status (2026-09-29):** Production is CT 104 (`<jellyfin-host>`). Native CT 101 was
destroyed after soak (`pct destroy 101 --purge`). Rollback by starting CT 101 is no
longer possible. Older restic snapshots tagged `jellyfin` are the only remaining
copy of the native install. The steps below are the historical procedure.

## Prerequisites

- CT 104 provisioned: Debian, Docker, `mp0` media, `mp1` config, `dev0` renderD128 `gid=10000`
- `./deploy.sh identity` — Authentik + LDAP outpost on CT 100 (`<arrstack-host>:3389`)
- `LDAP_JELLYFIN_BIND_TOKEN` in `identity/.env` / `jellyfin/.env`
- restic snapshot: `restic snapshots --tag jellyfin` on pve (optional; prefer a stopped copy)

## Phase 1 — Trial (dual-run) — done

### 1. Copy Jellyfin state (brief CT 101 stop)

On pve:

```bash
JELLYFIN_SRC=/proc/$(lxc-info -n 101 -H -p)/root
CT104_CONFIG=/proc/$(lxc-info -n 104 -H -p)/root/arrstack/jellyfin/config

pct exec 104 -- docker stop jellyfin || true
pct exec 101 -- systemctl stop jellyfin

mkdir -p "${CT104_CONFIG}/data"
# wipe previous clone contents under config/ if re-copying
find "${CT104_CONFIG}" -mindepth 1 -maxdepth 1 ! -name data -exec rm -rf {} +
find "${CT104_CONFIG}/data" -mindepth 1 -maxdepth 1 -exec rm -rf {} + 2>/dev/null || true

tar -C "${JELLYFIN_SRC}/etc" -cf - jellyfin \
  | tar -C "${CT104_CONFIG}" --strip-components=1 -xf -
tar -C "${JELLYFIN_SRC}/var/lib" -cf - jellyfin \
  | tar -C "${CT104_CONFIG}/data" --strip-components=1 -xf -

# Docker data path is /config/data; DB lives at data/data/jellyfin.db
rm -rf "${CT104_CONFIG}/data/transcodes" \
  "${CT104_CONFIG}/data/data/jellyfin.db-shm" \
  "${CT104_CONFIG}/data/data/jellyfin.db-wal" 2>/dev/null || true
rm -f "${CT104_CONFIG}/.jellyfin-data"   # keep data/.jellyfin-data only

# Unprivileged CT 104: PUID 10000 → host UID 110000
chown -R 110000:110000 /proc/$(lxc-info -n 104 -H -p)/root/arrstack/jellyfin
pct exec 104 -- chmod 664 /arrstack/jellyfin/config/data/data/jellyfin.db
```

Native paths map to official/`linuxserver`-compatible `/config`:

- `/etc/jellyfin/*` → `/arrstack/jellyfin/config/`
- `/var/lib/jellyfin/*` → `/arrstack/jellyfin/config/data/` (not the config root)

### 1b. Rewrite avatar paths (required after every native→Docker copy)

`ImageInfos.Path` stores absolute native paths. Docker expects `/config/users/...`:

```bash
pct exec 104 -- python3 -c '
import sqlite3
db="/arrstack/jellyfin/config/data/data/jellyfin.db"
con=sqlite3.connect(db)
con.execute(
  "UPDATE ImageInfos SET Path = replace(Path, ?, ?) WHERE Path LIKE ?",
  ("/etc/jellyfin/", "/config/", "/etc/jellyfin/%"),
)
con.commit()
print(list(con.execute("SELECT Path FROM ImageInfos")))
'
```

Without this, `/Users/.../Images/Primary` returns 404 even though `profile.jpg` files exist.

### 2. Clone-only tweaks (before first Docker start)

Edit on CT 104 under `/arrstack/jellyfin/config/`:

- `system.xml` — `<ServerName>` (use `jellyfin` in production; e.g. `Jellyfin (next)` only for trial)
- Set `JELLYFIN_PUBLISHED_SERVER_URL` and `LOCAL_DOCKER_IP=<jellyfin-host>` in `jellyfin/.env`

Library paths must stay `/mnt/media/...` (compose mounts `${FOLDER_FOR_MEDIA}:/mnt/media`).

### 3. Deploy clone

```bash
./deploy.sh jellyfin identity
```

Bootstrap installs LDAP-Auth (server `<arrstack-host>`) and refreshes plugin config.
Image is `jellyfin/jellyfin:12.0`.

### 4. Verify on the clone

- `https://jellyfin.<lan-domain>` — local `admin` break-glass login
- Avatars load on the user picker
- Authentik user in `jellyfin_users` — LDAP login
- Transcode / QSV via `/dev/dri`
- Intro Skipper / File Transformation / Fanart in dashboard

## Phase 2 — Cutover — done (2026-09-12)

1. Final recopy from CT 101 (leave 101 jellyfin stopped after copy)
2. Avatar path rewrite + `<ServerName>jellyfin</ServerName>`
3. `JELLYFIN_PUBLISHED_SERVER_URL=https://jellyfin.<domain>`; `./deploy.sh jellyfin`
4. Pangolin blueprint target `<jellyfin-host>`; `pangolin/site/deploy.sh` (+ Newt restart)
5. Seerr `jellyseerr/settings.json` → `jellyfin.ip: <jellyfin-host>`; restart `seerr`
6. Homepage tile → CT 104 / `jellyfin.<lan-domain>`
7. `backup.sh`: CT 101 jellyfin restic disabled while parked; CT 104 `jellyfin-docker` active
8. `pct stop 101` — keep disk for soak; **do not** `pct destroy` until confirmed

## Rollback

Not available. CT 101 was destroyed 2026-09-29. Pangolin, Seerr, and Caddy stay on
`<jellyfin-host>`.

## After soak — done 2026-09-29

- `pct destroy 101 --purge`
- Commented CT 101 restic block removed from `scripts/backup/backup.sh`
- Leftover `/arrstack/jellyfin` on CT 100 removed
- Weekly `guest-fstrim.sh` trims CT 100 and CT 104

## Fresh deploy (no clone)

Empty `/arrstack/jellyfin/config` on CT 104, set `JELLYFIN_ADMIN_*` in `jellyfin/.env`, run
`./deploy.sh jellyfin`. Add libraries in the Jellyfin UI. Users and watch history came from
the CT 101 clone; that guest no longer exists.
