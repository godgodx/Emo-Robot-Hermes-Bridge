#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
UPLINK=""
EMO_WIFI=""
SSID="EMO-HERMES"
SERVICE_USER=${SUDO_USER:-}
HERMES_BIN=""
API_PORT=8642
SESSION_ID="emo-robot-main"
ASSUME_YES=0
PREFLIGHT_ONLY=0

usage() {
  cat <<'EOF'
Usage: sudo ./install.sh [options]

Options:
  --uplink IFACE       Internet/SSH interface (auto-detected when unambiguous)
  --wifi IFACE         Secondary Wi-Fi adapter dedicated to EMO (auto-detected when unambiguous)
  --ssid NAME          EMO access-point name (default: EMO-HERMES)
  --user USER          Linux user that owns the existing Hermes installation
  --hermes-bin PATH    Existing Hermes executable (auto-detected from common locations)
  --api-port PORT      Loopback Hermes API port (default: 8642)
  --session-id ID      Initial Hermes session identifier (default: emo-robot-main)
  --preflight-only     Detect and validate without changing the system
  --yes                Skip the final confirmation prompt
  -h, --help           Show this help

The Pi must already have Internet access and Hermes Agent installed. A second,
access-point-capable Wi-Fi adapter is required for EMO.
EOF
}

while (($#)); do
  case "$1" in
    --uplink) UPLINK=${2:-}; shift 2 ;;
    --wifi) EMO_WIFI=${2:-}; shift 2 ;;
    --ssid) SSID=${2:-}; shift 2 ;;
    --user) SERVICE_USER=${2:-}; shift 2 ;;
    --hermes-bin) HERMES_BIN=${2:-}; shift 2 ;;
    --api-port) API_PORT=${2:-}; shift 2 ;;
    --session-id) SESSION_ID=${2:-}; shift 2 ;;
    --preflight-only) PREFLIGHT_ONLY=1; shift ;;
    --yes) ASSUME_YES=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) usage >&2; exit 2 ;;
  esac
done

[[ $EUID -eq 0 ]] || { printf 'Run this installer with sudo.\n' >&2; exit 1; }
[[ -x $ROOT_DIR/deploy/pi/preflight.sh && -x $ROOT_DIR/deploy/pi/install.sh ]] || {
  printf 'Run this script from a complete repository checkout.\n' >&2
  exit 1
}

for command in getent ip nmcli runuser systemctl; do
  command -v "$command" >/dev/null 2>&1 || {
    printf 'Required command is missing: %s\n' "$command" >&2
    exit 1
  }
done

[[ $SERVICE_USER =~ ^[a-z_][a-z0-9_-]*$ ]] || {
  printf 'Cannot determine the Hermes user. Re-run with --user USER.\n' >&2
  exit 2
}
SERVICE_HOME=$(getent passwd "$SERVICE_USER" | cut -d: -f6)
[[ -n $SERVICE_HOME && -d $SERVICE_HOME ]] || {
  printf 'Linux user or home directory not found: %s\n' "$SERVICE_USER" >&2
  exit 1
}

if [[ -z $UPLINK ]]; then
  UPLINK=$(ip route show default | awk 'NR == 1 {for (i=1; i<=NF; i++) if ($i == "dev") {print $(i+1); exit}}')
fi
[[ $UPLINK =~ ^[a-zA-Z0-9_.:-]+$ && -d /sys/class/net/$UPLINK ]] || {
  printf 'Could not safely determine the Internet interface. Use --uplink IFACE.\n' >&2
  exit 2
}
ip route show default | grep -Eq "dev[[:space:]]+$UPLINK([[:space:]]|$)" || {
  printf '%s does not carry the default route; refusing to risk SSH.\n' "$UPLINK" >&2
  exit 1
}

if [[ -z $EMO_WIFI ]]; then
  mapfile -t WIFI_CANDIDATES < <(
    for candidate in /sys/class/net/*/wireless; do
      [[ -e $candidate ]] || continue
      interface=$(basename "$(dirname "$candidate")")
      [[ $interface == "$UPLINK" ]] || printf '%s\n' "$interface"
    done
  )
  if ((${#WIFI_CANDIDATES[@]} == 1)); then
    EMO_WIFI=${WIFI_CANDIDATES[0]}
  else
    printf 'Expected exactly one secondary Wi-Fi adapter; found %d. Use --wifi IFACE.\n' \
      "${#WIFI_CANDIDATES[@]}" >&2
    exit 2
  fi
fi
[[ $EMO_WIFI =~ ^[a-zA-Z0-9_.:-]+$ && -d /sys/class/net/$EMO_WIFI/wireless ]] || {
  printf '%s is not a Wi-Fi interface.\n' "$EMO_WIFI" >&2
  exit 1
}
[[ $EMO_WIFI != "$UPLINK" ]] || {
  printf 'The Internet and EMO interfaces must be different.\n' >&2
  exit 1
}
[[ $SSID =~ ^[a-zA-Z0-9._-]{1,32}$ ]] || { printf 'Invalid SSID.\n' >&2; exit 2; }
if [[ ! $API_PORT =~ ^[0-9]{2,5}$ ]] || ((API_PORT < 1024 || API_PORT > 65535)); then
  printf 'Invalid Hermes API port.\n' >&2
  exit 2
fi
[[ $SESSION_ID =~ ^[a-zA-Z0-9._:-]{1,128}$ ]] || {
  printf 'Invalid Hermes session identifier.\n' >&2
  exit 2
}

if [[ -z $HERMES_BIN ]]; then
  for candidate in \
    "$SERVICE_HOME/.local/bin/hermes" \
    "$SERVICE_HOME/.hermes/hermes-agent/venv/bin/hermes"; do
    if [[ -x $candidate ]]; then
      HERMES_BIN=$candidate
      break
    fi
  done
fi
if [[ -z $HERMES_BIN ]]; then
  HERMES_BIN=$(runuser -u "$SERVICE_USER" -- env HOME="$SERVICE_HOME" sh -lc 'command -v hermes' 2>/dev/null || true)
fi
[[ $HERMES_BIN == /* && -x $HERMES_BIN ]] || {
  printf 'Hermes executable not found for %s. Install Hermes first or use --hermes-bin PATH.\n' \
    "$SERVICE_USER" >&2
  exit 1
}

printf 'EMO Hermes Bridge preflight\n'
printf '  Hermes user: %s\n' "$SERVICE_USER"
printf '  Internet/SSH: %s\n' "$UPLINK"
printf '  EMO Wi-Fi:    %s\n' "$EMO_WIFI"
printf '  EMO SSID:     %s\n' "$SSID"
printf '  Hermes API:   127.0.0.1:%s\n' "$API_PORT"
"$ROOT_DIR/deploy/pi/preflight.sh"

if ((PREFLIGHT_ONLY == 1)); then
  printf '\npreflight_only_complete=yes\n'
  exit 0
fi

if ((ASSUME_YES == 0)); then
  [[ -t 0 ]] || {
    printf 'Interactive confirmation is unavailable. Re-run with --yes after reviewing preflight.\n' >&2
    exit 2
  }
  printf '\nThis will create a private access point on %s and restart gateway services. Continue? [y/N] ' "$EMO_WIFI"
  read -r confirmation
  [[ $confirmation =~ ^[Yy]$ ]] || { printf 'Installation cancelled.\n'; exit 1; }
fi

"$ROOT_DIR/deploy/pi/install.sh" \
  --uplink "$UPLINK" \
  --wifi "$EMO_WIFI" \
  --ssid "$SSID" \
  --user "$SERVICE_USER" \
  --payload "$ROOT_DIR"

/usr/local/sbin/emo-agent-configure-hermes \
  --user "$SERVICE_USER" \
  --hermes-bin "$HERMES_BIN" \
  --api-port "$API_PORT" \
  --session-id "$SESSION_ID"

/usr/local/sbin/emo-agent-verify

printf '\ninstallation_complete=yes\n'
printf 'EMO Wi-Fi: %s\n' "$SSID"
printf 'The generated password is stored at /etc/emo-agent/ap.psk. Display it explicitly with:\n'
printf '  sudo cat /etc/emo-agent/ap.psk\n'
printf 'After the first voice request, run: sudo emo-agent-verify --require-audio\n'
printf 'To uninstall the bridge later, run: sudo emo-agent-rollback\n'
