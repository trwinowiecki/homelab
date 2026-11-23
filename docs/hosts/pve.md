# pve — Proxmox VE host

Primary compute node. Everything except DNS and storage runs here.

Inventoried 2026-08-14 by live SSH inspection.

## Access

- Address: `<pve-host>` (static, on `vmbr0`)
- SSH: `root@<pve-host>`, key auth
- Web UI: `https://<pve-host>:8006`

## Platform

- Proxmox VE 9.1.6 (`pve-manager/9.1.6/71482d1833ded40a`)
- Debian 13 Trixie, kernel `6.17.13-2-pve`
- Uptime at inventory: 29 days
- Pending `apt` updates outstanding — see F-10

## Hardware

- CPU: Intel Core i5-7500 @ 3.40 GHz (Kaby Lake), 4 cores / 4 threads, no SMT, VT-x
- RAM: 15.8 GB total, 2.9 GB used, 12.9 GB available at inventory
- Swap: 8 GB, 1.5 GB in use despite free RAM — see F-11
- GPU: Intel HD Graphics 630 (`8086:5912`), render node `/dev/dri/renderD128` present and working
- Boot/system disk: single 512 GB SSD, model `P3-512`, no redundancy — see F-12

Quick Sync on Kaby Lake handles H.264 and HEVC encode/decode including 10-bit, plus VP9 decode.
No AV1. Currently used by Jellyfin only — see F-06.

## Storage layout

Single SSD partitioned to LVM, no ZFS anywhere.

- `/dev/sda2` — 1 GB EFI, mounted `/boot/efi`
- `/dev/sda3` — 475.9 GB LVM physical volume, volume group `pve`
  - `pve-root` — 96 GB ext4 on `/`, 7% used
  - `pve-swap` — 8 GB
  - `pve-data` — 348.8 GB LVM-thin pool, **74.65% allocated**
    - `vm-100-disk-0` — 300 GB (CT 100). 81.49% allocated at the 2026-08-17 inventory
    - `vm-101-disk-0` — removed when native CT 101 was destroyed on 2026-09-29
    - CT 102 adguard — 4 GB rootfs
    - CT 103 newt — 8 GB rootfs
    - CT 104 jellyfin-docker — 32 GB rootfs

Allocation badly exceeds real usage because discard was never enabled — see F-02.

Proxmox storages defined (`/etc/pve/storage.cfg`):

- `local` — directory at `/var/lib/vz`, content `iso,import,vztmpl`
- `local-lvm` — the thin pool, content `rootdir,images`

No backup-capable storage is defined, which is why no backup jobs exist — see F-01.

## Network shares from TrueNAS

Mounted at host level via `/etc/fstab` with systemd automount, then bind-mounted into guests:

- `//<truenas-host>/media` → `/mnt/lxc_shares/metrion/media` — 3.6 TB, 1.5 TB used (43%)
- `//<truenas-host>/config` → `/mnt/lxc_shares/metrion/config` — 2.1 TB, 177 MB used (1%)

Both use SMB 3.1.1 with `uid=100000,gid=110000,dir_mode=0770,file_mode=0770`. The uid/gid map to
root and GID 10000 inside the unprivileged containers, matching `PGID=10000` in the stack's `.env`.

The media mount sets `cache=none`. `noserverino` was removed 2026-08-15 after confirming
Sonarr was copy-renaming on CIFS (now remounted with `serverino`). `cache=none` is still
worth benchmarking later — see F-04.

Credentials are inline and world-readable — see F-03.

## Networking

- Bridge `vmbr0` holds `<pve-host>/24`, backed by `nic0`
- Default gateway `192.168.1.254`
- Guests attach to `vmbr0` via veth with DHCP
- IPv6 is active and guests hold public addresses — see F-14

## Guests

No VMs. Guests are unprivileged LXCs with `nesting=1` and `onboot: 1`.

### CT 100 — `arrstack`

- Address: `<arrstack-host>` (DHCP), plus public IPv6
- 2 cores, 8 GB RAM, 2 GB swap, 300 GB rootfs on `local-lvm`
- Debian, unprivileged, `nesting=1`
- Extra config: `lxc.cgroup2.devices.allow: c 10:200 rwm` and a `/dev/net` bind, providing
  `/dev/net/tun` for Gluetun
- Mount points:
  - `mp0` → `/mnt/media` (TrueNAS media share)
  - `mp1` → `/mnt/config` (TrueNAS config share)
- Real disk usage: 44 GB of 295 GB. `/arrstack/downloads` is 19 GB, `/arrstack/config` is 1.1 GB
- Load average 0.38 on 2 cores; 1.2 GB of 8 GB RAM in use

Runs the full MediaStack via Docker Compose, project `arr_zinthos`, sourced from
`/mnt/config/docker-compose.yaml` — i.e. off the TrueNAS share, not local disk.

qBittorrent (fixed 2026-08-15): category `tv-sonarr` previously saved into `/data/media/tv`,
so completed torrents landed in the Jellyfin library and Sonarr imported by copy-rename.
Default save path is now `/downloads/torrents/complete` on the LXC SSD; categories
`tv-sonarr`, `radarr`, and `lidarr` all use matching subdirs under that path. Auto TMM will
not relocate existing torrents when those paths change. Share limits: ratio 1.0, 3 days,
remove torrent keep files (F-28).

No GPU passthrough on CT 100, so Tdarr is CPU-bound — see F-06. Jellyfin Docker uses
`/dev/dri` on **CT 104**, not CT 100.

### CT 104 — `jellyfin-docker` (production)

Dedicated Docker guest for Jellyfin 12.0. Not merged into CT 100.

- Address: `<jellyfin-host>/24` (static in LXC net0), MAC `BC:24:11:A4:10:04`
- 3 cores, 4 GB RAM, 1 GB swap, 32 GB rootfs on `local-lvm`
- Debian 13, unprivileged, `features: nesting=1`, Docker Engine
- `dev0: /dev/dri/renderD128,gid=10000` (sole GPU holder; native CT 101 destroyed 2026-09-29)
- `mp0` → `/mnt/media`, `mp1` → `/mnt/config`
- Compose: `jellyfin/jellyfin:12.0` via `./deploy.sh jellyfin`
- LAN HTTPS: Caddy on CT 100 → `<jellyfin-host>:8096`
- Public HTTPS: Pangolin → `<jellyfin-host>:8096`
- LDAP: Authentik outpost on CT 100 `<arrstack-host>:3389`

## Capacity headroom

- CPU: 7 vCPU assigned (CT 100: 2, CT 102: 1, CT 103: 1, CT 104: 3) across 4 physical cores.
  Tdarr on CPU is the variable that changes the load.
- RAM: ~12.9 GB free at the 2026-08-17 inventory.
- Disk: configured rootfs sizes are about 344 GB on the 348.8 GB thin pool (300 + 4 + 8 + 32).
  The old "~88 GB free" figure counted only CT 100 and CT 101. Check `lvs` before adding a guest.
- The i5-7500 has no SMT, so parallel transcode capacity is limited until Tdarr gets the GPU.
