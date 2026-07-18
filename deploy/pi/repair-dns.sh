#!/usr/bin/env bash
set -euo pipefail

[[ $EUID -eq 0 ]] || { printf 'Run as root.\n' >&2; exit 1; }
# shellcheck source=/dev/null
source /etc/emo-agent/state.env

UPSTREAM_DNS=""
while read -r candidate; do
  [[ $candidate =~ ^[0-9]+(\.[0-9]+){3}$ && $candidate != "$AP_IP" ]] || continue
  if dig +time=2 +tries=1 +short @"$candidate" example.com A | grep -Eq '^[0-9]+(\.[0-9]+){3}$'; then
    UPSTREAM_DNS=$candidate
    break
  fi
done < <(awk '/^nameserver[[:space:]]+/ {print $2}' /etc/resolv.conf; printf '%s\n' 1.1.1.1 8.8.8.8)
[[ -n $UPSTREAM_DNS ]] || { printf 'No working upstream DNS resolver found.\n' >&2; exit 1; }

cat > /etc/emo-agent/dnsmasq.conf <<EOF
interface=$WIFI
bind-interfaces
listen-address=$AP_IP
domain-needed
bogus-priv
dhcp-authoritative
dhcp-range=10.42.0.20,10.42.0.120,255.255.255.0,12h
dhcp-option=option:router,$AP_IP
dhcp-option=option:dns-server,$AP_IP
address=/api.living.ai/$AP_IP
no-resolv
strict-order
server=$UPSTREAM_DNS
EOF

if grep -q '^UPSTREAM_DNS=' /etc/emo-agent/state.env; then
  sed -i "s/^UPSTREAM_DNS=.*/UPSTREAM_DNS=$UPSTREAM_DNS/" /etc/emo-agent/state.env
else
  printf 'UPSTREAM_DNS=%s\n' "$UPSTREAM_DNS" >> /etc/emo-agent/state.env
fi

systemctl restart emo-agent-dns.service
/usr/local/sbin/emo-agent-verify
printf 'dns_repair_complete=yes\n'
