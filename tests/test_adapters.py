from __future__ import annotations

import base64
import json
from typing import Any

import httpx
import pytest

from emotional_tts.config import ProviderConfig
from emotional_tts.errors import ProviderError
from emotional_tts.providers.azure import AzureProvider
from emotional_tts.providers.base import SynthesisRequest
from emotional_tts.providers.edge import EdgeProvider
from emotional_tts.providers.elevenlabs import ElevenLabsProvider
from emotional_tts.providers.local_http import LocalHTTPProvider
from emotional_tts.providers.minimax import MiniMaxProvider
from emotional_tts.providers.openai import OpenAIProvider


def request(style: dict[str, Any] | None = None) -> SynthesisRequest:
    return SynthesisRequest(
        text="你好 & <世界>",
        language="zh-CN",
        emotion="comfort",
        tone="温柔低缓",
        speed=0.92,
        voice=None,
        style=style or {},
        output_format="mp3",
    )


async def collect(provider, req: SynthesisRequest) -> bytes:
    parts = []
    async for chunk in provider.stream(req):
        parts.append(chunk)
    return b"".join(parts)


def replace_client(provider, handler):
    old = provider._client
    provider._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return old


async def test_openai_sends_instructions_for_gpt4o_and_streams_audio():
    seen: dict[str, Any] = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen["url"] = str(req.url)
        seen["auth"] = req.headers.get("authorization")
        seen["payload"] = json.loads(req.content)
        return httpx.Response(
            200,
            content=b"ID3openai",
            headers={"content-type": "audio/mpeg", "x-request-id": "req_1"},
        )

    provider = OpenAIProvider(
        ProviderConfig(
            enabled=True,
            base_url="https://api.openai.test/v1",
            model="gpt-4o-mini-tts",
            voice="coral",
            response_format="mp3",
        ),
        api_key="sk-test-openai",
    )
    old = replace_client(provider, handler)
    try:
        data = await collect(
            provider,
            request({"instructions": "warm, slow and gentle", "emotion": "comfort"}),
        )
    finally:
        await old.aclose()
        await provider.close()

    assert data == b"ID3openai"
    assert seen["url"].endswith("/v1/audio/speech")
    assert seen["auth"] == "Bearer sk-test-openai"
    assert seen["payload"]["instructions"] == "warm, slow and gentle"
    assert seen["payload"]["speed"] == pytest.approx(0.92)
    assert seen["payload"]["voice"] == "coral"


async def test_openai_omits_instructions_for_tts1():
    seen: dict[str, Any] = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen["payload"] = json.loads(req.content)
        return httpx.Response(200, content=b"audio", headers={"content-type": "audio/mpeg"})

    provider = OpenAIProvider(
        ProviderConfig(model="tts-1", voice="alloy", response_format="mp3"),
        api_key="sk-test",
    )
    old = replace_client(provider, handler)
    try:
        await collect(provider, request({"instructions": "ignored"}))
    finally:
        await old.aclose()
        await provider.close()
    assert "instructions" not in seen["payload"]


async def test_openai_error_does_not_leak_upstream_body():
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(
            401,
            content=b'{"error":"bad key sk-leaked-secret"}',
            headers={"content-type": "application/json"},
        )

    provider = OpenAIProvider(
        ProviderConfig(model="gpt-4o-mini-tts", voice="coral"), api_key="sk-wrong"
    )
    old = replace_client(provider, handler)
    try:
        with pytest.raises(ProviderError) as info:
            await collect(provider, request())
    finally:
        await old.aclose()
        await provider.close()
    assert info.value.code == "auth_failed"
    assert "sk-leaked-secret" not in info.value.message
    assert "sk-leaked-secret" not in json.dumps(info.value.as_attempt(), ensure_ascii=False)


async def test_elevenlabs_payload_and_endpoint():
    seen: dict[str, Any] = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen["url"] = str(req.url)
        seen["headers"] = dict(req.headers)
        seen["payload"] = json.loads(req.content)
        return httpx.Response(200, content=b"ID3eleven", headers={"content-type": "audio/mpeg"})

    provider = ElevenLabsProvider(
        ProviderConfig(
            enabled=True,
            base_url="https://api.eleven.test/v1",
            voice_id="voice_123",
            model_id="eleven_multilingual_v2",
            output_format="mp3_44100_128",
        ),
        api_key="xi-test",
    )
    old = replace_client(provider, handler)
    try:
        data = await collect(
            provider,
            request(
                {
                    "stability": 0.62,
                    "similarity_boost": 0.85,
                    "style": 0.45,
                    "use_speaker_boost": False,
                    "emotion": "comfort",
                }
            ),
        )
    finally:
        await old.aclose()
        await provider.close()

    assert data == b"ID3eleven"
    assert "/text-to-speech/voice_123/stream" in seen["url"]
    assert "output_format=mp3_44100_128" in seen["url"]
    assert seen["headers"]["xi-api-key"] == "xi-test"
    assert seen["payload"]["voice_settings"]["style"] == 0.45
    assert seen["payload"]["voice_settings"]["speed"] == pytest.approx(0.92)
    assert seen["payload"]["text"] == "你好 & <世界>"


async def test_azure_builds_escaped_ssml_with_native_style():
    seen: dict[str, Any] = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen["url"] = str(req.url)
        seen["content"] = req.content.decode("utf-8")
        return httpx.Response(200, content=b"ID3azure", headers={"content-type": "audio/mpeg"})

    provider = AzureProvider(
        ProviderConfig(
            enabled=True,
            region="eastasia",
            voice="zh-CN-XiaoxiaoNeural",
            output_format="audio-24khz-48kbitrate-mono-mp3",
        ),
        api_key="azure-test",
    )
    old = replace_client(provider, handler)
    try:
        data = await collect(
            provider,
            request({"style": "gentle", "style_degree": 1.6, "pitch": "-2%"}),
        )
    finally:
        await old.aclose()
        await provider.close()

    assert data == b"ID3azure"
    assert "eastasia.tts.speech.microsoft.com" in seen["url"]
    assert 'style="gentle"' in seen["content"]
    assert 'styledegree="1.6"' in seen["content"]
    assert "你好 &amp; &lt;世界&gt;" in seen["content"]
    assert "mstts:express-as" in seen["content"]


async def test_minimax_decodes_hex_and_sends_group_id():
    seen: dict[str, Any] = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen["url"] = str(req.url)
        seen["auth"] = req.headers.get("authorization")
        seen["payload"] = json.loads(req.content)
        return httpx.Response(
            200,
            json={
                "data": {"audio": b"ID3minimax".hex()},
                "trace_id": "trace-1",
                "base_resp": {"status_code": 0, "status_msg": "success"},
            },
        )

    provider = MiniMaxProvider(
        ProviderConfig(
            enabled=True,
            base_url="https://api.minimax.test/v1/t2a_v2",
            voice_id="female-shaonv",
            group_id="group-1",
            model="speech-02-hd",
        ),
        api_key="mm-test",
    )
    old = replace_client(provider, handler)
    try:
        data = await collect(
            provider,
            request({"emotion": "happy", "pitch_semitones": 2}),
        )
    finally:
        await old.aclose()
        await provider.close()

    assert data == b"ID3minimax"
    assert "GroupId=group-1" in seen["url"]
    assert seen["auth"] == "Bearer mm-test"
    assert seen["payload"]["voice_setting"]["emotion"] == "happy"
    assert seen["payload"]["voice_setting"]["speed"] == pytest.approx(0.92)


async def test_minimax_vendor_error_is_mapped():
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"data": None, "trace_id": "t2", "base_resp": {"status_code": 1004}},
        )

    provider = MiniMaxProvider(
        ProviderConfig(base_url="https://api.minimax.test/v1/t2a_v2", voice_id="v"),
        api_key="bad",
    )
    old = replace_client(provider, handler)
    try:
        with pytest.raises(ProviderError) as info:
            await collect(provider, request())
    finally:
        await old.aclose()
        await provider.close()
    assert info.value.code == "auth_failed"
    assert info.value.request_id == "t2"


async def test_local_http_binary_and_json_contracts():
    seen: list[dict[str, Any]] = []

    def binary_handler(req: httpx.Request) -> httpx.Response:
        seen.append(json.loads(req.content))
        return httpx.Response(200, content=b"RIFFlocal", headers={"content-type": "audio/wav"})

    provider = LocalHTTPProvider(
        ProviderConfig(enabled=True, base_url="http://local.test/tts", format="wav"),
    )
    old = replace_client(provider, binary_handler)
    try:
        data = await collect(provider, request({"pitch_semitones": -1}))
    finally:
        await old.aclose()
        await provider.close()
    assert data == b"RIFFlocal"
    assert seen[0]["emotion"] == "comfort"
    assert seen[0]["style"]["speed"] == pytest.approx(0.92)
    assert seen[0]["format"] == "wav"

    payload = base64.b64encode(b"RIFFjson").decode()
    provider = LocalHTTPProvider(
        ProviderConfig(enabled=True, base_url="http://local.test/tts", format="wav"),
    )
    old = replace_client(
        provider,
        lambda req: httpx.Response(200, json={"audio_base64": payload, "format": "wav"}),
    )
    try:
        data = await collect(provider, request())
    finally:
        await old.aclose()
        await provider.close()
    assert data == b"RIFFjson"


async def test_local_format_mismatch_is_explicit():
    provider = LocalHTTPProvider(
        ProviderConfig(enabled=True, base_url="http://local.test/tts", format="wav"),
    )
    old = replace_client(
        provider,
        lambda req: httpx.Response(200, content=b"ID3", headers={"content-type": "audio/mpeg"}),
    )
    try:
        with pytest.raises(ProviderError) as info:
            await collect(provider, request())
    finally:
        await old.aclose()
        await provider.close()
    assert info.value.code == "format_mismatch"
    assert info.value.details["configured_format"] == "wav"


async def test_edge_invalid_voice_is_a_style_error(monkeypatch):
    import edge_tts

    def boom(*args, **kwargs):
        raise ValueError("Invalid voice 'nope'")

    monkeypatch.setattr(edge_tts, "Communicate", boom)
    provider = EdgeProvider(ProviderConfig(enabled=True, voice="nope"))
    with pytest.raises(ProviderError) as info:
        await collect(provider, request({"pitch": "-2Hz", "volume": "-2%"}))
    assert info.value.code == "invalid_style"
    assert info.value.stage == "style_resolution"
    assert info.value.details["parameter"] == "voice"


def test_edge_params_derive_from_canonical_style():
    provider = EdgeProvider(ProviderConfig(enabled=True, voice="zh-CN-XiaoxiaoNeural"))
    req = SynthesisRequest(
        text="测试",
        language="zh-CN",
        emotion="comfort",
        tone="温柔",
        speed=0.92,
        voice="zh-CN-XiaoxiaoNeural",
        style={"pitch": "-2Hz", "volume": "-2%", "pitch_semitones": -1},
        output_format="mp3",
    )
    preview = provider.request_preview(req)
    assert preview["effective"] == {
        "voice": "zh-CN-XiaoxiaoNeural",
        "rate": "-8%",
        "pitch": "-2Hz",
        "volume": "-2%",
    }
    assert provider.ready is True
