"""Azure Neural TTS adapter with mstts:express-as emotional styles."""

from __future__ import annotations

import re
from collections.abc import AsyncIterator
from typing import Any, ClassVar
from xml.sax.saxutils import escape, quoteattr

import httpx

from ..errors import ProviderError
from .base import ProviderCapabilities, SynthesisRequest, TTSProvider
from .http import stream_audio

_STYLE_RE = re.compile(r"^[a-z][a-z0-9_-]{0,40}$")
_REGION_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,40}$")
_RATE_RE = re.compile(r"^[+-]\d{1,3}%$")
_PITCH_RE = re.compile(r"^[+-]\d{1,2}%$")
_VOLUME_RE = re.compile(r"^[+-]\d{1,2}%$")


class AzureProvider(TTSProvider):
    name: ClassVar[str] = "azure"
    display_name: ClassVar[str] = "Azure Neural TTS"
    capabilities: ClassVar[ProviderCapabilities] = ProviderCapabilities(
        streaming=True,
        emotional_control="native_style",
        emotion_styles="mstts:express-as 原生情感风格 + styledegree",
        requires_key=True,
        data_destination="Azure AI Speech（用户自己的订阅）",
        formats=("mp3", "wav", "ogg"),
        notes="情感表达最直接：style 枚举 + 强度 + prosody。",
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
        self.region = str(getattr(config, "region", None) or "").strip().lower()
        self.voice = str(getattr(config, "voice", None) or "zh-CN-XiaoxiaoNeural")
        self.output_format = str(
            getattr(config, "output_format", None) or "audio-24khz-48kbitrate-mono-mp3"
        )
        self.base_url = (getattr(config, "base_url", None) or "").rstrip("/")
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(timeout_seconds, connect=10.0),
            follow_redirects=False,
        )

    @property
    def audio_format(self) -> str:
        value = self.output_format.lower()
        if "wav" in value or "riff" in value:
            return "wav"
        if "ogg" in value or "opus" in value:
            return "ogg"
        return "mp3"

    def readiness(self) -> tuple[bool, list[str]]:
        missing: list[str] = []
        if not getattr(self.config, "enabled", True):
            missing.append("disabled_in_config")
        if not self.api_key:
            missing.append("AZURE_SPEECH_KEY")
        if not self.region:
            missing.append("AZURE_SPEECH_REGION")
        elif not _REGION_RE.fullmatch(self.region):
            missing.append("valid_region")
        return not missing, missing

    def _endpoint(self) -> str:
        if self.base_url:
            return self.base_url
        return f"https://{self.region}.tts.speech.microsoft.com/cognitiveservices/v1"

    def _effective(self, request: SynthesisRequest) -> dict[str, Any]:
        style = request.style
        azure_style = str(style.get("style") or "assistant").strip().lower()
        if not _STYLE_RE.fullmatch(azure_style):
            raise ProviderError(
                provider=self.name,
                code="invalid_style",
                message="Azure 情感风格名格式无效，请检查风格映射配置。",
                stage="style_resolution",
                details={"parameter": "style", "value": azure_style[:40]},
            )
        degree = _clamp(style.get("style_degree", 1.0), 0.01, 2.0)
        rate = str(style.get("rate") or _percent((request.speed - 1.0) * 100))
        pitch = str(style.get("pitch") or _percent(float(style.get("pitch_semitones", 0)) * 2))
        volume = str(style.get("volume") or _percent(float(style.get("volume_db", 0)) * 2))
        for label, value, pattern in (
            ("rate", rate, _RATE_RE),
            ("pitch", pitch, _PITCH_RE),
            ("volume", volume, _VOLUME_RE),
        ):
            if not pattern.fullmatch(value):
                raise ProviderError(
                    provider=self.name,
                    code="invalid_style",
                    message=f"Azure {label} 参数格式无效，请检查风格映射配置。",
                    stage="style_resolution",
                    details={"parameter": label, "value": value[:20]},
                )
        return {
            "voice": str(style.get("voice") or request.voice or self.voice),
            "style": azure_style,
            "style_degree": degree,
            "rate": rate,
            "pitch": pitch,
            "volume": volume,
            "lang": request.language,
        }

    def _ssml(self, request: SynthesisRequest, effective: dict[str, Any]) -> str:
        voice = effective["voice"]
        return (
            '<speak version="1.0" '
            'xmlns="http://www.w3.org/2001/10/synthesis" '
            'xmlns:mstts="https://www.w3.org/2001/mstts" '
            f"xml:lang={quoteattr(str(effective['lang']))}>"
            f"<voice name={quoteattr(str(voice))}>"
            f"<mstts:express-as style={quoteattr(str(effective['style']))} "
            f"styledegree={quoteattr(str(effective['style_degree']))}>"
            f"<prosody rate={quoteattr(str(effective['rate']))} "
            f"pitch={quoteattr(str(effective['pitch']))} "
            f"volume={quoteattr(str(effective['volume']))}>"
            f"{escape(request.text)}"
            "</prosody></mstts:express-as></voice></speak>"
        )

    def request_preview(self, request: SynthesisRequest) -> dict[str, Any]:
        preview = super().request_preview(request)
        effective = self._effective(request)
        preview["effective"] = {
            **effective,
            "output_format": self.output_format,
            "ssml_shape": "mstts:express-as > prosody",
            # The SSML itself contains user text, so only its length is exposed.
            "ssml_chars": len(self._ssml(request, effective)),
        }
        return preview

    async def stream(self, request: SynthesisRequest) -> AsyncIterator[bytes]:
        ready, missing = self.readiness()
        if not ready:
            raise ProviderError(
                provider=self.name,
                code="missing_credentials",
                message="Azure TTS 未就绪，请配置 AZURE_SPEECH_KEY 与 AZURE_SPEECH_REGION。",
                stage="provider_auth",
                details={"missing": missing},
            )
        effective = self._effective(request)
        headers = {
            "Ocp-Apim-Subscription-Key": str(self.api_key),
            "Content-Type": "application/ssml+xml",
            "X-Microsoft-OutputFormat": self.output_format,
            "User-Agent": "MoodVoice/1.0",
        }
        try:
            async for chunk in stream_audio(
                self.name,
                self._client,
                "POST",
                self._endpoint(),
                headers=headers,
                content=self._ssml(request, effective).encode("utf-8"),
                stage="provider_synthesis",
            ):
                yield chunk
        except ProviderError as exc:
            # Attach the effective style so a 400 can be triaged as a mapping
            # problem vs. an adapter/request problem without exposing SSML.
            exc.details.setdefault(
                "effective_style",
                {
                    "voice": effective["voice"],
                    "style": effective["style"],
                    "style_degree": effective["style_degree"],
                    "rate": effective["rate"],
                    "pitch": effective["pitch"],
                },
            )
            raise

    async def close(self) -> None:
        await self._client.aclose()


def _percent(value: float) -> str:
    pct = max(-50, min(100, round(value)))
    return f"{pct:+d}%"


def _clamp(value: object, low: float, high: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = 1.0
    return round(max(low, min(high, number)), 2)
