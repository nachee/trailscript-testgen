"""Unit tests for DB-row → canonical-event reconstruction and typed parsing."""

from datetime import datetime, timezone

from testgen.generators.playwright.event_typing import parse_flow_events, reconstruct_event


def _fill_row(**overrides) -> dict:
    """A DB-row event dict (storage shape) for a fill event.

    Mirrors the SELECT in tasks/generate.py: flat `url` + `page_context`
    columns, no `schema_version`, no nested `page`.
    """
    row = {
        "event_id": "11111111-1111-4111-8111-111111111111",
        "session_id": "22222222-2222-4222-8222-222222222222",
        "tab_id": "33333333-3333-4333-8333-333333333333",
        "sequence": 0,
        "timestamp": datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc),
        "event_type": "fill",
        "url": "https://example.com/signup",
        "target": {
            "selectors": {"css": "#email"},
            "tag": "INPUT",
            "attributes": {"id": "email"},
            "is_in_iframe": False,
        },
        "payload": {"value": "user@example.com"},
        "page_context": {"title": "Sign up", "viewport": {"width": 1280, "height": 800}},
    }
    row.update(overrides)
    return row


class TestReconstructEvent:
    def test_folds_url_and_page_context_into_nested_page(self):
        out = reconstruct_event(_fill_row())
        assert out["page"] == {
            "url": "https://example.com/signup",
            "title": "Sign up",
            "viewport": {"width": 1280, "height": 800},
        }

    def test_drops_storage_only_keys(self):
        out = reconstruct_event(_fill_row())
        assert "url" not in out
        assert "page_context" not in out

    def test_defaults_missing_schema_version_to_1(self):
        out = reconstruct_event(_fill_row())
        assert out["schema_version"] == 1

    def test_preserves_present_schema_version(self):
        out = reconstruct_event(_fill_row(schema_version=2))
        assert out["schema_version"] == 2

    def test_null_page_context_yields_url_only_page(self):
        out = reconstruct_event(_fill_row(page_context=None))
        assert out["page"] == {"url": "https://example.com/signup"}

    def test_carries_envelope_and_payload_through(self):
        out = reconstruct_event(_fill_row())
        assert out["event_type"] == "fill"
        assert out["payload"] == {"value": "user@example.com"}
        assert out["target"]["tag"] == "INPUT"


def _nav_row(**overrides) -> dict:
    """A DB-row event dict for a navigation event (a no-target meta event)."""
    row = {
        "event_id": "44444444-4444-4444-8444-444444444444",
        "session_id": "22222222-2222-4222-8222-222222222222",
        "tab_id": "33333333-3333-4333-8333-333333333333",
        "sequence": 1,
        "timestamp": datetime(2026, 1, 1, 12, 0, 1, tzinfo=timezone.utc),
        "event_type": "navigation",
        "url": "https://example.com/signup",
        "target": None,
        "payload": {"to_url": "https://example.com/welcome", "trigger": "js_redirect"},
        "page_context": {"title": "Sign up"},
    }
    row.update(overrides)
    return row


class TestParseFlowEvents:
    def test_valid_fill_flow_returns_typed_events(self):
        result = parse_flow_events([_fill_row()])
        assert result is not None
        assert result[0].event_type == "fill"
        assert result[0].payload.value == "user@example.com"
        assert result[0].target.tag == "INPUT"

    def test_navigation_event_with_null_target_validates(self):
        result = parse_flow_events([_nav_row()])
        assert result is not None
        assert result[0].event_type == "navigation"
        assert result[0].target is None
        assert result[0].payload.to_url == "https://example.com/welcome"

    def test_missing_schema_version_still_validates(self):
        row = _fill_row()
        assert "schema_version" not in row
        result = parse_flow_events([row])
        assert result is not None
        # schema_version is a RootModel wrapper in the generated Pydantic models.
        assert result[0].schema_version.root == 1

    def test_drifted_event_returns_none(self, caplog):
        import logging
        # Extra payload key violates extra="forbid" on the fill payload.
        drifted = _fill_row(payload={"value": "x", "bogus": True})
        with caplog.at_level(logging.WARNING):
            result = parse_flow_events([drifted])
        assert result is None
        assert any(rec.levelno == logging.WARNING for rec in caplog.records)
        assert "1 events" in caplog.text

    def test_one_bad_event_fails_the_whole_flow(self):
        good = _fill_row()
        drifted = _fill_row(payload={"value": "x", "bogus": True})
        assert parse_flow_events([good, drifted]) is None

    def test_empty_flow_returns_empty_list(self):
        assert parse_flow_events([]) == []
