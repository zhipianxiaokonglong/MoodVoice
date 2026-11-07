"""Contract tests: the machine-readable document must match live behaviour."""

from __future__ import annotations

import json
import re
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from emotional_tts.api import create_app
from emotional_tts.contract import (
    ACCEPTED_UPSTREAM_SETS,
    CONTRACT_VERSION,
    ContractProvider,
    build_contract,
)
from emotional_tts.errors import RETRYABLE_CODES, is_retryable

SRC = Path(__file__).resolve().parents[1] / "src" / "emotional_tts"


def contract_for(config, registry, router=None):
    from emotional_tts.routing import load_router
    from emotional_tts.styles import load_stylebook

    styles = load_stylebook(config.styles_path)
    router = router or load_router(config.routes_path)
    app = create_app(config=config, registry=registry)
    return build_contract(styles=styles, router=router, config=config, app=app)


def test_contract_matches_stylebook(config, registry):
    from emotional_tts.styles import load_stylebook

    styles = load_stylebook(config.styles_path)
    doc = contract_for(config, registry)
    emotion = doc["tags"]["emotion"]

    assert emotion["canonical"] == sorted(styles.emotions)
    assert emotion["fallback"] == styles.fallback_key
    assert emotion["canonical"] == sorted(
        [
            "neutral",
            "joy",
            "excitement",
            "comfort",
            "tenderness",
            "calm",
            "sadness",
            "apology",
            "encouragement",
            "surprise",
            "frustration",
            "anger",
        ]
    )
    # Every alias in the YAML must be addressable in the contract.
    for key, definition in styles.emotions.items():
        assert emotion["aliases"][key] == key
        for alias in definition.aliases:
            from emotional_tts.styles import normalize_tag

            assert emotion["aliases"][normalize_tag(alias)] == key


def test_contract_matches_router(config, registry):
    from emotional_tts.routing import load_router

    router = load_router(config.routes_path)
    doc = contract_for(config, registry, router)
    intent = doc["tags"]["intent"]

    assert intent["canonical"] == sorted(router.model.intents)
    assert intent["unknown_destination"] == router.model.unknown_intent
    for name, definition in router.model.intents.items():
        assert intent["routes"][name]["destination"] == definition.destination
        assert intent["routes"][name]["synthesize"] == definition.synthesize


def test_contract_endpoints_match_registered_routes(config, registry):
    app = create_app(config=config, registry=registry)
    from emotional_tts.contract import describe_endpoints

    endpoints = describe_endpoints(app)
    contracted = contract_for(config, registry)["endpoints"]

    assert contracted["rest"] == endpoints["rest"]
    assert contracted["ws"] == endpoints["ws"]
    assert "GET /api/v1/contract" in contracted["rest"]
    assert "POST /api/v1/outputs" in contracted["rest"]
    assert contracted["ws"] == ["/ws/v1/outputs"]


def test_every_literal_error_code_is_registered():
    """A new code raised in src/ must be added to errors.RETRYABLE_CODES."""

    pattern = re.compile(r'\b(support_code|code)\s*=\s*"([a-z][a-z_]*)"')
    found: dict[str, set[str]] = {}
    for path in SRC.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        for match in pattern.finditer(text):
            found.setdefault(match.group(2), set()).add(path.name)

    missing = {code: files for code, files in found.items() if code not in RETRYABLE_CODES}
    assert not missing, f"unregistered error codes: {missing}"


def test_retryable_table_is_boolean():
    assert all(isinstance(value, bool) for value in RETRYABLE_CODES.values())
    assert is_retryable("timeout") is True
    assert is_retryable("auth_failed") is False
    assert is_retryable("definitely_not_a_code") is False


def test_upstream_set_is_fully_covered_by_aliases(config, registry):
    doc = contract_for(config, registry)
    emotion = doc["tags"]["emotion"]
    for name, values in ACCEPTED_UPSTREAM_SETS.items():
        assert emotion["accepted_upstream_sets"][name] == sorted(values)
        for label in values:
            assert emotion["aliases"][label] in emotion["canonical"]

    # The three OmniNexus bridges must be explicit aliases, not fallbacks.
    assert emotion["aliases"]["fear"] == "sadness"
    assert emotion["aliases"]["confusion"] == "frustration"
    assert emotion["aliases"]["gratitude"] == "joy"


def test_contract_hash_is_stable_and_ignores_copy_edits(config, registry):
    first = contract_for(config, registry)
    second = contract_for(config, registry)
    assert first["contract_hash"] == second["contract_hash"]
    assert first["contract_hash"].startswith("sha256:")
    assert len(first["contract_hash"]) == len("sha256:") + 64

    # Changing only a display label/tone must not change the interface hash.
    from emotional_tts.routing import load_router
    from emotional_tts.styles import load_stylebook

    styles = load_stylebook(config.styles_path)
    definition = styles.emotions["comfort"]
    original_tone = definition.tone
    try:
        definition.tone = original_tone + " (copy edit)"
        app = create_app(config=config, registry=registry)
        edited = build_contract(
            styles=styles,
            router=load_router(config.routes_path),
            config=config,
            app=app,
        )
    finally:
        definition.tone = original_tone
    assert edited["contract_hash"] == first["contract_hash"]


def test_contract_endpoint_and_header(config, registry):
    app = create_app(config=config, registry=registry)
    with TestClient(app) as client:
        response = client.get("/api/v1/contract")
        assert response.status_code == 200
        assert response.headers["x-moodvoice-contract-version"] == CONTRACT_VERSION
        body = response.json()
        assert body["contract_version"] == CONTRACT_VERSION
        assert body["contract_hash"].startswith("sha256:")
        assert body["output"]["required_fields"] == ["event_id", "text", "audio_ref", "style"]
        assert body["output"]["schema_version"] == "1.0"
        assert body["errors"]["codes"]["timeout"]["retryable"] is True
        assert body["errors"]["codes"]["invalid_event"]["retryable"] is False
        assert body["limits"] == {
            "max_input_chars": config.app.max_input_chars,
            "max_tts_chars": config.app.max_tts_chars,
        }

        health = client.get("/api/health").json()
        assert health["contract_version"] == body["contract_version"]
        assert health["contract_hash"] == body["contract_hash"]
        assert health["contract_url"] == "/api/v1/contract"

        # Every response carries the version so a consumer can detect drift
        # without parsing bodies.
        for path in ("/", "/api/health", "/api/v1/records"):
            assert client.get(path).headers["x-moodvoice-contract-version"] == (CONTRACT_VERSION)


def test_contract_has_no_secrets_or_host_details(config, registry):
    app = create_app(config=config, registry=registry)
    with TestClient(app) as client:
        raw = client.get("/api/v1/contract").text
    assert "api_key" not in raw.lower()
    assert "127.0.0.1" not in raw
    assert "sk-" not in raw


@pytest.mark.parametrize(
    ("status", "code", "retryable"),
    [
        (422, "invalid_event", False),
        (404, "audio_not_found", False),
        (404, "connection_not_found", False),
    ],
)
def test_error_envelope_carries_retryable(config, registry, status, code, retryable):
    app = create_app(config=config, registry=registry)
    with TestClient(app) as client:
        if code == "invalid_event":
            response = client.post("/api/v1/outputs", json={"content": ""})
        elif code == "audio_not_found":
            response = client.get("/api/v1/audio/nope.mp3")
        else:
            response = client.get("/api/v1/connections/api_doesnotexist")
    assert response.status_code == status
    body = response.json()
    assert body["error"]["code"] == code
    assert body["error"]["retryable"] is retryable


def test_connection_timeout_is_retryable(config, registry):
    from emotional_tts.errors import ConnectionFailure

    failure = ConnectionFailure(
        "等待首个音频分片超时。",
        code="timeout",
        stage="provider_ttfb",
        http_status=504,
    )
    assert failure.retryable is True
    assert failure.http_status == 504


def test_ws_error_frame_carries_retryable(config, registry):
    app = create_app(config=config, registry=registry)
    too_long = "字" * (config.app.max_input_chars + 1)
    event = {
        "event_id": str(uuid4()),
        "session_id": str(uuid4()),
        "content": too_long,
        "stream": True,
        "tags": {"emotion": "neutral", "type": "text", "intent": "reply"},
    }
    with TestClient(app) as client, client.websocket_connect("/ws/v1/outputs") as ws:
        ws.send_json({"type": "synthesize", "event": event})
        frame = ws.receive_json()
    assert frame["type"] == "error"
    assert frame["error"]["code"] == "content_too_long"
    assert frame["error"]["retryable"] is False


def test_contract_provider_caches_document(config, registry):
    app = create_app(config=config, registry=registry)
    from emotional_tts.routing import load_router
    from emotional_tts.styles import load_stylebook

    provider = ContractProvider(
        styles=load_stylebook(config.styles_path),
        router=load_router(config.routes_path),
        config=config,
        app=app,
    )
    assert provider.document() is provider.document()
    assert provider.version == CONTRACT_VERSION
    assert provider.contract_hash == provider.document()["contract_hash"]
    summary = provider.summary()
    assert summary["contract_url"] == "/api/v1/contract"
    assert json.dumps(summary)  # serialisable
