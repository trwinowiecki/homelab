# Layer 1 — zero-agent paging (pve and TrueNAS)

These do not run on CT 100. They share the Discord webhook with `obsv` but fail independently
of it. Apply after the webhook exists.

## Discord webhook file on pve

```bash
install -m 0600 /dev/null /etc/pve/priv/discord-webhook
# paste the webhook URL as the only line
nano /etc/pve/priv/discord-webhook
```

## Thin-pool cron

Copy `obsv/host/thinpool-alert.sh` from this repo to `/usr/local/sbin/thinpool-alert.sh` on pve,
mode `0755`. It reads `lvs` for `pve/data` (Data% and Meta%), not `df`. Thresholds: data 80%,
metadata 70%; recovery pages below 75% / 65%; reminder every 6 hours while still over; `lvs`
failure pages once per hour.

```bash
install -m 0755 thinpool-alert.sh /usr/local/sbin/thinpool-alert.sh
# dry-run (will page if already over threshold, or no-op if under)
/usr/local/sbin/thinpool-alert.sh
printf '%s\n' \
  'PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin' \
  '*/15 * * * * root /usr/local/sbin/thinpool-alert.sh' \
  > /etc/cron.d/thinpool-alert
```

The cron line does not contain the webhook. Cron's default PATH is `/usr/bin:/bin`, which does not include `lvs` (`/usr/sbin/lvs`). Without that PATH line (or the script's `/usr/sbin/lvs` fallback) you get a Discord page every hour: `could not read pool pve/data via lvs`. That is not a full pool. Test that `/usr/sbin/lvs --noheadings -o data_percent,metadata_percent pve/data` works as root.

The Discord cron stays in phase 2. Metrics (`node_lvm_thin_pools_*` via node_exporter) are a second path, not a replacement.

## CT 100 rootfs and guest fstrim

`./deploy.sh pve` also installs:

- `ct100-disk-alert.sh` — pages when CT 100 `df /` ≥ 85% (thin-pool Data% can lag guest df).
- `guest-fstrim.sh` — weekly `pct fstrim 100 104` to return deleted blocks to the thin pool.
- `qbit-ssd-cleanup.py` on CT 100 (cron every 20 min) — removes SSD torrent copies after share
  limits only when Sonarr/Radarr confirm a NAS import. Downloads stay unlimited.

Proxmox LXC `rootfs:` does not accept `discard=on`; fstrim is the supported reclaim path.

## node_exporter and LVM textfile (phase 2)

Install on the **hypervisor**, not in CT 100. Docker-in-LXC misreports host memory.

```bash
apt-get install -y prometheus-node-exporter
install -m 0755 lvm-textfile.sh /usr/local/sbin/lvm-textfile.sh
echo '* * * * * root /usr/local/sbin/lvm-textfile.sh' > /etc/cron.d/lvm-textfile
/usr/local/sbin/lvm-textfile.sh
curl -s http://127.0.0.1:9100/metrics | grep node_lvm_thin_pools
```

`./deploy.sh pve` does this. Default listen is LAN-reachable at `<pve-host>:9100`.

## PVE API token for pve-exporter

Create a token that can only read. Do not use `privsep 0` as root.

```bash
pveum user token add root@pam obsv --privsep 1
pveum aclmod / -token 'root@pam!obsv' -role PVEAuditor
```

Put the token **value** in gitignored `obsv/.env` as `PVE_TOKEN_VALUE`. Matching keys already in the example: `PVE_USER=root@pam`, `PVE_TOKEN_NAME=obsv`, `PVE_VERIFY_SSL=false`. Redeploy `./deploy.sh obsv` after filling it. The exporter runs in CT 100 and scrapes `https://<pve-host>:8006`.

## Proxmox notification target

PVE 9: Datacenter → Notifications → Add → Webhook.

- URL: the Discord webhook (or reference a secret in `/etc/pve/priv/notifications.cfg`)
- Method: POST
- Body (Handlebars, **not** Go `{{ .field }}`):

```json
{"content": "**pve ({{ severity }}):** {{ title }}\n{{ escape message }}"}
```

`{{ escape … }}` is required so quotes/newlines in the message stay valid JSON. Header:
`Content-Type: application/json`.

Then add a **matcher** on severity `warning` and `error` pointing at this target. A target with
no matcher receives nothing. Send Test:

```bash
pvesh create /cluster/notifications/targets/discord/test
```

If the body was created with `{{ .severity }}` / `{{ .title }}`, PVE 9 fails with
`invalid handlebars syntax`. `pvesh set --body` takes **base64**, not raw JSON
(raw JSON yields `could not decode base64 value`). Fix in place:

```bash
BODY_B64="$(printf '%s' '{"content":"**pve ({{ severity }}):** {{ title }}\n{{ escape message }}"}' | base64 -w0)"
pvesh set /cluster/notifications/endpoints/webhook/discord --body "$BODY_B64"
pvesh create /cluster/notifications/targets/discord/test
```

CLI to create (adjust if `pvesh` schema differs):

```bash
pvesh create /cluster/notifications/endpoints/webhook \
  --name discord \
  --url "$(cat /etc/pve/priv/discord-webhook)" \
  --method POST \
  --header 'Content-Type: application/json' \
  --body "$(printf '%s' '{"content":"**pve ({{ severity }}):** {{ title }}\n{{ escape message }}"}' | base64 -w0)"

pvesh create /cluster/notifications/matchers \
  --name discord-warning-error \
  --target discord \
  --match-severity warning,error
```

## TrueNAS Alert Service

TrueNAS SCALE: Alerts (bell) → Alert Services → Add.

- Type: **Slack**
- Webhook URL: Discord webhook with `/slack` appended
  (`https://discord.com/api/webhooks/.../.../slack`)
- Enable categories at least: SMART, scrub, pool, capacity

Send Test. Set the destination email/user on the `admin` account if TrueNAS also mails; that is
independent of Discord.
