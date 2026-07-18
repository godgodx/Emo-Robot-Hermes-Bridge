#!/usr/bin/env bash
set -euo pipefail

[[ $EUID -eq 0 ]] || { printf 'Run as root.\n' >&2; exit 1; }
REQUIRE_AUDIO=0
if (($#)); then
  [[ $# -eq 1 && $1 == --require-audio ]] || {
    printf 'Usage: %s [--require-audio]\n' "$0" >&2
    exit 2
  }
  REQUIRE_AUDIO=1
fi
export REQUIRE_AUDIO
# shellcheck source=/dev/null
source /etc/emo-agent/state.env

for _attempt in $(seq 1 20); do
  if ss -lnt | grep -qE '(:|\])443[[:space:]]' && \
     ss -lnt | grep -qE '(:|\])80[[:space:]]' && \
     dig +time=1 +tries=1 +short @"$AP_IP" api.living.ai A | grep -Fxq "$AP_IP"; then
    break
  fi
  sleep 0.5
done

printf '[interfaces]\n'
nmcli -t -f DEVICE,TYPE,STATE device | grep -E "^($UPLINK|$WIFI):"
ip -4 -brief address show dev "$WIFI"
ip route show default | grep -E "dev[[:space:]]+$UPLINK([[:space:]]|$)"

printf '\n[services]\n'
for service in emo-agent-network emo-agent-dns emo-agent-bluetooth emo-agent-gateway; do
  printf '%s=' "$service"
  systemctl is-active "$service"
done

printf '\n[bluetooth]\n'
python3 -c 'import bleak' || { printf 'bleak_import=failed\n' >&2; exit 1; }
printf 'bleak_import=ok\n'
btmgmt info | grep -Eq 'current settings:.* powered( |$)' || { printf 'bluetooth_power=failed\n' >&2; exit 1; }
printf 'bluetooth_power=ok\n'

printf '\n[listeners]\n'
ss -lntup | awk 'NR == 1 || /10\.42\.0\.1:53 |0\.0\.0\.0:(80|443) |\[::\]:(80|443) /'

printf '\n[dns]\n'
TARGET=$(dig +short @"$AP_IP" api.living.ai A | tail -n 1)
[[ $TARGET == "$AP_IP" ]] || { printf 'targeted_dns=failed (%s)\n' "$TARGET" >&2; exit 1; }
printf 'targeted_dns=ok\n'
FORWARDED=$(dig +short @"$AP_IP" example.com A | head -n 1)
[[ -n $FORWARDED ]] || { printf 'forwarded_dns=failed\n' >&2; exit 1; }
printf 'forwarded_dns=ok\n'

printf '\n[https]\n'
PYTHONPATH=/opt/emo-agent python3 - <<'PY'
import json
import os
import ssl
import urllib.request

context = ssl._create_unverified_context()
health = urllib.request.urlopen(
    "https://127.0.0.1/_emo_agent/health", context=context, timeout=10
).read()
payload = json.loads(health)
assert payload["status"] == "ok"
mode = payload.get("mode")
assert mode in {"observe", "hermes"}
print(f"gateway_health=ok mode={mode}")
if mode == "hermes":
    assert payload.get("session_state_persistent") is True
    ready = payload.get("ack_audio_ready") is True
    count = payload.get("ack_audio_count", 0)
    if os.environ.get("REQUIRE_AUDIO") == "1":
        assert ready and count == 11
        audio_request = urllib.request.Request(
            "http://127.0.0.1/_emo_agent/audio/ack-01",
            headers={"Host": "api.living.ai"},
        )
        audio_response = urllib.request.urlopen(audio_request, timeout=10)
        sample = audio_response.read(12)
        assert sample.startswith(b"RIFF") or sample.startswith(b"ID3") or sample[:1] == b"\xff"
    print(f"ack_audio={'ready' if ready else 'unprimed'} count={count}")

request = urllib.request.Request(
    "https://127.0.0.1/time", headers={"Host": "api.living.ai"}
)
response = urllib.request.urlopen(request, context=context, timeout=20)
assert response.status == 200
payload = json.loads(response.read())
assert isinstance(payload.get("time"), int)
print("gateway_passthrough=ok")
PY

if [[ -s /etc/emo-agent/hermes.env ]]; then
  [[ -s /var/lib/emo-agent/active-session ]] || {
    printf 'session_state=missing\n' >&2
    exit 1
  }
  [[ $(stat -c '%a' /var/lib/emo-agent/active-session) == 600 ]] || {
    printf 'session_state=bad_permissions\n' >&2
    exit 1
  }
  printf 'session_state=persistent\n'
  set -a
  # shellcheck source=/dev/null
  source /etc/emo-agent/hermes.env
  set +a
  curl --fail --silent --show-error \
    --header "Authorization: Bearer $HERMES_API_KEY" \
    http://127.0.0.1:8642/health >/dev/null
  printf 'hermes_api=ok\n'
fi

printf '\nverification_complete=yes\n'
