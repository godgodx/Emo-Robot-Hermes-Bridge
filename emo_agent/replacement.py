"""Pure helpers for one-shot voice-response replacement experiments."""

from __future__ import annotations

import gzip
import json
import zlib
from typing import Any


class ReplacementError(ValueError):
    pass


def decode_transport_body(body: bytes, content_encoding: str | None) -> bytes:
    encoding = (content_encoding or "").strip().lower()
    try:
        if not encoding or encoding == "identity":
            return body
        if encoding == "gzip":
            return gzip.decompress(body)
        if encoding == "deflate":
            return zlib.decompress(body)
    except (gzip.BadGzipFile, EOFError, zlib.error) as exc:
        raise ReplacementError(f"invalid {encoding} response") from exc
    raise ReplacementError(f"unsupported content encoding: {encoding}")


def response_language(body: bytes) -> str:
    payload = _decode_response(body)
    language = payload.get("languageCode")
    if not isinstance(language, str) or not language.strip():
        return "fr"
    return language.strip()[:16]


def response_query_text(body: bytes) -> str:
    payload = _decode_response(body)
    query_result = payload.get("queryResult")
    if not isinstance(query_result, dict):
        raise ReplacementError("queryResult is missing")
    query_text = query_result.get("queryText")
    if not isinstance(query_text, str) or not query_text.strip():
        raise ReplacementError("queryText is missing")
    return query_text.strip()


def response_query_id(body: bytes) -> str | None:
    payload = _decode_response(body)
    query_id = payload.get("queryId")
    if not isinstance(query_id, str) or not query_id.strip():
        return None
    normalized = query_id.strip()
    return normalized[:128]


def apply_speech_replacement(
    body: bytes,
    *,
    text: str,
    audio_url: str,
    pre_animation: str | None = None,
    post_animation: str | None = None,
) -> bytes:
    payload = _decode_response(body)
    query_result = payload.get("queryResult")
    if not isinstance(query_result, dict):
        raise ReplacementError("queryResult is missing")

    behavior = query_result.get("behavior_paras")
    if not isinstance(behavior, dict):
        behavior = {}
        query_result["behavior_paras"] = behavior

    normalized_text = text.strip()
    if not normalized_text:
        raise ReplacementError("replacement text is empty")
    if not audio_url.startswith(("http://", "https://")):
        raise ReplacementError("TTS URL is invalid")

    query_result["rec_behavior"] = "speak"
    behavior["txt"] = normalized_text
    behavior["url"] = audio_url
    behavior["listen"] = 0
    behavior.pop("animation_name", None)

    if pre_animation:
        behavior["pre_animation"] = pre_animation
    else:
        behavior.pop("pre_animation", None)
    if post_animation:
        behavior["post_animation"] = post_animation
    else:
        behavior.pop("post_animation", None)

    return json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def _decode_response(body: bytes) -> dict[str, Any]:
    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ReplacementError("response is not UTF-8 JSON") from exc
    if not isinstance(payload, dict):
        raise ReplacementError("response root is not an object")
    return payload
