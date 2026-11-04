(() => {
  "use strict";

  const $ = (id) => document.getElementById(id);
  const state = {
    health: null,
    busy: false,
    streamer: null,
    socket: null,
    prepared: null,
    sessionId: getSessionId(),
    lastOutput: null,
  };

  const els = {
    form: $("composerForm"),
    content: $("contentInput"),
    charCount: $("charCount"),
    charLimit: $("charLimit"),
    emotion: $("emotionSelect"),
    type: $("typeSelect"),
    intentSelect: $("intentSelect"),
    provider: $("providerSelect"),
    stream: $("streamToggle"),
    generate: $("generateBtn"),
    generateLabel: document.querySelector("#generateBtn .button-label"),
    clearCache: $("clearCacheBtn"),
    healthPill: $("healthPill"),
    healthText: $("healthText"),
    versionTag: $("versionTag"),
    privacyText: $("privacyText"),
    status: $("statusBadge"),
    stageEmpty: $("stageEmpty"),
    result: $("resultView"),
    audioCard: $("audioCard"),
    audio: $("audioPlayer"),
    latency: $("latencyBadge"),
    ttfb: $("ttfbValue"),
    providerBadge: $("providerBadge"),
    formatBadge: $("formatBadge"),
    download: $("downloadLink"),
    textOnly: $("textOnlyBanner"),
    textOnlyTitle: $("textOnlyTitle"),
    textOnlyReason: $("textOnlyReason"),
    emotionGlyph: $("emotionGlyph"),
    emotionName: $("emotionName"),
    tone: $("toneText"),
    speed: $("speedValue"),
    pitch: $("pitchValue"),
    degraded: $("degradedValue"),
    intent: $("intentName"),
    destination: $("destinationName"),
    routeReason: $("routeReason"),
    egressStrip: $("egressStrip"),
    egressSummary: $("egressSummary"),
    egressBadge: $("egressBadge"),
    speech: $("speechText"),
    renderArea: $("renderArea"),
    renderMeta: $("renderMeta"),
    diagnostics: $("diagnosticsJson"),
    raw: $("rawJson"),
    toast: $("toast"),
    openConnections: $("openConnectionsBtn"),
    closeConnections: $("closeConnectionsBtn"),
    connectionsPanel: $("connectionsPanel"),
    connectionForm: $("connectionForm"),
    connProtocol: $("connProtocol"),
    connLabel: $("connLabel"),
    connUrl: $("connUrl"),
    connKey: $("connKey"),
    connGroup: $("connGroup"),
    connModel: $("connModel"),
    connModelManual: $("connModelManual"),
    connVoice: $("connVoice"),
    connVoiceManual: $("connVoiceManual"),
    connInsecure: $("connInsecure"),
    discoverBtn: $("discoverBtn"),
    testConnBtn: $("testConnBtn"),
    saveConnBtn: $("saveConnBtn"),
    connectionStatus: $("connectionStatus"),
    connectionResult: $("connectionResult"),
    connectionResultTitle: $("connectionResultTitle"),
    connectionResultMeta: $("connectionResultMeta"),
    connectionWarnings: $("connectionWarnings"),
    connectionLists: $("connectionLists"),
    savedConnections: $("savedConnections"),
    savedConnectionsMeta: $("savedConnectionsMeta"),
    keyStorageText: $("keyStorageText"),
  };
  const connectionState = {
    discovery: null,
    testing: false,
    testResult: null,
    busy: false,
  };

  init().catch((error) => {
    setHealth("error", "本地服务未连接");
    showToast(error.message || "初始化失败", "error");
  });

  async function init() {
    bindEvents();
    updateCharCount();
    try {
      const response = await fetch("/api/health", { cache: "no-store" });
      if (!response.ok) throw new Error("健康检查失败");
      state.health = await response.json();
      renderHealth();
    } catch (error) {
      setHealth("error", "本地服务未连接");
      els.privacyText.textContent =
        "无法连接后端。请先在本地运行 python -m emotional_tts serve。";
      throw error;
    }
  }

  function bindEvents() {
    els.form.addEventListener("submit", generate);
    els.content.addEventListener("input", updateCharCount);
    els.provider.addEventListener("change", updatePrivacyHint);
    els.clearCache.addEventListener("click", clearLocalData);
    els.audio.addEventListener("play", () => els.audioCard.classList.add("is-playing"));
    els.audio.addEventListener("pause", () => els.audioCard.classList.remove("is-playing"));
    els.audio.addEventListener("ended", () => els.audioCard.classList.remove("is-playing"));
    els.openConnections.addEventListener("click", toggleConnections);
    els.closeConnections.addEventListener("click", () => {
      els.connectionsPanel.hidden = true;
    });
    els.connectionForm.addEventListener("submit", saveConnection);
    els.discoverBtn.addEventListener("click", discoverModels);
    els.testConnBtn.addEventListener("click", testConnection);
    els.connProtocol.addEventListener("change", () => {
      connectionState.discovery = null;
      connectionState.testResult = null;
      renderConnectionResult();
    });
    window.addEventListener("beforeunload", () => stopStream());
  }

  function renderHealth() {
    const ready = state.health.providers.filter((p) => p.ready);
    setHealth("ok", `本地就绪 · ${ready.length} 个厂商可用`);
    els.versionTag.textContent = `v${state.health.app.version}`;
    els.charLimit.textContent = String(state.health.limits.max_input_chars);
    els.content.maxLength = state.health.limits.max_input_chars;
    const current = els.provider.value || "auto";
    els.provider.replaceChildren();
    const auto = document.createElement("option");
    auto.value = "auto";
    auto.textContent = state.health.auto_ready
      ? `自动选择 · ${providerLabel(state.health.auto_ready)}`
      : "自动选择 · 无可用厂商";
    els.provider.append(auto);
    for (const provider of state.health.providers) {
      const option = document.createElement("option");
      option.value = provider.id;
      option.textContent = provider.ready
        ? provider.display_name
        : `${provider.display_name}（未配置）`;
      els.provider.append(option);
    }
    els.provider.value = [...els.provider.options].some((o) => o.value === current)
      ? current
      : "auto";
    els.keyStorageText.textContent = state.health.connection_key_storage?.persistent
      ? "系统凭据库"
      : "本次会话";
    renderSavedConnections();
    updatePrivacyHint();
    updateCharCount();
  }

  function renderSavedConnections() {
    if (!state.health) return;
    const items = state.health.connections || [];
    els.savedConnectionsMeta.textContent = items.length
      ? `${items.length} 个连接`
      : "尚未保存";
    els.savedConnections.replaceChildren();
    if (!items.length) {
      const empty = document.createElement("p");
      empty.className = "saved-empty";
      empty.textContent = "还没有 API 连接。填写上方信息，探索并保存后即可在“语音厂商”中切换。";
      els.savedConnections.append(empty);
      return;
    }
    for (const item of items) {
      const row = document.createElement("div");
      row.className = "saved-item";
      const info = document.createElement("div");
      info.className = "saved-info";
      const title = document.createElement("strong");
      title.textContent = item.label;
      const meta = document.createElement("small");
      const status = item.last_test
        ? item.last_test.ok
          ? `上次测试成功 · ${Math.round(item.last_test.first_chunk_ms || 0)} ms`
          : `上次测试失败 · ${item.last_test.code || "error"}`
        : item.key_configured
          ? "已配置密钥"
          : "未配置密钥";
      meta.textContent = `${protocolLabel(item.protocol)} · ${status}`;
      info.append(title, meta);
      const actions = document.createElement("div");
      actions.className = "saved-actions";
      const test = document.createElement("button");
      test.type = "button";
      test.className = "text-button";
      test.textContent = "试听";
      test.addEventListener("click", () => retestSaved(item.id));
      const remove = document.createElement("button");
      remove.type = "button";
      remove.className = "text-button danger";
      remove.textContent = "删除";
      remove.addEventListener("click", () => deleteSaved(item.id));
      actions.append(test, remove);
      row.append(info, actions);
      els.savedConnections.append(row);
    }
  }

  function toggleConnections() {
    els.connectionsPanel.hidden = !els.connectionsPanel.hidden;
    if (!els.connectionsPanel.hidden) {
      els.connUrl.focus();
    }
  }

  function selectedModel() {
    return els.connModel.value || els.connModelManual.value.trim();
  }

  function selectedVoice() {
    return els.connVoice.value || els.connVoiceManual.value.trim();
  }

  function protocolValue() {
    if (els.connProtocol.value !== "auto") return els.connProtocol.value;
    return connectionState.discovery?.protocol || null;
  }

  function connectionPayload(includeLabel = false) {
    const protocol = protocolValue();
    if (!protocol) throw new Error("请先探索并确认协议，或手动选择协议。");
    const baseUrl = els.connUrl.value.trim();
    if (!baseUrl) throw new Error("请填写 Base URL。");
    const payload = {
      base_url: baseUrl,
      protocol,
      model: selectedModel() || null,
      voice: selectedVoice() || null,
      group_id: els.connGroup.value.trim() || null,
      allow_insecure_http: els.connInsecure.checked,
    };
    const key = els.connKey.value.trim();
    if (key) payload.api_key = key;
    if (includeLabel) {
      const label = els.connLabel.value.trim();
      if (!label) throw new Error("请填写连接名称。");
      payload.label = label;
    }
    if (!payload.model) delete payload.model;
    if (!payload.voice) delete payload.voice;
    if (!payload.group_id) delete payload.group_id;
    return payload;
  }

  async function discoverModels() {
    if (connectionState.busy) return;
    const baseUrl = els.connUrl.value.trim();
    if (!baseUrl) {
      showToast("请填写 Base URL。", "error");
      els.connUrl.focus();
      return;
    }
    setConnectionStatus("正在探索模型…", "busy");
    connectionState.busy = true;
    try {
      const payload = {
        base_url: baseUrl,
        protocol: els.connProtocol.value,
        group_id: els.connGroup.value.trim() || null,
        allow_insecure_http: els.connInsecure.checked,
      };
      const key = els.connKey.value.trim();
      if (key) payload.api_key = key;
      if (!payload.group_id) delete payload.group_id;
      const response = await fetch("/api/v1/connections/discover", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      const body = await response.json().catch(() => null);
      if (!response.ok) throw new Error(apiErrorMessage(body, "探索失败"));
      connectionState.discovery = body;
      if (els.connProtocol.value === "auto") {
        els.connProtocol.value = body.protocol;
      }
      populateModelVoice(body);
      renderConnectionResult();
      setConnectionStatus(`已识别为 ${protocolLabel(body.protocol)}，请选择模型与音色。`, "ok");
      els.connLabel.value = els.connLabel.value || defaultLabel(body.protocol);
    } catch (error) {
      setConnectionStatus(error.message || "探索失败", "error");
      showToast(error.message || "探索失败", "error");
    } finally {
      connectionState.busy = false;
    }
  }

  function populateModelVoice(result) {
    const modelOptions = result.models || [];
    const voiceOptions = result.voices || [];
    els.connModel.replaceChildren();
    els.connVoice.replaceChildren();
    const modelPlaceholder = document.createElement("option");
    modelPlaceholder.value = "";
    modelPlaceholder.textContent = modelOptions.length ? "请选择模型" : "该协议没有模型目录";
    els.connModel.append(modelPlaceholder);
    for (const item of modelOptions) {
      const option = document.createElement("option");
      option.value = item.id;
      option.textContent = item.name && item.name !== item.id ? `${item.name} · ${item.id}` : item.id;
      els.connModel.append(option);
    }
    const voicePlaceholder = document.createElement("option");
    voicePlaceholder.value = "";
    voicePlaceholder.textContent = voiceOptions.length ? "请选择音色" : "该协议没有音色目录";
    els.connVoice.append(voicePlaceholder);
    for (const item of voiceOptions) {
      const option = document.createElement("option");
      option.value = item.id;
      const meta = [item.locale, item.gender, item.styles?.length ? item.styles.slice(0, 3).join("/") : ""]
        .filter(Boolean)
        .join(" · ");
      option.textContent = meta ? `${item.name || item.id}（${meta}）` : item.name || item.id;
      els.connVoice.append(option);
    }
    const likely = modelOptions.find((item) => item.details?.likely_tts);
    if (likely) els.connModel.value = likely.id;
    if (voiceOptions.length) els.connVoice.value = voiceOptions[0].id;
  }

  function renderConnectionResult() {
    const result = connectionState.discovery;
    if (!result) {
      els.connectionResult.hidden = true;
      return;
    }
    els.connectionResult.hidden = false;
    els.connectionResultTitle.textContent = `${protocolLabel(result.protocol)} · 探索结果`;
    els.connectionResultMeta.textContent = `${result.models?.length || 0} 模型 / ${result.voices?.length || 0} 音色`;
    els.connectionWarnings.replaceChildren();
    for (const warning of result.warnings || []) {
      const line = document.createElement("p");
      line.textContent = warning;
      els.connectionWarnings.append(line);
    }
    els.connectionWarnings.hidden = !(result.warnings || []).length;
    els.connectionLists.replaceChildren();
    const modelList = document.createElement("div");
    modelList.className = "connection-list";
    const modelTitle = document.createElement("strong");
    modelTitle.textContent = "模型";
    modelList.append(modelTitle);
    for (const item of (result.models || []).slice(0, 12)) {
      const line = document.createElement("small");
      line.textContent = item.id;
      modelList.append(line);
    }
    const voiceList = document.createElement("div");
    voiceList.className = "connection-list";
    const voiceTitle = document.createElement("strong");
    voiceTitle.textContent = "音色";
    voiceList.append(voiceTitle);
    for (const item of (result.voices || []).slice(0, 12)) {
      const line = document.createElement("small");
      line.textContent = `${item.name || item.id}${item.locale ? ` · ${item.locale}` : ""}`;
      voiceList.append(line);
    }
    els.connectionLists.append(modelList, voiceList);
  }

  async function testConnection() {
    if (connectionState.busy) return;
    let payload;
    try {
      payload = connectionPayload(false);
    } catch (error) {
      showToast(error.message, "error");
      return;
    }
    connectionState.busy = true;
    setConnectionStatus("正在调用 API 试听…（会产生一次厂商调用）", "busy");
    try {
      const response = await fetch("/api/v1/connections/test", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      const body = await response.json().catch(() => null);
      if (!response.ok) throw new Error(apiErrorMessage(body, "试听失败"));
      connectionState.testResult = body;
      if (body.audio_ref) {
        els.audio.src = body.audio_ref;
        els.audio.load();
        els.audio.play().catch(() => {});
      }
      setConnectionStatus(
        `试听成功 · 首响 ${Math.round(body.first_chunk_ms || 0)} ms · ${body.audio_bytes || 0} bytes`,
        "ok"
      );
      setStatus("试听成功", "ok");
    } catch (error) {
      setConnectionStatus(error.message || "试听失败", "error");
      showToast(error.message || "试听失败", "error");
    } finally {
      connectionState.busy = false;
    }
  }

  async function saveConnection(event) {
    event.preventDefault();
    if (connectionState.busy) return;
    let payload;
    try {
      payload = connectionPayload(true);
    } catch (error) {
      showToast(error.message, "error");
      return;
    }
    connectionState.busy = true;
    setConnectionStatus("正在保存连接…", "busy");
    try {
      const response = await fetch("/api/v1/connections", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      const body = await response.json().catch(() => null);
      if (!response.ok) throw new Error(apiErrorMessage(body, "保存失败"));
      const savedId = body.id;
      els.connKey.value = "";
      await refreshHealth();
      els.provider.value = savedId;
      updatePrivacyHint();
      setConnectionStatus("连接已保存，可以生成情感语音。", "ok");
      showToast("API 连接已保存。");
    } catch (error) {
      setConnectionStatus(error.message || "保存失败", "error");
      showToast(error.message || "保存失败", "error");
    } finally {
      connectionState.busy = false;
    }
  }

  async function retestSaved(connectionId) {
    setConnectionStatus("正在用已保存连接试听…", "busy");
    try {
      const response = await fetch(`/api/v1/connections/${encodeURIComponent(connectionId)}/test`, {
        method: "POST",
      });
      const body = await response.json().catch(() => null);
      if (!response.ok) throw new Error(apiErrorMessage(body, "试听失败"));
      if (body.audio_ref) {
        els.audio.src = body.audio_ref;
        els.audio.load();
        els.audio.play().catch(() => {});
      }
      setConnectionStatus(`已保存连接试听成功 · 首响 ${Math.round(body.first_chunk_ms || 0)} ms`, "ok");
      await refreshHealth();
    } catch (error) {
      setConnectionStatus(error.message || "试听失败", "error");
      showToast(error.message || "试听失败", "error");
    }
  }

  async function deleteSaved(connectionId) {
    if (!window.confirm("删除该 API 连接及其本地密钥？此操作不可撤销。")) return;
    try {
      const response = await fetch(`/api/v1/connections/${encodeURIComponent(connectionId)}`, {
        method: "DELETE",
      });
      const body = await response.json().catch(() => null);
      if (!response.ok) throw new Error(apiErrorMessage(body, "删除失败"));
      if (els.provider.value === connectionId) els.provider.value = "auto";
      await refreshHealth();
      setConnectionStatus("连接已删除。", "idle");
      showToast("连接已删除。");
    } catch (error) {
      showToast(error.message || "删除失败", "error");
    }
  }

  async function refreshHealth() {
    const response = await fetch("/api/health", { cache: "no-store" });
    if (!response.ok) throw new Error("健康检查失败");
    state.health = await response.json();
    renderHealth();
  }

  function setConnectionStatus(text, tone) {
    els.connectionStatus.textContent = text;
    els.connectionStatus.dataset.state = tone || "idle";
  }

  function protocolLabel(protocol) {
    return (
      {
        auto: "自动识别",
        openai: "OpenAI 兼容",
        azure: "Azure Speech",
        elevenlabs: "ElevenLabs",
        minimax: "MiniMax",
      }[protocol] || protocol
    );
  }

  function defaultLabel(protocol) {
    return `${protocolLabel(protocol)} 连接`;
  }

  function apiErrorMessage(body, fallback) {
    return body?.error?.message || body?.detail?.message || fallback;
  }

  function updateCharCount() {
    const count = [...els.content.value].length;
    els.charCount.textContent = String(count);
    els.charCount.closest(".char-count").classList.toggle(
      "is-near",
      count > Number(els.charLimit.textContent) * 0.92
    );
  }

  function updatePrivacyHint() {
    if (!state.health) return;
    const selected = els.provider.value;
    if (selected === "edge") {
      els.privacyText.textContent =
        "edge-tts 免密钥，但会把朗读文本发往微软在线服务；Demo 默认使用它。需要完全本地时请选择 local 适配器。";
      return;
    }
    if (selected === "local") {
      els.privacyText.textContent =
        "文本只会发往你在 LOCAL_TTS_URL 中配置的本地服务，不经过 MoodVoice 中转。";
      return;
    }
    if (selected !== "auto") {
      const provider = state.health.providers.find((item) => item.id === selected);
      els.privacyText.textContent = provider
        ? `文本只会发往你配置的 ${provider.display_name}。密钥从本地环境变量读取，不进入浏览器。`
        : "文本只会发往你选择的 TTS 厂商；密钥只在本地读取。";
      return;
    }
    els.privacyText.textContent =
      "自动选择会使用第一个已就绪的厂商；没有任何密钥时会使用 edge-tts，朗读文本会发往微软在线服务。每次结果都会标明实际去向。";
  }

  async function generate(event) {
    event.preventDefault();
    if (state.busy) return;
    const content = els.content.value;
    if (!content.trim()) {
      showToast("先写一点要朗读的内容。", "error");
      els.content.focus();
      return;
    }
    const eventPayload = {
      event_id: makeId(),
      session_id: state.sessionId,
      content,
      stream: true,
      tags: {
        emotion: els.emotion.value,
        type: els.type.value,
        intent: els.intentSelect.value,
      },
    };
    state.prepared = null;
    state.lastOutput = null;
    stopStream();
    resetAudio();
    setBusy(true);
    setStatus("解析标签中…", "busy");
    try {
      if (els.stream.checked && "WebSocket" in window) {
        await streamGenerate(eventPayload, els.provider.value);
      } else {
        await restGenerate(eventPayload, els.provider.value);
      }
    } catch (error) {
      if (error && error.handled) return;
      setStatus("生成失败", "error");
      showToast(error.message || "生成失败", "error");
      showFailure(error.message || "生成失败");
    } finally {
      setBusy(false);
      stopStream();
    }
  }

  async function restGenerate(eventPayload, provider) {
    const query = provider ? `?provider=${encodeURIComponent(provider)}` : "";
    const response = await fetch(`/api/v1/outputs${query}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(eventPayload),
    });
    const payload = await response.json().catch(() => null);
    if (!response.ok) {
      const message = payload?.error?.message || `请求失败（HTTP ${response.status}）`;
      throw new Error(message);
    }
    renderOutput(payload, { final: true, setAudioSrc: true });
  }

  function streamGenerate(eventPayload, provider) {
    return new Promise((resolve, reject) => {
      const protocol = location.protocol === "https:" ? "wss:" : "ws:";
      const socket = new WebSocket(`${protocol}//${location.host}/ws/v1/outputs`);
      socket.binaryType = "arraybuffer";
      state.socket = socket;
      let settled = false;
      let started = false;
      let gotDone = false;
      let gotError = false;
      const timeout = window.setTimeout(() => {
        if (settled) return;
        settled = true;
        socket.close();
        if (started && state.streamer) state.streamer.abort();
        const error = new Error("合成超时，请切换厂商或稍后重试。");
        error.handled = true;
        setStatus("合成超时", "error");
        showToast(error.message, "error");
        showFailure(error.message);
        resolve();
      }, 95000);

      const finish = (error) => {
        if (settled) return;
        settled = true;
        window.clearTimeout(timeout);
        if (error) reject(error);
        else resolve();
      };

      socket.addEventListener("open", () => {
        socket.send(JSON.stringify({ type: "synthesize", event: eventPayload, provider }));
      });

      socket.addEventListener("message", (message) => {
        if (typeof message.data !== "string") {
          if (state.streamer && started) {
            state.streamer.append(new Uint8Array(message.data));
          }
          return;
        }
        let packet;
        try {
          packet = JSON.parse(message.data);
        } catch {
          return;
        }
        if (packet.type === "preparing") {
          state.prepared = packet.output;
          renderOutput(packet.output, { preview: true });
          setStatus("正在合成…", "busy");
          return;
        }
        if (packet.type === "start") {
          started = true;
          state.prepared = packet.output;
          state.streamer = new StreamPlayer(els.audio);
          state.streamer.start(packet.audio.mime);
          renderOutput(packet.output, { streaming: true });
          els.audioCard.hidden = false;
          els.textOnly.hidden = true;
          setStatus("接收音频流…", "busy");
          return;
        }
        if (packet.type === "done") {
          gotDone = true;
          if (started && state.streamer) state.streamer.end();
          state.lastOutput = packet.output;
          renderOutput(packet.output, { final: true, streaming: started });
          finish();
          return;
        }
        if (packet.type === "error") {
          gotError = true;
          const message = packet.error?.message || "语音合成失败。";
          if (started && state.streamer) state.streamer.abort();
          setStatus("已保留文本", "warn");
          showToast(message, "error");
          showFailure(message);
          finish();
        }
      });

      socket.addEventListener("error", () => {
        if (!gotDone && !gotError) {
          const error = new Error("WebSocket 连接失败，请重试或关闭流式播放。");
          error.handled = true;
          setStatus("连接失败", "error");
          showToast(error.message, "error");
          showFailure(error.message);
        }
        finish();
      });

      socket.addEventListener("close", () => {
        if (settled) return;
        if (!gotDone) {
          const message = gotError ? "" : "连接中断，文本已保留。";
          if (message) {
            setStatus("连接中断", "warn");
            showFailure(message);
          }
          finish();
        } else {
          finish();
        }
      });
    });
  }

  function renderOutput(output, options = {}) {
    state.lastOutput = output;
    els.stageEmpty.hidden = true;
    els.result.hidden = false;

    const status = output.status || "synthesized";
    const isFailure = status === "text_fallback";
    const isTextOnly = status === "text_only";
    const isPreparing = status === "preparing";
    const isStreaming = Boolean(options.streaming) || status === "synthesized";

    els.textOnly.hidden = !(isFailure || isTextOnly);
    if (isTextOnly) {
      els.textOnlyTitle.textContent = "按意图路由 · 不合成语音";
      els.textOnlyReason.textContent =
        output.route?.reason || "当前意图只向下游发送文本。";
      setStatus("纯文本路由", "warn");
    } else if (isFailure) {
      els.textOnlyTitle.textContent = "已降级为纯文本";
        els.textOnlyReason.textContent =
        output.degradation?.message ||
        "TTS 暂时不可用，完整文本已保留，可直接展示或稍后重试。";
      setStatus("纯文本降级", "warn");
    } else if (isPreparing) {
      setStatus("准备音色…", "busy");
    } else if (isStreaming && !options.final) {
      setStatus("接收音频流…", "busy");
    } else if (output.degraded) {
      setStatus("纯文本降级", "warn");
    } else {
      setStatus("语音已就绪", "ok");
    }

    const style = output.style || {};
    const emotionLabel = style.label || style.emotion || "中性";
    els.emotionGlyph.textContent = [...emotionLabel][0] || "声";
    els.emotionName.textContent = emotionLabel;
    els.tone.textContent = style.tone || "自然表达";
    els.speed.textContent = `${Number(style.speed ?? 1).toFixed(2)}×`;
    const pitch = Number(style.pitch_semitones ?? 0);
    els.pitch.textContent = `${pitch > 0 ? "+" : ""}${pitch}`;
    const ttfb = output.timings?.first_chunk_ms;
    els.ttfb.textContent = Number.isFinite(ttfb) ? `${Math.round(ttfb)} ms` : "—";
    els.latency.textContent = Number.isFinite(ttfb)
      ? `首响 ${Math.round(ttfb)} ms`
      : "首响 —";
    els.degraded.textContent = output.degraded ? "已降级" : "正常";
    els.degraded.classList.toggle("is-warn", Boolean(output.degraded));

    els.intent.textContent = output.route?.intent || "—";
    els.destination.textContent = output.route?.destination || "—";
    els.routeReason.textContent = output.route?.reason || "";
    renderPrivacy(output.privacy);
    els.speech.textContent = output.speech_text || output.text || "—";
    els.providerBadge.textContent = output.provider
      ? providerLabel(output.provider)
      : output.requested_provider === "auto"
        ? "auto · 未合成"
        : `${providerLabel(output.requested_provider)} · 未合成`;
    els.formatBadge.textContent = output.audio_format
      ? `${output.audio_format.toUpperCase()} · ${output.diagnostics?.speech?.speech_chars || 0} 字脚本`
      : "text only";

    const hasAudio = Boolean(output.audio_ref);
    if (hasAudio) {
      els.audioCard.hidden = false;
      els.download.href = output.audio_ref;
      els.download.hidden = false;
      if (options.setAudioSrc) {
        els.audio.src = output.audio_ref;
        els.audio.load();
        els.audio.play().catch(() => {});
      }
    } else if (!options.streaming && status === "preparing") {
      els.audioCard.hidden = true;
    } else if (!hasAudio) {
      els.audioCard.hidden = true;
    }

    els.renderMeta.textContent = describeRender(output.render);
    renderBlocks(output.render, output.text);
    els.raw.textContent = JSON.stringify(output, null, 2);
    const diagnostics = output.diagnostics || {};
    els.diagnostics.textContent = JSON.stringify(
      {
        emotion_mapping: diagnostics.emotion_mapping,
        provider_params: diagnostics.provider_params,
        attempts: diagnostics.provider_attempts,
        adapter: diagnostics.adapter,
        speech: diagnostics.speech,
        privacy: output.privacy || diagnostics.privacy,
        timings: output.timings,
      },
      null,
      2
    );
  }

  function renderPrivacy(privacy) {
    if (!privacy) {
      els.egressStrip.dataset.egress = "local";
      els.egressSummary.textContent = "尚未向 TTS 厂商或本地服务发送文本";
      els.egressBadge.textContent = "未发送";
      return;
    }
    const external = Boolean(privacy.external_egress);
    els.egressStrip.dataset.egress = external ? "external" : "local";
    els.egressSummary.textContent =
      privacy.summary || (external ? "文本已发往外部 TTS 厂商" : "文本仅在本机处理");
    const sent = Array.isArray(privacy.text_sent_to) && privacy.text_sent_to.length > 0;
    els.egressBadge.textContent = sent ? (external ? "已出网" : "本机") : "未发送";
  }

  function renderBlocks(render, fallbackText) {
    els.renderArea.replaceChildren();
    const blocks = render?.blocks || [];
    if (!blocks.length) {
      const p = document.createElement("p");
      p.textContent = fallbackText || "";
      els.renderArea.append(p);
      return;
    }
    for (const block of blocks) {
      if (block.kind === "code") {
        const label = document.createElement("span");
        label.className = "code-label";
        label.textContent = block.language ? `${block.language} code` : "code";
        const pre = document.createElement("pre");
        const code = document.createElement("code");
        code.textContent = block.content || "";
        pre.append(code);
        els.renderArea.append(label, pre);
      } else if (block.kind === "list_item") {
        const item = document.createElement("div");
        item.className = "list-item";
        const num = document.createElement("span");
        num.className = "num";
        num.textContent = String(block.ordinal || "·");
        const text = document.createElement("span");
        text.textContent = block.content || "";
        item.append(num, text);
        els.renderArea.append(item);
      } else {
        const p = document.createElement("p");
        p.textContent = block.content || "";
        els.renderArea.append(p);
      }
    }
  }

  function showFailure(message) {
    if (!state.prepared && !state.lastOutput) {
      els.stageEmpty.hidden = false;
      els.result.hidden = true;
      return;
    }
    const output = state.lastOutput || state.prepared;
    if (!output) return;
    els.stageEmpty.hidden = true;
    els.result.hidden = false;
    els.textOnly.hidden = false;
    els.textOnlyTitle.textContent = "语音不可用 · 文本已保留";
    els.textOnlyReason.textContent = `${message} 可切换厂商重试；文本仍可照常展示。`;
    els.speech.textContent = output.speech_text || output.text || "—";
    els.speech.scrollIntoView({ block: "nearest", behavior: "smooth" });
  }

  function describeRender(render) {
    if (!render) return "unknown";
    if (render.type === "code") {
      return `code${render.language ? ` · ${render.language}` : ""} · ${render.blocks?.length || 0} blocks`;
    }
    if (render.type === "list") return `list · ${render.blocks?.length || 0} items`;
    if (render.type === "markdown") return "markdown · flattened for speech";
    return "plain text";
  }

  function setStatus(text, tone) {
    els.status.textContent = text;
    els.status.dataset.state = tone || "idle";
  }

  function setHealth(tone, text) {
    els.healthPill.dataset.state = tone;
    els.healthText.textContent = text;
  }

  function setBusy(busy) {
    state.busy = busy;
    document.body.dataset.busy = String(busy);
    els.generate.disabled = busy;
    els.generateLabel.textContent = busy ? "合成中…" : "生成情感语音";
    if (busy) setStatus("正在处理…", "busy");
  }

  function resetAudio() {
    if (state.streamer) {
      state.streamer.abort();
      state.streamer = null;
    }
    els.audio.pause();
    els.audio.removeAttribute("src");
    els.audio.load();
    els.audioCard.hidden = true;
    els.audioCard.classList.remove("is-playing");
    els.download.hidden = true;
    els.latency.textContent = "首响 —";
  }

  function stopStream() {
    if (state.socket) {
      try {
        state.socket.close();
      } catch {
        // ignore
      }
      state.socket = null;
    }
  }

  async function clearLocalData() {
    const ok = window.confirm(
      "将删除本机缓存音频与结构化情感记录。密钥和配置不会受影响。继续？"
    );
    if (!ok) return;
    try {
      const response = await fetch("/api/v1/user-data", { method: "DELETE" });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload?.detail?.message || "清理失败");
      showToast(
        `已清理 ${payload.deleted_files} 个音频文件、${payload.deleted_records} 条记录。`
      );
      state.health = null;
      const health = await fetch("/api/health", { cache: "no-store" }).then((r) => r.json());
      state.health = health;
      renderHealth();
    } catch (error) {
      showToast(error.message || "清理失败", "error");
    }
  }

  function showToast(message, tone = "info") {
    els.toast.textContent = message;
    els.toast.dataset.tone = tone;
    els.toast.classList.add("is-visible");
    window.clearTimeout(showToast.timer);
    showToast.timer = window.setTimeout(() => {
      els.toast.classList.remove("is-visible");
    }, 4200);
  }

  function providerLabel(id) {
    if (!id) return "—";
    if (id === "auto") return "自动选择";
    const provider = state.health?.providers?.find((item) => item.id === id);
    return provider?.display_name || id;
  }

  function getSessionId() {
    try {
      let value = sessionStorage.getItem("moodvoice.session");
      if (!value) {
        value = makeId();
        sessionStorage.setItem("moodvoice.session", value);
      }
      return value;
    } catch {
      return makeId();
    }
  }

  function makeId() {
    if (window.crypto?.randomUUID) return window.crypto.randomUUID();
    return "xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx".replace(/[xy]/g, (char) => {
      const value = (Math.random() * 16) | 0;
      const digit = char === "x" ? value : (value & 0x3) | 0x8;
      return digit.toString(16);
    });
  }

  class StreamPlayer {
    constructor(audio) {
      this.audio = audio;
      this.ms = null;
      this.sb = null;
      this.queue = [];
      this.chunks = [];
      this.url = null;
      this.mime = "audio/mpeg";
      this.useMse = false;
      this.ended = false;
      this.aborted = false;
      this.finishedMse = false;
    }

    start(mime) {
      this.reset();
      this.aborted = false;
      this.mime = mime || "audio/mpeg";
      this.useMse =
        typeof window.MediaSource !== "undefined" &&
        window.MediaSource.isTypeSupported(this.mime);
      if (!this.useMse) return;
      try {
        this.ms = new MediaSource();
        this.url = URL.createObjectURL(this.ms);
        this.audio.src = this.url;
        this.ms.addEventListener(
          "sourceopen",
          () => {
            if (this.aborted) return;
            try {
              this.sb = this.ms.addSourceBuffer(this.mime);
              this.sb.mode = "sequence";
              this.sb.addEventListener("updateend", () => this.pump());
              this.pump();
              this.audio.play().catch(() => {});
            } catch {
              this.switchToBlob();
            }
          },
          { once: true }
        );
      } catch {
        this.useMse = false;
      }
    }

    append(bytes) {
      if (this.aborted) return;
      const copy = new Uint8Array(bytes);
      this.chunks.push(copy);
      if (this.useMse) {
        this.queue.push(copy);
        this.pump();
      }
    }

    pump() {
      if (this.aborted || !this.useMse) return;
      if (!this.sb || this.sb.updating) return;
      if (this.queue.length) {
        try {
          this.sb.appendBuffer(this.queue.shift());
        } catch {
          this.switchToBlob();
        }
        return;
      }
      if (this.ended) this.finishMse();
    }

    finishMse() {
      if (this.finishedMse || !this.ms || !this.sb) return;
      if (this.sb.updating) return;
      this.finishedMse = true;
      try {
        if (this.ms.readyState === "open") this.ms.endOfStream();
      } catch {
        // ignore
      }
      this.audio.play().catch(() => {});
    }

    end() {
      this.ended = true;
      if (this.useMse) {
        this.pump();
        window.setTimeout(() => this.finishMse(), 1200);
      } else {
        this.playBlob();
      }
    }

    playBlob() {
      if (this.aborted || !this.chunks.length) return;
      const blob = new Blob(this.chunks, { type: this.mime });
      if (this.url) URL.revokeObjectURL(this.url);
      this.url = URL.createObjectURL(blob);
      this.audio.src = this.url;
      this.audio.load();
      this.audio.play().catch(() => {});
    }

    switchToBlob() {
      if (this.aborted) return;
      this.useMse = false;
      this.aborted = false;
      this.audio.pause();
      if (this.ms && this.ms.readyState === "open") {
        try {
          this.ms.endOfStream();
        } catch {
          // ignore
        }
      }
      this.playBlob();
    }

    reset() {
      this.aborted = true;
      this.queue = [];
      this.chunks = [];
      this.sb = null;
      this.ms = null;
      this.ended = false;
      this.finishedMse = false;
      if (this.url) {
        URL.revokeObjectURL(this.url);
        this.url = null;
      }
    }

    abort() {
      this.reset();
      this.audio.pause();
      this.audio.removeAttribute("src");
      this.audio.load();
    }
  }
})();
