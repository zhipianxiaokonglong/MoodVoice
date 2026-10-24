"""Emotion tag -> voice style resolution.

The upstream classifier owns the label. This module only normalizes aliases,
resolves one canonical emotion and exposes vendor-specific direction. It never
infers emotion from text.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .errors import ConfigError


def normalize_tag(value: str | None) -> str:
    """Normalize an upstream label without changing its language."""

    if value is None:
        return ""
    text = unicodedata.normalize("NFKC", str(value)).strip().lower()
    text = re.sub(r"^(emotion|emotion_tag|情绪|情感)\s*[:/|]\s*", "", text)
    text = re.sub(r"[\s_\-]+", " ", text)
    return text.strip()


class EmotionDefinition(BaseModel):
    model_config = ConfigDict(extra="ignore")

    label: str
    tone: str
    description: str = ""
    speed: float = Field(default=1.0, ge=0.5, le=2.0)
    pitch_semitones: float = Field(default=0.0, ge=-12, le=12)
    volume_db: float = Field(default=0.0, ge=-12, le=6)
    aliases: list[str] = Field(default_factory=list)
    provider_overrides: dict[str, dict[str, Any]] = Field(default_factory=dict)

    @field_validator("label", "tone")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("must not be blank")
        return value.strip()


class StyleBookModel(BaseModel):
    model_config = ConfigDict(extra="ignore")

    version: int = 1
    fallback_emotion: str = "neutral"
    emotions: dict[str, EmotionDefinition]

    @model_validator(mode="after")
    def _fallback_exists(self) -> StyleBookModel:
        if self.fallback_emotion not in self.emotions:
            raise ValueError("fallback_emotion must reference a defined emotion")
        return self


@dataclass(frozen=True)
class ResolvedEmotion:
    key: str
    label: str
    tone: str
    description: str
    speed: float
    pitch_semitones: float
    volume_db: float
    source_tag: str
    mapping_hit: bool
    fallback_reason: str | None
    provider_overrides: dict[str, dict[str, Any]]

    def public_style(self) -> dict[str, Any]:
        return {
            "emotion": self.key,
            "label": self.label,
            "speed": round(self.speed, 2),
            "tone": self.tone,
            "description": self.description,
            "pitch_semitones": round(self.pitch_semitones, 2),
            "source_tag": self.source_tag,
            "mapping_hit": self.mapping_hit,
        }


class StyleBook:
    def __init__(self, model: StyleBookModel, path: Path | None = None) -> None:
        self.model = model
        self.path = path
        self._alias_index: dict[str, str] = {}
        for key, definition in model.emotions.items():
            self._register_alias(normalize_tag(key), key)
            for alias in definition.aliases:
                self._register_alias(normalize_tag(alias), key)

    def _register_alias(self, alias: str, emotion: str) -> None:
        if not alias:
            return
        previous = self._alias_index.get(alias)
        if previous and previous != emotion:
            raise ConfigError(f"情感别名冲突: '{alias}' 同时映射到 {previous} 和 {emotion}")
        self._alias_index[alias] = emotion

    @property
    def emotions(self) -> dict[str, EmotionDefinition]:
        return self.model.emotions

    @property
    def fallback_key(self) -> str:
        return self.model.fallback_emotion

    def resolve(self, tag: str | None) -> ResolvedEmotion:
        normalized = normalize_tag(tag)
        source = "" if tag is None else str(tag).strip()

        if not normalized:
            key = self.fallback_key
            reason = "missing_tag"
            hit = False
        else:
            key = self._alias_index.get(normalized, self.fallback_key)
            hit = key in self._alias_index.values() and normalized in self._alias_index
            reason = None if hit else "unknown_tag"

        definition = self.model.emotions[key]
        return ResolvedEmotion(
            key=key,
            label=definition.label,
            tone=definition.tone,
            description=definition.description,
            speed=definition.speed,
            pitch_semitones=definition.pitch_semitones,
            volume_db=definition.volume_db,
            source_tag=source,
            mapping_hit=hit,
            fallback_reason=reason,
            provider_overrides=definition.provider_overrides,
        )

    def provider_params(self, emotion: ResolvedEmotion, provider: str) -> dict[str, Any]:
        params = dict(emotion.provider_overrides.get(provider, {}))
        params.setdefault("emotion", emotion.key)
        params.setdefault("tone", emotion.tone)
        params.setdefault("speed", round(emotion.speed, 3))
        params.setdefault("pitch_semitones", round(emotion.pitch_semitones, 3))
        params.setdefault("volume_db", round(emotion.volume_db, 3))
        return params

    def describe(self) -> dict[str, Any]:
        return {
            "version": self.model.version,
            "fallback_emotion": self.fallback_key,
            "emotion_count": len(self.model.emotions),
            "aliases": sorted(self._alias_index),
        }


def load_stylebook(path: str | Path) -> StyleBook:
    source = Path(path)
    if not source.exists():
        raise ConfigError(f"风格映射文件不存在: {source}")
    try:
        raw = yaml.safe_load(source.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise ConfigError(f"风格映射 YAML 解析失败: {source.name}") from exc
    try:
        model = StyleBookModel.model_validate(raw)
    except Exception as exc:
        raise ConfigError(f"风格映射配置无效: {exc}") from exc
    return StyleBook(model, source)
