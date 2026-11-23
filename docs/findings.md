# Findings Log

Running catalog of issues discovered while inventorying the homelab. Each entry keeps its ID for
life so it can be referenced in commits and conversation. Update `Status` rather than deleting.

Status values: `open`, `in progress`, `resolved`, `accepted` (known and deliberately tolerated),
`deferred`.

Last updated: 2026-10-30. Entries below are the 2026-08-17 inventory. Several are stale:
Unpackerr is gone (F-08), Buildarr was replaced by Recyclarr and Configarr (F-33), Jellyfin
moved to CT 104 and native CT 101 was destroyed (F-05, F-06), and admin UIs are no longer
published on the LAN (F-14). Treat those as historical unless a later note says otherwise.

**Fleet as of 2026-09-29:** pve (Proxmox host) with CT 100 `arrstack` (media, identity, obsv),
CT 102 `adguard`, CT 103 `newt`, CT 104 `jellyfin-docker`; truenas; Linode Pangolin VPS; the
AT&T BGW gateway; and a spare Dell Inspiron 15 7559 (service tag `1JY6LD2`) that is not on the
network — see `docs/hosts/inspiron-7559.md` and F-36. The Raspberry Pi is decommissioned — see F-29.
CT 101 `jellyfin` was destroyed after the Docker cutover.

---

## Critical

### F-29 — The Pi's SD card failed at the controller level; the host and its data are gone
- **Status:** **closed 2026-08-15 — data permanently lost, no further action possible.** During the
  reseat attempt the microSD split cleanly in half. A microSD is a single moulded package holding
  both controller and NAND die, so the physical break ends even the chip-off option. Nothing is
  recoverable. The Pi is decommissioned and the fleet continues without it.
- **Host:** <adguard-host>
- **What happened:** The card was pulled to add an SSH public key by hand, per the F-09 recovery
  plan. It read and wrote correctly on the laptop. On reinsertion into the Pi an audible snap was
  heard, the Pi no longer boots, and the card no longer enumerates correctly anywhere.
- **Evidence** — the laptop's kernel log captures the failure across a fifteen minute window:
  ```
  23:02:10  mmc0: new UHS-I speed SDR104 SDXC card at address aaaa
  23:02:10  mmcblk0: mmc0:aaaa SN128 119 GiB
  23:02:10   mmcblk0: p1 p2
  23:02:11  EXT4-fs (mmcblk0p2): recovery complete
  23:02:11  EXT4-fs (mmcblk0p2): mounted filesystem ... r/w
  23:06:57  EXT4-fs (mmcblk0p2): unmounting filesystem
  23:07:00  mmc0: card aaaa removed
  23:17:57  mmc0: new SD card at address d555
  23:17:57  mmcblk0: mmc0:d555 SD032 30.6 MiB
  ```
  The card now reports `serial 0xffffffff`, manufacture date `02/2006`, and a CSD that decodes as
  structure version 1.0 — a pre-SDHC standard-capacity card. Its real identity is a 128 GB SanDisk
  (`SN128`). No partition table is visible.
- **Diagnosis:** The card's controller has lost its own configuration and fallen back to a ROM/safe
  state. This is a hardware fault in the card, not filesystem corruption.
  - **It is not the reader.** The same reader read the same card correctly at 23:02.
  - **It is not recoverable in software.** `ddrescue`, `testdisk`, and `photorec` can only address
    what the controller presents, which is 30.6 MiB of nonsense. The flash is unreachable without
    chip-off recovery.
  - The filesystem was cleanly unmounted at 23:06:57, so the data was consistent at the moment the
    hardware failed. That matters only if professional recovery is ever attempted.
- **Impact:** Realises F-24 in full and takes out F-09's host. Also realises F-23 ahead of schedule
  — see F-30, the Pi was already the network's DHCP server.
- **Reseat attempted 2026-08-14 23:28.** Identical result — `SD032 30.6 MiB` at address d555,
  `serial 0xffffffff`, no partition table. The fault is stable and reproducible across insertions,
  which rules out a contact or seating problem.
- **Handling:** The card is writable (`ro` = 0). Do not format it, run `fsck` on it, or accept any
  "repair this disk?" prompt, or the option of professional recovery is lost too.
- **Untested:** Whether the Pi itself survived. A Pi 3 uses a friction microSD slot with no spring
  or click mechanism, so a snap is not a normal sound and the slot may have broken off the board.
  A freshly flashed card is the only way to distinguish a dead card from a dead Pi.

### F-30 — The Pi was the network's DHCP server; the whole LAN degrades on a 24 hour clock
- **Status:** **resolved 2026-08-15.** CT 102 `adguard` serves DNS and DHCP at the Pi's old address.
  Gateway DHCP is disabled again, AdGuard's is enabled, and the cutover is verified: the service is
  active, listening on 53 and 67, using 294 MB of its 1 GB, and answering correctly from off-host
  (`github.com` resolves, `doubleclick.net` returns `0.0.0.0`). Static reservations are registered
  for pve `.200`, arrstack `.201`, truenas `.202`, jellyfin `.203`, and adguard's own `.119`.
  Residual items live on in F-31 (IPv6 bypass) and the plain-DNS upstream noted in
  `docs/hosts/adguard.md`.
- **Evidence:** `nmcli` on a leased client, after the Pi went down:
  ```
  dhcp_server_identifier = <adguard-host>
  domain_name           = lan
  domain_name_servers   = <adguard-host>
  dhcp_lease_time       = 86400
  ```
  A DNS probe of every candidate on the segment returns answers only from the gateway at
  `192.168.1.254`; `.119`, `.87`, `.200`, and `.202` all time out.
- **Impact, in two stages:**
  1. **Immediate.** Every client was handed `<adguard-host>` as its only IPv4 resolver, and it is
     gone. IPv4-only devices have no working DNS right now. Dual-stack devices are limping along on
     AT&T's IPv6 resolver, which masks the outage unevenly and makes it look intermittent.
  2. **Within 24 hours.** Leases are 86400 seconds. Clients begin failing renewal at the halfway
     point and drop their addresses entirely at expiry, staggered by whenever each last renewed.
     Without a DHCP server the network progressively falls apart through 2026-08-15.
- **Stopgap (owner, 2026-08-14):** Re-enable DHCP on the AT&T gateway. This restores addressing and
  resolution at the cost of ad blocking, which is the right trade while the replacement is built.
  **Done 2026-08-14.**
- **Replacement built 2026-08-14:** CT 102 `adguard` on pve, at the Pi's old address
  `<adguard-host>` so existing leases renew against it. AdGuard Home v0.107.78 installed and running;
  the setup wizard has not been run yet. See `docs/hosts/adguard.md` for the cutover order — the
  gateway's DHCP must not be disabled until AdGuard is confirmed answering, and the two must not
  serve DHCP simultaneously for longer than necessary.
- **Open risk during the overlap:** the BGW's pool spans roughly `.64`–`.253`, which includes
  `<adguard-host>` and every static address in the fleet. A conflict is possible until gateway DHCP
  is switched back off.
- **Corrects F-21:** That finding recorded moving DHCP to AdGuard Home as a *future* decision with
  an unverified precondition. In fact DHCP had already been moved off the gateway to the Pi-hole
  some time ago. The BGW firmware therefore does permit disabling DHCPv4 — the precondition F-21
  flagged as unconfirmed is confirmed, by the fact that the network has been running that way.

### F-01 — No backups exist for any host or guest
- **Status:** in progress 2026-08-16 — restic of irreplaceable configs is live (see
  `docs/runbooks/backup-and-restore.md`). ZFS snapshots cover `metrion/zinthos`. Media is still a
  single copy on one HDD (F-12). Pangolin VPS config is not in the automated job yet (pve has no
  SSH key to the Nanode). Offsite is deferred.
- **Host:** pve, truenas
- **Evidence:** `/etc/pve/jobs.cfg` does not exist. `/var/lib/vz/dump` is empty. `zpool list` on pve
  returns "no pools available", so there are no snapshots either. Storage is a single 512 GB SSD in
  pve and a single 4 TB HDD in TrueNAS holding 1.5 TB of media.
- **Impact:** Any single drive failure is unrecoverable data loss. This is the only finding on this
  list that destroys something you cannot rebuild.
- **This has already cost you once.** The `metrion/zinthos/backups` dataset is a former Proxmox
  directory storage (`dump/`, `images/`, `template/iso`) from a previous pve instance whose SSD
  failed in March 2026, forcing a rebuild. Backups were configured then and were not working when
  they were needed.
- **Forensics from the surviving logs** — these change the fix, see F-27:
  - `2025-10-27`: completed, but took **34 hours 14 minutes** to write 169 GiB at **1.4 MiB/s**.
    Archive was 151.57 GB. The archive itself is gone; only the log remains.
  - `2025-10-28`: a follow-on job started 26 seconds after the previous one finished and never
    completed, leaving the orphaned 5.09 GB `.tar.dat` and `.tmp` that account for the 4.8 GB
    sitting there today. There is no usable backup in this dataset.
  - `2026-03-14`: ran in `stop` mode during the rebuild, failed after four minutes with
    `interrupted by signal`.
- **Fix applied 2026-08-16:** file-level restic from pve to `metrion/zinthos/backups/restic` over
  NFS, not vzdump. Daily timer `homelab-backup.timer` at 03:30 America/New_York. First run finished
  in minutes (arrstack config 1.4 GiB, Jellyfin 3.5 GiB, AdGuard 66 MiB, plus compose/pve/TrueNAS
  config). Restore drill of AdGuard YAML succeeded. The old CIFS+vzdump path is withdrawn — F-27.
- **Caveat:** Even then it is only half a backup. It protects against guest-level mistakes, not
  drive failure, because the target is the same single disk holding the only copy of the media. Real
  redundancy needs a second disk in the TrueNAS box. See F-12.

### F-27 — vzdump of CT 100 is pathologically slow because it archives Docker's overlay2
- **Status:** resolved 2026-08-16 — F-01 no longer uses vzdump. restic copies `/arrstack/config`
  only (~1.4 GiB in 13s). overlay2 and `/arrstack/downloads` are excluded by not being in the
  backup set.
- **Host:** pve / CT 100
- **Evidence:** The 2025-10-27 job wrote 169 GiB at 1.4 MiB/s over 34 hours. `vzdump` archives the
  entire LXC rootfs, which includes `/var/lib/docker` (16.11 GB of images across overlay2) and
  `/arrstack/downloads` (19 GB). The job also ran `zstd --threads=1` at `ionice` priority 7.
- **Why it is so slow:** Docker's overlay2 is close to a worst case for `tar` — hundreds of
  thousands of tiny files and hardlinks across layered directories. Combined with single-threaded
  compression, idle-class I/O priority, and an SMB target on a single spinning disk behind a 2014
  APU, per-file overhead dominates completely and throughput collapses.
- **Confirmed by owner 2026-08-14:** the bulk of that 169 GiB was seeding torrents in
  `/arrstack/downloads` that duplicated media already sitting on the NAS. The backup was copying
  data back to the same disk it already lived on.
- **Likely aggravating factor:** `ionice` priority 7 places the job in the idle I/O class, meaning
  it yields to all other I/O. With 19 containers generating constant torrent activity, an
  idle-class job can be starved almost indefinitely — which fits a 1.4 MiB/s rate far better than
  raw SMB or disk limits do.
- **Insight:** Almost none of that data is worth backing up. Docker images are reproducible from
  `docker-compose.yaml`, and seeding data is a duplicate of the NAS by design (F-04). The genuinely
  irreplaceable state is `/arrstack/config` at 1.1 GB — the *ARR databases and settings. A backup
  scoped to that finishes in minutes instead of days.
- **Fix:** Set vzdump exclude paths for `/var/lib/docker` and `/arrstack/downloads`, raise zstd
  thread count, and drop the ionice restriction. Consider whether a file-level job targeting
  `/arrstack/config` is a better fit than a full container dump, given the container itself is
  reproducible from this repo once the declarative work lands.

### F-02 — LVM thin pool leaking ~210 GB, trending toward exhaustion
- **Status:** mitigated 2026-08-28 (fstrim + discard; monitor still open)
- **Host:** pve
- **Evidence:**
  ```
  LV            LSize    Data%
  data          348.82g  74.65
  vm-100-disk-0 300.00g  81.49
  vm-101-disk-0  16.00g  99.65
  ```
- **Emergency grow 2026-08-16:** CT 100 rootfs was 100% full (280G/295G) from 77 preallocated
  Gilmore Girls torrents in `/arrstack/downloads/incomplete`. Added the 16 GB VG free to the thin
  pool (now 364.82g) and grew `vm-100-disk-0` to 316g. Filesystem has 16G free. qBit queueing is
  on (max 3 active downloads), preallocation off. Discard leak is unchanged.
- **Recurrence 2026-08-28:** CT 100 rootfs hit 100% again. **257G** of SSD space was orphaned
  `complete/` copies left by qBit “remove torrent, keep files” after Sonarr/Radarr had already
  imported to NAS (Gilmore Girls, Schitt's Creek, Radarr movies). Manual delete freed ~230G guest
  space; `pct fstrim 100` reclaimed **258G** thin-pool allocation (Data% **88 → 18**). `tdarr-node`
  had a **7.5G** unbounded json log because compose log rotation was never applied to running
  containers.
- **Impact:** ~210 GB unavailable. More seriously, a thin pool that reaches 100% fails writes and
  can corrupt the filesystems on it. Downloads accumulate on this pool, so it grows unattended.
- **Fix applied 2026-08-28:** `pct fstrim 100/101`; weekly `guest-fstrim.sh` cron; CT 100 rootfs Discord
  alert at 85% (`ct100-disk-alert.sh`). Proxmox LXC rootfs does not accept `discard=on` on the
  `rootfs:` line (schema error); fstrim is the supported reclaim path. Pair with F-28's NAS-aware
  SSD cleanup.

---

## High

### F-03 — CIFS credentials in plaintext in a world-readable file, password equals username
- **Status:** open
- **Host:** pve
- **Evidence:** `/etc/fstab` is mode `-rw-r--r--` and contains `username=zinthos,password=zinthos`
  for both `//<truenas-host>/media` and `//<truenas-host>/config`.
- **Impact:** Any local user on pve can read credentials granting full read/write to all media and
  all container configuration. The password is trivially guessable independently.
- **Fix:** Move to `/etc/cifs-credentials` at mode `0600`, reference with `credentials=` in fstab,
  and change the password to something generated. Store the new value SOPS-encrypted in this repo.

### F-04 — Split download/library storage prevents hardlinks (deliberate, with a cost)
- **Status:** accepted by design 2026-08-14, but see F-28 for the cost that must be bounded
- **Host:** pve / CT 100
- **Evidence:** `FOLDER_FOR_DOWNLOADS=/arrstack/downloads` is the LXC's local SSD rootfs, while
  `FOLDER_FOR_MEDIA=/mnt/media` is the CIFS mount. Different filesystems, so hardlinking is
  impossible by definition and every import is a full copy.
- **Rationale (owner, 2026-08-14):** Intentional. Downloading and seeding happen on the SSD
  specifically to keep constant torrent I/O off the NAS's single spinning disk; only finished media
  is written to the HDD.
- **Assessment:** This is a sound tradeoff, not a misconfiguration. The alternative — everything on
  the CIFS share so hardlinks work — would give up duplication but subject a lone 7200 RPM drive to
  continuous seeding reads while it is also serving Jellyfin. On a single-disk pool with 6.7 GB of
  RAM and minimal ARC, that is the worse failure mode. The current design gives the HDD one
  sequential write per import and nothing else.
- **Residual costs to manage:**
  1. Seeding copies consume SSD space indefinitely unless bounded — see F-28.
  2. Every import is a full-file SMB write. Sequential, so acceptable, but the mount's
     `cache=none` option throttles it unnecessarily. `noserverino` and `cache=none` were likely
     copied from a guide to work around *ARR behaviour on CIFS; neither is buying anything here,
     since hardlinks are already impossible. Worth benchmarking without them.
  3. The *ARR apps should be set to copy rather than "hardlink or copy", so they stop attempting a
     link that can never succeed.

### F-28 — Seeding data on the SSD is unbounded and is what fills the thin pool
- **Status:** mitigated 2026-08-28 (NAS-aware cleanup live; downloads stay unlimited)
- **Host:** CT 100
- **Evidence:** The October 2025 vzdump archived **169 GiB** from the LXC rootfs, the bulk of which
  was seeding torrents in `/arrstack/downloads` duplicating media already on the NAS. Aug 2026
  recurrence: **257G** in `torrents/complete` after keep-files seed limits, with only **7** torrents
  still tracked in qBit.
- **Impact:** This is the compounding half of F-02. The thin pool is 348.8 GB with roughly 210 GB
  already locked up in unreclaimed blocks. Add an unbounded seeding directory that has demonstrably
  reached 169 GiB, and the pool can plausibly reach 100%. A full thin pool fails writes and can
  corrupt the filesystems on it — and both LXCs live on this pool, so that is the whole stack.
- **Fix applied 2026-08-15:** qBittorrent share limits ratio **1.0** and seed time **4320 minutes**
  (3 days). New torrents save under `/downloads/torrents/complete` on the SSD.
- **Fix applied 2026-08-28:** Downloads stay **unlimited** (`max_active_downloads=-1`). At share
  limit qBit **pauses** (`max_ratio_act=0`), not remove-keep-files. Cron
  `scripts/qbit-ssd-cleanup.py` (every 20 min via pve) deletes SSD data only when Sonarr/Radarr
  history shows an import for that hash (same-size NAS fallback for orphan dirs). CT 100 rootfs
  Discord alert at 85% catches simultaneous grabs before ratio limits.

### F-18 — TrueNAS is running ZFS on 6.7 GB of RAM with under 1 GB available
- **Status:** open
- **Host:** truenas
- **Evidence:** `free -h` reports 6.7 GB total, 5.7 GB used, 990 MB available, and **no swap**. The
  A10-7850K is an APU, so some of the nominal 8 GB is reserved for integrated graphics.
- **Impact:** ZFS ARC is the bulk of that "used" figure and is reclaimable, but 8 GB is the
  conventional floor for ZFS and this box is below it. Low ARC means more physical reads from a
  single spinning disk, which is already the slowest link in the media path. With no swap there is
  no cushion if something spikes.
- **Fix:** Add RAM. The FM2+ platform is old but DDR3 is cheap. This is the single highest-value
  upgrade for NAS responsiveness.

### F-21 — AT&T gateway will not advertise a custom DNS server, so DHCP cannot stay on it
- **Status:** in progress — AdGuard Home host built 2026-08-14 (`docs/hosts/adguard.md`), wizard and
  cutover outstanding. The precondition below is confirmed rather than unverified; see F-30.
- **Host:** 192.168.1.254
- **Evidence:** Gateway redirects to `/cgi-bin/home.ha`, the Arris/Pace web interface used by AT&T
  BGW-series residential gateways. These do not expose a DHCP option to hand out a third-party DNS
  server; the gateway always advertises itself.
- **Impact:** This breaks the earlier assumption that DHCP could stay on the router while an ad
  blocker handled DNS. Network-wide DNS filtering is impossible in that arrangement without
  configuring every client by hand.
- **Decision (2026-08-14):** AdGuard Home will serve DHCP as well as DNS, and the gateway's DHCP
  server will be turned off.
- **Precondition, unverified:** This only works if the BGW firmware actually permits disabling
  DHCPv4, under Home Network → Subnets & DHCP. Reports vary by model and firmware revision. Two
  DHCP servers answering on the same layer 2 segment is a race, and the failure mode is ugly —
  clients intermittently receive the gateway's DNS instead of AdGuard's, so filtering appears to
  work for some devices and not others, and the pattern changes on every lease renewal. **Confirm
  the setting exists before migrating anything.** If it does not, fall back to IP Passthrough with
  an owned router.
- **Follow-on risk:** Whichever host serves DHCP becomes a hard dependency for the whole network.
  See F-23.

---

## Medium

### F-31 — IPv6 clients bypass AdGuard entirely; ad blocking / split-horizon leaks
- **Status:** open — confirmed 2026-08-14; phone `*.<lan-domain>` 404s confirmed 2026-09-06
- **Evidence:** The AT&T gateway advertises itself as an IPv6 resolver via RDNSS in Router
  Advertisements, independent of DHCPv4. Clients therefore end up with both, and the gateway's
  entry does no filtering:
  ```
  AdGuard  (<adguard-host>)          doubleclick.net -> 0.0.0.0        (blocked)
  Gateway  (2600:1702:6400:bb0::1)  doubleclick.net -> 74.125.138.139 (not blocked)
  ```
  A client `resolv.conf` after the cutover lists `<adguard-host>`, then the gateway's IPv6 address.
  On phones the same race hits split-horizon: public DNS answers Namecheap `*` → Pangolin VPS
  (`172.234.196.180`), Traefik returns **404** for unpublished `*.<lan-domain>` hosts;
  AdGuard answers `<arrstack-host>` (Caddy) and the page loads (~1/10). Android Private DNS and
  iCloud Private Relay produce the same public path.
- **Impact:** Any dual-stack device that prefers the IPv6 resolver — which is most of them, most of
  the time — resolves without filtering. Blocking will appear to work intermittently and per-device,
  which is exactly the symptom that makes people distrust an ad blocker and disable it.
  This also explains why DNS appeared partly functional during the F-30 outage: dual-stack clients
  were quietly falling through to AT&T's IPv6 resolver the whole time.
- **Why AdGuard cannot fix it alone:** RDNSS is carried in Router Advertisements from the gateway.
  Serving DHCPv4 options does not override it, and the BGW does not expose a way to advertise a
  third-party IPv6 DNS server — the same limitation as F-21, one layer down.
- **Mitigations (stack both):**
  1. **Namecheap:** replace zone-wide `*` A with explicit `pangolin` / `jellyfin` / `auth` /
     `seerr` only. Verify with `./scripts/check_home_dns_public.sh` (LAN names must be NXDOMAIN).
  2. **Phone checklist:** Pangolin VPN off on home WiFi (or DNS Over Tunnel → `<adguard-host>`);
     Android Private DNS Off; iPhone Private Relay Off for WiFi.
  3. **BGW (only clean network-wide fix):** disable IPv6 / IPv6 DNS so RDNSS stops advertising
     the gateway resolver — gives up IPv6 and interacts with F-14.
  4. **AdGuard:** `*.<lan-domain>` rewrite → `<arrstack-host>` (defense in depth for clients
     that *do* query AdGuard).
- **Not yet decided** on BGW IPv6 disable vs owned router (F-21).

### F-05 — Jellyfin LXC runs in the wrong timezone, four hours off
- **Status:** open
- **Host:** CT 101
- **Evidence:** `pct config 101` shows `timezone: America/Adak`; `date` inside returns HDT. Every
  other component uses `America/New_York`.
- **Impact:** Scheduled task times, "recently added" ordering, and log timestamps are all skewed.
  A community-script default that was never changed.
- **Fix:** Set the LXC timezone to `America/New_York`.

### F-06 — Tdarr transcodes on CPU while the Quick Sync GPU sits idle
- **Status:** open
- **Host:** CT 100
- **Evidence:** CT 101 has `dev0: /dev/dri/renderD128,gid=993`; CT 100 has no such entry. pve has
  Intel HD Graphics 630 with a working render node.
- **Impact:** Tdarr is doing CPU-only transcoding on 2 cores of a 4-core i5-7500, when the same
  Quick Sync engine Jellyfin already uses could do the work far faster and cooler.
- **Fix:** Pass `/dev/dri/renderD128` into CT 100 as well, then configure Tdarr for QSV.

### F-07 — `.env` records the wrong address for the Docker host
- **Status:** open
- **File:** `.env`
- **Evidence:** `LOCAL_DOCKER_IP=192.168.1.158`, but CT 100 reports `<arrstack-host>`.
- **Impact:** Currently only feeds `HOMEPAGE_ALLOWED_HOSTS`, and Homepage is healthy, so nothing is
  visibly broken. The file misdescribes the deployment, which will mislead future work.
- **Fix:** Correct the value.

### F-08 — Unpackerr *ARR integration is non-functional
- **Status:** open
- **File:** `docker-compose.yaml`
- **Evidence:** Every `UN_*_API_KEY` is the placeholder `0123456789abcdef0123456789abcdef`, and
  `UN_WEBHOOK_0_URL` still points at a Notifiarr example path.
- **Impact:** Unpackerr cannot talk to any *ARR app, so archive extraction is not being driven.
- **Fix:** Real keys already exist in `buildarr.yml`. Wire them through SOPS rather than inline.

### F-09 — Raspberry Pi is locked out, runs four services, and its OS is end of life this month
- **Status:** **overtaken by events 2026-08-14** — the host is down and its storage is destroyed.
  See F-29. The lockout is moot; the question is now rebuild, not recovery. Retained for the
  service inventory below, which is the only surviving record of what this host ran.
- **Host:** <adguard-host>
- **Evidence:** SSH banner reports `OpenSSH_8.4p1 Debian-5+deb11u2`, i.e. Debian 11 Bullseye, whose
  LTS window closes at the end of August 2026. Probing with `PreferredAuthentications=none` shows
  `publickey` is the only accepted method, and the existing key fails for `admin`, `pi`, `root`,
  `dietpi`, `taylor`, and `ubuntu`. Port scan and HTTP title probes identify four services:
  - `53`, `80` — Pi-hole (port 80 returns 403 at `/`, admin lives at `/admin`)
  - `1883` — MQTT broker
  - `8080` — Zigbee2MQTT
  - `8123` — Home Assistant
- **Impact:** This is not a DNS appliance, it is the home automation hub *and* network DNS, on an
  unpatchable OS, on an SD card, with no backups, that nobody can log into.
- **Plan revision:** The earlier "replace rather than recover" plan is withdrawn. Reflashing would
  destroy the Zigbee network pairing and the Home Assistant configuration. Regaining access is now
  a prerequisite to touching this host at all. See F-24.

### F-24 — Zigbee network state is unbacked-up and currently unreachable
- **Status:** **realised 2026-08-14** — the card died before the backup was taken. See F-29.
- **Outcome:** The predicted failure mode in the Impact section below is exactly what happened, and
  on the same day it was written. `database.db`, `coordinator_backup.json`, and the Home Assistant
  configuration were on that card and exist nowhere else. Confirmed absent from TrueNAS
  (`/mnt/metrion`), from `/var/lib/vz/dump` on pve, and as any disk image on the laptop.
- **What survives, and the one rule that protects it:** The coordinator radio holds the network key,
  PAN ID, and channel in its own NVRAM, independent of the SD card. The devices are still joined to
  that network. A rebuilt Zigbee2MQTT should therefore **adopt the adapter's existing network** —
  leave `pan_id` and `network_key` unset in `configuration.yaml` so they are inherited from the
  radio. Setting new values makes Z2M form a fresh network, which orphans every device and forces
  the physical re-pairing this finding was written to avoid. Do not factory-reset the coordinator.
- **What is lost regardless:** Friendly names and per-device settings lived in `database.db`, so
  devices will reappear as raw IEEE addresses needing relabelling. Home Assistant's automations,
  dashboards, entity registry, and recorder history have no recovery path.
- **Host:** <adguard-host>
- **Evidence:** Zigbee2MQTT is serving on port 8080, so a Zigbee coordinator radio is attached and a
  paired device network exists. There is no backup of the Pi anywhere, and SSH access is unavailable.
- **Impact:** Zigbee2MQTT holds the coordinator backup, network key, PAN ID, and the device database
  in its data directory. Lose those and **every Zigbee device must be physically re-paired**, which
  for sensors in awkward places is genuinely painful. An SD card that has been running Home
  Assistant, Zigbee2MQTT, MQTT, and Pi-hole for years is doing constant small writes and is a
  plausible near-term failure — the standard way this goes wrong is that the card dies before anyone
  gets around to the backup.
- **Fix, in order:** Regain access (console or SD card reader), immediately copy off the
  Zigbee2MQTT data directory and the Home Assistant config, *then* make any other decision. Nothing
  about this host should change before that copy exists.

### F-26 — Home Assistant is on a Pi 3 and the owner wants to use it more
- **Status:** open, design consideration
- **Host:** <adguard-host>
- **Evidence:** Home Assistant, Zigbee2MQTT, an MQTT broker, and Pi-hole all share a Raspberry Pi 3
  with 1 GB of RAM and SD card storage.
- **Impact:** That is already a heavy load for the hardware, and Home Assistant grows over time with
  recorder history, add-ons, and integrations. Stated intent is to use it more, which this host will
  not support well. SD card write wear is the specific failure mode.
- **Options:** Move Home Assistant to an LXC or VM on pve where there is ~12 GB of free RAM, and
  keep the Zigbee radio reachable either by relocating the dongle to pve with USB passthrough, or by
  leaving Zigbee2MQTT on a separate box and pointing it at the relocated MQTT broker. Radio placement
  matters for mesh coverage, so the dongle's physical location is a real constraint, not an
  afterthought.
- **Spare hardware, 2026-08-17:** The Dell Inspiron 15 7559 (`docs/hosts/inspiron-7559.md`, F-36) is
  a candidate for the HA + Zigbee2MQTT + MQTT rebuild on hardware that is not pve. That keeps lights
  working through a Proxmox reboot, and the laptop can sit where the mesh actually needs the radio.
  An LXC on pve is still the simpler ops story. Do not put HA back on the dead Pi even if the board
  still boots.

### F-19 — TrueNAS boots from an unbranded consumer SSD
- **Status:** in progress 2026-08-16 — nightly `tar` of `/data/freenas-v1.db` and `pwenc_secret`
  into `metrion/zinthos/backups/staging`, ingested by restic (`tag truenas-config`). Boot SSD is
  still a single unbranded Fanxiang; a mirror is still the hardware fix.
- **Host:** truenas
- **Evidence:** Boot device is `sda`, a 119.2 GB `Fanxiang S101 128GB`, holding the boot-pool.
- **Impact:** If it fails, the NAS will not boot. Configuration is recoverable from a TrueNAS config
  backup, but there is currently no such backup — related to F-01.
- **Fix:** Export a TrueNAS config backup into this repo's workflow, and consider mirroring the boot
  device. Losing the boot SSD should be an inconvenience, not an outage.

### F-20 — Cannot read TrueNAS pool configuration; `taylor` has no passwordless sudo
- **Status:** **resolved 2026-08-14.** Passwordless sudo was granted and the full inventory was
  collected — pools, datasets, shares, snapshot tasks, and SMART are all recorded in
  `docs/hosts/truenas.md`. The gap it opened became F-25 (no snapshot tasks).
- **Host:** truenas
- **Evidence:** `zpool list`, `zpool status`, and `zfs list` all return empty as `taylor`, because
  `/dev/zfs` requires root. `sudo -n true` returns "a password is required", and `root`,
  `truenas_admin`, and `admin` all reject the SSH key.
- **Impact:** Pool topology, dataset layout, share definitions, snapshot tasks, and SMART data are
  all unknown. The storage picture cannot be completed.
- **Fix:** Either grant `taylor` passwordless sudo in the TrueNAS UI (Credentials → Local Users →
  Allow all sudo commands, no password), or install the key for a root-capable account.

### F-34 — Gluetun goes unhealthy at random; qBittorrent has to be restarted by hand
- **Status:** open, needs investigation
- **Host:** CT 100 `arrstack`
- **Evidence:** Owner report, 2026-08-15. The `gluetun` container flips to unhealthy without a
  corresponding restart, and torrenting stays dead until both `gluetun` and `qbittorrent` are
  restarted. SABnzbd shares the same network namespace and would be equally stuck; it has not been
  called out yet.
- **Why a restart of both is required:** qBittorrent and SABnzbd use `network_mode: "service:gluetun"`
  and `depends_on: gluetun: {condition: service_healthy, restart: true}`. Compose restarts the
  downloaders only when Gluetun itself restarts. An unhealthy-but-still-running VPN container
  leaves them attached to a dead netns. The compose file does not declare a healthcheck on
  `gluetun`; the image's built-in check is what marks it unhealthy, and nothing currently acts on
  that besides the UI going red.
- **Likely causes to check first:** NordVPN endpoint churn / auth, WireGuard vs OpenVPN (`VPN_TYPE`
  in `.env`), DNS inside the tunnel, the control server, and LXC `/dev/net/tun` plus `NET_ADMIN`
  interaction. Capture `docker inspect gluetun` Health.Log and `docker logs gluetun` from the next
  incident before restarting, or the evidence is gone.
- **Fix direction:** Confirm whether Gluetun should restart on unhealthy, whether the healthcheck
  is too aggressive, and whether downloaders should be bounced automatically when health fails.
  Pair with F-35 so the next drop is noticed without watching Portainer.
- **Do not "fix"** by moving qBittorrent or SABnzbd off the Gluetun network. That is the intended
  failure mode when the VPN is down.

---

## Low

### F-10 — Pending package updates on pve
- **Status:** open
- **Host:** pve
- **Evidence:** `apt list --upgradable` includes ZFS 2.4.0-pve1 to 2.4.3-pve1 among others. Uptime
  is 29 days.
- **Fix:** Schedule a patch window. Note ZFS is unused here (no pools), so the ZFS packages are low
  risk either way.

### F-11 — Host swaps despite ample free memory
- **Status:** open
- **Host:** pve
- **Evidence:** 1.5 GB of 8 GB swap in use with 12.9 GB RAM available; `vm.swappiness` is the
  default 60.
- **Impact:** Minor latency on swapped pages. Not urgent.
- **Fix:** Lower `vm.swappiness` to 10 if it proves bothersome.

### F-12 — No storage redundancy anywhere in the fleet
- **Status:** open, needs hardware
- **Hosts:** pve, truenas
- **Evidence:** pve has one 512 GB SSD (`P3-512`) with no mirror. TrueNAS has a single 4 TB HDD with
  no pool redundancy.
- **Fix:** Requires purchasing disks. A second 4 TB drive to mirror the NAS pool is the highest
  value spend and is a precondition for F-01 being a real backup.

### F-22 — No UPS on any machine
- **Status:** open, needs hardware
- **Hosts:** pve, truenas
- **Evidence:** Confirmed by owner, 2026-08-14. (The simultaneous July 16 reboot on both machines
  was an intentional power cycle, not an outage.)
- **Impact:** TrueNAS runs ZFS on a single disk with no redundancy and no backups. ZFS is resilient
  to unclean shutdown by design, but an unprotected power loss during a write is precisely the case
  where a single-disk pool with no replica has nothing to fall back on. Once AdGuard Home serves
  DHCP from pve, a power blip also takes the whole network's addressing down until it returns.
- **Fix:** A basic line-interactive UPS covering both boxes, with NUT configured so they shut down
  cleanly. Lower priority than F-01 (backups) but the two are related — a UPS reduces the odds of
  needing the backup you do not currently have.

### F-23 — Planned DHCP host becomes a single point of failure for the network
- **Status:** open — and no longer hypothetical. This risk was written about a *future* AdGuard
  host, but the Pi already occupied that role and its failure took the network down exactly as
  described. See F-30. Treat the mitigations below as requirements for the replacement, not
  considerations.
- **Evidence:** Per the F-21 decision, AdGuard Home will serve both DNS and DHCP.
- **Impact:** If AdGuard runs in an LXC on pve, then any pve reboot, upgrade, or failure takes down
  name resolution and address assignment for every device in the house — including the ones that
  are not yours to inconvenience.
- **Mitigations:** Set long DHCP lease times, so a short pve outage passes unnoticed by already-
  leased clients. Run a second AdGuard instance as secondary DNS on separate hardware and advertise
  both. The Pi was that hardware and is gone; the spare Inspiron 7559 is the current candidate
  (F-36, `docs/hosts/inspiron-7559.md`). Keep DHCP primary on CT 102 — DNS redundancy is
  straightforward, DHCP redundancy is not, so lease duration is still the real defence.

### F-13 — Docker runs inside an unprivileged LXC
- **Status:** accepted
- **Host:** CT 100
- **Evidence:** `unprivileged: 1` with `features: nesting=1`.
- **Note:** Works and is stable at 19/19 containers up. Proxmox officially recommends a VM for
  Docker workloads. Recording it as a known deviation, not a defect to fix.

### F-14 — CT 100 holds public IPv6 addresses while publishing services on raw ports
- **Status:** open, needs investigation
- **Host:** CT 100
- **Evidence:** `hostname -I` returns `2600:1702:6400:bb0:be24:11ff:febd:599b` and
  `2600:1702:6400:bb0::34`. The LXC config sets `ip6=dhcp`. There is no reverse proxy, and every
  service binds a host port.
- **Impact:** Unknown until the router's IPv6 firewall posture is confirmed. If inbound IPv6 is not
  filtered at the gateway, services intended to be LAN-only may be reachable from the internet.
- **Fix:** Verify the gateway's IPv6 firewall rules before doing anything else.

### F-15 — Unidentified service on the Pi's port 8080
- **Status:** resolved 2026-08-14
- **Resolution:** It is Zigbee2MQTT. Confirmed by HTTP title probe. Folded into F-09 and F-24.

### F-25 — TrueNAS has no periodic snapshot tasks
- **Status:** resolved 2026-08-16; **media excluded 2026-10-30** (F-37). Recursive tasks on
  `metrion/zinthos` except `metrion/zinthos/media`: 6-hourly keep 2 days, daily keep 14 days,
  weekly keep 8 weeks. Declared in `scripts/backup/truenas-snapshots.yaml`. Setup script:
  `scripts/backup/setup-truenas-snapshots.sh`.
- **Host:** truenas
- **Evidence (historical):** `midclt call pool.snapshottask.query` returned `[]` at inventory.
- **Impact:** ZFS snapshots are the cheapest protection available on this box and cost almost
  nothing on a pool at 41% capacity. Without them, an accidental deletion or a bad *ARR move is
  permanent. They are not a substitute for F-01, since a snapshot lives on the same disk as the
  data, but they defend against the far more common failure — human error, not hardware.
- **Fix:** Add periodic snapshot tasks on `metrion/zinthos` with a sensible retention ladder.
  Scrubs are already running correctly, so the maintenance habit exists; snapshots are the gap.

---

## Wanted

Not defects. Recorded so they do not get stuffed into the wrong host later.

### F-33 — Finish TRaSH Guides quality on the *ARR stack
- **Status:** deferred (wanted)
- **Host:** CT 100 `arrstack`
- **Intent:** Bring Sonarr/Radarr (and the rest of the *ARR apps as they are used) in line with
  [TRaSH Guides](https://trash-guides.info/) quality profiles, custom formats, and naming — not
  just the handful of `trash_id` entries already in `buildarr.yml`.
- **Where it stands:** Sonarr has several TRaSH release profiles and custom formats declared in
  `buildarr.yml`. Radarr's custom formats are commented out. Quality-profile scoring, naming,
  and media-management settings from the guides are not fully applied. Recyclarr is the usual
  tool for this; this stack uses Buildarr instead, so any work has to fit that, or justify a
  switch.
- **Constraint:** Keep ports in `buildarr.yml` in sync with `docker-compose.yaml`. Do not paste
  live `api_key` values into chat or commits (F-16).

### F-35 — Logging, monitoring, and notifications for stack health
- **Status:** in progress 2026-08-17 — phase 1+2 implemented in-repo; deploy with `./deploy.sh obsv pve`
- **Host:** CT 100 `arrstack` (logs + metrics + Grafana); node_exporter on pve; no agents on TrueNAS
- **Intent:** Notice failures like F-34 without watching Portainer. Collect container logs in one
  place and send alerts (Discord) when a service goes unhealthy, restarts in a loop, or stops.
  Phase 2 adds host/guest metrics, thin-pool graphs, and Linode transfer.
- **Spec:** `docs/superpowers/specs/2026-08-16-observability-design.md`
- **Deploy:** `docs/observability/deploy.md` (obsv Compose) and `docs/observability/layer1.md`
  (PVE webhook, TrueNAS Slack-compat, thin-pool cron, node_exporter, PVE API token). Needs a
  Discord webhook in `obsv/.env` and a copy to `/mnt/config/obsv` before anything pages.
- **Why now:** Gluetun can sit unhealthy while qBittorrent and SABnzbd keep running on a dead VPN
  netns. Homepage and Portainer exist, but neither pages anyone. Unpackerr's Notifiarr webhook is
  still a placeholder (F-08), so there is no working notification path today.
- **Constraint:** This compose file is the mini VPN topology, not the upstream MediaStack with
  Grafana/CrowdSec/Traefik. Observability is the `obsv/` project, not extra arrstack services.
- **Related:** F-34 (the first thing this should catch), F-08 (dead Unpackerr/Notifiarr wiring).

### F-36 — Spare Inspiron 15 7559 inventoried; no role assigned
- **Status:** open, design consideration
- **Host:** none yet (not on the LAN)
- **Evidence:** Owner-reported Dell Inspiron 15 7559, service tag `1JY6LD2`, i7-6700HQ, 8 GB RAM,
  GTX 960M, 256 GB 2.5" SSD. Full write-up in `docs/hosts/inspiron-7559.md`. Not live-inspected.
- **Intent:** Pick a role or explicitly leave it as spare so it does not become another unbacked,
  multi-service box by default. Strongest fit is the Home Assistant rebuild (F-26) and/or a
  secondary AdGuard resolver (F-23). A 16 GB RAM upgrade is the only spend that also makes a
  small (3–4B GGUF) local-LLM experiment sane; more disk does not. It is not a compute,
  transcode, NAS, or hypervisor upgrade.
- **Constraint:** If it becomes always-on, Debian + Intel iGPU only, sleep disabled, Ethernet, and
  backups before it holds irreplaceable state. Do not recreate the Pi's "everything on one disk"
  layout with DHCP primary on this chassis.

### F-32 — Browser file browsing via copyparty, not on arrstack
- **Status:** deferred (wanted)
- **Host:** truenas (intended). Must not live on CT 100 `arrstack`.
- **Intent:** Browse NAS files from a browser, with a LAN login. Additional datasets/folders will
  be added over time that arrstack has no reason to see.
- **Why not arrstack:** Arrstack should keep only the NAS mounts it needs (today: `media` and
  `config`). Putting copyparty there would either widen that blast radius or freeze the file
  browser to the media library. Copyparty belongs next to the datasets, on TrueNAS.
- **Constraint:** TrueNAS currently has no Apps deployed and is already memory-tight (F-18). Any
  implementation has to fit that, or wait until RAM is less of a problem. Do not enable TrueNAS
  Apps casually just to park this.
- **Access when built:** LAN only, password required. Anonymous off.

### F-37 — Media snapshots held deleted library files
- **Status:** resolved 2026-10-30. Periodic tasks now exclude `media`. Auto snapshots and the
  leftover `media@manual-2026-08-17` (549G) were destroyed. `USEDSNAP` on media is 0B.
- **Host:** truenas
- **Evidence:** Recursive tasks on `metrion/zinthos` included `media`. After exclude + purge,
  pool AVAIL rose (media used 3.31T → 2.77T).
- **Intent:** Stop snapshotting media (re-downloadable). Keep the ladder on config and backups.
- **Fix:** `scripts/backup/truenas-snapshots.yaml` `exclude: metrion/zinthos/media`, reconcile
  (not create-if-missing), opt-in purge of all snapshots under `metrion/zinthos/media`.

### F-38 — Container error/warn logs are collected but not paged
- **Status:** in progress 2026-10-30 — implemented in-repo; deploy with `./deploy.sh obsv`
- **Host:** CT 100 `arrstack` (obsv)
- **Intent:** Discord on error spikes and new error signatures, plus a daily warn+error digest
  and a Grafana errors dashboard. Config: `obsv/log-alerts.yaml`. CT 104 Jellyfin is out of
  scope until Vector runs there.
- **Spec:** `docs/superpowers/specs/2026-10-30-logs-quality-nas-design.md`

### F-39 — No 720p-or-lower quality profile for low-care titles
- **Status:** in progress 2026-10-30 — Recyclarr YAML in-repo; deploy with `./deploy.sh arrstack`
- **Host:** CT 100 `arrstack`
- **Intent:** Manual `Low (720p)` profile on Sonarr and Radarr. Does not auto-assign; does not
  downgrade existing files.

### F-40 — Homepage has no NAS stats; no library-size visualizer
- **Status:** resolved 2026-10-30. Widget `version: 2` (websocket); REST `/api/v2.0` is 403 on
  SCALE 25.04. Grafana is proxied to `grafana:3000` on `mediastack` (host `:3800` is loopback).
- **Host:** CT 100 Homepage widget; obsv library-exporter + Grafana "Library space"
- **Intent:** Pool/dataset stats on Homepage; largest/stale items and dataset used vs
  snapshot-held in Grafana. Read-only — delete stays in Sonarr/Radarr.

---

## Repo hygiene (tracked separately from infrastructure)

### F-16 — Live secrets committed in plaintext, git root spans unrelated projects
- **Status:** open
- **Evidence:** `.env` contains real NordVPN credentials. `buildarr.yml` contains real *ARR API
  keys. `git rev-parse --show-toplevel` resolves to `/home/taylor/projects`, a repository with zero
  commits, no `.gitignore`, and a dozen unrelated projects, so a blanket `git add` from the root
  would capture all of it.
- **Fix:** `git init` inside `homelab/`, add a `.gitignore`, rotate both sets of credentials, then
  reintroduce them SOPS-encrypted.

### F-17 — `.env` carries a large amount of upstream cruft
- **Status:** open
- **Evidence:** Variables exist for Authentik, Traefik, Headscale, Plex, Grafana, PostgreSQL, and
  CrowdSec — none of which appear in `docker-compose.yaml`. Several hold placeholder secrets.
- **Impact:** The file appears to describe a much larger deployment than exists, which is misleading.
- **Fix:** Strip to what is actually deployed when producing `.env.example`.
