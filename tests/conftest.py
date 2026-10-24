from __future__ import annotations

from uuid import uuid4

import pytest

from emotional_tts.cache import AudioCache
from emotional_tts.config import load_config
from emotional_tts.contracts import InputEvent, InputTags
from emotional_tts.providers.registry import ProviderRegistry
from emotional_tts.routing import load_router
from emotional_tts.storage import RecordStore
from emotional_tts.styles import load_stylebook
from tests.fakes import FakeProvider


@pytest.fixture
def config(tmp_path):
    cfg = load_config()
    cfg.data_path = tmp_path
    cfg.tts.provider = "auto"
    cfg.tts.fallback_providers = []
    cfg.tts.cache.max_files = 20
    cfg.tts.first_chunk_timeout_seconds = 2
    cfg.tts.total_timeout_seconds = 5
    return cfg


@pytest.fixture
def styles(config):
    return load_stylebook(config.styles_path)


@pytest.fixture
def router(config):
    return load_router(config.routes_path)


@pytest.fixture
def fake_a():
    return FakeProvider("fake_a")


@pytest.fixture
def fake_b():
    return FakeProvider("fake_b")


@pytest.fixture
def registry(fake_a, fake_b):
    return ProviderRegistry(
        {"fake_a": fake_a, "fake_b": fake_b},
        auto_order=["fake_a", "fake_b"],
        default_provider="auto",
    )


@pytest.fixture
def cache(tmp_path):
    return AudioCache(tmp_path / "cache")


@pytest.fixture
def records(tmp_path):
    store = RecordStore(tmp_path / "records.sqlite3")
    yield store
    store.close()


def make_event(
    *,
    content: str = "今天辛苦了，先慢慢休息一下。",
    emotion: str = "comfort",
    content_type: str = "text",
    intent: str = "reply",
    provider: str | None = None,
) -> InputEvent:
    return InputEvent(
        event_id=uuid4(),
        session_id=uuid4(),
        content=content,
        stream=True,
        tags=InputTags(emotion=emotion, type=content_type, intent=intent),
        provider=provider,
    )
