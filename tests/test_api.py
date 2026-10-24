from __future__ import annotations

import json
import sqlite3
from uuid import uuid4

from fastapi.testclient import TestClient

from emotional_tts.api import create_app


def app_for(config, registry):
    return create_app(config=config, registry=registry)


def payload(**overrides):
    data = {
        "event_id": str(uuid4()),
        "session_id": str(uuid4()),
        "content": "今天辛苦了，先慢慢呼吸一下。",
        "stream": True,
        "tags": {"emotion": "comfort", "type": "text", "intent": "reply"},
    }
    data.update(overrides)
    return data


def test_health_and_demo_page(config, registry):
    app = app_for(config, registry)
    with TestClient(app) as client:
        health = client.get("/api/health")
        assert health.status_code == 200
        body = health.json()
        assert body["auto_ready"] == "fake_a"
        assert body["privacy"]["content_logged"] is False
        assert "fake" in json.dumps(body["providers"])
        page = client.get("/")
        assert page.status_code == 200
        assert "MoodVoice" in page.text
        assert page.headers["content-security-policy"].startswith("default-src")
        assert client.get("/assets/app.js").status_code == 200


def test_rest_contract_and_audio_serving(config, registry):
    app = app_for(config, registry)
    with TestClient(app) as client:
        response = client.post("/api/v1/outputs?provider=fake_a", json=payload())
        assert response.status_code == 200
        assert response.headers["x-moodvoice-status"] == "synthesized"
        data = response.json()
        assert data["event_id"]
        assert data["text"].startswith("今天辛苦")
        assert data["audio_ref"].startswith("/api/v1/audio/")
        assert data["style"]["emotion"] == "comfort"
        assert data["style"]["tone"]
        assert data["route"]["destination"] == "ui.voice_reply"
        assert data["render"]["mode"] == "plain"
        assert data["diagnostics"]["emotion_mapping"]["mapping_hit"] is True

        audio = client.get(data["audio_ref"])
        assert audio.status_code == 200
        assert audio.headers["content-type"].startswith("audio/mpeg")
        assert audio.headers["cache-control"] == "no-store"
        assert audio.content == b"ID3fake-audio-1fake-audio-2"

        records = client.get("/api/v1/records").json()
        assert records["total"] == 1
        item = records["items"][0]
        assert item["source_emotion"] == "comfort"
        assert item["resolved_emotion"] == "comfort"
        assert item["tags"]["intent"] == "reply"
        assert "content" not in item
        assert "speech_text" not in item


def test_tool_intent_returns_text_only_contract(config, registry):
    app = app_for(config, registry)
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/outputs?provider=fake_a",
            json=payload(tags={"emotion": "joy", "type": "list", "intent": "tool"}),
        )
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "text_only"
        assert data["audio_ref"] is None
        assert data["degraded"] is False
        assert data["route"]["synthesize"] is False


def test_failed_provider_returns_plain_text_with_attempts(config, registry):
    app = app_for(config, registry)
    registry.get("fake_a").fail_at = 0
    with TestClient(app) as client:
        response = client.post("/api/v1/outputs?provider=fake_a", json=payload())
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "text_fallback"
        assert data["degraded"] is True
        assert data["audio_ref"] is None
        assert data["degradation"]["code"] == "tts_unavailable"
        assert data["diagnostics"]["provider_attempts"][0]["outcome"] == "failed"


def test_validation_errors_do_not_echo_input_content(config, registry):
    app = app_for(config, registry)
    with TestClient(app) as client:
        too_long = "秘密内容" * 2100  # > 8000 but < pydantic's 20000 guard
        response = client.post(
            "/api/v1/outputs",
            json=payload(content=too_long),
        )
        assert response.status_code == 422
        assert too_long not in response.text
        assert response.json()["error"]["code"] == "content_too_long"

        invalid = client.post(
            "/api/v1/outputs",
            json=payload(event_id="not-a-uuid", content="不要回显我"),
        )
        assert invalid.status_code == 422
        assert "不要回显我" not in invalid.text
        assert invalid.json()["error"]["code"] == "invalid_event"


def test_origin_guard_and_cache_clearing(config, registry):
    app = app_for(config, registry)
    with TestClient(app) as client:
        blocked = client.get("/api/health", headers={"Origin": "http://evil.example"})
        assert blocked.status_code == 403
        allowed = client.get("/api/health", headers={"Origin": "http://127.0.0.1:8765"})
        assert allowed.status_code == 200

        data = client.post("/api/v1/outputs?provider=fake_a", json=payload()).json()
        audio_ref = data["audio_ref"]
        cleared = client.delete("/api/v1/user-data")
        assert cleared.status_code == 200
        assert cleared.json()["deleted_files"] == 1
        assert cleared.json()["deleted_records"] == 1
        assert client.get(audio_ref).status_code == 404
        assert client.get("/api/v1/records").json()["total"] == 0


def test_websocket_streams_metadata_then_audio(config, registry):
    app = app_for(config, registry)
    with (
        TestClient(app) as client,
        client.websocket_connect("/ws/v1/outputs") as ws,
    ):
        ws.send_json(
            {
                "type": "synthesize",
                "event": payload(),
                "provider": "fake_a",
            }
        )
        preparing = ws.receive_json()
        assert preparing["type"] == "preparing"
        assert preparing["output"]["status"] == "preparing"

        start = ws.receive_json()
        assert start["type"] == "start"
        assert start["audio"]["mime"] == "audio/mpeg"
        assert start["output"]["provider"] == "fake_a"

        first = ws.receive_bytes()
        second = ws.receive_bytes()
        assert first + second == b"ID3fake-audio-1fake-audio-2"

        done = ws.receive_json()
        assert done["type"] == "done"
        assert done["output"]["status"] == "synthesized"
        assert done["output"]["audio_ref"]


def test_diagnostics_endpoint_explains_mapping(config, registry):
    app = app_for(config, registry)
    with TestClient(app) as client:
        result = client.get("/api/v1/diagnostics/style?emotion=安慰&provider=fake_a")
        assert result.status_code == 200
        body = result.json()
        assert body["resolved"]["emotion"] == "comfort"
        assert body["checks"][0]["name"] == "emotion_mapping_hit"
        assert body["checks"][0]["ok"] is True
        assert body["request_preview"]["effective"]
        assert "占位文本" not in json.dumps(body)  # no user text or placeholder echo


def test_records_do_not_store_content_at_rest(config, registry):
    app = app_for(config, registry)
    content = "这是一段绝不应该落盘的原文。"
    with TestClient(app) as client:
        client.post(
            "/api/v1/outputs?provider=fake_a",
            json=payload(content=content),
        )
    db_path = config.data_path / "records.sqlite3"
    with sqlite3.connect(db_path) as conn:
        columns = [row[1] for row in conn.execute("PRAGMA table_info(output_records)")]
        assert "content" not in columns
        assert "speech_text" not in columns
        values = conn.execute("SELECT * FROM output_records").fetchall()
        dumped = json.dumps(values, ensure_ascii=False, default=str)
        assert content not in dumped
