"""Tests for the dedup strategy module."""

import pytest

from testgen.generators.playwright.dedup import (
    compute_action_fingerprint,
    group_flows_by_interaction,
    apply_dedup_strategy,
    _is_nav_click,
    _has_navigation_prefix,
    _find_interaction_page_url,
    _find_interaction_start_index,
    _interactions_on_single_page,
    VALID_STRATEGIES,
)


def _make_flow(pattern, events, popularity=10.0):
    """Create a minimal flow dict for testing."""
    return {
        "canonical_pattern": pattern,
        "popularity_score": popularity,
        "session_count": int(popularity),
        "significant_actions": [],
        "representative_events": events,
    }


def _click_event(element_tag, text, url="http://localhost/interactive.html"):
    """Create a click event."""
    return {
        "event_type": "click",
        "url": url,
        "target": {
            "tag": element_tag,
            "attributes": {},
            "text_content": text,
            "role": "button" if element_tag == "BUTTON" else None,
        },
        "payload": {},
    }


def _link_click_event(href, url="http://localhost/index.html", text="Link"):
    """Create a click event on an <a> tag with an href (navigation click)."""
    return {
        "event_type": "click",
        "url": url,
        "target": {
            "tag": "A",
            "attributes": {"href": href},
            "text_content": text,
        },
        "payload": {},
    }


def _nav_event(to_url):
    """Create a navigation event."""
    return {
        "event_type": "navigation",
        "url": to_url,
        "target": None,
        "payload": {"to_url": to_url},
    }


def _fill_event(field_id, value, url="http://localhost/forms.html"):
    """Create a fill event."""
    return {
        "event_type": "fill",
        "url": url,
        "target": {
            "tag": "INPUT",
            "attributes": {"id": field_id},
            "text_content": "",
        },
        "payload": {"value": value},
    }


# ---------- Fingerprinting ----------

class TestActionFingerprint:
    def test_same_actions_same_fingerprint(self):
        """Flows with identical interactions get the same fingerprint."""
        events = [
            _nav_event("http://localhost/interactive.html"),
            _click_event("BUTTON", "+ 10%"),
            _click_event("BUTTON", "+ 10%"),
            _click_event("BUTTON", "Reset"),
        ]
        flow_a = _make_flow("/ → /interactive", events, popularity=12)
        flow_b = _make_flow("/about → /interactive", events, popularity=8)

        assert compute_action_fingerprint(flow_a) == compute_action_fingerprint(flow_b)

    def test_different_actions_different_fingerprint(self):
        """Flows with different interactions get different fingerprints."""
        flow_a = _make_flow("/ → /interactive", [
            _click_event("BUTTON", "+ 10%"),
        ])
        flow_b = _make_flow("/ → /forms", [
            _fill_event("name", "Alice"),
        ])

        assert compute_action_fingerprint(flow_a) != compute_action_fingerprint(flow_b)

    def test_navigation_events_ignored(self):
        """Navigation events don't affect the fingerprint."""
        events_with_nav = [
            _nav_event("http://localhost/"),
            _nav_event("http://localhost/interactive.html"),
            _click_event("BUTTON", "Submit"),
        ]
        events_without_nav = [
            _click_event("BUTTON", "Submit"),
        ]
        flow_a = _make_flow("/ → /interactive", events_with_nav)
        flow_b = _make_flow("/interactive", events_without_nav)

        assert compute_action_fingerprint(flow_a) == compute_action_fingerprint(flow_b)

    def test_scroll_hover_focus_ignored(self):
        """Skip events don't affect the fingerprint."""
        events_with_skip = [
            {"event_type": "scroll", "url": "http://localhost/", "target": None, "payload": {}},
            {"event_type": "hover", "url": "http://localhost/", "target": None, "payload": {}},
            _click_event("BUTTON", "Submit"),
        ]
        events_without_skip = [
            _click_event("BUTTON", "Submit"),
        ]
        flow_a = _make_flow("pattern_a", events_with_skip)
        flow_b = _make_flow("pattern_b", events_without_skip)

        assert compute_action_fingerprint(flow_a) == compute_action_fingerprint(flow_b)

    def test_blur_input_change_submit_ignored(self):
        """Intermediate browser events (blur, input, change, submit) don't affect fingerprint."""
        events_with_intermediate = [
            _click_event("BUTTON", "Submit"),
            {"event_type": "blur", "url": "http://localhost/", "target": None, "payload": {}},
            {"event_type": "input", "url": "http://localhost/", "target": None, "payload": {}},
            {"event_type": "change", "url": "http://localhost/", "target": None, "payload": {}},
            {"event_type": "submit", "url": "http://localhost/", "target": None, "payload": {}},
        ]
        events_clean = [
            _click_event("BUTTON", "Submit"),
        ]
        flow_a = _make_flow("pattern_a", events_with_intermediate)
        flow_b = _make_flow("pattern_b", events_clean)

        assert compute_action_fingerprint(flow_a) == compute_action_fingerprint(flow_b)

    def test_nav_only_flows_get_unique_fingerprints(self):
        """Flows with no interactions get unique fingerprints based on pattern."""
        flow_a = _make_flow("/ → /about", [_nav_event("http://localhost/about")])
        flow_b = _make_flow("/ → /forms", [_nav_event("http://localhost/forms")])

        assert compute_action_fingerprint(flow_a) != compute_action_fingerprint(flow_b)


# ---------- Grouping ----------

class TestGroupFlows:
    def test_groups_by_fingerprint(self):
        """Flows with same interactions are grouped together."""
        click_events = [_click_event("BUTTON", "Submit")]
        flow_a = _make_flow("/ → /page", click_events, popularity=15)
        flow_b = _make_flow("/about → /page", click_events, popularity=8)
        flow_c = _make_flow("/ → /other", [_fill_event("name", "x")], popularity=10)

        groups = group_flows_by_interaction([flow_a, flow_b, flow_c])

        assert len(groups) == 2
        # Find the group with 2 flows
        big_group = [g for g in groups.values() if len(g) == 2][0]
        assert big_group[0]["popularity_score"] == 15  # Sorted by popularity
        assert big_group[1]["popularity_score"] == 8

    def test_sorted_by_popularity(self):
        """Within each group, flows are sorted by popularity descending."""
        events = [_click_event("BUTTON", "Click")]
        flows = [
            _make_flow("p1", events, popularity=5),
            _make_flow("p2", events, popularity=20),
            _make_flow("p3", events, popularity=10),
        ]
        groups = group_flows_by_interaction(flows)
        group = list(groups.values())[0]

        assert [f["popularity_score"] for f in group] == [20, 10, 5]


# ---------- Strategy: full ----------

class TestStrategyFull:
    def test_all_flows_get_full_mode(self):
        events = [_click_event("BUTTON", "X")]
        flows = [
            _make_flow("p1", events, popularity=15),
            _make_flow("p2", events, popularity=8),
        ]
        result = apply_dedup_strategy(flows, "full")

        assert len(result) == 2
        assert all(f["_test_mode"] == "full" for f in result)


# ---------- Strategy: smart ----------

class TestStrategySmart:
    def test_most_popular_gets_full_others_nav_only(self):
        events = [_click_event("BUTTON", "X")]
        flows = [
            _make_flow("p1", events, popularity=15),
            _make_flow("p2", events, popularity=8),
            _make_flow("p3", events, popularity=5),
        ]
        result = apply_dedup_strategy(flows, "smart")

        assert len(result) == 3
        full_tests = [f for f in result if f["_test_mode"] == "full"]
        nav_tests = [f for f in result if f["_test_mode"] == "nav_only"]
        assert len(full_tests) == 1
        assert len(nav_tests) == 2
        assert full_tests[0]["popularity_score"] == 15

    def test_unique_flows_all_get_full(self):
        """Flows with different interactions all get full tests."""
        flows = [
            _make_flow("p1", [_click_event("BUTTON", "A")], popularity=15),
            _make_flow("p2", [_fill_event("name", "x")], popularity=8),
        ]
        result = apply_dedup_strategy(flows, "smart")

        assert len(result) == 2
        assert all(f["_test_mode"] == "full" for f in result)

    def test_default_strategy(self):
        """Invalid strategy name falls back to smart."""
        events = [_click_event("BUTTON", "X")]
        flows = [
            _make_flow("p1", events, popularity=15),
            _make_flow("p2", events, popularity=8),
        ]
        result = apply_dedup_strategy(flows, "invalid_name")

        nav_tests = [f for f in result if f["_test_mode"] == "nav_only"]
        assert len(nav_tests) == 1  # smart behavior


# ---------- Strategy: lean ----------

class TestStrategyLean:
    def test_only_canonical_survives(self):
        events = [_click_event("BUTTON", "X")]
        flows = [
            _make_flow("p1", events, popularity=15),
            _make_flow("p2", events, popularity=8),
            _make_flow("p3", events, popularity=5),
        ]
        result = apply_dedup_strategy(flows, "lean")

        assert len(result) == 1
        assert result[0]["_test_mode"] == "full"
        assert result[0]["popularity_score"] == 15


# ---------- Strategy: modular ----------

class TestStrategyModular:
    def test_interaction_only_plus_nav_only_for_groups(self):
        events = [_click_event("BUTTON", "X")]
        flows = [
            _make_flow("p1", events, popularity=15),
            _make_flow("p2", events, popularity=8),
        ]
        result = apply_dedup_strategy(flows, "modular")

        interaction_tests = [f for f in result if f["_test_mode"] == "interaction_only"]
        nav_tests = [f for f in result if f["_test_mode"] == "nav_only"]

        assert len(interaction_tests) == 1
        assert len(nav_tests) == 2  # Both flows get nav-only

    def test_singleton_no_nav_prefix_gets_interaction_only(self):
        """Singleton flow starting on interaction page → interaction_only."""
        flows = [
            _make_flow("p1", [_click_event("BUTTON", "A")], popularity=15),
        ]
        result = apply_dedup_strategy(flows, "modular")

        assert len(result) == 1
        assert result[0]["_test_mode"] == "interaction_only"

    def test_singleton_with_nav_prefix_splits(self):
        """Singleton flow with navigation before interactions → split into
        interaction_only + nav_only."""
        events = [
            _link_click_event("/interactive.html", url="http://localhost/index.html"),
            _nav_event("http://localhost/interactive.html"),
            _click_event("BUTTON", "Counter +", url="http://localhost/interactive.html"),
            _click_event("BUTTON", "Counter +", url="http://localhost/interactive.html"),
        ]
        flows = [_make_flow("/ → /interactive", events, popularity=10)]
        result = apply_dedup_strategy(flows, "modular")

        interaction_tests = [f for f in result if f["_test_mode"] == "interaction_only"]
        nav_tests = [f for f in result if f["_test_mode"] == "nav_only"]

        assert len(interaction_tests) == 1
        assert len(nav_tests) == 1
        assert interaction_tests[0]["_interaction_page_url"] == "http://localhost/interactive.html"

    def test_singleton_no_interactions_gets_full(self):
        """Singleton flow with only navigation (no interactions) → full test."""
        events = [
            _link_click_event("/about.html", url="http://localhost/index.html"),
            _nav_event("http://localhost/about.html"),
            _link_click_event("/forms.html", url="http://localhost/about.html"),
            _nav_event("http://localhost/forms.html"),
        ]
        flows = [_make_flow("/ → /about → /forms", events, popularity=10)]
        result = apply_dedup_strategy(flows, "modular")

        assert len(result) == 1
        assert result[0]["_test_mode"] == "full"

    def test_singleton_interleaved_gets_full(self):
        """Singleton flow with interleaved nav+interactions → full test (can't split)."""
        events = [
            _click_event("BUTTON", "Submit", url="http://localhost/forms.html"),
            _link_click_event("/interactive.html", url="http://localhost/forms.html"),
            _nav_event("http://localhost/interactive.html"),
            _click_event("BUTTON", "Counter +", url="http://localhost/interactive.html"),
        ]
        flows = [_make_flow("/forms → /interactive", events, popularity=10)]
        result = apply_dedup_strategy(flows, "modular")

        full_tests = [f for f in result if f["_test_mode"] == "full"]
        assert len(full_tests) == 1

    def test_nav_only_deduplication(self):
        """Multiple singletons sharing same route produce one nav_only test, not duplicates.

        Uses a fill event + click event to ensure truly different fingerprints
        (same button tag = same fingerprint, so we need different event types).
        """
        events_a = [
            _link_click_event("/interactive.html", url="http://localhost/index.html"),
            _nav_event("http://localhost/interactive.html"),
            _click_event("BUTTON", "Counter +", url="http://localhost/interactive.html"),
            _click_event("BUTTON", "Counter +", url="http://localhost/interactive.html"),
        ]
        events_b = [
            _link_click_event("/interactive.html", url="http://localhost/index.html"),
            _nav_event("http://localhost/interactive.html"),
            _fill_event("search", "test", url="http://localhost/interactive.html"),
        ]
        flows = [
            _make_flow("/ → /interactive", events_a, popularity=10),
            _make_flow("/ → /interactive", events_b, popularity=8),
        ]
        result = apply_dedup_strategy(flows, "modular")

        interaction_tests = [f for f in result if f["_test_mode"] == "interaction_only"]
        nav_tests = [f for f in result if f["_test_mode"] == "nav_only"]

        assert len(interaction_tests) == 2  # Two different interactions
        assert len(nav_tests) == 1  # Only one nav_only (same route deduplicated)


# ---------- _is_nav_click ----------

class TestIsNavClick:
    def test_link_to_different_page(self):
        """Click on <a href="/other.html"> from /index.html is a nav click."""
        event = _link_click_event("/other.html", url="http://localhost/index.html")
        assert _is_nav_click(event) is True

    def test_link_to_same_page_anchor(self):
        """Click on <a href="#section"> is NOT a nav click."""
        event = _link_click_event("#section", url="http://localhost/index.html")
        assert _is_nav_click(event) is False

    def test_button_click_is_not_nav(self):
        """Click on a BUTTON is never a nav click."""
        event = _click_event("BUTTON", "Submit")
        assert _is_nav_click(event) is False

    def test_javascript_href_is_not_nav(self):
        """Click on <a href="javascript:void(0)"> is NOT a nav click."""
        event = _link_click_event("javascript:void(0)", url="http://localhost/index.html")
        assert _is_nav_click(event) is False

    def test_relative_href_to_different_page(self):
        """Click on <a href="about.html"> from /index.html is a nav click."""
        event = _link_click_event("about.html", url="http://localhost/index.html")
        assert _is_nav_click(event) is True

    def test_absolute_href_to_different_page(self):
        """Click on <a href="http://localhost/other.html"> is a nav click."""
        event = _link_click_event("http://localhost/other.html", url="http://localhost/index.html")
        assert _is_nav_click(event) is True

    def test_link_to_same_page(self):
        """Click on <a href="/index.html"> from /index.html is NOT a nav click."""
        event = _link_click_event("/index.html", url="http://localhost/index.html")
        assert _is_nav_click(event) is False


# ---------- Navigation click filtering in fingerprint ----------

class TestNavClickFiltering:
    def test_nav_clicks_excluded_from_fingerprint(self):
        """Flows with same interactions but different nav clicks get same fingerprint."""
        interaction_events = [
            _click_event("BUTTON", "+ 10%", url="http://localhost/interactive.html"),
            _click_event("BUTTON", "Reset", url="http://localhost/interactive.html"),
        ]
        # Flow A: short route (1 nav click)
        flow_a = _make_flow("/ → /interactive", [
            _link_click_event("/interactive.html", url="http://localhost/index.html"),
            _nav_event("http://localhost/interactive.html"),
            *interaction_events,
        ], popularity=8)

        # Flow B: long route (3 nav clicks)
        flow_b = _make_flow("/ → /about → /forms → /interactive", [
            _link_click_event("/about.html", url="http://localhost/index.html"),
            _nav_event("http://localhost/about.html"),
            _link_click_event("/forms.html", url="http://localhost/about.html"),
            _nav_event("http://localhost/forms.html"),
            _link_click_event("/interactive.html", url="http://localhost/forms.html"),
            _nav_event("http://localhost/interactive.html"),
            *interaction_events,
        ], popularity=12)

        assert compute_action_fingerprint(flow_a) == compute_action_fingerprint(flow_b)

    def test_different_interactions_still_differ(self):
        """Even with nav click filtering, different interactions get different fingerprints."""
        flow_a = _make_flow("p1", [
            _link_click_event("/interactive.html", url="http://localhost/"),
            _click_event("BUTTON", "Submit", url="http://localhost/interactive.html"),
        ])
        flow_b = _make_flow("p2", [
            _link_click_event("/forms.html", url="http://localhost/"),
            _fill_event("name", "Alice"),
        ])
        assert compute_action_fingerprint(flow_a) != compute_action_fingerprint(flow_b)


# ---------- _find_interaction_page_url ----------

class TestFindInteractionPageUrl:
    def test_skips_nav_clicks_returns_interaction_url(self):
        """Returns the URL of the first actual interaction, not a nav click."""
        flow = _make_flow("/ → /interactive", [
            _link_click_event("/interactive.html", url="http://localhost/index.html"),
            _nav_event("http://localhost/interactive.html"),
            _click_event("BUTTON", "Toggle", url="http://localhost/interactive.html"),
        ])
        assert _find_interaction_page_url(flow) == "http://localhost/interactive.html"

    def test_returns_none_for_nav_only_flow(self):
        """Returns None for flows with only navigation events."""
        flow = _make_flow("/ → /about", [
            _nav_event("http://localhost/about.html"),
        ])
        assert _find_interaction_page_url(flow) is None

    def test_returns_first_fill_url(self):
        """Returns the URL of the first fill event."""
        flow = _make_flow("/ → /forms", [
            _link_click_event("/forms.html", url="http://localhost/index.html"),
            _nav_event("http://localhost/forms.html"),
            _fill_event("name", "Alice", url="http://localhost/forms.html"),
        ])
        assert _find_interaction_page_url(flow) == "http://localhost/forms.html"

    def test_contextual_nav_click_skipped(self):
        """Click on non-A element followed by navigation is skipped."""
        flow = _make_flow("/ → /interactive", [
            # Click on SPAN inside a link — tracker captured child, not <a>
            _click_event("SPAN", "TS TestSite", url="http://localhost/about.html"),
            _nav_event("http://localhost/index.html"),
            _click_event("BUTTON", "Toggle", url="http://localhost/interactive.html"),
        ])
        assert _find_interaction_page_url(flow) == "http://localhost/interactive.html"


# ---------- _find_interaction_start_index ----------

class TestFindInteractionStartIndex:
    def test_skips_nav_clicks(self):
        """Navigation clicks (A tags) are not interactions."""
        events = [
            _link_click_event("/about.html", url="http://localhost/index.html"),
            _nav_event("http://localhost/about.html"),
            _click_event("BUTTON", "Submit", url="http://localhost/about.html"),
        ]
        flow = _make_flow("/ → /about", events)
        assert _find_interaction_start_index(flow) == 2

    def test_skips_contextual_nav_clicks(self):
        """Clicks on non-A elements followed by navigation are not interactions."""
        events = [
            _click_event("SPAN", "Logo", url="http://localhost/index.html"),
            _nav_event("http://localhost/about.html"),
            _click_event("BUTTON", "Submit", url="http://localhost/about.html"),
        ]
        flow = _make_flow("/ → /about", events)
        assert _find_interaction_start_index(flow) == 2

    def test_returns_none_for_pure_nav(self):
        """Pure navigation flows (only link clicks + nav events) return None."""
        events = [
            _link_click_event("/about.html", url="http://localhost/index.html"),
            _nav_event("http://localhost/about.html"),
            _link_click_event("/index.html", url="http://localhost/about.html"),
            _nav_event("http://localhost/index.html"),
        ]
        flow = _make_flow("/ → /about → /", events)
        assert _find_interaction_start_index(flow) is None


# ---------- Contextual _is_nav_click ----------

class TestIsNavClickContextual:
    def test_non_a_click_followed_by_navigation(self):
        """Click on SPAN followed by navigation event is detected as nav click."""
        events = [
            _click_event("SPAN", "Logo", url="http://localhost/index.html"),
            _nav_event("http://localhost/about.html"),
        ]
        assert _is_nav_click(events[0], events, 0) is True

    def test_non_a_click_followed_by_interaction(self):
        """Click on SPAN followed by another click (not navigation) is NOT a nav click."""
        events = [
            _click_event("SPAN", "Tab 1", url="http://localhost/interactive.html"),
            _click_event("BUTTON", "Submit", url="http://localhost/interactive.html"),
        ]
        assert _is_nav_click(events[0], events, 0) is False

    def test_non_a_click_without_context(self):
        """Click on SPAN without event context is NOT a nav click (backward compat)."""
        event = _click_event("SPAN", "Logo", url="http://localhost/index.html")
        assert _is_nav_click(event) is False

    def test_skip_events_between_click_and_nav(self):
        """Skip events (scroll, hover) between click and navigation don't block detection."""
        events = [
            _click_event("SPAN", "Logo", url="http://localhost/index.html"),
            {"event_type": "scroll", "url": "http://localhost/", "target": None, "payload": {}},
            _nav_event("http://localhost/about.html"),
        ]
        assert _is_nav_click(events[0], events, 0) is True

    def test_blur_between_click_and_nav(self):
        """Blur events between a link-child click and navigation don't block detection."""
        events = [
            _click_event("SPAN", "Interactive", url="http://localhost/index.html"),
            {"event_type": "blur", "url": "http://localhost/index.html", "target": None, "payload": {}},
            _nav_event("http://localhost/interactive.html"),
        ]
        assert _is_nav_click(events[0], events, 0) is True

    def test_input_change_between_click_and_nav(self):
        """Input/change events between click and navigation don't block detection."""
        events = [
            _click_event("SPAN", "Logo", url="http://localhost/index.html"),
            {"event_type": "input", "url": "http://localhost/index.html", "target": None, "payload": {}},
            {"event_type": "change", "url": "http://localhost/index.html", "target": None, "payload": {}},
            _nav_event("http://localhost/about.html"),
        ]
        assert _is_nav_click(events[0], events, 0) is True


# ---------- Modular strategy with pure-navigation flows ----------

class TestModularPureNav:
    def test_pure_nav_flows_get_full_not_split(self):
        """Flows with only nav clicks should NOT be split into interaction/nav-only."""
        events = [
            _link_click_event("/about.html", url="http://localhost/index.html"),
            _nav_event("http://localhost/about.html"),
            _link_click_event("/index.html", url="http://localhost/about.html"),
            _nav_event("http://localhost/index.html"),
        ]
        flows = [
            _make_flow("/ → /about → /", events, popularity=10),
        ]
        result = apply_dedup_strategy(flows, "modular")

        assert len(result) == 1
        assert result[0]["_test_mode"] == "full"

    def test_contextual_nav_flows_get_full_not_split(self):
        """Flows where all clicks trigger navigation (child-element clicks) get full tests."""
        events = [
            _click_event("SPAN", "Logo", url="http://localhost/about.html"),
            _nav_event("http://localhost/index.html"),
            _click_event("SPAN", "About", url="http://localhost/index.html"),
            _nav_event("http://localhost/about.html"),
        ]
        flows = [
            _make_flow("about → / → about", events, popularity=5),
        ]
        result = apply_dedup_strategy(flows, "modular")

        assert len(result) == 1
        assert result[0]["_test_mode"] == "full"


# ---------- _interactions_on_single_page ----------

class TestInteractionsOnSinglePage:
    def test_single_page_interactions(self):
        """All interactions on one page returns True."""
        flow = _make_flow("/ → /forms", [
            _link_click_event("/forms.html", url="http://localhost/index.html"),
            _nav_event("http://localhost/forms.html"),
            _fill_event("name", "Alice", url="http://localhost/forms.html"),
            _fill_event("email", "a@b.com", url="http://localhost/forms.html"),
        ])
        assert _interactions_on_single_page(flow) is True

    def test_interleaved_interactions(self):
        """Interactions on multiple pages returns False."""
        flow = _make_flow("/ → /forms → /other", [
            _fill_event("name", "Alice", url="http://localhost/forms.html"),
            _link_click_event("/other.html", url="http://localhost/forms.html"),
            _nav_event("http://localhost/other.html"),
            _fill_event("email", "a@b.com", url="http://localhost/other.html"),
        ])
        assert _interactions_on_single_page(flow) is False

    def test_no_interactions(self):
        """Flow with only nav events returns True (vacuously)."""
        flow = _make_flow("/ → /about", [
            _link_click_event("/about.html", url="http://localhost/index.html"),
            _nav_event("http://localhost/about.html"),
        ])
        assert _interactions_on_single_page(flow) is True

    def test_blur_between_nav_click_and_navigation(self):
        """Blur events don't cause false interaction-page detection.

        When a blur event appears between a child-element link click and
        the resulting navigation, it must not be treated as an interaction
        on the source page.
        """
        flow = _make_flow("/ → /interactive", [
            _click_event("SPAN", "Interactive", url="http://localhost/index.html"),
            {"event_type": "blur", "url": "http://localhost/index.html", "target": None, "payload": {}},
            _nav_event("http://localhost/interactive.html"),
            _click_event("BUTTON", "Toggle", url="http://localhost/interactive.html"),
        ])
        assert _interactions_on_single_page(flow) is True

    def test_submit_event_skipped(self):
        """Submit events are skipped (not treated as interactions)."""
        flow = _make_flow("/ → /forms", [
            _link_click_event("/forms.html", url="http://localhost/index.html"),
            _nav_event("http://localhost/forms.html"),
            _fill_event("name", "Alice", url="http://localhost/forms.html"),
            {"event_type": "submit", "url": "http://localhost/forms.html", "target": None, "payload": {}},
        ])
        assert _interactions_on_single_page(flow) is True


# ---------- Modular with interleaved flows ----------

class TestModularInterleaved:
    def test_interleaved_falls_back_to_smart(self):
        """Multi-flow groups with interleaved nav+interactions fall back to smart strategy."""
        interleaved_events = [
            _fill_event("name", "Alice", url="http://localhost/forms.html"),
            _link_click_event("/other.html", url="http://localhost/forms.html"),
            _nav_event("http://localhost/other.html"),
            _fill_event("email", "a@b.com", url="http://localhost/other.html"),
        ]
        flows = [
            _make_flow("/ → /forms → /other", interleaved_events, popularity=15),
            _make_flow("/about → /forms → /other", interleaved_events, popularity=8),
        ]
        result = apply_dedup_strategy(flows, "modular")

        # Should fall back to smart: full for canonical, nav_only for alternative
        full_tests = [f for f in result if f["_test_mode"] == "full"]
        nav_tests = [f for f in result if f["_test_mode"] == "nav_only"]
        interaction_tests = [f for f in result if f["_test_mode"] == "interaction_only"]

        assert len(full_tests) == 1
        assert len(nav_tests) == 1
        assert len(interaction_tests) == 0  # No interaction_only for interleaved

    def test_single_page_interactions_get_split(self):
        """Multi-flow groups with single-page interactions get properly split."""
        single_page_events = [
            _link_click_event("/forms.html", url="http://localhost/index.html"),
            _nav_event("http://localhost/forms.html"),
            _fill_event("name", "Alice", url="http://localhost/forms.html"),
            _fill_event("email", "a@b.com", url="http://localhost/forms.html"),
        ]
        flows = [
            _make_flow("/ → /forms", single_page_events, popularity=15),
            _make_flow("/about → /forms", single_page_events, popularity=8),
        ]
        result = apply_dedup_strategy(flows, "modular")

        interaction_tests = [f for f in result if f["_test_mode"] == "interaction_only"]
        nav_tests = [f for f in result if f["_test_mode"] == "nav_only"]

        assert len(interaction_tests) == 1
        assert len(nav_tests) == 2

    def test_modular_with_blur_between_nav_click_and_navigation(self):
        """Modular correctly splits when blur events appear between link clicks and navigation.

        This is the key bug fix: intermediate browser events (blur, input, change)
        between a child-element link click and the resulting navigation event must
        not prevent modular from detecting single-page interactions.
        """
        events = [
            _click_event("SPAN", "Interactive", url="http://localhost/index.html"),
            {"event_type": "blur", "url": "http://localhost/index.html", "target": None, "payload": {}},
            _nav_event("http://localhost/interactive.html"),
            _click_event("BUTTON", "Toggle", url="http://localhost/interactive.html"),
            {"event_type": "check", "url": "http://localhost/interactive.html",
             "target": {"tag": "INPUT", "attributes": {"id": "toggle1"}, "text_content": ""},
             "payload": {"checked": True}},
        ]
        flows = [
            _make_flow("/ → /interactive", events, popularity=15),
            _make_flow("/about → /interactive", events, popularity=8),
        ]
        result = apply_dedup_strategy(flows, "modular")

        interaction_tests = [f for f in result if f["_test_mode"] == "interaction_only"]
        nav_tests = [f for f in result if f["_test_mode"] == "nav_only"]

        assert len(interaction_tests) == 1, "Should produce interaction_only test"
        assert len(nav_tests) == 2, "Should produce nav_only for both routes"
