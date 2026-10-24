from __future__ import annotations

import pytest

from emotional_tts.errors import ProviderError
from emotional_tts.pipeline import OutputPipeline, SynthesisHooks
from tests.conftest import make_event
from tests.fakes import FakeProvider


def build_pipeline(config, styles, router, registry, cache, records=None):
    return OutputPipeline(
        config,
        styles=styles,
        router=router,
        registry=registry,
        cache=cache,
        records=records,
    )


async def test_reply_synthesizes_and_records_structured_data(
    config, styles, router, registry, cache, records
):
    pipeline = build_pipeline(config, styles, router, registry, cache, records)
    output = await pipeline.process(make_event(), provider_override="fake_a")

    assert output.status == "synthesized"
    assert output.degraded is False
    assert output.provider == "fake_a"
    assert output.style.emotion == "comfort"
    assert output.audio_ref and output.audio_ref.startswith("/api/v1/audio/")
    assert output.audio_ref.endswith(".mp3")
    assert output.audio_mime == "audio/mpeg"
    assert output.speech_text == output.text
    assert output.timings.first_chunk_ms is not None
    assert output.timings.total_ms >= output.timings.first_chunk_ms
    assert output.diagnostics.privacy["text_sent_to"][0]["provider"] == "fake_a"
    assert output.diagnostics.emotion_mapping["mapping_hit"] is True
    assert output.diagnostics.provider_params["selected"]["emotion"] == "comfort"

    filename = output.audio_ref.rsplit("/", 1)[-1]
    assert cache.resolve(filename).read_bytes() == b"ID3fake-audio-1fake-audio-2"
    stored = records.list()
    assert len(stored) == 1
    assert stored[0]["speech_chars"] > 0
    assert "text" not in stored[0]
    assert "content" not in stored[0]


async def test_intent_tool_routes_text_only_without_calling_provider(
    config, styles, router, registry, cache
):
    pipeline = build_pipeline(config, styles, router, registry, cache)
    fake = registry.get("fake_a")
    output = await pipeline.process(make_event(intent="tool"), provider_override="fake_a")

    assert output.status == "text_only"
    assert output.audio_ref is None
    assert output.degraded is False
    assert output.route.destination == "ui.tool_result"
    assert fake.requests == []
    assert output.diagnostics.privacy["text_sent_to"] == []


async def test_unknown_emotion_is_visible_in_diagnostics(config, styles, router, registry, cache):
    pipeline = build_pipeline(config, styles, router, registry, cache)
    output = await pipeline.process(
        make_event(emotion="mystery-emotion"), provider_override="fake_a"
    )
    assert output.style.emotion == "neutral"
    assert output.style.source_tag == "mystery-emotion"
    assert output.style.mapping_hit is False
    assert output.diagnostics.emotion_mapping["fallback_reason"] == "unknown_tag"


async def test_code_content_is_not_read_aloud(config, styles, router, registry, cache):
    pipeline = build_pipeline(config, styles, router, registry, cache)
    output = await pipeline.process(
        make_event(content="```python\ndef secret():\n    return 42\n```", content_type="code"),
        provider_override="fake_a",
    )
    assert output.render.type == "code"
    assert "def secret" not in output.speech_text
    assert "已经显示" in output.speech_text
    request = registry.get("fake_a").requests[0]
    assert "def secret" not in request.text


async def test_provider_switch_uses_the_requested_vendor(config, styles, router, registry, cache):
    pipeline = build_pipeline(config, styles, router, registry, cache)
    first = await pipeline.process(make_event(), provider_override="fake_a")
    second = await pipeline.process(make_event(), provider_override="fake_b")
    assert first.provider == "fake_a"
    assert second.provider == "fake_b"
    assert first.requested_provider == "fake_a"
    assert second.requested_provider == "fake_b"


async def test_invalid_style_is_reported_at_style_resolution(config, styles, router, cache):
    class BadStyleProvider(FakeProvider):
        def request_preview(self, request):
            raise ProviderError(
                provider=self.name,
                code="invalid_style",
                message="bad vendor style",
                stage="style_resolution",
                details={"parameter": "style", "value": "not-a-style"},
            )

    bad = BadStyleProvider("bad_style")
    from emotional_tts.providers.registry import ProviderRegistry

    registry = ProviderRegistry({"bad_style": bad}, auto_order=["bad_style"])
    pipeline = build_pipeline(config, styles, router, registry, cache)
    output = await pipeline.process(make_event(), provider_override="bad_style")
    assert output.status == "text_fallback"
    attempt = output.diagnostics.provider_attempts[0]
    assert attempt["stage"] == "style_resolution"
    assert attempt["code"] == "invalid_style"
    assert attempt["details"]["parameter"] == "style"


async def test_provider_failure_falls_back_to_explicit_fallback_provider(
    config, styles, router, cache
):
    failing = FakeProvider("failing", fail_at=0, fail_code="upstream_error")
    backup = FakeProvider("backup")
    from emotional_tts.providers.registry import ProviderRegistry

    registry = ProviderRegistry(
        {"failing": failing, "backup": backup},
        auto_order=["failing", "backup"],
    )
    config.tts.fallback_providers = ["backup"]
    pipeline = build_pipeline(config, styles, router, registry, cache)
    output = await pipeline.process(make_event(), provider_override="failing")

    assert output.status == "synthesized"
    assert output.provider == "backup"
    assert len(failing.requests) == 1
    assert len(backup.requests) == 1
    attempts = output.diagnostics.provider_attempts
    assert [a["provider"] for a in attempts] == ["failing", "backup"]
    assert attempts[0]["outcome"] == "failed"
    assert attempts[1]["outcome"] == "succeeded"
    assert output.diagnostics.privacy["text_sent_to"][0]["provider"] == "failing"


async def test_all_providers_fail_to_plain_text(config, styles, router, cache):
    first = FakeProvider("first", fail_at=0)
    second = FakeProvider("second", fail_at=0, fail_code="rate_limited")
    from emotional_tts.providers.registry import ProviderRegistry

    registry = ProviderRegistry(
        {"first": first, "second": second},
        auto_order=["first", "second"],
    )
    config.tts.fallback_providers = ["second"]
    pipeline = build_pipeline(config, styles, router, registry, cache)
    output = await pipeline.process(make_event(), provider_override="first")

    assert output.status == "text_fallback"
    assert output.degraded is True
    assert output.audio_ref is None
    assert output.degradation.code == "tts_unavailable"
    assert len(output.diagnostics.provider_attempts) == 2
    assert cache.stats()["files"] == 0
    assert output.speech_text


async def test_unready_provider_degrades_without_network(config, styles, router, cache):
    unavailable = FakeProvider("unavailable", ready=False)
    from emotional_tts.providers.registry import ProviderRegistry

    registry = ProviderRegistry({"unavailable": unavailable}, auto_order=["unavailable"])
    pipeline = build_pipeline(config, styles, router, registry, cache)
    output = await pipeline.process(make_event(), provider_override="unavailable")

    assert output.status == "text_fallback"
    assert output.provider is None
    assert unavailable.requests == []
    attempt = output.diagnostics.provider_attempts[0]
    assert attempt["code"] == "missing_credentials"
    assert attempt["missing"] == ["FAKE_KEY"]


async def test_streaming_hooks_receive_metadata_then_bytes(config, styles, router, registry, cache):
    pipeline = build_pipeline(config, styles, router, registry, cache)
    starts: list[tuple[str, str]] = []
    chunks: list[bytes] = []
    order: list[str] = []

    async def on_start(output, mime):
        order.append("start")
        starts.append((output.provider, mime))

    async def on_chunk(chunk):
        order.append("chunk")
        chunks.append(chunk)

    prepared = pipeline.prepare(make_event(), provider_override="fake_a")
    output = await pipeline.synthesize(
        prepared, hooks=SynthesisHooks(on_start=on_start, on_chunk=on_chunk)
    )

    assert starts == [("fake_a", "audio/mpeg")]
    assert b"".join(chunks) == b"ID3fake-audio-1fake-audio-2"
    assert order[0] == "start"
    assert order.count("chunk") == 2
    assert output.audio_ref


async def test_first_chunk_timeout_degrades(config, styles, router, cache):
    slow = FakeProvider("slow", delay=0.2)
    from emotional_tts.providers.registry import ProviderRegistry

    registry = ProviderRegistry({"slow": slow}, auto_order=["slow"])
    config.tts.first_chunk_timeout_seconds = 0.05
    pipeline = build_pipeline(config, styles, router, registry, cache)
    output = await pipeline.process(make_event(), provider_override="slow")

    assert output.status == "text_fallback"
    assert output.diagnostics.provider_attempts[0]["code"] == "timeout"
    assert output.diagnostics.provider_attempts[0]["stage"] == "provider_ttfb"


async def test_midstream_failure_aborts_partial_cache(config, styles, router, cache, records):
    flaky = FakeProvider("flaky", fail_at=1)
    from emotional_tts.providers.registry import ProviderRegistry

    registry = ProviderRegistry({"flaky": flaky}, auto_order=["flaky"])
    pipeline = build_pipeline(config, styles, router, registry, cache, records)
    starts = []

    async def on_start(output, mime):
        starts.append(output)

    async def on_chunk(chunk):
        return None

    prepared = pipeline.prepare(make_event(), provider_override="flaky")
    with pytest.raises(ProviderError) as info:
        await pipeline.synthesize(
            prepared, hooks=SynthesisHooks(on_start=on_start, on_chunk=on_chunk)
        )
    assert info.value.details["midstream"] is True
    assert starts
    assert cache.stats()["files"] == 0
    stored = records.list()
    assert len(stored) == 1
    assert stored[0]["status"] == "text_fallback"
    assert stored[0]["error_type"] == "stream_interrupted"


async def test_midstream_failure_without_hooks_can_still_fallback(config, styles, router, cache):
    flaky = FakeProvider("flaky", fail_at=1)
    backup = FakeProvider("backup")
    from emotional_tts.providers.registry import ProviderRegistry

    registry = ProviderRegistry({"flaky": flaky, "backup": backup}, auto_order=["flaky", "backup"])
    config.tts.fallback_providers = ["backup"]
    pipeline = build_pipeline(config, styles, router, registry, cache)
    output = await pipeline.process(make_event(), provider_override="flaky")

    assert output.status == "synthesized"
    assert output.provider == "backup"
    assert cache.stats()["files"] == 1


async def test_content_too_long_is_rejected(config, styles, router, registry, cache):
    from emotional_tts.errors import ValidationError

    pipeline = build_pipeline(config, styles, router, registry, cache)
    config.app.max_input_chars = 10
    with pytest.raises(ValidationError):
        pipeline.prepare(make_event(content="x" * 11))
