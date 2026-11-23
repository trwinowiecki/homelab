# rpi — Raspberry Pi 3 (DECOMMISSIONED)

Former network DNS, DHCP, and home automation hub. **Dead as of 2026-08-14** — the SD card failed at
the controller level during reinsertion, then split physically in half during the reseat attempt.
See F-29 and F-30.

This document is retained as a historical record only. The fleet no longer includes this host, and
nothing should be planned against it.

- **DNS and DHCP** were replaced the same night by CT 102 `adguard` on pve, at the same address
  `<adguard-host>`. See [adguard.md](adguard.md).
- **Home Assistant, its automations, and the MQTT broker** were lost with the card and have no
  replacement yet. Rebuilding is tracked in F-26.
- **The Zigbee network itself survives** in the coordinator radio's NVRAM. Devices remain joined;
  what was lost is the Zigbee2MQTT friendly-name database and the automations built on top. Do not
  let a new Zigbee2MQTT install form a fresh network, or that survival is thrown away too.

Everything below was inventoried 2026-08-14 by external probing only, before the failure. No shell
access was ever obtained, so it is inferred from the network — and it is now the only surviving
record of what this host ran. It cannot be confirmed against the host, so treat it as the best
available reconstruction rather than fact.

## Status

- The host does not boot and does not answer on any port.
- The SD card enumerates as a 30.6 MiB device with garbage identity registers on any reader. Its
  real identity was a 128 GB SanDisk holding a FAT boot partition and an ext4 root. No software
  recovery is possible; see F-29 for the evidence and handling rules.
- Whether the Pi itself survived is untested. The snap heard on reinsertion may have been the
  microSD slot breaking off the board. A freshly flashed card will distinguish the two.

## Access (historical)

- Address: `<adguard-host>`
- SSH: was **locked out.** The server accepted `publickey` only — password authentication was
  disabled — and the then-current key was rejected for `admin`, `pi`, `root`, `dietpi`, `taylor`,
  and `ubuntu`. Some other key, on some other machine, was presumably authorized. Moot now.

## Platform

- Debian 11 Bullseye, inferred from the SSH banner `OpenSSH_8.4p1 Debian-5+deb11u2`
- Debian 11's LTS support window closes at the end of August 2026, so this host is days away from
  receiving no further security updates
- Raspberry Pi 3, 1 GB RAM, SD card storage

## Services

Identified by port scan and HTTP title probe.

| Port | Service | Notes |
| --- | --- | --- |
| 22 | SSH | Publickey only, locked out |
| 53 | DNS | Pi-hole, serves the whole network |
| 80 | Pi-hole admin | Returns 403 at `/`; the UI is at `/admin` |
| 1883 | MQTT broker | Presumably Mosquitto |
| 8080 | Zigbee2MQTT | Confirms an attached Zigbee coordinator radio |
| 8123 | Home Assistant | |
| 67/udp | DHCP server | **Missed by the original TCP port scan.** Confirmed after the failure from a client lease: `dhcp_server_identifier = <adguard-host>`, `domain_name = lan`, 86400s leases. |

Five services on a Pi 3 with 1 GB of RAM, all writing to the same SD card — and one of them was
addressing for the entire house.

## Why this host is the most fragile thing in the fleet

Every other machine here is either healthy or fixable at leisure. This one combines several risks
that compound:

- It holds Zigbee pairing state that exists nowhere else. Losing it means physically re-pairing
  every device — see F-24.
- It holds a Home Assistant configuration with no backup.
- It runs network DNS, so its failure is immediately visible to everyone in the house.
- It is on an SD card, under constant small writes from Home Assistant's recorder, Zigbee2MQTT's
  database, and Pi-hole's query log. This is the classic wear-out pattern.
- Nobody can log in to change any of that.
- Its OS stops receiving security patches this month.

## What the failure cost

Step 1 of the recovery sequence this document previously specified — copy off the Zigbee2MQTT data
directory before anything else — was never reached. The card died during the attempt to regain the
access that would have made it possible.

Gone: the Home Assistant configuration, automations, dashboards, entity registry, and recorder
history; Zigbee2MQTT's `database.db` and `coordinator_backup.json`; the Pi-hole configuration,
adlists, and local DNS records; and the DHCP reservation table.

Not gone: the Zigbee network itself, which lives in the coordinator radio's NVRAM. See F-24 for the
rule that protects it during the rebuild.

## Rebuild sequence

1. **Restore addressing first.** Re-enable DHCP on the AT&T gateway as a stopgap. Everything else
   can wait; a house without DHCP cannot.
2. **Stand up AdGuard Home** for DNS and DHCP, per the F-21 decision. An LXC on pve is the natural
   home. Give it `<adguard-host>` — clients already hold leases naming that address as their DHCP
   server, so reusing it makes renewal seamless. Mind F-23: whichever host serves DHCP becomes a
   hard dependency for the whole network.
3. **Rebuild Zigbee2MQTT against the surviving coordinator**, letting it inherit the radio's
   existing network. Read F-24 before touching `configuration.yaml`.
4. **Rebuild Home Assistant** from scratch. Size it properly this time — F-26 argues it should not
   go back onto a Pi 3 regardless of whether this one still works.
5. **Configure backups before the rebuild is declared done**, not after. F-01 and F-24 both said
   this in advance and neither was acted on in time.

## Open questions

- Did the Pi 3 hardware survive, or did the snap take the microSD slot with it?
- What Zigbee coordinator model is attached, and where is it physically located? Mesh coverage
  depends on its position, which constrains whether it can move to the Proxmox box. This is now the
  single most important unknown, because that radio holds the only surviving copy of the Zigbee
  network.
- Which DHCP reservations existed? Any device that depended on a fixed address from the Pi will
  come back on a different one. Static-IP assumptions elsewhere in the fleet may now be wrong.
- Is `192.168.1.87` — which appeared as a secondary resolver in client `resolv.conf` but does not
  answer DNS — a stale entry, or a device that matters?
