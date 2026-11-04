from __future__ import annotations

import json

import httpx
import pytest
from fastapi.testclient import TestClient

from emotional_tts.api import create_app
from emotional_tts.connections.discovery import discover
from emotional_tts.connections.models import DiscoverRequest
from emotional_tts.connections.security import validate_base_url
from emotional_tts.connections.store import MemoryKeyStore
from emotional_tts.errors import ConnectionFailure


def _client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


@pytest.fixture
def manager(config, styles, registry, cache):
    from emotional_tts.connections.manager import ConnectionManager

    def factory():
        return _client(lambda request: httpx.Response(404, json={"error": "test client"}))

    return ConnectionManager(
        config,
        styles=styles,
        cache=cache,
        registry=registry,
        key_store=MemoryKeyStore(),
        client_factory=factory,
    )


async def test_openai_discovery_lists_models_and_voices():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/models"):
            return httpx.Response(
                200,
                json={
                    "data": [
                        {"id": "tts-1"},
                        {"id": "gpt-4o-mini-tts"},
                        {"id": "gpt-4.1"},
                    ]
                },
            )
        return httpx.Response(404)

    async with _client(handler) as client:
        result = await discover(
            client,
            base_url="https://api.openai.com/v1",
            protocol="openai",
            api_key="test-key",
        )
    assert result.protocol == "openai"
    assert [m.id for m in result.models] == ["gpt-4o-mini-tts", "tts-1", "gpt-4.1"]
    assert any(voice.id == "coral" for voice in result.voices)
    assert result.capabilities["model_discovery"] is True


async def test_azure_discovery_parses_styles_and_locale():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["Ocp-Apim-Subscription-Key"] == "azure-key"
        return httpx.Response(
            200,
            json=[
                {
                    "ShortName": "zh-CN-XiaoxiaoNeural",
                    "LocalName": "晓晓",
                    "Gender": "Female",
                    "Locale": "zh-CN",
                    "StyleList": ["cheerful", "gentle", "sad"],
                }
            ],
        )

    async with _client(handler) as client:
        result = await discover(
            client,
            base_url="https://eastasia.tts.speech.microsoft.com/cognitiveservices/v1",
            protocol="azure",
            api_key="azure-key",
        )
    assert result.voices[0].id == "zh-CN-XiaoxiaoNeural"
    assert result.voices[0].locale == "zh-CN"
    assert "gentle" in result.voices[0].styles
    assert result.capabilities["voice_discovery"] is True


async def test_elevenlabs_discovery_parses_models_and_voices():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/models"):
            return httpx.Response(200, json={"models": [{"model_id": "eleven_v3", "name": "v3"}]})
        if request.url.path.endswith("/voices"):
            return httpx.Response(
                200,
                json={
                    "voices": [
                        {
                            "voice_id": "voice-1",
                            "name": "Alice",
                            "labels": {"language": "en", "gender": "female"},
                        }
                    ]
                },
            )
        return httpx.Response(404)

    async with _client(handler) as client:
        result = await discover(
            client,
            base_url="https://api.elevenlabs.io/v1",
            protocol="elevenlabs",
            api_key="xi-key",
        )
    assert result.models[0].id == "eleven_v3"
    assert result.voices[0].id == "voice-1"
    assert result.voices[0].gender == "female"


async def test_minimax_discovery_parses_voices():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert json.loads(request.content)["voice_type"] == "all"
        return httpx.Response(
            200,
            json={
                "system_voice": [{"voice_id": "female-shaonv", "voice_name": "少女"}],
                "base_resp": {"status_code": 0},
            },
        )

    async with _client(handler) as client:
        result = await discover(
            client,
            base_url="https://api.minimax.io/v1/t2a_v2",
            protocol="minimax",
            api_key="mm-key",
        )
    assert result.voices[0].id == "female-shaonv"
    assert result.capabilities["emotional_control"] == "native_emotion"


async def test_auto_discovery_reports_attempts():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"error": "not found"})

    async with _client(handler) as client:
        with pytest.raises(ConnectionFailure) as info:
            from emotional_tts.connections.discovery import auto_discover

            await auto_discover(client, base_url="https://example.test/v1", api_key="key")
    assert info.value.code == "protocol_detection_failed"
    assert len(info.value.details["attempts"]) == 4


async def test_discovery_auth_failure_is_clean():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": "bad key"})

    async with _client(handler) as client:
        with pytest.raises(ConnectionFailure) as info:
            await discover(
                client,
                base_url="https://api.openai.test/v1",
                protocol="openai",
                api_key="bad",
            )
    assert info.value.code == "auth_failed"
    assert info.value.http_status == 401


async def test_validate_url_blocks_remote_http_and_metadata():
    with pytest.raises(ConnectionFailure) as info:
        validate_base_url("http://api.example.com/v1")
    assert info.value.code == "insecure_url"

    with pytest.raises(ConnectionFailure) as info:
        validate_base_url("http://169.254.169.254/latest/meta-data")
    assert info.value.code == "blocked_url"

    with pytest.raises(ConnectionFailure) as info:
        validate_base_url("https://user:pass@api.example.com/v1")
    assert info.value.code == "invalid_url"

    with pytest.raises(ConnectionFailure) as info:
        validate_base_url("https://api.example.com:not-a-port/v1")
    assert info.value.code == "invalid_url"

    with pytest.raises(ConnectionFailure) as info:
        validate_base_url("https://api.example.com/v1?api_key=leak")
    assert info.value.code == "invalid_url"

    with pytest.raises(ConnectionFailure) as info:
        validate_base_url("https://api.example.com/v1#frag")
    assert info.value.code == "invalid_url"

    assert validate_base_url("http://127.0.0.1:9880/synthesize").endswith("/synthesize")
    assert validate_base_url("https://api.example.com/v1").startswith("https://")


async def test_validate_url_requires_opt_in_for_private_networks():
    with pytest.raises(ConnectionFailure) as info:
        validate_base_url("https://192.168.1.10:8443/v1")
    assert info.value.code == "blocked_private_network"

    with pytest.raises(ConnectionFailure) as info:
        validate_base_url("http://192.168.1.10:8080/v1")
    assert info.value.code == "blocked_private_network"

    # Explicit opt-in unlocks LAN targets (HTTP and HTTPS alike).
    assert validate_base_url("http://192.168.1.10:8080/v1", allow_insecure_http=True) == (
        "http://192.168.1.10:8080/v1"
    )
    assert validate_base_url("https://192.168.1.10:8443/v1", allow_insecure_http=True) == (
        "https://192.168.1.10:8443/v1"
    )
    # Plain HTTP to a public host stays rejected even with the LAN opt-in.
    with pytest.raises(ConnectionFailure) as info:
        validate_base_url("http://api.example.com/v1", allow_insecure_http=True)
    assert info.value.code == "insecure_url"


def test_corrupt_connection_store_is_quarantined(tmp_path):
    from emotional_tts.connections.store import ConnectionStore

    path = tmp_path / "connections.json"
    path.write_text("{not json", encoding="utf-8")
    store = ConnectionStore(path)
    assert store.list() == []
    assert not path.exists()
    assert list(tmp_path.glob("connections.json.corrupt-*"))


async def test_connection_manager_saves_without_plaintext_key(manager, config, cache):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/models"):
            return httpx.Response(200, json={"data": [{"id": "tts-1"}]})
        return httpx.Response(404)

    from emotional_tts.connections.manager import ConnectionManager
    from emotional_tts.connections.models import SaveConnectionRequest

    manager.client_factory = lambda: _client(handler)
    result = await manager.discover(
        DiscoverRequest(
            base_url="https://api.openai.test/v1",
            api_key="super-secret",
            protocol="openai",
        )
    )
    assert result.ok

    manager2 = ConnectionManager(
        config,
        styles=manager.styles,
        cache=cache,
        registry=manager.registry,
        key_store=MemoryKeyStore(),
        client_factory=lambda: _client(handler),
    )
    record = await manager2.save(
        SaveConnectionRequest(
            label="test",
            base_url="https://api.openai.test/v1",
            api_key="super-secret",
            protocol="openai",
            model="tts-1",
            voice="coral",
            allow_insecure_http=False,
        )
    )
    raw = (config.data_path / "connections.json").read_text(encoding="utf-8")
    assert "super-secret" not in raw
    assert record.id.startswith("api_")
    assert manager2.key_store.get(record.id) == "super-secret"
    public = manager2.public_connections()[0]
    assert public["key_configured"] is True
    assert "super-secret" not in json.dumps(public)


async def test_connection_is_registered_as_provider(manager, config, cache):
    from emotional_tts.connections.models import SaveConnectionRequest

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"ID3", headers={"content-type": "audio/mpeg"})

    manager.client_factory = lambda: _client(handler)
    record = await manager.save(
        SaveConnectionRequest(
            label="local",
            base_url="http://127.0.0.1:9999/v1",
            api_key=None,
            protocol="openai",
            model="tts-1",
            voice="coral",
        )
    )
    provider = manager.registry.get(record.id)
    assert provider is not None
    assert provider.style_family == "openai"
    assert provider.external_egress() is False
    # The egress label must name the user's connection, not the protocol's default vendor.
    assert provider.capabilities.data_destination.startswith("local")
    assert "OpenAI API" not in provider.capabilities.data_destination
    assert provider.capabilities.external_egress is False


def test_api_connection_endpoints(config, registry, tmp_path):
    from emotional_tts.cache import AudioCache
    from emotional_tts.connections.store import MemoryKeyStore
    from emotional_tts.styles import load_stylebook

    styles = load_stylebook(config.styles_path)
    cache = AudioCache(config.data_path / "cache")

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/models"):
            return httpx.Response(200, json={"data": [{"id": "tts-1"}]})
        if request.url.path.endswith("/audio/speech"):
            return httpx.Response(200, content=b"ID3audio", headers={"content-type": "audio/mpeg"})
        return httpx.Response(404)

    from emotional_tts.connections.manager import ConnectionManager

    manager = ConnectionManager(
        config,
        styles=styles,
        cache=cache,
        registry=registry,
        key_store=MemoryKeyStore(),
        client_factory=lambda: _client(handler),
    )
    app = create_app(
        config=config,
        registry=registry,
        cache=cache,
        connection_manager=manager,
    )
    with TestClient(app) as client:
        discover = client.post(
            "/api/v1/connections/discover",
            json={
                "base_url": "https://api.openai.test/v1",
                "api_key": "secret",
                "protocol": "auto",
            },
        )
        assert discover.status_code == 200, discover.text
        assert discover.json()["protocol"] == "openai"

        test = client.post(
            "/api/v1/connections/test",
            json={
                "base_url": "https://api.openai.test/v1",
                "api_key": "secret",
                "protocol": "openai",
                "model": "tts-1",
                "voice": "coral",
            },
        )
        assert test.status_code == 200, test.text
        assert test.json()["audio_ref"].startswith("/api/v1/audio/")

        saved = client.post(
            "/api/v1/connections",
            json={
                "label": "test",
                "base_url": "https://api.openai.test/v1",
                "api_key": "secret",
                "protocol": "openai",
                "model": "tts-1",
                "voice": "coral",
            },
        )
        assert saved.status_code == 200, saved.text
        assert saved.json()["key_configured"] is True
        assert "secret" not in saved.text


async def test_saved_connection_synthesizes_through_pipeline(manager, config, cache):
    from uuid import uuid4

    from emotional_tts.connections.models import SaveConnectionRequest
    from emotional_tts.contracts import InputEvent, InputTags

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/audio/speech"):
            return httpx.Response(
                200, content=b"ID3connection", headers={"content-type": "audio/mpeg"}
            )
        return httpx.Response(404)

    manager.client_factory = lambda: _client(handler)
    record = await manager.save(
        SaveConnectionRequest(
            label="test api",
            base_url="https://api.openai.test/v1",
            api_key="secret",
            protocol="openai",
            model="tts-1",
            voice="coral",
        )
    )
    from emotional_tts.pipeline import OutputPipeline
    from emotional_tts.routing import load_router
    from emotional_tts.storage import RecordStore

    records = RecordStore(config.data_path / "pipeline.sqlite3")
    pipeline = OutputPipeline(
        config,
        styles=manager.styles,
        router=load_router(config.routes_path),
        registry=manager.registry,
        cache=cache,
        records=records,
    )
    try:
        output = await pipeline.process(
            InputEvent(
                event_id=uuid4(),
                session_id="",
                content="测试连接合成。",
                tags=InputTags(emotion="comfort", type="text", intent="reply"),
                provider=record.id,
            )
        )
    finally:
        records.close()
    assert output.status == "synthesized"
    assert output.provider == record.id
    assert output.style.emotion == "comfort"
    assert output.diagnostics.adapter["request"]["connection"]["protocol"] == "openai"
    assert output.privacy.external_egress is True


async def test_connections_test_does_not_write_records(manager, config):
    from emotional_tts.connections.models import TestConnectionRequest

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"ID3test", headers={"content-type": "audio/mpeg"})

    manager.client_factory = lambda: _client(handler)
    result = await manager.test(
        TestConnectionRequest(
            base_url="https://api.openai.test/v1",
            api_key="secret",
            protocol="openai",
            model="tts-1",
            voice="coral",
        )
    )
    assert result.audio_ref
    assert not (config.data_path / "records.sqlite3").exists()


async def test_validate_url_blocks_private_network_and_rebinding():
    from unittest.mock import patch

    with pytest.raises(ConnectionFailure) as info:
        validate_base_url("http://192.168.1.20:9880/synthesize")
    assert info.value.code == "blocked_private_network"

    assert validate_base_url(
        "http://192.168.1.20:9880/synthesize", allow_insecure_http=True
    ).startswith("http://192.168.1.20")

    with patch("emotional_tts.connections.security.socket.getaddrinfo") as resolver:
        resolver.return_value = [(2, 1, 6, "", ("127.0.0.1", 443))]
        with pytest.raises(ConnectionFailure) as info:
            validate_base_url("https://api.example.test/v1")
        assert info.value.code == "blocked_loopback_resolution"


async def test_reload_picks_up_external_store_edits(manager):
    from emotional_tts.connections.models import SaveConnectionRequest

    manager.client_factory = lambda: _client(
        lambda request: httpx.Response(200, content=b"ID3", headers={"content-type": "audio/mpeg"})
    )
    record = await manager.save(
        SaveConnectionRequest(
            label="reload me",
            base_url="https://api.openai.test/v1",
            api_key="secret",
            protocol="openai",
            model="tts-1",
            voice="coral",
        )
    )
    assert manager.registry.get(record.id) is not None

    # Simulate an external edit: the file loses the record while the process
    # still holds the provider.
    manager.store.delete(record.id)
    assert manager.registry.get(record.id) is not None

    count = await manager.reload()
    assert count == 0
    assert manager.registry.get(record.id) is None
