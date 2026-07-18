#!/usr/bin/env bash
set -euo pipefail

[[ $EUID -eq 0 ]] || { printf 'Run as root.\n' >&2; exit 1; }

if [[ -r /etc/emo-agent/state.env ]]; then
  # Values were validated before installation and contain no shell metacharacters.
  # shellcheck source=/dev/null
  source /etc/emo-agent/state.env
else
  PROFILE=emo-agent-ap
  PREVIOUS_FORWARD=0
fi

systemctl disable --now emo-agent-gateway.service emo-agent-dns.service emo-agent-network.service emo-agent-bluetooth.service 2>/dev/null || true
rm -f /etc/systemd/system/emo-agent-gateway.service.d/hermes.conf
rm -f /etc/systemd/system/emo-agent-gateway.service.d/bluetooth.conf
rm -f /etc/emo-agent/hermes.env
/usr/sbin/nft delete table inet emo_gateway 2>/dev/null || true

if nmcli -t -f NAME connection show | grep -Fxq "$PROFILE"; then
  nmcli connection down "$PROFILE" >/dev/null 2>&1 || true
  nmcli connection delete "$PROFILE"
fi

printf '%s' "${PREVIOUS_FORWARD:-0}" > /proc/sys/net/ipv4/ip_forward 2>/dev/null || true
rm -f /etc/sysctl.d/90-emo-agent.conf
rm -f /etc/systemd/system/emo-agent-gateway.service
rm -f /etc/systemd/system/emo-agent-dns.service
rm -f /etc/systemd/system/emo-agent-network.service
rm -f /etc/systemd/system/emo-agent-bluetooth.service
if [[ ${PREVIOUS_BLUETOOTH_POWERED:-unknown} == no ]]; then
  btmgmt power off >/dev/null 2>&1 || true
fi
rm -rf /etc/emo-agent /opt/emo-agent /var/lib/emo-agent
systemctl daemon-reload
systemctl reset-failed
rm -f /usr/local/sbin/emo-agent-verify
rm -f /usr/local/sbin/emo-agent-repair-dns
rm -f /usr/local/sbin/emo-agent-configure-hermes
rm -f /usr/local/sbin/emo-agent-rollback

printf 'rollback_complete=yes\n'
