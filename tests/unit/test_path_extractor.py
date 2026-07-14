"""Unit tests for path extraction."""

import networkx as nx

from testgen.graph.path_extractor import extract_popular_paths, extract_popular_paths_from_sessions


def _build_test_graph():
    """Build a simple test graph: home → login → dashboard → settings."""
    g = nx.DiGraph()
    g.add_node("nav:/", count=10, url="/", event_type="navigation")
    g.add_node("nav:/login", count=8, url="/login", event_type="navigation")
    g.add_node("nav:/dashboard", count=7, url="/dashboard", event_type="navigation")
    g.add_node("nav:/settings", count=3, url="/settings", event_type="navigation")

    g.add_edge("nav:/", "nav:/login", weight=8)
    g.add_edge("nav:/login", "nav:/dashboard", weight=7)
    g.add_edge("nav:/dashboard", "nav:/settings", weight=3)

    return g


class TestExtractPopularPaths:
    def test_extracts_paths_above_threshold(self):
        graph = _build_test_graph()
        flows = extract_popular_paths(graph, total_sessions=10, threshold_percent=5.0)
        assert len(flows) > 0
        for flow in flows:
            assert flow["popularity_score"] >= 5.0

    def test_filters_below_threshold(self):
        graph = _build_test_graph()
        # High threshold should filter out most paths
        flows = extract_popular_paths(graph, total_sessions=100, threshold_percent=50.0)
        # With only 8 max weight and 100 sessions, nothing should be above 50%
        assert len(flows) == 0

    def test_popularity_score_calculation(self):
        graph = _build_test_graph()
        flows = extract_popular_paths(graph, total_sessions=10, threshold_percent=1.0)
        for flow in flows:
            assert 0 < flow["popularity_score"] <= 100
            assert isinstance(flow["canonical_pattern"], str)
            assert isinstance(flow["significant_actions"], list)

    def test_empty_graph(self):
        graph = nx.DiGraph()
        flows = extract_popular_paths(graph, total_sessions=10)
        assert flows == []

    def test_zero_sessions(self):
        graph = _build_test_graph()
        flows = extract_popular_paths(graph, total_sessions=0)
        assert flows == []

    def test_sorted_by_popularity(self):
        graph = _build_test_graph()
        flows = extract_popular_paths(graph, total_sessions=10, threshold_percent=1.0)
        if len(flows) > 1:
            scores = [f["popularity_score"] for f in flows]
            assert scores == sorted(scores, reverse=True)


def _make_event(event_type, url, target=None, payload=None, sequence=1):
    return {
        "event_id": f"evt-{sequence}",
        "session_id": "session-1",
        "tab_id": "tab-1",
        "sequence": sequence,
        "timestamp": "2026-03-06T14:00:00.000Z",
        "event_type": event_type,
        "url": url,
        "target": target,
        "payload": payload or {},
    }


def _make_session(pages):
    """Build a sub-session from a list of page URLs (nav events)."""
    events = []
    for i, page in enumerate(pages):
        events.append(_make_event("navigation", pages[i - 1] if i > 0 else "/",
                                  payload={"to_url": page}, sequence=i + 1))
    return events


class TestExtractPopularPathsFromSessions:
    def test_groups_identical_sessions(self):
        """3 identical sessions should give 100% popularity."""
        session = _make_session(["/", "/login", "/dashboard"])
        flows = extract_popular_paths_from_sessions(
            [session, session, session], total_sessions=3, threshold_percent=5.0,
        )
        assert len(flows) >= 1
        assert flows[0]["popularity_score"] == 100.0
        assert flows[0]["session_count"] == 3

    def test_filters_below_threshold(self):
        """1 out of 100 sessions = 1%, below 5% threshold."""
        session = _make_session(["/", "/login", "/dashboard"])
        flows = extract_popular_paths_from_sessions(
            [session], total_sessions=100, threshold_percent=5.0,
        )
        assert len(flows) == 0

    def test_multiple_patterns(self):
        """Different sessions produce different patterns."""
        session_a = _make_session(["/", "/login", "/dashboard"])
        session_b = _make_session(["/", "/settings", "/profile"])
        flows = extract_popular_paths_from_sessions(
            [session_a, session_a, session_b, session_b],
            total_sessions=4, threshold_percent=5.0,
        )
        assert len(flows) == 2
        for flow in flows:
            assert flow["popularity_score"] == 50.0
            assert flow["session_count"] == 2

    def test_empty_input(self):
        assert extract_popular_paths_from_sessions([], total_sessions=10) == []

    def test_zero_sessions(self):
        session = _make_session(["/", "/login", "/dashboard"])
        assert extract_popular_paths_from_sessions([session], total_sessions=0) == []

    def test_short_sessions_excluded(self):
        """Sessions with fewer than 3 meaningful nodes are skipped."""
        short_session = _make_session(["/", "/login"])
        flows = extract_popular_paths_from_sessions(
            [short_session], total_sessions=1, threshold_percent=1.0,
        )
        assert len(flows) == 0

    def test_sorted_by_popularity(self):
        session_a = _make_session(["/", "/login", "/dashboard"])
        session_b = _make_session(["/", "/settings", "/profile"])
        # 3 of A, 1 of B
        flows = extract_popular_paths_from_sessions(
            [session_a, session_a, session_a, session_b],
            total_sessions=4, threshold_percent=1.0,
        )
        if len(flows) > 1:
            scores = [f["popularity_score"] for f in flows]
            assert scores == sorted(scores, reverse=True)

    def test_flow_has_required_fields(self):
        session = _make_session(["/", "/login", "/dashboard"])
        flows = extract_popular_paths_from_sessions(
            [session, session], total_sessions=2, threshold_percent=1.0,
        )
        assert len(flows) >= 1
        flow = flows[0]
        assert "canonical_pattern" in flow
        assert "significant_actions" in flow
        assert "popularity_score" in flow
        assert "session_count" in flow
        assert "path_nodes" in flow

    def test_full_url_actions_extracted_correctly(self):
        """Actions from sessions with full URLs should have correct url and type."""
        session = [
            _make_event("navigation", "http://localhost:8080/",
                        payload={"to_url": "http://localhost:8080/login"}, sequence=1),
            _make_event("click", "http://localhost:8080/login", target={
                "selectors": {"role": {"role": "button", "name": "Submit"}},
                "tag": "BUTTON",
            }, sequence=2),
            _make_event("navigation", "http://localhost:8080/login",
                        payload={"to_url": "http://localhost:8080/dashboard"}, sequence=3),
        ]
        flows = extract_popular_paths_from_sessions(
            [session, session], total_sessions=2, threshold_percent=1.0,
        )
        assert len(flows) >= 1
        actions = flows[0]["significant_actions"]
        # Should contain navigation and click actions
        types = [a["type"] for a in actions]
        assert "navigation" in types
        assert "click" in types
        # Navigation URL should be the full URL, not just "http"
        nav_actions = [a for a in actions if a["type"] == "navigation"]
        for nav in nav_actions:
            assert nav["url"].startswith("http://localhost:8080/")
        # Click action should have the element
        click_actions = [a for a in actions if a["type"] == "click"]
        assert len(click_actions) >= 1
        assert "getByRole" in click_actions[0].get("element", "")

    def test_representative_events_preserved(self):
        """Flow dict should include the original events from the representative sub-session."""
        session = _make_session(["/", "/login", "/dashboard"])
        flows = extract_popular_paths_from_sessions(
            [session, session], total_sessions=2, threshold_percent=1.0,
        )
        assert len(flows) >= 1
        flow = flows[0]
        assert "representative_events" in flow
        events = flow["representative_events"]
        assert isinstance(events, list)
        assert len(events) > 0
        # Events should be full event dicts with all original fields
        assert "event_type" in events[0]
        assert "payload" in events[0]
        assert "url" in events[0]

    def test_representative_prefers_more_actions(self):
        """Sessions with different interactions on the same URL pattern become separate flows."""
        # Session A: 3 navigations, 0 actions = 3 path nodes (all nav)
        session_a = _make_session(["/", "/login", "/dashboard"])
        # Session B: 3 navigations + 1 click = 4 path nodes (1 action)
        session_b = list(session_a) + [
            _make_event("click", "/dashboard", target={
                "selectors": {"role": {"role": "button", "name": "Save"}},
                "tag": "BUTTON",
            }, sequence=4),
        ]
        flows = extract_popular_paths_from_sessions(
            [session_a, session_b], total_sessions=2, threshold_percent=1.0,
        )
        # With interaction sub-grouping, sessions A and B have different
        # fingerprints and become separate flows.
        assert len(flows) == 2
        all_event_types = []
        for flow in flows:
            all_event_types.extend(e["event_type"] for e in flow["representative_events"])
        assert "click" in all_event_types, "One flow should include the click action"


def _make_event_ts(event_type, url, timestamp, sequence=1, target=None, payload=None, page_context=None):
    """Build an event dict with explicit timestamp for ordering tests."""
    e = {
        "event_id": f"evt-{timestamp}-{sequence}",
        "session_id": "session-1",
        "tab_id": f"tab-{timestamp[:16]}",
        "sequence": sequence,
        "timestamp": timestamp,
        "event_type": event_type,
        "url": url,
        "target": target,
        "payload": payload or {},
    }
    if page_context is not None:
        e["page_context"] = page_context
    return e


class TestMultiPageOverlappingSequences:
    """Tests for multi-page flows where sequence counters reset per page."""

    def _build_three_page_session(self):
        """Simulate a 3-page flow with overlapping sequences (the bug scenario).

        Page 1 (/): seq 1-2, timestamps T+0..T+1
        Page 2 (/about): seq 1-2, timestamps T+2..T+3  (RESET!)
        Page 3 (/contact): seq 1-2, timestamps T+4..T+5 (RESET!)

        When sorted by (timestamp, sequence) the order is correct.
        When sorted by sequence alone, events interleave.
        """
        return [
            _make_event_ts("navigation", "http://site.com/",
                           "2026-03-06T14:00:00.000Z", sequence=1,
                           payload={"to_url": "http://site.com/"}),
            _make_event_ts("click", "http://site.com/",
                           "2026-03-06T14:00:01.000Z", sequence=2,
                           target={"selectors": {"css": "a.nav-about"}, "tag": "A",
                                   "attributes": {"href": "/about"}}),
            _make_event_ts("navigation", "http://site.com/",
                           "2026-03-06T14:00:02.000Z", sequence=1,
                           payload={"to_url": "http://site.com/about"}),
            _make_event_ts("click", "http://site.com/about",
                           "2026-03-06T14:00:03.000Z", sequence=2,
                           target={"selectors": {"css": "a.nav-contact"}, "tag": "A",
                                   "attributes": {"href": "/contact"}}),
            _make_event_ts("navigation", "http://site.com/about",
                           "2026-03-06T14:00:04.000Z", sequence=1,
                           payload={"to_url": "http://site.com/contact"}),
            _make_event_ts("click", "http://site.com/contact",
                           "2026-03-06T14:00:05.000Z", sequence=2,
                           target={"selectors": {"role": {"role": "button", "name": "Send"}},
                                   "tag": "BUTTON"}),
        ]

    def test_timestamp_ordered_events_produce_correct_pattern(self):
        """Events sorted by (timestamp, sequence) should produce a clean 3-page flow."""
        session = self._build_three_page_session()
        # Sort as the fixed analysis query would
        session.sort(key=lambda e: (e["timestamp"], e["sequence"]))

        flows = extract_popular_paths_from_sessions(
            [session, session], total_sessions=2, threshold_percent=1.0,
        )
        assert len(flows) >= 1
        pattern = flows[0]["canonical_pattern"]
        # Should contain 3 pages in order, not an interleaved mess
        assert "http://site.com/" in pattern
        assert "http://site.com/about" in pattern
        assert "http://site.com/contact" in pattern
        # Pattern should show pages in correct order: / → /about → /contact
        parts = pattern.split(" → ")
        url_indices = {}
        for i, p in enumerate(parts):
            if "/contact" in p:
                url_indices["contact"] = i
            elif "/about" in p:
                url_indices["about"] = i
            elif p.endswith("site.com/") or p == "http://site.com/":
                url_indices["home"] = i
        assert url_indices["home"] < url_indices["about"] < url_indices["contact"]

    def test_sequence_only_ordering_produces_wrong_pattern(self):
        """Demonstrates the bug: sorting by sequence alone interleaves pages."""
        session = self._build_three_page_session()
        # Sort as the broken query would — by sequence only
        session.sort(key=lambda e: e["sequence"])

        flows = extract_popular_paths_from_sessions(
            [session, session], total_sessions=2, threshold_percent=1.0,
        )
        # With interleaved events, the URL pattern should bounce between pages
        if flows:
            pattern = flows[0]["canonical_pattern"]
            parts = pattern.split(" → ")
            # Interleaved ordering produces more URL transitions than the clean 3-page path
            assert len(parts) > 3, (
                "Sequence-only ordering should produce an inflated path "
                f"but got {len(parts)} segments: {pattern}"
            )

    def test_mobile_viewport_multi_page_correct_ordering(self):
        """Mobile sessions across pages should produce [mobile]-prefixed flows."""
        mobile_ctx = {"viewport": {"width": 375, "height": 812}}

        session = [
            _make_event_ts("navigation", "http://m.site.com/",
                           "2026-03-06T14:00:00.000Z", sequence=1,
                           payload={"to_url": "http://m.site.com/"},
                           page_context=mobile_ctx),
            _make_event_ts("click", "http://m.site.com/",
                           "2026-03-06T14:00:01.000Z", sequence=2,
                           target={"selectors": {"css": "button.hamburger"}, "tag": "BUTTON"},
                           page_context=mobile_ctx),
            _make_event_ts("click", "http://m.site.com/",
                           "2026-03-06T14:00:02.000Z", sequence=3,
                           target={"selectors": {"css": "a.nav-about"}, "tag": "A",
                                   "attributes": {"href": "/about"}},
                           page_context=mobile_ctx),
            # Page 2 — sequence resets
            _make_event_ts("navigation", "http://m.site.com/",
                           "2026-03-06T14:00:03.000Z", sequence=1,
                           payload={"to_url": "http://m.site.com/about"},
                           page_context=mobile_ctx),
            _make_event_ts("click", "http://m.site.com/about",
                           "2026-03-06T14:00:04.000Z", sequence=2,
                           target={"selectors": {"css": "button.hamburger"}, "tag": "BUTTON"},
                           page_context=mobile_ctx),
            _make_event_ts("click", "http://m.site.com/about",
                           "2026-03-06T14:00:05.000Z", sequence=3,
                           target={"selectors": {"css": "a.nav-forms"}, "tag": "A",
                                   "attributes": {"href": "/forms"}},
                           page_context=mobile_ctx),
            # Page 3 — sequence resets again
            _make_event_ts("navigation", "http://m.site.com/about",
                           "2026-03-06T14:00:06.000Z", sequence=1,
                           payload={"to_url": "http://m.site.com/forms"},
                           page_context=mobile_ctx),
            _make_event_ts("click", "http://m.site.com/forms",
                           "2026-03-06T14:00:07.000Z", sequence=2,
                           target={"selectors": {"role": {"role": "button", "name": "Submit"}},
                                   "tag": "BUTTON"},
                           page_context=mobile_ctx),
        ]
        # Sort as fixed query would
        session.sort(key=lambda e: (e["timestamp"], e["sequence"]))

        flows = extract_popular_paths_from_sessions(
            [session, session], total_sessions=2, threshold_percent=1.0,
        )
        assert len(flows) >= 1
        pattern = flows[0]["canonical_pattern"]
        assert pattern.startswith("[mobile]"), f"Expected [mobile] prefix, got: {pattern}"
        # Verify correct page ordering inside the pattern
        assert "/about" in pattern
        assert "/forms" in pattern
