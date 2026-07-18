"""Hermes Agent API client and strict final-response validation."""

from __future__ import annotations

import asyncio
import json
import os
import re
import secrets
import unicodedata
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from aiohttp import ClientSession


SYSTEM_PROMPT = (
    "Tu réponds par l'intermédiaire du petit robot physique EMO. "
    "Exécute silencieusement toutes les étapes et tous les outils nécessaires. "
    "Ta réponse terminale doit être uniquement un objet JSON compact, sans Markdown, avec exactement "
    "les clés speech et animation. Mets uniquement ta réponse finale naturelle en français dans speech, adaptée à "
    "l'oral et limitée à 450 caractères. animation vaut none, hi, happy, excited ou dj. "
    "Choisis hi pour saluer, happy pour une réponse positive ou chaleureuse, excited pour une bonne "
    "nouvelle ou un fort enthousiasme, dj seulement si danser ou faire la fête est pertinent, sinon none. "
    "Ne décris jamais ton raisonnement, tes étapes ou tes appels d'outils dans speech."
)
MAX_SPOKEN_CHARS = 500
ALLOWED_ANIMATIONS = frozenset({"none", "hi", "happy", "excited", "dj"})
SESSION_ID_PATTERN = re.compile(r"^[a-zA-Z0-9._:-]{1,128}$")
RESET_CONFIRMATION = "D'accord, nouvelle conversation."
RESET_COMMANDS = frozenset(
    {
        "/new",
        "/reset",
        "nouvelle conversation",
        "nouvelle discussion",
        "commence une nouvelle conversation",
        "commence une nouvelle discussion",
        "demarre une nouvelle conversation",
        "demarre une nouvelle discussion",
        "emo nouvelle conversation",
        "emo nouvelle discussion",
    }
)


class HermesError(ValueError):
    pass


@dataclass(frozen=True)
class EmoReply:
    speech: str
    animation: str | None = None


@dataclass(frozen=True)
class HermesBridge:
    api_url: str
    api_key: str = field(repr=False)
    session_id: str = "emo-robot-main"
    model: str = "hermes-agent"


def chat_payload(transcript: str, *, model: str, stream: bool = False) -> dict[str, Any]:
    normalized = transcript.strip()
    if not normalized:
        raise HermesError("Living.AI transcript is empty")
    return {
        "model": model,
        "stream": stream,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": normalized},
        ],
    }


def is_reset_command(transcript: str) -> bool:
    normalized = unicodedata.normalize("NFKD", transcript.casefold())
    normalized = "".join(char for char in normalized if not unicodedata.combining(char))
    normalized = re.sub(r"[^a-z0-9/]+", " ", normalized).strip()
    normalized = re.sub(r"\s+", " ", normalized)
    return normalized in RESET_COMMANDS


def validate_session_id(session_id: str) -> str:
    if not SESSION_ID_PATTERN.fullmatch(session_id):
        raise HermesError("Hermes session identifier is invalid")
    return session_id


def write_active_session(path: Path, session_id: str) -> None:
    normalized = validate_session_id(session_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.{secrets.token_hex(4)}")
    try:
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as output:
            output.write(normalized + "\n")
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def load_active_session(path: Path, default_session_id: str) -> str:
    default = validate_session_id(default_session_id)
    if path.is_file():
        return validate_session_id(path.read_text(encoding="utf-8").strip())
    write_active_session(path, default)
    return default


def rotate_active_session(path: Path | None, base_session_id: str) -> str:
    base = validate_session_id(base_session_id)
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    suffix = secrets.token_hex(4)
    maximum_base = 128 - len(timestamp) - len(suffix) - 2
    session_id = f"{base[:maximum_base]}-{timestamp}-{suffix}"
    validate_session_id(session_id)
    if path is not None:
        write_active_session(path, session_id)
    return session_id


def final_response(raw: bytes) -> str:
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise HermesError("Hermes response is not UTF-8 JSON") from exc
    if not isinstance(payload, dict):
        raise HermesError("Hermes response root is not an object")

    state = payload.get("hermes")
    if isinstance(state, dict) and (
        state.get("failed") is True
        or state.get("partial") is True
        or state.get("completed") is False
    ):
        raise HermesError("Hermes run is incomplete")

    choices = payload.get("choices")
    if not isinstance(choices, list) or len(choices) != 1 or not isinstance(choices[0], dict):
        raise HermesError("Hermes response must contain exactly one choice")
    choice = choices[0]
    if choice.get("finish_reason") != "stop":
        raise HermesError("Hermes run did not finish normally")
    message = choice.get("message")
    if not isinstance(message, dict):
        raise HermesError("Hermes final message is missing")
    content = message.get("content")
    if not isinstance(content, str) or not content.strip():
        raise HermesError("Hermes final message is empty")
    normalized = content.strip()
    if len(normalized) > 8_000:
        raise HermesError("Hermes final message is unexpectedly large")
    return normalized


def parse_emo_reply(content: str) -> EmoReply:
    """Parse Hermes's terminal EMO envelope, with safe plain-text compatibility."""
    normalized = content.strip()
    if normalized.startswith("```") and normalized.endswith("```"):
        lines = normalized.splitlines()
        if len(lines) >= 3:
            normalized = "\n".join(lines[1:-1]).strip()

    try:
        payload = json.loads(normalized)
    except json.JSONDecodeError:
        payload = None

    if payload is None:
        speech = normalized
        animation = None
    elif isinstance(payload, dict):
        if set(payload) != {"speech", "animation"}:
            raise HermesError("Hermes EMO response has invalid fields")
        speech = payload.get("speech")
        animation_value = payload.get("animation")
        if not isinstance(animation_value, str) or animation_value not in ALLOWED_ANIMATIONS:
            raise HermesError("Hermes EMO animation is invalid")
        animation = None if animation_value == "none" else animation_value
    else:
        raise HermesError("Hermes EMO response is not an object")

    if not isinstance(speech, str) or not speech.strip():
        raise HermesError("Hermes EMO speech is empty")
    speech = speech.strip()
    if len(speech) > MAX_SPOKEN_CHARS:
        raise HermesError("Hermes final message is too long for EMO speech")
    return EmoReply(speech=speech, animation=animation)


async def request_streaming_final_response(
    session: ClientSession,
    bridge: HermesBridge,
    *,
    transcript: str,
    idempotency_key: str | None,
    tool_started: asyncio.Event,
) -> EmoReply:
    """Consume Hermes SSE while exposing only tool-start and terminal reply."""
    headers = {
        "Authorization": f"Bearer {bridge.api_key}",
        "Content-Type": "application/json",
        "Accept": "text/event-stream",
        "X-Hermes-Session-Id": bridge.session_id,
    }
    if idempotency_key:
        headers["Idempotency-Key"] = idempotency_key
    url = bridge.api_url.rstrip("/") + "/v1/chat/completions"
    chunks: list[str] = []
    total_chars = 0
    finish_reason: str | None = None
    event_name = "message"
    data_lines: list[str] = []

    def consume_event() -> None:
        nonlocal event_name, data_lines, finish_reason, total_chars
        data = "\n".join(data_lines)
        current_event = event_name
        event_name = "message"
        data_lines = []
        if not data or data == "[DONE]":
            return
        try:
            payload = json.loads(data)
        except json.JSONDecodeError as exc:
            raise HermesError("Hermes SSE event is not JSON") from exc
        if current_event == "hermes.tool.progress":
            if isinstance(payload, dict) and payload.get("status") == "running":
                tool_started.set()
                chunks.clear()
                total_chars = 0
            return
        if not isinstance(payload, dict):
            raise HermesError("Hermes SSE chunk is not an object")
        choices = payload.get("choices")
        if not isinstance(choices, list) or len(choices) != 1 or not isinstance(choices[0], dict):
            raise HermesError("Hermes SSE chunk has invalid choices")
        choice = choices[0]
        delta = choice.get("delta")
        if isinstance(delta, dict):
            content = delta.get("content")
            if isinstance(content, str):
                chunks.append(content)
                total_chars += len(content)
                if total_chars > 8_000:
                    raise HermesError("Hermes SSE response is unexpectedly large")
        reason = choice.get("finish_reason")
        if reason is not None:
            finish_reason = reason

    async with session.post(
        url,
        json=chat_payload(transcript, model=bridge.model, stream=True),
        headers=headers,
        allow_redirects=False,
    ) as response:
        if response.status != 200:
            await response.read()
            raise HermesError(f"Hermes HTTP status is {response.status}")
        async for raw_line in response.content:
            try:
                line = raw_line.decode("utf-8").rstrip("\r\n")
            except UnicodeDecodeError as exc:
                raise HermesError("Hermes SSE is not UTF-8") from exc
            if not line:
                consume_event()
            elif line.startswith("event:"):
                event_name = line[6:].strip()
            elif line.startswith("data:"):
                data_lines.append(line[5:].lstrip())
        if data_lines:
            consume_event()

    if finish_reason != "stop":
        raise HermesError("Hermes streaming run did not finish normally")
    return parse_emo_reply("".join(chunks))


async def request_final_response(
    session: ClientSession,
    bridge: HermesBridge,
    *,
    transcript: str,
    idempotency_key: str | None,
) -> str:
    headers = {
        "Authorization": f"Bearer {bridge.api_key}",
        "Content-Type": "application/json",
        "X-Hermes-Session-Id": bridge.session_id,
    }
    if idempotency_key:
        headers["Idempotency-Key"] = idempotency_key
    url = bridge.api_url.rstrip("/") + "/v1/chat/completions"
    async with session.post(
        url,
        json=chat_payload(transcript, model=bridge.model),
        headers=headers,
        allow_redirects=False,
    ) as response:
        raw = await response.read()
        if response.status != 200:
            raise HermesError(f"Hermes HTTP status is {response.status}")
    return final_response(raw)
