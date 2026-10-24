"""edge-tts adapter (keyless online channel, prosody-only emotion)."""

from __future__ import annotations

import re
from collections.abc import AsyncIterator
from typing import Any, ClassVar

import edge_tts
from edge_tts.exceptions import EdgeTTSException, NoAudioReceived

from ..errors import ProviderError
from ..security import sanitize_message
from .base import ProviderCapabilities, SynthesisRequest, TTSProvider
from .http import network_error

_RATE_RE = re.compile(r"^[+-]\d{1,3}%$")
_PITCH_RE = re.compile(r"^[+-]\d{1,3}(Hz|st|%)$")
_VOLUME_RE = re.compile(r"^[+-]\d{1,3}%$")


class EdgeProvider(TTSProvider):
    name: ClassVar[str] = "edge"
    display_name: ClassVar[str] = "edge-tts（免密钥）"
    capabilities: ClassVar[ProviderCapabilities] = ProviderCapabilities(
        streaming=True,
        emotional_control="prosody",
        emotion_styles="通过音色、语速、音高和音量表达情绪；不支持 Azure 风格标签",
        requires_key=False,
        data_destination="微软 Edge 在线朗读服务（文本会离开本机）",
        formats=("mp3",),
        notes="无需账号，适合开箱即用 Demo；完全本地请使用 local 适配器。",
    )

    def readiness(self) -> tuple[bool, list[str]]:
        if not getattr(self.config, "enabled", True):
            return False, ["disabled_in_config"]
        return True, []

    def _params(self, request: SynthesisRequest) -> dict[str, str]:
        style = request.style
        voice = request.voice or getattr(self.config, "voice", None) or "zh-CN-XiaoxiaoNeural"
        rate = str(style.get("rate") or _percent((request.speed - 1.0) * 100))
        pitch = str(style.get("pitch") or _hz(request.style.get("pitch_semitones", 0)))
        volume = str(style.get("volume") or _percent(float(style.get("volume_db", 0)) * 3))
        for label, value, pattern in (
            ("rate", rate, _RATE_RE),
            ("pitch", pitch, _PITCH_RE),
            ("volume", volume, _VOLUME_RE),
        ):
            if not pattern.fullmatch(value):
                raise ProviderError(
                    provider=self.name,
                    code="invalid_style",
                    message=f"edge-tts 的 {label} 参数格式无效。",
                    stage="style_resolution",
                    details={"parameter": label, "value": value[:30]},
                )
        return {"voice": voice, "rate": rate, "pitch": pitch, "volume": volume}

    def request_preview(self, request: SynthesisRequest) -> dict[str, Any]:
        preview = super().request_preview(request)
        preview["effective"] = self._params(request)
        preview["text_chars"] = request.text_chars
        return preview

    def stream(self, request: SynthesisRequest) -> AsyncIterator[bytes]:
        params = self._params(request)

        async def _iterate() -> AsyncIterator[bytes]:
            stream = None
            try:
                communicate = edge_tts.Communicate(
                    request.text,
                    params["voice"],
                    rate=params["rate"],
                    pitch=params["pitch"],
                    volume=params["volume"],
                    connect_timeout=10,
                    receive_timeout=60,
                )
                stream = communicate.stream()
                async for event in stream:
                    if event.get("type") == "audio" and event.get("data"):
                        yield event["data"]
            except NoAudioReceived as exc:
                raise ProviderError(
                    provider=self.name,
                    code="empty_audio",
                    message="edge-tts 没有返回音频，可能是文本或网络异常。",
                    stage="provider_synthesis",
                    retryable=True,
                ) from exc
            except ValueError as exc:
                # edge-tts raises ValueError for an unknown voice before it
                # opens the websocket. This is a style/config problem, not a
                # network problem.
                raise ProviderError(
                    provider=self.name,
                    code="invalid_style",
                    message="edge-tts 不认识的音色，请检查风格映射中的 voice。",
                    stage="style_resolution",
                    details={
                        "parameter": "voice",
                        "value": str(params["voice"])[:80],
                    },
                ) from exc
            except EdgeTTSException as exc:
                raise ProviderError(
                    provider=self.name,
                    code="edge_tts_error",
                    message=sanitize_message(f"edge-tts 合成失败: {type(exc).__name__}"),
                    stage="provider_synthesis",
                    retryable=True,
                ) from exc
            except Exception as exc:  # aiohttp/network/socket errors
                raise network_error(self.name, exc, stage="provider_synthesis") from exc
            finally:
                aclose = getattr(stream, "aclose", None)
                if aclose is not None:
                    try:
                        await aclose()
                    except Exception:
                        pass

        return _iterate()


def _percent(value: float) -> str:
    pct = max(-50, min(100, round(value)))
    return f"{pct:+d}%"


def _hz(value: object) -> str:
    try:
        semitones = float(value)
    except (TypeError, ValueError):
        semitones = 0.0
    # A deliberately modest mapping; per-emotion YAML can override it.
    hz = max(-20, min(20, round(semitones * 4)))
    return f"{hz:+d}Hz"
