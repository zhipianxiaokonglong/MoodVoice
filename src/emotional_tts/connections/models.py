"""Models for user-supplied TTS API connections."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator

Protocol = Literal["openai", "azure", "elevenlabs", "minimax"]
DiscoverProtocol = Literal["auto", "openai", "azure", "elevenlabs", "minimax"]


class DiscoverRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    base_url: str = Field(min_length=1, max_length=2048)
    api_key: SecretStr | None = None
    protocol: DiscoverProtocol = "auto"
    group_id: str | None = Field(default=None, max_length=120)
    allow_insecure_http: bool = False

    @field_validator("base_url")
    @classmethod
    def _strip(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("base_url must not be blank")
        return value


class TestConnectionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    base_url: str = Field(min_length=1, max_length=2048)
    api_key: SecretStr | None = None
    protocol: Protocol
    model: str | None = Field(default=None, max_length=200)
    voice: str | None = Field(default=None, max_length=200)
    region: str | None = Field(default=None, max_length=80)
    group_id: str | None = Field(default=None, max_length=120)
    response_format: str | None = Field(default=None, max_length=40)
    output_format: str | None = Field(default=None, max_length=80)
    format: str | None = Field(default=None, max_length=20)
    allow_insecure_http: bool = False
    text: str = Field(
        default="你好，这是一次 API 接入测试。",
        min_length=1,
        max_length=200,
    )


class SaveConnectionRequest(TestConnectionRequest):
    label: str = Field(min_length=1, max_length=60)
    api_key: SecretStr | None = None


class ModelOption(BaseModel):
    id: str
    name: str = ""
    details: dict[str, Any] = Field(default_factory=dict)


class VoiceOption(BaseModel):
    id: str
    name: str = ""
    locale: str | None = None
    gender: str | None = None
    styles: list[str] = Field(default_factory=list)
    details: dict[str, Any] = Field(default_factory=dict)


class DiscoveryResult(BaseModel):
    ok: bool
    protocol: Protocol
    base_url: str
    models: list[ModelOption] = Field(default_factory=list)
    voices: list[VoiceOption] = Field(default_factory=list)
    capabilities: dict[str, Any] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)


class TestResult(BaseModel):
    ok: bool
    protocol: Protocol
    first_chunk_ms: float | None = None
    total_ms: float | None = None
    audio_ref: str | None = None
    audio_mime: str | None = None
    audio_format: str | None = None
    audio_bytes: int = 0
    external_egress: bool = True
    warnings: list[str] = Field(default_factory=list)


class LastTest(BaseModel):
    ok: bool
    at: str
    first_chunk_ms: float | None = None
    total_ms: float | None = None
    code: str | None = None
    message: str | None = None


class ConnectionRecord(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str
    label: str
    protocol: Protocol
    base_url: str
    model: str | None = None
    voice: str | None = None
    region: str | None = None
    group_id: str | None = None
    response_format: str | None = None
    output_format: str | None = None
    format: str | None = None
    allow_insecure_http: bool = False
    created_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())
    updated_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())
    last_test: LastTest | None = None
    capabilities: dict[str, Any] = Field(default_factory=dict)

    def public_dict(self, *, key_configured: bool, ready: bool) -> dict[str, Any]:
        data = self.model_dump(mode="json", exclude={"last_test"})
        data["last_test"] = self.last_test.model_dump(mode="json") if self.last_test else None
        data["key_configured"] = key_configured
        data["ready"] = ready
        return data
