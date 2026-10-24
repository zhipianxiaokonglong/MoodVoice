"""Generic local TTS HTTP adapter.

Designed for self-hosted GPT-SoVITS / CosyVoice / F5-TTS wrappers. The exact
request/response contract is documented in docs/PROVIDERS.md; no vendor SDK or
cloud credential is required.
"""

from __future__ import annotations

import base64
import binascii
from collections.abc import AsyncIterator
from typing import Any, ClassVar
from urllib.parse import urlsplit, urlunsplit

import httpx

from ..errors import ProviderError
from .base import ProviderCapabilities, SynthesisRequest, TTSProvider
from .http import network_error, request_id_of, upstream_error

_ALLOWED_FORMATS = {"mp3", "wav", "ogg", "opus", "flac"}
_CONTENT_TYPE_FORMAT = {
    "audio/mpeg": {"mp3"},
    "audio/mp3": {"mp3"},
    "audio/wav": {"wav"},
    "audio/x-wav": {"wav"},
    "audio/wave": {"wav"},
    "audio/ogg": {"ogg", "opus"},
    "audio/opus": {"opus", "ogg"},
    "audio/flac": {"flac"},
    "audio/x-flac": {"flac"},
    "application/octet-stream": _ALLOWED_FORMATS,
    "binary/octet-stream": _ALLOWED_FORMATS,
}


class LocalHTTPProvider(TTSProvider):
    name: ClassVar[str] = "local"
    display_name: ClassVar[str] = "本地 TTS（HTTP）"
    capabilities: ClassVar[ProviderCapabilities] = ProviderCapabilities(
        streaming=True,
        emotional_control="style_payload",
        emotion_styles="把 emotion/tone/speed 传给本地服务；表达力取决于本地模型",
        requires_key=False,
        data_destination="用户指定的本地 HTTP 服务（不经 MoodVoice 转发）",
        formats=("wav", "mp3", "ogg", "opus", "flac"),
        notes="用于 GPT-SoVITS / CosyVoice / F5-TTS 的自定义封装。",
    )

    def __init__(
        self,
        config: Any,
        *,
        api_key: str | None = None,
        timeout_seconds: float = 60.0,
    ) -> None:
        super().__init__(config, timeout_seconds=timeout_seconds)
        self.base_url = str(getattr(config, "base_url", None) or "").strip()
        self.voice = str(getattr(config, "voice", None) or "default")
        self.format = str(getattr(config, "format", None) or "wav").lower()
        self.api_key = api_key
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(timeout_seconds, connect=10.0),
            follow_redirects=False,
        )

    @property
    def audio_format(self) -> str:
        return self.format

    def readiness(self) -> tuple[bool, list[str]]:
        missing: list[str] = []
        if not getattr(self.config, "enabled", True):
            missing.append("disabled_in_config")
        if not self.base_url:
            missing.append("LOCAL_TTS_URL")
        else:
            parts = urlsplit(self.base_url)
            if parts.scheme not in {"http", "https"} or not parts.hostname:
                missing.append("valid_LOCAL_TTS_URL")
        if self.format not in _ALLOWED_FORMATS:
            missing.append("supported_format")
        return not missing, missing

    def _safe_endpoint(self) -> str:
        parts = urlsplit(self.base_url)
        # Never surface query strings or userinfo in diagnostics: they may
        # contain tokens or local credentials.
        host = parts.hostname or ""
        if ":" in host and not host.startswith("["):
            host = f"[{host}]"
        netloc = f"{host}:{parts.port}" if parts.port else host
        return urlunsplit((parts.scheme, netloc, parts.path, "", ""))

    def _payload(self, request: SynthesisRequest) -> dict[str, Any]:
        return {
            "text": request.text,
            "voice": request.voice or self.voice,
            "language": request.language,
            "emotion": request.emotion,
            "style": {
                "tone": request.tone,
                "speed": round(request.speed, 3),
                "pitch_semitones": round(float(request.style.get("pitch_semitones", 0)), 3),
                "volume_db": round(float(request.style.get("volume_db", 0)), 3),
            },
            "format": self.format,
        }

    def request_preview(self, request: SynthesisRequest) -> dict[str, Any]:
        preview = super().request_preview(request)
        preview["effective"] = {
            "endpoint": self._safe_endpoint(),
            "voice": request.voice or self.voice,
            "format": self.format,
            "emotion": request.emotion,
            "speed": round(request.speed, 3),
        }
        return preview

    async def stream(self, request: SynthesisRequest) -> AsyncIterator[bytes]:
        ready, missing = self.readiness()
        if not ready:
            raise ProviderError(
                provider=self.name,
                code="missing_credentials",
                message="本地 TTS 未就绪，请通过 LOCAL_TTS_URL 配置本地服务地址。",
                stage="provider_configuration",
                details={"missing": missing},
            )
        headers = {
            "Content-Type": "application/json",
            "Accept": "audio/*, application/json",
        }
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        try:
            async with self._client.stream(
                "POST",
                self.base_url,
                headers=headers,
                json=self._payload(request),
                timeout=httpx.Timeout(self.timeout_seconds, connect=10.0),
            ) as response:
                if response.status_code >= 400:
                    await response.aread()
                    raise upstream_error(self.name, response, stage="provider_synthesis")
                content_type = (
                    response.headers.get("content-type", "").split(";")[0].strip().lower()
                )
                if content_type == "application/json":
                    raw = await response.aread()
                    audio = self._decode_json_audio(raw, content_type)
                    if audio:
                        yield audio
                    return
                if content_type and content_type not in _CONTENT_TYPE_FORMAT:
                    await response.aread()
                    raise ProviderError(
                        provider=self.name,
                        code="invalid_response",
                        message="本地服务返回了非音频 Content-Type。",
                        stage="provider_synthesis",
                        http_status=response.status_code,
                        request_id=request_id_of(response),
                        details={"upstream_content_type": content_type[:80]},
                    )
                allowed = _CONTENT_TYPE_FORMAT.get(content_type, _ALLOWED_FORMATS)
                if self.format not in allowed:
                    await response.aread()
                    raise ProviderError(
                        provider=self.name,
                        code="format_mismatch",
                        message="本地服务音频格式与配置不一致，请检查 providers.local.format。",
                        stage="provider_synthesis",
                        http_status=response.status_code,
                        request_id=request_id_of(response),
                        details={
                            "configured_format": self.format,
                            "upstream_content_type": content_type[:80],
                        },
                    )
                async for chunk in response.aiter_bytes():
                    if chunk:
                        yield chunk
        except ProviderError:
            raise
        except httpx.HTTPError as exc:
            raise network_error(self.name, exc, stage="provider_synthesis") from exc

    def _decode_json_audio(self, raw: bytes, content_type: str) -> bytes:
        import json

        try:
            payload = json.loads(raw)
        except (ValueError, UnicodeDecodeError) as exc:
            raise ProviderError(
                provider=self.name,
                code="invalid_response",
                message="本地服务返回了无效 JSON。",
                stage="provider_synthesis",
                details={"upstream_content_type": content_type[:80]},
            ) from exc
        if not isinstance(payload, dict):
            raise ProviderError(
                provider=self.name,
                code="invalid_response",
                message="本地服务 JSON 必须是对象。",
                stage="provider_synthesis",
            )
        response_format = str(payload.get("format") or self.format).lower()
        if response_format not in _ALLOWED_FORMATS:
            raise ProviderError(
                provider=self.name,
                code="invalid_response",
                message="本地服务返回了不支持的格式。",
                stage="provider_synthesis",
            )
        if response_format != self.format:
            raise ProviderError(
                provider=self.name,
                code="format_mismatch",
                message="本地服务返回格式与配置不一致，请检查 providers.local.format。",
                stage="provider_synthesis",
                details={"configured_format": self.format, "response_format": response_format},
            )
        encoded = payload.get("audio_base64") or payload.get("audio_b64")
        if not encoded:
            raise ProviderError(
                provider=self.name,
                code="invalid_response",
                message="本地服务 JSON 缺少 audio_base64。",
                stage="provider_synthesis",
            )
        try:
            data = base64.b64decode(str(encoded), validate=True)
        except (binascii.Error, ValueError) as exc:
            raise ProviderError(
                provider=self.name,
                code="invalid_response",
                message="本地服务音频 base64 无效。",
                stage="provider_synthesis",
            ) from exc
        if not data:
            raise ProviderError(
                provider=self.name,
                code="empty_audio",
                message="本地服务返回了空音频。",
                stage="provider_synthesis",
            )
        return data

    async def close(self) -> None:
        await self._client.aclose()
