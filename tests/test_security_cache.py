from __future__ import annotations

import io
import logging

import pytest

from emotional_tts.cache import AudioCache
from emotional_tts.errors import CacheError
from emotional_tts.security import (
    RedactingFormatter,
    redact,
    register_secrets,
    sanitize_message,
)


def test_redaction_removes_registered_secrets_and_patterns():
    register_secrets(["super-secret-value"])
    fake_openai = "sk-" + "unit-test-only-12345"
    fake_bearer = "unit-test-bearer-12345"
    text = f"key=super-secret-value Bearer {fake_bearer} {fake_openai}?api_key=very-secret&x=1"
    cleaned = redact(text)
    assert "super-secret-value" not in cleaned
    assert fake_bearer not in cleaned
    assert fake_openai not in cleaned
    assert "very-secret" not in cleaned
    assert "***" in cleaned


def test_error_messages_are_single_line_and_bounded():
    cleaned = sanitize_message("line one\nline two " + "x" * 1000, limit=40)
    assert "\n" not in cleaned
    assert len(cleaned) <= 40


def test_logging_formatter_redacts_output():
    register_secrets(["log-secret"])
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(RedactingFormatter("%(message)s"))
    logger = logging.getLogger("test.security")
    logger.handlers = [handler]
    logger.setLevel(logging.INFO)
    logger.propagate = False
    logger.info("token is log-secret")
    assert "log-secret" not in stream.getvalue()
    assert "***" in stream.getvalue()

    formatter = RedactingFormatter("%(message)s")
    record = logging.LogRecord("x", logging.INFO, __file__, 1, "log-secret", (), None)
    assert "log-secret" not in formatter.format(record)


def test_cache_rejects_path_traversal(tmp_path):
    cache = AudioCache(tmp_path / "cache")
    with pytest.raises(CacheError):
        cache.resolve("../records.sqlite3")
    with pytest.raises(CacheError):
        cache.resolve("not-a-token.mp3")


def test_cache_writer_abort_removes_partial_file(tmp_path):
    cache = AudioCache(tmp_path / "cache")
    writer = cache.lease("mp3")
    writer.append(b"partial")
    writer.abort()
    assert cache.stats()["files"] == 0


def test_cache_prunes_to_max_files(tmp_path):
    cache = AudioCache(tmp_path / "cache", max_files=2, max_age_hours=24)
    for index in range(4):
        writer = cache.lease("mp3")
        writer.append(f"audio-{index}".encode())
        writer.finalize()
    assert cache.stats()["files"] == 2
