"""Provider interface.

Adapters are deliberately small: build a vendor request, stream audio bytes,
map errors. Emotion mapping and routing live above this layer.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass, field
from typing import Any, ClassVar


@dataclass(frozen=True)
class ProviderCapabilities:
    streaming: bool
    emotional_control: str
    emotion_styles: str
    requires_key: bool
    data_destination: str
    external_egress: bool = True
    formats: tuple[str, ...] = ("mp3",)
    notes: str = ""

    def public(self) -> dict[str, Any]:
        return {
            "streaming": self.streaming,
            "emotional_control": self.emotional_control,
            "emotion_styles": self.emotion_styles,
            "requires_key": self.requires_key,
            "data_destination": self.data_destination,
            "external_egress": self.external_egress,
            "formats": list(self.formats),
            "notes": self.notes,
        }


@dataclass(frozen=True)
class SynthesisRequest:
    text: str
    language: str
    emotion: str
    tone: str
    speed: float
    voice: str | None
    style: Mapping[str, Any] = field(default_factory=dict)
    output_format: str = "mp3"

    @property
    def text_chars(self) -> int:
        return len(self.text)


class TTSProvider(ABC):
    name: ClassVar[str] = "base"
    display_name: ClassVar[str] = "Base"
    kind: ClassVar[str] = "builtin"
    capabilities: ClassVar[ProviderCapabilities]

    def __init__(self, config: Any, *, timeout_seconds: float = 30.0) -> None:
        self.config = config
        self.timeout_seconds = timeout_seconds

    def readiness(self) -> tuple[bool, list[str]]:
        """Return (ready, missing requirement labels). Never return secrets."""

        return True, []

    @property
    def ready(self) -> bool:
        return self.readiness()[0]

    @property
    def audio_format(self) -> str:
        """Actual format of ``stream()`` bytes; must match the HTTP headers."""

        return "mp3"

    def external_egress(self) -> bool:
        """Whether this provider sends text outside the current machine."""

        return self.capabilities.external_egress

    @property
    def style_family(self) -> str:
        """Key used by emotion_styles.yaml provider_overrides.

        Built-ins use their own name. User API connections override this with
        the underlying protocol (openai/azure/elevenlabs/minimax).
        """

        return self.name

    def decorate_style(self, style: dict[str, Any]) -> dict[str, Any]:
        """Allow a provider/connection to pin user-selected voice/model.

        The default keeps the YAML mapping untouched; connections override it
        so a voice chosen in the UI is not silently replaced by the style map.
        """

        return style

    def resolve_voice(self, request: SynthesisRequest) -> str | None:
        return request.voice

    def request_preview(self, request: SynthesisRequest) -> dict[str, Any]:
        """Non-secret request parameters for diagnostics / root-cause triage."""

        return {
            "provider": self.name,
            "voice": self.resolve_voice(request),
            "text_chars": request.text_chars,
            "language": request.language,
            "output_format": request.output_format,
            "emotion": request.emotion,
            "speed": request.speed,
            "tone": request.tone,
            "provider_params": dict(request.style),
            "effective": {
                "voice": self.resolve_voice(request),
                "emotion": request.emotion,
                "speed": request.speed,
                "output_format": request.output_format,
            },
            "capabilities": self.capabilities.public(),
        }

    @abstractmethod
    def stream(self, request: SynthesisRequest) -> AsyncIterator[bytes]:
        """Yield compressed audio bytes in the configured format."""

        raise NotImplementedError

    async def close(self) -> None:
        return None

    def public_descriptor(self) -> dict[str, Any]:
        ready, missing = self.readiness()
        capabilities = self.capabilities.public()
        capabilities["external_egress"] = self.external_egress()
        return {
            "id": self.name,
            "display_name": self.display_name,
            "kind": self.kind,
            "ready": ready,
            "missing": missing,
            "capabilities": capabilities,
        }
