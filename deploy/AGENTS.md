# Purpose

- Own deployment assets for hosts that run the EMO bridge or its network interception components.

# Ownership

- This file owns cross-platform deployment conventions under `deploy/`.
- Platform-specific installation and operating rules belong to the child contracts indexed below.

# Local Contracts

- Keep deployment steps inspectable, repeatable, and parameterized for the target host.
- Separate read-only preflight checks from package installation, service changes, firewall changes, and network reconfiguration.
- Never embed host passwords, SSH private keys, Wi-Fi credentials, EMO identifiers, or captured authorization material.
- Require an explicit target and approval before any remote state change.

# Work Guidance

- Prefer generated configuration from validated host facts over assumptions about interface names, operating systems, or service managers.

# Verification


# Child DOX Index

- `pi/AGENTS.md` - Raspberry Pi access-point, DNS override, HTTPS relay, and service deployment.
