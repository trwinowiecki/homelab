# Deploy obsv (CT 100) and layer 1 (pve)

Workspace edits do nothing until they are copied. Use the repo-root script; do **not** run
`restart_no_sudo.sh` for this.

```bash
./deploy.sh obsv          # rsync + compose up -d --build on CT 100
./deploy.sh pve           # node_exporter, LVM textfile, thin-pool cron, webhook file
./deploy.sh obsv pve      # typical phase-2 ship
./deploy.sh --dry-run obsv pve
```

`obsv/.env` must exist (gitignored) with a real `DISCORD_WEBHOOK_URL`. Copy from
`obsv/.env.example` — never put the webhook in the example file, it is tracked.

Phase 2 also needs, in the same file:

| Variable | Notes |
| --- | --- |
| `GRAFANA_ADMIN_PASSWORD` | LAN admin login; anonymous Viewer is enabled without it |
| `PVE_TOKEN_VALUE` | Read-only PVE API token (see `docs/observability/layer1.md`). Leave `CHANGE_ME` and guest graphs stay empty |
| `LINODE_TOKEN` | Linode `account:read_only`. Leave `CHANGE_ME` and transfer graphs stay at 0; no Discord page for that |
| `TRUENAS_API_KEY` | Same read-only TrueNAS key as repo-root `.env`. Empty is fine until the key exists; Homepage and library-exporter tiles stay empty |

Do not put those tokens in chat or in the example file.

## What the script does

1. Validates `docker compose config` locally.
2. rsyncs `obsv/` to `root@<pve-host>:/mnt/lxc_shares/metrion/config/obsv/`
   (CT 100 mounts that share at `/mnt/config`).
3. `pct exec 100` to create `/arrstack/obsv/{victoria-logs,vector-buffer,victoria-metrics,grafana,grafana-provisioning,grafana-dashboards,log-watch}`
   on the LXC SSD and `docker compose up -d --build`. Grafana data + dashboard YAML/JSON are uid `472`.
   Provisioning is copied off the CIFS share (`0770` `root:lxc_shares`) because Grafana cannot read that.
   `log-watch` pages Discord on error spikes and new error signatures (`obsv/log-alerts.yaml`).
   `library-exporter` scrapes Sonarr/Radarr/Jellyfin/TrueNAS for the Library space dashboard.

`./deploy.sh pve` additionally:

- Installs Debian `prometheus-node-exporter` on the hypervisor if missing (not in Docker, not in CT 100).
- Writes `/var/lib/prometheus/node-exporter/lvm.prom` every minute via `lvm-textfile.sh`.
- Keeps the Discord thin-pool cron as a second page path (same duplicate-page idea as monocker + vmalert).

UIs (LAN only, not on Pangolin):

| What | URL |
| --- | --- |
| Grafana | https://grafana.<lan-domain> (Authentik OIDC; host port is `127.0.0.1` only) |
| Dozzle | https://dozzle.<lan-domain> (Authentik forward-auth) |
| VictoriaMetrics | `127.0.0.1:8428` on CT 100 (no LAN publish; unauthenticated write API) |
| VictoriaLogs | `127.0.0.1:9428` on CT 100 (no LAN publish; unauthenticated write API) |

node_exporter listens on `<pve-host>:9100`. It is not published through CT 100.

## Fire-drill (after a real deploy)

Phase 1:

1. `docker stop flaresolverr` inside CT 100 — Discord should get a **monocker** message.
2. `docker start flaresolverr`.
3. Query VictoriaLogs for a Sonarr line and for `stream:event` after the stop/start.

Phase 2:

1. Grafana **Homelab** dashboard shows thin-pool %, pve CPU/RAM, guest `pve_up`, Linode used/quota (quota is 0 until `LINODE_TOKEN` is set). **Errors and warnings** and **Library space** dashboards provision from JSON. Library space needs `TRUENAS_API_KEY` and `arrstack.auto.env`.
2. `curl -s http://<pve-host>:9100/metrics | grep node_lvm_thin_pools` is non-empty after the first pve deploy.
3. Stopping a disposable LXC (not CT 100) should Discord **vmalert-metrics: PveGuestDown** within ~5 minutes.

Arrstack json-file rotation applies only after those containers are recreated:

```bash
./deploy.sh arrstack
```

## Keep obsv up

`restart_no_sudo.sh` still runs `docker image prune -a -f` after a successful arrstack start.
If obsv is running, its images count as used. Bind mounts mean the log and metric stores survive
`docker volume prune`.

## Not in this script

Proxmox notification matcher and TrueNAS Slack-compat alert service are one-time UI steps.
PVE API token creation is also one-time. See `docs/observability/layer1.md`.
