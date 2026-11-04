"""Protocol-specific model/voice discovery.

The four protocols expose different capabilities:
- OpenAI-compatible: models are discoverable; voices are usually a fixed set.
- Azure: voices (and styles) are discoverable; no model catalog.
- ElevenLabs: both models and voices are discoverable.
- MiniMax: voices are discoverable; no model catalog.

Nothing here sends user speech text; discovery requests only ask for metadata.
"""

from __future__ import annotations

from urllib.parse import urlsplit, urlunsplit

import httpx

from ..errors import ConnectionFailure
from .models import DiscoveryResult, ModelOption, Protocol, VoiceOption
from .security import validate_base_url

MAX_JSON_BYTES = 4 * 1024 * 1024

OPENAI_VOICES = [
    "alloy",
    "ash",
    "ballad",
    "coral",
    "echo",
    "fable",
    "nova",
    "onyx",
    "sage",
    "shimmer",
    "verse",
]

MINIMAX_MODELS = [
    "speech-2.8-hd",
    "speech-2.8-turbo",
    "speech-2.6-hd",
    "speech-2.6-turbo",
    "speech-02-hd",
    "speech-02-turbo",
    "speech-01-hd",
    "speech-01-turbo",
]


def normalize_base_url(protocol: Protocol, raw: str, *, allow_insecure_http: bool = False) -> str:
    url = validate_base_url(raw, allow_insecure_http=allow_insecure_http)
    parts = urlsplit(url)
    path = parts.path.rstrip("/")

    if protocol == "openai":
        if not path and (parts.hostname or "").lower() == "api.openai.com":
            path = "/v1"
    elif protocol == "elevenlabs":
        if not path and (parts.hostname or "").lower() == "api.elevenlabs.io":
            path = "/v1"
    elif protocol == "azure":
        if path.endswith("/tts/cognitiveservices/voices/list"):
            path = path[: -len("/tts/cognitiveservices/voices/list")]
        if not path or not path.endswith("/cognitiveservices/v1"):
            path = f"{path}/cognitiveservices/v1"
    elif protocol == "minimax":
        if path.endswith("/t2a_v2"):
            pass
        elif path.endswith("/v1"):
            path = f"{path}/t2a_v2"
        elif not path:
            path = "/v1/t2a_v2"
        else:
            raise ConnectionFailure(
                "MiniMax URL 请填写到 /v1/t2a_v2（或 /v1）",
                code="invalid_url",
                stage="url",
            )

    return urlunsplit((parts.scheme, parts.netloc, path, parts.query, ""))


def _voices_url_for_azure(base_url: str) -> str:
    parts = urlsplit(base_url)
    path = parts.path
    if path.endswith("/cognitiveservices/v1"):
        path = path[: -len("/cognitiveservices/v1")]
    return urlunsplit(
        (parts.scheme, parts.netloc, f"{path}/tts/cognitiveservices/voices/list", "", "")
    )


def _minimax_voice_url(base_url: str) -> str:
    parts = urlsplit(base_url)
    path = parts.path
    if path.endswith("/t2a_v2"):
        path = path[: -len("/t2a_v2")] + "/get_voice"
    elif path.endswith("/v1"):
        path = f"{path}/get_voice"
    else:
        path = f"{path}/get_voice"
    return urlunsplit((parts.scheme, parts.netloc, path, "", ""))


def _join(base: str, suffix: str) -> str:
    return f"{base.rstrip('/')}/{suffix.lstrip('/')}"


def _auth_headers(protocol: Protocol, api_key: str | None) -> dict[str, str]:
    if not api_key:
        return {}
    if protocol == "azure":
        return {"Ocp-Apim-Subscription-Key": api_key}
    if protocol == "elevenlabs":
        return {"xi-api-key": api_key}
    return {"Authorization": f"Bearer {api_key}"}


async def _get_json(
    client: httpx.AsyncClient,
    url: str,
    *,
    protocol: Protocol,
    headers: dict[str, str],
) -> object:
    try:
        response = await client.get(url, headers=headers)
    except httpx.HTTPError as exc:
        raise ConnectionFailure(
            "无法连接该 API 地址，请检查 URL 与网络。",
            code="network_error",
            stage="discover",
            details={"protocol": protocol, "exception": type(exc).__name__},
            http_status=502,
        ) from exc
    if response.status_code >= 400:
        _raise_status(protocol, response.status_code, "discover")
    if len(response.content) > MAX_JSON_BYTES:
        raise ConnectionFailure(
            "API 返回的数据过大，已拒绝解析。",
            code="response_too_large",
            stage="discover",
        )
    try:
        return response.json()
    except ValueError as exc:
        raise ConnectionFailure(
            "API 返回了非 JSON 数据。",
            code="invalid_response",
            stage="discover",
            details={"protocol": protocol},
        ) from exc


async def _post_json(
    client: httpx.AsyncClient,
    url: str,
    *,
    protocol: Protocol,
    headers: dict[str, str],
    body: dict[str, object],
) -> object:
    try:
        response = await client.post(url, headers=headers, json=body)
    except httpx.HTTPError as exc:
        raise ConnectionFailure(
            "无法连接该 API 地址，请检查 URL 与网络。",
            code="network_error",
            stage="discover",
            details={"protocol": protocol, "exception": type(exc).__name__},
            http_status=502,
        ) from exc
    if response.status_code >= 400:
        _raise_status(protocol, response.status_code, "discover")
    if len(response.content) > MAX_JSON_BYTES:
        raise ConnectionFailure(
            "API 返回的数据过大，已拒绝解析。",
            code="response_too_large",
            stage="discover",
        )
    try:
        return response.json()
    except ValueError as exc:
        raise ConnectionFailure(
            "API 返回了非 JSON 数据。",
            code="invalid_response",
            stage="discover",
            details={"protocol": protocol},
        ) from exc


def _raise_status(protocol: Protocol, status: int, stage: str) -> None:
    if status in (401, 403):
        raise ConnectionFailure(
            "API Key 无效或没有权限。",
            code="auth_failed",
            stage=stage,
            details={"protocol": protocol, "upstream_status": status},
            http_status=401,
        )
    if status == 404:
        raise ConnectionFailure(
            "该地址没有预期的 API 端点，请检查 Base URL。",
            code="endpoint_not_found",
            stage=stage,
            details={"protocol": protocol, "upstream_status": status},
            http_status=404,
        )
    if status == 429:
        raise ConnectionFailure(
            "API 已限流，请稍后再试。",
            code="rate_limited",
            stage=stage,
            details={"protocol": protocol, "upstream_status": status},
            http_status=429,
        )
    raise ConnectionFailure(
        "API 返回了错误状态，请检查配置。",
        code="upstream_error",
        stage=stage,
        details={"protocol": protocol, "upstream_status": status},
        http_status=502,
    )


def _likely_tts(model_id: str) -> bool:
    value = model_id.lower()
    return any(token in value for token in ("tts", "speech", "audio", "voice", "kokoro", "orpheus"))


async def discover_openai(
    client: httpx.AsyncClient,
    base_url: str,
    api_key: str | None,
) -> DiscoveryResult:
    models_payload = await _get_json(
        client,
        _join(base_url, "models"),
        protocol="openai",
        headers=_auth_headers("openai", api_key),
    )
    models: list[ModelOption] = []
    if isinstance(models_payload, dict):
        data = models_payload.get("data")
        if isinstance(data, list):
            for item in data:
                if not isinstance(item, dict) or not item.get("id"):
                    continue
                model_id = str(item["id"])
                models.append(
                    ModelOption(
                        id=model_id,
                        name=str(item.get("name") or model_id),
                        details={"likely_tts": _likely_tts(model_id)},
                    )
                )
    if not models:
        raise ConnectionFailure(
            "该服务没有返回模型列表。",
            code="no_models",
            stage="discover",
            details={"protocol": "openai"},
        )
    models.sort(key=lambda item: (not item.details.get("likely_tts"), item.id))

    voices = [
        VoiceOption(id=voice, name=voice.title(), details={"source": "builtin"})
        for voice in OPENAI_VOICES
    ]
    warnings: list[str] = []
    # Some OpenAI-compatible servers expose an extra voice endpoint.
    try:
        payload = await _get_json(
            client,
            _join(base_url, "audio/voices"),
            protocol="openai",
            headers=_auth_headers("openai", api_key),
        )
        raw_voices = payload.get("voices") if isinstance(payload, dict) else None
        if isinstance(raw_voices, list):
            extra = []
            for item in raw_voices:
                if isinstance(item, str):
                    extra.append(VoiceOption(id=item, name=item, details={"source": "server"}))
                elif isinstance(item, dict) and item.get("id"):
                    extra.append(
                        VoiceOption(
                            id=str(item["id"]),
                            name=str(item.get("name") or item["id"]),
                            details={"source": "server"},
                        )
                    )
            if extra:
                voices = extra
    except ConnectionFailure:
        warnings.append("该服务未提供音色列表，显示 OpenAI 常用音色；可手动填写音色 ID。")

    warnings.append("模型列表可能包含非 TTS 模型，接口测试会验证是否支持 /audio/speech。")
    return DiscoveryResult(
        ok=True,
        protocol="openai",
        base_url=base_url,
        models=models,
        voices=voices,
        capabilities={
            "streaming": True,
            "emotional_control": "instructions",
            "model_discovery": True,
            "voice_discovery": bool(voices and voices[0].details.get("source") == "server"),
        },
        warnings=warnings,
    )


async def discover_elevenlabs(
    client: httpx.AsyncClient,
    base_url: str,
    api_key: str | None,
) -> DiscoveryResult:
    models_payload = await _get_json(
        client,
        _join(base_url, "models"),
        protocol="elevenlabs",
        headers=_auth_headers("elevenlabs", api_key),
    )
    models: list[ModelOption] = []
    data = models_payload if isinstance(models_payload, list) else models_payload.get("models", [])
    if isinstance(data, list):
        for item in data:
            if not isinstance(item, dict) or not item.get("model_id"):
                continue
            models.append(
                ModelOption(
                    id=str(item["model_id"]),
                    name=str(item.get("name") or item["model_id"]),
                    details={
                        "languages": item.get("languages", []),
                        "max_characters": item.get("maximum_text_length_per_request"),
                    },
                )
            )
    voices_payload = await _get_json(
        client,
        _join(base_url, "voices"),
        protocol="elevenlabs",
        headers=_auth_headers("elevenlabs", api_key),
    )
    voices: list[VoiceOption] = []
    raw = voices_payload.get("voices", []) if isinstance(voices_payload, dict) else []
    if isinstance(raw, list):
        for item in raw:
            if not isinstance(item, dict) or not item.get("voice_id"):
                continue
            labels = item.get("labels") if isinstance(item.get("labels"), dict) else {}
            voices.append(
                VoiceOption(
                    id=str(item["voice_id"]),
                    name=str(item.get("name") or item["voice_id"]),
                    locale=labels.get("language"),
                    gender=labels.get("gender"),
                    details={"category": item.get("category"), "labels": labels},
                )
            )
    if not models:
        raise ConnectionFailure(
            "ElevenLabs 没有返回可用模型。",
            code="no_models",
            stage="discover",
            details={"protocol": "elevenlabs"},
        )
    return DiscoveryResult(
        ok=True,
        protocol="elevenlabs",
        base_url=base_url,
        models=models,
        voices=voices,
        capabilities={
            "streaming": True,
            "emotional_control": "style_intensity",
            "model_discovery": True,
            "voice_discovery": True,
        },
        warnings=[] if voices else ["该账户没有可用音色。"],
    )


async def discover_azure(
    client: httpx.AsyncClient,
    base_url: str,
    api_key: str | None,
) -> DiscoveryResult:
    payload = await _get_json(
        client,
        _voices_url_for_azure(base_url),
        protocol="azure",
        headers=_auth_headers("azure", api_key),
    )
    voices: list[VoiceOption] = []
    if isinstance(payload, list):
        for item in payload:
            if not isinstance(item, dict) or not item.get("ShortName"):
                continue
            voices.append(
                VoiceOption(
                    id=str(item["ShortName"]),
                    name=str(item.get("LocalName") or item.get("DisplayName") or item["ShortName"]),
                    locale=item.get("Locale"),
                    gender=item.get("Gender"),
                    styles=[str(style) for style in item.get("StyleList", [])],
                    details={
                        "display_name": item.get("DisplayName"),
                        "voice_type": item.get("VoiceType"),
                        "status": item.get("Status"),
                    },
                )
            )
    if not voices:
        raise ConnectionFailure(
            "Azure 没有返回音色列表。",
            code="no_voices",
            stage="discover",
            details={"protocol": "azure"},
        )
    return DiscoveryResult(
        ok=True,
        protocol="azure",
        base_url=base_url,
        models=[ModelOption(id="azure-neural-tts", name="Azure Neural TTS")],
        voices=voices,
        capabilities={
            "streaming": True,
            "emotional_control": "native_style",
            "model_discovery": False,
            "voice_discovery": True,
        },
        warnings=["Azure 没有模型目录；模型固定为 azure-neural-tts，情感风格由音色与 style 决定。"],
    )


async def discover_minimax(
    client: httpx.AsyncClient,
    base_url: str,
    api_key: str | None,
    *,
    group_id: str | None = None,
) -> DiscoveryResult:
    url = _minimax_voice_url(base_url)
    if group_id:
        url = f"{url}?GroupId={group_id}"
    payload = await _post_json(
        client,
        url,
        protocol="minimax",
        headers={**_auth_headers("minimax", api_key), "Content-Type": "application/json"},
        body={"voice_type": "all"},
    )
    voices: list[VoiceOption] = []
    if isinstance(payload, dict):
        base_resp = payload.get("base_resp") or {}
        if isinstance(base_resp, dict) and base_resp.get("status_code") not in (0, None):
            raise ConnectionFailure(
                "MiniMax 拒绝了音色列表请求。",
                code="provider_rejected",
                stage="discover",
                details={"protocol": "minimax", "vendor_status": base_resp.get("status_code")},
                http_status=400,
            )
        for key in ("system_voice", "voice_cloning", "voice_generation"):
            items = payload.get(key) or []
            if not isinstance(items, list):
                continue
            for item in items:
                if not isinstance(item, dict) or not item.get("voice_id"):
                    continue
                desc = item.get("description")
                if isinstance(desc, list):
                    desc = " · ".join(str(x) for x in desc if x)
                voices.append(
                    VoiceOption(
                        id=str(item["voice_id"]),
                        name=str(item.get("voice_name") or item["voice_id"]),
                        details={"source": key, "description": desc or ""},
                    )
                )
    if not voices:
        raise ConnectionFailure(
            "MiniMax 没有返回可用音色。",
            code="no_voices",
            stage="discover",
            details={"protocol": "minimax"},
        )
    return DiscoveryResult(
        ok=True,
        protocol="minimax",
        base_url=base_url,
        models=[ModelOption(id=model, name=model) for model in MINIMAX_MODELS],
        voices=voices,
        capabilities={
            "streaming": False,
            "emotional_control": "native_emotion",
            "model_discovery": False,
            "voice_discovery": True,
        },
        warnings=["MiniMax 没有模型发现接口；显示官方常用 TTS 模型，可手动填写其他模型。"],
    )


async def discover(
    client: httpx.AsyncClient,
    *,
    base_url: str,
    protocol: str,
    api_key: str | None,
    group_id: str | None = None,
    allow_insecure_http: bool = False,
) -> DiscoveryResult:
    """Discover without auto-detection; caller decides the protocol."""

    if protocol == "openai":
        normalized = normalize_base_url("openai", base_url, allow_insecure_http=allow_insecure_http)
        return await discover_openai(client, normalized, api_key)
    if protocol == "elevenlabs":
        normalized = normalize_base_url(
            "elevenlabs", base_url, allow_insecure_http=allow_insecure_http
        )
        return await discover_elevenlabs(client, normalized, api_key)
    if protocol == "azure":
        normalized = normalize_base_url("azure", base_url, allow_insecure_http=allow_insecure_http)
        return await discover_azure(client, normalized, api_key)
    if protocol == "minimax":
        normalized = normalize_base_url(
            "minimax", base_url, allow_insecure_http=allow_insecure_http
        )
        return await discover_minimax(client, normalized, api_key, group_id=group_id)
    raise ConnectionFailure("未知协议。", code="invalid_protocol", stage="discover")


async def auto_discover(
    client: httpx.AsyncClient,
    *,
    base_url: str,
    api_key: str | None,
    group_id: str | None = None,
    allow_insecure_http: bool = False,
) -> DiscoveryResult:
    failures: list[dict[str, object]] = []
    for protocol in ("openai", "elevenlabs", "azure", "minimax"):
        try:
            result = await discover(
                client,
                base_url=base_url,
                protocol=protocol,
                api_key=api_key,
                group_id=group_id,
                allow_insecure_http=allow_insecure_http,
            )
            if result.warnings:
                result.warnings.insert(0, f"自动识别为 {protocol}；建议保存前先试听。")
            return result
        except ConnectionFailure as exc:
            failures.append(
                {
                    "protocol": protocol,
                    "code": exc.code,
                    "stage": exc.stage,
                    "status": exc.details.get("upstream_status"),
                }
            )
    raise ConnectionFailure(
        "无法自动识别该 API 协议，请手动选择协议并检查 Base URL。",
        code="protocol_detection_failed",
        stage="discover",
        details={"attempts": failures},
        http_status=400,
    )
