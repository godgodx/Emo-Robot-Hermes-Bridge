# EMO Hermes Bridge

[![CI](https://github.com/godgodx/Emo-Robot-Hermes-Bridge/actions/workflows/ci.yml/badge.svg)](https://github.com/godgodx/Emo-Robot-Hermes-Bridge/actions/workflows/ci.yml)

A Raspberry Pi gateway that connects a Living.AI EMO robot to
[Hermes Agent](https://github.com/NousResearch/hermes-agent). EMO keeps its familiar
cloud speech recognition and voice, while Hermes handles the conversation, tools,
and multi-step work.

The bridge is designed for a dedicated Raspberry Pi with two network interfaces. It
creates a private Wi-Fi network for EMO, intercepts only `api.living.ai`, sends the
recognized text to the local Hermes gateway, and delivers only Hermes's final answer
back through EMO.

> [!IMPORTANT]
> This is an independent, experimental project. It is not affiliated with or endorsed
> by Living.AI or Nous Research. Firmware or cloud API changes may break compatibility.

## What it does

- Routes EMO conversations through an existing Hermes Agent installation.
- Keeps Living.AI speech-to-text and text-to-speech for EMO's original voice.
- Plays an immediate cached acknowledgement so long Hermes tasks do not feel frozen.
- Delivers Hermes's final response later over Bluetooth, followed by an appropriate
  allowlisted animation.
- Supports simple and multi-step Hermes workflows while hiding intermediate reasoning,
  tool arguments, and tool output from EMO.
- Starts a fresh conversation with phrases such as "new conversation" without deleting
  earlier Hermes sessions.
- Falls back to the original Living.AI response if Hermes cannot accept the request.
- Installs hardened, restart-safe systemd services with a complete rollback command.

## Architecture

```text
Internet / home router
        |
  primary Wi-Fi (Internet + SSH)
        |
  Raspberry Pi
    |-- targeted DNS + HTTPS gateway --> Living.AI STT/TTS
    |-- loopback API -----------------> Hermes Agent
    |-- Bluetooth --------------------> final speech + animation
        |
  secondary Wi-Fi access point
        |
       EMO
```

When EMO sends a recognized voice request, the gateway immediately returns one of ten
short acknowledgement clips generated in EMO's Living.AI voice. Hermes continues in the
background. Once its terminal response is ready, the Pi obtains the final Living.AI TTS
audio and uses EMO's Theater Bluetooth protocol to play the response and optional
animation.

## Requirements

- A Living.AI EMO robot with working cloud access.
- A Raspberry Pi running a current Debian-based Raspberry Pi OS with systemd and
  NetworkManager.
- Hermes Agent already installed and working for a non-root Linux user.
- One interface already connected to the Internet and used for SSH.
- A second Wi-Fi adapter capable of access-point mode, dedicated to EMO.
- A working Bluetooth controller on the Pi.

The validated setup uses Raspberry Pi OS, built-in `wlan0` for Internet, and a TP-Link
AR9271/`ath9k_htc` adapter as `wlan1` for EMO. Other adapters may work if Linux reports
access-point support.

## Wi-Fi preparation

Connect the Pi's primary interface to your normal network before running the installer,
confirm that SSH works through it, and then plug in the secondary Wi-Fi adapter.

The installer deliberately does **not** configure the Pi's upstream/home Wi-Fi. Doing so
automatically could disconnect SSH or overwrite a user's NetworkManager profile. It does
automatically create the isolated EMO access point on the secondary adapter after
validating the topology.

Do not move EMO to the new access point until installation and verification complete.

## Quick start

```bash
git clone https://github.com/godgodx/Emo-Robot-Hermes-Bridge.git
cd Emo-Robot-Hermes-Bridge

# Read-only: shows what the installer detected.
sudo ./install.sh --preflight-only

# Installs the gateway and connects it to the existing Hermes installation.
sudo ./install.sh
```

The installer automatically detects the default-route interface, a single secondary
Wi-Fi adapter, the user who invoked `sudo`, and common Hermes installation paths. If the
machine is ambiguous, it stops instead of guessing:

```bash
sudo ./install.sh \
  --uplink wlan0 \
  --wifi wlan1 \
  --user pi \
  --hermes-bin /home/pi/.hermes/hermes-agent/venv/bin/hermes
```

At the end, the installer prints the EMO Wi-Fi name and the protected path containing its
generated password. Display it deliberately with `sudo cat /etc/emo-agent/ap.psk`, then
use the official EMO app to connect the robot to that access point. The password file is
root-only and is never written to service logs.

After the first successful voice request, verify that all eleven cached voice clips are
ready:

```bash
sudo emo-agent-verify --require-audio
```

See [deploy/pi/README.md](deploy/pi/README.md) for the detailed network model,
verification commands, service layout, and recovery procedure.

## Conversation behavior

Hermes must return a compact terminal contract containing spoken text and one optional
logical animation. The bridge accepts only `hi`, `happy`, `excited`, and `dj`; every
other action is rejected. Final speech is bounded to fit the tested Bluetooth path.

The following exact voice commands rotate to a new Hermes session:

- `new conversation` or `new discussion`;
- `nouvelle conversation` or `nouvelle discussion`;
- `démarre une nouvelle conversation`;
- `/new` or `/reset`, if the speech recognizer produces them literally.

Rotation never deletes the previous session. The active session pointer survives service
and Pi restarts, and Hermes may still access older sessions through its history features.

## Operations

```bash
# Overall health, network, Bluetooth, and Hermes API
sudo emo-agent-verify

# Require the complete Living.AI acknowledgement cache
sudo emo-agent-verify --require-audio

# Repair only the selected upstream DNS resolver
sudo emo-agent-repair-dns

# Remove the EMO gateway and restore its network/Bluetooth changes
sudo emo-agent-rollback
```

Useful logs are available through:

```bash
journalctl -u emo-agent-gateway.service -f
```

Events are intentionally metadata-only. Transcripts, prompts, final answers, API keys,
authorization headers, BLE addresses, and temporary final audio are not logged.

## Security and privacy boundaries

- Only `api.living.ai` is redirected on the private EMO network; unrelated traffic is
  forwarded normally.
- The Hermes API listens on `127.0.0.1` and uses a generated local credential.
- Cached acknowledgement clips persist locally; final response audio is kept only in a
  small in-memory cache.
- Living.AI still receives EMO audio for speech recognition and supplies TTS. This bridge
  is not an offline voice stack.
- The generated certificate, Wi-Fi password, local API key, captures, and logs are
  excluded from Git.

Review [SECURITY.md](SECURITY.md) before exposing or modifying the network design.

## Development

```bash
python3 -m pip install -r requirements.txt
python3 -m unittest discover -s tests -v
python3 -m compileall -q emo_agent tests
bash -n install.sh deploy/pi/*.sh
```

The repository also contains a small direct BLE CLI for discovery, allowlisted
animations, and bounded speech tests. Close the official EMO app before direct BLE use:

```bash
python3 -m emo_agent scan
python3 -m emo_agent animate happy
python3 -m emo_agent speak "Hello from Hermes."
```

## Uninstall

```bash
sudo emo-agent-rollback
```

Rollback removes only this bridge's services, private access point, gateway state, and
network rules. It does not uninstall Hermes Agent or remove Hermes conversation history.
Debian packages installed as dependencies remain installed but inactive.

## License

The bridge code is available under the [MIT License](LICENSE). Living.AI, EMO, Hermes,
and related names and marks belong to their respective owners.
