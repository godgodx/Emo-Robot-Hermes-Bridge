"""Narrow HTTPS observation gateway for api.living.ai traffic."""

from __future__ import annotations

import argparse
import asyncio
import json
import secrets
import signal
import ssl
import time
from collections import OrderedDict
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urlencode, urlsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from aiohttp import ClientError, ClientSession, ClientTimeout, web

from .acknowledgements import (
    ACKNOWLEDGEMENTS,
    NETWORK_ACK_ANIMATIONS,
    RESET_ACKNOWLEDGEMENT,
    AudioCacheError,
    AudioClip,
    AudioLibrary,
    detect_audio_format,
    expected_clips,
    load_library,
    write_library_atomic,
)
from .certificate import create_test_certificate
from .hermes import (
    EmoReply,
    HermesBridge,
    HermesError,
    is_reset_command,
    load_active_session,
    request_streaming_final_response,
    rotate_active_session,
)
from .protocol import ANIMATIONS
from .replacement import (
    ReplacementError,
    apply_speech_replacement,
    decode_transport_body,
    response_language,
    response_query_id,
    response_query_text,
)


DEFAULT_CERT = Path("certs/api.living.ai.crt")
DEFAULT_KEY = Path("certs/api.living.ai.key")
UPSTREAM = "https://api.living.ai"
LOCAL_AUDIO_BASE = "http://api.living.ai/_emo_agent/audio"
LOCAL_TTS_BASE = "http://api.living.ai/_emo_agent/tts"
MAX_EPHEMERAL_TTS_CLIPS = 8
HOP_BY_HOP_HEADERS = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "proxy-connection",
    "te",
    "trailer",
    "transfer-encoding",
    "upgrade",
}
FIRMWARE_INCOMPATIBLE_REQUEST_HEADERS = {"accept-encoding"}


@dataclass
class OneShotTest:
    text: str
    pre_animation: str | None
    post_animation: str | None
    remaining: int = 1


def forwarded_headers(items: Iterable[tuple[str, str]]) -> dict[str, str]:
    """Forward values, including auth, without ever logging them."""
    return {
        name: value
        for name, value in items
        if name.lower()
        not in HOP_BY_HOP_HEADERS
        | FIRMWARE_INCOMPATIBLE_REQUEST_HEADERS
        | {"host", "content-length"}
    }


def response_headers(items: Iterable[tuple[str, str]]) -> dict[str, str]:
    return {
        name: value
        for name, value in items
        if name.lower() not in HOP_BY_HOP_HEADERS | {"content-length"}
    }


def safe_event(**values: object) -> str:
    """Serialize only explicitly supplied, non-secret observation metadata."""
    return json.dumps(values, ensure_ascii=False, separators=(",", ":"))


def safe_request_path(path: str) -> str:
    """Redact identifiers and unguessable tokens embedded in request paths."""
    for prefix in ("/token/", "/_emo_agent/audio/", "/_emo_agent/tts/"):
        if path.startswith(prefix):
            return prefix + "<redacted>"
    return path


def time_payload(timezone_name: str, epoch: int | None = None) -> bytes:
    """Return the compact clock envelope expected by EMO's firmware."""
    timestamp = int(time.time()) if epoch is None else int(epoch)
    try:
        timezone = ZoneInfo(timezone_name) if timezone_name else None
    except ZoneInfoNotFoundError:
        timezone = None
    current = (
        datetime.fromtimestamp(timestamp).astimezone()
        if timezone is None
        else datetime.fromtimestamp(timestamp, tz=timezone)
    )
    offset = current.utcoffset()
    offset_seconds = int(offset.total_seconds()) if offset is not None else 0
    return json.dumps(
        {"time": timestamp, "offset": offset_seconds},
        separators=(",", ":"),
    ).encode("utf-8")


async def local_time(request: web.Request) -> web.Response:
    body = time_payload(request.query.get("tz", ""))
    print(
        safe_event(
            event="relay",
            method=request.method,
            path=request.path,
            status=200,
            request_bytes=0,
            response_bytes=len(body),
            source="local-clock",
        ),
        flush=True,
    )
    return web.Response(body=body, content_type="application/json", charset="utf-8")


def network_animation(animation: str | None) -> str | None:
    if animation is None:
        return None
    values = ANIMATIONS.get(animation)
    return values[0] if values else None


def track_task(app: web.Application, task: asyncio.Task[object]) -> None:
    tasks: set[asyncio.Task[object]] = app["background_tasks"]
    tasks.add(task)
    task.add_done_callback(tasks.discard)


def track_hermes_task(app: web.Application, task: asyncio.Task[EmoReply]) -> None:
    tasks: set[asyncio.Task[EmoReply]] = app["hermes_tasks"]
    tasks.add(task)
    task.add_done_callback(tasks.discard)
    track_task(app, task)


async def cancel_hermes_turns(app: web.Application) -> int:
    tasks = [task for task in app["hermes_tasks"] if not task.done()]
    for task in tasks:
        task.cancel()
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)
    return len(tasks)


async def run_hermes_turn(
    app: web.Application,
    *,
    transcript: str,
    query_id: str | None,
    tool_started: asyncio.Event,
) -> EmoReply:
    async with app["hermes_lock"]:
        bridge = replace(
            app["hermes_bridge"],
            session_id=app["runtime"]["active_session_id"],
        )
        return await request_streaming_final_response(
            app["hermes_session"],
            bridge,
            transcript=transcript,
            idempotency_key=query_id,
            tool_started=tool_started,
        )


async def deliver_deferred_reply(
    app: web.Application,
    hermes_task: asyncio.Task[EmoReply],
    *,
    deferred_at: float,
) -> None:
    try:
        reply = await hermes_task
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        print(safe_event(event="hermes_deferred_failed", error=type(exc).__name__), flush=True)
        return

    async with app["ble_lock"]:
        started = time.monotonic()
        last_error = ""
        for attempt in range(1, app["ble_delivery_attempts"] + 1):
            try:
                from .ble import TheaterSession, select_emo

                device = await select_emo(None, timeout=app["ble_discovery_timeout"])
                async with TheaterSession(device) as theater:
                    await theater.speak(reply.speech)
                    if reply.animation is not None:
                        await theater.animate(reply.animation)
                print(
                    safe_event(
                        event="hermes_ble_delivery",
                        attempt=attempt,
                        animation=reply.animation or "",
                        answer_chars=len(reply.speech),
                        deferred_ms=round((time.monotonic() - deferred_at) * 1000),
                        delivery_ms=round((time.monotonic() - started) * 1000),
                    ),
                    flush=True,
                )
                return
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                last_error = type(exc).__name__
                if attempt < app["ble_delivery_attempts"]:
                    await asyncio.sleep(2)
    print(
        safe_event(
            event="hermes_ble_delivery_failed",
            attempts=app["ble_delivery_attempts"],
            error=last_error,
        ),
        flush=True,
    )


def living_ai_audio_url(url: str) -> bool:
    parsed = urlsplit(url)
    hostname = (parsed.hostname or "").casefold()
    return (
        parsed.scheme in {"http", "https"}
        and parsed.username is None
        and parsed.password is None
        and (hostname == "living.ai" or hostname.endswith(".living.ai"))
    )


def tts_audio_endpoint(response_body: bytes, content_encoding: str | None) -> tuple[str, str, str]:
    decoded = decode_transport_body(response_body, content_encoding)
    payload = json.loads(decoded.decode("utf-8"))
    url = payload.get("url") if isinstance(payload, dict) else None
    if not isinstance(url, str):
        raise ValueError("TTS response URL is absent")
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("TTS response URL is invalid")
    parts = [part for part in parsed.path.split("/") if part]
    path_prefix = "/" + "/".join(parts[:2])
    return parsed.scheme, parsed.hostname, path_prefix


async def download_tts_audio(session: ClientSession, url: str) -> tuple[str, bytes]:
    if not living_ai_audio_url(url):
        raise AudioCacheError("Living.AI returned an untrusted audio URL")
    async with session.get(
        url,
        headers={"Accept-Encoding": "identity"},
        allow_redirects=True,
        max_redirects=3,
    ) as response:
        if response.status != 200 or not living_ai_audio_url(str(response.url)):
            raise AudioCacheError("Living.AI acknowledgement download failed")
        data = await response.read()
        content_type = response.headers.get("Content-Type", "").split(";", 1)[0].strip()
    return content_type, data


async def localize_tts_response(
    app: web.Application,
    response_body: bytes,
    content_encoding: str | None,
) -> tuple[bytes, str, int]:
    decoded = decode_transport_body(response_body, content_encoding)
    payload = json.loads(decoded.decode("utf-8"))
    audio_url = payload.get("url") if isinstance(payload, dict) else None
    if not isinstance(audio_url, str):
        raise ValueError("TTS response URL is absent")
    upstream_type, data = await download_tts_audio(app["upstream_session"], audio_url)
    _suffix, content_type = detect_audio_format(data, upstream_type)
    token = secrets.token_urlsafe(18)
    cache: OrderedDict[str, tuple[str, bytes]] = app["runtime"]["tts_audio_cache"]
    cache[token] = (content_type, data)
    cache.move_to_end(token)
    while len(cache) > MAX_EPHEMERAL_TTS_CLIPS:
        cache.popitem(last=False)
    payload["url"] = f"{LOCAL_TTS_BASE}/{token}"
    localized = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return localized, content_type, len(data)


async def prime_audio_library(
    app: web.Application,
    *,
    language: str,
    auth_headers: tuple[tuple[str, str], ...],
) -> None:
    started = time.monotonic()
    semaphore = asyncio.Semaphore(3)

    async def generate(clip_id: str, text: str) -> tuple[str, str, str, bytes]:
        async with semaphore:
            audio_url = await request_tts(
                app["upstream_session"],
                text=text,
                language=language,
                incoming_headers=auth_headers,
            )
            content_type, data = await download_tts_audio(app["upstream_session"], audio_url)
            return clip_id, text, content_type, data

    try:
        generated = await asyncio.gather(
            *(generate(clip_id, text) for clip_id, text in expected_clips())
        )
        library = await asyncio.to_thread(
            write_library_atomic,
            app["ack_audio_root"],
            generated,
        )
        app["runtime"]["audio_library"] = library
        print(
            safe_event(
                event="ack_audio_primed",
                clips=len(library.by_id),
                duration_ms=round((time.monotonic() - started) * 1000),
            ),
            flush=True,
        )
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        print(safe_event(event="ack_audio_prime_failed", error=type(exc).__name__), flush=True)
    finally:
        app["runtime"]["audio_prime_task"] = None


def start_audio_priming(
    app: web.Application,
    *,
    language: str,
    incoming_headers: Iterable[tuple[str, str]],
) -> None:
    runtime: dict[str, Any] = app["runtime"]
    if runtime.get("audio_library") is not None:
        return
    current = runtime.get("audio_prime_task")
    if current is not None and not current.done():
        return
    auth_headers = tuple(
        (name, value)
        for name, value in incoming_headers
        if name.lower() in {"authorization", "secret"}
    )
    task = asyncio.create_task(
        prime_audio_library(app, language=language, auth_headers=auth_headers)
    )
    runtime["audio_prime_task"] = task
    track_task(app, task)


async def acknowledgement_audio(request: web.Request) -> web.Response:
    library: AudioLibrary | None = request.app["runtime"].get("audio_library")
    clip: AudioClip | None = library.by_id.get(request.match_info["clip_id"]) if library else None
    if clip is None:
        raise web.HTTPNotFound()
    print(
        safe_event(
            event="ack_audio_served",
            clip_id=clip.clip_id,
            response_bytes=len(clip.data),
        ),
        flush=True,
    )
    return web.Response(
        body=clip.data,
        content_type=clip.content_type,
        headers={"Cache-Control": "public, max-age=31536000, immutable"},
    )


async def ephemeral_tts_audio(request: web.Request) -> web.Response:
    cache: OrderedDict[str, tuple[str, bytes]] = request.app["runtime"]["tts_audio_cache"]
    audio = cache.get(request.match_info["token"])
    if audio is None:
        raise web.HTTPNotFound()
    content_type, data = audio
    print(safe_event(event="tts_audio_served", response_bytes=len(data)), flush=True)
    return web.Response(
        body=data,
        content_type=content_type,
        headers={"Cache-Control": "private, max-age=300"},
    )


async def health(request: web.Request) -> web.Response:
    test: OneShotTest | None = request.app.get("one_shot_test")
    bridge: HermesBridge | None = request.app.get("hermes_bridge")
    runtime: dict[str, Any] = request.app["runtime"]
    library: AudioLibrary | None = runtime.get("audio_library")
    return web.json_response(
        {
            "status": "ok",
            "mode": "one-shot-test" if test else "hermes" if bridge else "observe",
            "remaining": test.remaining if test else 0,
            "upstream": "api.living.ai",
            "ack_audio_ready": library is not None,
            "ack_audio_priming": runtime.get("audio_prime_task") is not None,
            "ack_audio_count": len(library.by_id) if library is not None else 0,
            "tts_audio_cached": len(runtime["tts_audio_cache"]),
            "session_state_persistent": request.app.get("session_state_path") is not None,
            "pending_deliveries": len(request.app.get("background_tasks", ())),
        }
    )


async def relay(request: web.Request) -> web.Response:
    body = await request.read()
    upstream_url = UPSTREAM + request.rel_url.raw_path
    if request.rel_url.raw_query_string:
        upstream_url += "?" + request.rel_url.raw_query_string

    session: ClientSession = request.app["upstream_session"]
    try:
        async with session.request(
            request.method,
            upstream_url,
            data=body,
            headers=forwarded_headers(request.headers.items()),
            allow_redirects=False,
        ) as upstream_response:
            response_body = await upstream_response.read()
            status = upstream_response.status
            headers = response_headers(upstream_response.headers.items())
    except (TimeoutError, OSError) as exc:
        print(
            safe_event(
                event="upstream_error",
                method=request.method,
                path=safe_request_path(request.path),
                request_bytes=len(body),
                error=type(exc).__name__,
            ),
            flush=True,
        )
        return web.json_response({"error": "upstream unavailable"}, status=502)

    if status == 200 and request.path == "/emo/speech/tts":
        try:
            scheme, hostname, path_prefix = tts_audio_endpoint(
                response_body,
                headers.get("Content-Encoding"),
            )
            print(
                safe_event(
                    event="tts_audio_endpoint",
                    scheme=scheme,
                    hostname=hostname,
                    path_prefix=path_prefix,
                ),
                flush=True,
            )
            response_body, content_type, audio_bytes = await localize_tts_response(
                request.app,
                response_body,
                headers.get("Content-Encoding"),
            )
            headers = {
                name: value
                for name, value in headers.items()
                if name.lower() not in {"content-encoding", "content-length"}
            }
            headers["Content-Type"] = "application/json; charset=utf-8"
            print(
                safe_event(
                    event="tts_audio_localized",
                    content_type=content_type,
                    audio_bytes=audio_bytes,
                ),
                flush=True,
            )
        except (
            AudioCacheError,
            ClientError,
            TimeoutError,
            OSError,
            UnicodeDecodeError,
            json.JSONDecodeError,
            ReplacementError,
            ValueError,
        ) as exc:
            print(safe_event(event="tts_audio_localize_failed", error=type(exc).__name__), flush=True)

    test: OneShotTest | None = request.app.get("one_shot_test")
    bridge: HermesBridge | None = request.app.get("hermes_bridge")
    is_voice_response = status == 200 and request.path == "/emo/voice/detectintent"
    if test is not None and test.remaining > 0 and is_voice_response:
        try:
            decoded_body = decode_transport_body(
                response_body,
                headers.get("Content-Encoding"),
            )
            language = response_language(decoded_body)
            audio_url = await request_tts(
                session,
                text=test.text,
                language=language,
                incoming_headers=request.headers.items(),
            )
            response_body = apply_speech_replacement(
                decoded_body,
                text=test.text,
                audio_url=audio_url,
                pre_animation=test.pre_animation,
                post_animation=test.post_animation,
            )
            headers = {
                name: value
                for name, value in headers.items()
                if name.lower() not in {"content-encoding", "content-length"}
            }
            headers["Content-Type"] = "application/json; charset=utf-8"
            test.remaining -= 1
            print(
                safe_event(
                    event="one_shot_replacement",
                    pre_animation=test.pre_animation or "",
                    post_animation=test.post_animation or "",
                    remaining=test.remaining,
                ),
                flush=True,
            )
        except (ClientError, TimeoutError, OSError, ReplacementError, ValueError) as exc:
            print(
                safe_event(
                    event="replacement_skipped",
                    error=type(exc).__name__,
                    reason=str(exc),
                    content_encoding=headers.get("Content-Encoding", ""),
                    content_type=headers.get("Content-Type", ""),
                ),
                flush=True,
            )
    elif bridge is not None and is_voice_response:
        started_at = time.monotonic()
        hermes_task: asyncio.Task[EmoReply] | None = None
        try:
            decoded_body = decode_transport_body(
                response_body,
                headers.get("Content-Encoding"),
            )
            transcript = response_query_text(decoded_body)
            query_id = response_query_id(decoded_body)
            reset_requested = is_reset_command(transcript)
            language = response_language(decoded_body)
            library: AudioLibrary | None = request.app["runtime"].get("audio_library")
            cancelled_turns = 0
            if reset_requested:
                cancelled_turns = await cancel_hermes_turns(request.app)
                async with request.app["hermes_lock"]:
                    active_session_id = rotate_active_session(
                        request.app.get("session_state_path"),
                        bridge.session_id,
                    )
                    request.app["runtime"]["active_session_id"] = active_session_id
                clip = library.reset if library is not None else None
                acknowledgement_text = RESET_ACKNOWLEDGEMENT
            else:
                tool_started = asyncio.Event()
                hermes_task = asyncio.create_task(
                    run_hermes_turn(
                        request.app,
                        transcript=transcript,
                        query_id=query_id,
                        tool_started=tool_started,
                    )
                )
                track_hermes_task(request.app, hermes_task)
                deferred_task = asyncio.create_task(
                    deliver_deferred_reply(
                        request.app,
                        hermes_task,
                        deferred_at=time.monotonic(),
                    )
                )
                track_task(request.app, deferred_task)
                clip = secrets.choice(library.acknowledgements) if library is not None else None
                acknowledgement_text = clip.text if clip is not None else secrets.choice(ACKNOWLEDGEMENTS)

            if clip is None:
                audio_url = await request_tts(
                    session,
                    text=acknowledgement_text,
                    language=language,
                    incoming_headers=request.headers.items(),
                )
                start_audio_priming(
                    request.app,
                    language=language,
                    incoming_headers=request.headers.items(),
                )
                audio_source = "living-ai-prime"
            else:
                audio_url = f"{LOCAL_AUDIO_BASE}/{clip.clip_id}"
                audio_source = "local-cache"
            animation = secrets.choice(NETWORK_ACK_ANIMATIONS)
            response_body = apply_speech_replacement(
                decoded_body,
                text=acknowledgement_text,
                audio_url=audio_url,
                post_animation=animation,
            )
            headers = {
                name: value
                for name, value in headers.items()
                if name.lower() not in {"content-encoding", "content-length"}
            }
            headers["Content-Type"] = "application/json; charset=utf-8"
            print(
                safe_event(
                    event="hermes_session_rotated" if reset_requested else "hermes_acknowledgement",
                    audio_source=audio_source,
                    animation=animation,
                    cancelled_turns=cancelled_turns,
                    duration_ms=round((time.monotonic() - started_at) * 1000),
                ),
                flush=True,
            )
        except asyncio.CancelledError:
            raise
        except (ClientError, TimeoutError, OSError, HermesError, ReplacementError, ValueError) as exc:
            if hermes_task is not None and not hermes_task.done():
                hermes_task.cancel()
            print(
                safe_event(
                    event="hermes_fallback",
                    error=type(exc).__name__,
                    reason=str(exc),
                ),
                flush=True,
            )

    print(
        safe_event(
            event="relay",
            method=request.method,
            path=safe_request_path(request.path),
            status=status,
            request_bytes=len(body),
            response_bytes=len(response_body),
        ),
        flush=True,
    )
    return web.Response(body=response_body, status=status, headers=headers)


async def request_tts(
    session: ClientSession,
    *,
    text: str,
    language: str,
    incoming_headers: Iterable[tuple[str, str]],
) -> str:
    auth_headers = {
        name: value
        for name, value in incoming_headers
        if name.lower() in {"authorization", "secret"}
    }
    auth_headers["Accept-Encoding"] = "identity"
    query = urlencode({"q": text, "l": language})
    async with session.get(
        f"{UPSTREAM}/emo/speech/tts?{query}",
        headers=auth_headers,
        allow_redirects=False,
    ) as response:
        if response.status != 200:
            raise ValueError("TTS HTTP status is not 200")
        raw = await response.read()
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("TTS response is not JSON") from exc
    if not isinstance(payload, dict) or payload.get("code") != 200:
        raise ValueError("TTS response code is not 200")
    audio_url = payload.get("url")
    if not isinstance(audio_url, str) or not audio_url.startswith(("http://", "https://")):
        raise ValueError("TTS response URL is invalid")
    return audio_url


async def create_app(
    one_shot_test: OneShotTest | None = None,
    hermes_bridge: HermesBridge | None = None,
    *,
    hermes_timeout: float = 180,
    session_state_path: Path | None = None,
    ack_audio_root: Path = Path("ack-audio"),
    ble_delivery_attempts: int = 3,
    ble_discovery_timeout: float = 8,
) -> web.Application:
    app = web.Application(client_max_size=16 * 1024 * 1024)
    app["background_tasks"] = set()
    app["runtime"] = {
        "audio_library": None,
        "audio_prime_task": None,
        "tts_audio_cache": OrderedDict(),
    }
    timeout = ClientTimeout(total=45, connect=10)
    # aiohttp otherwise advertises gzip even when EMO did not. Living.AI then
    # compresses small JSON responses such as /token/*, but EMO's firmware
    # expects the uncompressed JSON produced by the original Go proxy.
    app["upstream_session"] = ClientSession(
        timeout=timeout,
        auto_decompress=False,
        skip_auto_headers={"Accept-Encoding"},
    )
    if one_shot_test is not None:
        app["one_shot_test"] = one_shot_test
    if hermes_bridge is not None:
        app["hermes_bridge"] = hermes_bridge
        app["hermes_lock"] = asyncio.Lock()
        app["hermes_tasks"] = set()
        app["ble_lock"] = asyncio.Lock()
        app["session_state_path"] = session_state_path
        app["runtime"]["active_session_id"] = (
            load_active_session(session_state_path, hermes_bridge.session_id)
            if session_state_path is not None
            else hermes_bridge.session_id
        )
        app["ble_delivery_attempts"] = ble_delivery_attempts
        app["ble_discovery_timeout"] = ble_discovery_timeout
        app["ack_audio_root"] = ack_audio_root
        app["runtime"]["audio_library"] = load_library(ack_audio_root)
        app["hermes_session"] = ClientSession(
            timeout=ClientTimeout(total=hermes_timeout, connect=10),
        )
    app.router.add_get("/_emo_agent/health", health)
    app.router.add_get("/_emo_agent/audio/{clip_id}", acknowledgement_audio)
    app.router.add_get("/_emo_agent/tts/{token}", ephemeral_tts_audio)
    app.router.add_get("/time", local_time)
    app.router.add_route("*", "/{tail:.*}", relay)

    async def close_session(application: web.Application) -> None:
        tasks = list(application["background_tasks"])
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        await application["upstream_session"].close()
        hermes_session: ClientSession | None = application.get("hermes_session")
        if hermes_session is not None:
            await hermes_session.close()

    app.on_cleanup.append(close_session)
    return app


def tls_context(cert_path: Path, key_path: Path) -> ssl.SSLContext:
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.load_cert_chain(cert_path, key_path)
    return context


async def serve_sites(
    app: web.Application,
    *,
    host: str,
    https_port: int,
    audio_port: int,
    ssl_context: ssl.SSLContext,
) -> None:
    """Serve intercepted API traffic over TLS and cached audio over plain HTTP."""
    runner = web.AppRunner(app)
    await runner.setup()
    try:
        await web.TCPSite(runner, host, https_port, ssl_context=ssl_context).start()
        if audio_port:
            await web.TCPSite(runner, host, audio_port).start()
        stopped = asyncio.Event()
        loop = asyncio.get_running_loop()
        for interrupt in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(interrupt, stopped.set)
            except NotImplementedError:
                pass
        await stopped.wait()
    finally:
        await runner.cleanup()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Relais HTTPS local pour le test EMO")
    subparsers = parser.add_subparsers(dest="command", required=True)

    init = subparsers.add_parser("init-cert", help="générer un certificat local jetable")
    init.add_argument("--cert", type=Path, default=DEFAULT_CERT)
    init.add_argument("--key", type=Path, default=DEFAULT_KEY)

    serve = subparsers.add_parser("serve", help="démarrer le relais en mode observation")
    serve.add_argument("--host", default="0.0.0.0")
    serve.add_argument("--port", type=int, default=8443)
    serve.add_argument("--audio-port", type=int, default=0)
    serve.add_argument("--cert", type=Path, default=DEFAULT_CERT)
    serve.add_argument("--key", type=Path, default=DEFAULT_KEY)
    serve.add_argument("--test-text", help="remplacer une seule prochaine réponse vocale")
    serve.add_argument("--test-pre-animation")
    serve.add_argument("--test-post-animation")
    serve.add_argument("--hermes-api-url", help="activer Hermes via son API locale")
    serve.add_argument("--hermes-session-id", default="emo-robot-main")
    serve.add_argument("--hermes-session-state-file", type=Path)
    serve.add_argument("--hermes-model", default="hermes-agent")
    serve.add_argument("--hermes-timeout", type=float, default=180)
    serve.add_argument("--ack-audio-dir", type=Path, default=Path("ack-audio"))
    return parser


def main() -> None:
    args = build_parser().parse_args()
    if args.command == "init-cert":
        create_test_certificate(args.cert, args.key)
        print(f"Certificat de test généré dans {args.cert.parent}.")
        return

    if not args.cert.is_file() or not args.key.is_file():
        raise SystemExit("Certificat absent. Lance d'abord: python -m emo_agent.gateway init-cert")
    test = None
    if args.test_text:
        test = OneShotTest(
            text=args.test_text,
            pre_animation=args.test_pre_animation,
            post_animation=args.test_post_animation,
        )
    if test is not None and args.hermes_api_url:
        raise SystemExit("Les modes one-shot et Hermes sont mutuellement exclusifs.")
    if args.hermes_timeout <= 0:
        raise SystemExit("Le délai Hermes est invalide.")
    if not 0 <= args.audio_port <= 65535:
        raise SystemExit("Le port audio HTTP est invalide.")
    bridge = None
    if args.hermes_api_url:
        import os

        api_key = os.getenv("HERMES_API_KEY", "").strip()
        if len(api_key) < 16:
            raise SystemExit("HERMES_API_KEY est absent ou trop court.")
        bridge = HermesBridge(
            api_url=args.hermes_api_url,
            api_key=api_key,
            session_id=args.hermes_session_id,
            model=args.hermes_model,
        )
    mode = "test one-shot" if test else "Hermes" if bridge else "observation"
    audio_listener = f" et HTTP :{args.audio_port}" if args.audio_port else ""
    print(f"Relais {mode} HTTPS sur {args.host}:{args.port}{audio_listener} -> {UPSTREAM}")

    async def run_server() -> None:
        app = await create_app(
            test,
            bridge,
            hermes_timeout=args.hermes_timeout,
            session_state_path=args.hermes_session_state_file,
            ack_audio_root=args.ack_audio_dir,
        )
        await serve_sites(
            app,
            host=args.host,
            https_port=args.port,
            audio_port=args.audio_port,
            ssl_context=tls_context(args.cert, args.key),
        )

    try:
        asyncio.run(run_server())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
