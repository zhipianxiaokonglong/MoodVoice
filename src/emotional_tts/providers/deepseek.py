"""DeepSeek text-to-speech tool API adapter.

DeepSeek exposes TTS at ``POST /v1/tools/tts`` (JSON body, audio streamed back).
Only two voices are available (``stella`` / ``tide``); there is no speed or
emotion-style parameter, so the adapter keeps the upstream emotion label for
routing/UI but does not pretend to control prosody.
"""

from __future__ import annotations

import struct
from collections.abc import AsyncIterator
from typing import Any, ClassVar

import httpx

from ..errors import ProviderError
from ..speech import SpeechSegment, concat_wav
from .base import ProsodyCapabilities, ProviderCapabilities, SynthesisRequest, TTSProvider
from .http import stream_audio


class DeepSeekProvider(TTSProvider):
    name: ClassVar[str] = "deepseek"
    display_name: ClassVar[str] = "DeepSeek TTS"
    capabilities: ClassVar[ProviderCapabilities] = ProviderCapabilities(
        streaming=True,
        emotional_control="voice_only",
        emotion_styles="Stella（女声）/ Tide（男声）两音色；不支持语速与情感风格参数",
        requires_key=True,
        data_destination="DeepSeek API（用户自己的账户）",
        formats=("wav",),
        notes="工具 API 返回 WAV；情感标签只用于本层路由与 UI，不改变音色风格。",
        prosody=ProsodyCapabilities(
            text_pause_sensitivity="unknown",
            supports_segment_synthesis=True,
        ),
    )

    def __init__(
        self,
        config: Any,
        *,
        api_key: str | None,
        timeout_seconds: float = 60.0,
        transport: httpx.AsyncBaseTransport | None = None,
        speech_settings: Any | None = None,
    ) -> None:
        super().__init__(config, timeout_seconds=timeout_seconds)
        self.api_key = api_key
        self.base_url = (getattr(config, "base_url", None) or "https://api.deepseek.com").rstrip(
            "/"
        )
        self.voice_id = (
            getattr(config, "voice_id", None) or getattr(config, "voice", None) or "stella"
        )
        self.filter_markdown = bool(getattr(config, "filter_markdown", False))
        self.speech_settings = speech_settings
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(timeout_seconds, connect=10.0),
            follow_redirects=False,
            transport=transport,
        )

    @property
    def audio_format(self) -> str:
        return "wav"

    def readiness(self) -> tuple[bool, list[str]]:
        missing: list[str] = []
        if not getattr(self.config, "enabled", True):
            missing.append("disabled_in_config")
        if not self.api_key:
            missing.append("DEEPSEEK_API_KEY")
        if not self.base_url:
            missing.append("base_url")
        return not missing, missing

    def _voice(self, request: SynthesisRequest) -> str:
        voice = str(request.voice or self.voice_id or "stella").lower()
        return voice if voice in {"stella", "tide"} else "stella"

    def _payload(self, text: str, voice: str) -> dict[str, Any]:
        return {
            "text": text,
            "voice_id": voice,
            "format": "wav",
            "filter_markdown": self.filter_markdown,
        }

    def request_preview(self, request: SynthesisRequest) -> dict[str, Any]:
        preview = super().request_preview(request)
        preview["effective"] = {
            "voice": self._voice(request),
            "format": "wav",
            "speed": "vendor_default",
            "intensity": "ignored_by_vendor",
            "emotional_control": "voice_only",
            "filter_markdown": self.filter_markdown,
            "segmentation": bool(
                request.segments and getattr(self.speech_settings, "segmentation_enabled", False)
            ),
        }
        return preview

    async def stream(self, request: SynthesisRequest) -> AsyncIterator[bytes]:
        ready, missing = self.readiness()
        if not ready:
            raise ProviderError(
                provider=self.name,
                code="missing_credentials",
                message="DeepSeek TTS 未就绪，请配置 DEEPSEEK_API_KEY。",
                stage="provider_auth",
                details={"missing": missing},
            )
        voice = self._voice(request)
        segments = self._segments_for_request(request)
        if segments:
            try:
                audio = await self._synthesize_segments(segments, voice)
            except Exception:
                # A failed segment must not expose partial audio. Fall back to
                # one full-text request; if that also fails, the pipeline gets
                # the normal ProviderError and degrades to text.
                audio = await self._full_audio(request.text, voice)
        else:
            audio = await self._full_audio(request.text, voice)
        for offset in range(0, len(audio), 16_384):
            yield audio[offset : offset + 16_384]

    def _segments_for_request(self, request: SynthesisRequest) -> list[SpeechSegment]:
        settings = self.speech_settings
        if not getattr(settings, "segmentation_enabled", False):
            return []
        segments = [segment for segment in request.segments if segment.text.strip()]
        if len(segments) < 2:
            return []
        minimum = int(getattr(settings, "segmentation_min_chars", 0) or 0)
        if len(request.text) < minimum:
            return []
        limit = int(getattr(settings, "max_segments", 4) or 4)
        while len(segments) > limit:
            index = min(
                range(len(segments) - 1),
                key=lambda item: len(segments[item].text) + len(segments[item + 1].text),
            )
            left, right = segments[index], segments[index + 1]
            segments[index : index + 2] = [
                SpeechSegment(
                    text=left.text + right.text,
                    pause_after_ms=right.pause_after_ms,
                    paragraph_index=left.paragraph_index,
                )
            ]
        return segments

    async def _synthesize_segments(self, segments: list[SpeechSegment], voice: str) -> bytes:
        parts = [await self._full_audio(segment.text, voice) for segment in segments]
        default_pause = int(getattr(self.speech_settings, "segment_pause_ms", 320) or 0)
        pauses = [segment.pause_after_ms or default_pause for segment in segments[:-1]]
        fade = int(getattr(self.speech_settings, "crossfade_ms", 30) or 0)
        return concat_wav(parts, pauses, fade_ms=fade)

    async def _full_audio(self, text: str, voice: str) -> bytes:
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
        }
        # DeepSeek streams WAV with 0xFFFFFFFF RIFF/data sizes. Chrome/WebView2
        # may refuse to play such a file (or report a 24-hour duration), so we
        # buffer the short response and write the real sizes before handing it
        # to the local cache. TTS answers are capped at 10k characters, so this
        # does not meaningfully change memory usage.
        payload = bytearray()
        async for chunk in stream_audio(
            self.name,
            self._client,
            "POST",
            f"{self.base_url}/v1/tools/tts",
            headers=headers,
            json_body=self._payload(text, voice),
            stage="provider_synthesis",
        ):
            payload.extend(chunk)
        return _repair_wav_header(bytes(payload))

    async def close(self) -> None:
        await self._client.aclose()


def _repair_wav_header(data: bytes) -> bytes:
    """Replace streaming 0xFFFFFFFF RIFF/data lengths with actual sizes."""

    if len(data) < 44 or data[:4] != b"RIFF" or data[8:12] != b"WAVE":
        return data
    buffer = bytearray(data)
    struct.pack_into("<I", buffer, 4, len(buffer) - 8)
    offset = 12
    while offset + 8 <= len(buffer):
        chunk_id = bytes(buffer[offset : offset + 4])
        declared = struct.unpack_from("<I", buffer, offset + 4)[0]
        body = offset + 8
        available = len(buffer) - body
        if chunk_id == b"data":
            struct.pack_into("<I", buffer, offset + 4, available)
            break
        if declared in {0, 0xFFFFFFFF} or declared > available:
            break
        offset = body + declared + (declared & 1)
    return bytes(buffer)
