"""Connection discovery, testing, persistence and provider registration."""

from __future__ import annotations

import asyncio
import logging
import secrets
import time
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

import httpx

from ..cache import AudioCache, mime_for_format, normalize_audio_format
from ..config import AppConfig
from ..errors import CacheError, ConnectionFailure, ProviderError
from ..providers.base import SynthesisRequest
from ..providers.registry import ProviderRegistry
from ..security import register_secrets, sanitize_message
from ..styles import StyleBook
from .discovery import auto_discover, discover, normalize_base_url
from .models import (
    ConnectionRecord,
    DiscoverRequest,
    DiscoveryResult,
    LastTest,
    Protocol,
    SaveConnectionRequest,
    TestConnectionRequest,
    TestResult,
)
from .provider import ConnectionProvider
from .security import is_local_host
from .store import ConnectionStore, KeyringKeyStore, KeyStore

logger = logging.getLogger(__name__)

_TEST_TIMEOUT = 35.0
_FIRST_CHUNK_TIMEOUT = 15.0


class ConnectionManager:
    def __init__(
        self,
        config: AppConfig,
        *,
        styles: StyleBook,
        cache: AudioCache,
        registry: ProviderRegistry,
        store: ConnectionStore | None = None,
        key_store: KeyStore | None = None,
        client_factory: Callable[[], httpx.AsyncClient] | None = None,
        timeout_seconds: float = 20.0,
    ) -> None:
        self.config = config
        self.styles = styles
        self.cache = cache
        self.registry = registry
        self.store = store or ConnectionStore(config.data_path / "connections.json")
        self.key_store = key_store or KeyringKeyStore()
        self.client_factory = client_factory
        self.timeout_seconds = timeout_seconds

    # ------------------------------------------------------------------ load
    def bootstrap(self) -> None:
        for record in self.store.list():
            key = self.key_store.get(record.id)
            if key:
                register_secrets([key])
            try:
                provider = ConnectionProvider(
                    record,
                    api_key=key,
                    timeout_seconds=self.config.tts.total_timeout_seconds,
                    client_factory=self.client_factory,
                )
            except Exception as exc:
                logger.warning(
                    "connection %s skipped during load: %s",
                    record.id,
                    type(exc).__name__,
                )
                continue
            self.registry.add(provider)

    # --------------------------------------------------------------- discovery
    async def discover(self, request: DiscoverRequest) -> DiscoveryResult:
        key = _secret(request.api_key)
        try:
            async with self._client() as client:
                if request.protocol == "auto":
                    return await auto_discover(
                        client,
                        base_url=request.base_url,
                        api_key=key,
                        group_id=request.group_id,
                        allow_insecure_http=request.allow_insecure_http,
                    )
                return await discover(
                    client,
                    base_url=request.base_url,
                    protocol=request.protocol,
                    api_key=key,
                    group_id=request.group_id,
                    allow_insecure_http=request.allow_insecure_http,
                )
        except ConnectionFailure:
            raise
        except Exception as exc:  # defensive: never leak request details
            logger.error("connection discovery failed: %s", type(exc).__name__)
            raise ConnectionFailure(
                "API 探索失败，请检查地址与网络。",
                code="discovery_failed",
                stage="discover",
            ) from exc

    def _client(self) -> httpx.AsyncClient:
        if self.client_factory is not None:
            return self.client_factory()
        return httpx.AsyncClient(
            timeout=httpx.Timeout(self.timeout_seconds, connect=8.0),
            follow_redirects=False,
        )

    # ------------------------------------------------------------------- test
    async def test(self, request: TestConnectionRequest) -> TestResult:
        key = _secret(request.api_key)
        record = self._record_from(
            request,
            connection_id=f"test_{secrets.token_hex(5)}",
            label="test",
        )
        provider = self._provider(record, key)
        if not provider.ready:
            missing = provider.readiness()[1]
            await provider.close()
            raise ConnectionFailure(
                "连接未就绪：" + ", ".join(missing),
                code="connection_not_ready",
                stage="connection",
                http_status=400,
            )
        fmt = normalize_audio_format(provider.audio_format)
        style = self.styles.provider_params(self.styles.resolve("neutral"), provider.style_family)
        style = provider.decorate_style(style)
        synthesis = SynthesisRequest(
            text=request.text,
            language=self.config.app.language,
            emotion="neutral",
            tone="自然平稳",
            speed=1.0,
            voice=style.get("voice"),
            style=style,
            output_format=fmt,
        )
        writer = self.cache.lease(fmt)
        iterator = None
        started = time.perf_counter()
        first_ms: float | None = None
        try:
            iterator = provider.stream(synthesis)
            first = await _first_chunk(iterator, provider, timeout=_FIRST_CHUNK_TIMEOUT)
            first_ms = round((time.perf_counter() - started) * 1000, 1)
            writer.append(first)
            deadline = started + _TEST_TIMEOUT
            while True:
                remaining = deadline - time.perf_counter()
                if remaining <= 0:
                    raise ProviderError(
                        provider=provider.name,
                        code="timeout",
                        message="测试合成超时。",
                        stage="provider_synthesis",
                        retryable=True,
                    )
                try:
                    chunk = await asyncio.wait_for(iterator.__anext__(), timeout=remaining)
                except StopAsyncIteration:
                    break
                except TimeoutError as exc:
                    raise ProviderError(
                        provider=provider.name,
                        code="timeout",
                        message="测试合成超时。",
                        stage="provider_synthesis",
                        retryable=True,
                    ) from exc
                if chunk:
                    writer.append(chunk)
            cached = writer.finalize()
            total_ms = round((time.perf_counter() - started) * 1000, 1)
            return TestResult(
                ok=True,
                protocol=record.protocol,
                first_chunk_ms=first_ms,
                total_ms=total_ms,
                audio_ref=self.cache.ref_for(cached.filename),
                audio_mime=mime_for_format(cached.fmt),
                audio_format=cached.fmt,
                audio_bytes=cached.size,
                external_egress=provider.external_egress(),
            )
        except ProviderError as exc:
            writer.abort()
            raise _connection_failure(exc) from exc
        except CacheError as exc:
            writer.abort()
            raise ConnectionFailure(
                "测试音频写入失败，请检查本地磁盘。",
                code="cache_error",
                stage="cache",
            ) from exc
        finally:
            if iterator is not None:
                aclose = getattr(iterator, "aclose", None)
                if aclose is not None:
                    try:
                        await aclose()
                    except Exception:
                        pass
            await provider.close()

    # ------------------------------------------------------------------- save
    async def save(self, request: SaveConnectionRequest) -> ConnectionRecord:
        key = _secret(request.api_key)
        record = self._record_from(
            request,
            connection_id=f"api_{secrets.token_hex(6)}",
            label=request.label.strip(),
        )
        provider = self._provider(record, key)
        try:
            ready, missing = provider.readiness()
            if not ready:
                raise ConnectionFailure(
                    "连接未就绪：" + ", ".join(missing),
                    code="connection_not_ready",
                    stage="connection",
                )
        finally:
            await provider.close()

        try:
            if key:
                self.key_store.set(record.id, key)
            self.store.put(record)
            self._register(record, key)
        except Exception:
            self.key_store.delete(record.id)
            self.store.delete(record.id)
            self.registry.remove(record.id)
            raise

        if key:
            register_secrets([key])
        return record

    async def remove_connection(self, connection_id: str) -> bool:
        record = self.store.get(connection_id)
        if record is None:
            return False
        self.store.delete(connection_id)
        self.key_store.delete(connection_id)
        provider = self.registry.remove(connection_id)
        if provider is not None:
            await provider.close()
        return True

    async def clear_all(self) -> int:
        records = self.store.list()
        for record in records:
            self.store.delete(record.id)
            self.key_store.delete(record.id)
            provider = self.registry.remove(record.id)
            if provider is not None:
                await provider.close()
        return len(records)

    async def reload(self) -> int:
        """Re-read ``connections.json`` from disk and re-register providers."""

        for name, provider in list(self.registry.providers.items()):
            if getattr(provider, "kind", "builtin") != "connection":
                continue
            self.registry.remove(name)
            await provider.close()
        self.store = ConnectionStore(self.store.path)
        self.bootstrap()
        return len(self.store.list())

    # --------------------------------------------------------------- listing
    def public_connections(self) -> list[dict[str, Any]]:
        result = []
        for record in self.store.list():
            key = self.key_store.get(record.id)
            provider = self.registry.get(record.id)
            ready = bool(provider and provider.readiness()[0])
            result.append(record.public_dict(key_configured=bool(key), ready=ready))
        return result

    def key_storage(self) -> dict[str, Any]:
        persistent = bool(getattr(self.key_store, "persistent", False))
        return {
            "persistent": persistent,
            "backend": "os_keyring" if persistent else "session_only",
            "message": (
                "密钥保存在操作系统凭据库"
                if persistent
                else "当前密钥库不可用，密钥仅保留在本次进程会话"
            ),
        }

    # --------------------------------------------------------------- internal
    def _register(self, record: ConnectionRecord, key: str | None) -> None:
        existing = self.registry.get(record.id)
        if existing is not None:
            self.registry.remove(record.id)
        provider = ConnectionProvider(
            record,
            api_key=key,
            timeout_seconds=self.config.tts.total_timeout_seconds,
            client_factory=self.client_factory,
        )
        self.registry.add(provider)

    def _provider(self, record: ConnectionRecord, key: str | None) -> ConnectionProvider:
        return ConnectionProvider(
            record,
            api_key=key,
            timeout_seconds=self.config.tts.total_timeout_seconds,
            client_factory=self.client_factory,
        )

    def _record_from(
        self,
        request: TestConnectionRequest | SaveConnectionRequest,
        *,
        connection_id: str,
        label: str,
    ) -> ConnectionRecord:
        protocol = request.protocol
        base_url = normalize_base_url(
            protocol,
            request.base_url,
            allow_insecure_http=request.allow_insecure_http,
        )
        model = (request.model or "").strip() or None
        voice = (request.voice or "").strip() or None
        region = (request.region or "").strip() or None
        group_id = (request.group_id or "").strip() or None
        response_format = (request.response_format or "").strip() or None
        output_format = (request.output_format or "").strip() or None
        audio_format = (request.format or "").strip().lower() or None

        if protocol == "openai":
            model = model or "gpt-4o-mini-tts"
            voice = voice or "coral"
            response_format = response_format or "mp3"
        elif protocol == "azure":
            if not voice:
                raise ConnectionFailure(
                    "Azure 连接需要选择音色。",
                    code="missing_voice",
                    stage="connection",
                )
            output_format = output_format or "audio-24khz-48kbitrate-mono-mp3"
            region = region or "custom"
        elif protocol == "elevenlabs":
            if not voice:
                raise ConnectionFailure(
                    "ElevenLabs 连接需要选择 Voice ID。",
                    code="missing_voice",
                    stage="connection",
                )
            model = model or "eleven_multilingual_v2"
            output_format = output_format or "mp3_44100_128"
        elif protocol == "minimax":
            if not voice:
                raise ConnectionFailure(
                    "MiniMax 连接需要选择音色。",
                    code="missing_voice",
                    stage="connection",
                )
            model = model or "speech-02-hd"
            audio_format = audio_format or "mp3"
        else:
            raise ConnectionFailure("未知协议。", code="invalid_protocol", stage="connection")

        now = _now()
        return ConnectionRecord(
            id=connection_id,
            label=label,
            protocol=protocol,
            base_url=base_url,
            model=model,
            voice=voice,
            region=region,
            group_id=group_id,
            response_format=response_format,
            output_format=output_format,
            format=audio_format,
            allow_insecure_http=request.allow_insecure_http,
            created_at=now,
            updated_at=now,
            last_test=None,
            capabilities=self._capabilities(protocol, base_url),
        )

    def _capabilities(self, protocol: Protocol, base_url: str) -> dict[str, Any]:
        return {
            "streaming": protocol != "minimax",
            "emotional_control": {
                "openai": "instructions",
                "azure": "native_style",
                "elevenlabs": "style_intensity",
                "minimax": "native_emotion",
            }[protocol],
            "external_egress": not is_local_host(base_url),
        }


def _secret(value: Any) -> str | None:
    if value is None:
        return None
    getter = getattr(value, "get_secret_value", None)
    raw = getter() if getter else str(value)
    raw = str(raw).strip()
    return raw or None


async def _first_chunk(iterator, provider: ConnectionProvider, *, timeout: float) -> bytes:
    deadline = time.perf_counter() + timeout
    while True:
        remaining = deadline - time.perf_counter()
        if remaining <= 0:
            raise ProviderError(
                provider=provider.name,
                code="timeout",
                message="等待首个音频分片超时。",
                stage="provider_ttfb",
                retryable=True,
            )
        try:
            chunk = await asyncio.wait_for(iterator.__anext__(), timeout=remaining)
        except StopAsyncIteration as exc:
            raise ProviderError(
                provider=provider.name,
                code="empty_audio",
                message="API 没有返回音频。",
                stage="provider_synthesis",
                retryable=True,
            ) from exc
        except TimeoutError as exc:
            raise ProviderError(
                provider=provider.name,
                code="timeout",
                message="等待首个音频分片超时。",
                stage="provider_ttfb",
                retryable=True,
            ) from exc
        if chunk:
            return chunk


def _connection_failure(exc: ProviderError) -> ConnectionFailure:
    status = 400
    if exc.code in {"auth_failed", "missing_credentials"}:
        status = 401
    elif exc.code == "rate_limited":
        status = 429
    elif exc.code == "timeout":
        status = 504
    elif exc.code in {"upstream_error", "network_error", "empty_audio"}:
        status = 502
    return ConnectionFailure(
        sanitize_message(exc.message),
        code=exc.code,
        stage=exc.stage,
        details=exc.as_attempt(),
        http_status=status,
    )


def _now() -> str:
    return datetime.now(UTC).isoformat()


def save_last_test(
    store: ConnectionStore,
    connection_id: str,
    result: TestResult | None,
    error: ProviderError | ConnectionFailure | None = None,
) -> None:
    record = store.get(connection_id)
    if record is None:
        return
    if result is not None:
        record.last_test = LastTest(
            ok=True,
            at=_now(),
            first_chunk_ms=result.first_chunk_ms,
            total_ms=result.total_ms,
        )
    elif error is not None:
        record.last_test = LastTest(
            ok=False,
            at=_now(),
            code=getattr(error, "code", "error"),
            message=sanitize_message(getattr(error, "message", "测试失败")),
        )
    store.put(record)
