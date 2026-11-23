#!/usr/bin/env bash
# Quick check for F-14 — CT 100 public IPv6 + listening services.
# Does not change anything; prints what to verify on the AT&T BGW.
set -euo pipefail

ARRSTACK_IP="${ARRSTACK_IP:-192.168.1.201}"
echo "==> F-14 IPv6 exposure checklist"
echo "CT 100 (arrstack) should only expose Caddy 80/443 on the LAN after identity deploy."
echo ""
echo "From a host on CT 100 (or: ssh root@${PVE_HOST:-192.168.1.200} 'pct exec 100 -- ...'):"
echo "  hostname -I"
echo "  ss -tlnp | grep -E ':443|:80|:8989'"
echo ""
echo "Expected after identity:"
echo "  - UIs on 127.0.0.1 only (8989, 7878, ...)"
echo "  - Caddy on 0.0.0.0:80 and :443"
echo ""
echo "On AT&T BGW: confirm IPv6 firewall blocks inbound to ${ARRSTACK_IP} / delegated prefix."
echo "If unsure, disable IPv6 on CT 100 LXC until BGW rules are confirmed."
