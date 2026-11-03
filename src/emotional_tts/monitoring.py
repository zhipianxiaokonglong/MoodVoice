"""Small, bounded in-process telemetry for emotion tag drift.

No user text is stored here. The goal is to make an upstream label change
visible in logs and ``/api/health`` without adding a database table.
"""

from __future__ import annotations

import threading
from collections import Counter, deque
from datetime import UTC, datetime
from typing import Any


class MappingMonitor:
    def __init__(self, max_recent: int = 32) -> None:
        self._lock = threading.RLock()
        self._recent: deque[dict[str, Any]] = deque(maxlen=max_recent)
        self._counts: Counter[str] = Counter()
        self._total = 0

    def record(
        self,
        *,
        event_id: str,
        source_tag: str,
        normalized_tag: str,
        resolved_emotion: str,
        reason: str | None,
    ) -> None:
        record = {
            "event_id": event_id,
            "source_tag": source_tag,
            "normalized_tag": normalized_tag,
            "resolved_emotion": resolved_emotion,
            "reason": reason or "unknown_tag",
            "at": datetime.now(UTC).isoformat(),
        }
        tag_key = source_tag or "<missing>"
        with self._lock:
            self._total += 1
            if len(self._counts) >= 100 and tag_key not in self._counts:
                tag_key = "<other>"
            self._counts[tag_key] += 1
            self._recent.appendleft(record)

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "total": self._total,
                "recent": list(self._recent),
                "by_tag": [
                    {"tag": tag, "count": count} for tag, count in self._counts.most_common(20)
                ],
            }
