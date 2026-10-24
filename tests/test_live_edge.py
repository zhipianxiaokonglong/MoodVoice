from __future__ import annotations

import os

import pytest

from emotional_tts.cache import AudioCache
from emotional_tts.config import ProviderConfig
from emotional_tts.pipeline import OutputPipeline
from emotional_tts.providers.edge import EdgeProvider
from emotional_tts.providers.registry import ProviderRegistry
from tests.conftest import make_event


@pytest.mark.live
@pytest.mark.skipif(
    os.getenv("MOODVOICE_LIVE_TESTS") != "1",
    reason="set MOODVOICE_LIVE_TESTS=1 to call the real edge-tts service",
)
async def test_live_edge_synthesizes_emotional_audio(config, styles, router, tmp_path):
    provider = EdgeProvider(ProviderConfig(enabled=True, voice="zh-CN-XiaoxiaoNeural"))
    registry = ProviderRegistry({"edge": provider}, auto_order=["edge"])
    cache = AudioCache(tmp_path / "live-cache")
    pipeline = OutputPipeline(
        config,
        styles=styles,
        router=router,
        registry=registry,
        cache=cache,
    )
    try:
        output = await pipeline.process(
            make_event(content="今天辛苦了，先慢慢呼吸一下。", emotion="comfort"),
            provider_override="edge",
        )
    finally:
        await registry.close()

    assert output.status == "synthesized"
    assert output.provider == "edge"
    assert output.audio_ref
    assert output.timings.first_chunk_ms is not None
    assert output.timings.first_chunk_ms < 15000
    filename = output.audio_ref.rsplit("/", 1)[-1]
    assert cache.resolve(filename).stat().st_size > 1000
