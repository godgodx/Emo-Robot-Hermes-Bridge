# Purpose

- Turn a dedicated Raspberry Pi into a reversible EMO test-network gateway without requiring router administration.

# Ownership

- This file owns Raspberry Pi preflight, installation, service definitions, AP/DHCP/DNS configuration, and rollback guidance.

# Local Contracts

- Require separate upstream and access-point interfaces before changing networking. The validated lab topology uses built-in `wlan0` for Internet/SSH and TP-Link AR9271 `wlan1` (`ath9k_htc`) for the EMO access point.
- Run read-only preflight first and derive interface names, OS version, network manager, firewall backend, occupied ports, and package state from its output.
- Keep the DNS override narrow to `api.living.ai`; forward unrelated DNS and HTTPS traffic normally.
- Bind the interception gateway only where required and keep its health endpoint and redacted event log visible during tests.
- Bind plain HTTP port 80 only for firmware-compatible acknowledgement and ephemeral Theater TTS downloads; keep API interception on TLS port 443.
- Keep `/time` local to the gateway so EMO completes clock initialization without an upstream relay dependency.
- Preserve SSH access on `wlan0` and provide tested rollback steps before changing `wlan1`, DHCP, DNS, firewall, or boot services.
- Do not connect EMO to the test AP until the AP, upstream NAT, DNS resolution, TLS listener, and rollback path have been verified independently.
- Implement temporary response tests through a narrowly scoped systemd drop-in, verify the one-shot counter, and remove the drop-in immediately after success or failure so the gateway returns to observation mode.
- Bind the Hermes API adapter to `127.0.0.1` only, require a strong generated `API_SERVER_KEY`, share it with the EMO service through a root-owned protected environment file, and keep the Hermes gateway enabled through the user's systemd service plus linger.
- Configure Hermes mode with `configure-hermes.sh`; on configuration failure, remove the EMO systemd drop-in and restore observation mode automatically.
- Install `python3-bleak`, BlueZ, and `rfkill`; keep the built-in controller unblocked and HCI-up through `emo-agent-bluetooth.service` without changing EMO's pairing or persistent settings.
- Allow `AF_BLUETOOTH` in the hardened gateway service and keep Bluetooth failure isolated from the AP, DNS, HTTPS pass-through, and Hermes services.
- Provision `/var/lib/emo-agent/ack-audio` through the gateway systemd state directory; preserve the validated 11-clip cache across service and host restarts and remove it during full rollback.
- Persist only the active Hermes session pointer in `/var/lib/emo-agent/active-session` with mode `0600`; spoken conversation rotation must never delete historical Hermes sessions.
- Keep the Pi's upstream Internet/Wi-Fi setup outside the installer. The public root `install.sh` may detect an existing default-route interface and one secondary Wi-Fi adapter, but must stop on ambiguity and create only the dedicated EMO access point.
- Require the low-level `install.sh` to receive an explicit Hermes service user; never fall back to a repository author's username.

# Work Guidance

- Prefer SSH keys over passwords and never request that a password be pasted into project files or chat logs.
- Use explicit `--uplink wlan0 --wifi wlan1` values for this validated host; state-changing scripts must still reject missing or mismatched interfaces.

# Verification

- Run `bash -n preflight.sh`.
- Run `bash -n install.sh configure-hermes.sh repair-dns.sh rollback.sh verify.sh` before deployment.
- Run `systemd-analyze verify emo-agent-bluetooth.service emo-agent-gateway.service` on the target after installing or changing either unit.
- Run `./preflight.sh` on the target Pi and retain only sanitized system/network facts needed to generate the deployment.
- Run `sudo ./verify.sh` after installation and before moving EMO to the access point.
- When Hermes mode is configured, verify both `127.0.0.1:8642/health` and `/_emo_agent/health` before a live robot test.
- After the first live Hermes request, run `verify.sh --require-audio` and require all 11 acknowledgement/reset clips to be ready.
- Require both gateway listeners, TCP 443 and TCP 80, before validating cached or Theater speech.
- Verify `emo-agent-bluetooth.service`, the `bleak` import, powered HCI state, and a passive EMO scan before testing deferred delivery.
- Verify the active-session pointer exists with mode `0600` and retains the same value after restarting the gateway.
- Run the root installer in `--preflight-only` mode without interface overrides to verify safe auto-detection, then validate an idempotent full installation on a supported Pi when installer behavior changes materially.

# Child DOX Index
