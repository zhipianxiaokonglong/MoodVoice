"""OpenAI Audio Speech adapter (instruction-driven emotion)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any, ClassVar

import httpx

from ..errors import ProviderError
from .base import ProviderCapabilities, SynthesisRequest, TTSProvider
from .http import stream_audio


class OpenAIProvider(TTSProvider):
    name: ClassVar[str] = "openai"
    display_name: ClassVar[str] = "OpenAI TTS"
    capabilities: ClassVar[ProviderCapabilities] = ProviderCapabilities(
        streaming=True,
        emotional_control="instructions",
        emotion_styles="gpt-4o-mini-tts 支持自然语言表演指令；tts-1 仅支持语速",
        requires_key=True,
        data_destination="OpenAI API（用户自己的账户）",
        formats=("mp3", "wav", "opus", "aac", "flac", "pcm"),
        notes="情感风格通过 instructions 传达，不是离散 style 枚举。",
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
        self.base_url = (getattr(config, "base_url", None) or "https://api.openai.com/v1").rstrip(
            "/"
        )
        self.model = getattr(config, "model", None) or "gpt-4o-mini-tts"
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(timeout_seconds, connect=10.0),
            follow_redirects=False,
        )

    @property
    def audio_format(self) -> str:
        return str(getattr(self.config, "response_format", None) or "mp3").lower()

    def readiness(self) -> tuple[bool, list[str]]:
        missing: list[str] = []
        if not getattr(self.config, "enabled", True):
            missing.append("disabled_in_config")
        if not self.api_key:
            missing.append("OPENAI_API_KEY")
        if not self.base_url:
            missing.append("base_url")
        return not missing, missing

    def _voice(self, request: SynthesisRequest) -> str:
        return str(request.voice or getattr(self.config, "voice", None) or "coral")

    def _payload(self, request: SynthesisRequest, voice: str) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": self.model,
            "input": request.text,
            "voice": voice,
            "response_format": getattr(self.config, "response_format", None)
            or request.output_format,
            "speed": round(max(0.25, min(4.0, request.speed)), 3),
        }
        instructions = request.style.get("instructions")
        # tts-1/tts-1-hd do not accept instructions. Avoid sending a parameter
        # the selected model will reject.
        supports_instructions = not self.model.startswith("tts-1")
        if instructions and supports_instructions:
            payload["instructions"] = str(instructions)
        return payload

    def request_preview(self, request: SynthesisRequest) -> dict[str, Any]:
        preview = super().request_preview(request)
        voice = self._voice(request)
        preview["effective"] = {
            "model": self.model,
            "voice": voice,
            "response_format": getattr(self.config, "response_format", None)
            or request.output_format,
            "speed": round(request.speed, 3),
            "instructions": str(request.style.get("instructions", ""))[:400],
        }
        return preview

    async def stream(self, request: SynthesisRequest) -> AsyncIterator[bytes]:
        ready, missing = self.readiness()
        if not ready:
            raise ProviderError(
                provider=self.name,
                code="missing_credentials",
                message="OpenAI TTS 未就绪，请在本地 .env 配置 OPENAI_API_KEY。",
                stage="provider_auth",
                details={"missing": missing},
            )
        voice = self._voice(request)
        url = f"{self.base_url}/audio/speech"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        async for chunk in stream_audio(
            self.name,
            self._client,
            "POST",
            url,
            headers=headers,
            json_body=self._payload(request, voice),
            stage="provider_synthesis",
        ):
            yield chunk

    async def close(self) -> None:
        await self._client.aclose()
