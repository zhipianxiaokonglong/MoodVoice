# 测试报告

- 日期：2026-10-24
- 环境：Windows 11 (10.0.26200)，Python 3.12.14
- 依赖：FastAPI 0.162.0，Pydantic 2.14.1，httpx 0.28.1，edge-tts 7.3.1，pytest 8.4.2
- 代码版本：v1.0.0
- 结论：**通过**。60 个自动化测试通过，1 个依赖真实 edge-tts 的 live 测试通过；
  浏览器端 5 个场景通过；无外部资源请求、无控制台错误。

---

## 1. 执行结果

### 1.1 全量离线测试

```powershell
python -m pytest -q
```

```text
................................................................         [100%]
60 passed, 1 skipped, 1 warning in ~3.4s
```

跳过项是 `tests/test_live_edge.py`（默认不访问外网）。warning 为 Starlette
TestClient 对 httpx 的弃用提示，不影响功能。

### 1.2 真实 edge-tts 冒烟

```powershell
$env:MOODVOICE_LIVE_TESTS="1"
python -m pytest -q -m live -s
```

```text
.
1 passed, 60 deselected, 1 warning in ~1.4s
```

覆盖：真实网络、真实 MP3、音频文件 > 1 KB、端到端首响 < 15 s。

### 1.3 静态检查

```powershell
ruff check src tests
ruff format --check src tests
```

```text
All checks passed!
```

### 1.4 真实首响样本

使用同一段文本/同一音色，流式写入本地缓存，测量从 `prepare()` 开始到第一个非空
音频分片的时间：

| 场景 | 文本 | 首响 | 总耗时 | 结果 |
|---|---|---:|---:|---|
| 安慰 | 今天辛苦了，先慢慢呼吸一下。 | 808.8 ms | 1050.1 ms | 成功 |
| 开心 | 太好了，我们真的做到了！ | 769.8 ms | 1008.4 ms | 成功 |
| 兴奋 | 太棒了，这个好消息一定要庆祝一下！ | 862.8 ms | 1099.0 ms | 成功 |
| 浏览器 UI 流式 | 默认安慰文本 | 790 ms | 约 1.0 s | MSE 播放成功 |

> edge-tts 依赖公网，存在抖动。一次 UI 运行观测到约 7.0 s 的首响，仍在 12 s
> 超时阈值内且成功；这说明必须保留超时/降级，而不能把首响当成固定 SLA。
> 商业厂商的真实延迟未验证（无密钥）。

---

## 2. 需求覆盖矩阵

| 需求 | 测试/证据 | 结果 |
|---|---|---|
| 情感标签 → TTS 风格 | `test_comfort_maps_to_canonical_profile`、`test_provider_params_merge_mapping_and_provider_direction`、`test_rest_contract...` | 通过 |
| 未知标签可诊断 | `test_unknown_tag_falls_back_without_hiding_the_problem`、`test_unknown_emotion_is_visible_in_diagnostics` | 通过 |
| 内容类型 → 渲染 | `test_rendering.py` 7 项 | 通过 |
| 代码不逐行朗读 | `test_code_is_rendered_without_reading_symbols_aloud`、`test_bare_code...`、UI 场景 | 通过 |
| 意图 → 路由 | `test_routing.py` 3 项、`test_tool_intent...`、UI 场景 | 通过 |
| 多厂商切换 | `test_provider_switch_uses_the_requested_vendor`、API/WS 用不同 fake provider | 通过 |
| 厂商失败 → 纯文本 | `test_all_providers_fail_to_plain_text`、`test_failed_provider_returns_plain_text...`、UI 未配置 OpenAI 场景 | 通过 |
| 显式降级链 | `test_provider_failure_falls_back_to_explicit_fallback_provider` | 通过 |
| 首片超时 | `test_first_chunk_timeout_degrades` | 通过 |
| 流中断清理 | `test_midstream_failure_aborts_partial_cache`（断言缓存 0 文件 + 记录） | 通过 |
| 结构化情感记录 | `test_reply_synthesizes_and_records_structured_data`、`test_records_do_not_store_content_at_rest` | 通过 |
| 音频只存本地 | `test_rest_contract_and_audio_serving`、`test_cache_*` | 通过 |
| 密钥不进入输出/日志 | `test_openai_error_does_not_leak_upstream_body`、`test_security_cache.py` | 通过 |
| 错误不回显原文 | `test_validation_errors_do_not_echo_input_content` | 通过 |
| 路径穿越防护 | `test_cache_rejects_path_traversal` | 通过 |
| WebSocket 流式契约 | `test_websocket_streams_metadata_then_audio`、浏览器 MSE 场景 | 通过 |
| 独立 Demo | 启动服务 + 浏览器 5 场景；`moodvoice demo` CLI | 通过 |

---

## 3. 厂商适配测试矩阵

商业厂商没有真实密钥，因此使用 `httpx.MockTransport` 做**请求构造 + 状态映射**
契约测试。这不是“真实账户联调”，报告如实标注。

| 厂商 | 成功载荷/端点 | 关键断言 | 错误映射 |
|---|---|---|---|
| edge-tts | **真实调用** | MP3、首响、文件大小、韵律参数 | 非法 voice → `invalid_style/style_resolution` |
| OpenAI | mock HTTP | `/audio/speech`、Bearer、instructions、tts-1 自动省略 | 401 → `auth_failed`，不泄漏响应体 |
| ElevenLabs | mock HTTP | `/text-to-speech/{id}/stream`、voice_settings、speed clamp | 通用状态映射（见 `http.py`） |
| Azure | mock HTTP | SSML 转义、`mstts:express-as`、style/styledegree | 4xx 附 `effective_style` |
| MiniMax | mock HTTP | GroupId、Bearer、emotion、hex 解码 | 1004 → `auth_failed`，保留 trace_id |
| local | mock HTTP | 二进制 WAV、JSON base64、请求载荷 | 格式不一致 → `format_mismatch` |

未验证项：

- 真实商业账户的限流、区域、音色授权、账单；
- Azure 每个 voice × style 的支持矩阵（需真实订阅枚举）；
- ElevenLabs 不同 Voice 对 style 强度的主观差异；
- 火山/阿里/讯飞/腾讯/Deepgram/Cartesia 的专用适配器（本项目未内置，见厂商文档）。

---

## 4. 浏览器端验证

工具：Playwright 1.64.1 + 本机 Chrome headless（未提交测试脚本/截图）。

| 场景 | 操作 | 实际结果 |
|---|---|---|
| 流式合成 | 默认文本 + 安慰 + 流式 | 首响 790 ms，MSE `blob:` 播放，状态“语音已就绪” |
| 标准请求 | 代码类型 + 关闭流式 | 1 个代码块；`speech_text` 不含 `def greet`；音频 URL 正常 |
| 意图 tool | 意图选工具结果 | “纯文本路由”，音频卡隐藏，目的地 `ui.tool_result` |
| 纯文本降级 | 选未配置的 OpenAI | “纯文本降级”，`missing_credentials` / `provider_auth` / `["OPENAI_API_KEY"]`，`audio_ref=null` |
| 移动端 | 390×844 | 水平溢出 0 px |

附加检查：

- 控制台错误：0；
- 非本机外部请求：0（页面不加载 CDN/字体/分析脚本）；
- CSP 无非法 source 警告；
- 音频下载链接可访问；
- 清除本地数据后端到端可用（API 测试）。

### 浏览器验证发现并修复的真实缺陷

**现象**：在 UI 中选择 `intent=tool`，实际仍返回语音。

**根因**：`app.js` 的 `els` 对象里 `intent` 键被定义了两次（select 和结果展示
节点），后者覆盖前者；请求构造读取的是展示节点，`value` 为空，Pydantic 使用默认
`reply`。这是**前端事件构造层**问题，不是后端 intent 路由问题。

**修复**：select 引用改为 `intentSelect`，请求构造读取 `els.intentSelect.value`。
修复后 UI 场景通过，API 路由测试仍然通过。

**教训**：先看实际请求里的 `tags.intent`，不要因为后端测试通过就排除前端。
该案例也记录在 `ROOT_CAUSE_PLAYBOOK.md`。

---

## 5. 根因验证

| 假设 | 证据 | 结论 |
|---|---|---|
| 未知标签被静默吞掉 | `mapping_hit=false`、`fallback_reason=unknown_tag` | 否；可见且可诊断 |
| 代码被 TTS 朗读 | `speech_text` 不含 `def`，渲染块含 code | 否；渲染层正确清理 |
| 厂商参数没有下发 | `adapter.request.effective` 含 style/speed/rate | 否；适配器请求可审计 |
| 降级是“假降级” | `audio_ref=null`、`degraded=true`、attempts 有 stage/code | 否；文本完整 |
| 失败会留下半截音频 | 流中断后缓存文件数 0，记录 `stream_interrupted` | 否；已清理 |
| 密钥会进错误信息 | 401 响应体含伪造密钥，异常/诊断中不存在 | 否；只保留状态码 |

详细决策树与真实案例见 [ROOT_CAUSE_PLAYBOOK.md](ROOT_CAUSE_PLAYBOOK.md)。

---

## 6. 已知限制

1. **没有商业厂商真实密钥**：OpenAI/ElevenLabs/Azure/MiniMax 的成功路径是 mock
   HTTP 契约测试。上线前应使用最小额度密钥做一次真机联调，并补充到本报告。
2. **edge-tts 延迟有抖动**：0.77–1.20 s 是稳定样本，单次可达 7 s；产品的首响 SLA
   需要监控和降级策略，不应写死承诺。
3. **MiniMax 非流式**：同步 hex 响应，首响高于其他厂商；若要低延迟需按其 SSE
   协议另写适配器。
4. **浏览器 MSE 差异**：Safari/Firefox 对 `audio/mpeg` 的 MSE 支持不同；Demo 在
   不支持时退化为整段 Blob 播放。服务端首响仍被记录。
5. **单机 Demo 无鉴权**：不能直接暴露公网；远程部署需反向代理鉴权。
6. **长文本分段**：当前按字符上限截断并提示；分段合成 + 拼接是下一版。

---

## 7. 复现

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
pip install -e .
pip install pytest pytest-asyncio
python -m pytest -q

$env:MOODVOICE_LIVE_TESTS="1"
python -m pytest -q -m live -s

python -m emotional_tts serve
# 浏览器打开 http://127.0.0.1:8765
```

测试不会提交任何音频、数据库、日志或截图；`.data/` 在 `.gitignore` 中。
