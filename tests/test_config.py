from __future__ import annotations

import pytest

from emotional_tts.config import load_config
from emotional_tts.errors import ConfigError


def test_environment_can_select_provider_and_fallbacks(monkeypatch):
    monkeypatch.setenv("EMOTION_TTS_PROVIDER", "edge")
    monkeypatch.setenv("EMOTION_TTS_FALLBACK_PROVIDERS", "local, edge")
    config = load_config()
    assert config.tts.provider == "edge"
    assert config.tts.fallback_providers == ["local", "edge"]


def test_unknown_fallback_provider_is_rejected(monkeypatch):
    monkeypatch.setenv("EMOTION_TTS_FALLBACK_PROVIDERS", "not-a-vendor")
    with pytest.raises(ConfigError, match="未知厂商"):
        load_config()


def test_data_dir_env_override(monkeypatch, tmp_path):
    monkeypatch.setenv("EMOTION_TTS_DATA_DIR", str(tmp_path / "private-data"))
    config = load_config()
    assert config.data_path == (tmp_path / "private-data").resolve()
