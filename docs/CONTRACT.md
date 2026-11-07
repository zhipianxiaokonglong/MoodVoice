# 接口契约（机器可读）

MoodVoice 对外提供一份**由运行中配置自动生成**的契约，消费方不需要读源码或猜文档：

```
GET /api/v1/contract
```

同一份版本号也出现在：

- 每个响应头：`X-MoodVoice-Contract-Version: 1.1.0`
- `GET /api/health`：`contract_version`、`contract_hash`、`contract_url`

---

## 1. 版本语义

| 字段 | 含义 |
|---|---|
| `contract_version` | **接口契约**的语义版本，当前 `1.1.0` |
| `schema_version` | 单条输出事件的 schema 版本，当前 `1.0`（四字段契约） |
| `module_version` | 程序发布版本（`config/app.yaml → app.version`） |
| `contract_hash` | 契约内容的 sha256 指纹 |

规则：

- **只做加法**（新增别名 / 新增错误码 / 新增可选字段）→ minor +1；
- **改语义或删字段** → major +1；
- `contract_hash` 只覆盖**接口事实**（标签集合、别名、策略、输出字段、错误码、上限、端点），
  不覆盖 label / tone / description 与厂商参数，所以改文案不会误报漂移。

消费方建议：**major 不匹配 → 拒绝并提示升级；minor 不匹配 → 降级并告警；hash 变化 → 记录排查**。

---

## 2. 契约内容

```json
{
  "contract_version": "1.1.0",
  "module_version": "1.0.0",
  "contract_hash": "sha256:…",
  "tags": {
    "emotion": {
      "canonical": ["neutral", "joy", "excitement", "comfort", "tenderness", "calm",
                    "sadness", "apology", "encouragement", "surprise", "frustration", "anger"],
      "fallback": "neutral",
      "unknown_policy": "fallback_and_flag",
      "unknown_flags": ["style.mapping_hit=false", "style.source_tag", "diagnostics"],
      "aliases": {"fear": "sadness", "confusion": "frustration", "gratitude": "joy", "…": "…"},
      "accepted_upstream_sets": {"omninexus_v1": ["joy", "sadness", "anger", "fear",
                                "surprise", "confusion", "frustration", "gratitude", "neutral"]}
    },
    "type": {"canonical": ["text", "code", "list", "markdown"], "coerce_to": "text",
             "unknown_policy": "coerce_to_default", "aliases": {"…": "…"}},
    "intent": {"canonical": ["linkage", "reply", "tool"],
               "unknown_policy": "route_to_unknown_destination",
               "unknown_destination": "ui.text_reply",
               "routes": {"reply": {"destination": "ui.voice_reply", "synthesize": true}, "…": {}}}
  },
  "output": {
    "required_fields": ["event_id", "text", "audio_ref", "style"],
    "schema_version": "1.0",
    "status_values": ["synthesized", "text_fallback", "text_only", "preparing"],
    "headers": ["X-MoodVoice-Status", "X-MoodVoice-Degraded", "X-MoodVoice-Egress",
                "X-MoodVoice-Contract-Version"],
    "degraded_semantics": "text_always_intact_audio_ref_null"
  },
  "errors": {
    "shape": "error.code + error.message + error.retryable (+ request_id, details)",
    "retryable_semantics": "retryable=true 表示同一请求稍后可能成功；false 表示原样重试无意义",
    "codes": {"timeout": {"retryable": true}, "invalid_event": {"retryable": false}, "…": {}}
  },
  "limits": {"max_input_chars": 8000, "max_tts_chars": 3000},
  "endpoints": {"rest": ["DELETE /api/v1/cache", "GET /api/v1/contract", "…"],
                "ws": ["/ws/v1/outputs"]}
}
```

`endpoints` 从 `app.routes` 运行时生成，不会与真实路由漂移。

---

## 3. 情绪标签口径（答复跨仓库文档 C1）

- **规范集是 12 类**：就是 `config/emotion_styles.yaml` 的完整映射集。
  收敛到 9 类会丢掉 `comfort` / `calm` / `apology` 这些陪伴场景最常用的风格。
- **9 类上游标签全部精确命中**，靠的是**显式别名**，不是兜底：

  | 上游标签 | 解析为 | 方式 |
  |---|---|---|
  | joy / sadness / anger / surprise / frustration / neutral | 原样 | 同名 |
  | fear | sadness | 别名 |
  | confusion | frustration | 别名 |
  | gratitude | joy | 别名 |

- **9 类之外**（如 `disgust`、`anticipation`）回退到 `neutral`，并在输出里写
  `style.mapping_hit=false`、`style.source_tag` 与 `diagnostics`，同时计入
  `/api/health.emotion_mapping.fallbacks`。消费方可以据此告警，而不是静默当成正常情绪。
- 契约里的 `accepted_upstream_sets` 只收录**已核实**的集合（当前是 `omninexus_v1`）；
  其他消费方请用 `aliases` 自行映射，不要依赖未经核实的名单。

---

## 4. 错误体（答复跨仓库文档 D1）

REST 与 WebSocket 统一形状：

```json
{
  "error": {
    "code": "timeout",
    "message": "等待首个音频分片超时。",
    "retryable": true,
    "request_id": "upstream-request-id",
    "details": {"provider": "openai", "stage": "provider_ttfb"}
  }
}
```

- `retryable` 一定存在（REST 与 WS 都有），消费方不需要再维护"按错误码推断表"。
- `request_id` 只在存在时出现（来自上游 `x-request-id` / ElevenLabs / MiniMax trace），
  它**不是** MoodVoice 自己的 ID，不会包含用户文本。
- 错误码表在契约的 `errors.codes` 里；新增错误码必须同时登记到
  `src/emotional_tts/errors.py`，否则 `tests/test_contract.py` 会失败。

`retryable=true` 的错误码：

```
timeout upstream_timeout upstream_conflict rate_limited upstream_error
network_error empty_audio stream_interrupted cache_write_failed
key_store_unavailable internal_error unexpected_status
```

其余全部为 `false`（参数、鉴权、URL 拦截、模型/音色缺失、格式不匹配等）。

---

## 5. 消费方接入示例

```python
import httpx

contract = httpx.get("http://127.0.0.1:8765/api/v1/contract").json()
major = int(contract["contract_version"].split(".")[0])
if major != 1:
    raise RuntimeError("MoodVoice 契约 major 版本不兼容")

emotions = set(contract["tags"]["emotion"]["canonical"])          # 强类型/下拉框
aliases = contract["tags"]["emotion"]["aliases"]                  # 归一化
retryable = {code for code, meta in contract["errors"]["codes"].items() if meta["retryable"]}
```

运行时轻量校验（不想拉全量时）：

```python
response = httpx.post(url, json=event)
version = response.headers.get("X-MoodVoice-Contract-Version")
if version and not version.startswith("1."):
    warn("MoodVoice 契约版本已变化")
```

---

## 6. 兼容性

- 契约端点、响应头、`retryable` 都是**增量**：只读 `code` / `message` 的旧消费方不受影响。
- 契约不包含密钥、密钥配置、绑定地址等运行期信息。
- `GET /api/v1/contract` 不需要认证，和 `/api/health` 一样只暴露接口事实。
