"""Configuration loading.

Secrets are intentionally *not* part of the YAML configuration. They are read
from the process environment (optionally populated by a local, git-ignored
``.env``) into :class:`Secrets` and handed only to the provider that needs them.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .errors import ConfigError

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config" / "app.yaml"

KNOWN_PROVIDERS = ("edge", "openai", "elevenlabs", "azure", "minimax", "local")
PROVIDER_ORDER = ("azure", "openai", "elevenlabs", "minimax", "local", "edge")


class AppMeta(BaseModel):
    model_config = ConfigDict(extra="ignore")

    name: str = "MoodVoice"
    version: str = "1.0.0"
    language: str = "zh-CN"
    max_input_chars: int = Field(default=8000, ge=1, le=20000)
    max_tts_chars: int = Field(default=3000, ge=1, le=10000)

    @model_validator(mode="after")
    def _check_limits(self) -> AppMeta:
        if self.max_tts_chars > self.max_input_chars:
            raise ValueError("max_tts_chars cannot exceed max_input_chars")
        return self


class ServerSettings(BaseModel):
    model_config = ConfigDict(extra="ignore")

    host: str = "127.0.0.1"
    port: int = Field(default=8765, ge=1, le=65535)


class CacheSettings(BaseModel):
    model_config = ConfigDict(extra="ignore")

    max_files: int = Field(default=200, ge=1, le=10000)
    max_age_hours: int = Field(default=168, ge=1, le=24 * 365)


class TTSSettings(BaseModel):
    model_config = ConfigDict(extra="ignore")

    provider: str = "auto"
    fallback_providers: list[str] = Field(default_factory=list)
    first_chunk_timeout_seconds: float = Field(default=12.0, gt=0, le=120)
    total_timeout_seconds: float = Field(default=60.0, gt=0, le=300)
    auto_order: list[str] = Field(default_factory=lambda: list(PROVIDER_ORDER))
    cache: CacheSettings = Field(default_factory=CacheSettings)
    data_dir: str = ".data"
    styles_file: str = "config/emotion_styles.yaml"
    routes_file: str = "config/routes.yaml"

    @field_validator("provider")
    @classmethod
    def _provider(cls, value: str) -> str:
        value = value.strip().lower()
        if value != "auto" and value not in KNOWN_PROVIDERS:
            raise ValueError(f"unknown provider: {value}")
        return value

    @field_validator("fallback_providers", "auto_order")
    @classmethod
    def _providers(cls, values: list[str]) -> list[str]:
        normalized: list[str] = []
        for value in values:
            name = str(value).strip().lower()
            if not name:
                continue
            if name not in KNOWN_PROVIDERS:
                raise ValueError(f"unknown provider in list: {name}")
            if name not in normalized:
                normalized.append(name)
        return normalized


class ProviderConfig(BaseModel):
    """Vendor settings. Unknown keys are kept so adapters can evolve."""

    model_config = ConfigDict(extra="allow")

    enabled: bool = True
    base_url: str | None = None
    voice: str | None = None
    voice_id: str | None = None
    region: str | None = None
    model: str | None = None
    model_id: str | None = None
    response_format: str | None = None
    output_format: str | None = None
    group_id: str | None = None
    language_boost: str | None = None
    format: str | None = None

    def get(self, key: str, default: Any = None) -> Any:
        return getattr(self, key, default)


class AppConfig(BaseModel):
    model_config = ConfigDict(extra="ignore", arbitrary_types_allowed=True)

    app: AppMeta = Field(default_factory=AppMeta)
    server: ServerSettings = Field(default_factory=ServerSettings)
    tts: TTSSettings = Field(default_factory=TTSSettings)
    providers: dict[str, ProviderConfig] = Field(default_factory=dict)

    root: Path = Field(default=PROJECT_ROOT, exclude=True)
    config_path: Path = Field(default=DEFAULT_CONFIG_PATH, exclude=True)
    styles_path: Path = Field(default=PROJECT_ROOT / "config/emotion_styles.yaml", exclude=True)
    routes_path: Path = Field(default=PROJECT_ROOT / "config/routes.yaml", exclude=True)
    data_path: Path = Field(default=PROJECT_ROOT / ".data", exclude=True)

    def public_dict(self) -> dict[str, Any]:
        return {
            "name": self.app.name,
            "version": self.app.version,
            "language": self.app.language,
            "default_provider": self.tts.provider,
            "fallback_providers": list(self.tts.fallback_providers),
        }


class Secrets:
    """Secrets loaded from the environment only."""

    __slots__ = (
        "openai_api_key",
        "elevenlabs_api_key",
        "azure_speech_key",
        "minimax_api_key",
        "local_tts_api_key",
    )

    def __init__(
        self,
        *,
        openai_api_key: str | None = None,
        elevenlabs_api_key: str | None = None,
        azure_speech_key: str | None = None,
        minimax_api_key: str | None = None,
        local_tts_api_key: str | None = None,
    ) -> None:
        self.openai_api_key = _clean_secret(openai_api_key)
        self.elevenlabs_api_key = _clean_secret(elevenlabs_api_key)
        self.azure_speech_key = _clean_secret(azure_speech_key)
        self.minimax_api_key = _clean_secret(minimax_api_key)
        self.local_tts_api_key = _clean_secret(local_tts_api_key)

    @classmethod
    def from_env(cls) -> Secrets:
        return cls(
            openai_api_key=os.getenv("OPENAI_API_KEY"),
            elevenlabs_api_key=os.getenv("ELEVENLABS_API_KEY"),
            azure_speech_key=os.getenv("AZURE_SPEECH_KEY"),
            minimax_api_key=os.getenv("MINIMAX_API_KEY"),
            local_tts_api_key=os.getenv("LOCAL_TTS_API_KEY"),
        )

    def values(self) -> tuple[str, ...]:
        return tuple(
            value
            for value in (
                self.openai_api_key,
                self.elevenlabs_api_key,
                self.azure_speech_key,
                self.minimax_api_key,
                self.local_tts_api_key,
            )
            if value
        )


def _clean_secret(value: str | None) -> str | None:
    if value is None:
        return None
    value = value.strip()
    return value or None


def _split_env_list(value: str | None) -> list[str]:
    if not value:
        return []
    return [item.strip() for item in value.split(",") if item.strip()]


def _resolve_path(root: Path, value: str | Path) -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = root / path
    return path.resolve()


def load_config(config_path: str | Path | None = None) -> AppConfig:
    """Load YAML + environment overrides.

    ``.env`` is loaded only from the repository root and never overrides an
    already-exported environment variable.
    """

    root = Path(os.getenv("EMOTION_TTS_ROOT", str(PROJECT_ROOT))).expanduser().resolve()
    env_file = root / ".env"
    if env_file.exists():
        load_dotenv(env_file, override=False)

    chosen = Path(
        config_path or os.getenv("EMOTION_TTS_CONFIG") or str(root / "config" / "app.yaml")
    ).expanduser()
    if not chosen.is_absolute():
        chosen = (root / chosen).resolve()
    if not chosen.exists():
        raise ConfigError(f"配置文件不存在: {chosen}")

    try:
        raw = yaml.safe_load(chosen.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise ConfigError(f"配置文件 YAML 解析失败: {chosen.name}") from exc
    if not isinstance(raw, dict):
        raise ConfigError("配置文件根节点必须是对象")

    try:
        config = AppConfig.model_validate(raw)
    except Exception as exc:  # pydantic ValidationError has a long repr
        raise ConfigError(f"配置校验失败: {exc}") from exc

    provider_env = os.getenv("EMOTION_TTS_PROVIDER")
    if provider_env:
        config.tts.provider = provider_env.strip().lower()
        if config.tts.provider != "auto" and config.tts.provider not in KNOWN_PROVIDERS:
            raise ConfigError(f"EMOTION_TTS_PROVIDER 未知: {config.tts.provider}")

    fallback_env = os.getenv("EMOTION_TTS_FALLBACK_PROVIDERS")
    if fallback_env is not None:
        fallback_names = _split_env_list(fallback_env)
        for name in fallback_names:
            if name.strip().lower() not in KNOWN_PROVIDERS:
                raise ConfigError(f"EMOTION_TTS_FALLBACK_PROVIDERS 含未知厂商: {name}")
        config.tts.fallback_providers = [name.strip().lower() for name in fallback_names]

    data_env = os.getenv("EMOTION_TTS_DATA_DIR")
    if data_env:
        config.tts.data_dir = data_env

    host_env = os.getenv("EMOTION_TTS_HOST")
    if host_env:
        config.server.host = host_env.strip()
    port_env = os.getenv("EMOTION_TTS_PORT")
    if port_env:
        try:
            port = int(port_env)
        except ValueError as exc:
            raise ConfigError("EMOTION_TTS_PORT 必须是整数") from exc
        if not 1 <= port <= 65535:
            raise ConfigError("EMOTION_TTS_PORT 必须在 1..65535 之间")
        config.server.port = port

    # Non-secret endpoint overrides are convenient for gateways and local setups.
    if os.getenv("OPENAI_BASE_URL"):
        _ensure_provider(config, "openai").base_url = os.environ["OPENAI_BASE_URL"].strip()
    if os.getenv("AZURE_SPEECH_REGION"):
        _ensure_provider(config, "azure").region = os.environ["AZURE_SPEECH_REGION"].strip()
    if os.getenv("MINIMAX_BASE_URL"):
        _ensure_provider(config, "minimax").base_url = os.environ["MINIMAX_BASE_URL"].strip()
    if os.getenv("MINIMAX_GROUP_ID"):
        _ensure_provider(config, "minimax").group_id = os.environ["MINIMAX_GROUP_ID"].strip()
    if os.getenv("LOCAL_TTS_URL"):
        _ensure_provider(config, "local").base_url = os.environ["LOCAL_TTS_URL"].strip()

    config.root = root
    config.config_path = chosen
    config.styles_path = _resolve_path(root, config.tts.styles_file)
    config.routes_path = _resolve_path(root, config.tts.routes_file)
    config.data_path = _resolve_path(root, config.tts.data_dir)

    for required in (config.styles_path, config.routes_path):
        if not required.exists():
            raise ConfigError(f"缺少配置文件: {required}")

    return config


def _ensure_provider(config: AppConfig, name: str) -> ProviderConfig:
    if name not in config.providers:
        config.providers[name] = ProviderConfig()
    return config.providers[name]
