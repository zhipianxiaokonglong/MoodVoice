# 开发文档 · 标签消费、风格映射与厂商扩展

面向维护输出层、接入新厂商或调整情感表现的工程师。

---

## 1. 职责边界

本层**不**做以下事情：

- 不从文本推断情感（上游已经统一打标）；
- 不改写 `text` 原文；
- 不做对话编排、记忆、意图识别；
- 不保存用户原文用于训练或回放。

本层负责：

- 消费 `emotion` → 解析并选择 TTS 风格；
- 消费 `type` → 生成 UI 渲染块和“朗读安全”的 `speech_text`；
- 消费 `intent` → 选择下游目的地与是否朗读；
- 调用可插拔 TTS 厂商，流式落盘，失败降级；
- 产出下游契约事件和可复用的结构化情感记录。

---

## 2. 代码地图

```text
api.py            FastAPI、REST、WebSocket、Origin/CSP、错误处理
contracts.py      输入/输出 Pydantic 模型
pipeline.py       prepare -> 候选厂商 -> 流式合成 -> 降级 -> 记录
styles.py         标签归一化、别名索引、厂商参数
rendering.py      type -> RenderPayload + speech_text
routing.py        intent -> destination / synthesize
providers/base.py TTSProvider / SynthesisRequest / capabilities
providers/http.py 共享 HTTP 流式与错误映射
providers/*.py    各厂商适配器
cache.py          本地音频缓存（原子写入、校验、清理）
storage.py        结构化记录（SQLite，不含原文）
security.py       密钥脱敏、日志 formatter
config.py         YAML + 环境变量
web/              无构建 Demo UI
```

---

## 3. 输入/输出契约

### 3.1 输入

```python
InputEvent(
  event_id: UUID,
  session_id: UUID,
  content: str,                 # 1..20000，pipeline 再检查 max_input_chars
  stream: bool = True,          # 上游流式标志；本层接收聚合后的完整文本
  tags: InputTags(emotion, type, intent),
  provider: str | None = None,  # 可选扩展；也可用 ?provider= 覆盖
)
```

`stream` 表示上游生成方式；当前 REST 与 WS 都接收完整 `content`。如果未来上游改成
token 流，应在接入层先聚合，不应让 TTS 层承担 LLM 流解析。

### 3.2 输出

`OutputEvent` 至少包含契约四字段：

- `event_id`
- `text`：原文，逐字保持
- `audio_ref`：同源相对 URL，或 `null`
- `style`：`emotion/label/speed/tone/...`

增量字段：

- `speech_text`：实际朗读稿；
- `render`：`type/mode/language/blocks`；
- `route`：`intent/destination/synthesize/reason`；
- `provider/requested_provider/status/degraded/degradation`；
- `diagnostics`：标签映射、厂商参数、尝试记录、隐私边界；
- `timings`：端到端首包与总耗时。

消费方应忽略不认识的增量字段；不要把 `audio_ref` 当永久链接（缓存会过期/被清除）。

---

## 4. 标签消费规范

### 4.1 情感标签

实现：`styles.StyleBook.resolve()`。

1. `normalize_tag()`：
   - Unicode NFKC；
   - 去首尾空白、转小写；
   - 去 `emotion:` / `情绪/` 等前缀；
   - 空白、下划线、连字符折叠为单个空格。
2. 用别名索引解析到 canonical key；
3. 未命中 → `fallback_emotion`（默认 `neutral`）；
4. 同时输出：
   - `style.source_tag`：上游原始值；
   - `style.mapping_hit`：是否命中；
   - `diagnostics.emotion_mapping.fallback_reason`：`missing_tag` / `unknown_tag` / `null`。

**不要**在适配器里临时猜测标签含义。未知标签必须走配置或显式回退，且要在
诊断里可见。

### 4.2 内容类型

实现：`rendering.shape_content()`。

| 上游 type | 标准化 | render.mode | speech_text 策略 |
|---|---|---|---|
| `text` / `plain` | text | plain | 原文直接朗读 |
| `code` / `snippet` | code | code | 不朗读代码符号；朗读代码围栏外的说明；无说明时提示“代码已显示” |
| `list` / `bullets` | list | list | 去掉 `-`/`*`/`1.` 等标记，条目以分号连接 |
| `markdown` / `md` | markdown | markdown | 去标题/强调/链接/行内代码；代码块替换为提示语 |
| 未知值 | text | plain | 按普通文本处理，并在 `diagnostics.speech.requested_type` 保留原值 |

原则：**type 是上游声明，不做自动内容分类**。即使代码看起来像普通句子，只要标签是
`code`，就不逐行朗读。

`render.blocks` 是 UI 的渲染建议：

- `kind=text`：段落；
- `kind=code`：代码块，带 `language`；
- `kind=list_item`：列表项，带 `ordinal`；
- 代码内容不会被转成 HTML；Demo 使用 `textContent` 防止 XSS。

`max_tts_chars` 超出时在句子边界截断，并注明“内容有点长，我先读到这里”。
`diagnostics.speech.speech_truncated=true`。

### 4.3 意图

实现：`routing.IntentRouter`，配置 `config/routes.yaml`。

| intent | destination | synthesize |
|---|---|---|
| `reply` | `ui.voice_reply` | true |
| `tool` | `ui.tool_result` | false |
| `linkage` | `integration.linkage` | false |
| 未知 | `ui.text_reply` | false |

- `intent` 不合法不会抛 500；会走未知意图的 text-only 路由，并在
  `diagnostics.speech.intent_known=false` 中体现；
- 路由是业务策略，不是 TTS 厂商能力。需要“工具结果也朗读”时改 YAML，不要改代码；
- `route.synthesize=false` 时不会调用任何厂商，因此不会产生费用或外发文本。

---

## 5. 情感风格映射配置

文件：`config/emotion_styles.yaml`。由 `EMOTION_TTS_CONFIG` 指向的 app 配置里的
`tts.styles_file` 决定实际路径。

### 5.1 Schema

```yaml
version: 1
fallback_emotion: "neutral"
emotions:
  comfort:
    label: "安慰"
    tone: "温柔低缓，有陪伴感"
    description: "降低语速与音高，句间留白……"   # 可选
    speed: 0.92                                # 0.5 .. 2.0
    pitch_semitones: -1                         # -12 .. 12
    volume_db: -1                               # -12 .. 6
    aliases: ["comfort", "安慰", "安抚"]        # 可省略
    provider_overrides:
      edge:
        voice: "zh-CN-XiaoxiaoNeural"
        pitch: "-2Hz"
        volume: "-2%"
      azure:
        style: "gentle"
        style_degree: 1.6
        pitch: "-2%"
      openai:
        voice: "sage"
        instructions: "Speak in Mandarin Chinese like a caring friend..."
      elevenlabs:
        stability: 0.62
        similarity_boost: 0.85
        style: 0.45
        use_speaker_boost: false
      minimax:
        emotion: "neutral"
      local:
        tone: "温柔低缓"
```

约束：

- `fallback_emotion` 必须存在于 `emotions`；
- `speed / pitch / volume` 有范围校验；
- 别名在全部情感之间必须唯一；canonical key 自动成为别名；
- `provider_overrides` 是自由表单，由适配器解释；
- 配置错误会在启动时抛 `ConfigError`，不会静默使用错误参数。

### 5.2 优先级

```text
provider_overrides.<provider>
  > 顶层 canonical speed / pitch / volume / tone
  > providers.<provider> 的默认 voice / model / format
```

- `provider_overrides` 优先：例如 Azure 的离散 `style` 无法从 canonical speed 推导；
- 适配器会把 canonical `speed` 用在厂商支持的语速参数上（Edge `rate`、OpenAI `speed`、
  ElevenLabs `speed`、MiniMax `voice_setting.speed`、Azure `prosody rate`）；
- 厂商特定音高/音量若未显式配置，适配器会做保守换算；为了可复现，建议对每个情感
  显式写 `pitch/volume`。

### 5.3 各厂商参数契约

| 适配器 | 读取的 override 字段 | 说明 |
|---|---|---|
| edge | `voice, rate, pitch, volume` | 无原生 style；`rate` 可由 speed 推导，pitch 用 Hz |
| azure | `voice, style, style_degree, rate, pitch, volume` | `mstts:express-as`；风格支持因音色而异 |
| openai | `voice, instructions` | `gpt-4o-mini-tts` 发送 instructions；tts-1 自动省略 |
| elevenlabs | `voice_id, stability, similarity_boost, style, use_speaker_boost` | speed 用 canonical，clamp 到 0.7–1.2 |
| minimax | `voice_id, emotion` | 合法枚举见适配器；`neutral` 会省略以让模型自行判断 |
| local | 整个 `style` 载荷 + `emotion/tone/speed` | 由本地服务解释 |

### 5.4 新增情感

1. 在 `emotions` 下增加 canonical key；
2. 配置 `label/tone/speed/pitch/volume`；
3. 添加中英文别名，注意不要与其他情感冲突；
4. 为每个启用的厂商写 override；至少验证 Azure/OpenAI/Edge/local；
5. 运行：

```powershell
moodvoice diagnose --emotion 新标签 --provider azure
python -m pytest -q tests/test_styles.py
```

6. 用同一段文本做 A/B 试听，确认差异来自预期参数，而不是音量/音色偶然变化。

### 5.5 修改后检查

```powershell
moodvoice diagnose --emotion comfort --provider azure
```

输出包含：`mapping_hit`、`resolved`、各厂商参数、`request_preview.effective`、
就绪状态和警告。它**不会**发起网络请求，也不会包含用户文本。

---

## 6. 厂商适配器

### 6.1 接口

```python
class TTSProvider(ABC):
    name: ClassVar[str]
    display_name: ClassVar[str]
    capabilities: ClassVar[ProviderCapabilities]

    def readiness(self) -> tuple[bool, list[str]]
    @property
    def audio_format(self) -> str
    def request_preview(self, request) -> dict
    def stream(self, request) -> AsyncIterator[bytes]
    async def close(self) -> None
```

关键要求：

- `stream()` **异步产出压缩音频字节**；不要产出 base64 / JSON 文本；
- `audio_format` 必须与实际字节和 HTTP Content-Type 一致，否则缓存扩展名会错；
- `readiness()` 只返回缺失项名称，绝不返回值；
- `request_preview()` 只返回非敏感参数、`text_chars`，不返回用户文本或密钥；
- `close()` 关闭自身 HTTP 客户端；
- 所有异常映射成 `ProviderError`，使用关键字参数：

```python
raise ProviderError(
    provider=self.name,
    code="invalid_style",
    message="Azure 情感风格名格式无效。",
    stage="style_resolution",
    details={"parameter": "style"},
)
```

### 6.2 能力声明

`ProviderCapabilities` 用于 UI/诊断和产品决策：

```python
ProviderCapabilities(
    streaming=True,
    emotional_control="native_style",  # native_style | instructions |
                                       # style_intensity | native_emotion |
                                       # prosody | style_payload | none
    emotion_styles="mstts:express-as ...",
    requires_key=True,
    data_destination="Azure AI Speech（用户自己的订阅）",
    formats=("mp3", "wav"),
    notes="...",
)
```

`data_destination` 会出现在输出 `diagnostics.privacy.text_sent_to`，用于审计音频文本
流向。接入新厂商时必须如实填写。

### 6.3 新增厂商步骤

1. 在 `providers/` 新建适配器，继承 `TTSProvider`；
2. 实现 `readiness / audio_format / request_preview / stream / close`；
3. 使用 `providers/http.py` 的 `stream_audio()` 或 `client.stream`，禁止记录响应体；
4. 在 `providers/registry.py` 注册；
5. 在 `config.py` 的 `KNOWN_PROVIDERS` 和 `config/app.yaml` 增加配置；
6. 在 `config/emotion_styles.yaml` 增加该厂商的 override；
7. 添加 mock HTTP 契约测试（见 `tests/test_adapters.py`）；
8. 更新 `docs/PROVIDERS.md` 的能力与隐私表；
9. 运行全部测试和 `moodvoice providers`。

> 不要把厂商 SDK 的密钥读取逻辑散落在适配器里。密钥统一由 `Secrets` 注入。

### 6.4 超时与流

- 首个音频分片超时：`tts.first_chunk_timeout_seconds`；
- 总合成超时：`tts.total_timeout_seconds`；
- 首片超时前失败：可尝试下一候选厂商；
- 已经向 WS 客户端发过音频后失败：不再切换厂商（避免拼接两个声音）；
  删除不完整缓存，返回 `error`，并记录 `stream_interrupted`；
- REST 无客户端流，可以在中途失败后丢弃重试（同样删除不完整文件）。

---

## 7. 管线与候选厂商

实现：`pipeline.OutputPipeline`。

### 7.1 阶段

```text
prepare(event)
  validate content length
  rendering.shape_content(type)
  routing.route(intent)
  styles.resolve(emotion)
  plan candidates (requested / auto / fallback)
  diagnostics snapshot

synthesize(prepared)
  if !route.synthesize -> text_only
  for candidate:
    if provider missing/not ready -> skipped
    build SynthesisRequest + vendor style params
    provider.request_preview (no network)
    provider.stream
    first chunk -> write -> on_start -> on_chunk
    all chunks -> finalize -> success record
    error before first chunk -> abort -> next candidate
    error after first chunk + WS -> abort -> record interrupted -> raise
  all failed -> text_fallback record + output
```

### 7.2 状态值

| status | 含义 | `degraded` |
|---|---|---|
| `synthesized` | 音频完整生成 | false |
| `text_only` | 意图路由决定不合成 | false |
| `text_fallback` | 厂商失败/全部失败，保留文本 | true |
| `preparing` | 仅 WS 元数据阶段 | false |

### 7.3 时间指标

- `timings.first_chunk_ms`：从 `prepare()` 开始到第一段**非空**音频到达的端到端时间；
- `timings.total_ms`：到音频完整落盘/文本降级完成；
- 厂商尝试的 `first_chunk_ms` 使用同一个起点，因此在有降级尝试时会包含前一次失败耗时。

### 7.4 厂商候选

- `provider=auto`：按 `auto_order` 选第一个 `ready`；
- 显式 provider：即使未就绪也会进入候选，记录 `missing_credentials`，方便排障；
- 只有 `fallback_providers` 中显式列出的厂商会被追加；
- 同一厂商去重；
- 不需要合成（`route.synthesize=false`）时完全不调用厂商。

---

## 8. 诊断与根因

任何一个“风格不对应”的问题，必须先定位到三层之一：

1. **标签映射层**：`diagnostics.emotion_mapping`
   - `source_tag`、`resolved_emotion`、`mapping_hit`、`fallback_reason`；
   - 未知标签回退会明确标记，不会只表现成“听起来中性”。
2. **厂商参数层**：`diagnostics.provider_params`
   - `canonical`：speed/pitch/volume/tone；
   - `by_provider`：每个厂商最终参数；
   - `selected`：实际选中的厂商参数；
   - `adapter.request.effective`：适配器真正将发给厂商的值。
3. **适配层**：`diagnostics.provider_attempts` + `adapter`
   - `stage`：`style_resolution` / `provider_auth` / `provider_ttfb` /
     `provider_synthesis` / `cache` 等；
   - `code`：`invalid_style` / `auth_failed` / `timeout` / `format_mismatch` 等；
   - `provider`/`adapter.name`：确认请求确实落在预期适配器；
   - Azure 的 4xx 会额外附 `effective_style`；本地格式错误会附 `configured_format`。

命令：

```powershell
moodvoice diagnose --emotion comfort --provider azure
```

Web：

```text
GET /api/v1/diagnostics/style?emotion=comfort&provider=azure
```

完整决策树与实例见 [ROOT_CAUSE_PLAYBOOK.md](ROOT_CAUSE_PLAYBOOK.md)。

---

## 9. 结构化记录

SQLite：`.data/records.sqlite3`，表 `output_records`。

保存：

- `event_id / session_id / created_at`
- `tags_json`（emotion/type/intent 原值）
- `source_emotion / resolved_emotion / mapping_hit`
- `content_type / intent / route`
- `requested_provider / provider`
- `style_json`（解析后的 style + provider params）
- `diagnostics_json`
- `status / degraded / degradation_code`
- `first_chunk_ms / total_ms / audio_bytes / audio_ref`
- `error_type / error_message / speech_chars`

**不保存**：`content`、`speech_text`、音频二进制、密钥、原始厂商响应。

音频 URL 可被清理；记录中的 `audio_ref` 只是一个引用。

清空：

```powershell
moodvoice cache clear --records
```

---

## 10. API 与 WebSocket

### 10.1 REST

| Method | Path | 用途 |
|---|---|---|
| GET | `/api/health` | 厂商就绪状态、缓存统计、隐私边界 |
| POST | `/api/v1/outputs?provider=...` | 处理一条事件 |
| GET | `/api/v1/audio/{filename}` | 取本地音频（`no-store`，文件名白名单） |
| GET | `/api/v1/records?limit=&offset=` | 结构化记录 |
| DELETE | `/api/v1/cache` | 清音频 |
| DELETE | `/api/v1/records` | 清记录 |
| DELETE | `/api/v1/user-data` | 全清 |
| GET | `/api/v1/diagnostics/style?emotion=&provider=` | 无网络的风格诊断 |
| GET | `/api/openapi.json` | OpenAPI schema（无外部 CDN 文档页） |

错误统一：

```json
{"error": {"code": "...", "message": "...", "details": []}}
```

校验错误不会回显输入 `input`（避免把原文复制到错误日志/界面）。

### 10.2 WebSocket

连接：`ws://127.0.0.1:8765/ws/v1/outputs`，每次连接处理一条事件。

Client → Server：

```json
{"type": "synthesize", "event": {"...": "InputEvent"}, "provider": "auto"}
```

Server → Client：

1. `{"type":"preparing","output":{...}}`（立即；此时无音频引用）
2. `{"type":"start","output":{...},"audio":{"mime":"audio/mpeg"}}`
   （第一片音频前发送；`output.audio_ref=null`）
3. **二进制**音频帧（可能多帧）
4. `{"type":"done","output":{...}}`（完整事件；含 `audio_ref`、最终计时）

错误：

```json
{"type":"error","error":{"code":"timeout","message":"...","stage":"provider_ttfb","midstream":false}}
```

客户端应：

- 用 `start.audio.mime` 初始化 MSE/解码器；
- 收到 `done` 后把 `audio_ref` 作为下载/回放地址；
- 收到 `error` 时停止音频、展示 `preparing.output.text`；
- 每次生成使用新连接，不并发复用。

---

## 11. 配置与环境变量

主配置：`config/app.yaml`。

| 环境变量 | 作用 |
|---|---|
| `EMOTION_TTS_CONFIG` | 主配置路径 |
| `EMOTION_TTS_ROOT` | 仓库根目录（高级；影响相对路径） |
| `EMOTION_TTS_PROVIDER` | 默认厂商 |
| `EMOTION_TTS_FALLBACK_PROVIDERS` | 逗号分隔的显式降级链 |
| `EMOTION_TTS_HOST` / `EMOTION_TTS_PORT` | 监听地址/端口 |
| `EMOTION_TTS_DATA_DIR` | 数据目录 |
| `EMOTION_TTS_ALLOWED_ORIGINS` | 额外允许的浏览器 Origin（逗号分隔） |
| `OPENAI_API_KEY` / `OPENAI_BASE_URL` | OpenAI |
| `ELEVENLABS_API_KEY` | ElevenLabs |
| `AZURE_SPEECH_KEY` / `AZURE_SPEECH_REGION` | Azure |
| `MINIMAX_API_KEY` / `MINIMAX_GROUP_ID` / `MINIMAX_BASE_URL` | MiniMax |
| `LOCAL_TTS_URL` / `LOCAL_TTS_API_KEY` | 本地服务 |

密钥不在 YAML 中读取；`Secrets.from_env()` 只注入到对应适配器。

---

## 12. 测试

```powershell
python -m pytest -q
python -m pytest -q tests/test_adapters.py
$env:MOODVOICE_LIVE_TESTS="1"; python -m pytest -q -m live -s
```

测试分层：

- `test_styles.py`：别名、回退、参数优先级、配置冲突；
- `test_rendering.py`：四种内容类型、代码不朗读、截断；
- `test_routing.py`：intent 路由；
- `test_pipeline.py`：成功、切换、降级、超时、流中断、诊断；
- `test_adapters.py`：各厂商请求构造与错误映射（mock HTTP）；
- `test_api.py`：REST/WS/缓存/记录/同源/错误不回显；
- `test_security_cache.py`：脱敏、路径穿越、清理；
- `test_live_edge.py`：真实 edge-tts（默认跳过）。

新增厂商时至少补：成功载荷、认证失败、限流、空音频、格式不匹配（如适用）。

---

## 13. 部署注意

本项目默认是**单机 Demo**：

- 没有用户登录、配额、计费、多租户隔离；
- 不能直接暴露公网；
- 如必须局域网/远程访问：
  - 使用反向代理加认证、TLS、IP 白名单；
  - `EMOTION_TTS_ALLOWED_ORIGINS` 加实际页面 Origin；
  - 重新评估“音频仅发往用户配置厂商”的合规边界；
  - 不要把 `.env` 挂载到 Web 根目录。

并发：FastAPI 异步；每个厂商一个 `httpx.AsyncClient`；缓存/SQLite 走本地文件。
高并发场景应把 SQLite 换成正式数据库、缓存换成对象存储，并把音频清理放到后台任务。
