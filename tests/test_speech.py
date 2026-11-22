from __future__ import annotations

from emotional_tts.speech import (
    SpeechSegment,
    WavFormat,
    build_speech_plan,
    concat_wav,
    read_wav,
    split_paragraphs,
    split_sentences,
    write_wav,
)


def test_split_paragraphs_and_sentences_keep_punctuation():
    text = "第一段。第二句！\n\n第二段？最后一句；"
    assert split_paragraphs(text) == ["第一段。第二句！", "第二段？最后一句；"]
    assert split_sentences("第一段。第二句！") == ["第一段。", "第二句！"]


def test_plan_marks_paragraph_pauses_and_limits_segments():
    text = "第一段。\n\n第二段。\n\n第三段。\n\n第四段。"
    plan = build_speech_plan(
        text,
        sentence_pause_ms=50,
        paragraph_pause_ms=300,
        min_segment_chars=1,
        max_segments=2,
    )
    assert len(plan.segments) == 2
    assert plan.paragraphs >= 1
    assert plan.pause_total_ms >= 300
    assert plan.to_text().count("\n\n") >= 1


def _wav(ms: int = 100) -> bytes:
    fmt = WavFormat(channels=1, sample_width=2, frame_rate=24_000)
    frames = int(fmt.frame_rate * ms / 1000)
    return write_wav(b"\x00\x00" * frames, fmt)


def test_concat_wav_inserts_silence():
    parts = [_wav(100), _wav(100)]
    audio = concat_wav(parts, [250], fade_ms=0)
    parsed = read_wav(audio)
    assert parsed.fmt.frame_rate == 24_000
    assert len(parsed.pcm) == int(24_000 * 0.45) * 2


def test_segment_public_view_does_not_expose_text():
    segment = SpeechSegment("很长的原文", 200, 1)
    public = segment.public()
    assert public["text_chars"] == 5
    assert public["pause_after_ms"] == 200
    assert "很长的原文" not in public


def test_plan_with_anchors_keeps_text_and_applies_paragraph_style():
    plan = build_speech_plan("第一段。\n\n第二段。", min_segment_chars=1)
    anchored = plan.with_anchors([{"paragraph": 1, "emotion": "encouragement", "intensity": 0.8}])
    assert anchored.to_text() == plan.to_text()
    assert any(
        segment.emotion == "encouragement" and segment.intensity == 0.8
        for segment in anchored.segments
    )
