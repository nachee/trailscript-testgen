"""Unit tests for flow graph construction."""

from testgen.graph.flow_builder import build_flow_graph, _event_to_node


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


class TestBuildFlowGraph:
    def test_single_session(self):
        events = [
            _make_event("navigation", "/", payload={"to_url": "/login"}, sequence=1),
            _make_event("click", "/login", target={"selectors": {"role": {"role": "button", "name": "Submit"}}, "tag": "BUTTON"}, sequence=2),
            _make_event("navigation", "/login", payload={"to_url": "/dashboard"}, sequence=3),
        ]
        graph = build_flow_graph([events])
        assert len(graph.nodes) >= 2
        assert len(graph.edges) >= 1

    def test_multiple_sessions_increase_weight(self):
        session1 = [
            _make_event("navigation", "/", payload={"to_url": "/login"}, sequence=1),
            _make_event("navigation", "/login", payload={"to_url": "/dashboard"}, sequence=2),
        ]
        session2 = [
            _make_event("navigation", "/", payload={"to_url": "/login"}, sequence=1),
            _make_event("navigation", "/login", payload={"to_url": "/dashboard"}, sequence=2),
        ]
        graph = build_flow_graph([session1, session2])

        # The edge between login and dashboard should have weight 2
        for u, v, data in graph.edges(data=True):
            if "login" in u and "dashboard" in v:
                assert data["weight"] == 2

    def test_empty_sessions(self):
        graph = build_flow_graph([])
        assert len(graph.nodes) == 0

    def test_single_event_skipped(self):
        events = [_make_event("navigation", "/", payload={"to_url": "/"}, sequence=1)]
        graph = build_flow_graph([events])
        # Single event won't create any edges
        assert len(graph.edges) == 0


class TestEventToNode:
    def test_navigation_event(self):
        event = _make_event("navigation", "/login", payload={"to_url": "/dashboard"})
        node = _event_to_node(event)
        assert node == "nav:/dashboard"

    def test_click_with_target(self):
        event = _make_event("click", "/login", target={
            "selectors": {"role": {"role": "button", "name": "Submit"}},
            "tag": "BUTTON",
        })
        node = _event_to_node(event)
        assert "click" in node
        assert "getByRole" in node

    def test_scroll_skipped(self):
        event = _make_event("scroll", "/page")
        assert _event_to_node(event) is None

    def test_hover_skipped(self):
        event = _make_event("hover", "/page")
        assert _event_to_node(event) is None

    def test_click_with_full_url(self):
        """Nodes with full URLs must preserve the URL intact (not split on ://)."""
        event = _make_event("click", "http://localhost:8080/page.html", target={
            "selectors": {"role": {"role": "button", "name": "Go"}},
            "tag": "BUTTON",
        })
        node = _event_to_node(event)
        assert node is not None
        # URL should be preserved intact in the node, separated by ||
        assert "http://localhost:8080/page.html" in node
        assert "||click||" in node

    def test_click_without_target_full_url(self):
        """Click without target element should still preserve full URL."""
        event = _make_event("click", "http://example.com/app")
        node = _event_to_node(event)
        assert node == "http://example.com/app||click"
