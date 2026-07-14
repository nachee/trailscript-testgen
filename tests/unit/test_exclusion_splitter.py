"""Unit tests for URL exclusion splitting."""

from testgen.normalisation.exclusion_splitter import split_by_exclusions


def _make_event(url, sequence=1):
    return {
        "event_id": f"evt-{sequence}",
        "session_id": "session-1",
        "sequence": sequence,
        "timestamp": "2026-03-06T14:00:00Z",
        "event_type": "click",
        "url": url,
    }


class TestSplitByExclusions:
    def test_no_exclusions_returns_all(self):
        events = [_make_event("/page", i) for i in range(5)]
        result = split_by_exclusions(events, [])
        assert len(result) == 1
        assert len(result[0]) == 5

    def test_exact_match_exclusion(self):
        events = [
            _make_event("/home", 1),
            _make_event("/home", 2),
            _make_event("/home", 3),
            _make_event("/admin", 4),  # excluded
            _make_event("/dashboard", 5),
            _make_event("/dashboard", 6),
            _make_event("/dashboard", 7),
        ]
        result = split_by_exclusions(events, ["/admin"])
        assert len(result) == 2
        assert len(result[0]) == 3
        assert len(result[1]) == 3

    def test_wildcard_exclusion(self):
        events = [
            _make_event("/home", 1),
            _make_event("/home", 2),
            _make_event("/home", 3),
            _make_event("/admin/users", 4),      # excluded
            _make_event("/admin/settings", 5),    # excluded
            _make_event("/dashboard", 6),
            _make_event("/dashboard", 7),
            _make_event("/dashboard", 8),
        ]
        result = split_by_exclusions(events, ["/admin/*"])
        assert len(result) == 2

    def test_drops_partial_flows(self):
        events = [
            _make_event("/home", 1),
            _make_event("/admin", 2),  # excluded
            _make_event("/page", 3),   # only 1 event after split — dropped
        ]
        result = split_by_exclusions(events, ["/admin"])
        # First segment has 1 event, second has 1 — both dropped (< 3)
        assert len(result) == 0

    def test_keeps_segments_with_3_plus_events(self):
        events = [
            _make_event("/a", 1),
            _make_event("/a", 2),
            _make_event("/a", 3),
            _make_event("/excluded", 4),
            _make_event("/b", 5),
            _make_event("/b", 6),
        ]
        result = split_by_exclusions(events, ["/excluded"])
        # First segment: 3 events (kept), second: 2 events (dropped)
        assert len(result) == 1
        assert len(result[0]) == 3

    def test_empty_events(self):
        result = split_by_exclusions([], ["/admin"])
        assert result == []

    def test_all_events_excluded(self):
        events = [
            _make_event("/admin/a", 1),
            _make_event("/admin/b", 2),
        ]
        result = split_by_exclusions(events, ["/admin/*"])
        assert len(result) == 0

    def test_multiple_exclusion_patterns(self):
        events = [
            _make_event("/home", 1),
            _make_event("/home", 2),
            _make_event("/home", 3),
            _make_event("/admin", 4),
            _make_event("/page", 5),
            _make_event("/page", 6),
            _make_event("/page", 7),
            _make_event("/internal", 8),
            _make_event("/end", 9),
        ]
        result = split_by_exclusions(events, ["/admin", "/internal"])
        # Segments: [home x3], [page x3], [end x1 — dropped]
        assert len(result) == 2
