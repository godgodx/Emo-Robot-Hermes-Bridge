"""Living.AI acknowledgement phrases and validated local audio cache."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


ACKNOWLEDGEMENTS = (
    "Je regarde ça.",
    "D'accord, je m'en occupe.",
    "Je me mets au travail.",
    "Laisse-moi vérifier ça.",
    "Bonne question, je regarde.",
    "Un instant, je mène l'enquête.",
    "Je consulte mes neurones numériques.",
    "Je mets mon petit cerveau en mode turbo.",
    "Mission acceptée. Je reviens avec la réponse.",
    "Je fouille dans mes circuits. Ne bouge pas.",
)
RESET_ACKNOWLEDGEMENT = "C'est fait. La nouvelle conversation est prête."
NETWORK_ACK_ANIMATIONS = ("Hi", "mood_happy", "mood_excited")
MAX_AUDIO_BYTES = 2 * 1024 * 1024
_CLIP_ID = re.compile(r"^(ack-(?:0[1-9]|10)|reset)$")


class AudioCacheError(ValueError):
    pass


@dataclass(frozen=True)
class AudioClip:
    clip_id: str
    text: str
    content_type: str
    data: bytes


@dataclass(frozen=True)
class AudioLibrary:
    acknowledgements: tuple[AudioClip, ...]
    reset: AudioClip

    @property
    def by_id(self) -> dict[str, AudioClip]:
        return {clip.clip_id: clip for clip in (*self.acknowledgements, self.reset)}


def expected_clips() -> tuple[tuple[str, str], ...]:
    acknowledgements = tuple(
        (f"ack-{index:02d}", text)
        for index, text in enumerate(ACKNOWLEDGEMENTS, start=1)
    )
    return (*acknowledgements, ("reset", RESET_ACKNOWLEDGEMENT))


def cache_version() -> str:
    payload = json.dumps(expected_clips(), ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def version_directory(root: Path) -> Path:
    return root / cache_version()


def detect_audio_format(data: bytes, content_type: str = "") -> tuple[str, str]:
    if len(data) > MAX_AUDIO_BYTES:
        raise AudioCacheError("Living.AI acknowledgement audio is too large")
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WAVE":
        return ".wav", "audio/wav"
    if data.startswith(b"ID3") or (len(data) >= 2 and data[0] == 0xFF and data[1] & 0xE0 == 0xE0):
        return ".mp3", "audio/mpeg"
    raise AudioCacheError(f"Unsupported Living.AI audio format ({content_type or 'unknown'})")


def write_library_atomic(root: Path, generated: Iterable[tuple[str, str, str, bytes]]) -> AudioLibrary:
    root.mkdir(parents=True, exist_ok=True)
    final_dir = version_directory(root)
    staging = root / f".{cache_version()}.{os.getpid()}.tmp"
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(mode=0o750)
    records: list[dict[str, object]] = []
    try:
        for clip_id, text, upstream_type, data in generated:
            if not _CLIP_ID.fullmatch(clip_id):
                raise AudioCacheError("Invalid acknowledgement clip identifier")
            suffix, content_type = detect_audio_format(data, upstream_type)
            filename = clip_id + suffix
            (staging / filename).write_bytes(data)
            records.append(
                {
                    "id": clip_id,
                    "text": text,
                    "file": filename,
                    "content_type": content_type,
                    "sha256": hashlib.sha256(data).hexdigest(),
                }
            )
        manifest = {"version": cache_version(), "clips": records}
        (staging / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, separators=(",", ":")),
            encoding="utf-8",
        )
        library = load_library_from_directory(staging)
        if final_dir.exists():
            shutil.rmtree(final_dir)
        os.replace(staging, final_dir)
        return library
    finally:
        if staging.exists():
            shutil.rmtree(staging)


def load_library(root: Path) -> AudioLibrary | None:
    directory = version_directory(root)
    if not directory.is_dir():
        return None
    try:
        return load_library_from_directory(directory)
    except (AudioCacheError, OSError, UnicodeError, json.JSONDecodeError):
        return None


def load_library_from_directory(directory: Path) -> AudioLibrary:
    raw = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or raw.get("version") != cache_version():
        raise AudioCacheError("Acknowledgement manifest version is invalid")
    records = raw.get("clips")
    if not isinstance(records, list) or len(records) != len(expected_clips()):
        raise AudioCacheError("Acknowledgement manifest clip count is invalid")

    expected = dict(expected_clips())
    loaded: dict[str, AudioClip] = {}
    for record in records:
        if not isinstance(record, dict):
            raise AudioCacheError("Acknowledgement manifest record is invalid")
        clip_id = record.get("id")
        text = record.get("text")
        filename = record.get("file")
        content_type = record.get("content_type")
        digest = record.get("sha256")
        if (
            not isinstance(clip_id, str)
            or not _CLIP_ID.fullmatch(clip_id)
            or expected.get(clip_id) != text
            or not isinstance(filename, str)
            or Path(filename).name != filename
            or not isinstance(content_type, str)
            or not isinstance(digest, str)
        ):
            raise AudioCacheError("Acknowledgement manifest values are invalid")
        data = (directory / filename).read_bytes()
        _suffix, detected_type = detect_audio_format(data, content_type)
        if detected_type != content_type or hashlib.sha256(data).hexdigest() != digest:
            raise AudioCacheError("Acknowledgement audio integrity check failed")
        loaded[clip_id] = AudioClip(clip_id, text, content_type, data)

    if set(loaded) != set(expected):
        raise AudioCacheError("Acknowledgement manifest identifiers are incomplete")
    acknowledgements = tuple(loaded[f"ack-{index:02d}"] for index in range(1, 11))
    return AudioLibrary(acknowledgements=acknowledgements, reset=loaded["reset"])
