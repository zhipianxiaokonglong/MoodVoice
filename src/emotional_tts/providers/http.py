"""Shared HTTP streaming helpers.

No upstream response body is ever included in an error or logged: it can echo
user text, API keys or account data. We only retain a status code and the
vendor's request ID (when present).
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Mapping
from typing import Any

import httpx

from ..errors import ProviderError, provider_http_code


def request_id_of(response: httpx.Response) -> str | None:
    for header in ("x-request-id", "request-id", "x-amzn-requestid", "trace-id"):
        value = response.headers.get(header)
        if value:
            return value[:120]
    return None


def upstream_error(
    provider: str,
    response: httpx.Response,
    *,
    stage: str = "provider_request",
    details: Mapping[str, Any] | None = None,
) -> ProviderError:
    code, message, retryable = provider_http_code(response.status_code)
    extra = dict(details or {})
    content_type = response.headers.get("content-type")
    if content_type:
        extra.setdefault("upstream_content_type", content_type[:80])
    return ProviderError(
        provider=provider,
        code=code,
        message=message,
        stage=stage,
        retryable=retryable,
        http_status=response.status_code,
        request_id=request_id_of(response),
        details=extra,
    )


def network_error(provider: str, exc: Exception, *, stage: str) -> ProviderError:
    if isinstance(exc, (httpx.TimeoutException, asyncio.TimeoutError, TimeoutError)):
        code = "timeout"
        message = "连接厂商超时，请稍后重试或切换厂商。"
        retryable = True
    elif isinstance(exc, httpx.RequestError):
        code = "network_error"
        message = "无法连接厂商服务，请检查网络或本地服务地址。"
        retryable = True
    else:
        code = "adapter_error"
        message = "语音适配层发生异常。"
        retryable = False
    return ProviderError(
        provider=provider,
        code=code,
        message=message,
        stage=stage,
        retryable=retryable,
        details={"exception_type": type(exc).__name__},
    )


async def stream_audio(
    provider: str,
    client: httpx.AsyncClient,
    method: str,
    url: str,
    *,
    headers: Mapping[str, str],
    json_body: Mapping[str, Any] | None = None,
    content: bytes | str | None = None,
    params: Mapping[str, Any] | None = None,
    stage: str = "provider_request",
) -> AsyncIterator[bytes]:
    """Stream bytes from an HTTP endpoint with sanitized error mapping."""

    try:
        async with client.stream(
            method,
            url,
            headers=dict(headers),
            json=dict(json_body) if json_body is not None else None,
            content=content,
            params=dict(params) if params is not None else None,
            timeout=httpx.Timeout(60.0, connect=10.0),
        ) as response:
            if response.status_code >= 400:
                await response.aread()
                raise upstream_error(provider, response, stage=stage)
            content_type = response.headers.get("content-type", "")
            if "json" in content_type or "text" in content_type:
                # Some self-hosted gateways return JSON errors with HTTP 200.
                await response.aread()
                raise ProviderError(
                    provider=provider,
                    code="invalid_response",
                    message="厂商返回了 JSON/文本而不是音频流。",
                    stage=stage,
                    http_status=response.status_code,
                    request_id=request_id_of(response),
                    details={"upstream_content_type": content_type[:80]},
                )
            async for chunk in response.aiter_bytes():
                if chunk:
                    yield chunk
    except ProviderError:
        raise
    except httpx.HTTPError as exc:
        raise network_error(provider, exc, stage=stage) from exc
