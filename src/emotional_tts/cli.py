"""Command line entry points."""

from __future__ import annotations

import argparse
import asyncio
import json
import shutil
import sys
from pathlib import Path
from uuid import uuid4

from .api import create_app
from .cache import AudioCache
from .config import Secrets, load_config
from .contracts import InputEvent, InputTags
from .errors import ConfigError, MoodVoiceError
from .pipeline import OutputPipeline
from .providers.base import SynthesisRequest
from .providers.registry import build_registry
from .routing import load_router
from .security import install_logging, register_secrets
from .storage import RecordStore
from .styles import load_stylebook


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    if not hasattr(args, "handler"):
        parser.print_help()
        return 0
    try:
        return int(args.handler(args) or 0)
    except ConfigError as exc:
        print(f"配置错误: {exc}", file=sys.stderr)
        return 2
    except MoodVoiceError as exc:
        print(f"错误 [{exc.code}]: {exc.message}", file=sys.stderr)
        return 1


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="moodvoice",
        description="标签驱动的情感语音输出层",
    )
    parser.add_argument(
        "--config",
        default=None,
        help="配置文件路径（默认 config/app.yaml）",
    )
    sub = parser.add_subparsers(dest="command")

    serve = sub.add_parser("serve", help="启动 API + Web Demo")
    serve.add_argument("--host", default=None, help="覆盖监听地址（默认仅本机）")
    serve.add_argument("--port", type=int, default=None, help="覆盖端口")
    serve.set_defaults(handler=_serve)

    demo = sub.add_parser("demo", help="命令行合成一段情感语音")
    demo.add_argument("text", help="要合成的文本")
    demo.add_argument("--emotion", default="comfort")
    demo.add_argument("--type", dest="content_type", default="text")
    demo.add_argument("--intent", default="reply", choices=["reply", "tool", "linkage"])
    demo.add_argument("--provider", default="auto")
    demo.add_argument("--out", default=None, help="把音频复制到该路径（可选）")
    demo.set_defaults(handler=_demo)

    providers = sub.add_parser("providers", help="查看厂商配置状态（不联网）")
    providers.set_defaults(handler=_providers)

    diagnose = sub.add_parser("diagnose", help="定位情感标签 -> 厂商参数的映射问题")
    diagnose.add_argument("--emotion", required=True)
    diagnose.add_argument("--provider", default="auto")
    diagnose.set_defaults(handler=_diagnose)

    cache = sub.add_parser("cache", help="清理本地音频缓存")
    cache.add_argument("action", choices=["info", "clear"])
    cache.add_argument("--records", action="store_true", help="同时清理结构化记录")
    cache.set_defaults(handler=_cache)
    return parser


def _serve(args: argparse.Namespace) -> int:
    import uvicorn

    config = load_config(args.config)
    host = args.host or config.server.host
    port = args.port or config.server.port
    # Keep API-origin checks, CSP and the health response aligned with the
    # actual listener rather than only the YAML defaults.
    config.server.host = host
    config.server.port = port
    app = create_app(config=config)
    print(f"MoodVoice Demo: http://{_display_host(host)}:{port}")
    print("只监听本机；按 Ctrl+C 停止。")
    uvicorn.run(app, host=host, port=port, log_level="info", access_log=False)
    return 0


async def _run_demo(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    secrets = Secrets.from_env()
    register_secrets(secrets.values())
    styles = load_stylebook(config.styles_path)
    router = load_router(config.routes_path)
    registry = build_registry(config, secrets)
    cache = AudioCache(
        config.data_path / "cache",
        max_files=config.tts.cache.max_files,
        max_age_hours=config.tts.cache.max_age_hours,
    )
    records = RecordStore(config.data_path / "records.sqlite3")
    pipeline = OutputPipeline(
        config,
        styles=styles,
        router=router,
        registry=registry,
        cache=cache,
        records=records,
    )
    event = InputEvent(
        event_id=uuid4(),
        session_id=uuid4(),
        content=args.text,
        stream=True,
        tags=InputTags(emotion=args.emotion, type=args.content_type, intent=args.intent),
        provider=args.provider,
    )
    try:
        output = await pipeline.process(event)
    finally:
        await registry.close()
        records.close()
    print(json.dumps(output.to_json_dict(), ensure_ascii=False, indent=2))
    if output.audio_ref and args.out:
        filename = output.audio_ref.rsplit("/", 1)[-1]
        source = cache.resolve(filename)
        destination = Path(args.out).expanduser().resolve()
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
        print(f"音频已复制: {destination}", file=sys.stderr)
    return 0


def _demo(args: argparse.Namespace) -> int:
    install_logging()
    return asyncio.run(_run_demo(args))


def _providers(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    registry = build_registry(config, Secrets.from_env())
    try:
        print(
            json.dumps(
                {
                    "default_provider": config.tts.provider,
                    "fallback_providers": config.tts.fallback_providers,
                    "providers": registry.public_catalog(),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    finally:
        asyncio.run(registry.close())
    return 0


def _diagnose(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    secrets = Secrets.from_env()
    register_secrets(secrets.values())
    styles = load_stylebook(config.styles_path)
    registry = build_registry(config, secrets)
    resolved = styles.resolve(args.emotion)
    requested = args.provider.strip().lower()
    provider = registry.get(requested) if requested != "auto" else registry.auto_provider()
    if provider is None:
        print(
            json.dumps(
                {
                    "ok": False,
                    "error": f"未知或未就绪的厂商: {args.provider}",
                    "mapping": resolved.public_style(),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 1
    params = styles.provider_params(resolved, provider.name)
    req = SynthesisRequest(
        text="风格参数诊断占位文本",
        language=config.app.language,
        emotion=resolved.key,
        tone=resolved.tone,
        speed=resolved.speed,
        voice=params.get("voice"),
        style=params,
        output_format=provider.audio_format,
    )
    try:
        preview = provider.request_preview(req)
    except Exception as exc:
        preview = {"error": type(exc).__name__}
    print(
        json.dumps(
            {
                "source_tag": args.emotion,
                "mapping_hit": resolved.mapping_hit,
                "fallback_reason": resolved.fallback_reason,
                "resolved": resolved.public_style(),
                "provider": provider.name,
                "ready": provider.ready,
                "missing": provider.readiness()[1],
                "canonical_and_provider_params": params,
                "request_preview": preview,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    asyncio.run(registry.close())
    return 0 if resolved.mapping_hit else 3


def _cache(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    cache = AudioCache(
        config.data_path / "cache",
        max_files=config.tts.cache.max_files,
        max_age_hours=config.tts.cache.max_age_hours,
    )
    if args.action == "info":
        print(json.dumps(cache.stats(), ensure_ascii=False, indent=2))
        return 0
    files = cache.clear()
    result = {"deleted_files": files}
    if args.records:
        store = RecordStore(config.data_path / "records.sqlite3")
        result["deleted_records"] = store.clear()
        store.close()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def _display_host(host: str) -> str:
    if host in {"0.0.0.0", "::"}:
        return "127.0.0.1"
    return host


if __name__ == "__main__":
    raise SystemExit(main())
