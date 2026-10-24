# 隐私与安全说明

本文件说明 MoodVoice 处理**文本、音频、标签、密钥**的实际数据流。隐私是本项目的
最高优先级；任何新增厂商或部署方式都必须先通过这里的边界检查。

---

## 1. 一句话边界

> 用户文本只会被发送到**用户为本次合成选择的 TTS 厂商**（或用户配置的本地服务）。
> 没有隐式遥测，没有第三方 CDN，没有把文本写入日志或数据库。密钥仅在服务端内存中
> 使用，来源是本地 `.env`/环境变量。

例外必须显式且可见：

- `edge-tts` 是**在线**服务：选择它意味着朗读文本会发往微软；
- 配置了 `EMOTION_TTS_FALLBACK_PROVIDERS`：失败时文本会发往链中列出的厂商；
- 用户自行把服务暴露到局域网/公网：超出本项目的默认安全边界。

---

## 2. 数据流

```text
浏览器/调用方
   │  POST /api/v1/outputs 或 WS
   ▼
FastAPI（仅 127.0.0.1，默认）
   │  文本 + tags
   ├──► 渲染器 / 风格解析 / 路由（进程内，不落盘原文）
   │
   ├──► 所选 TTS 厂商 HTTPS（唯一外部出口）
   │       └── 返回音频字节
   │
   ├──► .data/cache/*.mp3|wav（本地音频）
   └──► .data/records.sqlite3（仅标签/风格/延迟/结果，不含原文）
   │
   ▼
输出事件（text + audio_ref + style + diagnostics）
```

没有其他网络调用。Demo 页面不加载外部字体、JS、CSS、图片或分析脚本；CSP 只允许
`self`、本地 blob/data 音频和本机 WebSocket。

---

## 3. 文本

| 位置 | 是否保存原文 | 说明 |
|---|---|---|
| 浏览器输入框 | 仅当前页面内存 | 刷新即丢失；不写 localStorage |
| API 请求体 | 否（不记录请求体） | 进程内解析 |
| 日志 | 否 | 不记录 content/headers；日志脱敏 |
| 输出事件 | 是（返回给调用方） | 契约要求 `text` 原样返回 |
| SQLite | **否** | 只保存 tags、解析后的 emotion、provider、style、延迟 |
| 音频缓存 | 音频包含朗读内容 | 本地文件，可清除 |
| 厂商侧 | 是 | 由厂商隐私政策约束；用户选择即授权 |

`diagnostics` 中只记录 `text_chars`、`ssml_chars`、`speech_chars` 等长度，不记录文本。
`request_preview` 不包含用户文本。

---

## 4. API 密钥

### 4.1 来源

- 仅从进程环境变量读取，可由仓库根目录的 `.env` 填充；
- `.env` 在 `.gitignore` 中，`.env.example` 只有占位符；
- 密钥不会写入 YAML、数据库、音频缓存、日志、API 响应或浏览器。

### 4.2 使用

- `Secrets.from_env()` 把密钥注入到对应适配器；
- 适配器只在请求头发送（Authorization / xi-api-key / Ocp-Apim-Subscription-Key）；
- `request_preview`、`health`、`diagnostics`、异常消息都不包含密钥；
- 日志 formatter 和 `sanitize_message` 会额外替换已登记的密钥与常见模式；
- 上游响应体从不写入错误消息（可能回显密钥/文本）；
- 不在 URL query 中传递密钥（MiniMax 的 GroupId 不是密钥）。

### 4.3 用户操作建议

- 使用专用、最小权限、可随时吊销的密钥；
- 定期轮换；
- 不要把 `.env` 放进截图、录屏、Issue、聊天记录；
- 怀疑泄漏时：厂商控制台吊销 → 更新 `.env` → 重启服务 → 检查账单；
- `.env` 权限建议 `chmod 600`（Windows 依赖用户目录 ACL；项目会提示 POSIX 权限问题）。

---

## 5. 音频缓存

- 目录：`.data/cache/`（可通过 `EMOTION_TTS_DATA_DIR` 改）；
- 文件名：随机 32 位 hex + 白名单扩展名，不含 event_id/用户信息；
- 写入：先 `.part`，完成后原子 rename；不完整文件在失败/中断时删除；
- 权限：POSIX 下目录 0700、文件 0600；Windows 下受当前用户目录 ACL 保护；
- 服务：只通过 `/api/v1/audio/{filename}` 返回，`Cache-Control: no-store`，
  文件名严格白名单，拒绝路径穿越；
- 保留：默认最多 200 个、168 小时；启动/写入后自动清理；
- 清除：
  - UI “清除本地数据”
  - `DELETE /api/v1/cache`
  - `moodvoice cache clear`
- 清除音频不会删除结构化记录；全清用 `DELETE /api/v1/user-data` 或
  `moodvoice cache clear --records`。

---

## 6. 结构化记录

SQLite 表 `output_records` 保存：

- 事件/会话 ID（UUID）
- 上游标签原值（emotion/type/intent）
- 解析后的 canonical emotion、mapping_hit
- 内容类型、意图、路由
- 厂商、风格参数、诊断
- 状态、降级原因、首响/总耗时、音频字节数、音频引用
- 朗读稿字符数（不是朗读稿）

**不保存**：原文、朗读稿、音频二进制、密钥、厂商响应体。

用途：产品后续版本复用情感标签/风格数据；排查“为什么这个标签听起来不对”；统计
厂商成功率和首响。用户可随时清除。

---

## 7. 网络边界

### 7.1 外部出口

| 选择 | 外部出口 | 文本是否离开本机 |
|---|---|---|
| `edge` | 微软 Edge 在线朗读 | **是** |
| `openai` | OpenAI API | 是 |
| `elevenlabs` | ElevenLabs API | 是 |
| `azure` | Azure AI Speech | 是 |
| `minimax` | MiniMax API | 是 |
| `local` | 用户配置的 URL | 取决于该 URL 是否为本机 |
| 纯文本降级 | 无 | 否 |

- 没有配置的厂商不会被调用；
- 自动模式只选择已就绪的厂商；
- 降级链必须显式配置，避免“失败后偷偷发到另一家”；
- 本地服务如果配置成远程 URL，文本同样会离开本机；请自行确认。

### 7.2 本地入口

- 默认绑定 `127.0.0.1`，不接受局域网/公网连接；
- `Origin` 校验：只允许同源（`127.0.0.1:port` / `localhost:port`）和
  `EMOTION_TTS_ALLOWED_ORIGINS` 显式列出的来源；
- 拒绝跨站浏览器请求，降低 DNS rebinding/恶意网页调用本地 TTS 的风险；
- CSP 限制脚本、样式、连接、媒体来源；
- 没有登录/配额/多租户鉴权——这是单机 Demo 的已知边界。

如果必须远程访问：

1. 反向代理加认证、TLS、IP 白名单；
2. 设置 `EMOTION_TTS_ALLOWED_ORIGINS`；
3. 重新评估厂商数据合规与用户告知；
4. 不要把服务直接 `--host 0.0.0.0` 裸露到公网。

---

## 8. 日志与错误

- 不记录请求体、响应体、Authorization、密钥；
- `httpx`/`httpcore`/`aiohttp` 的 INFO 日志被关闭，避免把带 GroupId 等账户参数的
  完整厂商 URL 写进日志；
- 错误信息只保留错误码、阶段、厂商名、状态码、request_id 和缺失项名称；
- 日志 formatter 对已登记密钥、`sk-*`、Bearer、`api_key=` 等模式脱敏；
- 校验错误响应会剔除 pydantic 的 `input` 字段，避免把原文复制到界面/日志；
- 结构化记录中的 `error_message` 是面向用户的短消息，最多 240 字符。

---

## 9. 仓库卫生

提交前必须确认：

- [ ] 无 `.env` / `.env.*`（`.env.example` 除外）
- [ ] 无 API Key、账户 ID、Cookie、Authorization
- [ ] 无 `.data/`、音频、SQLite、日志
- [ ] 无模型权重/大文件（文档说明下载方式）
- [ ] 无测试音频、临时脚本、调试输出
- [ ] `git status` 只包含源代码/文档/配置模板
- [ ] `git grep -i "sk-\|api[_-]key\|secret"` 只命中占位符和文档

---

## 10. 威胁模型（简版）

| 威胁 | 缓解 |
|---|---|
| 恶意网页调用本机 TTS | Origin 校验 + 默认 loopback + CSP |
| 密钥提交到 Git | `.gitignore`、`.env.example`、提交检查 |
| 日志泄漏密钥/文本 | 不记录 body、脱敏 formatter、错误体不回显 |
| 上游响应体回显密钥 | 只保留状态码/request_id，不保存 body |
| 缓存文件被遍历 | 随机文件名 + 白名单正则 + resolve 父目录校验 |
| 未清理音频长期留存 | TTL + 数量上限 + UI/API/CLI 清除 |
| 失败后意外外发到其他厂商 | 默认纯文本；降级链必须显式配置 |
| 局域网他人使用 | 默认不监听局域网；远程需自行加认证 |

---

## 11. 事件响应

如果怀疑密钥/音频/文本泄漏：

1. 立即停止服务；
2. 在厂商控制台吊销密钥并新建；
3. 删除 `.env` 中旧值，更新为新密钥；
4. 清理 `.data/`；
5. 检查 Git 历史与日志，必要时用 `git filter-repo` 清理历史并强推；
6. 检查厂商账单/调用记录；
7. 按组织合规流程通知受影响用户。

本项目不收集遥测，因此无法替用户做“服务端删除”；所有用户数据都在用户自己的机器上。
