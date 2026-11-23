# Infrastructure (Ansible)

Host substrate for Proxmox, TrueNAS, the Pangolin VPS, and the **CT 105 deploy agent**.
Application deploys stay in `./deploy.sh` — do not rewrite Compose/bootstrap as Ansible roles.

```bash
nix develop
cd infra
# Decrypt site values first so inventory IPs can be overridden if desired:
# ../scripts/secrets.sh decrypt && set -a && source ../secrets/site.env && set +a
ansible-playbook site.yml --tags deployer
```

| Role | Purpose |
| --- | --- |
| `pve_mounts` | CIFS/NFS fstab entries (creds in `/etc/pve/priv/metrion-cifs.creds`) |
| `lxc_guests` | Inventory/check for CTs 100–105 |
| `lxc_docker` | Docker-in-LXC on arrstack/newt/jellyfin/deployer |
| `adguard_install` | Ensure AdGuard binary exists (filters via `./deploy.sh adguard`) |
| `restic_repo` | restic package + password file reminder |
| `truenas_shares` | Pointer to midclt snapshot script |
| `pangolin_vps` | Swap, sshd, Docker baseline |
| `deployer` | CT 105 + `homelab-deploy` (02:00 timer, `--now`, GitHub Deployment poller) |

Manual from the LAN after the agent is up:

```bash
ssh root@<deployer> homelab-deploy --now arrstack
ssh root@<deployer> homelab-deploy --dry-run
```

From GitHub: Actions → **deploy-now**.
