# 测试报告

- 日期：2026-10-24（M-01~M-05 修复复验：2026-11-03；API 接入模式：2026-11-04）
- 环境：Windows 11 (10.0.26200)，Python 3.12.14
- 依赖：FastAPI 0.162.0，Pydantic 2.14.1，httpx 0.28.1，edge-tts 7.3.1，pytest 8.4.2，keyring 25.x
- 代码版本：v1.0.0 + 审计修复 + API 接入模式
- 结论：**通过**。92 个自动化测试通过，1 个依赖真实 edge-tts 的 live 测试通过；
  浏览器端覆盖流式、9 类标签、纯文本路由、纯文本降级、移动端，以及 API 连接面板的
  探索/试听/保存/切换/删除全流程，无控制台错误、无失败请求。

---

## 1. 执行结果

### 1.1 全量离线测试

```powershell
python -m pytest -q
```

```text
.............................s.......................................     [100%]
92 passed, 1 skipped, 1 warning in 5.56s
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
1 passed, 92 deselected, 1 warning (final verification: 2.50s)
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
| 浏览器 UI 流式（修复后复验） | 默认安慰文本 | 1306 ms | 约 1.5 s | MSE 播放成功 |

> edge-tts 依赖公网，存在抖动。一次 UI 运行观测到约 7.0 s 的首响，仍在 12 s
> 超时阈值内且成功；这说明必须保留超时/降级，而不能把首响当成固定 SLA。
> 商业厂商的真实延迟未验证（无密钥）。

### 1.5 API 接入模式（页面内 URL + Key）

`tests/test_connections.py`（22 个用例，全部使用 `httpx.MockTransport`，不访问外网）：

| 用例 | 断言 |
|---|---|
| OpenAI / Azure / ElevenLabs / MiniMax 探测 | 端点、鉴权头、模型与音色解析正确 |
| 自动识别全部失败 | `protocol_detection_failed` + 4 个协议各自的错误码 |
| 探测 401 | 映射为 `auth_failed`，不保存上游响应体 |
| URL 校验 | 非 https、userinfo、fragment、非法端口、query 里藏密钥、元数据地址被拒 |
| 私网 + DNS rebinding | 未显式允许时拒绝（`blocked_private_network` / `blocked_loopback_resolution`）；勾选后局域网 HTTP/HTTPS 可用，公网 HTTP 仍被拒 |
| 保存连接 | `connections.json` 不含密钥；`public_connections()` 只有 `key_configured` |
| 动态 provider | 注册成功、`style_family=openai`、loopback 记为不出网、去向标签为连接名 |
| 损坏的 `connections.json` | 隔离为 `.corrupt-<时间戳>` 并继续启动，不再让服务起不来 |
| 连接 API | discover/test/save/list/get/retest/delete 全部走通 |
| 完整管线 | 通过连接合成成功，`provider=api_*`、`status=synthesized` |
| 试听不写记录 | 试听后 `records` 仍为 0 |
| 重定向 / 超大响应 | 3xx → `redirect_not_allowed`；>4 MB 元数据在读取过程中即中止（`response_too_large`） |
| JSON 冒充音频 | 200 + `octet-stream` 的 JSON 错误体 → `not_audio`，不会写进缓存 |
| 本机免密钥 | loopback + 空 Key 可保存/试听，不发送 `Authorization`；远程缺 Key 提示“连接 API Key” |
| 空白连接名 | Pydantic 直接拒绝（去掉首尾空格后为空） |

浏览器端另有一轮 40 项断言的端到端检查（见 4.1）。

---

## 2. 需求覆盖矩阵

| 需求 | 测试/证据 | 结果 |
|---|---|---|
| 情感标签 → TTS 风格 | `test_comfort_maps_to_canonical_profile`、`test_provider_params_merge_mapping_and_provider_direction`、`test_rest_contract...` | 通过 |
| 上游 9 类标签全命中 | `test_omninexus_v1_labels_map_to_canonical_emotions`、`test_upstream_emotion_labels_all_resolve` | 通过 |
| 未知标签可诊断 | `test_unknown_tag_falls_back_without_hiding_the_problem`、`test_unknown_emotion_is_visible_in_diagnostics` | 通过 |
| 标签回退可观测 | `test_mapping_fallback_is_observable_in_health`、`test_mapping_monitor_is_bounded...` | 通过 |
| 非 UUID/缺失 session | `test_missing_session_id_is_derived_and_non_uuid_is_accepted` | 通过 |
| 文本去向显式可见 | `test_privacy_egress_is_exposed_top_level_and_as_header`、`test_failed_provider_privacy...`、UI 场景 | 通过 |
| Origin 本机端口通配 | `test_local_origin_wildcard_is_opt_in`、`test_remote_origin_wildcard...` | 通过 |
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

### 4.1 API 连接面板（2026-11-04，40 项断言）

工具：Playwright 1.64.1 驱动本机 Edge（headless），被测实例是独立进程、
独立数据目录、`MemoryKeyStore`（避免污染真实凭据库），厂商是一个本地
OpenAI 兼容假 TTS（`GET /v1/models`、`GET /v1/audio/voices`、`POST /v1/audio/speech`）。

| 阶段 | 断言 |
|---|---|
| 打开页面 | 厂商下拉框可用；页面显示“本次会话”（内存密钥库） |
| 面板 | 点击“＋ 接入 API”展开；Base URL 自动聚焦 |
| 探索 | 自动识别为 `openai`；模型含 TTS 模型并被优先选中；音色取服务端返回；连接名自动填充 |
| 试听 | 请求真实发出；状态“试听成功”带首响与字节数；`audioPlayer.src` 指向本地缓存 URL |
| 保存 | 厂商下拉框自动切到 `api_*`；密钥输入框被清空；已保存列表显示连接名与“已配置密钥” |
| 浏览器存储 | `localStorage` / `sessionStorage` 中无密钥 |
| 生成（REST） | 关闭流式开关（真实点击自定义开关）后生成，结果 `provider` 为连接名，去向条写明连接名 |
| 生成（WS） | 重新打开流式开关，WebSocket 合成完成，状态“语音已就绪” |
| 再次试听 | 已保存连接试听成功，列表更新时间显示“上次测试成功” |
| 删除 | 有确认弹窗；删除后列表空、厂商回落到 `auto`、状态复位、健康检查 `connections=[]` |
| 移动端 | 390×844 下水平溢出 0 px，面板仍可用 |
| 全程 | 控制台错误 0、未捕获异常 0、失败请求 0 |

发现并修复的问题：`ConnectionProvider.capabilities.data_destination` 直接沿用了适配器
默认厂商名（本地连接会显示“OpenAI API”），已改为使用连接名称，并补充了
`external_egress` 覆盖；`test_connections.py` 增加了对应断言。

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

## 6. 审计 M-01~M-05 修复记录

| ID | 修复 | 验证 |
|---|---|---|
| M-01 | 增加 `fear→sadness`、`confusion→frustration`、`gratitude→joy` 别名；UI 同步提供 3 个兼容标签 | 9 类标签参数化测试、UI 手动验证 |
| M-02 | 回退 warning + 进程内有界 `MappingMonitor` + `/api/health.emotion_mapping.fallbacks` | `test_mapping_fallback_is_observable_in_health`、`test_mapping_monitor_*` |
| M-03 | `session_id` 改为可选字符串，缺失时由 `event_id` 派生；`diagnostics.session.source` 标记来源 | `test_missing_session_id...` |
| M-04 | 顶层 `privacy`（`external_egress/text_sent_to/summary`）+ `X-MoodVoice-Egress` + UI 去向条 + edge 明示在线 | 顶层 privacy/header 测试、UI 三态（已出网/未发送/本机） |
| M-05 | Origin 精确白名单 + 显式本机端口通配；非法远程通配启动失败；拒绝日志 | 通配/拒绝/启动校验测试 |

### 6.1 复验结果

- 全量：`70 passed, 1 skipped`
- live edge：`1 passed`
- 浏览器：流式 MSE、`gratitude→开心`、`fear→悲伤`、tool 路由“未发送”、未配置厂商降级“未发送”、移动端无溢出、控制台错误 0
- 静态：`ruff check` / `ruff format --check` 通过
- 首响复验：流式 1306 ms

### 6.2 API 接入模式复审（2026-11-04）

对新增的 `connections/` 及其调用方做了一次代码复审，修复项：

| # | 问题 | 处理 |
|---|---|---|
| 1 | 发现阶段的 4 MB 上限在**整包读完之后**才判断，恶意/异常端点可以先把内存吃满 | 改为流式读取并逐块累计，超限立即中止 |
| 2 | 3xx 未被显式识别，`follow_redirects=False` 后拿到重定向页 HTML，报成 `invalid_response` | 明确返回 `redirect_not_allowed` |
| 3 | 连接名只校验长度，`"   "` 能保存出空名称 | Pydantic validator 去空格后拒绝 |
| 4 | `save()` 回滚阶段再次抛错会掩盖原始异常 | 回滚改为 best-effort，保留原始错误 |
| 5 | 凭据库写入失败时提示“密钥仅保留在本次会话”，但实际保存被回滚 | 提示改为“连接未保存” |
| 6 | `/api/v1/admin/reload-connections` 只返回列表，并未真正重载 | 实现 `reload()`：移除全部 `kind=connection` 的 provider、重读文件、重新注册 |
| 7 | 健康检查里 `connection_protocols` 无人使用且与 `Protocol` 重复 | 删除该字段 |
| 8 | 连接表单的 422 文案沿用了“输入事件不符合契约” | 改为“请求参数不符合契约” |
| 9 | `moodvoice diagnose` 未知厂商提前返回时未关闭 httpx 客户端 | 提前返回前关闭注册表 |
| 10 | 文档写的自动识别顺序与代码不一致 | 文档改为 `openai → elevenlabs → azure → minimax` |

同时复核了工作区里未提交的“本机免密钥”改动（`OpenAIProvider.allow_keyless` +
`ConnectionProvider` 的就绪标签改写）：逻辑正确，并补了两条测试覆盖
loopback 无密钥可合成、远程缺密钥提示“连接 API Key”。

复审后：`92 passed, 1 skipped`；`ruff` 通过；另用真实 socket 跑了一遍
discover → test → save → outputs → retest → delete，确认 `connections.json` 不含密钥。

---

## 7. 恢复与验证记录

在最终清理阶段，一次错误的 PowerShell `-Include` 清理命令误删了工作区文件。项目随后
从本会话的写文件/改文件事件按时间顺序恢复，重新执行了格式化、静态检查、全量离线
测试、真实 edge-tts 冒烟与 CLI 端到端验证；恢复后的行为与清理前一致。该记录仅用于
说明测试证据来源，不影响仓库内容。

## 8. 已知限制

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
7. **凭据库依赖系统后端**：Windows/macOS/带 Secret Service 的 Linux 可持久化；
   精简容器里会退化为“本次会话”，重启后需重填密钥（页面会明确提示）。
8. **API 接入只用假服务验证**：四种协议的真实账户联调仍建议用最小额度密钥做一次，
   尤其是 Azure 的区域端点与 MiniMax 的 GroupId 差异。

---

## 9. 复现

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
