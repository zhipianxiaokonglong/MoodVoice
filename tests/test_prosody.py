"""Provider-specific prosody realization tests (no real network calls)."""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from emotional_tts.config import ProviderConfig
from emotional_tts.providers.azure import AzureProvider
from emotional_tts.providers.base import SynthesisRequest
from emotional_tts.providers.edge import EdgeProvider
from emotional_tts.providers.elevenlabs import ElevenLabsProvider
from emotional_tts.providers.local_http import LocalHTTPProvider
from emotional_tts.providers.minimax import MiniMaxProvider
from emotional_tts.providers.openai import OpenAIProvider
from emotional_tts.speech import SpeechSegment


def request(
    style: dict[str, Any] | None = None,
    *,
    intensity: float = 0.5,
    segments: tuple[SpeechSegment, ...] = (),
) -> SynthesisRequest:
    return SynthesisRequest(
        text="你好 & <世界>",
        language="zh-CN",
        emotion="comfort",
        tone="温柔低缓",
        speed=0.92,
        voice=None,
        style=style or {},
        output_format="mp3",
        intensity=intensity,
        segments=segments,
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


async def test_azure_uses_intensity_and_breaks_for_segments():
    seen: dict[str, Any] = {}

    def handler(req: httpx.Request) -> httpx.Response:
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
    segments = (
        SpeechSegment("先别急。", 300, 0),
        SpeechSegment("我们慢慢来。", 0, 0),
    )
    try:
        data = await collect(
            provider,
            request(
                {"style": "gentle", "style_degree": 1.6, "pitch": "-2%"},
                intensity=0.8,
                segments=segments,
            ),
        )
    finally:
        await old.aclose()
        await provider.close()

    assert data == b"ID3azure"
    assert 'style="gentle"' in seen["content"]
    assert '<break time="300ms"/>' in seen["content"]
    assert "先别急。" in seen["content"]
    # 1.6 * (0.85 + 0.3 * 0.8) = 1.744, clamped/rounded to two decimals.
    assert 'styledegree="1.74"' in seen["content"]
    assert "&lt;break" not in seen["content"]


def test_edge_maps_intensity_to_rate_pitch_and_volume():
    provider = EdgeProvider(ProviderConfig(voice="zh-CN-XiaoxiaoNeural"))
    base = provider._params(request({"pitch_semitones": 0, "volume_db": 0}))
    stronger = provider._params(request({"pitch_semitones": 0, "volume_db": 0}, intensity=0.9))
    assert base["rate"] != stronger["rate"]
    assert base["pitch"] != stronger["pitch"]
    assert base["volume"] != stronger["volume"]


async def test_openai_adds_intensity_to_instructions():
    seen: dict[str, Any] = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen["payload"] = json.loads(req.content)
        return httpx.Response(200, content=b"ID3openai", headers={"content-type": "audio/mpeg"})

    provider = OpenAIProvider(
        ProviderConfig(
            enabled=True,
            base_url="https://api.openai.test/v1",
            model="gpt-4o-mini-tts",
            voice="coral",
            response_format="mp3",
        ),
        api_key="sk-test",
    )
    old = replace_client(provider, handler)
    try:
        await collect(
            provider,
            request({"instructions": "warm and gentle"}, intensity=0.9),
        )
    finally:
        await old.aclose()
        await provider.close()

    assert "warm and gentle" in seen["payload"]["instructions"]
    assert "Performance intensity: strong." in seen["payload"]["instructions"]
    assert seen["payload"]["speed"] > 0.92


async def test_elevenlabs_maps_intensity_to_style():
    seen: dict[str, Any] = {}

    def handler(req: httpx.Request) -> httpx.Response:
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
        await collect(
            provider,
            request({"stability": 0.62, "style": 0.45}, intensity=0.9),
        )
    finally:
        await old.aclose()
        await provider.close()

    assert seen["payload"]["voice_settings"]["style"] > 0.45
    assert seen["payload"]["voice_settings"]["stability"] < 0.62


async def test_minimax_maps_intensity_to_speed_and_pitch():
    seen: dict[str, Any] = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen["payload"] = json.loads(req.content)
        return httpx.Response(
            200,
            json={
                "data": {"audio": b"ID3minimax".hex()},
                "base_resp": {"status_code": 0},
            },
        )

    provider = MiniMaxProvider(
        ProviderConfig(
            enabled=True,
            base_url="https://api.minimax.test/v1/t2a_v2",
            voice_id="female-shaonv",
            model="speech-02-hd",
        ),
        api_key="mm-test",
    )
    old = replace_client(provider, handler)
    try:
        await collect(provider, request({"pitch_semitones": 0}, intensity=0.9))
    finally:
        await old.aclose()
        await provider.close()

    assert seen["payload"]["voice_setting"]["speed"] > 0.92
    assert seen["payload"]["voice_setting"]["pitch"] > 0


def test_local_receives_intensity_and_segments():
    provider = LocalHTTPProvider(
        ProviderConfig(base_url="http://127.0.0.1:9999/synthesize", format="wav")
    )
    payload = provider._payload(
        request(
            intensity=0.7,
            segments=(SpeechSegment("第一段。", 300, 0),),
        )
    )
    assert payload["style"]["intensity"] == pytest.approx(0.7)
    assert payload["segments"][0]["text"] == "第一段。"
    assert payload["segments"][0]["pause_after_ms"] == 300
