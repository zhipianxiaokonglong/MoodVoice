"""Dynamic TTS provider backed by a user API connection."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from typing import Any, ClassVar

import httpx

from ..errors import ProviderError
from ..providers.azure import AzureProvider
from ..providers.base import ProviderCapabilities, TTSProvider
from ..providers.elevenlabs import ElevenLabsProvider
from ..providers.minimax import MiniMaxProvider
from ..providers.openai import OpenAIProvider
from .models import ConnectionRecord
from .security import is_local_host

_BUILDERS: dict[str, type[TTSProvider]] = {
    "openai": OpenAIProvider,
    "azure": AzureProvider,
    "elevenlabs": ElevenLabsProvider,
    "minimax": MiniMaxProvider,
}

# Vendor adapters name environment variables; a connection user only knows the
# form they filled in, so these labels are rewritten in readiness().
_MISSING_LABELS: dict[str, str] = {
    "OPENAI_API_KEY": "连接 API Key",
    "ELEVENLABS_API_KEY": "连接 API Key",
    "AZURE_SPEECH_KEY": "连接 API Key",
    "MINIMAX_API_KEY": "连接 API Key",
    "AZURE_SPEECH_REGION": "连接区域（region）",
}

_PROTOCOL_LABELS: dict[str, str] = {
    "openai": "OpenAI 兼容协议",
    "azure": "Azure Speech",
    "elevenlabs": "ElevenLabs",
    "minimax": "MiniMax",
}


class _Config:
    """Minimal attribute object accepted by the existing adapters.

    Each protocol interprets overlapping fields differently; only populate the
    fields its adapter actually reads.
    """

    def __init__(self, record: ConnectionRecord) -> None:
        self.enabled = True
        self.base_url = record.base_url
        self.voice = record.voice
        self.voice_id = record.voice
        self.region = record.region or "custom"
        self.model = record.model
        self.model_id = record.model
        self.response_format = record.response_format or "mp3"
        self.output_format = record.output_format
        self.format = record.format or "mp3"
        self.group_id = record.group_id
        if record.protocol == "azure":
            self.output_format = record.output_format or "audio-24khz-48kbitrate-mono-mp3"
        if record.protocol == "elevenlabs":
            self.model_id = record.model or "eleven_multilingual_v2"
            self.output_format = record.output_format or "mp3_44100_128"
        if record.protocol == "minimax":
            self.model = record.model or "speech-02-hd"
            self.voice_id = record.voice
            self.format = record.format or "mp3"
        if record.protocol == "openai":
            self.model = record.model or "gpt-4o-mini-tts"
            self.response_format = record.response_format or "mp3"


class ConnectionProvider(TTSProvider):
    kind: ClassVar[str] = "connection"

    def __init__(
        self,
        record: ConnectionRecord,
        *,
        api_key: str | None,
        timeout_seconds: float = 30.0,
        client_factory: Callable[[], httpx.AsyncClient] | None = None,
    ) -> None:
        self.record = record
        self.name = record.id
        self.display_name = record.label
        self.api_key = api_key
        builder = _BUILDERS.get(record.protocol)
        if builder is None:
            raise ProviderError(
                provider=record.id,
                code="invalid_connection",
                message="不支持的连接协议。",
                stage="connection",
            )
        config = _Config(record)
        keyless_local = (
            record.protocol == "openai" and not api_key and is_local_host(record.base_url)
        )
        self._delegate = builder(
            config,
            api_key=api_key,
            timeout_seconds=timeout_seconds,
            # Only the OpenAI-compatible protocol may be keyless, and only when
            # the endpoint is loopback: that is the documented local-gateway case.
            **({"allow_keyless": keyless_local} if record.protocol == "openai" else {}),
        )
        if client_factory is not None:
            self._delegate._client = client_factory()
        self.timeout_seconds = timeout_seconds
        self.keyless_local = keyless_local

    @property
    def style_family(self) -> str:
        return self.record.protocol

    @property
    def capabilities(self) -> ProviderCapabilities:  # type: ignore[override]
        base = self._delegate.capabilities
        # The protocol adapter names its default vendor; a user connection must
        # point at the endpoint the user actually configured.
        return replace(
            base,
            data_destination=f"{self.record.label}（{_PROTOCOL_LABELS[self.record.protocol]}）",
            external_egress=self.external_egress(),
        )

    def readiness(self) -> tuple[bool, list[str]]:
        """Report readiness, re-labelled for a user-supplied connection.

        The delegate is a vendor adapter that reports missing *environment*
        variables (``OPENAI_API_KEY``...). A user who filled in a connection form
        only knows about "the API key I typed", so those labels are rewritten to
        something actionable instead of pointing at ``.env``.
        """

        ready, missing = self._delegate.readiness()
        if ready:
            return True, []
        labels = [_MISSING_LABELS.get(item, item) for item in missing]
        return False, labels

    @property
    def audio_format(self) -> str:
        return self._delegate.audio_format

    def external_egress(self) -> bool:
        return not is_local_host(self.record.base_url)

    def decorate_style(self, style: dict[str, Any]) -> dict[str, Any]:
        merged = dict(style)
        if self.record.voice:
            if self.record.protocol == "elevenlabs" or self.record.protocol == "minimax":
                merged["voice_id"] = self.record.voice
            else:
                merged["voice"] = self.record.voice
        if self.record.model and self.record.protocol == "openai":
            merged["model"] = self.record.model
        return merged

    def request_preview(self, request) -> dict[str, Any]:
        preview = self._delegate.request_preview(request)
        preview["connection"] = {
            "id": self.record.id,
            "label": self.record.label,
            "protocol": self.record.protocol,
        }
        return preview

    def stream(self, request):
        return self._delegate.stream(request)

    async def close(self) -> None:
        await self._delegate.close()
