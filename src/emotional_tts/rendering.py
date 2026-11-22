"""Content-type aware rendering and speech shaping.

The rendered ``text`` always remains byte-for-byte identical to the upstream
content. ``speech_text`` is a separate, safer script for TTS: code fences are
not read symbol-by-symbol, list markers are removed, and Markdown syntax is
flattened. This is presentation logic, not content classification.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from .contracts import RenderBlock, RenderPayload
from .speech import SpeechPlan, build_speech_plan

TYPE_ALIASES = {
    "text": "text",
    "plain": "text",
    "plaintext": "text",
    "string": "text",
    "markdown": "markdown",
    "md": "markdown",
    "code": "code",
    "snippet": "code",
    "list": "list",
    "bullets": "list",
    "ul": "list",
    "ol": "list",
    "table": "table",
    "grid": "table",
    "json": "json",
    "mixed": "mixed",
    "文本": "text",
    "纯文本": "text",
    "代码": "code",
    "列表": "list",
    "表格": "table",
    "混合": "mixed",
}

_CODE_FENCE = re.compile(r"```(?P<lang>[A-Za-z0-9_+.-]*)[ \t]*\n?(?P<body>.*?)```", re.DOTALL)
_MD_IMAGE = re.compile(r"!\[([^\]]*)\]\([^)]+\)")
_MD_LINK = re.compile(r"\[([^\]]+)\]\([^)]+\)")
_MD_HEADING = re.compile(r"^\s{0,3}#{1,6}\s+", re.MULTILINE)
_MD_EMPHASIS = re.compile(r"(\*\*|__|\*|_|~~)")
_MD_INLINE_CODE = re.compile(r"`([^`]+)`")
_MD_LIST_LINE = re.compile(r"^\s{0,3}(?:[-*+]|\d{1,3}[.)、])\s+", re.MULTILINE)
_SPEAKABLE = re.compile(r"[A-Za-z0-9\u4e00-\u9fff]")
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def normalize_type(value: str | None) -> str:
    key = (value or "text").strip().lower()
    return TYPE_ALIASES.get(key, "text")


@dataclass
class RenderResult:
    render: RenderPayload
    speech_text: str
    meta: dict[str, Any] = field(default_factory=dict)
    plan: SpeechPlan = field(default_factory=SpeechPlan)


def shape_content(
    content: str,
    declared_type: str | None,
    *,
    max_speech_chars: int = 3000,
    sentence_pause_ms: int = 80,
    paragraph_pause_ms: int = 350,
    min_segment_chars: int = 8,
    max_segments: int = 0,
) -> RenderResult:
    normalized = normalize_type(declared_type)
    if normalized == "code":
        result = _shape_code(content)
    elif normalized == "list":
        result = _shape_list(content)
    elif normalized == "markdown":
        result = _shape_markdown(content)
    elif normalized == "table":
        result = _shape_table(content)
    elif normalized == "json":
        result = _shape_json(content)
    elif normalized == "mixed":
        result = _shape_mixed(content)
    else:
        result = RenderResult(
            render=RenderPayload(
                type="text",
                mode="plain",
                copyable=False,
                blocks=[RenderBlock(kind="text", content=content)],
            ),
            speech_text=_flatten_for_speech(content),
        )

    speech = _clean_speech(result.speech_text)
    truncated = False
    if len(speech) > max_speech_chars:
        speech = _truncate_speech(speech, max_speech_chars)
        speech = f"{speech} 内容有点长，我先读到这里。"
        truncated = True
    if not speech:
        speech = "内容已经准备好，我放在屏幕上了。"

    plan = build_speech_plan(
        speech,
        sentence_pause_ms=sentence_pause_ms,
        paragraph_pause_ms=paragraph_pause_ms,
        min_segment_chars=min_segment_chars,
        max_segments=max_segments,
    )
    result.speech_text = speech
    result.plan = plan
    result.meta = {
        "requested_type": declared_type or "",
        "normalized_type": normalized,
        "speech_chars": len(speech),
        "source_chars": len(content),
        "speech_truncated": truncated,
        "speech_segments": len(plan.segments),
        "speech_paragraphs": plan.paragraphs,
        "speech_pause_ms": plan.pause_total_ms,
        "code_blocks": sum(1 for block in result.render.blocks if block.kind == "code"),
        "list_items": sum(1 for block in result.render.blocks if block.kind == "list_item"),
    }
    return result


def _shape_code(content: str) -> RenderResult:
    blocks: list[RenderBlock] = []
    outside: list[str] = []
    cursor = 0
    languages: list[str] = []

    for match in _CODE_FENCE.finditer(content):
        before = content[cursor : match.start()].strip()
        if before:
            outside.append(before)
            blocks.append(RenderBlock(kind="text", content=before))
        language = match.group("lang") or None
        body = match.group("body").rstrip("\n")
        blocks.append(RenderBlock(kind="code", content=body, language=language))
        if language:
            languages.append(language)
        cursor = match.end()

    tail = content[cursor:].strip()
    if tail:
        outside.append(tail)
        blocks.append(RenderBlock(kind="text", content=tail))

    if not any(block.kind == "code" for block in blocks):
        # The type tag says "code", so trust it even without a fence. Do not
        # read the bare code as prose: it was appended to ``outside`` above.
        blocks = [RenderBlock(kind="code", content=content.rstrip())]
        outside = []

    prose = _flatten_for_speech("\n".join(outside))
    code_count = sum(1 for block in blocks if block.kind == "code")
    language = languages[0] if languages else None
    code_label = f"{language} " if language else ""
    if prose:
        speech = f"{prose} {code_label}代码已经显示在屏幕上。"
    elif code_count == 1:
        speech = f"我写好了一段{code_label}代码，已经显示在屏幕上。"
    else:
        speech = f"我写好了 {code_count} 段{code_label}代码，已经显示在屏幕上。"

    return RenderResult(
        render=RenderPayload(
            type="code",
            mode="code",
            language=language,
            blocks=blocks,
            copyable=True,
        ),
        speech_text=speech,
    )


def _shape_list(content: str) -> RenderResult:
    blocks: list[RenderBlock] = []
    paragraphs: list[str] = []
    ordinal = 0

    for group in re.split(r"\n\s*\n", content):
        lines = [line.strip() for line in group.splitlines() if line.strip()]
        if not lines:
            continue
        marked = [re.match(r"^(?:[-*+•·]|\d+[.)、])\s+(.*)$", line) for line in lines]
        parts: list[str] = []
        if any(match is not None for match in marked):
            for line, match in zip(lines, marked, strict=True):
                if match is not None:
                    item = _flatten_for_speech(match.group(1))
                    if item:
                        ordinal += 1
                        blocks.append(RenderBlock(kind="list_item", content=item, ordinal=ordinal))
                        parts.append(item)
                else:
                    prose = _flatten_for_speech(line)
                    if prose:
                        blocks.append(RenderBlock(kind="text", content=prose))
                        parts.append(prose)
        else:
            for line in lines:
                item = _flatten_for_speech(line)
                if item:
                    ordinal += 1
                    blocks.append(RenderBlock(kind="list_item", content=item, ordinal=ordinal))
                    parts.append(item)
        if parts:
            paragraphs.append("；".join(part.rstrip("。；;，, ") for part in parts) + "。")

    speech = "\n\n".join(paragraphs)
    return RenderResult(
        render=RenderPayload(type="list", mode="list", blocks=blocks, copyable=False),
        speech_text=speech,
    )


def _shape_markdown(content: str) -> RenderResult:
    blocks: list[RenderBlock] = []
    prose_parts: list[str] = []
    cursor = 0
    has_code = False

    for match in _CODE_FENCE.finditer(content):
        before = content[cursor : match.start()]
        prose_parts.append(before)
        prose = _flatten_for_speech(before)
        if prose:
            blocks.append(RenderBlock(kind="text", content=prose))
        language = match.group("lang") or None
        body = match.group("body").rstrip("\n")
        blocks.append(RenderBlock(kind="code", content=body, language=language))
        has_code = True
        cursor = match.end()

    tail = content[cursor:]
    prose_parts.append(tail)
    tail_speech = _flatten_for_speech(tail)
    if tail_speech:
        blocks.append(RenderBlock(kind="text", content=tail_speech))

    speech = _flatten_for_speech("\n".join(prose_parts))
    if has_code:
        speech = f"{speech} 代码已经显示在屏幕上。".strip()

    return RenderResult(
        render=RenderPayload(
            type="markdown",
            mode="markdown",
            blocks=blocks or [RenderBlock(kind="text", content=_flatten_for_speech(content))],
            copyable=False,
        ),
        speech_text=speech,
    )


def _shape_table(content: str) -> RenderResult:
    """Preserve table semantics while reading cells as prose.

    The upstream contract (OmniNexus ``ContentType``) can emit ``table``.
    Returning ``text`` here would silently erase the table type for the UI,
    so the render payload keeps ``type="table"`` and gives each row a block.
    """

    blocks: list[RenderBlock] = []
    spoken_rows: list[str] = []

    for raw_line in content.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        # Drop Markdown table separator rows such as |---|:---:|
        cells = [cell.strip() for cell in line.strip("|").split("|")]
        if cells and all(re.fullmatch(r":?-{2,}:?", cell) for cell in cells if cell):
            continue
        spoken = "，".join(cell for cell in cells if cell)
        if not spoken:
            continue
        blocks.append(RenderBlock(kind="table_row", content=spoken))
        spoken_rows.append(spoken)

    if not blocks:
        blocks = [RenderBlock(kind="table_row", content=_flatten_for_speech(content))]

    speech = "；".join(row.rstrip("。；;，, ") for row in spoken_rows if row)
    if speech:
        speech += "。"

    return RenderResult(
        render=RenderPayload(type="table", mode="table", blocks=blocks, copyable=True),
        speech_text=speech or "表格已经显示在屏幕上。",
    )


def _shape_json(content: str) -> RenderResult:
    """Keep JSON as JSON; never read braces and quotes aloud."""

    try:
        json.loads(content)
        valid = True
    except (TypeError, ValueError):
        valid = False

    speech = "JSON 内容已经显示在屏幕上。" if valid else _flatten_for_speech(content)
    return RenderResult(
        render=RenderPayload(
            type="json",
            mode="json",
            blocks=[RenderBlock(kind="json", content=content.rstrip())],
            copyable=True,
        ),
        speech_text=speech,
    )


def _shape_mixed(content: str) -> RenderResult:
    """Mixed content keeps prose/code shaping but is not re-labeled as Markdown."""

    shaped = _shape_markdown(content)
    return RenderResult(
        render=RenderPayload(
            type="mixed",
            mode="mixed",
            language=shaped.render.language,
            blocks=shaped.render.blocks,
            copyable=shaped.render.copyable,
        ),
        speech_text=shaped.speech_text,
    )


def _flatten_for_speech(text: str) -> str:
    """Remove visual-only syntax while preserving paragraph boundaries."""

    text = _CONTROL.sub("", text)
    text = _CODE_FENCE.sub(" ", text)
    text = _MD_IMAGE.sub(r"\1", text)
    text = _MD_LINK.sub(r"\1", text)
    text = _MD_HEADING.sub("", text)
    text = _MD_EMPHASIS.sub("", text)
    text = _MD_INLINE_CODE.sub(r"\1", text)
    text = re.sub(r"^\s{0,3}>\s?", "", text, flags=re.MULTILINE)
    text = re.sub(r"^\s*([-*_]\s*){3,}$", "", text, flags=re.MULTILINE)
    text = _MD_LIST_LINE.sub("", text)
    text = re.sub(r"[ \t]+", " ", text)
    # Keep real paragraph breaks. Single newlines inside a paragraph are
    # visual wrapping, not pauses.
    text = re.sub(r"\n[ \t]*\n+", "\n\n", text)
    text = re.sub(r"(?<!\n)\n(?!\n)", " ", text)
    return text.strip()


def _clean_speech(text: str) -> str:
    text = _CONTROL.sub("", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n[ \t]*\n+", "\n\n", text)
    text = re.sub(r"(?<!\n)\n(?!\n)", " ", text)
    return text.strip()


def _truncate_speech(text: str, limit: int) -> str:
    window = text[:limit]
    boundary = max(
        window.rfind("。"),
        window.rfind("！"),
        window.rfind("？"),
        window.rfind("；"),
        window.rfind("."),
    )
    if boundary >= int(limit * 0.55):
        return window[: boundary + 1].rstrip()
    return window.rstrip()


def is_speakable(text: str) -> bool:
    return bool(_SPEAKABLE.search(text))
