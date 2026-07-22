# Purpose

- Own GitHub-hosted automation and repository community configuration.

# Ownership

- This file owns workflows and templates under `.github/`.

# Local Contracts

- Keep CI free of repository, robot, network, and account secrets.
- Keep validation workflows read-only. They must not commit, push, publish, or deploy unless a future task explicitly introduces and documents that behavior.
- Validate Python behavior, compilation, and shell syntax on every push and pull request.
- Use only maintained, version-pinned major releases of GitHub Actions.

# Work Guidance

- Keep checks reproducible from the public development commands in `README.md`.

# Verification

- Validate workflow YAML structure before publishing.
- Run every command represented by the workflow locally or on the validated Pi when practical.

# Child DOX Index
