"""Test doubles. Not used by the production application."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Sequence

from emotional_tts.errors import ProviderError
from emotional_tts.providers.base import (
    ProviderCapabilities,
    SynthesisRequest,
    TTSProvider,
)


class FakeProvider(TTSProvider):
    capabilities = ProviderCapabilities(
        streaming=True,
        emotional_control="test",
        emotion_styles="test",
        requires_key=False,
        data_destination="in-process test provider",
        formats=("mp3",),
    )

    def __init__(
        self,
        name: str = "fake_a",
        *,
        ready: bool = True,
        chunks: Sequence[bytes] = (b"ID3fake-audio-1", b"fake-audio-2"),
        fail_at: int | None = None,
        fail_code: str = "upstream_error",
        delay: float = 0.0,
        fail_stage: str = "provider_synthesis",
    ) -> None:
        self.name = name
        self.display_name = f"Fake {name}"
        self._ready = ready
        self.chunks = list(chunks)
        self.fail_at = fail_at
        self.fail_code = fail_code
        self.delay = delay
        self.fail_stage = fail_stage
        self.requests: list[SynthesisRequest] = []
        self.closed = False

    def readiness(self) -> tuple[bool, list[str]]:
        return (self._ready, [] if self._ready else ["FAKE_KEY"])

    async def stream(self, request: SynthesisRequest) -> AsyncIterator[bytes]:
        self.requests.append(request)
        for index, chunk in enumerate(self.chunks):
            if self.fail_at == index:
                raise ProviderError(
                    provider=self.name,
                    code=self.fail_code,
                    message="fake provider failure",
                    stage=self.fail_stage,
                    retryable=True,
                )
            if self.delay:
                await asyncio.sleep(self.delay)
            yield chunk
        if self.fail_at is not None and self.fail_at >= len(self.chunks):
            raise ProviderError(
                provider=self.name,
                code=self.fail_code,
                message="fake provider late failure",
                stage=self.fail_stage,
                retryable=True,
            )

    async def close(self) -> None:
        self.closed = True
