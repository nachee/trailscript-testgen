"""Unit tests for flow comparison engine."""

from testgen.flow_library.comparator import compare_flows


def _make_flow(pattern, actions=None, score=50.0):
    return {
        "canonical_pattern": pattern,
        "significant_actions": actions or [
            {"url": "/login", "type": "navigation"},
            {"url": "/login", "type": "click", "element": 'getByRole("button")'},
        ],
        "popularity_score": score,
        "session_count": int(score),
        "status": "active",
    }


class TestCompareFlows:
    def test_new_flow_detected(self):
        new_flows = [_make_flow("/login → /dashboard")]
        existing = []
        results = compare_flows(new_flows, existing)
        assert len(results) == 1
        assert results[0]["status"] == "new"

    def test_unchanged_flow(self):
        flow = _make_flow("/login → /dashboard")
        results = compare_flows([flow], [flow])
        assert len(results) == 1
        assert results[0]["status"] == "unchanged"

    def test_changed_flow_different_actions(self):
        existing = _make_flow("/login → /dashboard", [
            {"url": "/login", "type": "navigation"},
            {"url": "/login", "type": "click", "element": 'getByRole("button")'},
        ])
        new = _make_flow("/login → /dashboard", [
            {"url": "/login", "type": "navigation"},
            {"url": "/login", "type": "fill", "element": 'getByRole("textbox")'},
            {"url": "/login", "type": "click", "element": 'getByRole("button")'},
        ])
        results = compare_flows([new], [existing])
        assert len(results) == 1
        assert results[0]["status"] == "changed"

    def test_inactive_flow(self):
        existing = _make_flow("/old-page → /gone")
        new_flows = [_make_flow("/login → /dashboard")]
        results = compare_flows(new_flows, [existing])
        statuses = {r["status"] for r in results}
        assert "new" in statuses
        assert "inactive" in statuses

    def test_mixed_comparison(self):
        existing = [
            _make_flow("/login → /dashboard"),
            _make_flow("/settings → /profile"),
        ]
        new = [
            _make_flow("/login → /dashboard"),  # unchanged
            _make_flow("/signup → /onboard"),     # new
        ]
        results = compare_flows(new, existing)
        statuses = [r["status"] for r in results]
        assert "unchanged" in statuses
        assert "new" in statuses
        assert "inactive" in statuses

    def test_empty_flows(self):
        results = compare_flows([], [])
        assert results == []
