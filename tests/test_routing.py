from __future__ import annotations

from emotional_tts.routing import load_router


def test_reply_routes_to_voice(config):
    router = load_router(config.routes_path)
    route = router.route("reply")
    assert route.destination == "ui.voice_reply"
    assert route.synthesize is True
    assert router.is_known("reply")


def test_tool_and_linkage_do_not_speak(config):
    router = load_router(config.routes_path)
    assert router.route("tool").synthesize is False
    assert router.route("tool").destination == "ui.tool_result"
    assert router.route("linkage").synthesize is False
    assert router.route("linkage").destination == "integration.linkage"


def test_unknown_intent_gets_a_text_only_route(config):
    router = load_router(config.routes_path)
    route = router.route("made-up-intent")
    assert route.synthesize is False
    assert route.destination == router.model.unknown_intent
    assert "unknown intent" in route.reason
