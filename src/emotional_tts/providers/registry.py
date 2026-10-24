"""Provider registry and lifecycle."""

from __future__ import annotations

from typing import Any

from ..config import AppConfig, ProviderConfig, Secrets
from .azure import AzureProvider
from .base import TTSProvider
from .edge import EdgeProvider
from .elevenlabs import ElevenLabsProvider
from .local_http import LocalHTTPProvider
from .minimax import MiniMaxProvider
from .openai import OpenAIProvider


class ProviderRegistry:
    def __init__(
        self,
        providers: dict[str, TTSProvider],
        *,
        auto_order: list[str],
        default_provider: str = "auto",
    ) -> None:
        self.providers = providers
        self.auto_order = [name for name in auto_order if name in providers]
        self.default_provider = default_provider

    def get(self, name: str) -> TTSProvider | None:
        return self.providers.get((name or "").strip().lower())

    def is_ready(self, name: str) -> bool:
        provider = self.get(name)
        return bool(provider and provider.ready)

    def missing(self, name: str) -> list[str]:
        provider = self.get(name)
        return provider.readiness()[1] if provider else ["unknown_provider"]

    def auto_provider(self) -> TTSProvider | None:
        for name in self.auto_order:
            provider = self.providers.get(name)
            if provider and provider.ready:
                return provider
        return None

    def first_ready_name(self) -> str | None:
        provider = self.auto_provider()
        return provider.name if provider else None

    def public_catalog(self) -> list[dict[str, Any]]:
        return [provider.public_descriptor() for provider in self.providers.values()]

    async def close(self) -> None:
        for provider in self.providers.values():
            try:
                await provider.close()
            except Exception:
                pass


def build_registry(config: AppConfig, secrets: Secrets) -> ProviderRegistry:
    timeout = config.tts.total_timeout_seconds
    get = lambda name: config.providers.get(name) or ProviderConfig()  # noqa: E731

    providers: dict[str, TTSProvider] = {
        "edge": EdgeProvider(get("edge"), timeout_seconds=timeout),
        "openai": OpenAIProvider(
            get("openai"), api_key=secrets.openai_api_key, timeout_seconds=timeout
        ),
        "elevenlabs": ElevenLabsProvider(
            get("elevenlabs"),
            api_key=secrets.elevenlabs_api_key,
            timeout_seconds=timeout,
        ),
        "azure": AzureProvider(
            get("azure"), api_key=secrets.azure_speech_key, timeout_seconds=timeout
        ),
        "minimax": MiniMaxProvider(
            get("minimax"), api_key=secrets.minimax_api_key, timeout_seconds=timeout
        ),
        "local": LocalHTTPProvider(
            get("local"),
            api_key=secrets.local_tts_api_key,
            timeout_seconds=timeout,
        ),
    }
    order = list(config.tts.auto_order)
    # Keep every known provider selectable even if it was omitted from the YAML.
    for name in providers:
        if name not in order:
            order.append(name)
    return ProviderRegistry(
        providers,
        auto_order=order,
        default_provider=config.tts.provider,
    )
