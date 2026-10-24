"""MiniMax (海螺) synchronous T2A adapter.

MiniMax returns hex audio in a JSON envelope. It is not a byte-streaming API
unless its SSE mode is used, so this adapter intentionally reports
``streaming=False``; the pipeline still starts writing as soon as the single
audio payload arrives.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any, ClassVar

import httpx

from ..errors import ProviderError
from .base import ProviderCapabilities, SynthesisRequest, TTSProvider
from .http import network_error, upstream_error

_ALLOWED_EMOTIONS = {
    "happy",
    "sad",
    "angry",
    "fearful",
    "disgusted",
    "surprised",
    "calm",
    "fluent",
    "whisper",
}


class MiniMaxProvider(TTSProvider):
    name: ClassVar[str] = "minimax"
    display_name: ClassVar[str] = "MiniMax 海螺语音"
    capabilities: ClassVar[ProviderCapabilities] = ProviderCapabilities(
        streaming=False,
        emotional_control="native_emotion",
        emotion_styles="voice_setting.emotion（happy/sad/angry/surprised/calm 等）",
        requires_key=True,
        data_destination="MiniMax API（用户自己的账户）",
        formats=("mp3", "wav", "flac"),
        notes="同步接口一次性返回 hex 音频；首响高于流式厂商。",
    )

    def __init__(
        self,
        config: Any,
        *,
        api_key: str | None,
        timeout_seconds: float = 60.0,
    ) -> None:
        super().__init__(config, timeout_seconds=timeout_seconds)
        self.api_key = api_key
        self.base_url = (
            getattr(config, "base_url", None) or "https://api.minimax.chat/v1/t2a_v2"
        ).rstrip("/")
        self.model = getattr(config, "model", None) or "speech-02-hd"
        self.voice_id = str(getattr(config, "voice_id", None) or "").strip()
        self.group_id = str(getattr(config, "group_id", None) or "").strip()
        self.format = str(getattr(config, "format", None) or "mp3").lower()
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
        if not self.api_key:
            missing.append("MINIMAX_API_KEY")
        if not self.voice_id:
            missing.append("providers.minimax.voice_id")
        return not missing, missing

    def _emotion(self, request: SynthesisRequest) -> str | None:
        value = str(request.style.get("emotion") or "").strip().lower()
        if not value or value == "neutral":
            return None
        if value not in _ALLOWED_EMOTIONS:
            raise ProviderError(
                provider=self.name,
                code="invalid_style",
                message="MiniMax 不支持该情绪枚举，请检查风格映射。",
                stage="style_resolution",
                details={"parameter": "emotion", "value": value[:30]},
            )
        return value

    def _payload(self, request: SynthesisRequest) -> dict[str, Any]:
        voice_setting: dict[str, Any] = {
            "voice_id": self.voice_id,
            "speed": round(max(0.5, min(2.0, request.speed)), 3),
            "vol": 1.0,
            "pitch": round(max(-12, min(12, float(request.style.get("pitch_semitones", 0))))),
        }
        emotion = self._emotion(request)
        if emotion:
            voice_setting["emotion"] = emotion
        return {
            "model": self.model,
            "text": request.text,
            "stream": False,
            "output_format": "hex",
            "voice_setting": voice_setting,
            "audio_setting": {
                "sample_rate": 32000,
                "bitrate": 128000,
                "format": self.format,
                "channel": 1,
            },
            "language_boost": getattr(self.config, "language_boost", None) or "Chinese",
        }

    def request_preview(self, request: SynthesisRequest) -> dict[str, Any]:
        payload = self._payload(request)
        preview = super().request_preview(request)
        preview["effective"] = {
            "model": payload["model"],
            "voice_id": payload["voice_setting"]["voice_id"],
            "speed": payload["voice_setting"]["speed"],
            "pitch": payload["voice_setting"]["pitch"],
            "emotion": payload["voice_setting"].get("emotion"),
            "format": self.format,
            "mode": "synchronous_hex",
        }
        return preview

    async def stream(self, request: SynthesisRequest) -> AsyncIterator[bytes]:
        ready, missing = self.readiness()
        if not ready:
            raise ProviderError(
                provider=self.name,
                code="missing_credentials",
                message="MiniMax 未就绪，请配置 MINIMAX_API_KEY 与 voice_id。",
                stage="provider_auth",
                details={"missing": missing},
            )
        params: dict[str, Any] = {}
        if self.group_id:
            params["GroupId"] = self.group_id
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        try:
            response = await self._client.post(
                self.base_url,
                headers=headers,
                params=params,
                json=self._payload(request),
                timeout=httpx.Timeout(self.timeout_seconds, connect=10.0),
            )
        except httpx.HTTPError as exc:
            raise network_error(self.name, exc, stage="provider_synthesis") from exc

        if response.status_code >= 400:
            raise upstream_error(self.name, response, stage="provider_synthesis")
        try:
            data = response.json()
        except ValueError as exc:
            raise ProviderError(
                provider=self.name,
                code="invalid_response",
                message="MiniMax 返回了非 JSON 响应。",
                stage="provider_synthesis",
                http_status=response.status_code,
            ) from exc
        if not isinstance(data, dict):
            raise ProviderError(
                provider=self.name,
                code="invalid_response",
                message="MiniMax 返回了非对象 JSON。",
                stage="provider_synthesis",
                http_status=response.status_code,
            )

        base_resp = data.get("base_resp") or {}
        status_code = int(base_resp.get("status_code", -1)) if isinstance(base_resp, dict) else -1
        trace_id = str(data.get("trace_id") or "")[:120] or None
        if status_code != 0:
            code = {
                1001: "timeout",
                1002: "rate_limited",
                1004: "auth_failed",
                2013: "provider_rejected",
            }.get(status_code, "upstream_error")
            message = {
                1001: "MiniMax 请求超时。",
                1002: "MiniMax 触发限流。",
                1004: "MiniMax 鉴权失败，请检查密钥。",
                2013: "MiniMax 拒绝了合成参数，请检查音色与情感映射。",
            }.get(status_code, "MiniMax 返回错误。")
            raise ProviderError(
                provider=self.name,
                code=code,
                message=message,
                stage="provider_synthesis",
                retryable=code in {"timeout", "rate_limited", "upstream_error"},
                request_id=trace_id,
                details={"vendor_status_code": status_code},
            )

        audio_hex = ((data.get("data") or {}).get("audio")) or ""
        if not audio_hex:
            raise ProviderError(
                provider=self.name,
                code="empty_audio",
                message="MiniMax 没有返回音频。",
                stage="provider_synthesis",
                request_id=trace_id,
            )
        try:
            audio = bytes.fromhex(str(audio_hex))
        except ValueError as exc:
            raise ProviderError(
                provider=self.name,
                code="invalid_response",
                message="MiniMax 音频编码无效。",
                stage="provider_synthesis",
                request_id=trace_id,
            ) from exc
        if not audio:
            raise ProviderError(
                provider=self.name,
                code="empty_audio",
                message="MiniMax 返回了空音频。",
                stage="provider_synthesis",
                request_id=trace_id,
            )
        yield audio

    async def close(self) -> None:
        await self._client.aclose()
