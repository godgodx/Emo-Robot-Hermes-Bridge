"""Pure helpers for EMO's BLE Theater protocol.

Protocol source: ../../../Emo-Scripts/run.py and the Bluetooth documentation.
"""

from __future__ import annotations

import json
from typing import Any


CHAR_UUID = "0000ffe1-0000-1000-8000-00805f9b34fb"
CHUNK_SIZE = 20
CHUNK_DELAY_SECONDS = 0.02

# Small, deliberately safe initial allowlist. Values are exact Theater keys.
ANIMATIONS: dict[str, list[str]] = {
    "hi": ["Hi"],
    "happy": ["mood_happy"],
    "excited": ["mood_excited"],
    "dj": ["DJ1_ready", "DJ1_loop2"],
}


def frame_json(message: dict[str, Any]) -> bytes:
    payload = json.dumps(message, separators=(",", ":")).encode("utf-8")
    size = len(payload)
    if size > 0xFFFF:
        raise ValueError("BLE payload exceeds the 16-bit frame length")
    return bytes((0xBB, 0xAA, size & 0xFF, (size >> 8) & 0xFF)) + payload


def theater_packet(**data: Any) -> bytes:
    clean = {key: value for key, value in data.items() if value is not None}
    return frame_json({"type": "theater_req", "data": clean})


def theater_operation(operation: str) -> bytes:
    return theater_packet(op=operation)


def theater_animation(animation: str) -> bytes:
    try:
        names = ANIMATIONS[animation]
    except KeyError as exc:
        allowed = ", ".join(sorted(ANIMATIONS))
        raise ValueError(f"Unknown animation. Allowed values: {allowed}") from exc
    return theater_packet(op="play", animations=names)


def theater_speech(text: str) -> bytes:
    normalized = text.strip()
    if not normalized:
        raise ValueError("Speech text cannot be empty")
    if len(normalized) > 500:
        raise ValueError("Speech text is limited to 500 characters")
    return theater_packet(op="speak", txt=normalized)


class ResponseAssembler:
    """Reassemble 0xBBAA JSON notifications split across BLE packets."""

    def __init__(self) -> None:
        self._buffer: bytearray | None = None
        self._expected = 0

    def feed(self, chunk: bytes) -> dict[str, Any] | None:
        if len(chunk) < 2:
            return None

        if chunk[:2] == b"\xBB\xAA":
            if len(chunk) < 4:
                self._reset()
                return None
            self._expected = chunk[2] | (chunk[3] << 8)
            self._buffer = bytearray(chunk[4:])
        elif chunk[:2] == b"\xDD\xCC":
            return None
        elif self._buffer is not None:
            self._buffer.extend(chunk)
        else:
            return None

        if self._buffer is None or len(self._buffer) < self._expected:
            return None

        raw = bytes(self._buffer[: self._expected])
        self._reset()
        try:
            value = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return None
        return value if isinstance(value, dict) else None

    def _reset(self) -> None:
        self._buffer = None
        self._expected = 0
