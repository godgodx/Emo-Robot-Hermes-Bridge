import json
import gzip
import asyncio
import tempfile
import unittest
from collections import OrderedDict
from pathlib import Path

from emo_agent.acknowledgements import (
    ACKNOWLEDGEMENTS,
    AudioCacheError,
    cache_version,
    expected_clips,
    load_library,
    write_library_atomic,
)
from emo_agent.ble import EmoBleError, TheaterSession
from emo_agent.protocol import ResponseAssembler, frame_json, theater_animation, theater_speech
from emo_agent.gateway import (
    LOCAL_AUDIO_BASE,
    LOCAL_TTS_BASE,
    forwarded_headers,
    living_ai_audio_url,
    localize_tts_response,
    safe_request_path,
    safe_event,
    time_payload,
    tts_audio_endpoint,
)
from emo_agent.hermes import (
    EmoReply,
    HermesBridge,
    HermesError,
    chat_payload,
    final_response,
    is_reset_command,
    load_active_session,
    parse_emo_reply,
    rotate_active_session,
)
from emo_agent.replacement import (
    ReplacementError,
    apply_speech_replacement,
    decode_transport_body,
    response_language,
    response_query_id,
    response_query_text,
)


class ProtocolTests(unittest.TestCase):
    def test_frame_uses_bbaa_and_little_endian_length(self) -> None:
        packet = frame_json({"type": "x", "data": {}})
        payload = packet[4:]
        self.assertEqual(packet[:2], b"\xBB\xAA")
        self.assertEqual(packet[2] | (packet[3] << 8), len(payload))
        self.assertEqual(json.loads(payload), {"type": "x", "data": {}})

    def test_response_assembler_handles_fragmented_json(self) -> None:
        packet = frame_json({"type": "theater_rsp", "data": {"result": 1}})
        assembler = ResponseAssembler()
        self.assertIsNone(assembler.feed(packet[:20]))
        self.assertEqual(
            assembler.feed(packet[20:]),
            {"type": "theater_rsp", "data": {"result": 1}},
        )

    def test_response_assembler_ignores_ddcc_control_packet(self) -> None:
        self.assertIsNone(ResponseAssembler().feed(b"\xDD\xCC\x00\x00"))

    def test_animation_is_allowlisted(self) -> None:
        packet = theater_animation("hi")
        body = json.loads(packet[4:])
        self.assertEqual(body["data"], {"op": "play", "animations": ["Hi"]})
        with self.assertRaises(ValueError):
            theater_animation("not-real")

    def test_speech_rejects_empty_text(self) -> None:
        with self.assertRaises(ValueError):
            theater_speech("   ")

    def test_gateway_forwards_auth_without_logging_it(self) -> None:
        headers = forwarded_headers(
            [
                ("Host", "api.living.ai"),
                ("Authorization", "private"),
                ("Secret", "private-too"),
                ("Accept-Encoding", "gzip"),
            ]
        )
        self.assertEqual(headers["Authorization"], "private")
        self.assertEqual(headers["Secret"], "private-too")
        self.assertNotIn("Host", headers)
        self.assertNotIn("Accept-Encoding", headers)
        event = safe_event(event="relay", method="POST", path="/emo/voice/detectintent")
        self.assertNotIn("private", event)

    def test_gateway_redacts_device_and_audio_tokens_in_logged_paths(self) -> None:
        self.assertEqual(safe_request_path("/token/device-id"), "/token/<redacted>")
        self.assertEqual(
            safe_request_path("/_emo_agent/tts/unguessable"),
            "/_emo_agent/tts/<redacted>",
        )
        self.assertEqual(safe_request_path("/emo/voice/detectintent"), "/emo/voice/detectintent")

    def test_gateway_builds_compact_local_time_with_requested_offset(self) -> None:
        payload = time_payload("America/Halifax", epoch=1_767_225_600)
        self.assertEqual(
            json.loads(payload),
            {"time": 1_767_225_600, "offset": -14_400},
        )
        self.assertNotIn(b" ", payload)

    def test_cached_audio_uses_firmware_compatible_plain_http(self) -> None:
        self.assertEqual(LOCAL_AUDIO_BASE, "http://api.living.ai/_emo_agent/audio")

    def test_tts_endpoint_metadata_drops_the_private_download_token(self) -> None:
        endpoint = tts_audio_endpoint(
            b'{"code":200,"url":"http://eu-api.living.ai/tts/dl/private-token"}',
            None,
        )
        self.assertEqual(endpoint, ("http", "eu-api.living.ai", "/tts/dl"))
        self.assertNotIn("private-token", repr(endpoint))

    def test_speech_replacement_preserves_envelope_and_sets_pre_animation(self) -> None:
        original = json.dumps(
            {
                "queryId": "id",
                "queryResult": {
                    "queryText": "private question",
                    "intent": {"name": "chatgpt_speak", "confidence": 1},
                    "rec_behavior": "speak",
                    "behavior_paras": {"txt": "old", "url": "https://old", "post_animation": "Old"},
                },
                "languageCode": "fr",
            }
        ).encode()
        replaced = json.loads(
            apply_speech_replacement(
                original,
                text="Oui, je suis heureux.",
                audio_url="https://audio.test/answer.wav",
                pre_animation="Hi",
            )
        )
        self.assertEqual(replaced["queryId"], "id")
        self.assertEqual(replaced["queryResult"]["rec_behavior"], "speak")
        behavior = replaced["queryResult"]["behavior_paras"]
        self.assertEqual(behavior["pre_animation"], "Hi")
        self.assertNotIn("post_animation", behavior)
        self.assertEqual(response_language(original), "fr")
        self.assertEqual(response_query_text(original), "private question")
        self.assertEqual(response_query_id(original), "id")

    def test_speech_replacement_rejects_non_json(self) -> None:
        with self.assertRaises(ReplacementError):
            apply_speech_replacement(b"not-json", text="x", audio_url="https://audio.test/x")

    def test_transport_body_decodes_gzip_json(self) -> None:
        body = b'{"languageCode":"fr"}'
        self.assertEqual(decode_transport_body(gzip.compress(body), "gzip"), body)

    def test_hermes_payload_is_non_streaming_and_final_only(self) -> None:
        payload = chat_payload("  Quelle heure est-il ?  ", model="hermes-agent")
        self.assertFalse(payload["stream"])
        self.assertEqual(payload["messages"][-1]["content"], "Quelle heure est-il ?")
        self.assertIn("uniquement ta réponse finale", payload["messages"][0]["content"])

    def test_hermes_streaming_payload_can_report_tool_progress(self) -> None:
        payload = chat_payload("Fais une recherche", model="hermes-agent", stream=True)
        self.assertTrue(payload["stream"])
        self.assertIn("speech et animation", payload["messages"][0]["content"])

    def test_hermes_emo_reply_parses_allowlisted_animation(self) -> None:
        reply = parse_emo_reply('{"speech":"Oui, avec plaisir !","animation":"excited"}')
        self.assertEqual(reply, EmoReply("Oui, avec plaisir !", "excited"))
        self.assertEqual(parse_emo_reply("Réponse compatible."), EmoReply("Réponse compatible."))

        with self.assertRaises(HermesError):
            parse_emo_reply('{"speech":"Test","animation":"shutdown"}')

    def test_hermes_emo_reply_fits_ble_limit(self) -> None:
        with self.assertRaises(HermesError):
            parse_emo_reply('{"speech":"' + ("x" * 501) + '","animation":"none"}')

    def test_hermes_final_response_requires_complete_stop(self) -> None:
        completed = json.dumps(
            {
                "choices": [
                    {
                        "message": {"role": "assistant", "content": " Réponse finale. "},
                        "finish_reason": "stop",
                    }
                ]
            }
        ).encode()
        self.assertEqual(final_response(completed), "Réponse finale.")

        partial = json.dumps(
            {
                "choices": [
                    {"message": {"content": "partiel"}, "finish_reason": "length"}
                ],
                "hermes": {"completed": False, "partial": True, "failed": False},
            }
        ).encode()
        with self.assertRaises(HermesError):
            final_response(partial)

    def test_hermes_bridge_repr_redacts_key(self) -> None:
        bridge = HermesBridge(api_url="http://127.0.0.1:8642", api_key="private-secret-key")
        self.assertNotIn("private-secret-key", repr(bridge))

    def test_hermes_reset_command_is_exact_and_accent_insensitive(self) -> None:
        self.assertTrue(is_reset_command("Nouvelle conversation !"))
        self.assertTrue(is_reset_command("Démarre une nouvelle discussion."))
        self.assertTrue(is_reset_command("/new"))
        self.assertFalse(is_reset_command("Explique-moi ce qu'est une nouvelle conversation"))
        self.assertFalse(is_reset_command("nouvelle conversation demain"))

    def test_session_rotation_preserves_previous_id_and_survives_restart(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            state_path = Path(temporary) / "active-session"
            previous = load_active_session(state_path, "emo-robot-main")
            current = rotate_active_session(state_path, "emo-robot-main")

            self.assertEqual(previous, "emo-robot-main")
            self.assertNotEqual(current, previous)
            self.assertEqual(load_active_session(state_path, "unused-default"), current)
            self.assertEqual(state_path.read_text(encoding="utf-8").strip(), current)


class TheaterCompletionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.session = object.__new__(TheaterSession)
        self.session._messages = asyncio.Queue()
        self.session._expired = False

        async def no_write(_payload: bytes) -> None:
            return None

        self.session._write = no_write

    async def test_theater_command_waits_for_terminal_result(self) -> None:
        await self.session._messages.put({"type": "theater_rsp", "data": {"result": 1}})
        await self.session._messages.put({"type": "theater_rsp", "data": {"result": 2}})
        result = await self.session._command_until_complete(b"test", timeout=1)
        self.assertEqual(result, 2)

    async def test_theater_command_rejects_busy_result(self) -> None:
        await self.session._messages.put({"type": "theater_rsp", "data": {"result": 0}})
        with self.assertRaises(EmoBleError):
            await self.session._command_until_complete(b"test", timeout=1)


class TtsLocalizationTests(unittest.IsolatedAsyncioTestCase):
    async def test_final_tts_audio_is_kept_in_memory_and_rewritten_locally(self) -> None:
        wave = b"RIFF" + (4).to_bytes(4, "little") + b"WAVE" + b"data"

        class FakeResponse:
            status = 200
            url = "http://us-api-3.living.ai/tts/dl/private-token"
            headers = {"Content-Type": "audio/wav"}

            async def __aenter__(self):
                return self

            async def __aexit__(self, *_args):
                return None

            async def read(self) -> bytes:
                return wave

        class FakeSession:
            def get(self, *_args, **_kwargs):
                return FakeResponse()

        app = {
            "upstream_session": FakeSession(),
            "runtime": {"tts_audio_cache": OrderedDict()},
        }
        localized, content_type, audio_bytes = await localize_tts_response(
            app,
            b'{"code":200,"url":"http://us-api-3.living.ai/tts/dl/private-token"}',
            None,
        )
        payload = json.loads(localized)
        self.assertTrue(payload["url"].startswith(LOCAL_TTS_BASE + "/"))
        self.assertNotIn("private-token", payload["url"])
        self.assertEqual(content_type, "audio/wav")
        self.assertEqual(audio_bytes, len(wave))
        self.assertEqual(len(app["runtime"]["tts_audio_cache"]), 1)


class AcknowledgementAudioTests(unittest.TestCase):
    def test_phrase_library_has_ten_unique_acknowledgements_and_reset(self) -> None:
        self.assertEqual(len(ACKNOWLEDGEMENTS), 10)
        self.assertEqual(len(set(ACKNOWLEDGEMENTS)), 10)
        self.assertEqual(len(expected_clips()), 11)
        self.assertRegex(cache_version(), r"^[0-9a-f]{16}$")

    def test_audio_library_round_trip_is_integrity_checked(self) -> None:
        wave = b"RIFF" + (4).to_bytes(4, "little") + b"WAVE" + b"data"
        generated = [
            (clip_id, text, "audio/wav", wave + clip_id.encode("ascii"))
            for clip_id, text in expected_clips()
        ]
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            written = write_library_atomic(root, generated)
            loaded = load_library(root)
            self.assertIsNotNone(loaded)
            self.assertEqual(len(written.by_id), 11)
            self.assertEqual(loaded.by_id["reset"].text, dict(expected_clips())["reset"])

            manifest = next(root.glob("*/manifest.json"))
            manifest.write_text("{}", encoding="utf-8")
            self.assertIsNone(load_library(root))

    def test_audio_cache_rejects_unsupported_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaises(AudioCacheError):
                write_library_atomic(
                    Path(temporary),
                    [(clip_id, text, "text/plain", b"not-audio") for clip_id, text in expected_clips()],
                )

    def test_tts_download_url_is_limited_to_living_ai(self) -> None:
        self.assertTrue(living_ai_audio_url("https://eu-api.living.ai/tts/dl/token"))
        self.assertTrue(living_ai_audio_url("http://tts.living.ai/download/file.wav"))
        self.assertFalse(living_ai_audio_url("https://living.ai.example.test/audio"))
        self.assertFalse(living_ai_audio_url("https://user:pass@api.living.ai/audio"))


if __name__ == "__main__":
    unittest.main()
