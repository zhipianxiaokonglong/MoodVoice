from __future__ import annotations

from emotional_tts.rendering import shape_content


def test_plain_text_is_preserved_and_used_for_speech():
    content = "你好，今天感觉怎么样？"
    result = shape_content(content, "text")
    assert result.render.type == "text"
    assert result.render.mode == "plain"
    assert result.render.blocks[0].content == content
    assert result.speech_text == content


def test_paragraph_breaks_are_preserved_and_planned():
    content = "第一段先停一下。\n\n第二段说原因。\n\n第三段给建议。"
    result = shape_content(content, "text", paragraph_pause_ms=400)
    assert result.render.type == "text"
    assert result.speech_text.count("\n\n") == 2
    assert result.plan.paragraphs == 3
    assert len(result.plan.segments) >= 3
    assert result.plan.pause_total_ms >= 800
    assert result.meta["speech_paragraphs"] == 3


def test_list_keeps_blank_line_groups():
    result = shape_content("- 先呼吸\n- 再喝水\n\n- 最后休息", "list")
    assert result.render.type == "list"
    assert result.speech_text.count("\n\n") == 1
    assert result.plan.paragraphs == 2
    assert "先呼吸；再喝水" in result.speech_text


def test_list_group_size_breaks_long_lists_into_pauses():
    content = "\n".join(f"- 第{i}项" for i in range(1, 8))
    result = shape_content(content, "list", list_group_size=3)
    assert result.speech_text.count("\n\n") == 2
    assert result.plan.paragraphs == 3


def test_preserve_paragraphs_can_be_disabled():
    result = shape_content("第一段。\n\n第二段。", "text", preserve_paragraphs=False)
    assert "\n" not in result.speech_text
    assert "第一段。" in result.speech_text
    assert "第二段。" in result.speech_text


def test_code_is_rendered_without_reading_symbols_aloud():
    content = """这里是说明。

```python
def add(a, b):
    return a + b
```
"""
    result = shape_content(content, "code", max_speech_chars=3000)
    assert result.render.type == "code"
    assert result.render.language == "python"
    assert any(block.kind == "code" for block in result.render.blocks)
    assert "def add" not in result.speech_text
    assert "代码已经显示" in result.speech_text
    assert "这里是说明" in result.speech_text


def test_bare_code_without_fence_is_still_treated_as_code():
    result = shape_content("def f():\n    return 1", "code")
    assert result.render.language is None
    assert result.render.blocks[0].kind == "code"
    assert "def f" not in result.speech_text


def test_list_markers_are_stripped_for_speech():
    result = shape_content("- 先呼吸\n- 再喝水\n1. 最后休息", "list")
    assert result.render.type == "list"
    assert len([b for b in result.render.blocks if b.kind == "list_item"]) == 3
    assert "- " not in result.speech_text
    assert "先呼吸；再喝水；最后休息" in result.speech_text


def test_markdown_flattens_links_and_keeps_prose():
    result = shape_content(
        "# 标题\n\n**重点**是 [慢慢来](https://example.invalid)。\n\n```js\nconsole.log(1)\n```",
        "markdown",
    )
    assert result.render.type == "markdown"
    assert "console.log" not in result.speech_text
    assert "https://example.invalid" not in result.speech_text
    assert "重点是 慢慢来" in result.speech_text
    assert "代码已经显示" in result.speech_text


def test_markdown_list_markers_are_not_spoken():
    result = shape_content("- 先呼吸\n- 再喝水", "markdown")
    assert "- " not in result.speech_text
    assert "先呼吸" in result.speech_text
    assert "再喝水" in result.speech_text


def test_table_keeps_type_and_reads_cells_as_prose():
    result = shape_content("| 名称 | 状态 |\n|---|---|\n| 输入 | 正常 |", "table")
    assert result.render.type == "table"
    assert result.render.mode == "table"
    assert len(result.render.blocks) == 2
    assert "名称，状态" in result.speech_text
    assert "|" not in result.speech_text
    assert "---" not in result.speech_text


def test_json_keeps_type_and_is_not_read_symbol_by_symbol():
    result = shape_content('{"name": "Dounai", "ok": true}', "json")
    assert result.render.type == "json"
    assert result.render.mode == "json"
    assert "{" not in result.speech_text
    assert "JSON" in result.speech_text


def test_mixed_keeps_type_and_shapes_code():
    result = shape_content("说明如下。\n\n```python\nprint(1)\n```", "mixed")
    assert result.render.type == "mixed"
    assert "说明如下" in result.speech_text
    assert "print(1)" not in result.speech_text


def test_unknown_type_falls_back_to_plain_text():
    result = shape_content("hello", "audio-waveform")
    assert result.render.type == "text"
    assert result.meta["normalized_type"] == "text"
    assert result.meta["requested_type"] == "audio-waveform"


def test_long_speech_is_truncated_at_a_sentence_boundary():
    content = ("这是第一句。" * 20) + ("这是第二句。" * 20)
    result = shape_content(content, "text", max_speech_chars=40)
    assert len(result.speech_text) < 100
    assert result.meta["speech_truncated"] is True
    assert result.speech_text.endswith("我先读到这里。")
