# truenas — TrueNAS SCALE storage server

Sole storage host. Serves the media library and the arrstack's configuration over SMB, both of
which the Proxmox guests depend on.

Inventoried 2026-08-14 by live SSH inspection.

## Access

- Address: `<truenas-host>` (on `enp4s0`)
- SSH: `taylor@<truenas-host>`, key auth, passwordless sudo, shell `zsh`
- Web UI: `https://<truenas-host>`

## Homepage / exporter API key (manual, once)

The Homepage TrueNAS widget and the obsv library-exporter need a read-only API key. TrueNAS shows
the key value only at creation.

1. Credentials → Users → Add `homepage` (or reuse a dedicated service user).
2. Role: **Readonly Admin** (`READONLY_ADMIN`). No shell, no sudo.
3. Credentials → Users → API Keys → Add, owned by that user.
4. Put the key in **both** gitignored files (same value):
   - repo-root `.env` as `TRUENAS_API_KEY=...` (synced to `HOMEPAGE_VAR_TRUENAS_API_KEY`)
   - `obsv/.env` as `TRUENAS_API_KEY=...`
5. Deploy: `./deploy.sh arrstack` (Homepage) and `./deploy.sh obsv` (library-exporter).

SCALE 25.04 removed useful REST. Homepage widget `version: 2` and library-exporter use
`wss://<truenas-host>/api/current` (`auth.login_with_api_key`). REST `/api/v2.0/*` returns 403.

The UI cert is private. Homepage and the exporter skip TLS verify for this LAN box. Do not
commit the key.

## Platform

- TrueNAS SCALE 25.04.2.3 (Fangtooth)
- Debian 12 Bookworm, kernel `6.12.15-production+truenas`
- Uptime at inventory: 29 days
- A system update is available (the only active alert)
- No TrueNAS apps deployed — this box does storage only
- Wanted (not deployed): copyparty for browser file browsing, on this host rather than arrstack —
  see F-32. Arrstack should not gain extra NAS paths just to feed a file browser.

## Hardware

- CPU: AMD A10-7850K Radeon R7 (Kaveri, 2014), 4 cores / 4 threads, FM2+
- RAM: 6.7 GB usable, 5.7 GB in use, 990 MB available, **no swap** — see F-18
- Boot: 119.2 GB `Fanxiang S101 128GB` SSD — see F-19
- Data: 3.6 TB `TOSHIBA MG04ACA400N`, 7200 RPM enterprise SATA

Part of the nominal 8 GB of RAM is reserved by the APU's integrated graphics, which is why only
6.7 GB is visible. This is below the conventional 8 GB floor for ZFS.

## Pools

Both pools are single-disk. No redundancy anywhere — see F-12.

### `boot-pool` — 118 GB on `sda3`

- 2.84 GB allocated, 115 GB free, 2% capacity, `ONLINE`
- Last scrub 2026-08-14, repaired 0 B, no errors

### `metrion` — 3.62 TB on `sdb1`, mounted at `/mnt`

- 1.50 TB allocated, 2.13 TB free, 41% capacity, 12% fragmentation, `ONLINE`
- Last scrub 2026-07-26, took 02:48:53, repaired 0 B, no errors
- No known data errors

Scrubs are running on schedule. Periodic snapshots cover `metrion/zinthos` recursively **except**
`metrion/zinthos/media` (F-25, F-37). Media is re-downloadable; snapshotting it pinned deleted
files and made pool free space look lower than the live library. Declared in
`scripts/backup/truenas-snapshots.yaml`. To destroy leftover `media@auto-*` snapshots:

```bash
./scripts/backup/setup-truenas-snapshots.sh --purge-media-snapshots
```

## Datasets

All of `metrion` uses `lz4` compression.

| Dataset | Used | Purpose |
| --- | --- | --- |
| `metrion/zinthos/media` | 1.49 TB | The media library, mounted into CT 100 and CT 104 |
| `metrion/zinthos/backups` | 4.74 GB | Contents not yet identified |
| `metrion/zinthos/config` | 176 MB | arrstack compose files and container config |
| `metrion/.system` | 1.93 GB | TrueNAS internal (netdata, samba4, configs) |

`metrion/zinthos/config` is where `/mnt/config` on the Proxmox host resolves to, which means the
running Docker Compose file itself lives here. The arrstack therefore depends on this NAS being
reachable for more than just media.

The `backups` dataset holds 4.74 GB from an unidentified source — worth establishing what writes
there before relying on it or reusing it as the vzdump target for F-01.

## Shares

- SMB: serves `media` and `config`, consumed by the Proxmox host via `/etc/fstab`. Credentials are
  currently plaintext on the Proxmox side — see F-03.
- NFS: share definitions exist for `backups` and `config` but are **disabled**.

## Drive health

Both drives pass SMART with clean counters.

- `sda` (boot SSD): PASSED, 949 power-on hours, 0 reallocated sectors, 0 offline uncorrectable, 32 °C
- `sdb` (Toshiba 4 TB): PASSED, 5803 power-on hours (~242 days), 0 reallocated, 0 current pending,
  0 offline uncorrectable, load cycle count 23, 31 °C (range 11–35 °C)

A load cycle count of 23 over 5803 hours is excellent — this drive is not head-parking itself to
death, which is a common failure path for consumer drives used in NAS duty. The hardware is
healthy; the risk here is topology, not wear.

## Assessment

The disks are fine and the maintenance that exists is being done properly. The exposure is
structural: one disk holding 1.5 TB with no mirror, no snapshots, no backups, and no UPS. Every one
of those is a separate decision that was never made, and any single one of them would meaningfully
reduce the blast radius of the others.
