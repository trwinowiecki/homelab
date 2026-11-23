# Homelab backup and restore

Two local layers. Media (1.49 TB) is **not** copied and is **not** snapshotted: it is
re-downloadable. Offsite (B2 / Hetzner) is a later `restic copy`.

## What runs where

| Layer | Where | Protects against |
| --- | --- | --- |
| ZFS snapshots on `metrion/zinthos` (6-hourly / daily / weekly), **excluding `media`** | TrueNAS | Accidental delete of config/backups, bad *ARR config move |
| restic repo at `/mnt/lxc_shares/metrion/backups/restic` on pve (TrueNAS `metrion/zinthos/backups/restic`) | written from pve | pve SSD death, guest mistakes, TrueNAS config loss (the tarball) |

The restic **password** lives at `/root/.restic-password` on pve (`chmod 600`). Copy it into a
password manager now:

```bash
ssh root@<pve-host> 'cat /root/.restic-password'
```

If pve dies and the password is only on pve, the repo is brick.

## Contents of a restic snapshot

- CT 100 `/arrstack/config` (*ARR databases). Not overlay2, not `/arrstack/downloads`.
- CT 100 Authentik: `pg_dump` of the `authentik` database (tag `identity`) plus `/arrstack/identity` without the live Postgres and Redis data dirs (tag `identity-files`). `/arrstack/obsv` metrics and logs are not backed up.
- CT 104 `/arrstack/jellyfin/config` (Docker Jellyfin production; tag `jellyfin-docker`). Native CT 101 was destroyed 2026-09-29; older snapshots tagged `jellyfin` are the native install.
- CT 102 AdGuard YAML + data dir.
- CT 103 `/opt/pangolin-site`.
- pve `/etc/pve`, fstab, network interfaces.
- TrueNAS `freenas-v1.db` + `pwenc_secret` tarball from `backups/staging`.
- Compose files on the `config` SMB share.
- Pangolin VPS `/opt/pangolin/config` if pve can SSH to `172.234.196.180`; otherwise skipped.

## Operations

`./deploy.sh pve` installs the systemd units and scripts. It does not install `restic.env`.
Copy `scripts/backup/restic.env.example` to `/root/homelab-backup/restic.env` on pve by hand.

On pve:

```bash
source /root/homelab-backup/restic.env
restic snapshots
restic check
systemctl status homelab-backup.timer
journalctl -u homelab-backup.service -n 50
```

Manual run: `systemctl start homelab-backup.service`

## Restore

Guests are rebuilt from `docs/hosts/*.md`, then files are restored on top.

LXC paths in the repo are recorded as `/proc/<init-pid>/root/...` because restic runs on the
pve host against the live container namespace. After restore, `find` for the filename:

```bash
source /root/homelab-backup/restic.env
restic snapshots --tag adguard
restic restore latest --tag adguard --target /tmp/restore-drill
find /tmp/restore-drill -name AdGuardHome.yaml
```

AdGuard: restore `AdGuardHome.yaml` over `/opt/AdGuardHome/AdGuardHome.yaml` and restart `AdGuardHome`.

TrueNAS boot SSD death: upload `truenas-config.tar.gz` in the SCALE UI (System → General → Upload Config), or extract `freenas-v1.db` / `pwenc_secret` into `/data` before first boot is finished — prefer the UI.

Pangolin: restore `pangolin-config.tar` into `/opt/pangolin/config` on the VPS. The Git templates in `pangolin/` recreate everything except SQLite state, Gerbil key, and `acme.json`.

## Adding offsite later

```bash
source /root/homelab-backup/restic.env
restic copy --repo /mnt/lxc_shares/metrion/backups/restic sftp:u123@u123.your-storagebox.de:restic
```

Do not use the Pangolin Nanode as the backup target.

## Disk note (CT 100)

qBittorrent saves to `/downloads/torrents/complete` with temp under `/downloads/incomplete`.
**Active downloads are unlimited.** Share limits are ratio 1.0 / 3 days; qBit pauses at the limit
and `scripts/qbit-ssd-cleanup.py` removes SSD copies only after Sonarr/Radarr confirm a NAS import.
Do not dump incomplete/complete in restic. A simultaneous season grab can still fill the 316 GB
rootfs before ratio limits — the CT 100 rootfs Discord alert (85%) is the backstop. After large
deletes run `pct fstrim 100` or wait for the weekly guest-fstrim cron.
