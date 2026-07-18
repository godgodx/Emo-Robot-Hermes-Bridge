# Security policy

## Supported versions

Security fixes are applied to the latest commit on the default branch. This project is
experimental and does not currently publish versioned releases.

## Reporting a vulnerability

Please do not publish credentials, private traffic captures, voice transcripts, robot
identifiers, or a working exploit in a public issue. Contact the repository owner
privately through their GitHub profile and include only the minimum information needed to
reproduce the issue safely.

## Deployment boundaries

- Run the bridge on a dedicated private access point, not a public or untrusted LAN.
- Keep the Hermes API bound to `127.0.0.1`.
- Do not copy `/etc/emo-agent`, `/var/lib/emo-agent`, captures, or service logs into bug
  reports without reviewing and redacting them first.
- Keep SSH on the existing uplink and verify the detected topology before installation.
- Treat interception certificates, API keys, Wi-Fi credentials, and Living.AI headers as
  secrets.

The bridge intentionally retains Living.AI for speech recognition and synthesis. Audio
and associated cloud metadata therefore remain subject to Living.AI's service and privacy
terms.
