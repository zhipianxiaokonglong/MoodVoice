"""Content-type aware rendering and speech shaping.

The rendered ``text`` always remains byte-for-byte identical to the upstream
content. ``speech_text`` is a separate, safer script for TTS: code fences are
not read symbol-by-symbol, list markers are removed, and Markdown syntax is
flattened. This is presentation logic, not content classification.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from .contracts import RenderBlock, RenderPayload

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
    "文本": "text",
    "纯文本": "text",
    "代码": "code",
    "列表": "list",
}

_CODE_FENCE = re.compile(r"```(?P<lang>[A-Za-z0-9_+.-]*)[ \t]*\n?(?P<body>.*?)```", re.DOTALL)
_MD_IMAGE = re.compile(r"!\[([^\]]*)\]\([^)]+\)")
_MD_LINK = re.compile(r"\[([^\]]+)\]\([^)]+\)")
_MD_HEADING = re.compile(r"^\s{0,3}#{1,6}\s+", re.MULTILINE)
_MD_EMPHASIS = re.compile(r"(\*\*|__|\*|_|~~)")
_MD_INLINE_CODE = re.compile(r"`([^`]+)`")
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


def shape_content(
    content: str,
    declared_type: str | None,
    *,
    max_speech_chars: int = 3000,
) -> RenderResult:
    normalized = normalize_type(declared_type)
    if normalized == "code":
        result = _shape_code(content)
    elif normalized == "list":
        result = _shape_list(content)
    elif normalized == "markdown":
        result = _shape_markdown(content)
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

    result.speech_text = speech
    result.meta = {
        "requested_type": declared_type or "",
        "normalized_type": normalized,
        "speech_chars": len(speech),
        "source_chars": len(content),
        "speech_truncated": truncated,
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
    speech_parts: list[str] = []
    ordinal = 0
    has_marker = False

    for raw_line in content.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        match = re.match(r"^(?:[-*+•·]|\d+[.)、])\s+(.*)$", line)
        if match:
            has_marker = True
            ordinal += 1
            item = _flatten_for_speech(match.group(1))
            blocks.append(RenderBlock(kind="list_item", content=item, ordinal=ordinal))
            if item:
                speech_parts.append(item)
        else:
            item = _flatten_for_speech(line)
            blocks.append(RenderBlock(kind="text", content=item))
            if item:
                speech_parts.append(item)

    if not has_marker:
        # Tag declared a list but no marker was found: still render every
        # non-empty line as an item, without silently re-classifying it.
        blocks = []
        for index, raw_line in enumerate(content.splitlines(), start=1):
            line = raw_line.strip()
            if not line:
                continue
            item = _flatten_for_speech(line)
            blocks.append(RenderBlock(kind="list_item", content=item, ordinal=index))
            speech_parts.append(item)

    speech = "；".join(part.rstrip("。；;，, ") for part in speech_parts if part)
    if speech:
        speech += "。"
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


def _flatten_for_speech(text: str) -> str:
    """Remove visual-only syntax while preserving words and punctuation."""

    text = _CONTROL.sub("", text)
    text = _CODE_FENCE.sub(" ", text)
    text = _MD_IMAGE.sub(r"\1", text)
    text = _MD_LINK.sub(r"\1", text)
    text = _MD_HEADING.sub("", text)
    text = _MD_EMPHASIS.sub("", text)
    text = _MD_INLINE_CODE.sub(r"\1", text)
    text = re.sub(r"^\s{0,3}>\s?", "", text, flags=re.MULTILINE)
    text = re.sub(r"^\s*([-*_]\s*){3,}$", "", text, flags=re.MULTILINE)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{2,}", "\n", text)
    return text.strip()


def _clean_speech(text: str) -> str:
    text = _CONTROL.sub("", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\s*\n\s*", " ", text)
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
