# pangolin — remote access

Two hosts, one system. A public VPS terminates TLS and holds the control plane; a dedicated LXC at
home dials out to it. Built 2026-08-15.

Configuration lives in this repo under `pangolin/` and is deployed from there. Editing files on
either host directly will be overwritten by the next deploy.

## Why this exists

Jellyfin needed to be reachable away from home, including by people who will not install a VPN
client, and the owner wanted their own devices to reach AdGuard and the LAN while travelling.
Pangolin covers both from one control plane: a public HTTPS resource for the first case, a
WireGuard client for the second.

No inbound ports are open on the AT&T gateway. The connector establishes an outbound tunnel, so the
home network keeps no listening services exposed to the internet.

## VPS — Pangolin server

| Property | Value |
| --- | --- |
| Provider | Linode (Akamai) Nanode 1 GB, $5/mo |
| Address | `172.234.196.180` |
| OS | Ubuntu 24.04.4 LTS |
| Resources | 1 vCPU, 1 GB RAM, 25 GB disk, **1 TB transfer/mo** |
| Hostname | `pangolin` |
| Stack path | `/opt/pangolin` |
| Access | `ssh <pangolin-vps>`, key only |

Baseline changes made at build time:

- **2 GB swapfile** at `/swapfile`. Pangolin's docs want 2 GB RAM; on a 1 GB Nanode swap is what
  keeps Docker pulls and updates from being OOM-killed. Linode's own 496 MB swap partition is not
  enough on its own.
- **SSH hardened** to keys only — `PasswordAuthentication no`, `PermitRootLogin prohibit-password`
  in `/etc/ssh/sshd_config.d/99-hardening.conf`.
- **Host `ufw` left inactive** deliberately. The Linode Cloud Firewall is the single enforcement
  point; running both invites a rule that exists in one place and not the other.

### Containers

| Service | Image | Role |
| --- | --- | --- |
| `pangolin` | `fosrl/pangolin:1.21.1` | dashboard, API, access control, SQLite state |
| `gerbil` | `fosrl/gerbil:1.4.3` | WireGuard endpoint; owns all published host ports |
| `traefik` | `traefik:v3.6` | TLS termination and routing, Badger plugin `v1.5.0` |

Versions are pinned in `pangolin/.env`, not `latest`, so a redeploy cannot silently change what is
running. Traefik uses `network_mode: service:gerbil`, so **ports 80 and 443 appear on the `gerbil`
service** — to change them, edit `gerbil`, not `traefik`. Same pattern as Gluetun in the media stack.

### Firewall

Linode Cloud Firewall, attached to this instance:

| Direction | Protocol | Ports | Purpose |
| --- | --- | --- | --- |
| In | TCP | 22 | SSH — restrict to known source addresses |
| In | TCP | 80, 443 | ACME HTTP-01 challenge and published resources |
| In | UDP | 51820 | site tunnels (Newt) |
| In | UDP | 21820 | client hole punch and relay (Olm) |

Tightening TCP 22 to a single source locked out the workstation once already. The workstation's
public address is dynamic AT&T, so it will happen again on lease change; Lish is the way back in.

Two traps when narrowing the SSH rule:

- **The workstation has a public IPv6** as well as IPv4 (`108.95.31.222` and
  `2600:1702:6400:bb0:…` at time of writing). SSH will happily prefer the v6 route, so an allow rule
  covering only the v4 address silently fails. Allow both, or force `ssh -4`.
- Both addresses are dynamic. Anything pinned here has a shelf life.

## CT 103 `newt` — site connector

| Property | Value |
| --- | --- |
| CTID | 103 |
| Hostname | `newt` |
| Address | `<newt-host>/24`, gateway `192.168.1.254`, **static** |
| Template | `debian-13-standard_13.1-2` |
| Resources | 1 core, 1024 MB RAM, 512 MB swap, 8 GB rootfs on `local-lvm` |
| Unprivileged | yes, `nesting=1,keyctl=1` (both required for Docker inside the container) |
| `onboot` | `1` |
| Timezone | `America/New_York`, set at creation (see F-05) |
| DNS | `<adguard-host>` with `9.9.9.9` as fallback |
| Stack path | `/opt/pangolin-site` |
| Newt networking | `network_mode: host` + `cap_add: NET_ADMIN` (userspace WireGuard) |

**Why a dedicated container rather than CT 100.** The connector has to reach `<jellyfin-host>`
(Jellyfin) and `<adguard-host>` (AdGuard) by direct LAN routing. On CT 100 there is a standing risk of
it being pulled onto Gluetun's network namespace, which would break exactly that. A separate
container also keeps the credential that can reach the whole LAN out of the media stack.

**Why host networking.** Olm clients only receive a site after Newt completes `newt/wg/get-config`,
which requires a recent UDP hole punch (Gerbil **21820**). On Docker's default bridge, Newt
advertises a container IP and get-config stays stuck; Pangolin then logs
`Site 1 has no public key, skipping` and the phone shows the org with **no sites**. Host mode makes
hole punch use `<newt-host>`. After changing compose, restart Newt so get-config runs with UDP
open — a tunnel that only recovered for HTTP (Jellyfin) is not enough for the client VPN.

**Why `.121`.** `.120` was already answering. `.121` sits below AdGuard's DHCP pool
(`192.168.1.130`–`.250`), so it will not be handed out to anything else. It is set statically in the
container config rather than as a DHCP reservation — unlike CT 100, this host will not
drift.

The 8 GB rootfs was added to a thin pool already at ~75% allocation. See F-02 and F-28: the pool is
the most likely cause of an outage in this homelab, and this container made it slightly worse.

## Two access paths (do not confuse them)

| Path | Who | How | Needs site WG key? |
| --- | --- | --- | --- |
| Public HTTPS | Friends / anyone with a Jellyfin login | `https://jellyfin.<domain>` / `https://seerr.<domain>` → Traefik → Newt | No — works even if Olm clients see no sites |
| Client VPN | You, Pangolin app | Olm WireGuard → private host/CIDR resources | **Yes** — without a completed `newt/wg/get-config`, Pangolin skips the site |

Log the phone into **`https://pangolin.<domain>`**, not Pangolin Cloud. Aliases / DNS Over Tunnel
stay grayed out until the client is given a site. Do not test the `<lan-cidr>` private resource
while on home WiFi — that CIDR overlaps the phone's LAN and steals local routes.

With the VPN up: enable **Aliases (Override DNS)** (informational "see docs" warning is normal), then **DNS Over Tunnel**, upstream **`<adguard-host>`** (AdGuard — use the IP, not a hostname). Optional **Match Domains**: `*.<lan-domain>` so carrier DNS handles everything else.

Without DNS Over Tunnel, `*.<lan-domain>` used to hit a zone-wide Namecheap `*` A and return
Pangolin **404** for unpublished admin hosts. After replacing `*` with explicit public A records,
those names are NXDOMAIN on the public internet — still broken off-LAN without tunnel DNS, but no
longer a confusing Traefik 404. `auth.<domain>` / `jellyfin.<domain>` / `seerr.<domain>`
work without tunnel DNS (public resources).

**On home WiFi:** disconnect the Pangolin client (or enable DNS Over Tunnel). Overlapping
`<lan-cidr>` steals LAN routes. Also turn off Android Private DNS / iCloud Private Relay so the
phone uses AdGuard — see F-31.

See [docs/identity/deploy.md](../identity/deploy.md#remote-access-pangolin-vpn) for the full client checklist.

## Published resources

Defined declaratively in `pangolin/site/blueprint.yaml`, which Newt reapplies continuously. **The
blueprint is the source of truth** — dashboard edits to anything in it are reverted on the next
apply. Private-resource `users:` must be listed in the YAML or Newt will wipe dashboard-only user
grants (Admin role grants are kept; `Admin` cannot appear under `roles:`).

| Resource | Kind | Destination | Who |
| --- | --- | --- | --- |
| `jellyfin.<domain>` | public HTTP | `<jellyfin-host>:8096` | anyone with a Jellyfin account |
| `seerr.<domain>` | public HTTP | `<arrstack-host>:5055` | anyone with a Jellyfin account (requests) |
| `auth.<domain>` | public HTTP | `<arrstack-host>:9000` | Authentik login (Pangolin SSO off; OIDC IdP for Pangolin app) |
| AdGuard Home | private host | `<adguard-host>` tcp 53,80,3000 / udp 53 | Pangolin clients (pinned user in blueprint) |
| Home LAN | private CIDR | `<lan-cidr>` | Pangolin clients (pinned user in blueprint) |

**Pangolin SSO is deliberately off for Jellyfin and Seerr.** Native Jellyfin clients — Android TV,
iOS, Roku — cannot complete a browser-based auth flow, and Seerr reuses Jellyfin login. Enabling
Pangolin SSO would break clients and force Authentik accounts. Pangolin is only terminating TLS and
tunnelling. Pangolin login federates to Authentik — see [docs/identity/pangolin-oidc.md](../identity/pangolin-oidc.md).
Admin UIs stay unpublished; reach them over the client VPN + `*.<lan-domain>` (Caddy).

## DNS

`<domain>` is registered at Namecheap. **Nameservers are Cloudflare** (Free plan, DNS-only).
No paid certificate was purchased — Traefik on the VPS obtains Let's Encrypt certificates over
HTTP-01 for the public hosts. LAN Caddy obtains `*.<lan-domain>` over DNS-01 (Cloudflare API).

**Do not use a zone-wide `*` A.** Namecheap's `*` answers every name in the zone (including
`sonarr.<lan-domain>`), which sent phones that skipped AdGuard to Pangolin and produced
intermittent 404s (F-31). Publish only the public hosts:

| Type | Host | Value |
| --- | --- | --- |
| A | `pangolin` | `172.234.196.180` |
| A | `jellyfin` | `172.234.196.180` |
| A | `auth` | `172.234.196.180` |
| A | `seerr` | `172.234.196.180` |

Delete Host `*`, `home`, and `*.home` if present. After DNS propagates:

```bash
./scripts/check_home_dns_public.sh
```

LAN names must be NXDOMAIN on public resolvers; AdGuard still rewrites `*.<lan-domain>` →
`<arrstack-host>`. New public resources need an explicit A at Cloudflare before Traefik can serve them.
The apex may still point at Namecheap parking — nothing serves it.

## Bandwidth is the real limit

Friends' Jellyfin traffic always transits the VPS, and the Nanode includes 1 TB of transfer per
month. 1080p at roughly 8 Mbps is about 3–4 GB per hour, so 1 TB is on the order of 250 hours. 4K
remuxes are an order of magnitude worse and can exhaust it in a weekend. Overage runs about
$0.01/GB.

If this gets close, cap remote streaming quality in Jellyfin rather than paying for transfer. The
owner's own client traffic is peer-to-peer where NAT allows and usually does not count against it.

## Follow-ups

- Linode transfer is scraped by `linode-exporter` and alerted by `LinodeTransferWarn` /
  `LinodeExporterDown` in `obsv/vmalert-metrics-rules.yaml`.
- `/opt/pangolin/config` is in the nightly restic run (tag `pangolin`) when pve can SSH to the VPS.
  `PangolinBackupFailed` fires if that leg did not succeed. Losing it still means rebuilding the
  org, sites, and users by hand.
- The Nanode is a second single point of failure for remote access, unrelated to F-23's DNS one. If
  it goes away, remote Jellyfin and the client VPN both stop; the LAN is unaffected.
- Consider restricting TCP 22 on the Cloud Firewall to a stable source, or fronting SSH with
  Pangolin itself, so the lockout dance is not repeated.
