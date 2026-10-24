"""Pluggable TTS providers."""

from .base import ProviderCapabilities, SynthesisRequest, TTSProvider
from .registry import ProviderRegistry, build_registry

__all__ = [
    "ProviderCapabilities",
    "ProviderRegistry",
    "SynthesisRequest",
    "TTSProvider",
    "build_registry",
]
