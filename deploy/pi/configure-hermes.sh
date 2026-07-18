#!/usr/bin/env bash
set -euo pipefail

SERVICE_USER=""
HERMES_BIN=""
API_PORT=8642
SESSION_ID="emo-robot-main"
TMP_ENV=""
TMP_BRIDGE=""
BRIDGE_INSTALLED=0

cleanup() {
  status=$?
  trap - EXIT
  rm -f "${TMP_ENV:-}" "${TMP_BRIDGE:-}"
  if ((status != 0 && BRIDGE_INSTALLED == 1)); then
    rm -f /etc/systemd/system/emo-agent-gateway.service.d/hermes.conf
    rm -f /etc/emo-agent/hermes.env
    systemctl daemon-reload
    systemctl restart emo-agent-gateway.service || true
  fi
  exit "$status"
}
trap cleanup EXIT

usage() {
  printf 'Usage: %s --user USER [--hermes-bin PATH] [--api-port PORT] [--session-id ID]\n' "$0" >&2
  exit 2
}

while (($#)); do
  case "$1" in
    --user) SERVICE_USER=${2:-}; shift 2 ;;
    --hermes-bin) HERMES_BIN=${2:-}; shift 2 ;;
    --api-port) API_PORT=${2:-}; shift 2 ;;
    --session-id) SESSION_ID=${2:-}; shift 2 ;;
    *) usage ;;
  esac
done

[[ $EUID -eq 0 ]] || { printf 'Run as root.\n' >&2; exit 1; }
[[ $SERVICE_USER =~ ^[a-z_][a-z0-9_-]*$ ]] || usage
[[ $API_PORT =~ ^[0-9]{2,5}$ ]] || usage
((API_PORT >= 1024 && API_PORT <= 65535)) || usage
[[ $SESSION_ID =~ ^[a-zA-Z0-9._:-]{1,128}$ ]] || usage

SERVICE_HOME=$(getent passwd "$SERVICE_USER" | cut -d: -f6)
[[ -n $SERVICE_HOME && -d $SERVICE_HOME ]] || { printf 'Missing home for %s.\n' "$SERVICE_USER" >&2; exit 1; }
if [[ -z $HERMES_BIN ]]; then
  HERMES_BIN="$SERVICE_HOME/.local/bin/hermes"
fi
[[ $HERMES_BIN == /* && -x $HERMES_BIN ]] || { printf 'Hermes executable not found.\n' >&2; exit 1; }
[[ -d /etc/emo-agent && -f /etc/systemd/system/emo-agent-gateway.service ]] || {
  printf 'Install the EMO gateway before configuring Hermes.\n' >&2
  exit 1
}

HERMES_ENV="$SERVICE_HOME/.hermes/.env"
install -d -o "$SERVICE_USER" -g "$SERVICE_USER" -m 700 "$SERVICE_HOME/.hermes"
touch "$HERMES_ENV"
chown "$SERVICE_USER:$SERVICE_USER" "$HERMES_ENV"
chmod 600 "$HERMES_ENV"

API_KEY=$(sed -n 's/^API_SERVER_KEY=//p' "$HERMES_ENV" | tail -n 1)
if ((${#API_KEY} < 32)); then
  API_KEY=$(openssl rand -hex 32)
  TMP_ENV=$(mktemp)
  grep -v '^API_SERVER_KEY=' "$HERMES_ENV" > "$TMP_ENV" || true
  printf '\nAPI_SERVER_KEY=%s\n' "$API_KEY" >> "$TMP_ENV"
  install -o "$SERVICE_USER" -g "$SERVICE_USER" -m 600 "$TMP_ENV" "$HERMES_ENV"
fi

printf '[1/4] Configuring the loopback-only Hermes API adapter...\n'
runuser -u "$SERVICE_USER" -- "$HERMES_BIN" config set platforms.api_server.enabled true
runuser -u "$SERVICE_USER" -- "$HERMES_BIN" config set platforms.api_server.extra.host 127.0.0.1
runuser -u "$SERVICE_USER" -- "$HERMES_BIN" config set platforms.api_server.extra.port "$API_PORT"

printf '[2/4] Installing the Hermes user gateway service...\n'
loginctl enable-linger "$SERVICE_USER"
runuser -u "$SERVICE_USER" -- "$HERMES_BIN" gateway install --force --start-now --start-on-login

printf '[3/4] Sharing only the local API credential with the EMO service...\n'
TMP_BRIDGE=$(mktemp)
printf 'HERMES_API_KEY=%s\n' "$API_KEY" > "$TMP_BRIDGE"
install -o root -g "$SERVICE_USER" -m 640 "$TMP_BRIDGE" /etc/emo-agent/hermes.env
install -d -o root -g root -m 755 /etc/systemd/system/emo-agent-gateway.service.d
cat > /etc/systemd/system/emo-agent-gateway.service.d/hermes.conf <<EOF
[Service]
EnvironmentFile=/etc/emo-agent/hermes.env
StateDirectory=emo-agent
StateDirectoryMode=0750
ExecStart=
ExecStart=/usr/bin/python3 -m emo_agent.gateway serve --host 0.0.0.0 --port 443 --audio-port 80 --cert /etc/emo-agent/certs/api.living.ai.crt --key /etc/emo-agent/certs/api.living.ai.key --hermes-api-url=http://127.0.0.1:$API_PORT --hermes-session-id=$SESSION_ID --hermes-session-state-file=/var/lib/emo-agent/active-session --ack-audio-dir=/var/lib/emo-agent/ack-audio
EOF
chmod 644 /etc/systemd/system/emo-agent-gateway.service.d/hermes.conf
BRIDGE_INSTALLED=1

printf '[4/4] Restarting and verifying both gateways...\n'
systemctl daemon-reload
systemctl restart emo-agent-gateway.service

HERMES_READY=0
for _attempt in $(seq 1 60); do
  if curl --fail --silent --show-error \
    --header "Authorization: Bearer $API_KEY" \
    "http://127.0.0.1:$API_PORT/health" >/dev/null 2>&1; then
    HERMES_READY=1
    break
  fi
  sleep 1
done
((HERMES_READY == 1)) || { printf 'Hermes API did not become ready.\n' >&2; exit 1; }

GATEWAY_READY=0
for _attempt in $(seq 1 30); do
  MODE=$(curl -ks --max-time 2 https://127.0.0.1/_emo_agent/health 2>/dev/null | \
    python3 -c 'import json,sys; print(json.load(sys.stdin).get("mode", ""))' 2>/dev/null || true)
  if [[ $MODE == hermes ]]; then
    GATEWAY_READY=1
    break
  fi
  sleep 1
done
((GATEWAY_READY == 1)) || { printf 'EMO gateway did not enter Hermes mode.\n' >&2; exit 1; }
printf 'hermes_bridge_configured=yes\n'
