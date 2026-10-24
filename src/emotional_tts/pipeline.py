"""Tag-driven output pipeline.

Flow:
    upstream event
      -> content rendering (type tag)
      -> intent route
      -> emotion alias resolution
      -> provider plan + vendor style params
      -> streamed synthesis into local cache
      -> contract output event + structured record

Failures before the first audio byte may fall through to explicitly configured
fallback providers, then to a plain-text output. A failure after audio has
started cannot be replaced transparently; the stream is closed and the partial
file is deleted.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from .cache import AudioCache, CachedAudio, mime_for_format, normalize_audio_format
from .config import AppConfig
from .contracts import (
    DegradationPayload,
    DiagnosticsPayload,
    InputEvent,
    OutputEvent,
    StylePayload,
    TimingPayload,
)
from .errors import CacheError, ProviderError, ValidationError
from .providers.base import SynthesisRequest, TTSProvider
from .providers.registry import ProviderRegistry
from .rendering import RenderResult, shape_content
from .routing import IntentRouter
from .security import sanitize_message
from .storage import RecordStore
from .styles import ResolvedEmotion, StyleBook, normalize_tag

logger = logging.getLogger(__name__)


@dataclass
class ProviderCandidate:
    name: str
    source: str  # requested | auto | fallback
    provider: TTSProvider | None
    ready: bool
    missing: list[str] = field(default_factory=list)


@dataclass
class PreparedOutput:
    event: InputEvent
    emotion: ResolvedEmotion
    rendered: RenderResult
    route: Any  # RoutePayload
    requested_provider: str
    candidates: list[ProviderCandidate]
    diagnostics: DiagnosticsPayload
    started_at: float = field(default_factory=time.perf_counter)


@dataclass
class SynthesisHooks:
    """Callbacks used by WebSocket streaming."""

    on_start: Callable[[OutputEvent, str], Awaitable[None]]
    on_chunk: Callable[[bytes], Awaitable[None]]


class OutputPipeline:
    def __init__(
        self,
        config: AppConfig,
        *,
        styles: StyleBook,
        router: IntentRouter,
        registry: ProviderRegistry,
        cache: AudioCache,
        records: RecordStore | None = None,
    ) -> None:
        self.config = config
        self.styles = styles
        self.router = router
        self.registry = registry
        self.cache = cache
        self.records = records

    def prepare(
        self,
        event: InputEvent,
        *,
        provider_override: str | None = None,
    ) -> PreparedOutput:
        if len(event.content) > self.config.app.max_input_chars:
            raise ValidationError(
                f"content 超过 {self.config.app.max_input_chars} 字符上限",
                code="content_too_long",
            )

        rendered = shape_content(
            event.content,
            event.tags.type,
            max_speech_chars=self.config.app.max_tts_chars,
        )
        route = self.router.route(event.tags.intent)
        emotion = self.styles.resolve(event.tags.emotion)

        requested = (
            (provider_override or event.provider or self.config.tts.provider).strip().lower()
        )
        if not requested:
            requested = "auto"
        candidates = self._plan_candidates(requested)

        by_provider = {
            name: self.styles.provider_params(emotion, name) for name in self.registry.providers
        }
        diagnostics = DiagnosticsPayload(
            emotion_mapping={
                "source_tag": emotion.source_tag,
                "normalized_tag": normalize_tag(event.tags.emotion),
                "resolved_emotion": emotion.key,
                "label": emotion.label,
                "mapping_hit": emotion.mapping_hit,
                "fallback_reason": emotion.fallback_reason,
            },
            provider_params={
                "canonical": {
                    "emotion": emotion.key,
                    "speed": round(emotion.speed, 3),
                    "pitch_semitones": round(emotion.pitch_semitones, 3),
                    "volume_db": round(emotion.volume_db, 3),
                    "tone": emotion.tone,
                },
                "by_provider": by_provider,
            },
            provider_attempts=[],
            speech={
                **rendered.meta,
                "intent_known": self.router.is_known(event.tags.intent),
            },
            privacy={
                "content_logged": False,
                "cache_scope": "local_only",
                "text_sent_to": [],
                "configured_fallbacks": list(self.config.tts.fallback_providers),
            },
        )
        return PreparedOutput(
            event=event,
            emotion=emotion,
            rendered=rendered,
            route=route,
            requested_provider=requested,
            candidates=candidates,
            diagnostics=diagnostics,
        )

    def _plan_candidates(self, requested: str) -> list[ProviderCandidate]:
        candidates: list[ProviderCandidate] = []
        seen: set[str] = set()

        def add(name: str, source: str) -> None:
            if not name or name in seen:
                return
            seen.add(name)
            provider = self.registry.get(name)
            ready, missing = provider.readiness() if provider else (False, ["unknown_provider"])
            candidates.append(
                ProviderCandidate(
                    name=name,
                    source=source,
                    provider=provider,
                    ready=ready,
                    missing=missing,
                )
            )

        if requested == "auto":
            auto = self.registry.auto_provider()
            if auto:
                add(auto.name, "auto")
        else:
            add(requested, "requested")

        for name in self.config.tts.fallback_providers:
            add(name, "fallback")
        return candidates

    def preview(self, prepared: PreparedOutput) -> OutputEvent:
        """Metadata available before the first vendor byte (used by WS)."""

        return self._build_output(
            prepared,
            status="preparing",
            total_ms=None,
        )

    async def process(
        self,
        event: InputEvent,
        *,
        provider_override: str | None = None,
        hooks: SynthesisHooks | None = None,
    ) -> OutputEvent:
        prepared = self.prepare(event, provider_override=provider_override)
        return await self.synthesize(prepared, hooks=hooks)

    async def synthesize(
        self,
        prepared: PreparedOutput,
        *,
        hooks: SynthesisHooks | None = None,
    ) -> OutputEvent:
        if not prepared.route.synthesize:
            output = self._build_output(
                prepared,
                status="text_only",
                route_reason=prepared.route.reason,
                total_ms=round((time.perf_counter() - prepared.started_at) * 1000, 1),
            )
            self._record(output, prepared)
            return output

        attempts: list[dict[str, Any]] = []
        contacted: list[str] = []
        last_error: ProviderError | None = None
        selected_params: dict[str, Any] = {}

        if not prepared.candidates:
            attempts.append(
                {
                    "provider": None,
                    "source": "auto",
                    "outcome": "skipped",
                    "code": "no_ready_provider",
                    "stage": "provider_selection",
                    "message": "没有已就绪的 TTS 厂商，且未配置降级链。",
                }
            )

        for candidate in prepared.candidates:
            if candidate.provider is None:
                attempts.append(
                    {
                        "provider": candidate.name,
                        "source": candidate.source,
                        "outcome": "skipped",
                        "code": "unknown_provider",
                        "stage": "provider_selection",
                        "message": f"未知厂商: {candidate.name}",
                    }
                )
                continue
            provider = candidate.provider
            if not candidate.ready:
                attempts.append(
                    {
                        "provider": provider.name,
                        "source": candidate.source,
                        "outcome": "skipped",
                        "code": "missing_credentials",
                        "stage": "provider_auth",
                        "message": f"{provider.display_name} 未就绪。",
                        "missing": candidate.missing,
                    }
                )
                last_error = ProviderError(
                    provider=provider.name,
                    code="missing_credentials",
                    message=f"{provider.display_name} 未就绪。",
                    stage="provider_auth",
                    details={"missing": candidate.missing},
                )
                continue

            try:
                style_params = self.styles.provider_params(prepared.emotion, provider.name)
                selected_params = style_params
                fmt = normalize_audio_format(provider.audio_format)
                request = SynthesisRequest(
                    text=prepared.rendered.speech_text,
                    language=self.config.app.language,
                    emotion=prepared.emotion.key,
                    tone=prepared.emotion.tone,
                    speed=float(style_params.get("speed", prepared.emotion.speed)),
                    voice=style_params.get("voice"),
                    style=style_params,
                    output_format=fmt,
                )
                request_preview = provider.request_preview(request)
            except ProviderError as exc:
                attempts.append(self._failed_attempt(candidate, exc))
                last_error = exc
                continue
            except (CacheError, ValueError) as exc:
                exc = ProviderError(
                    provider=provider.name,
                    code="unsupported_format",
                    message="厂商音频格式不受本地缓存支持。",
                    stage="adapter_configuration",
                    details={"exception_type": type(exc).__name__},
                )
                attempts.append(self._failed_attempt(candidate, exc))
                last_error = exc
                continue

            writer = self.cache.lease(fmt)
            iterator: Any = None
            candidate_started = False
            completed = False
            first_chunk_ms: float | None = None
            try:
                contacted.append(provider.name)
                iterator = provider.stream(request)
                remaining_total = max(
                    0.01,
                    self.config.tts.total_timeout_seconds
                    - (time.perf_counter() - prepared.started_at),
                )
                first_chunk = await self._next_nonempty(
                    iterator,
                    timeout=min(
                        self.config.tts.first_chunk_timeout_seconds,
                        remaining_total,
                    ),
                    provider=provider,
                    first=True,
                )
                first_chunk_ms = round((time.perf_counter() - prepared.started_at) * 1000, 1)
                writer.append(first_chunk)
                candidate_started = True

                base = self._build_output(
                    prepared,
                    provider_name=provider.name,
                    status="synthesized",
                    attempts=attempts
                    + [self._success_attempt(candidate, first_chunk_ms, None, request_preview)],
                    adapter_request=request_preview,
                    contacted=contacted,
                    selected_params=style_params,
                )
                if hooks is not None:
                    await hooks.on_start(base, mime_for_format(fmt))
                    await hooks.on_chunk(first_chunk)

                deadline = prepared.started_at + self.config.tts.total_timeout_seconds
                while True:
                    remaining = deadline - time.perf_counter()
                    if remaining <= 0:
                        raise ProviderError(
                            provider=provider.name,
                            code="timeout",
                            message="语音合成总时长超时。",
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
                            message="语音合成总时长超时。",
                            stage="provider_synthesis",
                            retryable=True,
                        ) from exc
                    if not chunk:
                        continue
                    writer.append(chunk)
                    if hooks is not None:
                        await hooks.on_chunk(chunk)

                cached = writer.finalize()
                completed = True
                total_ms = round((time.perf_counter() - prepared.started_at) * 1000, 1)
                attempt = self._success_attempt(
                    candidate, first_chunk_ms, total_ms, request_preview
                )
                output = self._build_output(
                    prepared,
                    provider_name=provider.name,
                    status="synthesized",
                    audio=cached,
                    attempts=attempts + [attempt],
                    adapter_request=request_preview,
                    contacted=contacted,
                    selected_params=style_params,
                    first_chunk_ms=first_chunk_ms,
                    total_ms=total_ms,
                )
                self._record(output, prepared, audio_bytes=cached.size)
                return output

            except ProviderError as exc:
                exc.details.setdefault("midstream", candidate_started)
                attempts.append(
                    self._failed_attempt(candidate, exc, first_chunk_ms, request_preview)
                )
                last_error = exc
                if candidate_started and hooks is not None:
                    # Bytes already left the process; silently switching
                    # vendors would splice two voices. Let the stream layer
                    # report the failure and keep the text available.
                    exc.details["partial_audio_discarded"] = True
                    self._record_interrupted(
                        prepared,
                        attempts=attempts,
                        contacted=contacted,
                        selected_params=selected_params,
                        adapter_request=request_preview,
                        first_chunk_ms=first_chunk_ms,
                        message="音频流中断，已丢弃不完整音频；完整文本已保留。",
                    )
                    raise
                continue
            except CacheError as exc:
                cached_error = ProviderError(
                    provider=provider.name,
                    code="cache_write_failed",
                    message="本地音频写入失败，已降级为纯文本。",
                    stage="cache",
                    details={"exception_type": type(exc).__name__},
                )
                attempts.append(
                    self._failed_attempt(candidate, cached_error, first_chunk_ms, request_preview)
                )
                last_error = cached_error
                if candidate_started and hooks is not None:
                    self._record_interrupted(
                        prepared,
                        attempts=attempts,
                        contacted=contacted,
                        selected_params=selected_params,
                        adapter_request=request_preview,
                        first_chunk_ms=first_chunk_ms,
                        message="音频流写入中断，已丢弃不完整音频；完整文本已保留。",
                    )
                    raise cached_error from exc
                continue
            finally:
                if not completed:
                    writer.abort()
                if iterator is not None:
                    aclose = getattr(iterator, "aclose", None)
                    if aclose is not None:
                        try:
                            await aclose()
                        except Exception:
                            pass

        output = self._build_output(
            prepared,
            status="text_fallback",
            degraded=True,
            degradation=DegradationPayload(
                code="no_ready_provider" if not prepared.candidates else "tts_unavailable",
                stage=last_error.stage if last_error else "provider_selection",
                message="语音合成不可用，已保留完整文本。",
                attempts=attempts,
            ),
            attempts=attempts,
            contacted=contacted,
            selected_params=selected_params,
            total_ms=round((time.perf_counter() - prepared.started_at) * 1000, 1),
        )
        self._record(output, prepared)
        return output

    async def _next_nonempty(
        self,
        iterator: Any,
        *,
        timeout: float,
        provider: TTSProvider,
        first: bool,
    ) -> bytes:
        deadline = time.perf_counter() + max(0.01, timeout)
        while True:
            remaining = deadline - time.perf_counter()
            if remaining <= 0:
                raise ProviderError(
                    provider=provider.name,
                    code="timeout",
                    message="等待厂商首个音频分片超时。",
                    stage="provider_ttfb",
                    retryable=True,
                )
            try:
                chunk = await asyncio.wait_for(iterator.__anext__(), timeout=remaining)
            except StopAsyncIteration as exc:
                raise ProviderError(
                    provider=provider.name,
                    code="empty_audio",
                    message="厂商没有返回音频。",
                    stage="provider_synthesis",
                    retryable=True,
                ) from exc
            except TimeoutError as exc:
                raise ProviderError(
                    provider=provider.name,
                    code="timeout",
                    message="等待厂商首个音频分片超时。",
                    stage="provider_ttfb" if first else "provider_synthesis",
                    retryable=True,
                ) from exc
            if chunk:
                return chunk

    def _build_output(
        self,
        prepared: PreparedOutput,
        *,
        status: str,
        provider_name: str | None = None,
        audio: CachedAudio | None = None,
        degraded: bool = False,
        degradation: DegradationPayload | None = None,
        attempts: list[dict[str, Any]] | None = None,
        adapter_request: dict[str, Any] | None = None,
        contacted: list[str] | None = None,
        selected_params: dict[str, Any] | None = None,
        first_chunk_ms: float | None = None,
        total_ms: float | None = None,
        route_reason: str | None = None,
    ) -> OutputEvent:
        diagnostics = prepared.diagnostics.model_copy(deep=True)
        diagnostics.provider_attempts = attempts or []
        if selected_params:
            diagnostics.provider_params["selected"] = selected_params
        if adapter_request:
            diagnostics.adapter = {
                "name": provider_name,
                "request": adapter_request,
            }
        destinations = []
        if contacted:
            for name in contacted:
                provider = self.registry.get(name)
                if provider:
                    destinations.append(
                        {
                            "provider": name,
                            "destination": provider.capabilities.data_destination,
                        }
                    )
        diagnostics.privacy["text_sent_to"] = destinations
        if route_reason:
            diagnostics.privacy["route_reason"] = route_reason

        route = prepared.route
        if route_reason:
            route = route.model_copy(update={"reason": route_reason})

        return OutputEvent(
            event_id=str(prepared.event.event_id),
            session_id=str(prepared.event.session_id),
            text=prepared.event.content,
            speech_text=prepared.rendered.speech_text,
            audio_ref=self.cache.ref_for(audio.filename) if audio else None,
            audio_mime=audio.mime if audio else None,
            audio_format=audio.fmt if audio else None,
            style=StylePayload(**prepared.emotion.public_style()),
            render=prepared.rendered.render,
            route=route,
            provider=provider_name,
            requested_provider=prepared.requested_provider,
            status=status,
            degraded=degraded,
            degradation=degradation,
            diagnostics=diagnostics,
            timings=TimingPayload(
                first_chunk_ms=first_chunk_ms,
                total_ms=total_ms,
            ),
        )

    def _record_interrupted(
        self,
        prepared: PreparedOutput,
        *,
        attempts: list[dict[str, Any]],
        contacted: list[str],
        selected_params: dict[str, Any],
        adapter_request: dict[str, Any] | None,
        first_chunk_ms: float | None,
        message: str,
    ) -> None:
        output = self._build_output(
            prepared,
            status="text_fallback",
            degraded=True,
            degradation=DegradationPayload(
                code="stream_interrupted",
                stage="provider_synthesis",
                message=message,
                attempts=attempts,
            ),
            attempts=attempts,
            contacted=contacted,
            selected_params=selected_params,
            adapter_request=adapter_request,
            first_chunk_ms=first_chunk_ms,
            total_ms=round((time.perf_counter() - prepared.started_at) * 1000, 1),
        )
        self._record(output, prepared)

    def _success_attempt(
        self,
        candidate: ProviderCandidate,
        first_chunk_ms: float | None,
        total_ms: float | None,
        preview: dict[str, Any],
    ) -> dict[str, Any]:
        return {
            "provider": candidate.name,
            "source": candidate.source,
            "outcome": "succeeded",
            "first_chunk_ms": first_chunk_ms,
            "total_ms": total_ms,
            "effective": preview.get("effective", {}),
        }

    def _failed_attempt(
        self,
        candidate: ProviderCandidate,
        error: ProviderError,
        first_chunk_ms: float | None = None,
        preview: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        data = error.as_attempt()
        data.update(
            {
                "provider": candidate.name,
                "source": candidate.source,
                "outcome": "failed",
                "first_chunk_ms": first_chunk_ms,
                "message": sanitize_message(data.get("message", "")),
            }
        )
        if preview:
            data["effective"] = preview.get("effective", {})
        return data

    def _record(
        self,
        output: OutputEvent,
        prepared: PreparedOutput,
        *,
        audio_bytes: int = 0,
    ) -> None:
        if self.records is None:
            return
        try:
            self.records.save(
                output,
                tags=prepared.event.tags.model_dump(),
                provider_params=output.diagnostics.provider_params,
                audio_bytes=audio_bytes,
            )
        except Exception as exc:  # storage must not break the user-facing path
            logger.error("failed to persist structured record: %s", type(exc).__name__)
