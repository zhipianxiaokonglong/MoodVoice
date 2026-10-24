# 厂商接入与推荐清单

MoodVoice 只提供**适配层**，不内置任何密钥、账户、试用额度或代充服务。所有厂商都
遵循 BYOK（Bring Your Own Key）：用户在自己的账户中开通、配置和付费。

---

## 1. 内置适配器

| ID | 中文名 | 流式 | 情感控制方式 | 需要密钥 | 音频文本去向 |
|---|---|---|---|---|---|
| `edge` | edge-tts | ✅ | 韵律（voice/rate/pitch/volume） | 否 | 微软 Edge 在线朗读服务 |
| `openai` | OpenAI TTS | ✅ | `instructions` 表演指令 | 是 | OpenAI API |
| `elevenlabs` | ElevenLabs | ✅ | `stability/style/speed` 风格强度 | 是 | ElevenLabs API |
| `azure` | Azure Neural TTS | ✅ | `mstts:express-as` 原生风格 + 强度 | 是 | Azure AI Speech |
| `minimax` | MiniMax 海螺语音 | ❌ 同步 | `voice_setting.emotion` 枚举 | 是 | MiniMax API |
| `local` | 本地 TTS（HTTP） | ✅ | 自定义 `style` 载荷 | 可选 | 用户指定的本地服务 |

### 如何选择

- **想立刻听**：`edge`（免密钥；在线）。
- **要最直接的情感风格枚举**：Azure。音色对 style 的支持范围需逐一验证。
- **要高自然度和自然语言表演指令**：OpenAI `gpt-4o-mini-tts`。
- **要高表现力/克隆音色**：ElevenLabs；用 `style` 强度表达情绪。
- **国内合规/中文场景**：MiniMax；或通过 `local` 接火山/阿里/讯飞/腾讯的自有网关。
- **文本绝不能离开本机**：`local` + GPT-SoVITS/CosyVoice/F5-TTS 本地推理。

---

## 2. 推荐清单（含未内置项）

产品侧建议清单与其他模块一致。以下是**推荐**，不是“本仓库全部已实现”：

### 国际

- **ElevenLabs** — 已内置，风格强度 + 多语言。
- **OpenAI TTS** — 已内置，`gpt-4o-mini-tts` 指令式情感。
- **Azure Neural TTS** — 已内置，原生情感 style。
- **Cartesia** — 推荐但未内置。Sonic 系列有原生 emotion / speed / volume 控制，
  适合低延迟流式；接入需按其当前 API 版本加 `Cartesia-Version` 和 voice ID。
  可按第 6 节新增适配器。

### 国内

- **MiniMax（海螺语音）** — 已内置，同步 hex 音频。
- **火山引擎 / 豆包语音** — 推荐但未内置；通常需要 AppID/Token/Cluster 和签名，
  建议用 `local` 网关或新增专用适配器。
- **阿里云智能语音** — 推荐但未内置；使用阿里云 POP 签名，建议专用适配器。
- **讯飞** — 推荐但未内置；WebSocket 鉴权/签名较特殊，建议专用适配器。
- **腾讯云 TTS** — 推荐但未内置；TC3 签名，建议专用适配器。
- **Deepgram** — 低延迟 Aura 系列；是否支持细粒度情感取决于当前模型/参数，
  建议专用适配器并做真实音色验证。

### 本地部署（免费用户）

- **GPT-SoVITS** — 通过 `local` HTTP 网关接入。
- **CosyVoice** — 通过 `local` HTTP 网关接入。
- **F5-TTS** — 通过 `local` HTTP 网关接入。
- **edge-tts** — 已内置，但它是**在线**服务（无需账户）；严格来说不属于本地推理。
  如果产品承诺“音频文本不出本机”，不要把它标成“本地”。

> 只推荐厂商，不提供密钥、账户、代注册、代充值。用户需要在各厂商控制台自行开通。

---

## 3. 能力与限制（务必如实告知用户）

### edge-tts

- 免密钥，适合 Demo；
- 不支持 Azure 那样的离散情感 `style`，只能改音色/语速/音高/音量；
- 文本会发往微软在线朗读服务；需在隐私说明中披露；
- 服务可用性依赖微软，不保证 SLA。

### Azure

- `mstts:express-as` 是最直接的情感表达；
- `style` 支持范围**因 voice 而异**，不是所有音色都支持所有 style；
- 不支持的 style 通常返回 4xx；诊断会记录 `effective_style`，请换支持该 style 的音色，
  而不是用-speed 掩盖。

### OpenAI

- `gpt-4o-mini-tts` 支持 `instructions`；
- `tts-1` / `tts-1-hd` 不接受 instructions，适配器会自动省略；
- 情感表达依赖指令质量，不保证可复现的离散风格。

### ElevenLabs

- 需要 API Key + Voice ID；
- 情感通过 `stability/style/speed` 组合，不是枚举；
- 不同 Voice 对同一组参数的响应可能不同；
- 克隆音色的授权由用户负责。

### MiniMax

- 当前适配器使用同步接口，返回 hex 音频，首响高于流式厂商；
- `emotion` 枚举有限（happy/sad/angry/fearful/disgusted/surprised/calm 等），
  `neutral` 会省略让模型自行判断；
- 中国大陆/国际站端点不同，按账户配置 `MINIMAX_BASE_URL`。

### local

- 音频质量/情感表达由本地模型决定；
- MoodVoice 只透传文本、emotion、tone、speed、pitch、volume 和格式；
- 需要用户自己保证服务只监听可信地址，不会把文本二次转发。

---

## 4. 本地 HTTP 适配器契约

任何语言/框架都可以实现。MoodVoice 用 `POST` 调用 `LOCAL_TTS_URL`，不跟随重定向，
并可选发送 Bearer 鉴权。

### 4.1 请求

```http
POST /synthesize HTTP/1.1
Content-Type: application/json
Accept: audio/*, application/json
Authorization: Bearer <LOCAL_TTS_API_KEY>   # 可选

{
  "text": "今天辛苦了，先慢慢呼吸一下。",
  "voice": "default",
  "language": "zh-CN",
  "emotion": "comfort",
  "style": {
    "tone": "温柔低缓",
    "speed": 0.92,
    "pitch_semitones": -1,
    "volume_db": -1
  },
  "format": "wav"
}
```

`format` 必须与 `config/app.yaml` 的 `providers.local.format` 一致，当前支持
`wav / mp3 / ogg / opus / flac`。

### 4.2 响应 A：二进制流（推荐）

```http
HTTP/1.1 200 OK
Content-Type: audio/wav
Transfer-Encoding: chunked

<WAV bytes>
```

- 支持 chunked，可边推理边返回；
- `Content-Type` 必须是 `audio/wav`、`audio/mpeg`、`audio/ogg`、`audio/flac`
  或 `application/octet-stream`；
- 如果声明格式与配置不一致，MoodVoice 会返回 `format_mismatch`，不会把 MP3
  字节写进 `.wav`。

### 4.3 响应 B：JSON + base64

```http
HTTP/1.1 200 OK
Content-Type: application/json

{
  "audio_base64": "UklGRi...",
  "format": "wav"
}
```

也支持字段名 `audio_b64`。此模式仍然只返回一次，适合不能流式的推理脚本。

### 4.4 错误

```http
HTTP/1.1 503 Service Unavailable
Content-Type: application/json

{"error": "model loading"}
```

适配器只读取 HTTP 状态，不把上游响应体写入日志或 API 响应（防止回显用户文本/密钥）。

### 4.5 最小 FastAPI 包装示例

以下只是“把本地模型接到统一契约”的骨架，不包含具体模型加载：

```python
from fastapi import FastAPI, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel

app = FastAPI()

class Style(BaseModel):
    tone: str = ""
    speed: float = 1.0
    pitch_semitones: float = 0
    volume_db: float = 0

class SynthRequest(BaseModel):
    text: str
    voice: str = "default"
    language: str = "zh-CN"
    emotion: str = "neutral"
    style: Style = Style()
    format: str = "wav"

@app.post("/synthesize")
def synthesize(req: SynthRequest):
    if not req.text.strip():
        raise HTTPException(400, "empty text")
    audio = my_local_model_tts(  # 你的 GPT-SoVITS / CosyVoice / F5-TTS 推理
        text=req.text,
        voice=req.voice,
        speed=req.style.speed,
        emotion=req.emotion,
    )
    return Response(content=audio, media_type="audio/wav")
```

`my_local_model_tts` 需要返回与 `format` 匹配的音频字节。

### 4.6 GPT-SoVITS / CosyVoice / F5-TTS

三者都没有跨版本稳定的“统一 API”，因此不内置硬编码：

- **GPT-SoVITS**：在本地启动其 API 服务，包装 `/tts` 或 `/tts/get_audio` 为上述
  `/synthesize`；参考音频路径、prompt 文本由本地服务配置，不下发给 MoodVoice。
- **CosyVoice**：用其 Python API 或 Gradio/HTTP 服务包装；把 `emotion` 映射到
  参考音频、 instruct 文本或模型参数。
- **F5-TTS**：用 CLI/Python API 生成 WAV 后返回；如果一次推理较慢，使用 chunked
  响应或先返回 JSON base64。

情感是否真正生效，取决于本地模型是否支持参考音频/情绪 prompt；MoodVoice 不能替
模型“变出”情感。请在本地侧做 A/B 验证。

---

## 5. 低延迟建议

1. 优先选择字节流厂商（Azure/OpenAI/ElevenLabs/edge）；
2. 文本在进入 TTS 前清理代码/标记，减少合成时长；
3. 保持 `first_chunk_timeout_seconds` 在 8–12 秒，避免用户无限等待；
4. 本地模型预热，避免首个请求加载权重；
5. 不要在 TTS 前做二次 LLM 改写（会增加首响，也越过职责边界）；
6. 长文本按句切分是下一版扩展；当前按 `max_tts_chars` 截断并显式提示。

---

## 6. 新增一个专用适配器

1. 新建 `src/emotional_tts/providers/your_vendor.py`，继承 `TTSProvider`；
2. 声明真实的 `capabilities.data_destination` 和情感控制类型；
3. 实现请求构造与 `stream()`；
4. 在 `providers/registry.py` 注册；
5. 在 `config.py` 的 `KNOWN_PROVIDERS` 增加 ID；
6. 在 `config/app.yaml` 和 `.env.example` 增加配置；
7. 在 `config/emotion_styles.yaml` 增加每个情感的 vendor override；
8. 添加 mock HTTP 测试（401/429/空音频/成功载荷）；
9. 更新本文件的能力表；
10. 跑 `pytest` 和真实小样本试听。

接口细节见 [DEVELOPER_GUIDE.md](DEVELOPER_GUIDE.md#6-厂商适配器)。
