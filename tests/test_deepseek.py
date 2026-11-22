from __future__ import annotations

import json
from types import SimpleNamespace

import httpx
import pytest

from emotional_tts.config import ProviderConfig
from emotional_tts.errors import ProviderError
from emotional_tts.providers.base import SynthesisRequest
from emotional_tts.providers.deepseek import DeepSeekProvider, _repair_wav_header
from emotional_tts.speech import SpeechSegment, WavFormat, read_wav, write_wav


def _request() -> SynthesisRequest:
    return SynthesisRequest(
        text="你好，我是豆奶。",
        language="zh-CN",
        emotion="comfort",
        tone="温柔",
        speed=1.0,
        voice=None,
        output_format="wav",
    )


def _provider(config: ProviderConfig, key: str | None = "sk-test") -> DeepSeekProvider:
    return DeepSeekProvider(config, api_key=key, timeout_seconds=5)


def test_deepseek_repairs_streaming_wav_header():
    import struct

    data = bytearray(72)
    data[0:4] = b"RIFF"
    data[4:8] = b"\xff\xff\xff\xff"
    data[8:12] = b"WAVE"
    data[12:16] = b"fmt "
    struct.pack_into("<I", data, 16, 16)
    struct.pack_into("<H", data, 20, 1)  # PCM
    struct.pack_into("<H", data, 22, 1)  # mono
    struct.pack_into("<I", data, 24, 24000)
    struct.pack_into("<I", data, 28, 48000)
    struct.pack_into("<H", data, 32, 2)
    struct.pack_into("<H", data, 34, 16)
    data[36:40] = b"data"
    data[40:44] = b"\xff\xff\xff\xff"

    fixed = _repair_wav_header(bytes(data))
    assert struct.unpack_from("<I", fixed, 4)[0] == len(data) - 8
    assert struct.unpack_from("<I", fixed, 40)[0] == len(data) - 44


def test_deepseek_requires_key():
    provider = _provider(ProviderConfig(), key=None)
    ready, missing = provider.readiness()
    assert ready is False
    assert "DEEPSEEK_API_KEY" in missing
    assert provider.audio_format == "wav"


async def test_deepseek_streams_wav_and_sends_expected_payload():
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["authorization"] = request.headers.get("authorization")
        seen["payload"] = json.loads(request.content.decode("utf-8"))
        return httpx.Response(
            200,
            content=b"RIFFfake-wav",
            headers={"content-type": "audio/wav"},
        )

    provider = _provider(
        ProviderConfig(base_url="https://api.deepseek.com", voice_id="tide"),
    )
    provider._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))

    chunks = [chunk async for chunk in provider.stream(_request())]

    assert b"".join(chunks) == b"RIFFfake-wav"
    assert seen["url"] == "https://api.deepseek.com/v1/tools/tts"
    assert seen["authorization"] == "Bearer sk-test"
    payload = seen["payload"]
    assert isinstance(payload, dict)
    assert payload["voice_id"] == "tide"
    assert payload["format"] == "wav"
    assert payload["filter_markdown"] is False
    await provider.close()


async def test_deepseek_rate_limit_is_retryable():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, json={"error": {"message": "busy"}})

    provider = _provider(ProviderConfig())
    provider._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))

    with pytest.raises(ProviderError) as raised:
        _ = [chunk async for chunk in provider.stream(_request())]

    assert raised.value.retryable is True
    assert raised.value.http_status == 429
    await provider.close()


def _wav(ms: int = 100) -> bytes:
    fmt = WavFormat(channels=1, sample_width=2, frame_rate=24_000)
    frames = int(fmt.frame_rate * ms / 1000)
    return write_wav(b"\x00\x00" * frames, fmt)


async def test_deepseek_segments_insert_silence_and_return_one_wav():
    texts: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content.decode("utf-8"))
        texts.append(payload["text"])
        return httpx.Response(200, content=_wav(100), headers={"content-type": "audio/wav"})

    settings = SimpleNamespace(
        segmentation_enabled=True,
        segmentation_min_chars=0,
        max_segments=4,
        segment_pause_ms=320,
        crossfade_ms=0,
    )
    provider = DeepSeekProvider(
        ProviderConfig(base_url="https://api.deepseek.com", voice_id="stella"),
        api_key="sk-test",
        speech_settings=settings,
    )
    provider._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    request = SynthesisRequest(
        text="第一段。第二段。",
        language="zh-CN",
        emotion="comfort",
        tone="温柔",
        speed=1.0,
        voice=None,
        output_format="wav",
        segments=(
            SpeechSegment("第一段。", 300, 0),
            SpeechSegment("第二段。", 0, 0),
        ),
    )
    try:
        audio = b"".join([chunk async for chunk in provider.stream(request)])
    finally:
        await provider.close()

    assert texts == ["第一段。", "第二段。"]
    parsed = read_wav(audio)
    # 100ms + 300ms + 100ms at 24kHz mono 16-bit.
    assert len(parsed.pcm) == int(24_000 * 0.5) * 2


async def test_deepseek_segment_failure_falls_back_to_full_text():
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content.decode("utf-8"))
        calls.append(payload["text"])
        if len(calls) == 1:
            return httpx.Response(500, json={"error": {"message": "boom"}})
        return httpx.Response(200, content=_wav(50), headers={"content-type": "audio/wav"})

    settings = SimpleNamespace(
        segmentation_enabled=True,
        segmentation_min_chars=0,
        max_segments=4,
        segment_pause_ms=320,
        crossfade_ms=0,
    )
    provider = DeepSeekProvider(
        ProviderConfig(base_url="https://api.deepseek.com"),
        api_key="sk-test",
        speech_settings=settings,
    )
    provider._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    request = SynthesisRequest(
        text="第一段。第二段。",
        language="zh-CN",
        emotion="neutral",
        tone="平稳",
        speed=1.0,
        voice=None,
        output_format="wav",
        segments=(
            SpeechSegment("第一段。", 300, 0),
            SpeechSegment("第二段。", 0, 0),
        ),
    )
    try:
        audio = b"".join([chunk async for chunk in provider.stream(request)])
    finally:
        await provider.close()

    assert calls == ["第一段。", "第一段。第二段。"]
    assert read_wav(audio).fmt.frame_rate == 24_000
