from __future__ import annotations

from emotional_tts.monitoring import MappingMonitor


def test_mapping_monitor_is_bounded_and_never_stores_text():
    monitor = MappingMonitor(max_recent=3)
    for index in range(5):
        monitor.record(
            event_id=f"e{index}",
            source_tag=f"tag-{index}",
            normalized_tag=f"tag {index}",
            resolved_emotion="neutral",
            reason="unknown_tag",
        )
    snapshot = monitor.snapshot()
    assert snapshot["total"] == 5
    assert len(snapshot["recent"]) == 3
    assert [item["source_tag"] for item in snapshot["recent"]] == [
        "tag-4",
        "tag-3",
        "tag-2",
    ]
    assert "content" not in snapshot
