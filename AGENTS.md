# Purpose

- Build, validate, and publish the Raspberry Pi gateway that lets Hermes Agent converse through EMO and trigger suitable robot actions.
- Combine deterministic BLE control with a network bridge that routes Living.AI transcription results through Hermes Agent and returns only Hermes's final answer to EMO.

# Ownership

- This file owns the Python prototype, its tests, local operating instructions, and future interception runtime under `workdir/emo-agent/`.

# Local Contracts

- Derive BLE framing and Theater commands from `../../Emo-Scripts/run.py` and protocol evidence under `../../Documentation/`.
- Keep discovery, connection, command, response, Theater exit, and disconnection bounded by timeouts and cleanup paths.
- Do not print or persist BLE addresses, Wi-Fi credentials, authorization headers, generated `Secret` values, voice recordings, or raw private captures by default.
- Start live-device validation with reversible display, speech, or animation actions. Require explicit user direction before movement, shutdown, Wi-Fi changes, firmware operations, or persistent settings.
- Keep deterministic response and action routing testable without a robot or an AI/network dependency.
- Treat network interception as an explicit opt-in mode with visible logs, narrow host scope, and transparent pass-through for unhandled traffic.
- Do not advertise upstream response compression on EMO's behalf; its firmware requires uncompressed JSON for initialization routes such as `/token/*`.
- Answer `/time` locally with EMO's compact Unix-time and requested-timezone-offset envelope; do not relay that initialization route upstream.
- Keep live response-replacement experiments bounded to one successful voice request, preserve the observed response envelope, decode declared transport compression before JSON changes, and restore pass-through mode immediately after validation.
- Integrate Hermes through its official loopback-only API server instead of modifying Hermes core. Accept only complete `finish_reason=stop` results, serialize EMO turns per Hermes session, and preserve the original Living.AI response as the runtime fallback.
- Consume Hermes chat-completion SSE only to detect structured tool-start events and the terminal answer; never relay intermediate text, reasoning, tool names, arguments, or results to EMO or logs.
- Acknowledge every accepted Hermes turn immediately with one of ten Living.AI-voiced cached clips, play a random allowlisted network animation after the clip, then deliver only Hermes's terminal answer through BLE.
- Prime the ten acknowledgement clips plus the dedicated reset confirmation from the first authenticated Living.AI voice request, store only validated audio and an integrity manifest under the configured runtime state directory, and serve cached clips only through the private gateway route.
- For Theater speech, download the authenticated Living.AI TTS result on the Pi, rewrite only its response URL to an unguessable local HTTP route, keep at most eight final clips in memory, and never persist or log their audio, tokens, or text.
- Give deferred BLE speech priority, discover a fresh BlueZ device for every attempt, wait for Theater result `2`, then play Hermes's optional allowlisted animation and wait for its terminal result. Retry the complete BLE delivery three times without persisting its content.
- Require Hermes terminal output to contain spoken text of at most 500 characters and an optional logical animation from `hi`, `happy`, `excited`, or `dj`; reject other structured actions.
- Keep Hermes API credentials in protected host configuration only. Never log transcripts, prompts, final answers, keys, authorization headers, or intermediate agent events.
- Treat spoken `/new` equivalents as exact, accent-insensitive control phrases rather than substring matches. Cancel active EMO Hermes turns, serialize the rotation to a fresh session identifier, preserve every earlier Hermes session, persist the active identifier across restarts, and confirm through the dedicated cached Living.AI clip.
- Present the public project as an independent Hermes Agent gateway for EMO, state clearly that Living.AI still provides STT/TTS, and avoid claims of affiliation or fully offline operation.
- Keep `install.sh` as the primary installation entrypoint. It may auto-detect only unambiguous interfaces and common Hermes paths, must preserve the existing Internet/SSH connection, and must require confirmation before network changes unless `--yes` is explicit.

# Work Guidance

- Use the installed Python 3.12 runtime with `bleak` for BLE, `aiohttp` for the interception gateway, and `cryptography` for disposable local test certificates.
- Prefer non-interactive commands that complete and release the BLE session automatically.
- Redact runtime identifiers in normal output and keep generated certificates, captures, logs, and local configuration ignored by Git.

# Verification

- Run `C:\Python312\python.exe -m unittest discover -s tests -v` from this directory.
- Run `C:\Python312\python.exe -m compileall -q emo_agent tests` from this directory.
- Run `bash -n install.sh deploy/pi/*.sh` and ShellCheck over the same scripts before publishing installer changes.
- Run `C:\Python312\python.exe -m emo_agent scan` before any live action test.
- Run the HTTPS gateway on port 8443 and probe `/_emo_agent/health` before attempting port 443 or changing EMO's DNS path.
- Validate Hermes mode with an isolated API smoke-test session before a live EMO request; verify the physical robot receives both the final speech and the selected animation in the order owned by the active delivery path.
- Validate both Hermes SSE paths with isolated sessions: a simple request must complete without a tool-start event and a forced-tool request must emit one. Delete both smoke sessions afterwards.
- For deferred BLE validation, verify speech and the optional animation each reach Theater terminal result `2` before treating delivery as successful.
- After initial audio priming, require gateway health to report `ack_audio_ready=true`, `ack_audio_count=11`, and no priming task before treating cached acknowledgements as operational.
- Verify reset-phrase matching rejects sentences that merely mention a new conversation; after a live rotation, confirm the active identifier changed, the previous Hermes session remains readable, and the new identifier survives a gateway restart.
- Validate `sudo ./install.sh --preflight-only` and one idempotent full run on the supported Pi topology before publishing installer behavior changes.

# Child DOX Index

- `deploy/AGENTS.md` - deployment assets and operating boundaries for machines that host parts of the EMO bridge.
- `.github/AGENTS.md` - GitHub Actions and repository-hosted automation.
