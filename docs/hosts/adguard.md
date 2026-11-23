# adguard — network DNS and DHCP

Replaces the failed Raspberry Pi (F-29, F-30). Built 2026-08-14.

This host is network infrastructure: if it is down, nothing in the house resolves names, and
eventually nothing holds an address. Treat changes here with the same care as changes to the
gateway. See F-23.

## Placement

CT 102 on `pve`, on `vmbr0`, so it is bridged directly onto the LAN rather than NAT'd. That is a
requirement, not a preference — DHCP relies on broadcast traffic reaching clients on the same layer
2 segment.

| Property | Value |
| --- | --- |
| CTID | 102 |
| Hostname | `adguard` |
| Address | `<adguard-host>/24`, gateway `192.168.1.254`, static |
| MAC | `BC:24:11:7C:73:CD` |
| Template | `debian-13-standard_13.1-2` |
| Resources | 1 core, 1024 MB RAM, 512 MB swap, 4 GB rootfs on `local-lvm` |
| Unprivileged | yes, with `nesting=1` (required by systemd 257 on Debian 13) |
| `onboot` | `1` — mandatory, this is network infrastructure |
| Timezone | `America/New_York` (deliberately set at creation; see F-05 for what happens when it isn't) |

**Why `<adguard-host>`.** It is the address the failed Pi used. Every client that still holds a lease
names `<adguard-host>` as its DHCP server, so reusing it lets those clients renew against the
replacement without a fight. It is not sentimental — it shortens the outage.

## AdGuard Home

- Version v0.107.78, installed from the upstream release tarball to `/opt/AdGuardHome`
- systemd unit `AdGuardHome`, enabled and active
- Web UI on port 3000
- Capabilities verified present for the DHCP server: `cap_net_raw`, `cap_net_bind_service`,
  `cap_net_admin`. AdGuard's DHCP server needs raw sockets, which is the usual reason it fails in an
  unprivileged container. It is not a problem here.
- Nothing else occupies port 53 in the container; `systemd-resolved` is inactive.

Chosen over Pi-hole per the F-21 decision, because the entire configuration is one YAML file and can
therefore live in this repo.

## Cutover

The AT&T gateway's DHCP server was re-enabled as an emergency stopgap when the Pi died. Two DHCP
servers on one segment is a race with an ugly failure mode — clients intermittently get the wrong
DNS, and the pattern changes on every renewal. So the order matters:

1. Complete the AdGuard setup wizard, binding DNS to port 53 on all interfaces.
2. Verify AdGuard answers DNS *before* touching the gateway.
3. Configure AdGuard's DHCP server but leave it disabled.
4. Disable DHCPv4 on the gateway.
5. Enable AdGuard's DHCP server.

**Address conflict risk during the overlap.** The BGW hands out roughly `.64`–`.253`, which includes
`<adguard-host>` and the static addresses below. While gateway DHCP is enabled, it can lease one of
these to some other device. Keep the overlap window short, and if anything loses its address, this
is the first thing to check.

## DHCP settings for the wizard

| Setting | Value |
| --- | --- |
| Gateway IP | `192.168.1.254` |
| Subnet mask | `255.255.255.0` |
| Lease duration | 86400s (24h) or longer — per F-23, long leases are the main defence against this host being a single point of failure |

Pick a range that excludes the static addresses below — `192.168.1.130`–`192.168.1.250` leaves room.

## Static reservations to re-create

The Pi's reservation table died with it. These are the four that matter, with addresses confirmed
live on 2026-08-14 before the rebuild:

| Host | Address | MAC | Notes |
| --- | --- | --- | --- |
| adguard (CT 102) | `<adguard-host>` | `BC:24:11:7C:73:CD` | static in `leases.json` as of 2026-08-15 |
| pve (Proxmox) | `<pve-host>` | `18:60:24:F8:0F:1B` | `vmbr0` |
| truenas | `<truenas-host>` | `D0:50:99:1C:4F:F2` | `enp4s0` |
| jellyfin-docker (CT 104) | `<jellyfin-host>` | `BC:24:11:A4:10:04` | production; static in LXC net0; LAN via Caddy on `.201` |
| arrstack (CT 100) | `<arrstack-host>` | `BC:24:11:BD:59:9B` | currently DHCP-assigned; see F-07 |

CT 100 takes its address from DHCP rather than static container config, so without
this reservation it will move. `arrstack` was not on the original request but belongs here —
F-07 already records `.env` disagreeing with its actual address, and letting it drift again would
make that worse.

## Upstream resolvers — why not the wizard default

The setup wizard defaults to `https://dns10.quad9.net/dns-query` (DoH). That endpoint failed
immediately and consistently, taking all name resolution down with it:

```
[error] dnsproxy: exchange failed upstream=https://dns10.quad9.net:443/dns-query
  question=";github.com. IN A" err="exchanging: ... unexpected EOF"
```

This was not a connectivity problem. From the container, ICMP to `1.1.1.1` succeeded, plain DNS
resolved, and general HTTPS worked over both IPv4 and IPv6 — Quad9 was specifically dropping
AdGuard's DoH connections. Upstreams were changed to plain `1.1.1.1`, `1.0.0.1`, and `9.9.9.9` to
restore service. The original file is backed up alongside as `AdGuardHome.yaml.bak-<epoch>`.

**This is a deliberate downgrade in privacy and should not be left as-is.** Plain DNS on port 53 is
visible to, and interceptable by, the ISP. Re-test an encrypted upstream — `tls://1.1.1.1` (DoT) or
`https://cloudflare-dns.com/dns-query` — and switch back once one is confirmed working. Verify with
a real query before trusting it; the failure mode here was a total resolution outage.

## Filter lists

Source of truth is `adguard/filters.yaml` in this repo. Ship with `./deploy.sh adguard`,
which stops AdGuard Home for a few seconds, replaces only the `filters:` block in
`/opt/AdGuardHome/AdGuardHome.yaml`, then starts it again. Do not edit filter URLs in
the live YAML by hand — the running process will overwrite the file.

HaGeZi Pro supersedes HaGeZi Normal, so Normal is kept in the config but disabled.
OISD Big and Steven Black stay omitted; they overlap Pro the same way they overlapped
Normal. Do not stack those on top.

| ID | List | Role | Notes |
| --- | --- | --- | --- |
| 1 | AdGuard DNS filter | baseline ads/trackers (wizard default) | enabled |
| 59 | AdGuard DNS Popup Hosts | popup / new-window ad hosts | enabled |
| 1001 | HaGeZi Pro | ads/trackers/junk (jsDelivr `@latest`) | enabled; replaces Normal |
| 3 | Peter Lowe's Blocklist | long-lived, low-false-positive ad hosts | enabled |
| 11 | URLHaus | live malware URLs | enabled |
| 44 | HaGeZi Threat Intelligence Feeds | malware/phishing/scam | enabled |
| 34 | HaGeZi Normal | superseded by Pro | disabled |
| 2 | AdAway | default wizard extra | disabled |

HaGeZi TIF is the expensive one. It pushed AdGuard to ~297 MB RSS and the original 512 MB container
into swap, so CT 102 was raised to 1024 MB RAM the same night. Pro is larger than Normal but small
next to TIF; watch RSS after the first download if this box starts swapping again.

## Known issues

- **DHCP range still includes `.119`.** The static reservation now prevents AdGuard from offering
  that address, which was the original risk. Narrowing the pool to `.130`–`.199` is still cleaner.
- **IPv6 clients bypass filtering entirely.** See F-31. This is the most significant open issue with
  the deployment and is not fixable from this host. Phones that skip AdGuard also lose
  `*.<lan-domain>` split-horizon (Pangolin 404). AdGuard ships a `*.<lan-domain>`
  rewrite for clients that *do* query it; Namecheap must not use a zone-wide `*` A.

## Follow-ups

- Filter lists live in `adguard/filters.yaml` and deploy with `./deploy.sh adguard`. The rest of
  `/opt/AdGuardHome/AdGuardHome.yaml` (DHCP, users, upstreams) is still live-only. It contains a
  bcrypt hash of the admin password, so pulling the whole file needs the same SOPS treatment as
  the rest (F-16).
- F-23's mitigation calls for a second DNS instance on separate hardware. Nothing here provides
  redundancy yet; a `pve` reboot still takes the network's name resolution with it.
- The adlists and custom local DNS records from the old Pi-hole are gone and will need rebuilding
  from scratch.
