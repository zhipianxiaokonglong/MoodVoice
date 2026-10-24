"""Public event contract.

The four required output fields stay contract-compatible:
``event_id``, ``text``, ``audio_ref`` and ``style``. Additional fields carry
rendering, routing, diagnostics and latency data for the assembly/UI layer.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


class InputTags(BaseModel):
    model_config = ConfigDict(extra="ignore")

    emotion: str = "neutral"
    type: str = "text"
    intent: str = "reply"

    @field_validator("emotion", "type", "intent", mode="before")
    @classmethod
    def _coerce_strings(cls, value: Any) -> str:
        if value is None:
            return ""
        return str(value).strip()


class InputEvent(BaseModel):
    model_config = ConfigDict(extra="ignore")

    event_id: UUID
    session_id: UUID
    content: str = Field(min_length=1, max_length=20000)
    stream: bool = True
    tags: InputTags = Field(default_factory=InputTags)
    # Optional extension for the demo/API. The upstream contract does not need
    # to send it; normal routing comes from config.
    provider: str | None = Field(default=None, max_length=32)

    @field_validator("content")
    @classmethod
    def _content_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("content must not be blank")
        return value

    @field_validator("provider")
    @classmethod
    def _normalize_provider(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip().lower()
        return value or None


class StylePayload(BaseModel):
    emotion: str
    label: str
    speed: float
    tone: str
    description: str = ""
    pitch_semitones: float = 0
    source_tag: str = ""
    mapping_hit: bool = True


class RenderBlock(BaseModel):
    kind: str
    content: str
    language: str | None = None
    ordinal: int | None = None


class RenderPayload(BaseModel):
    type: str
    mode: str
    language: str | None = None
    blocks: list[RenderBlock] = Field(default_factory=list)
    copyable: bool = False


class RoutePayload(BaseModel):
    intent: str
    destination: str
    synthesize: bool
    reason: str


class DegradationPayload(BaseModel):
    code: str
    stage: str
    message: str
    attempts: list[dict[str, Any]] = Field(default_factory=list)


class TimingPayload(BaseModel):
    first_chunk_ms: float | None = None
    total_ms: float | None = None


class DiagnosticsPayload(BaseModel):
    emotion_mapping: dict[str, Any] = Field(default_factory=dict)
    provider_params: dict[str, Any] = Field(default_factory=dict)
    provider_attempts: list[dict[str, Any]] = Field(default_factory=list)
    adapter: dict[str, Any] = Field(default_factory=dict)
    speech: dict[str, Any] = Field(default_factory=dict)
    privacy: dict[str, Any] = Field(default_factory=dict)


class OutputEvent(BaseModel):
    # Required output contract
    event_id: str
    text: str
    audio_ref: str | None
    style: StylePayload

    # Additive fields for assembly/UI and operations
    schema_version: str = "1.0"
    session_id: str
    speech_text: str = ""
    audio_mime: str | None = None
    audio_format: str | None = None
    render: RenderPayload
    route: RoutePayload
    provider: str | None = None
    requested_provider: str = "auto"
    status: str = "synthesized"
    degraded: bool = False
    degradation: DegradationPayload | None = None
    diagnostics: DiagnosticsPayload = Field(default_factory=DiagnosticsPayload)
    timings: TimingPayload = Field(default_factory=TimingPayload)
    created_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())

    def to_json_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="json")
