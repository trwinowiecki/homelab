# Observability: logging, alerting, and an LLM-ready substrate

Date: 2026-08-16
Status: phase 1+2 implemented in-repo; deploy separately
Implements: F-35 (wanted). First consumer: F-34 (gluetun unhealthy). Also covers the paging half of F-02 / F-28 (thin-pool fill).

## Problem

Failures in this homelab are noticed by watching Portainer. Gluetun can sit `unhealthy` while qBittorrent and SABnzbd keep running on a dead VPN namespace (F-34). Docker does not restart unhealthy containers. Compose `depends_on.restart: true` only fires on an explicit Compose operation, not on a runtime restart of gluetun. Homepage and Portainer page nobody. Unpackerr's Notifiarr webhook is a placeholder (F-08). Docker healthcheck history is five entries; gluetun probes every five seconds, so the forensic window is about 25 seconds.

The owner wants: (1) a notification when something is unhealthy, (2) relevant logs with provenance, (3) a path to LLM root-cause analysis that does not collapse to "restart the service." Nothing auto-recovers. Discord is the sink. Collection is CT 100 only; alerting is fleet-wide via zero-agent hooks.

## Non-goals

cAdvisor, Prometheus-as-a-server, Netdata, ntfy, Gotify, autoheal, Watchtower, Diun, n8n, HolmesGPT as a service, agents on other LXCs, UPS/NUT, backups (F-01), IPv6 (F-14 / F-31), Unpackerr extract notifications (F-08). Those stay out even if they would be "easy to add." Grafana and VictoriaMetrics are phase 2 of this spec, not arrstack services.

Phase 2 (metrics + Grafana) is implemented in `obsv/` and `./deploy.sh pve`. Phase 3 (LLM webhook script) is sketched so this design does not block it. It is not part of implementation of this spec.

## Architecture

Two layers that share only a Discord webhook URL.

**Layer 1 — wide alerting, zero new agents.** Proxmox's built-in notification system, TrueNAS Alert Services, and a thin-pool cron on pve post straight to Discord. None of this lives on CT 100, so hypervisor-visible events still page if arrstack is down.

**Layer 2 — collection, log-based alerts, and metrics, CT 100 only.** A separate Docker Compose project named `obsv` (not services in `docker-compose.yaml`) runs VictoriaLogs, Vector, vmalert, VictoriaMetrics, vmalert-metrics, Grafana, pve-exporter, linode-exporter, a Discord relay, monocker, and Dozzle. Vector reads the Docker socket read-only and ships three streams into VictoriaLogs. vmalert evaluates LogsQL rules. A second vmalert evaluates PromQL against VictoriaMetrics. monocker is the store-independent health path. node_exporter runs on the pve host, not in this Compose file.

Discord is offsite. If AdGuard or the WAN is down, nothing pages. That gap is accepted. No local fallback notifier.

```
  pve cron (thin pool) ──┐
  PVE notifications ─────┤
  TrueNAS alerts ────────┼── Discord channel
  monocker ──────────────┤
  vmalert ──► relay ─────┤
  vmalert-metrics ───────┘

  docker socket (ro)
       │
     Vector ──► VictoriaLogs ◄── vmalert
       │
     Dozzle (live tail + MCP)

  node_exporter (pve:9100) ─┐
  pve-exporter ─────────────┼── VictoriaMetrics ◄── vmalert-metrics
  linode-exporter ──────────┘         │
                                   Grafana
```

## Placement

| Piece | Where | Why |
| --- | --- | --- |
| `obsv` Compose project | CT 100, separate project | Must survive arrstack `compose down` / `restart_no_sudo.sh` |
| VictoriaLogs data | bind mount `/arrstack/obsv/victoria-logs` on the LXC SSD | Must not depend on CIFS; must not be an anonymous volume (`restart_no_sudo.sh` runs `docker volume prune -f`) |
| Vector buffer | bind mount `/arrstack/obsv/vector-buffer` | Same prune reason |
| Compose files | this repo `obsv/`, copied to `/mnt/config/obsv` | Same deploy convention as the rest of the stack; workspace edits do nothing until synced |
| Layer 1 cron / PVE target | pve host | Independent of CT 100 |
| TrueNAS alert service | TrueNAS UI | Zero RAM; the box is already memory-tight (F-18) |
| VictoriaMetrics data | bind mount `/arrstack/obsv/victoria-metrics` | Same prune reason as logs |
| Grafana data | bind mount `/arrstack/obsv/grafana` (uid 472) | Same prune reason |
| node_exporter | pve host systemd, port 9100 | Docker-in-LXC misreports host memory |
| Nothing | TrueNAS Apps, CT 101, CT 102, CT 103 | Out of scope. Linode is scraped from CT 100, not instrumented on the VPS |

`obsv` is started with `docker compose -p obsv -f /mnt/config/obsv/docker-compose.yaml up -d`. It is never listed in arrstack's `mkdir -p` brace expansion. `restart_no_sudo.sh` must not stop it. Because that script prunes unused networks, volumes, and (on full arrstack success) unused images, `obsv` must stay running across an arrstack restart so its image and network count as used. Bind mounts mean prune cannot delete the log store even if someone stops the project.

Arrstack `docker-compose.yaml` gains a logging default only. No new arrstack services.

## Components

### Vector

Only process that mounts the Docker socket (read-only), shared with monocker and Dozzle which also mount it read-only.

Three sources plus one file tail:

1. `docker_logs` — container stdout/stderr.
2. `exec` — `docker events --format '{{json .}}'`.
3. `exec` on a 10-second interval — for every container with `State.Health.Status == unhealthy`, dump `{{json .State.Health}}` (the five-entry probe log) before Docker rotates it.
4. `file` — `/arrstack/obsv/deploy-markers.log` (`stream: deploy`).

A VRL transform stamps every record:

| Label | Example | Rule |
| --- | --- | --- |
| `host` | `ct100-arrstack` | Literal |
| `container` | `qbittorrent` | Strip a leading slash if present |
| `compose_service` | `qbittorrent` | From compose labels; fall back to `container` |
| `image` | `qmcgaw/gluetun:latest` | From inspect/event payload |
| `network_mode` | `service:gluetun` | Empty string if default bridge |
| `stream` | `stdout` / `stderr` / `event` / `health` / `deploy` | Source kind |

Sink: VictoriaLogs jsonline ingest. If VictoriaLogs is unreachable, Vector writes to the on-disk buffer (cap 256 MB) and drops oldest. Vector never becomes a Docker logging driver. Arrstack containers keep `json-file`.

Memory limit: 128 MB.

### VictoriaLogs

Single-node. `-retentionPeriod=14d` for all streams (no per-stream retention in this product; events are tiny and 14 days is enough). Hard `mem_limit: 512m`. Restart `unless-stopped`. Published LAN-only on host port **9428** (upstream default; not used by arrstack). Not published via Pangolin.

Memory limit is a cgroup cap, not `-memory.allowedPercent`. The process may OOM; that is preferred to ballooning into the *ARR apps. After OOM, Vector's buffer covers a short gap.

### vmalert

One instance, `type: vlogs` only. Interval 1m. Datasource is VictoriaLogs. It emits Alertmanager-format notifications, which Discord rejects. It POSTs to the Discord relay on the `obsv` network, not to Discord.

Memory limit: 64 MB. No host port.

Rules live in `obsv/vmalert-rules.yaml` in this repo. The phase-1 set:

| Alert | Expression intent | Discord meaning |
| --- | --- | --- |
| `ContainerUnhealthy` | `stream:health` or `stream:event` health_status=unhealthy | Container is unhealthy. Include last probe output if present. |
| `ContainerExited` | event `die` / status exited or dead | Container stopped. Include exit code. |
| `GluetunVpnRestart` | gluetun log line containing `restarting VPN because it failed to pass the healthcheck` | Issue #1407 class: namespace may be fine, qBittorrent listen port may be dead. Restart *qbittorrent only* if inbound peers are gone. |
| `GluetunDependentsStranded` | gluetun `destroy`/`create` (container id changed) or gluetun `die` followed by start without compose-level dependent recreate | Dependents share a dead netns. `docker restart` will not fix this. From `/mnt/config`: `docker compose up -d qbittorrent sabnzbd`. |
| `RestartStorm` | restart events for one container ≥ 3 in 10m | Flapping. Do not loop restarts; inspect logs. |
| `CollectorSilent` | no `stream:event` records in 5m | Vector is down while VictoriaLogs is up. If VictoriaLogs itself is down, this rule cannot run; monocker on the `victoria-logs` / `vector` containers covers that. |

Exact LogsQL is written at implementation time against a live ingest sample; the names and meanings above are the contract.

### Discord relay

A small service owned in `obsv/discord-relay` (script and Dockerfile in this repo, one container). Accepts Alertmanager webhook POSTs from vmalert, formats a Discord `content` body, POSTs to `${DISCORD_WEBHOOK_URL}`. Retries 429/5xx with backoff; after a small cap, drops the alert (stale pages are worse than missed ones here). No third-party relay image.

The webhook URL is known to: this relay, monocker, the pve cron, and the Proxmox/TrueNAS UIs. vmalert does not hold it.

Memory limit: 64 MB. No host port.

### monocker

`petersem/monocker`. `ONLY_OFFLINE_STATES` equivalent: alert on `paused`, `exited`, `running (unhealthy)`, `dead`. Discord as `MESSAGE_PLATFORM`. Period 30s. Docker socket read-only.

This is not redundant with vmalert. monocker has no storage dependency; vmalert has richer context. Duplicate pages in phase 1 are accepted. Prefix messages so the sender is obvious (`monocker` vs `vmalert`).

Memory limit: 64 MB.

### Dozzle

`amir20/dozzle`. Live tail plus built-in MCP. Host port **8880** (8080 is the unused Traefik placeholder; 8888 is gluetun HTTP proxy). LAN only, no Pangolin, no auth beyond LAN reachability — same posture as Portainer today.

Memory limit: 64 MB. Socket read-only.

### Layer 1 (not containers)

**Proxmox (pve).** PVE 9 webhook notification target with a Discord JSON template (`{"content":"..."}`), secret stored in `/etc/pve/priv/notifications.cfg`. Matcher on severity `warning` and `error`. A target with no matcher receives nothing — the matcher is required. Test with the GUI send-test or `pvesh`.

**TrueNAS.** Alert Service of type Slack, URL = Discord webhook with `/slack` appended. Enable SMART, scrub, pool, and capacity categories. Test with send-test.

**Thin-pool cron on pve.** Script `/usr/local/sbin/thinpool-alert.sh`, root crontab every 15 minutes. Reads LVM thin pool `data_percent` and `metadata_percent` via `lvs` (not `df`). Thresholds: warn data 80% / metadata 70%; the message names both, because they exhaust independently. Hysteresis: page on crossing; reminder every 6 hours while still over; recovery page when data is back under 75% (metadata under 65%). If `lvs` fails, page once per hour with "could not read pool." Does not autoextend the pool.

## Arrstack compose change

Add a Compose anchor (or `x-logging`) and attach it to every service:

```yaml
x-logging: &default-logging
  driver: json-file
  options:
    max-size: "10m"
    max-file: "3"
```

This is disk control for F-02, not a Vector requirement. Vector reads the files before rotation. Do not switch to the `journald` or `loki` log driver.

## Deploy markers

Both `restart_no_sudo.sh` and `restart.sh` append one JSON line to `/arrstack/obsv/deploy-markers.log` at start and one at successful finish, with UTC timestamp and script name. Vector tails that file into VictoriaLogs (`stream: deploy`). If the path does not exist yet, the append is `|| true` so a deploy never aborts because observability is wedged. Do not curl VictoriaLogs from the restart scripts — that would couple arrstack deploys to the log store being up.

Equivalent change in both scripts so they do not drift.

## Secrets and env

`obsv/.env` is a separate file, ignored by `obsv/.gitignore`, not appended to arrstack `.env`. `${VAR:?err}` for every value the compose file references. A repo-root `.gitignore` is F-16 and is not created by this spec.

Minimum variables:

- `DISCORD_WEBHOOK_URL`
- `TIMEZONE` (`America/New_York`)
- `WEBUI_PORT_VICTORIALOGS` (`9428`)
- `WEBUI_PORT_VICTORIAMETRICS` (`8428`)
- `WEBUI_PORT_DOZZLE` (`8880`)
- `WEBUI_PORT_GRAFANA` (`3800`)
- `PUID` / `PGID` (`10000`) to match the LXC
- `GRAFANA_ADMIN_PASSWORD`
- `PVE_USER` / `PVE_TOKEN_NAME` / `PVE_TOKEN_VALUE` / `PVE_VERIFY_SSL`
- `LINODE_TOKEN`

`obsv/.env.example` lists the keys with `CHANGE_ME` placeholders. The webhook is a secret: a leak is spam in a channel, not LAN access, but it is still rotated by regenerating in Discord and updating four places (obsv `.env`, pve, TrueNAS, the cron script or a file it reads). The cron should read the webhook from a `0600` file on pve, not from the crontab line. PVE and Linode API tokens are also secrets; never commit them.

## Topology file

`docs/observability/topology.yaml` in this repo. Static, hand-written, about twenty lines. Phase 1 contents:

```yaml
host: ct100-arrstack
dns: adguard   # <adguard-host> — fleet-wide, not collected
edges:
  - from: gluetun
    to: [qbittorrent, sabnzbd]
    kind: network_namespace
    note: >
      network_mode service:gluetun. Recreating gluetun strands dependents.
      docker restart does not repair; docker compose up -d does.
  - from: [sonarr, radarr, lidarr, readarr, whisparr]
    to: [qbittorrent, sabnzbd]
    kind: download_client
  - from: [sonarr, radarr, lidarr, readarr, whisparr]
    to: prowlarr
    kind: indexer
```

This is prompt context for Cursor and later for the LLM script. It is not discovered dynamically.

## Alert copy

Because nothing auto-recovers, every Discord message is a runbook: what happened, what it means, the command. The gluetun family is three different alerts because they need three different human actions:

1. **Unhealthy but still running** — Gluetun is internally restarting the VPN (`HEALTH_RESTART_VPN` stays on). Dependents should still have a namespace. Wait; if inbound peers die, restart qbittorrent only (issue #1407). Evidence is already in VictoriaLogs.
2. **Container recreated / id changed** — dependents stranded. `docker compose up -d qbittorrent sabnzbd` from `/mnt/config`. Not `docker restart`.
3. **Exited / dead** — gluetun itself died. `restart: always` may bring it back without bouncing dependents. Same command as (2).

Do not add `NET_RAW` or delete obsolete gluetun env vars in this spec. Those are F-34 investigation, not F-35. Do capture the logs that make that investigation possible.

## LLM substrate (phase 1) and pipeline shape (phase 3)

Phase 1 does not call an LLM. It collects what an LLM would need:

- Consistent labels on every record, including `network_mode`.
- Docker events and health snapshots (not backfillable).
- Topology file.
- Deploy markers.
- Dozzle MCP for live logs/stats in Cursor.
- VictoriaLogs HTTP LogsQL for historical questions (paste or a later thin MCP wrapper). Portainer MCP is optional, LAN-only, not provisioned here.

Phase 3, not implemented now, is a ~100-line script — not n8n — that:

1. Receives a copy of the alert (second webhook URL on the relay, or a tap).
2. Dedupes on `{alertname, container}` for 48 hours.
3. Builds a read-only context bundle: last N minutes of logs for the container and topology neighbors, docker events in the window, latest health snapshot, image, last deploy marker.
4. Sends the bundle to an API LLM with a fixed system prompt: hypothesize root cause, not "restart it"; prefer events and topology over log keywords; max two hypotheses; include exact human commands; never mutate infra.
5. Posts the verdict as a follow-up in the same Discord channel.

Guardrails (implement in phase 3, freeze now): read-only everything, one retry per tool call, strip noisy labels, timeout, authenticate the webhook with a header not a query string, rate-limit so an alert storm cannot fork dozens of LLM calls.

HolmesGPT stays a laptop CLI. Not a container.

## Failure modes

| Failure | Behaviour |
| --- | --- |
| VictoriaLogs down / OOM | Vector buffers then drops. monocker and Layer 1 still page. CollectorSilent fires once logs resume or via monocker on the `victoria-logs` container. |
| Vector crash | Gap in history. Containers keep logging to json-file. monocker still pages. |
| Discord 429/5xx | Relay retries then drops. |
| Discord unreachable (DNS/WAN) | Nobody is paged. Accepted. |
| `restart_no_sudo.sh` | Leaves running `obsv` containers alone. Bind mounts survive `volume prune`. Keep `obsv` up so `image prune -a` does not delete its images. |
| Duplicate monocker + vmalert pages | Accepted in phase 1. |
| Thin-pool `lvs` failure | Hourly "could not read pool," not silence. |
| Webhook leak | Rotate in Discord + four consumers. |

Socket mounts are `:ro`. No container in `obsv` is given write access to Docker.

## Phasing

**1a — pages without a log store.** Discord webhook. json-file rotation on arrstack. monocker. Proxmox webhook + matcher. TrueNAS Slack-compat service. Thin-pool cron. Fire-drill: stop or unhealth a disposable container; send-test from PVE and TrueNAS; curl the webhook from the cron's 0600 file.

**1b — collection.** `obsv/` with Vector, VictoriaLogs, Dozzle. Topology file. Deploy markers in both restart scripts. `mkdir -p /arrstack/obsv/{victoria-logs,vector-buffer}` on CT 100 (owner `PUID:PGID`), not via the arrstack brace expansion. Vector also bind-mounts `/arrstack/obsv` so it can tail `deploy-markers.log` in that directory. Fire-drill: LogsQL for a known Sonarr line, a `health_status` event, and a health snapshot after a failing HEALTHCHECK. Confirm `docker stats` respects mem limits. Confirm socket mounts are `:ro`.

**1c — log-based alerts.** Relay + vmalert + the rule contract. Fire-drill: forced-unhealthy produces two Discord messages with runbook text. Confirm vmalert-down still leaves monocker working.

**2 — metrics + Grafana.** VictoriaMetrics (scrape 30s, retain 30d), node_exporter on the pve host (not inside CT 100; Docker-in-LXC misreports memory), pve-exporter, `lvm-textfile.sh` writing `node_lvm_thin_pools_*` next to the Discord thin-pool cron, Grafana 13.1.3 on host port 3800 (anonymous Viewer, LAN only), Linode account-transfer exporter. Same webhook, same label names. Second vmalert (`vmalert-metrics`) for PromQL rules. No cAdvisor, no Grafana VictoriaLogs plugin.

**3 — not this spec.** Context-bundle webhook script. Optional VictoriaLogs MCP wrapper.

## Verification

There is no test suite. After every edit:

- `docker compose -f obsv/docker-compose.yaml --env-file obsv/.env config >/dev/null`
- `docker compose -f docker-compose.yaml config >/dev/null` if arrstack logging was touched

After deploy, the fire-drills in phasing. After 24 hours, `docker stats` to replace RAM guesses; tighten limits if needed. `docker inspect --format '{{.HostConfig.LogConfig}}'` on an arrstack container confirms rotation.

## Repo layout

```
obsv/
  docker-compose.yaml
  scrape.yml
  .env                 # gitignored
  .env.example
  vector.yaml
  vmalert-rules.yaml
  vmalert-metrics-rules.yaml
  discord-relay/       # owned script + Dockerfile
  linode-exporter/     # owned script + Dockerfile
  grafana/             # provisioning + Homelab dashboard
  host/thinpool-alert.sh
  host/lvm-textfile.sh
  .gitignore           # ignores .env
docs/observability/topology.yaml
docs/superpowers/specs/2026-08-16-observability-design.md
```

Sync `obsv/` to `/mnt/config/obsv` before it affects production. Same rule as the rest of this repo.

## RAM budget (order of magnitude, verify with docker stats)

| Container | Limit |
| --- | --- |
| VictoriaLogs | 512 MB |
| VictoriaMetrics | 256 MB |
| Grafana | 256 MB |
| Vector | 128 MB |
| vmalert | 64 MB |
| vmalert-metrics | 64 MB |
| pve-exporter | 64 MB |
| Discord relay | 64 MB |
| monocker | 64 MB |
| Dozzle | 64 MB |
| linode-exporter | 32 MB |
| **Ceiling** | **~1.6 GB** |

CT 100 has 8 GB with about 1.2 GB in use at last inventory. This fits. If it does not fit in practice, drop Dozzle first (MCP convenience, not alerting). Grafana next.

## Related findings this does not close

F-34 remains open until the gluetun failure is understood; this spec only makes the next incident visible and inspectable. F-02 remains open until discard/fstrim; this spec only pages before 100%. F-08 remains open; Discord is not a Notifiarr replacement for unpack events.
