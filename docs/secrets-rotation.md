# Secrets rotation

## Already done

- Sonarr / Radarr / Prowlarr / Lidarr / Bazarr API keys in the live `.env` do **not**
  match the keys that were committed in the private `init` commit's `buildarr.yml`.
  No further *arr rotation is required for that exposure.
- All live secrets are encrypted with SOPS (`*.sops.env`). Age private keys stay in
  `.age/` on the workstation and `/etc/homelab/age.key` on the deploy agent.

## Required before the repo is public: Cloudflare API token

Caddy once logged `CLOUDFLARE_API_TOKEN` during a crash-loop. Create a **new**
zone-scoped token and retire the old one before pushing a public remote.

1. Cloudflare dashboard → My Profile → API Tokens → Create Token  
   Permissions: `Zone:DNS:Edit`, `Zone:Zone:Read`, include only your zone.
2. On the workstation:

   ```bash
   ./scripts/rotate_cloudflare_token.sh
   # paste the new token when prompted (not echoed)
   ./scripts/secrets.sh encrypt
   ./deploy.sh identity
   ```

3. Cloudflare dashboard → delete the old token.
4. Confirm ACME still renews: `docker logs caddy 2>&1 | grep -i acme` on CT 100.

## Age keys

If an age private key is ever copied or committed:

```bash
age-keygen -o .age/workstation.key.new
# update .sops.yaml recipients, then:
sops updatekeys secrets/*.sops.env .env.sops.env obsv/.env.sops.env \
  identity/.env.sops.env jellyfin/.env.sops.env \
  pangolin/.env.sops.env pangolin/site/.env.sops.env
```

Distribute the new deployer key to CT 105 and remove the old recipient.