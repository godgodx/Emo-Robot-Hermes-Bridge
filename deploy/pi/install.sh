#!/usr/bin/env bash
set -euo pipefail

UPLINK=""
WIFI=""
SSID="EMO-LAB"
PAYLOAD=""
SERVICE_USER=""
PROFILE="emo-agent-ap"
AP_ADDRESS="10.42.0.1/24"
AP_IP="10.42.0.1"
AP_SUBNET="10.42.0.0/24"

usage() {
  printf 'Usage: %s --uplink IFACE --wifi IFACE --ssid NAME --user USER --payload DIR\n' "$0" >&2
  exit 2
}

while (($#)); do
  case "$1" in
    --uplink) UPLINK=${2:-}; shift 2 ;;
    --wifi) WIFI=${2:-}; shift 2 ;;
    --ssid) SSID=${2:-}; shift 2 ;;
    --user) SERVICE_USER=${2:-}; shift 2 ;;
    --payload) PAYLOAD=${2:-}; shift 2 ;;
    *) usage ;;
  esac
done

[[ $EUID -eq 0 ]] || { printf 'Run as root.\n' >&2; exit 1; }
[[ $UPLINK =~ ^[a-zA-Z0-9_.:-]+$ ]] || usage
[[ $WIFI =~ ^[a-zA-Z0-9_.:-]+$ ]] || usage
[[ $SSID =~ ^[a-zA-Z0-9._-]{1,32}$ ]] || usage
[[ $SERVICE_USER =~ ^[a-z_][a-z0-9_-]*$ ]] || usage
[[ -d $PAYLOAD/emo_agent ]] || { printf 'Missing payload: %s/emo_agent\n' "$PAYLOAD" >&2; exit 1; }
[[ -d /sys/class/net/$UPLINK ]] || { printf 'Missing uplink interface %s\n' "$UPLINK" >&2; exit 1; }
[[ -d /sys/class/net/$WIFI/wireless ]] || { printf '%s is not a Wi-Fi interface\n' "$WIFI" >&2; exit 1; }
ip route show default | grep -Eq "dev[[:space:]]+$UPLINK([[:space:]]|$)" || {
  printf 'Default route is not using %s; refusing to risk SSH.\n' "$UPLINK" >&2
  exit 1
}

id "$SERVICE_USER" >/dev/null 2>&1 || { printf 'Missing service user %s\n' "$SERVICE_USER" >&2; exit 1; }

if nmcli -t -f NAME connection show | grep -Fxq "$PROFILE" && [[ ! -f /etc/emo-agent/state.env ]]; then
  printf 'Connection %s already exists but is not owned by this deployment.\n' "$PROFILE" >&2
  exit 1
fi

printf '[1/8] Installing required Debian packages...\n'
export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y --no-install-recommends dnsmasq-base nftables dnsutils python3-aiohttp python3-bleak python3-cryptography bluez rfkill ca-certificates curl iw openssl

WIFI_PHY=$(basename "$(readlink -f "/sys/class/net/$WIFI/phy80211")")
iw phy "$WIFI_PHY" info | grep -Eq '^[[:space:]]+\* AP$' || {
  printf '%s does not advertise Wi-Fi access-point support.\n' "$WIFI" >&2
  exit 1
}

UPSTREAM_DNS=""
while read -r candidate; do
  [[ -n $candidate && $candidate != "$AP_IP" ]] || continue
  if dig +time=2 +tries=1 +short @"$candidate" example.com A | grep -Eq '^[0-9]+(\.[0-9]+){3}$'; then
    UPSTREAM_DNS=$candidate
    break
  fi
done < <(awk '/^nameserver[[:space:]]+/ {print $2}' /etc/resolv.conf; printf '%s\n' 1.1.1.1 8.8.8.8)
[[ -n $UPSTREAM_DNS ]] || { printf 'No working upstream DNS resolver found.\n' >&2; exit 1; }

printf '[2/8] Creating protected state and application directories...\n'
install -d -o root -g "$SERVICE_USER" -m 750 /etc/emo-agent
install -d -o "$SERVICE_USER" -g "$SERVICE_USER" -m 750 /etc/emo-agent/certs
install -d -m 755 /opt/emo-agent
rm -rf /opt/emo-agent/emo_agent
cp -a "$PAYLOAD/emo_agent" /opt/emo-agent/emo_agent
find /opt/emo-agent/emo_agent -type d -name __pycache__ -prune -exec rm -rf {} +
chown -R root:root /opt/emo-agent
chmod -R a=rX,u+w /opt/emo-agent
install -m 755 "$PAYLOAD/deploy/pi/rollback.sh" /usr/local/sbin/emo-agent-rollback
install -m 755 "$PAYLOAD/deploy/pi/verify.sh" /usr/local/sbin/emo-agent-verify
install -m 755 "$PAYLOAD/deploy/pi/repair-dns.sh" /usr/local/sbin/emo-agent-repair-dns
install -m 755 "$PAYLOAD/deploy/pi/configure-hermes.sh" /usr/local/sbin/emo-agent-configure-hermes

PREVIOUS_FORWARD=$(sed -n 's/^PREVIOUS_FORWARD=//p' /etc/emo-agent/state.env 2>/dev/null | tail -n 1 || true)
PREVIOUS_FORWARD=${PREVIOUS_FORWARD:-$(cat /proc/sys/net/ipv4/ip_forward 2>/dev/null || printf 0)}
PREVIOUS_BLUETOOTH_POWERED=$(sed -n 's/^PREVIOUS_BLUETOOTH_POWERED=//p' /etc/emo-agent/state.env 2>/dev/null | tail -n 1 || true)
PREVIOUS_BLUETOOTH_POWERED=${PREVIOUS_BLUETOOTH_POWERED:-$(btmgmt info 2>/dev/null | awk '/current settings:/ {print ($0 ~ / powered / ? "yes" : "no"); exit}')}
PREVIOUS_BLUETOOTH_POWERED=${PREVIOUS_BLUETOOTH_POWERED:-unknown}
cat > /etc/emo-agent/state.env <<EOF
UPLINK=$UPLINK
WIFI=$WIFI
SSID=$SSID
PROFILE=$PROFILE
AP_IP=$AP_IP
AP_SUBNET=$AP_SUBNET
PREVIOUS_FORWARD=$PREVIOUS_FORWARD
SERVICE_USER=$SERVICE_USER
UPSTREAM_DNS=$UPSTREAM_DNS
PREVIOUS_BLUETOOTH_POWERED=$PREVIOUS_BLUETOOTH_POWERED
EOF
chmod 600 /etc/emo-agent/state.env

if [[ ! -s /etc/emo-agent/ap.psk ]]; then
  python3 - <<'PY' > /etc/emo-agent/ap.psk
import secrets
print(secrets.token_urlsafe(18))
PY
  chmod 600 /etc/emo-agent/ap.psk
fi
AP_PSK=$(< /etc/emo-agent/ap.psk)

printf '[3/8] Creating the isolated NetworkManager access point...\n'
if nmcli -t -f NAME connection show | grep -Fxq "$PROFILE"; then
  nmcli connection down "$PROFILE" >/dev/null 2>&1 || true
  nmcli connection delete "$PROFILE"
fi
nmcli connection add type wifi ifname "$WIFI" con-name "$PROFILE" ssid "$SSID"
nmcli connection modify "$PROFILE" \
  connection.autoconnect yes \
  802-11-wireless.mode ap \
  802-11-wireless.band bg \
  wifi-sec.key-mgmt wpa-psk \
  wifi-sec.psk "$AP_PSK" \
  ipv4.method manual \
  ipv4.addresses "$AP_ADDRESS" \
  ipv4.never-default yes \
  ipv6.method disabled
nmcli connection up "$PROFILE"
ip -4 address show dev "$WIFI" | grep -Fq "$AP_ADDRESS" || {
  printf 'Access-point address did not appear on %s.\n' "$WIFI" >&2
  exit 1
}

printf '[4/8] Configuring targeted DHCP and DNS...\n'
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

cat > /etc/systemd/system/emo-agent-dns.service <<'EOF'
[Unit]
Description=EMO lab DHCP and targeted DNS
After=NetworkManager.service network-online.target
Wants=network-online.target

[Service]
Type=simple
ExecStart=/usr/sbin/dnsmasq --keep-in-foreground --conf-file=/etc/emo-agent/dnsmasq.conf
Restart=on-failure
RestartSec=2

[Install]
WantedBy=multi-user.target
EOF

printf '[5/8] Configuring narrow forwarding and NAT...\n'
cat > /etc/emo-agent/nftables.conf <<EOF
table inet emo_gateway {
  chain forward {
    type filter hook forward priority filter; policy accept;
    iifname "$WIFI" oifname "$UPLINK" accept
    iifname "$UPLINK" oifname "$WIFI" ct state established,related accept
  }
  chain postrouting {
    type nat hook postrouting priority srcnat; policy accept;
    ip saddr $AP_SUBNET oifname "$UPLINK" masquerade
  }
}
EOF
cat > /etc/sysctl.d/90-emo-agent.conf <<'EOF'
net.ipv4.ip_forward=1
EOF
printf 1 > /proc/sys/net/ipv4/ip_forward

cat > /etc/systemd/system/emo-agent-network.service <<'EOF'
[Unit]
Description=EMO lab forwarding and NAT
After=NetworkManager.service network-online.target
Wants=network-online.target
Before=emo-agent-dns.service emo-agent-gateway.service

[Service]
Type=oneshot
ExecStartPre=-/usr/sbin/nft delete table inet emo_gateway
ExecStart=/usr/sbin/nft -f /etc/emo-agent/nftables.conf
ExecStop=-/usr/sbin/nft delete table inet emo_gateway
RemainAfterExit=yes

[Install]
WantedBy=multi-user.target
EOF

printf '[6/8] Enabling the local Bluetooth controller for deferred replies...\n'
[[ -x /usr/sbin/rfkill && -x /usr/bin/hciconfig ]] || { printf 'Bluetooth control tools not found.\n' >&2; exit 1; }
install -m 644 "$PAYLOAD/deploy/pi/emo-agent-bluetooth.service" /etc/systemd/system/emo-agent-bluetooth.service
install -d -m 755 /etc/systemd/system/emo-agent-gateway.service.d
install -m 644 "$PAYLOAD/deploy/pi/emo-agent-gateway-bluetooth.conf" /etc/systemd/system/emo-agent-gateway.service.d/bluetooth.conf

printf '[7/8] Creating the HTTPS interception service...\n'
if [[ ! -s /etc/emo-agent/certs/api.living.ai.crt || ! -s /etc/emo-agent/certs/api.living.ai.key ]]; then
  runuser -u "$SERVICE_USER" -- env PYTHONPATH=/opt/emo-agent python3 -m emo_agent.gateway init-cert \
    --cert /etc/emo-agent/certs/api.living.ai.crt \
    --key /etc/emo-agent/certs/api.living.ai.key
fi
chmod 640 /etc/emo-agent/certs/api.living.ai.key
chmod 644 /etc/emo-agent/certs/api.living.ai.crt
chown "$SERVICE_USER:$SERVICE_USER" /etc/emo-agent/certs/api.living.ai.key /etc/emo-agent/certs/api.living.ai.crt

cat > /etc/systemd/system/emo-agent-gateway.service <<EOF
[Unit]
Description=EMO HTTPS observation gateway
After=network-online.target emo-agent-network.service emo-agent-bluetooth.service
Wants=network-online.target emo-agent-bluetooth.service
Requires=emo-agent-network.service

[Service]
Type=simple
User=$SERVICE_USER
Group=$SERVICE_USER
WorkingDirectory=/opt/emo-agent
Environment=PYTHONPATH=/opt/emo-agent
Environment=PYTHONUNBUFFERED=1
Environment=PYTHONDONTWRITEBYTECODE=1
StateDirectory=emo-agent
StateDirectoryMode=0750
ExecStart=/usr/bin/python3 -m emo_agent.gateway serve --host 0.0.0.0 --port 443 --audio-port 80 --cert /etc/emo-agent/certs/api.living.ai.crt --key /etc/emo-agent/certs/api.living.ai.key
Restart=on-failure
RestartSec=2
AmbientCapabilities=CAP_NET_BIND_SERVICE
CapabilityBoundingSet=CAP_NET_BIND_SERVICE
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
ProtectKernelTunables=true
ProtectKernelModules=true
ProtectControlGroups=true
RestrictAddressFamilies=AF_INET AF_INET6 AF_UNIX AF_BLUETOOTH

[Install]
WantedBy=multi-user.target
EOF

printf '[8/8] Enabling and verifying services...\n'
systemctl daemon-reload
systemctl enable --now emo-agent-network.service
systemctl enable --now emo-agent-dns.service
systemctl enable --now emo-agent-bluetooth.service
systemctl enable --now emo-agent-gateway.service
/usr/local/sbin/emo-agent-verify

printf 'gateway_base_installed=yes\n'
