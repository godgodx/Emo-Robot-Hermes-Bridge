#!/usr/bin/env bash
set -u

section() {
  printf '\n[%s]\n' "$1"
}

have() {
  command -v "$1" >/dev/null 2>&1
}

section "identity"
printf 'architecture=%s\n' "$(uname -m 2>/dev/null || printf unknown)"
printf 'kernel=%s\n' "$(uname -r 2>/dev/null || printf unknown)"
if [[ -r /proc/device-tree/model ]]; then
  tr -d '\0' < /proc/device-tree/model
  printf '\n'
fi
if [[ -r /etc/os-release ]]; then
  grep -E '^(ID|VERSION_ID|VERSION_CODENAME|PRETTY_NAME)=' /etc/os-release || true
fi

section "interfaces"
if have ip; then
  for interface_path in /sys/class/net/*; do
    interface=$(basename "$interface_path")
    state=$(cat "$interface_path/operstate" 2>/dev/null || printf unknown)
    [[ -d $interface_path/wireless ]] && wireless=yes || wireless=no
    printf '%s state=%s wireless=%s\n' "$interface" "$state" "$wireless"
  done
  printf '\ndefault_route_interfaces:\n'
  ip route show default | awk '{for (i=1; i<=NF; i++) if ($i == "dev") print $(i+1)}' | sort -u
else
  printf 'ip_command=missing\n'
fi

section "wifi"
if have rfkill; then
  rfkill --output TYPE,SOFT,HARD 2>/dev/null | awk 'NR == 1 || $1 == "wlan"' || true
else
  printf 'rfkill=missing\n'
fi
if have iw; then
  iw dev | awk '/^phy#/ || /^[[:space:]]+Interface/ || /^[[:space:]]+type/ || /^[[:space:]]+channel/' || true
else
  printf 'iw=missing\n'
fi

section "network_manager"
if have systemctl; then
  for service in NetworkManager systemd-networkd dhcpcd hostapd dnsmasq; do
    printf '%s=' "$service"
    systemctl is-active "$service" 2>/dev/null || true
  done
else
  printf 'systemctl=missing\n'
fi

section "critical_ports"
if have ss; then
  ss -lntup 2>/dev/null | awk 'NR == 1 || /:22 |:53 |:67 |:80 |:443 /' || true
else
  printf 'ss=missing\n'
fi

section "routing"
if have sysctl; then
  sysctl net.ipv4.ip_forward 2>/dev/null || true
fi
for tool in nft iptables nmcli hostapd dnsmasq python3; do
  if have "$tool"; then
    printf '%s=present\n' "$tool"
  else
    printf '%s=missing\n' "$tool"
  fi
done

section "python"
if have python3; then
  python3 --version 2>&1 || true
  python3 - <<'PY'
import importlib.util

for module in ("aiohttp", "cryptography"):
    print(f"python_module_{module}={'present' if importlib.util.find_spec(module) else 'missing'}")
PY
fi

section "internet_probe"
if have getent; then
  if getent ahostsv4 api.living.ai >/dev/null 2>&1; then
    printf 'api_living_ai_dns=ok\n'
  else
    printf 'api_living_ai_dns=failed\n'
  fi
else
  printf 'getent=missing\n'
fi

printf '\npreflight_complete=yes\n'
