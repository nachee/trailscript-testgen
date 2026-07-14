"""Characterization tests for PlaywrightGenerator.build_variable_map.

These pin the behaviour through the dict→typed-events refactor: the typed
path and the dict-fallback path must produce identical variable maps.
"""

from datetime import datetime, timezone

from testgen.generators.playwright.adapter import PlaywrightGenerator
from testgen.generators.playwright.event_typing import parse_flow_events


def _fill_row(value: str, placeholder: str, **overrides) -> dict:
    """A DB-row fill event. Uses a placeholder selector so the element
    yields a variable name (CSS-only locators do not)."""
    row = {
        "event_id": "11111111-1111-4111-8111-111111111111",
        "session_id": "22222222-2222-4222-8222-222222222222",
        "tab_id": "33333333-3333-4333-8333-333333333333",
        "sequence": 0,
        "timestamp": datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc),
        "event_type": "fill",
        "url": "https://example.com/signup",
        "target": {
            "selectors": {"placeholder": placeholder},
            "tag": "INPUT",
            "attributes": {},
            "is_in_iframe": False,
        },
        "payload": {"value": value},
        "page_context": {"title": "Sign up"},
    }
    row.update(overrides)
    return row


class TestBuildVariableMap:
    def test_typed_path_maps_fill_elements(self):
        flow = {"representative_events": [
            _fill_row("user@example.com", "Email"),
            _fill_row("hunter2", "Password"),
        ]}
        result = PlaywrightGenerator().build_variable_map([flow])
        assert result == {
            'getByPlaceholder("Email")': "email",
            'getByPlaceholder("Password")': "password",
        }

    def test_dict_fallback_matches_typed_path(self):
        # A drifted event (extra payload key) forces the whole flow onto the
        # dict fallback path. The resulting map must be identical to the
        # all-valid (typed-path) map.
        valid_flow = {"representative_events": [
            _fill_row("user@example.com", "Email"),
            _fill_row("hunter2", "Password"),
        ]}
        drifted_flow = {"representative_events": [
            _fill_row("user@example.com", "Email"),
            _fill_row("hunter2", "Password", payload={"value": "hunter2", "bogus": True}),
        ]}
        gen = PlaywrightGenerator()
        # Confirm the drifted flow truly exercises the dict-fallback branch.
        assert parse_flow_events(drifted_flow["representative_events"]) is None
        assert gen.build_variable_map([drifted_flow]) == gen.build_variable_map([valid_flow])

    def test_non_fill_events_ignored(self):
        flow = {"representative_events": [
            {"event_type": "click", "target": {"selectors": {"css": "#btn"}, "tag": "BUTTON",
                                               "attributes": {}, "is_in_iframe": False},
             "payload": {}, "url": "https://example.com", "page_context": None,
             "event_id": "55555555-5555-4555-8555-555555555555",
             "session_id": "22222222-2222-4222-8222-222222222222",
             "tab_id": "33333333-3333-4333-8333-333333333333",
             "sequence": 0, "timestamp": datetime(2026, 1, 1, tzinfo=timezone.utc)},
        ]}
        assert PlaywrightGenerator().build_variable_map([flow]) == {}

    def test_empty_flows(self):
        assert PlaywrightGenerator().build_variable_map([]) == {}
