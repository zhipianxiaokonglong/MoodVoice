"""ElevenLabs streaming adapter (style intensity + stability)."""

from __future__ import annotations

import re
from collections.abc import AsyncIterator
from typing import Any, ClassVar

import httpx

from ..errors import ProviderError
from .base import ProviderCapabilities, SynthesisRequest, TTSProvider
from .http import stream_audio

_VOICE_RE = re.compile(r"^[A-Za-z0-9_-]{1,80}$")


class ElevenLabsProvider(TTSProvider):
    name: ClassVar[str] = "elevenlabs"
    display_name: ClassVar[str] = "ElevenLabs"
    capabilities: ClassVar[ProviderCapabilities] = ProviderCapabilities(
        streaming=True,
        emotional_control="style_intensity",
        emotion_styles="通过 stability / style / speed 调整表演强度与情绪张力",
        requires_key=True,
        data_destination="ElevenLabs API（用户自己的账户）",
        formats=("mp3",),
        notes="情绪不是枚举参数；映射表把标签转为 stability/style/speed 组合。",
    )

    def __init__(
        self,
        config: Any,
        *,
        api_key: str | None,
        timeout_seconds: float = 30.0,
    ) -> None:
        super().__init__(config, timeout_seconds=timeout_seconds)
        self.api_key = api_key
        self.base_url = (
            getattr(config, "base_url", None) or "https://api.elevenlabs.io/v1"
        ).rstrip("/")
        self.model_id = getattr(config, "model_id", None) or "eleven_multilingual_v2"
        self.output_format = getattr(config, "output_format", None) or "mp3_44100_128"
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(timeout_seconds, connect=10.0),
            follow_redirects=False,
        )

    def _voice_id(self, request: SynthesisRequest) -> str:
        value = str(
            request.style.get("voice_id")
            or request.voice
            or getattr(self.config, "voice_id", None)
            or ""
        ).strip()
        if value and not _VOICE_RE.fullmatch(value):
            raise ProviderError(
                provider=self.name,
                code="invalid_style",
                message="ElevenLabs Voice ID 格式无效。",
                stage="style_resolution",
                details={"parameter": "voice_id"},
            )
        return value

    @property
    def audio_format(self) -> str:
        return "mp3"

    def readiness(self) -> tuple[bool, list[str]]:
        missing: list[str] = []
        if not getattr(self.config, "enabled", True):
            missing.append("disabled_in_config")
        if not self.api_key:
            missing.append("ELEVENLABS_API_KEY")
        if not getattr(self.config, "voice_id", None):
            missing.append("providers.elevenlabs.voice_id")
        return not missing, missing

    def _voice_settings(self, request: SynthesisRequest) -> dict[str, Any]:
        style = request.style
        settings: dict[str, Any] = {
            "stability": _clamp(style.get("stability", 0.5), 0.0, 1.0),
            "similarity_boost": _clamp(style.get("similarity_boost", 0.8), 0.0, 1.0),
            "style": _clamp(style.get("style", 0.35), 0.0, 1.0),
            "use_speaker_boost": bool(style.get("use_speaker_boost", True)),
        }
        if getattr(self.config, "send_speed", True):
            settings["speed"] = _clamp(request.speed, 0.7, 1.2)
        return settings

    def request_preview(self, request: SynthesisRequest) -> dict[str, Any]:
        preview = super().request_preview(request)
        preview["effective"] = {
            "voice_id": self._voice_id(request),
            "model_id": self.model_id,
            "output_format": self.output_format,
            "voice_settings": self._voice_settings(request),
        }
        return preview

    async def stream(self, request: SynthesisRequest) -> AsyncIterator[bytes]:
        ready, missing = self.readiness()
        voice_id = self._voice_id(request)
        if not ready or not voice_id:
            raise ProviderError(
                provider=self.name,
                code="missing_credentials",
                message="ElevenLabs 未就绪，请配置 ELEVENLABS_API_KEY 与 Voice ID。",
                stage="provider_auth",
                details={"missing": missing or ["voice_id"]},
            )
        url = f"{self.base_url}/text-to-speech/{voice_id}/stream"
        payload = {
            "text": request.text,
            "model_id": self.model_id,
            "voice_settings": self._voice_settings(request),
        }
        headers = {
            "xi-api-key": str(self.api_key),
            "Content-Type": "application/json",
            "Accept": "audio/mpeg",
        }
        async for chunk in stream_audio(
            self.name,
            self._client,
            "POST",
            url,
            headers=headers,
            params={"output_format": self.output_format},
            json_body=payload,
            stage="provider_synthesis",
        ):
            yield chunk

    async def close(self) -> None:
        await self._client.aclose()


def _clamp(value: object, low: float, high: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = low
    return round(max(low, min(high, number)), 3)
