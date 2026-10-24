# 用户文档 · 安装、Demo 与 BYOK

本文面向第一次运行 MoodVoice 的使用者。目标：不接其他模块，独立完成
“输入文本 → 输出带情感语音”的完整演示；然后按需接入自己的 TTS 厂商账户。

---

## 1. 运行环境

- Python **3.11+**（推荐 3.12）
- Windows / macOS / Linux
- 首次安装依赖时需要网络
- 使用默认 `edge-tts` 时需要能访问微软在线朗读服务（不需要账号或密钥）
- 若要求文本完全不出本机，请直接使用 `local` 适配器（见第 6 节）

检查：

```powershell
python --version
```

如果系统里有多个 Python，建议显式创建虚拟环境，避免依赖装到全局。

---

## 2. 安装

在仓库根目录执行（PowerShell）：

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
pip install -e .
Copy-Item .env.example .env
```

macOS / Linux：

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt
pip install -e .
cp .env.example .env
```

> 如果 PowerShell 提示“禁止运行脚本”，可以不激活虚拟环境，直接使用
> `.\.venv\Scripts\python.exe -m emotional_tts serve`。

`.env` 已被 `.gitignore` 忽略；不要提交、不要发给他人。

---

## 3. 启动 Demo

```powershell
python -m emotional_tts serve
# 或安装后：
moodvoice serve
```

看到类似输出即成功：

```text
MoodVoice Demo: http://127.0.0.1:8765
只监听本机；按 Ctrl+C 停止。
```

打开浏览器访问 **http://127.0.0.1:8765**。

### 不需要密钥的第一次试听

1. 保留默认文本；
2. 情感标签选“安慰”；
3. 内容类型选“普通文本”；
4. 意图选“回复”；
5. 语音厂商选“自动选择”；
6. 打开“流式播放”，点击“生成情感语音”。

你会看到：

- 音频在合成过程中就开始播放（浏览器支持 MSE 时）；
- “首响”显示从收到事件到收到第一段音频的毫秒数；
- 下方展示实际朗读稿、文本渲染、路由和诊断；
- 默认 `edge` 会返回 `route=ui.voice_reply`，并生成一条本地记录。

### Demo 里值得试的四个场景

| 场景 | 操作 | 预期 |
|---|---|---|
| 情感差异 | 同一段话分别选“安慰 / 开心 / 兴奋” | 语速、音高、音色/风格参数变化；`speed` 与 `tone` 同步变化 |
| 代码不朗读 | 内容类型选“代码”，粘贴一段 Python | 屏幕显示代码块，朗读只说“代码已经显示在屏幕上” |
| 意图路由 | 意图选“工具结果” | 不合成语音，状态为“纯文本路由”，`audio_ref=null` |
| 纯文本降级 | 选一个显示“未配置”的厂商 | 保留完整文本，状态为“纯文本降级”，诊断里能看到失败原因 |

> 默认 `edge-tts` 是**在线**服务：不需要账号，但朗读文本会发往微软的朗读服务。
> UI 的“数据边界”卡片会随厂商切换更新说明。

---

## 4. 命令行 Demo

不启动 Web 也可以跑完整链路：

```powershell
python -m emotional_tts demo "今天辛苦了，先慢慢呼吸一下。" `
  --emotion comfort `
  --type text `
  --intent reply `
  --provider edge `
  --out .data/demo.mp3
```

- 标准输出是完整输出事件 JSON（含 `style`、`render`、`route`、`diagnostics`、`timings`）。
- `--out` 会把缓存音频复制到指定位置；不指定时只写入 `.data/cache/`。
- 不是所有厂商都能匿名运行；`--provider openai` 等需要先完成 BYOK。

---

## 5. BYOK：填写你自己的密钥

### 5.1 三条硬规则

1. 密钥**只**放在本地 `.env` 或系统环境变量里；
2. 不把 `.env`、密钥、账户信息放进仓库、截图、Issue 或聊天；
3. 不把密钥填进 Demo 页面。页面不会读取密钥，服务端也不会通过 API 返回密钥。

复制模板后，只填写你实际会用到的字段：

```powershell
Copy-Item .env.example .env
notepad .env
```

配置完成后重启 `moodvoice serve`，再执行：

```powershell
moodvoice providers
```

该命令只显示“是否就绪 / 缺少哪个环境变量”，不会打印密钥值。

### 5.2 通用变量表

| 厂商 | 环境变量（`.env`） | 非密钥配置 |
|---|---|---|
| edge-tts | 无 | `config/app.yaml → providers.edge.voice` |
| OpenAI TTS | `OPENAI_API_KEY` | `model`、`voice`、`response_format`；可选 `OPENAI_BASE_URL` |
| ElevenLabs | `ELEVENLABS_API_KEY` | `config/app.yaml → providers.elevenlabs.voice_id`、`model_id` |
| Azure Neural TTS | `AZURE_SPEECH_KEY`、`AZURE_SPEECH_REGION` | `voice`、`output_format` |
| MiniMax 海螺 | `MINIMAX_API_KEY`，按账户需要 `MINIMAX_GROUP_ID` | `model`、`voice_id`、`base_url` |
| 本地 TTS | 可选 `LOCAL_TTS_API_KEY` | `LOCAL_TTS_URL`、`format`、`voice` |

### 5.3 OpenAI TTS

1. 登录 <https://platform.openai.com/>，在 API Keys 页面创建项目密钥；
2. 确认项目有 TTS 模型权限和可用额度；
3. 编辑 `.env`：

```dotenv
OPENAI_API_KEY=<your-openai-api-key>
```

4. 默认模型是 `gpt-4o-mini-tts`。它支持自然语言表演指令，情感映射会通过
   `instructions` 传入；`tts-1` / `tts-1-hd` 只支持语速，代码会自动省略
   `instructions`，避免厂商拒绝参数。
5. 可选：如果你使用兼容网关，设置 `OPENAI_BASE_URL=https://你的网关/v1`。
6. 重启服务，在 Demo 中选择 “OpenAI TTS”，或 CLI 加 `--provider openai`。

### 5.4 ElevenLabs

1. 登录 <https://elevenlabs.io/>，在账户设置里创建 API Key；
2. 在 Voices 页面复制目标 Voice ID；
3. 编辑 `.env`：

```dotenv
ELEVENLABS_API_KEY=你的密钥
```

4. 把 Voice ID 填到 `config/app.yaml`（它不是密钥，但仍建议只放本地）：

```yaml
providers:
  elevenlabs:
    voice_id: "你的_voice_id"
```

5. 重启并选择 “ElevenLabs”。
6. 情感映射使用 `stability / similarity_boost / style / speed`，不依赖离散
   `style` 枚举；不同 Voice 对风格强度的响应可能不同，以试听为准。

### 5.5 Azure Neural TTS

1. 在 Azure Portal 创建 Speech 资源；
2. 在 “Keys and Endpoint” 复制 Key 和 Region（例如 `eastasia`）；
3. 编辑 `.env`：

```dotenv
AZURE_SPEECH_KEY=你的密钥
AZURE_SPEECH_REGION=eastasia
```

4. 重启并选择 “Azure Neural TTS”。
5. 默认音色为 `zh-CN-XiaoxiaoNeural`。Azure 的原生情感风格支持范围**因音色而异**；
   如果某风格不被该音色支持，会收到 4xx，诊断中会记录 `style_resolution` 或
   `provider_rejected`，请按根因手册检查映射和音色，而不是盲目改语速。

### 5.6 MiniMax 海螺语音

1. 登录 MiniMax 开放平台，创建 API Key，确认账户信息和音色；
2. 编辑 `.env`：

```dotenv
MINIMAX_API_KEY=你的密钥
MINIMAX_GROUP_ID=你的_group_id
```

3. 在 `config/app.yaml` 确认 `providers.minimax.voice_id` 和 `model`；
4. 中国大陆默认端点为 `https://api.minimax.chat/v1/t2a_v2`；国际站可设置
   `MINIMAX_BASE_URL=https://api.minimax.io/v1/t2a_v2`；
5. 重启并选择 “MiniMax 海螺语音”。
6. 该适配器使用同步接口一次性返回 hex 音频，因此首响通常高于流式厂商；
   这是厂商协议限制，不是适配层故障。

### 5.7 本地部署（完全不出本机）

推荐场景：GPT-SoVITS、CosyVoice、F5-TTS，或你自己的推理服务。

1. 在本地启动一个 HTTP 服务，实现 `docs/PROVIDERS.md` 里的统一契约；
2. `.env`：

```dotenv
LOCAL_TTS_URL=http://127.0.0.1:9880/synthesize
# 如果本地服务开了 Bearer 鉴权：
LOCAL_TTS_API_KEY=
```

3. 在 `config/app.yaml` 确认：

```yaml
providers:
  local:
    format: "wav"   # 必须与本地服务实际返回一致
```

4. 重启，在 Demo 选择 “本地 TTS（HTTP）”。
5. 适配器会把 `emotion / tone / speed / pitch_semitones / volume_db` 原样放进
   `style` 载荷，由本地模型决定如何使用。MoodVoice 不会把文本转发到其他地方。

也可以选择 `edge` 做免密钥演示，但要清楚它是**在线**服务，不是本地推理。

---

## 6. 厂商切换与降级

### 6.1 切换

- **Demo**：右上“语音厂商”下拉框，立即生效；
- **API**：`POST /api/v1/outputs?provider=azure`；
- **默认值**：`.env` 的 `EMOTION_TTS_PROVIDER=auto` 或某个厂商 ID；
- **自动模式**：按 `config/app.yaml` 的 `auto_order` 选第一个“已就绪”的厂商。

可用厂商 ID：`edge`、`openai`、`elevenlabs`、`azure`、`minimax`、`local`。

### 6.2 降级规则

- 默认：所选厂商失败 → **纯文本输出**（`degraded=true`，`audio_ref=null`）。
- 可选：显式配置降级链：

```dotenv
EMOTION_TTS_FALLBACK_PROVIDERS=local
```

- 只有写进降级链的厂商才会收到同一段文本；没有显式配置时不会偷偷换厂商。
- 所有尝试记录在 `diagnostics.provider_attempts`，包含厂商、错误码、阶段、
  可重试性和 `first_chunk_ms`。
- 流式播放中，如果已经向浏览器发出音频后才失败，不会把两个厂商的音频拼在一起；
  服务端会关闭流、删除不完整文件，并在记录中标记 `stream_interrupted`。

### 6.3 验证降级

在 Demo 里选择一个显示“未配置”的厂商，点击生成：

- 页面显示“已降级为纯文本”；
- 音频区域不播放；
- “开发诊断”里能看到 `missing_credentials` 和缺少的环境变量名（不含值）；
- 文本、渲染和路由仍正常下发。

---

## 7. 清除本地数据

音频和结构化记录都只在本机。三种清理方式：

1. **Demo**：右上“清除本地数据”；
2. **HTTP API**：

```powershell
# 只清音频
Invoke-RestMethod -Method Delete http://127.0.0.1:8765/api/v1/cache
# 只清结构化记录
Invoke-RestMethod -Method Delete http://127.0.0.1:8765/api/v1/records
# 全清
Invoke-RestMethod -Method Delete http://127.0.0.1:8765/api/v1/user-data
```

3. **CLI**：

```powershell
moodvoice cache info
moodvoice cache clear
moodvoice cache clear --records
```

说明：

- 音频默认保留 168 小时，最多 200 个文件，超出自动清理；
- 结构化记录**不含原文和朗读稿**，只含标签、风格、厂商、延迟、结果；
- 删除 `.data/` 目录可彻底重置，但请先停止服务。

---

## 8. 常见问题

| 现象 | 可能原因 | 处理 |
|---|---|---|
| 页面显示“本地服务未连接” | 服务未启动或端口不对 | 确认 `moodvoice serve` 正在运行，访问控制台打印的地址 |
| 选了 OpenAI 却“纯文本降级” | 没有 `OPENAI_API_KEY` / 额度不足 / 网络 | `moodvoice providers`；检查 `.env` 是否在仓库根目录；重启 |
| ElevenLabs 401 或 404 | Key 错误或 Voice ID 不存在 | 重新复制 Voice ID 到 `config/app.yaml`；确认账号权限 |
| Azure 返回 400 | 该音色不支持所选风格，或 region 错 | 换支持该风格的音色；用 `moodvoice diagnose --emotion ... --provider azure` 查看实际请求参数 |
| MiniMax 提示 GroupId | 账户需要 GroupId | 补 `MINIMAX_GROUP_ID`，或改用国际站端点 |
| 本地服务 `format_mismatch` | 返回音频格式与 `providers.local.format` 不一致 | 让两者一致（WAV/MP3），不要只改文件后缀 |
| 有文字没声音 | 意图是 `tool`/`linkage`，或厂商失败 | 看 `route.synthesize` 与 `degraded`；切换意图/厂商 |
| 浏览器无法流式播放 | 浏览器不支持该 MIME 的 MSE | 会自动退化为整段播放；仍可下载音频；首响指标在服务端有效 |
| 端口被占用 | 8765 已被其他程序使用 | `moodvoice serve --port 8787`，同时访问新地址 |
| 想从局域网访问 | 默认只监听 127.0.0.1 | 自行加反向代理鉴权；设置 `EMOTION_TTS_HOST=0.0.0.0`、端口和 `EMOTION_TTS_ALLOWED_ORIGINS`；**不要裸奔到公网** |

### 端口/地址覆盖

```powershell
moodvoice serve --host 127.0.0.1 --port 8787
```

`.env` 也支持：

```dotenv
EMOTION_TTS_HOST=127.0.0.1
EMOTION_TTS_PORT=8765
```

---

## 9. 安全清单

- [ ] `.env` 在 `.gitignore` 中，且没有被 `git add -f`
- [ ] 密钥只在自己的机器上，未出现在 Demo、日志、Issue、截图里
- [ ] 使用 `moodvoice providers` 验证，而不是在终端 `echo` 密钥
- [ ] 默认只监听 `127.0.0.1`
- [ ] 不需要语料留存时，定期“清除本地数据”
- [ ] 若切换 `edge`，已告知最终用户文本会离开本机
- [ ] 若部署到服务器，已加鉴权、TLS、访问控制，并重新评估厂商合规

更多细节：[PRIVACY.md](PRIVACY.md)。
