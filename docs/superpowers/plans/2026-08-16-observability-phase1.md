# Observability Phase 1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship F-35 phase 1 — Discord paging for container/host failures, centralized logs on CT 100, and an LLM-ready substrate — without auto-recovery.

**Architecture:** Separate Compose project `obsv` on CT 100 (Vector → VictoriaLogs → vmalert → owned Discord relay, plus monocker and Dozzle). Layer 1 is zero-agent: PVE webhook, TrueNAS Slack-compat, thin-pool cron on pve. Arrstack compose only gains json-file rotation.

**Tech Stack:** Docker Compose, Vector, VictoriaLogs, vmalert, Python 3 stdlib relay, monocker, Dozzle, Discord webhooks.

## Global Constraints

- Notify-only: Docker socket mounts are `:ro`. No autoheal.
- `obsv` is a separate Compose project; never add these services to arrstack `docker-compose.yaml`.
- VictoriaLogs data is a bind mount at `/arrstack/obsv`, not an anonymous volume (`restart_no_sudo.sh` prunes volumes).
- Every compose `${VAR:?err}`. Secrets in `obsv/.env`, not arrstack `.env`.
- Discord is the only sink. Alert copy is a runbook (what / meaning / command).
- Do not run `restart_no_sudo.sh` as part of this work. Sync to `/mnt/config` is a separate deploy step.
- Do not commit `obsv/.env` or webhook URLs.

---

### Task 1: Arrstack log rotation

**Files:**
- Modify: `docker-compose.yaml` — add `x-logging` anchor; `logging: *default-logging` on every service.

- [ ] Add the anchor and attach it to all 19 services.
- [ ] `docker compose config >/dev/null` from the repo with existing `.env`.

### Task 2: Deploy markers

**Files:**
- Modify: `restart_no_sudo.sh`, `restart.sh` — append JSON lines to `/arrstack/obsv/deploy-markers.log` at start and successful finish, `|| true`.

### Task 3: Layer 1 host artifacts

**Files:**
- Create: `obsv/host/thinpool-alert.sh`
- Create: `docs/observability/layer1.md` (PVE webhook, TrueNAS Slack-compat, cron install)
- Create: `docs/observability/topology.yaml`

Cannot apply PVE/TrueNAS UI changes without the Discord webhook. Scripts and exact commands live in the repo.

### Task 4: obsv collection stack

**Files:**
- Create: `obsv/docker-compose.yaml`, `obsv/.env.example`, `obsv/.gitignore`, `obsv/vector.yaml`, `obsv/vector/health_poll.py`, `obsv/vector/Dockerfile`
- Create: `docs/observability/deploy.md`

Services: victoria-logs, vector, dozzle, monocker. Bind mounts under `/arrstack/obsv`.

### Task 5: Discord relay + vmalert

**Files:**
- Create: `obsv/discord-relay/relay.py`, `obsv/discord-relay/Dockerfile`
- Create: `obsv/vmalert-rules.yaml`
- Modify: `obsv/docker-compose.yaml` — add `discord-relay` and `vmalert`

Rules: ContainerUnhealthy, ContainerExited, GluetunVpnRestart, GluetunDependentsStranded, RestartStorm, CollectorSilent (heartbeat).

### Task 6: Verify

- [ ] `docker compose -f obsv/docker-compose.yaml --env-file obsv/.env.example` will fail on empty `:?err` — copy example to a gitignored `.env` with placeholder URL and run `config >/dev/null`.
- [ ] `python3 -m py_compile obsv/discord-relay/relay.py obsv/vector/health_poll.py`
- [ ] `bash -n` on both restart scripts and `thinpool-alert.sh`.
- [ ] Arrstack `docker compose config >/dev/null`.
