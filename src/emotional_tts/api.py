"""FastAPI application: REST contract, audio serving and streaming WebSocket."""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from fastapi import FastAPI, Query, Request, Response, WebSocket, WebSocketDisconnect
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from .cache import AudioCache, mime_for_format
from .config import AppConfig, Secrets, load_config
from .connections.manager import ConnectionManager, save_last_test
from .connections.models import (
    DiscoverRequest,
    SaveConnectionRequest,
    TestConnectionRequest,
    TestResult,
)
from .contract import ContractProvider
from .contracts import InputEvent, OutputEvent
from .errors import (
    CacheError,
    ConfigError,
    ConnectionFailure,
    MoodVoiceError,
    ProviderError,
    ValidationError,
    is_retryable,
)
from .pipeline import OutputPipeline, SynthesisHooks
from .providers.base import SynthesisRequest
from .providers.registry import ProviderRegistry, build_registry
from .routing import load_router
from .security import install_logging, register_secrets, sanitize_message
from .storage import RecordStore
from .styles import load_stylebook, normalize_tag

logger = logging.getLogger(__name__)
WEB_DIR = Path(__file__).resolve().parent / "web"


def create_app(
    config_path: str | Path | None = None,
    *,
    config: AppConfig | None = None,
    registry: ProviderRegistry | None = None,
    cache: AudioCache | None = None,
    records: RecordStore | None = None,
    connection_manager: ConnectionManager | None = None,
) -> FastAPI:
    install_logging()
    app_config = config or load_config(config_path)
    secrets = Secrets.from_env()
    register_secrets(secrets.values())

    styles = load_stylebook(app_config.styles_path)
    router = load_router(app_config.routes_path)
    app_config.data_path.mkdir(parents=True, exist_ok=True)
    if registry is None:
        registry = build_registry(app_config, secrets)
    if cache is None:
        cache = AudioCache(
            app_config.data_path / "cache",
            max_files=app_config.tts.cache.max_files,
            max_age_hours=app_config.tts.cache.max_age_hours,
        )
    if records is None:
        records = RecordStore(app_config.data_path / "records.sqlite3")
    connections = connection_manager or ConnectionManager(
        app_config,
        styles=styles,
        cache=cache,
        registry=registry,
    )
    connections.bootstrap()
    pipeline = OutputPipeline(
        app_config,
        styles=styles,
        router=router,
        registry=registry,
        cache=cache,
        records=records,
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        ready = [p["id"] for p in registry.public_catalog() if p["ready"]]
        logger.info(
            "MoodVoice %s started; ready providers=%s; audio and metadata stay in %s",
            app_config.app.version,
            ",".join(ready) or "none",
            "the local data directory",
        )
        try:
            yield
        finally:
            await registry.close()
            records.close()

    api = FastAPI(
        title="MoodVoice Emotional TTS",
        version=app_config.app.version,
        description="Tag-driven emotional voice output with pluggable providers and text fallback.",
        docs_url=None,
        redoc_url=None,
        openapi_url="/api/openapi.json",
        lifespan=lifespan,
    )
    api.state.config = app_config
    api.state.styles = styles
    api.state.router = router
    api.state.registry = registry
    api.state.cache = cache
    api.state.records = records
    api.state.pipeline = pipeline
    api.state.connections = connections
    contract = ContractProvider(styles=styles, router=router, config=app_config, app=api)
    api.state.contract = contract
    allowed_origins = _allowed_origins(app_config)

    @api.middleware("http")
    async def security_middleware(request: Request, call_next):
        origin = request.headers.get("origin")
        if origin and not _is_trusted_origin(origin, allowed_origins):
            logger.warning("rejected origin=%r path=%s", origin[:200], request.url.path)
            return JSONResponse(
                status_code=403,
                headers={"X-MoodVoice-Contract-Version": contract.version},
                content={
                    "error": {
                        "code": "origin_not_allowed",
                        "message": "请求来源未被允许。请通过本机 Demo 页面访问。",
                        "retryable": is_retryable("origin_not_allowed"),
                    }
                },
            )
        response = await call_next(request)
        response.headers.setdefault("X-MoodVoice-Contract-Version", contract.version)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        response.headers.setdefault(
            "Permissions-Policy", "microphone=(), camera=(), geolocation=()"
        )
        page_origin = origin
        if not page_origin:
            host = request.headers.get("host", "")
            if host:
                page_origin = f"{request.url.scheme}://{host}"
        connect_sources = _csp_connect_sources(allowed_origins, page_origin=page_origin)
        response.headers.setdefault(
            "Content-Security-Policy",
            "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data:; media-src 'self' blob: data:; "
            f"connect-src {' '.join(connect_sources)}; "
            "frame-ancestors 'none'; base-uri 'none'",
        )
        if request.url.path.startswith("/api/"):
            response.headers.setdefault("Cache-Control", "no-store")
        return response

    @api.exception_handler(RequestValidationError)
    async def validation_handler(request: Request, exc: RequestValidationError):
        # Deliberately omit pydantic's "input" field: request content and keys
        # should not be copied into error logs or UI surfaces.
        errors = [
            {
                "loc": [str(part) for part in error.get("loc", ())],
                "msg": error.get("msg", "invalid value"),
                "type": error.get("type", "value_error"),
            }
            for error in exc.errors()
        ]
        return JSONResponse(
            status_code=422,
            content={
                "error": {
                    "code": "invalid_event",
                    "message": "请求参数不符合契约。",
                    "retryable": is_retryable("invalid_event"),
                    "details": errors,
                }
            },
        )

    @api.exception_handler(MoodVoiceError)
    async def moodvoice_handler(request: Request, exc: MoodVoiceError):
        payload: dict[str, Any] = {
            "code": exc.code,
            "message": sanitize_message(exc.message),
            "retryable": bool(getattr(exc, "retryable", is_retryable(exc.code))),
        }
        request_id = getattr(exc, "request_id", None)
        if request_id:
            payload["request_id"] = sanitize_message(str(request_id), limit=120)
        details = getattr(exc, "details", None)
        if isinstance(details, dict) and details:
            payload["details"] = details
        return JSONResponse(status_code=exc.http_status, content={"error": payload})

    @api.exception_handler(Exception)
    async def unhandled_handler(request: Request, exc: Exception):
        logger.error("unhandled request failure: %s", type(exc).__name__)
        return JSONResponse(
            status_code=500,
            content={
                "error": {
                    "code": "internal_error",
                    "message": "输出层发生内部错误，请查看本地日志。",
                    "retryable": is_retryable("internal_error"),
                }
            },
        )

    @api.exception_handler(StarletteHTTPException)
    async def http_handler(request: Request, exc: StarletteHTTPException):
        detail = exc.detail
        if isinstance(detail, dict):
            code = str(detail.get("code", "http_error"))
            message = str(detail.get("message", "请求失败。"))
        else:
            code = "http_error"
            message = str(detail) if detail else "请求失败。"
        public_code = sanitize_message(code, limit=80)
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "error": {
                    "code": public_code,
                    "message": sanitize_message(message),
                    "retryable": is_retryable(public_code),
                }
            },
            headers=getattr(exc, "headers", None),
        )

    @api.get("/", response_class=HTMLResponse, include_in_schema=False)
    async def index() -> HTMLResponse:
        html = (WEB_DIR / "index.html").read_text(encoding="utf-8")
        return HTMLResponse(html, headers={"Cache-Control": "no-store"})

    api.mount("/assets", StaticFiles(directory=WEB_DIR), name="assets")

    @api.get("/api/health")
    async def health() -> dict[str, Any]:
        return {
            "status": "ok",
            "app": {
                "name": app_config.app.name,
                "version": app_config.app.version,
                "language": app_config.app.language,
            },
            **contract.summary(),
            "providers": registry.public_catalog(),
            "default_provider": app_config.tts.provider,
            "auto_ready": registry.first_ready_name(),
            "fallback_providers": list(app_config.tts.fallback_providers),
            "cache": cache.stats(),
            "records": records.count(),
            "connections": connections.public_connections(),
            "connection_key_storage": connections.key_storage(),
            "limits": {
                "max_input_chars": app_config.app.max_input_chars,
                "max_tts_chars": app_config.app.max_tts_chars,
            },
            "emotion_mapping": {
                "version": styles.model.version,
                "canonical_count": len(styles.emotions),
                "alias_count": len(styles.describe()["aliases"]),
                "fallbacks": pipeline.mapping_monitor.snapshot(),
            },
            "privacy": {
                "bind": f"{app_config.server.host}:{app_config.server.port}",
                "content_logged": False,
                "cache_scope": "local_only",
                "key_source": "environment_or_local_dotenv",
                "egress": "only the TTS provider selected for synthesis",
                "default_external_egress": (
                    registry.auto_provider().external_egress()
                    if registry.auto_provider() is not None
                    else False
                ),
                "allowed_origins": sorted(allowed_origins),
                "origin_wildcards_enabled": any("*" in item for item in allowed_origins),
            },
        }

    @api.get("/api/v1/contract")
    async def interface_contract() -> dict[str, Any]:
        """Machine-readable contract: tags, output, errors, limits, endpoints."""

        return contract.document()

    @api.post("/api/v1/outputs", response_model=OutputEvent)
    async def outputs(
        event: InputEvent,
        response: Response,
        provider: str | None = Query(default=None, max_length=32),
    ) -> OutputEvent:
        output = await pipeline.process(
            event,
            provider_override=provider or event.provider,
        )
        response.headers["X-MoodVoice-Status"] = output.status
        response.headers["X-MoodVoice-Degraded"] = "1" if output.degraded else "0"
        response.headers["X-MoodVoice-Egress"] = (
            "external" if output.privacy.external_egress else "none"
        )
        return output

    @api.get("/api/v1/connections")
    async def list_connections() -> dict[str, Any]:
        return {
            "connections": connections.public_connections(),
            "key_storage": connections.key_storage(),
        }

    @api.post("/api/v1/connections/discover")
    async def discover_connection(request: DiscoverRequest) -> dict[str, Any]:
        result = await connections.discover(request)
        return result.model_dump(mode="json")

    @api.post("/api/v1/connections/test", response_model=TestResult)
    async def test_connection(request: TestConnectionRequest) -> TestResult:
        return await connections.test(request)

    @api.post("/api/v1/connections")
    async def save_connection(request: SaveConnectionRequest) -> dict[str, Any]:
        record = await connections.save(request)
        key_store = connections.key_store
        key_configured = bool(key_store.get(record.id))
        provider = registry.get(record.id)
        ready = bool(provider and provider.readiness()[0])
        return record.public_dict(key_configured=key_configured, ready=ready)

    @api.get("/api/v1/connections/{connection_id}")
    async def get_connection(connection_id: str) -> dict[str, Any]:
        record = connections.store.get(connection_id)
        if record is None:
            raise _not_found("connection_not_found", "连接不存在。")
        key_configured = bool(connections.key_store.get(record.id))
        provider = registry.get(record.id)
        ready = bool(provider and provider.readiness()[0])
        return record.public_dict(key_configured=key_configured, ready=ready)

    @api.post("/api/v1/connections/{connection_id}/test")
    async def retest_connection(connection_id: str) -> TestResult:
        record = connections.store.get(connection_id)
        if record is None:
            raise _not_found("connection_not_found", "连接不存在。")
        key = connections.key_store.get(connection_id)
        request = TestConnectionRequest(
            base_url=record.base_url,
            api_key=key,
            protocol=record.protocol,
            model=record.model,
            voice=record.voice,
            region=record.region,
            group_id=record.group_id,
            response_format=record.response_format,
            output_format=record.output_format,
            format=record.format,
            allow_insecure_http=record.allow_insecure_http,
        )
        try:
            result = await connections.test(request)
        except (ConnectionFailure, ProviderError) as exc:
            save_last_test(connections.store, connection_id, None, exc)
            raise
        save_last_test(connections.store, connection_id, result)
        return result

    @api.delete("/api/v1/connections/{connection_id}")
    async def delete_connection(connection_id: str) -> dict[str, Any]:
        if not await connections.remove_connection(connection_id):
            raise _not_found("connection_not_found", "连接不存在。")
        return {"ok": True}

    @api.get("/api/v1/audio/{filename}")
    async def audio(filename: str) -> FileResponse:
        try:
            path = cache.resolve(filename)
        except CacheError as exc:
            raise _not_found("audio_not_found", "音频不存在或已被清理。") from exc
        suffix = path.suffix.lstrip(".").lower()
        media_type = mime_for_format(suffix)
        return FileResponse(
            path,
            media_type=media_type,
            headers={
                "Cache-Control": "no-store",
                "Content-Disposition": f'inline; filename="{path.name}"',
                "X-Content-Type-Options": "nosniff",
            },
        )

    @api.get("/api/v1/records")
    async def list_records(
        limit: int = Query(default=50, ge=1, le=200),
        offset: int = Query(default=0, ge=0),
    ) -> dict[str, Any]:
        return {
            "total": records.count(),
            "items": records.list(limit=limit, offset=offset),
        }

    @api.delete("/api/v1/cache")
    async def clear_cache() -> dict[str, Any]:
        deleted = cache.clear()
        logger.info("local audio cache cleared: %s files", deleted)
        return {"ok": True, "deleted_files": deleted}

    @api.delete("/api/v1/records")
    async def clear_records() -> dict[str, Any]:
        deleted = records.clear()
        logger.info("structured records cleared: %s rows", deleted)
        return {"ok": True, "deleted_records": deleted}

    @api.delete("/api/v1/user-data")
    async def clear_user_data() -> dict[str, Any]:
        files = cache.clear()
        rows = records.clear()
        logger.info("user data cleared: %s files, %s rows", files, rows)
        return {"ok": True, "deleted_files": files, "deleted_records": rows}

    @api.get("/api/v1/diagnostics/style")
    async def diagnose_style(
        emotion: str = Query(default="neutral", max_length=64),
        provider: str = Query(default="", max_length=32),
    ) -> dict[str, Any]:
        return _diagnose_style(
            app_config,
            styles,
            registry,
            emotion=emotion,
            provider=provider,
            language=app_config.app.language,
        )

    @api.post("/api/v1/admin/reload-connections")
    async def reload_connections() -> dict[str, Any]:
        """Re-read connections.json and re-register providers (ops escape hatch)."""

        count = await connections.reload()
        return {
            "reloaded": count,
            "connections": connections.public_connections(),
            "key_storage": connections.key_storage(),
        }

    @api.websocket("/ws/v1/outputs")
    async def ws_outputs(websocket: WebSocket) -> None:
        origin = websocket.headers.get("origin")
        if origin and not _is_trusted_origin(origin, allowed_origins):
            logger.warning("rejected websocket origin=%r", origin[:200])
            await websocket.close(code=1008, reason="origin not allowed")
            return
        await websocket.accept()
        try:
            payload = await websocket.receive_json()
            event_data = payload.get("event", payload) if isinstance(payload, dict) else payload
            event = InputEvent.model_validate(event_data)
            provider_override = None
            if isinstance(payload, dict):
                provider_override = payload.get("provider") or event.provider
            prepared = pipeline.prepare(event, provider_override=provider_override)
            await websocket.send_json(
                {
                    "type": "preparing",
                    "output": pipeline.preview(prepared).to_json_dict(),
                }
            )

            async def on_start(output: OutputEvent, mime: str) -> None:
                await websocket.send_json(
                    {
                        "type": "start",
                        "output": output.to_json_dict(),
                        "audio": {"mime": mime},
                    }
                )

            async def on_chunk(chunk: bytes) -> None:
                await websocket.send_bytes(chunk)

            output = await pipeline.synthesize(
                prepared,
                hooks=SynthesisHooks(on_start=on_start, on_chunk=on_chunk),
            )
            await websocket.send_json({"type": "done", "output": output.to_json_dict()})
        except WebSocketDisconnect:
            return
        except ProviderError as exc:
            try:
                error_payload: dict[str, Any] = {
                    "code": exc.code,
                    "message": sanitize_message(exc.message),
                    "retryable": exc.retryable,
                    "stage": exc.stage,
                    "midstream": bool(exc.details.get("midstream")),
                }
                if exc.request_id:
                    error_payload["request_id"] = sanitize_message(str(exc.request_id), limit=120)
                await websocket.send_json({"type": "error", "error": error_payload})
            except Exception:
                pass
        except (ValidationError, MoodVoiceError) as exc:
            try:
                await websocket.send_json(
                    {
                        "type": "error",
                        "error": {
                            "code": exc.code,
                            "message": sanitize_message(exc.message),
                            "retryable": bool(getattr(exc, "retryable", False)),
                        },
                    }
                )
            except Exception:
                pass
        except Exception as exc:  # do not leak content or secrets in the message
            logger.error("websocket synthesis failed: %s", type(exc).__name__)
            try:
                await websocket.send_json(
                    {
                        "type": "error",
                        "error": {
                            "code": "internal_error",
                            "message": "输出层发生内部错误，请查看本地日志。",
                            "retryable": is_retryable("internal_error"),
                        },
                    }
                )
            except Exception:
                pass
        finally:
            try:
                await websocket.close()
            except Exception:
                pass

    return api


def _diagnose_style(
    config: AppConfig,
    styles,
    registry: ProviderRegistry,
    *,
    emotion: str,
    provider: str,
    language: str,
) -> dict[str, Any]:
    resolved = styles.resolve(emotion)
    requested = (provider or config.tts.provider).strip().lower()
    selected = registry.auto_provider() if requested == "auto" else registry.get(requested)
    if selected is None and requested == "auto":
        # Auto mode with nothing ready: inspect the first auto candidate anyway.
        for name in registry.auto_order:
            selected = registry.get(name)
            if selected:
                break
    warnings: list[str] = []
    if selected is None:
        return {
            "ok": False,
            "error": {"code": "unknown_provider", "message": f"未知厂商: {requested}"},
            "emotion": resolved.public_style(),
        }
    if not styles.resolve(emotion).mapping_hit:
        warnings.append(
            f"上游标签 '{emotion}' 未命中映射，已回退到 {resolved.key}；"
            "请检查 config/emotion_styles.yaml 的 aliases。"
        )
    if not selected.ready:
        warnings.append(
            "厂商未就绪，运行时将降级为纯文本；缺少: " + ", ".join(selected.readiness()[1])
        )
    params = styles.provider_params(resolved, selected.name)
    request = SynthesisRequest(
        text="风格参数诊断占位文本",
        language=language,
        emotion=resolved.key,
        tone=resolved.tone,
        speed=resolved.speed,
        voice=params.get("voice"),
        style=params,
        output_format=selected.audio_format,
    )
    preview: dict[str, Any] | None = None
    error: dict[str, Any] | None = None
    try:
        preview = selected.request_preview(request)
    except ProviderError as exc:
        error = {
            "code": exc.code,
            "message": sanitize_message(exc.message),
            "stage": exc.stage,
            "details": exc.details,
        }
        warnings.append("适配器在构造请求时发现参数问题，请按 error.stage 定位。")
    checks = [
        {
            "name": "emotion_mapping_hit",
            "ok": resolved.mapping_hit,
            "detail": resolved.fallback_reason or "matched",
        },
        {
            "name": "provider_ready",
            "ok": selected.ready,
            "detail": selected.readiness()[1],
        },
        {
            "name": "adapter_preview",
            "ok": error is None,
            "detail": error["code"] if error else "ok",
        },
    ]
    return {
        "ok": all(check["ok"] for check in checks),
        "source_emotion": emotion,
        "normalized_tag": normalize_tag(emotion),
        "resolved": resolved.public_style(),
        "provider": selected.name,
        "requested_provider": requested,
        "ready": selected.ready,
        "missing": selected.readiness()[1],
        "capabilities": selected.capabilities.public(),
        "mapping": {
            "canonical": params,
            "by_provider": {
                name: styles.provider_params(resolved, name) for name in registry.providers
            },
        },
        "request_preview": preview,
        "warnings": warnings,
        "checks": checks,
        "note": "此接口只解析配置，不发起厂商网络请求，也不包含用户文本。",
    }


def _not_found(code: str, message: str) -> Exception:
    from fastapi import HTTPException

    return HTTPException(status_code=404, detail={"code": code, "message": message})


def _allowed_origins(config: AppConfig) -> set[str]:
    port = config.server.port
    origins = {
        f"http://127.0.0.1:{port}",
        f"http://localhost:{port}",
    }
    extra = os.getenv("EMOTION_TTS_ALLOWED_ORIGINS", "")
    seen: set[str] = set()
    for item in extra.split(","):
        item = item.strip()
        if not item:
            continue
        normalized = _normalize_origin(item)
        if "*" in normalized:
            if normalized not in _LOOPBACK_WILDCARDS:
                raise ConfigError(
                    "EMOTION_TTS_ALLOWED_ORIGINS 只允许本机端口通配: "
                    "http://localhost:* 或 http://127.0.0.1:*"
                )
        else:
            parts = urlsplit(normalized)
            if parts.scheme not in {"http", "https"} or not parts.hostname:
                raise ConfigError(f"EMOTION_TTS_ALLOWED_ORIGINS 含非法 Origin: {item[:80]}")
        if normalized in seen:
            continue
        seen.add(normalized)
        origins.add(normalized)
    return origins


_LOOPBACK_WILDCARDS = {
    "http://localhost:*",
    "http://127.0.0.1:*",
}


def _is_trusted_origin(origin: str, allowed_origins: set[str]) -> bool:
    normalized = _normalize_origin(origin)
    if normalized in allowed_origins:
        return True
    # Port wildcards are opt-in only, and only for loopback hosts. A remote
    # proxy must be configured with its exact origin.
    for pattern in _LOOPBACK_WILDCARDS:
        if pattern not in allowed_origins:
            continue
        prefix = pattern[:-2]  # "http://localhost:*" -> "http://localhost"
        if normalized.startswith(prefix + ":"):
            port = normalized.rsplit(":", 1)[-1]
            if port.isdigit() and 1 <= int(port) <= 65535:
                return True
    return False


def _csp_connect_sources(allowed_origins: set[str], *, page_origin: str | None = None) -> list[str]:
    """Build concrete CSP websocket sources without invalid wildcard tokens.

    Browsers reject ``ws://localhost:*``. Port wildcards are enforced by the
    request-level Origin guard; when a trusted page origin is known, CSP gets
    its concrete WebSocket source so Vite/proxy pages can still connect.
    """

    sources = ["'self'"]
    for origin in sorted(allowed_origins):
        if origin in _LOOPBACK_WILDCARDS:
            continue
        sources.append(_websocket_origin(origin))
    if page_origin and _is_trusted_origin(page_origin, allowed_origins):
        ws_source = _websocket_origin(_normalize_origin(page_origin))
        if ws_source not in sources:
            sources.append(ws_source)
    return sources


def _normalize_origin(origin: str) -> str:
    return origin.strip().rstrip("/").lower()


def _websocket_origin(origin: str) -> str:
    if origin.startswith("https://"):
        return "wss://" + origin[len("https://") :]
    if origin.startswith("http://"):
        return "ws://" + origin[len("http://") :]
    return origin
