"""Intent -> downstream route."""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field

from .contracts import RoutePayload
from .errors import ConfigError


class IntentRoute(BaseModel):
    model_config = ConfigDict(extra="ignore")

    destination: str
    synthesize: bool = False
    reason: str = ""


class RoutesModel(BaseModel):
    model_config = ConfigDict(extra="ignore")

    version: int = 1
    unknown_intent: str = "ui.text_reply"
    intents: dict[str, IntentRoute] = Field(default_factory=dict)


class IntentRouter:
    def __init__(self, model: RoutesModel, path: Path | None = None) -> None:
        self.model = model
        self.path = path

    def route(self, intent: str | None) -> RoutePayload:
        normalized = (intent or "").strip().lower()
        definition = self.model.intents.get(normalized)
        if definition is None:
            return RoutePayload(
                intent=normalized or "unknown",
                destination=self.model.unknown_intent,
                synthesize=False,
                reason="unknown intent -> text-only fallback route",
            )
        return RoutePayload(
            intent=normalized,
            destination=definition.destination,
            synthesize=definition.synthesize,
            reason=definition.reason or f"route for {normalized}",
        )

    def is_known(self, intent: str | None) -> bool:
        return (intent or "").strip().lower() in self.model.intents

    def describe(self) -> dict[str, object]:
        return {
            "version": self.model.version,
            "unknown_intent": self.model.unknown_intent,
            "intents": {name: route.model_dump() for name, route in self.model.intents.items()},
        }


def load_router(path: str | Path) -> IntentRouter:
    source = Path(path)
    if not source.exists():
        raise ConfigError(f"路由配置不存在: {source}")
    try:
        raw = yaml.safe_load(source.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise ConfigError(f"路由配置 YAML 解析失败: {source.name}") from exc
    try:
        model = RoutesModel.model_validate(raw)
    except Exception as exc:
        raise ConfigError(f"路由配置无效: {exc}") from exc
    if not model.intents:
        raise ConfigError("路由配置至少需要一个 intent")
    return IntentRouter(model, source)
