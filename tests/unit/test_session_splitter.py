"""Unit tests for session inactivity splitting."""

from testgen.normalisation.session_splitter import split_session_events


def _make_event(timestamp, sequence=1):
    return {
        "event_id": f"evt-{sequence}",
        "session_id": "session-1",
        "sequence": sequence,
        "timestamp": timestamp,
        "event_type": "click",
        "url": "/page",
    }


class TestSplitSessionEvents:
    def test_single_session_no_gap(self):
        events = [
            _make_event("2026-03-06T14:00:00Z", 1),
            _make_event("2026-03-06T14:05:00Z", 2),
            _make_event("2026-03-06T14:10:00Z", 3),
        ]
        result = split_session_events(events)
        assert len(result) == 1
        assert len(result[0]) == 3

    def test_idle_gap_splits(self):
        events = [
            _make_event("2026-03-06T14:00:00Z", 1),
            _make_event("2026-03-06T14:05:00Z", 2),
            # 35-minute gap
            _make_event("2026-03-06T14:40:00Z", 3),
            _make_event("2026-03-06T14:45:00Z", 4),
        ]
        result = split_session_events(events)
        assert len(result) == 2
        assert len(result[0]) == 2
        assert len(result[1]) == 2

    def test_multiple_splits(self):
        events = [
            _make_event("2026-03-06T10:00:00Z", 1),
            # 45-min gap
            _make_event("2026-03-06T10:45:00Z", 2),
            # 60-min gap
            _make_event("2026-03-06T11:45:00Z", 3),
        ]
        result = split_session_events(events)
        assert len(result) == 3

    def test_exactly_30_minutes_no_split(self):
        events = [
            _make_event("2026-03-06T14:00:00Z", 1),
            _make_event("2026-03-06T14:30:00Z", 2),
        ]
        result = split_session_events(events)
        assert len(result) == 1

    def test_empty_events(self):
        result = split_session_events([])
        assert result == []

    def test_single_event(self):
        events = [_make_event("2026-03-06T14:00:00Z", 1)]
        result = split_session_events(events)
        assert len(result) == 1
        assert len(result[0]) == 1

    def test_concurrent_tabs_sort_by_timestamp_with_overlapping_sequences(self):
        """Concurrent tab groups should sort by timestamp when sequences overlap."""
        events = [
            {"event_id": "e1", "session_id": "s1", "tab_id": "t1", "sequence": 1,
             "timestamp": "2026-03-06T14:00:00Z", "event_type": "click", "url": "/a"},
            {"event_id": "e2", "session_id": "s1", "tab_id": "t2", "sequence": 1,
             "timestamp": "2026-03-06T14:00:01Z", "event_type": "click", "url": "/b"},
            {"event_id": "e3", "session_id": "s1", "tab_id": "t1", "sequence": 2,
             "timestamp": "2026-03-06T14:00:02Z", "event_type": "click", "url": "/a"},
            {"event_id": "e4", "session_id": "s1", "tab_id": "t2", "sequence": 2,
             "timestamp": "2026-03-06T14:00:03Z", "event_type": "click", "url": "/b"},
        ]
        result = split_session_events(events)
        # Should detect concurrent tabs and split into 2 groups
        assert len(result) == 2
        # Each group should be sorted by timestamp (not just sequence)
        for group in result:
            timestamps = [e["timestamp"] for e in group]
            assert timestamps == sorted(timestamps)
