# inspiron-7559 — Dell Inspiron 15 7559 (spare)

Spare laptop, not on the LAN. Inventoried 2026-08-17 from owner-reported specs plus the
Dell Inspiron 15 7559 specification sheet. Storage is a 256 GB 2.5" SSD. Still unknown:
display SKU, whether the M.2 slot is populated, RAM topology (1×8 vs 2×4), battery health,
and current OS. Dell's service-tag portal was not reachable from here, so the original
factory config and ship date were not confirmed.

No role assigned. Candidate uses are below; the decision is tracked as F-36.

## Identity

| Property | Value |
| --- | --- |
| Model | Dell Inspiron 15 7559 |
| Regulatory | P57F / P57F002 |
| Service tag | `1JY6LD2` |
| Express service code | `3383054246` |
| Generation | Skylake-era 15.6" gaming laptop, late 2015 |
| Warranty | Expired. Typical Inspiron coverage was 1 year; this chassis is ~11 years old. |
| Status | Spare. Not addressed, not in the fleet diagram, not a dependency of anything. |

Dell support page (needs the tag entered if the deep link 403s):
https://www.dell.com/support/home/en-us/product-support/servicetag/1jy6ld2/overview

## Hardware (confirmed)

- CPU: Intel Core i7-6700HQ (Skylake, 45 W). 4 cores / 8 threads, 2.60 GHz base, 3.50 GHz turbo, 6 MB cache. VT-x; VT-d is BIOS-dependent and unconfirmed.
- iGPU: Intel HD Graphics 530. Quick Sync does H.264 well; Skylake HEVC encode is a generation behind the HD 630 already on pve.
- dGPU: NVIDIA GeForce GTX 960M, 4 GB GDDR5 (Maxwell GM107). NVENC is 2nd-gen Maxwell: usable H.264, first-gen HEVC without B-frames. Optimus hybrid graphics.
- RAM: 8 GB DDR3L-1600. Two SODIMM slots, official maximum 16 GB (4/8/12/16). Topology unknown — 1×8 GB vs 2×4 GB changes whether an upgrade is "add a stick" or "replace both." Third-party vendors list 2×16 GB (32 GB) as working on this chassis; that is unofficial and a gamble, not a Dell spec.
- Storage: 256 GB 2.5" SATA SSD. The M.2 2280 SATA slot is presumed empty (unconfirmed). Do not buy NVMe for that slot.
- Chipset: Intel HM170.

## Hardware (platform, not this unit)

These are true of every 7559, not yet verified on `1JY6LD2`.

- Display: 15.6" — HD 1366×768, FHD 1920×1080, or UHD 3840×2160 touch. Unknown which.
- Storage: one 2.5" SATA 6 Gbps bay **and** one M.2 2280 slot. The M.2 slot is **SATA only**. NVMe drives physically fit an M-key slot and will not enumerate.
- Network: Gigabit RJ45, Wi-Fi 802.11ac, Bluetooth 4.0.
- Ports: 3× USB 3.0 (one PowerShare), HDMI, headset jack, SD reader, Kensington lock. No USB-C, no Thunderbolt.
- Battery: 6-cell 74 Wh, integrated. 130 W / 19.5 V adapter.
- Size: ~383 × 265 × 25 mm, ~2.58 kg (5.7 lb).

## Compared with the rest of the fleet

| | This laptop | pve (i5-7500) | truenas (A10-7850K) |
| --- | --- | --- | --- |
| Cores / threads | 4C / 8T | 4C / 4T | 4C / 4T |
| RAM | 8 GB DDR3L SODIMM | 16 GB | 8 GB DDR3 DIMM (~6.7 GB usable) |
| Transcode | HD 530 + Maxwell NVENC | HD 630 QSV (already wired to Jellyfin) | none worth using |
| Always-on fitness | laptop: sleep, thermals, no IPMI | desktop, already the hypervisor | desktop, already the NAS |
| Independent of pve | yes | — | yes |

Thread count looks better than pve on paper. It is not a compute upgrade. The 6700HQ is a 45 W mobile part in a 2015 chassis; pve is a 65 W desktop with better Quick Sync. Do not plan Tdarr, Jellyfin transcode, or a second hypervisor around this machine. F-06 (pass `/dev/dri` into arrstack) remains the transcode fix.

The SODIMMs cannot move into TrueNAS. F-18 wants DIMMs.

## What it is actually good for here

The hole in this fleet is not CPU. It is **services that should survive a pve reboot**, sitting on hardware that is not an SD card.

### 1. Home Assistant + Zigbee2MQTT + MQTT — strongest fit

F-26 is still open: HA, automations, and the MQTT broker died with the Pi. The Zigbee network itself survives in the coordinator radio. This chassis is a better HA host than the Pi 3 ever was (8 GB vs 1 GB, real disk, gigabit Ethernet, USB for the dongle) and a better HA host than an LXC on pve **if** the goal is "lights still work when Proxmox is patching."

Put it where the Pi sat, or wherever mesh coverage actually needs the radio. F-26 already says radio placement is a constraint, not an afterthought.

Do this on Debian, Intel iGPU only, sleep/hibernate off, lid-close set to ignore, Ethernet not Wi-Fi. Do not install the NVIDIA driver unless a later decision specifically needs the 960M. This model's Optimus stack on Linux is a known time sink (sleep freezes, `GPU has fallen off the bus`).

### 2. Secondary AdGuard — the F-23 box

F-23 wants a second DNS instance on **separate hardware** so a pve reboot does not take name resolution with it. The Pi was that hardware and is gone. This laptop is currently the only spare machine that is not pve.

DNS does not need an i7 or a 960M. That is fine. Overkill hardware that is independent beats a second LXC on the same host.

HA + secondary AdGuard on the same box is reasonable. Recreating the Pi's "DNS + DHCP + HA + Zigbee all on one fragile disk" pattern is not — keep DHCP primary on CT 102, keep this copy as resolver-only, and put backups in place before declaring it done (F-01).

### 3. Local LLM — feasible as a toy, not as a daily assistant

The 960M is CUDA 5.0 Maxwell, 4 GB GDDR5, ~80 GB/s. Ollama / llama.cpp still accept compute 5.0; current PyTorch wheels do not. So the path is GGUF only (Ollama or llama.cpp), never HuggingFace/vLLM.

8 GB of system RAM is the actual blocker. A 3–4B Q4 model plus Linux plus a chat UI will fit; a 7B Q4 will swap. **16 GB of DDR3L is the upgrade that makes an LLM experiment sane.** More disk does not.

Realistic ceiling after 16 GB, fully in VRAM, short context (2–4k):

| Model class | Fits in 4 GB VRAM? | Expected speed on this 960M |
| --- | --- | --- |
| 1–1.7B Q4/Q8 (Llama 3.2 1B, SmolLM) | yes | usable, maybe 20–40 tok/s |
| 3–4B Q4 (Llama 3.2 3B, Qwen 3 4B, Phi-4-mini) | yes, keep ctx small | the sweet spot; maybe 8–18 tok/s |
| 7–8B Q4 | no — partial CPU offload | 2–8 tok/s on DDR3L-1600; often slower than CPU-only on a modern desktop |
| 13B+ | no | not worth it even with unofficial 32 GB |

Those 3–4B numbers are discounted from GTX 1650 4 GB figures. The 960M has no tensor cores and about half the memory bandwidth. Treat them as "chat you can wait for," not Cursor.

Do not plan: image gen (SD 1.5 is a stretch, SDXL is out), a coding copilot that feels local-Cursor, RAG over the media library, or anything that wants PyTorch.

Running an LLM means installing the NVIDIA driver, which is the Optimus time sink this host was otherwise going to avoid. Fine for a dedicated experiment box; a tax if this is also the always-on HA host. Thermals: burst chat is fine, continuous generation will throttle a 2015 45 W chassis.

### 4. What RAM and disk actually buy

The 256 GB SSD already removes the "put an SSD in it" item. Replacing it with a bigger 2.5" drive does not change any role. Adding a SATA M.2 (500 GB–1 TB) only matters if a GGUF collection starts to crowd the OS disk — a handful of 2–8 GB models fit on 256 GB under Debian.

| Upgrade | HA / AdGuard | Small local LLM | Light docker playground | Second Proxmox node |
| --- | --- | --- | --- | --- |
| Keep 8 GB + 256 GB SSD | already enough | 3B only, tight | tight | no |
| **16 GB DDR3L (the one to buy)** | nicer headroom | 3–4B comfortable; 7B offload without swap | yes | still a laptop |
| Bigger / second SSD | no | model library only | maybe | still a laptop |
| Unofficial 32 GB | wasted | 13B at 1–3 tok/s. Skip. | wasted | still a laptop |

Buy: one matching 8 GB DDR3L-1600 SODIMM if this is a single 8 GB stick, or a 2×8 GB kit if it is 2×4. Confirm topology before ordering. Official max remains 16 GB.

Do not buy: NVMe, a replacement for the working 256 GB unless it is dying, or 32 GB on speculation.

Debian 12/13 either way. Windows 10 is past end of support; this CPU is not on Microsoft's Windows 11 list.

## What not to use it for

- **Tdarr / NVENC worker.** Maxwell HEVC is poor, GeForce NVENC is session-limited, and pve already has a better encoder sitting idle (F-06). Fighting Optimus for this is wasted effort.
- **Second Proxmox node.** 8 GB, laptop NIC, no out-of-band management, firmware that wants to sleep.
- **NAS or backup target.** TrueNAS exists. A laptop HDD is not a second disk for F-12.
- **Daily driver in 2026.** 8 GB, Skylake, 5.7 lb, dead Windows. A Linux rescue laptop is the ceiling.
- **Parts donor into TrueNAS or pve.** Wrong RAM form factor, wrong era.

## Open questions (need a look at the machine)

- Does it power on? Battery swollen?
- Display resolution?
- Is the M.2 slot empty, as assumed?
- 1×8 GB or 2×4 GB? Confirm before buying RAM.
- Current OS and whether the disk is worth wiping?
- Where it will physically live if it becomes the Zigbee host?

## Related

- F-36 — decide a role or explicitly leave it spare
- F-26 — Home Assistant rebuild
- F-23 — DNS on a second machine
- F-06 — transcode belongs on pve's Quick Sync, not here
- [rpi.md](rpi.md) — what this would be replacing
