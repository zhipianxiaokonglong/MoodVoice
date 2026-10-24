"""Typed errors shared by the pipeline, adapters and API."""

from __future__ import annotations

from typing import Any


class MoodVoiceError(Exception):
    """Base error with a stable, public code."""

    code = "moodvoice_error"
    http_status = 500

    def __init__(self, message: str, *, code: str | None = None) -> None:
        super().__init__(message)
        self.message = message
        if code:
            self.code = code


class ConfigError(MoodVoiceError):
    code = "invalid_configuration"


class ValidationError(MoodVoiceError):
    code = "invalid_event"
    http_status = 422


class CacheError(MoodVoiceError):
    code = "cache_error"


class ProviderError(MoodVoiceError):
    """A vendor/adapter failure that is safe to expose.

    ``message`` is deliberately short and sanitized by the adapter. We never
    retain a raw upstream response body: it can contain request echoes, keys or
    user text.
    """

    code = "provider_error"

    def __init__(
        self,
        *,
        provider: str,
        code: str,
        message: str,
        stage: str,
        retryable: bool = False,
        http_status: int | None = None,
        request_id: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message, code=code)
        self.provider = provider
        self.stage = stage
        self.retryable = retryable
        self.http_status = http_status
        self.request_id = request_id
        self.details = details or {}

    @property
    def http_status_public(self) -> int:
        return self.http_status or 502

    def as_attempt(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "provider": self.provider,
            "code": self.code,
            "stage": self.stage,
            "message": self.message,
            "retryable": self.retryable,
        }
        if self.http_status is not None:
            data["upstream_status"] = self.http_status
        if self.request_id:
            data["request_id"] = self.request_id
        if self.details:
            data["details"] = self.details
        return data


def provider_http_code(status: int) -> tuple[str, str, bool]:
    """Map an upstream HTTP status to a stable error code.

    Returns (code, human readable message, retryable).
    """

    if status in (401, 403):
        return "auth_failed", "厂商拒绝了凭据，请检查本地 .env 中的密钥与权限。", False
    if status == 404:
        return "endpoint_not_found", "厂商接口或 Voice ID 不存在，请检查本地配置。", False
    if status == 408:
        return "upstream_timeout", "厂商请求超时。", True
    if status == 409:
        return "upstream_conflict", "厂商暂时无法处理该请求。", True
    if status == 422:
        return "provider_rejected", "厂商拒绝了合成参数，请检查音色与情感风格配置。", False
    if status == 429:
        return "rate_limited", "厂商限流，请稍后重试或切换厂商。", True
    if 500 <= status <= 599:
        return "upstream_error", "厂商服务暂时不可用。", True
    if 400 <= status <= 499:
        return "provider_rejected", "厂商拒绝了请求参数。", False
    return "unexpected_status", "厂商返回了非预期状态。", True
