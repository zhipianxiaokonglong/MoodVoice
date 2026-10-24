"""Structured local records for later product versions.

Only tags, resolved style, route, provider, timings and outcome are stored.
The original content and speech script are intentionally absent, so deleting the
audio cache really removes the user's speech data from this module.
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
from pathlib import Path
from typing import Any

from .contracts import OutputEvent

SCHEMA = """
CREATE TABLE IF NOT EXISTS output_records (
    event_id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    tags_json TEXT NOT NULL,
    source_emotion TEXT NOT NULL,
    resolved_emotion TEXT NOT NULL,
    mapping_hit INTEGER NOT NULL,
    content_type TEXT NOT NULL,
    intent TEXT NOT NULL,
    route TEXT NOT NULL,
    requested_provider TEXT NOT NULL,
    provider TEXT,
    style_json TEXT NOT NULL,
    diagnostics_json TEXT NOT NULL,
    status TEXT NOT NULL,
    degraded INTEGER NOT NULL,
    degradation_code TEXT,
    first_chunk_ms REAL,
    total_ms REAL,
    audio_bytes INTEGER NOT NULL DEFAULT 0,
    audio_ref TEXT,
    error_type TEXT,
    error_message TEXT,
    speech_chars INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_output_records_created
    ON output_records(created_at);
CREATE INDEX IF NOT EXISTS idx_output_records_session
    ON output_records(session_id);
"""


class RecordStore:
    def __init__(self, db_path: str | Path) -> None:
        self.path = Path(db_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        _restrict_path(self.path.parent, 0o700)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(self.path), timeout=10, check_same_thread=False)
        _restrict_path(self.path, 0o600)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA synchronous=NORMAL")
            self._conn.executescript(SCHEMA)
            self._conn.commit()

    def save(
        self,
        output: OutputEvent,
        *,
        tags: dict[str, str],
        provider_params: dict[str, Any],
        audio_bytes: int = 0,
    ) -> None:
        """Persist structured metadata; never persist content or speech text."""

        style_json = json.dumps(
            {
                "style": output.style.model_dump(mode="json"),
                "provider_params": provider_params,
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )
        diagnostics = output.diagnostics.model_dump(mode="json")
        error_type = None
        error_message = None
        if output.degradation:
            error_type = output.degradation.code
            error_message = output.degradation.message
        values = (
            output.event_id,
            output.session_id,
            output.created_at,
            json.dumps(tags, ensure_ascii=False, separators=(",", ":")),
            output.style.source_tag,
            output.style.emotion,
            1 if output.style.mapping_hit else 0,
            output.render.type,
            output.route.intent,
            output.route.destination,
            output.requested_provider,
            output.provider,
            style_json,
            json.dumps(diagnostics, ensure_ascii=False, separators=(",", ":")),
            output.status,
            1 if output.degraded else 0,
            output.degradation.code if output.degradation else None,
            output.timings.first_chunk_ms,
            output.timings.total_ms,
            audio_bytes,
            output.audio_ref,
            error_type,
            error_message,
            len(output.speech_text),
        )
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO output_records (
                    event_id, session_id, created_at, tags_json,
                    source_emotion, resolved_emotion, mapping_hit,
                    content_type, intent, route, requested_provider, provider,
                    style_json, diagnostics_json, status, degraded,
                    degradation_code, first_chunk_ms, total_ms, audio_bytes,
                    audio_ref, error_type, error_message, speech_chars
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(event_id) DO UPDATE SET
                    created_at=excluded.created_at,
                    tags_json=excluded.tags_json,
                    resolved_emotion=excluded.resolved_emotion,
                    mapping_hit=excluded.mapping_hit,
                    provider=excluded.provider,
                    style_json=excluded.style_json,
                    diagnostics_json=excluded.diagnostics_json,
                    status=excluded.status,
                    degraded=excluded.degraded,
                    degradation_code=excluded.degradation_code,
                    first_chunk_ms=excluded.first_chunk_ms,
                    total_ms=excluded.total_ms,
                    audio_bytes=excluded.audio_bytes,
                    audio_ref=excluded.audio_ref,
                    error_type=excluded.error_type,
                    error_message=excluded.error_message,
                    speech_chars=excluded.speech_chars
                """,
                values,
            )
            self._conn.commit()

    def list(self, *, limit: int = 50, offset: int = 0) -> list[dict[str, Any]]:
        limit = max(1, min(int(limit), 200))
        offset = max(0, int(offset))
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM output_records ORDER BY created_at DESC LIMIT ? OFFSET ?",
                (limit, offset),
            ).fetchall()
        return [self._row_to_dict(row) for row in rows]

    def count(self) -> int:
        with self._lock:
            row = self._conn.execute("SELECT COUNT(*) AS n FROM output_records").fetchone()
        return int(row["n"])

    def clear(self) -> int:
        with self._lock:
            count = self.count()
            self._conn.execute("DELETE FROM output_records")
            self._conn.commit()
        return count

    def _row_to_dict(self, row: sqlite3.Row) -> dict[str, Any]:
        data = dict(row)
        for key in ("tags_json", "style_json", "diagnostics_json"):
            try:
                data[key.removesuffix("_json")] = json.loads(data.pop(key))
            except (json.JSONDecodeError, TypeError):
                data[key.removesuffix("_json")] = {}
        data["mapping_hit"] = bool(data["mapping_hit"])
        data["degraded"] = bool(data["degraded"])
        return data

    def close(self) -> None:
        with self._lock:
            self._conn.close()


def _restrict_path(path: Path, mode: int) -> None:
    try:
        os.chmod(path, mode)
    except OSError:
        pass
