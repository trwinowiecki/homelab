# Rebuild automation gaps

Catalog of setup that was done by hand or as a one-off against a live host, taken from
the Cursor chats for this workspace (Aug 14 – Sep 29 2026). One-off data cleanups
(duplicate media, fake `.exe` grabs, disk-full recovery) are omitted. Those are not
rebuild steps.

`./deploy.sh` already rebuilds the **application** layer once the guests, mounts, and
secrets exist. The gap is everything underneath that, plus a short list of app settings
that still live only in a container volume.

## What a rebuild can already do

| Target | Assumes |
| --- | --- |
| `./deploy.sh arrstack` | CT 100 exists, Docker is installed, `/dev/net/tun` is passed through, `/mnt/config` and `/mnt/media` are mounted, `.env` has Nord WireGuard credentials |
| `./deploy.sh identity` | CT 100, Docker, `identity/.env` (Cloudflare token, Authentik secrets). Blueprints, Caddy, LDAP token, and OIDC env sync run from the script |
| `./deploy.sh jellyfin` | CT 104 exists with Docker, GPU device, media and config mounts, `jellyfin/.env` |
| `./deploy.sh obsv` | CT 100, `obsv/.env` (Discord webhook, Grafana password, PVE and Linode tokens) |
| `./deploy.sh pve` | pve is reachable as `root@<pve-host>`. Installs node_exporter, alert crons, fstrim, qBit cleanup, restic timer units |
| `./deploy.sh adguard` | CT 102 exists, AdGuard Home is already installed and the setup wizard has been completed. Only filter lists and DNS rewrites are managed |
| `pangolin/deploy.sh`, `pangolin/site/deploy.sh` | Linode exists, Docker is installed, `pangolin/.env` and `pangolin/site/.env` exist, CT 103 has Docker |

Deploy order that actually worked: **pve host prep → arrstack (so the Docker network exists) → identity → adguard → jellyfin → obsv → arrstack again** so OIDC env is picked up. Identity and arrstack must not run at the same time; they race on compose and `arrstack.auto.env`.

## Still manual: host substrate

These were `pct create`, fstab edits, and UI clicks. None of them are in `deploy.sh`.
This is the Ansible-shaped work.

### Proxmox (`pve`, <pve-host>)

- Install the workstation SSH key for `root@<pve-host>` (`ssh-copy-id`, not under `sudo`).
- `/etc/fstab` CIFS mounts of the TrueNAS media and config shares at `/mnt/lxc_shares/metrion/...` (`uid=100000,gid=110000,cache=none`). Credentials are plaintext in fstab today. `noserverino` was removed on purpose; putting it back makes bind-mounted guests serve stale inodes.
- `/etc/fstab` NFS mount of `<truenas-host>:/mnt/metrion/zinthos/backups`.
- Host timezone. It was observed as Hawaii, so the 03:30 backup timer does not mean 03:30 Eastern.
- Create the LXC guests. Sizes and addresses that are live now:

| CT | Name | Address | Notes |
| --- | --- | --- | --- |
| 100 | arrstack | <arrstack-host> (DHCP reservation) | 3+ cores, 8 GB, ~316 GB rootfs, `nesting=1`, `/dev/net/tun`, bind-mount media and config. Docker from get.docker.com |
| 102 | adguard | <adguard-host> static | 1 core, 1024 MB RAM (512 was not enough for HaGeZi), 4 GB disk, `nesting=1`, unprivileged. AdGuard binary under `/opt/AdGuardHome`, not a container |
| 103 | newt | <newt-host> static | 1 core, 1 GB, 8 GB disk, `nesting=1,keyctl=1`. Docker plus `rsync` (site deploy fails without it) |
| 104 | jellyfin-docker | <jellyfin-host> static | 3 cores, 4 GB, 32 GB disk, bind-mount media and config, `dev0: /dev/dri/renderD128,gid=10000` |

- Unprivileged uid map: container uid 10000 is host uid 110000. `chown` inside the guest does not stick for files written from the host. Ownership fixes are `chown -R 110000:110000` on pve.
- Remounting a host CIFS share leaves stale binds inside guests. Stop the CTs, remount on the host, then start them.
- LXC rootfs rejects `discard=on`. Space comes back only from `pct fstrim` (weekly via `obsv/host/guest-fstrim.sh` after `./deploy.sh pve`). A stale `lock: mounted` (left by `pct mount`) blocks fstrim until `pct unlock`.
- `pct snapshot` is refused when a guest has media bind mounts. Pre-change snapshots have to be raw LVM thin snapshots, and Proxmox will not clean those up.

### TrueNAS (<truenas-host>)

- SSH service on, "Start Automatically".
- User `taylor`: shell not `nologin`, authorized key pasted in the UI (TrueNAS regenerates `authorized_keys` from its database; writing the file directly is discarded), passwordless sudo.
- Pool `metrion` (single 4 TB disk, no redundancy), datasets under `metrion/zinthos`, SMB shares for media and config, NFS share for backups with `maproot_user=root` or pve cannot write the restic repo.
- Snapshot tasks are codified in `scripts/backup/setup-truenas-snapshots.sh` (midclt). The rest of the NAS (pool, datasets, shares, users) is not.
- No UPS. A power loss during a scrub or resilver of a single-disk pool is unrecoverable.

### Linode VPS (`pangolin`, 172.234.196.180)

- Account, Nanode, and the Cloud Firewall: TCP 22, TCP 80/443, UDP 51820, UDP 21820. The firewall editor defaults to TCP, so the UDP rules were missed once and the tunnel never came up. Do not restrict SSH to the home IPv4 only; the workstation also has IPv6.
- First SSH key via the Lish console if it was not attached at create time.
- Baseline that `pangolin/deploy.sh` does not do: hostname, 2 GB swap (the 1 GB box OOM-kills Docker pulls on the stock 496 MB swap), `PasswordAuthentication no`.
- Pangolin admin account at `/auth/initial-setup` (token in the container log).
- Newt site "homelab": paste `NEWT_ID` / `NEWT_SECRET` into `pangolin/site/.env`.
- Open the UDP ports **before** starting Newt. If `get-config` times out, Newt does not retry and the site has no WireGuard key until `docker compose restart newt`.

### DNS and the gateway

- Domain `<domain>` at Namecheap, nameservers delegated to Cloudflare.
- Cloudflare zone, grey-cloud A records only: `pangolin`, `jellyfin`, `auth`, `seerr` → the VPS. No wildcard, no `home`, no `*.home`, no orange cloud (orange cloud breaks Traefik HTTP-01 and Pangolin).
- Zone-scoped API token (`Zone:DNS:Edit`, `Zone:Zone:Read`) in `identity/.env` as `CLOUDFLARE_API_TOKEN`. Caddy logged this token during a crash-loop; rotate it if those logs still exist.
- AT&T BGW: DHCP **off** (AdGuard is the only DHCP server). The gateway cannot hand out a third-party DNS server, which is why DHCP does not live on it.
- AT&T BGW still advertises its own IPv6 resolver (`2600:1702:6400:bb0::1`) in router advertisements, so IPv6 clients bypass AdGuard. Decision still open: disable IPv6 on the gateway, or accept the leak. `scripts/check_ipv6_exposure.sh` only detects it.
- AdGuard itself: the wizard (admin user, UI on port 80), upstreams, and the DHCP pool plus static leases are **not** in `adguard/filters.yaml` or `dns-rewrites.yaml`. A fresh CT 102 comes up with no DHCP until that wizard is done, and the house has no DNS while it is down. restic tag `adguard` is the current copy of that YAML.

## Still manual: application settings

These survive only because the config volume already exists. A wiped volume does not get them back from `./deploy.sh`.

### qBittorrent (highest priority)

`scripts/qbit-ssd-cleanup.py` re-applies ratio limits and excluded-file names every 20 minutes. It does **not** set:

- `Session\Interface=tun0`. A fresh qBit binds the Docker bridge. Gluetun's kill switch then drops UDP and every tracker fails while Gluetun itself stays healthy. This has been fixed by hand twice.
- Categories and save paths (`tv-sonarr`, `radarr`, `lidarr`, `audio` → `/downloads/torrents/complete/<cat>`). Configarr tells Sonarr the category is `tv`, Radarr `movies`, Lidarr `music`. Those names do not match the live qBit categories. A fresh qBit plus a fresh Configarr run will not reproduce the working setup.
- The 26 advanced keys that theme.park zeroed (`Session\ConnectionSpeed=0` means no outgoing connections). Theme.park is gone from compose, but nothing guards a fresh config against a zero `connection_speed`.
- Download dirs `/arrstack/downloads/torrents/complete/{tv-sonarr,radarr,lidarr,audio}` owned `10000:10000`. The restart scripts only mkdir `FOLDER_FOR_DATA` subdirs.

### Arrstack first boot

- Seerr's first admin is a Jellyfin login in the Seerr UI. Bootstrap refuses to run before that. Point it at `<jellyfin-host>:8096` (CT 104), not the old `.203`.
- SABnzbd setup wizard, then the usenet provider and indexer accounts (`USENET_*` in `.env`, indexer blocks in `arrstack/prowlarr/bootstrap.yml`). Never purchased in these chats.
- Audiobookshelf's first user is `root` (the bootstrap `POST /init` covers a fresh install; an existing DB does not).
- Mealie: self-register the admin, set `MEALIE_ALLOW_SIGNUP=false`, create the staging shopping list, mint `MEALIE_API_TOKEN` (Mealie stores tokens in SQLite; it cannot be seeded from `.env`).
- OurGroceries account, per-store lists, and `credentials.env` under the mealie-og-sync data dir (not the root `.env` — a `$` in that file is compose interpolation).
- Portainer admin password, then `PORTAINER_OAUTH_ENABLED=true` only after an OAuth login has been proven.
- Sonarr/Radarr quality profile assignment for existing series. Recyclarr creates the profiles; it does not move series off `HD-1080p`.
- Configarr's Lidarr root folder still fails schema validation. qBit client create returns 400 when the client already exists (`delete_unmanaged` is off, so a drifted client is never corrected).
- qBit must be restarted after Gluetun is recreated. `depends_on` does not cover a running container left in a dead network namespace. `docker restart qbittorrent` is not enough; it has to be `docker compose up -d qbittorrent`.
- Caddy sometimes keeps the old file after an rsync onto the CIFS share. `caddy reload` is not enough; restart the container.
- `sync.<lan-domain>` still proxies `host.docker.internal`, and mealie-og-sync binds `127.0.0.1`, so that route 502s the same way qBit did before it was pointed at `gluetun`.

### Identity and Pangolin

Already printed by `scripts/print_identity_manual_steps.py` after `./deploy.sh identity`:

- Create the Pangolin IdP, paste its redirect URI into `OAUTH_PANGOLIN_REDIRECT_URI`, redeploy identity, paste the client id and secret back into Pangolin.
- Pangolin users are a separate database. Each person needs an External user whose username matches Authentik, plus a grant (the blueprint lists emails for private resources).
- Authentik users, group membership, TOTP enrollment, invitations, and recovery links.
- Phone VPN profile: Override DNS and DNS Over Tunnel to `<adguard-host>`, match domains `*.<lan-domain>`.

`AUTHENTIK_BOOTSTRAP_PASSWORD` only applies when Postgres is created. Set it before the first `compose up` or the next step is `ak changepassword akadmin` inside the container.

### Jellyfin

- `jellyfin/.env`: admin password and `LDAP_JELLYFIN_BIND_TOKEN`.
- Library setup on an empty database. Watch history and users only come from a copy of `jellyfin.db` (see `docs/runbooks/jellyfin-migrate.md`). A native-to-Docker copy also needs `ImageInfos.Path` rewritten from `/etc/jellyfin/` to `/config/`.
- Household users in the Authentik `jellyfin_users` group. The LDAP plugin UI shows a blank bind password even when the file is set; saving that form wipes it.
- GPU passthrough on CT 104 (row above). Without it there is no hardware transcode.
- *Community* is 10-bit H.264. Android hardware decoders cannot play it. Re-encode or replace; a device-profile tweak was discussed and not done.

### Notifications

- Proxmox notification target and matcher (`docs/observability/layer1.md`). `pvesh` can do this; it was left as a UI step. PVE 9 uses Handlebars (`{{ severity }}`), and `pvesh --body` must be base64.
- TrueNAS alert service, Slack type, Discord webhook URL with `/slack` appended.
- PVE API token `PVE_TOKEN_VALUE` and `LINODE_TOKEN` in `obsv/.env`.

## Backup holes (a rebuild from restic is not complete)

`scripts/backup/backup.sh` covers arrstack config, Jellyfin on CT 104, AdGuard, Newt, `/etc/pve`, the compose share, and a TrueNAS staging dir.

- `/arrstack/identity` (Authentik Postgres and Caddy data) and `/arrstack/obsv` are not in the backup. Authentik users, sessions, and OIDC state would be lost. Postgres is not `pg_dump`ed anywhere.
- The Pangolin leg shells to `<pangolin-vps>` and currently fails (`pangolin_ok=0`): pve has no SSH key there. No alert watches that gauge.
- `/root/.restic-password` on pve must be copied to a password manager. The repo is unreadable without it.
- Media library (~1.5 TB) and the TrueNAS pool itself are not backed up. Offsite was deferred.
- `set -e` plus a missing CT path can abort the whole run (this happened on CT 101).

## Secrets that have to exist before automation

Generated by scripts on a fresh clone: Authentik secret key, Postgres password, *arr API keys, Mealie sync token, Pangolin `SERVER_SECRET`.

Must be brought in from outside:

| Secret | Where it comes from |
| --- | --- |
| `WIREGUARD_PRIVATE_KEY` | Nord dashboard, service credentials (not the account password) |
| `CLOUDFLARE_API_TOKEN` | Cloudflare, zone-scoped |
| `DISCORD_WEBHOOK_URL` | Discord |
| `PVE_TOKEN_VALUE`, `LINODE_TOKEN` | Proxmox, Linode |
| `OURGROCERIES_*` | `credentials.env` on the data volume |
| `MEALIE_API_TOKEN` | Mealie UI after the admin exists |
| restic password | password manager, not the repo |
| CIFS/NFS credentials | TrueNAS user `zinthos` (today in pve fstab) |

`buildarr.yml` in the initial git commit still contains old API keys. Rotate anything that was ever in that file before treating the repo as shareable.

## Where Ansible fits

Ansible earns its place on the **substrate**: the four LXC guests, pve fstab and packages, Docker-in-LXC, TrueNAS shares, and the Linode baseline. Those are idempotent host facts, they were typed by hand over SSH, and `deploy.sh` refuses to know about them.

Ansible should not replace `deploy.sh`. The app layer is already declarative (Compose, Authentik blueprints, Recyclarr, Configarr, the `scripts/bootstrap/` Python). Rewriting that as roles would duplicate it and lose the CIFS atomic-replace and API-key seeding behavior that took weeks to get right.

Suggested layout, if we add it:

```text
infra/                  # Ansible, run from the workstation
  inventory.ini         # pve, truenas, pangolin-vps
  host_vars/
  roles/
    pve_mounts/         # fstab from a credentials file, not inline passwords
    lxc_guests/         # pct create/set for 100, 102, 103, 104 (proxmox module or pct over SSH)
    lxc_docker/         # Docker, python3, rsync, tun, resolv.conf → <adguard-host>
    adguard_install/    # binary + systemd, then hand off to ./deploy.sh adguard
    restic_repo/        # mount, restic install, init if missing
    truenas_shares/     # midclt: datasets, SMB, NFS maproot, snapshot script
    pangolin_vps/       # swap, sshd, docker; then pangolin/deploy.sh
```

Then a single `make rebuild` (or a shell script) runs Ansible, then `./deploy.sh` in the order above.

Alternatives considered and rejected as the primary tool:

- **Terraform / OpenTofu** for Proxmox and Linode. Good for the VPS and the CT definitions, awkward for fstab, Docker, and TrueNAS. Two tools for one substrate is worse than Ansible plus the Linode and Proxmox modules.
- **cloud-init** inside the CTs. Fine for resolv.conf and packages, but Proxmox LXC cloud-init is thin and does not create the CT or the bind mounts.
- **More bash in `deploy.sh`.** That is how the one-offs happened. `deploy.sh` SSHes to a guest that already exists; it is the wrong layer for `pct create`.

Things no tool will remove: domain registration, Cloudflare and Nord accounts, the Pangolin setup token and per-user grants, Authentik invitations and MFA, the Seerr and SABnzbd wizards, Mealie and OurGroceries accounts, per-phone VPN DNS, and the AT&T IPv6 choice.

## Plan of attack

Ordered so each step makes the next rebuild shorter. Steps 1–3 are the ones that hurt if the SSD dies tomorrow.

1. **qBit bootstrap.** Extend `qbit-ssd-cleanup.py` (or a new `scripts/bootstrap/qbittorrent.py` called from `deploy.sh arrstack`) to set `tun0`, the category save paths, and a non-zero connection speed. Align Configarr category names with those paths. Create the download directories in the arrstack mkdir list.
2. **AdGuard beyond filters.** Teach `apply-dns-rewrites.py` (or a sibling) to set upstreams, the DHCP range, and static leases, and have `deploy.sh adguard` install the binary and systemd unit when `/opt/AdGuardHome` is missing. Keep the wizard admin user out of git; restore it from restic or set it via the AdGuard API on first boot.
3. **Guest definitions.** An Ansible role (or a checked-in `pct create` script) for CTs 100, 102, 103, and 104, plus pve fstab with credentials from a root-only file. Include tun, GPU, nesting, and the 110000 uid note.
4. **Backup the identity database.** `pg_dump` the Authentik Postgres into the restic run. Confirm `./deploy.sh pve` is what installs `backup.sh` (it is) and fix the Pangolin SSH key from pve so `pangolin_ok` stays 1. Alert on that gauge.
5. **Notification bootstrap.** `pvesh` for the Discord matcher, and `midclt` for the TrueNAS Slack alert service, called from `deploy.sh pve` or the TrueNAS role.
6. **Caddy mealie-og-sync upstream.** Point `sync.<lan-domain>` at the container DNS name, the same fix qBit already got.
7. **Leave the rest manual** and keep `print_identity_manual_steps.py` as the checklist: Pangolin IdP, users, MFA, usenet purchase, Seerr first login, phone DNS.

