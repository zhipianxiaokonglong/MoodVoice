# 2026-11-22 语音自然化更新：实现与测试记录

## 范围

- MoodVoice 新增 `speech.py`：段落/句子/停顿 `SpeechPlan` 与 WAV 静音拼接。
- `rendering.py` 保留段落边界；列表按空行分组；Markdown 列表标记不再朗读。
- `SynthesisRequest` 增加可选 `intensity` / `segments`；不改变公共四字段输出契约。
- 契约新增可选 `tags.intensity` / `style.intensity`，接口契约版本 `1.3.0`。
- 各厂商适配器：
  - Edge：intensity → rate / pitch / volume；
  - Azure：intensity → styledegree，segments → SSML `<break>`；
  - OpenAI：intensity → instructions / speed；
  - ElevenLabs：intensity → style / stability / speed；
  - MiniMax：intensity → speed / pitch；
  - DeepSeek：结构化文本；可选同音色分段 + 静音拼接；
  - Local：透传 intensity + segments。
- DeepSeek 默认 `filter_markdown=false`，由本层负责 Markdown 清理。
- 分段合成默认关闭：`tts.speech.segmentation_enabled=false`。

## 自动化测试

```text
MoodVoice: 131 passed, 1 skipped, 1 warning
OmniNexus: 132 passed
Dounai: 14 test files / 178 tests passed; typecheck 通过
Input: 180 passed
Dounai Launcher: vitest 通过；typecheck 通过
ruff check: All checks passed
ruff format --check: 通过
```

跳过项：`tests/test_live_edge.py`（默认不访问外网）。

## 真实调用

### DeepSeek 文本模式

```text
status=synthesized
provider=deepseek
计划段数=4，段落=4
厂商调用=1
音频时长≈15–16s
```

### DeepSeek 分段模式

```text
status=synthesized
provider=deepseek
计划段数=4
厂商调用=4
音频时长≈16.09s
段间静音总计≈1.05s
```

### HTTP 真实链路

```text
POST /api/v1/outputs → 200
style.intensity=0.7
speech_text 保留 4 个换行
diagnostics.speech.plan.segments=3
audio_ref 可下载（audio/wav）
GET /api/v1/contract → 1.3.0
```

## 未测试厂商

以下厂商只做了 mock 参数/结构测试，**未做真实账户调用**：

- Azure Neural TTS
- OpenAI TTS
- ElevenLabs
- MiniMax
- Edge 的真实 live 测试默认跳过

因此上线说明中必须注明：这些厂商的 intensity/SSML/style 映射已通过单元测试，
但真实音色、style 支持范围、延迟和接缝仍需在有密钥的环境逐项验收。
