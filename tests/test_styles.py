from __future__ import annotations

import pytest

from emotional_tts.errors import ConfigError
from emotional_tts.styles import StyleBook, StyleBookModel, normalize_tag


def test_normalize_tag_handles_prefix_case_and_separators():
    assert normalize_tag("  EMOTION: Happy  ") == "happy"
    assert normalize_tag("Comforting") == "comforting"
    assert normalize_tag("情绪/安慰") == "安慰"
    assert normalize_tag(None) == ""


def test_comfort_maps_to_canonical_profile(styles):
    resolved = styles.resolve("安慰")
    assert resolved.key == "comfort"
    assert resolved.mapping_hit is True
    assert resolved.speed == pytest.approx(0.92)
    assert resolved.label == "安慰"
    assert "温柔" in resolved.tone


def test_unknown_tag_falls_back_without_hiding_the_problem(styles):
    resolved = styles.resolve("not-a-real-emotion")
    assert resolved.key == "neutral"
    assert resolved.mapping_hit is False
    assert resolved.fallback_reason == "unknown_tag"
    assert resolved.source_tag == "not-a-real-emotion"


def test_missing_tag_is_explicitly_marked(styles):
    resolved = styles.resolve("")
    assert resolved.key == "neutral"
    assert resolved.mapping_hit is False
    assert resolved.fallback_reason == "missing_tag"


def test_provider_params_merge_mapping_and_provider_direction(styles):
    comfort = styles.resolve("comfort")
    azure = styles.provider_params(comfort, "azure")
    assert azure["style"] == "gentle"
    assert azure["style_degree"] == 1.6
    assert azure["speed"] == pytest.approx(0.92)

    edge = styles.provider_params(comfort, "edge")
    assert edge["pitch"] == "-2Hz"

    openai = styles.provider_params(comfort, "openai")
    assert openai["voice"] == "sage"
    assert "caring friend" in openai["instructions"]


def test_duplicate_alias_is_a_config_error():
    raw = {
        "fallback_emotion": "neutral",
        "emotions": {
            "neutral": {
                "label": "中性",
                "tone": "平稳",
                "aliases": ["共享"],
            },
            "joy": {
                "label": "开心",
                "tone": "轻快",
                "aliases": ["共享"],
            },
        },
    }
    model = StyleBookModel.model_validate(raw)
    with pytest.raises(ConfigError, match="别名冲突"):
        StyleBook(model)


def test_style_speed_bounds_are_validated():
    with pytest.raises(ValueError):
        StyleBookModel.model_validate(
            {
                "fallback_emotion": "neutral",
                "emotions": {"neutral": {"label": "中性", "tone": "平稳", "speed": 3.0}},
            }
        )
