"""Provider-neutral speech planning and small WAV helpers.

A ``SpeechPlan`` is built by the rendering layer from the *speech text*, not from
the original Markdown. Providers may ignore it, use its breaks, or synthesize it
segment by segment; the public contract still exposes one ``audio_ref``.
"""

from __future__ import annotations

import io
import re
import wave
from dataclasses import dataclass, field
from typing import Any

_SENTENCE = re.compile(r"[^。！？；!?;\n]+[。！？；!?;]?")
_SPACE = re.compile(r"[ \t\u3000]+")


def normalize_speech_text(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = _SPACE.sub(" ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def split_paragraphs(text: str) -> list[str]:
    text = normalize_speech_text(text)
    if not text:
        return []
    parts = re.split(r"\n\s*\n", text)
    return [part.strip() for part in parts if part.strip()]


def split_sentences(text: str) -> list[str]:
    sentences = [match.group(0).strip() for match in _SENTENCE.finditer(text)]
    return [sentence for sentence in sentences if sentence]


@dataclass(frozen=True, slots=True)
class SpeechSegment:
    text: str
    pause_after_ms: int = 0
    paragraph_index: int = 0
    emotion: str | None = None
    intensity: float | None = None

    def public(self) -> dict[str, object]:
        return {
            "text_chars": len(self.text),
            "pause_after_ms": self.pause_after_ms,
            "paragraph_index": self.paragraph_index,
            "emotion": self.emotion,
            "intensity": self.intensity,
        }


@dataclass(frozen=True, slots=True)
class SpeechPlan:
    segments: tuple[SpeechSegment, ...] = ()
    source_chars: int = 0

    @property
    def paragraphs(self) -> int:
        return len({segment.paragraph_index for segment in self.segments})

    @property
    def pause_total_ms(self) -> int:
        return sum(max(0, segment.pause_after_ms) for segment in self.segments)

    def to_text(self, *, paragraph_separator: str = "\n\n") -> str:
        parts: list[str] = []
        previous_paragraph: int | None = None
        for segment in self.segments:
            if not segment.text:
                continue
            if previous_paragraph is not None and segment.paragraph_index != previous_paragraph:
                parts.append(paragraph_separator)
            parts.append(segment.text)
            previous_paragraph = segment.paragraph_index
        return "".join(parts)

    def public(self) -> dict[str, object]:
        return {
            "segments": len(self.segments),
            "paragraphs": self.paragraphs,
            "pause_total_ms": self.pause_total_ms,
            "source_chars": self.source_chars,
            "anchors": sum(1 for segment in self.segments if segment.emotion),
        }

    def with_anchors(self, anchors: list[Any]) -> SpeechPlan:
        if not anchors:
            return self

        def value(anchor: Any, key: str, default: Any = None) -> Any:
            if isinstance(anchor, dict):
                return anchor.get(key, default)
            return getattr(anchor, key, default)

        mapping = {
            int(value(anchor, "paragraph", -1)): anchor
            for anchor in anchors
            if value(anchor, "emotion")
        }
        if not mapping:
            return self
        segments = tuple(
            SpeechSegment(
                text=segment.text,
                pause_after_ms=segment.pause_after_ms,
                paragraph_index=segment.paragraph_index,
                emotion=value(mapping.get(segment.paragraph_index), "emotion"),
                intensity=value(mapping.get(segment.paragraph_index), "intensity"),
            )
            for segment in self.segments
        )
        return SpeechPlan(segments=segments, source_chars=self.source_chars)


def build_speech_plan(
    text: str,
    *,
    sentence_pause_ms: int = 80,
    paragraph_pause_ms: int = 350,
    min_segment_chars: int = 8,
    max_segments: int = 0,
) -> SpeechPlan:
    """Split structured speech text into segments with pause hints.

    Boundaries are paragraph first, sentence second. Very short sentences are
    merged into the previous segment so we do not create choppy TTS requests.
    """

    paragraphs = split_paragraphs(text)
    segments: list[SpeechSegment] = []
    for paragraph_index, paragraph in enumerate(paragraphs):
        sentences = split_sentences(paragraph)
        if not sentences:
            continue
        for index, sentence in enumerate(sentences):
            pause = paragraph_pause_ms if index == len(sentences) - 1 else sentence_pause_ms
            if segments and segments[-1].paragraph_index == paragraph_index:
                previous = segments[-1]
                if len(previous.text) < min_segment_chars:
                    segments[-1] = SpeechSegment(
                        text=previous.text + sentence,
                        pause_after_ms=pause,
                        paragraph_index=paragraph_index,
                    )
                    continue
            segments.append(
                SpeechSegment(
                    text=sentence,
                    pause_after_ms=pause,
                    paragraph_index=paragraph_index,
                )
            )

    if len(segments) > 1:
        segments[-1] = SpeechSegment(
            text=segments[-1].text,
            pause_after_ms=0,
            paragraph_index=segments[-1].paragraph_index,
        )
    if max_segments and len(segments) > max_segments:
        segments = _merge_segments(segments, max_segments)
    return SpeechPlan(segments=tuple(segments), source_chars=len(text))


def _merge_segments(segments: list[SpeechSegment], limit: int) -> list[SpeechSegment]:
    merged = list(segments)
    while len(merged) > limit:
        index = min(
            range(len(merged) - 1),
            key=lambda item: len(merged[item].text) + len(merged[item + 1].text),
        )
        left, right = merged[index], merged[index + 1]
        merged[index : index + 2] = [
            SpeechSegment(
                text=left.text + right.text,
                pause_after_ms=right.pause_after_ms,
                paragraph_index=left.paragraph_index,
            )
        ]
    return merged


@dataclass(frozen=True, slots=True)
class WavFormat:
    channels: int
    sample_width: int
    frame_rate: int


@dataclass(slots=True)
class WavAudio:
    fmt: WavFormat
    pcm: bytes = b""
    extras: dict[str, object] = field(default_factory=dict)


def read_wav(data: bytes) -> WavAudio:
    with wave.open(io.BytesIO(data), "rb") as source:
        if source.getcomptype() != "NONE":
            raise ValueError("compressed WAV is not supported")
        fmt = WavFormat(
            channels=source.getnchannels(),
            sample_width=source.getsampwidth(),
            frame_rate=source.getframerate(),
        )
        return WavAudio(fmt=fmt, pcm=source.readframes(source.getnframes()))


def write_wav(pcm: bytes, fmt: WavFormat) -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as target:
        target.setnchannels(fmt.channels)
        target.setsampwidth(fmt.sample_width)
        target.setframerate(fmt.frame_rate)
        target.writeframes(pcm)
    return buffer.getvalue()


def silence_pcm(ms: int, fmt: WavFormat) -> bytes:
    if ms <= 0:
        return b""
    frames = int(fmt.frame_rate * ms / 1000)
    return b"\x00" * frames * fmt.channels * fmt.sample_width


def concat_wav(
    parts: list[bytes],
    pauses_ms: list[int],
    *,
    fade_ms: int = 30,
) -> bytes:
    """Concatenate same-format WAV parts, inserting silence between them."""

    if not parts:
        raise ValueError("no WAV parts")
    first = read_wav(parts[0])
    fmt = first.fmt
    chunks: list[bytes] = []
    for index, part in enumerate(parts):
        audio = read_wav(part)
        if audio.fmt != fmt:
            raise ValueError("WAV formats differ")
        pcm = audio.pcm
        if index > 0 and fade_ms > 0:
            frames = int(fmt.frame_rate * fade_ms / 1000)
            pcm = _fade_in(pcm, frames, fmt)
        chunks.append(pcm)
        if index < len(parts) - 1 and index < len(pauses_ms):
            chunks.append(silence_pcm(pauses_ms[index], fmt))
    return write_wav(b"".join(chunks), fmt)


def _fade_in(pcm: bytes, frames: int, fmt: WavFormat) -> bytes:
    if frames <= 0:
        return pcm
    frame_bytes = fmt.channels * fmt.sample_width
    total_frames = len(pcm) // frame_bytes
    fade_frames = min(frames, total_frames)
    if fade_frames <= 1:
        return pcm
    buffer = bytearray(pcm)
    for frame in range(fade_frames):
        gain = frame / fade_frames
        offset = frame * frame_bytes
        for channel in range(fmt.channels):
            sample_offset = offset + channel * fmt.sample_width
            sample = int.from_bytes(
                buffer[sample_offset : sample_offset + fmt.sample_width],
                "little",
                signed=True,
            )
            sample = int(sample * gain)
            buffer[sample_offset : sample_offset + fmt.sample_width] = sample.to_bytes(
                fmt.sample_width, "little", signed=True
            )
    return bytes(buffer)
