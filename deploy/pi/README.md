# Raspberry Pi deployment details

The supported topology uses one interface for the Pi's existing Internet/SSH connection
and a separate Wi-Fi adapter for EMO. The deployment redirects only `api.living.ai` to
the local gateway and forwards other traffic normally.

```text
Internet/router -- uplink --> Raspberry Pi -- secondary Wi-Fi --> private EMO AP --> EMO
                              | targeted DNS
                              | HTTPS :443 / HTTP :80
                              | Hermes API on 127.0.0.1:8642
                              + Bluetooth final delivery
```

## Prepare the Pi

1. Install and verify Hermes Agent for a non-root user.
2. Connect the Pi's primary interface to the Internet.
3. Confirm SSH uses that interface.
4. Plug in a second Wi-Fi adapter that supports access-point mode.
5. Keep EMO on its current network until verification succeeds.

The top-level installer intentionally leaves the existing Internet connection untouched.
It will stop if the default route is missing, the Wi-Fi roles are ambiguous, or the two
roles resolve to the same interface.

## Preferred installation

From the repository root:

```bash
sudo ./install.sh --preflight-only
sudo ./install.sh
```

Use explicit parameters when auto-detection is not unique:

```bash
sudo ./install.sh \
  --uplink wlan0 \
  --wifi wlan1 \
  --ssid EMO-HERMES \
  --user pi \
  --hermes-bin /home/pi/.hermes/hermes-agent/venv/bin/hermes
```

`--preflight-only` is read-only. A normal run summarizes the resolved topology and asks
for confirmation before changing networking. `--yes` is available for deliberate
non-interactive provisioning.

## Installed components

- `/opt/emo-agent`: immutable application source used by the service.
- `/etc/emo-agent`: protected certificates, local credentials, and network configuration.
- `/var/lib/emo-agent`: acknowledgement cache and active Hermes session pointer.
- `emo-agent-network.service`: narrow forwarding and NAT.
- `emo-agent-dns.service`: DHCP and targeted DNS for the EMO access point.
- `emo-agent-bluetooth.service`: bounded Bluetooth controller preparation.
- `emo-agent-gateway.service`: HTTPS interception, HTTP audio, Hermes routing, and BLE
  delivery.
- `hermes-gateway.service`: the existing Hermes user's gateway service, installed through
  Hermes's own CLI.

The installer generates a WPA2 password in `/etc/emo-agent/ap.psk`, enables only the
Hermes loopback API, creates a protected API key when needed, and persists the active EMO
conversation pointer without deleting older Hermes sessions.

## Verification

```bash
sudo emo-agent-verify
sudo emo-agent-verify --require-audio  # after the first authenticated EMO voice request
```

Verification checks interfaces, routes, all four EMO services, ports 80 and 443, targeted
and forwarded DNS, Bluetooth power, the Python BLE dependency, gateway health, Hermes
loopback health, and the persistent session pointer.

If only upstream DNS selection is unhealthy:

```bash
sudo emo-agent-repair-dns
```

## Recovery

Keep an SSH session open through the uplink while first installing. If the private EMO
network needs to be removed:

```bash
sudo emo-agent-rollback
```

Rollback disables and removes this project's services, NetworkManager access-point
profile, nftables table, certificates, local credentials, audio cache, and active-session
pointer. It restores the prior IP-forwarding value and powers Bluetooth off only if it
was off before installation. It does not uninstall Hermes, delete Hermes conversations,
or remove Debian packages.

## Advanced scripts

The scripts in this directory are implementation layers used by the top-level installer:

- `preflight.sh`: read-only host inspection.
- `install.sh`: gateway, networking, Bluetooth, and systemd provisioning.
- `configure-hermes.sh`: loopback API and bridge integration.
- `verify.sh`: operational checks.
- `repair-dns.sh`: upstream resolver repair.
- `rollback.sh`: scoped uninstall.

Prefer the top-level `install.sh` unless debugging or integrating with another
provisioning system.
