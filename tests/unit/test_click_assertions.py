"""Tests for click state assertions via checkpoint diffing."""

import pytest

from testgen.generators.playwright.adapter import PlaywrightGenerator


def _make_checkpoint(checkpoint_id, session_id, trigger_event_id, url, visible_elements=None, timestamp="2026-03-10T10:00:00Z"):
    """Create a minimal checkpoint dict for testing."""
    return {
        "checkpoint_id": checkpoint_id,
        "session_id": session_id,
        "trigger_event_id": trigger_event_id,
        "url": url,
        "timestamp": timestamp,
        "visible_elements": visible_elements or [],
        "form_values": [],
        "page_title": "Test Page",
    }


def _make_element(tag, text_content, role=None, testid=None, css=None):
    """Create a visible_element dict for checkpoint testing."""
    selectors = {}
    if role:
        selectors["role"] = role
    if testid:
        selectors["testid"] = testid
    if css:
        selectors["css"] = css
    return {
        "selectors": selectors,
        "tag": tag,
        "text_content": text_content,
    }


def _make_event(event_id, session_id, event_type="click", url="http://localhost/page"):
    """Create a minimal event dict."""
    return {
        "event_id": event_id,
        "session_id": session_id,
        "event_type": event_type,
        "url": url,
        "target": {"tag": "BUTTON", "attributes": {}, "text_content": "Click"},
        "payload": {},
    }


# ---------- Element Identity ----------

class TestElementIdentity:
    def test_testid_preferred(self):
        elem = _make_element("SPAN", "70%", testid="progress-value")
        assert PlaywrightGenerator._element_identity(elem) == "testid:progress-value"

    def test_role_used_when_no_testid(self):
        elem = _make_element("BUTTON", "Submit", role={"role": "button", "name": "Submit"})
        assert PlaywrightGenerator._element_identity(elem) == "role:button:Submit"

    def test_css_fallback(self):
        elem = _make_element("DIV", "Content", css=".progress-bar")
        assert PlaywrightGenerator._element_identity(elem) == "css:.progress-bar"

    def test_tag_fallback(self):
        elem = _make_element("SPAN", "Text")
        assert PlaywrightGenerator._element_identity(elem) == "css:SPAN"


# ---------- Checkpoint Element Locator ----------

class TestCheckpointElementLocator:
    def test_role_locator(self):
        elem = _make_element("BUTTON", "Submit", role={"role": "button", "name": "Submit"})
        locator = PlaywrightGenerator._checkpoint_element_locator(elem)
        assert locator == 'page.getByRole("button", { name: "Submit" })'

    def test_testid_locator(self):
        elem = _make_element("SPAN", "70%", testid="progress-value")
        locator = PlaywrightGenerator._checkpoint_element_locator(elem)
        assert locator == 'page.getByTestId("progress-value")'

    def test_text_locator(self):
        elem = _make_element("SPAN", "70%")
        locator = PlaywrightGenerator._checkpoint_element_locator(elem)
        assert locator == 'page.getByText("70%")'

    def test_no_locator_for_long_text(self):
        elem = _make_element("P", "x" * 60)
        locator = PlaywrightGenerator._checkpoint_element_locator(elem)
        assert locator is None

    def test_no_locator_for_empty_text(self):
        elem = _make_element("DIV", "")
        locator = PlaywrightGenerator._checkpoint_element_locator(elem)
        assert locator is None


# ---------- Diff Visible Elements ----------

class TestDiffVisibleElements:
    def setup_method(self):
        self.gen = PlaywrightGenerator()
        self.gen._global_elements = set()

    def test_text_change_generates_assertion(self):
        """When an element's text changes, a toContainText assertion is generated."""
        before = _make_checkpoint(
            "cp1", "s1", "ev0", "http://localhost/page",
            visible_elements=[
                _make_element("SPAN", "0%", testid="progress"),
            ],
        )
        after = _make_checkpoint(
            "cp2", "s1", "ev1", "http://localhost/page",
            visible_elements=[
                _make_element("SPAN", "70%", testid="progress"),
            ],
        )
        assertions = self.gen._diff_visible_elements(before, after)
        assert len(assertions) == 1
        assert 'toContainText("70%")' in assertions[0]
        assert 'getByTestId("progress")' in assertions[0]

    def test_new_element_generates_visibility_assertion(self):
        """When a new element appears, a toBeVisible assertion is generated."""
        before = _make_checkpoint("cp1", "s1", "ev0", "http://localhost/page", visible_elements=[])
        after = _make_checkpoint(
            "cp2", "s1", "ev1", "http://localhost/page",
            visible_elements=[
                _make_element("DIV", "Success!", role={"role": "alert", "name": "Success!"}),
            ],
        )
        assertions = self.gen._diff_visible_elements(before, after)
        assert len(assertions) == 1
        assert "toBeVisible()" in assertions[0]
        assert 'getByRole("alert"' in assertions[0]

    def test_no_before_checkpoint_returns_empty(self):
        """When there's no before checkpoint, no assertions are generated."""
        after = _make_checkpoint(
            "cp2", "s1", "ev1", "http://localhost/page",
            visible_elements=[_make_element("SPAN", "70%", testid="progress")],
        )
        assertions = self.gen._diff_visible_elements(None, after)
        assert len(assertions) == 0

    def test_unchanged_elements_ignored(self):
        """Elements with no text change don't generate assertions."""
        elem = _make_element("BUTTON", "Submit", role={"role": "button", "name": "Submit"})
        before = _make_checkpoint("cp1", "s1", "ev0", "http://localhost/page", visible_elements=[elem])
        after = _make_checkpoint("cp2", "s1", "ev1", "http://localhost/page", visible_elements=[elem])
        assertions = self.gen._diff_visible_elements(before, after)
        assert len(assertions) == 0

    def test_max_three_assertions(self):
        """At most 3 assertions are generated."""
        before = _make_checkpoint("cp1", "s1", "ev0", "http://localhost/page", visible_elements=[])
        after = _make_checkpoint(
            "cp2", "s1", "ev1", "http://localhost/page",
            visible_elements=[
                _make_element("SPAN", "A", testid="a"),
                _make_element("SPAN", "B", testid="b"),
                _make_element("SPAN", "C", testid="c"),
                _make_element("SPAN", "D", testid="d"),
                _make_element("SPAN", "E", testid="e"),
            ],
        )
        assertions = self.gen._diff_visible_elements(before, after)
        assert len(assertions) == 3

    def test_global_elements_skipped(self):
        """Elements in the global set are skipped."""
        self.gen._global_elements = {"role:navigation:Main Nav"}
        before = _make_checkpoint("cp1", "s1", "ev0", "http://localhost/page", visible_elements=[])
        after = _make_checkpoint(
            "cp2", "s1", "ev1", "http://localhost/page",
            visible_elements=[
                _make_element("NAV", "Main Nav", role={"role": "navigation", "name": "Main Nav"}),
            ],
        )
        assertions = self.gen._diff_visible_elements(before, after)
        assert len(assertions) == 0


# ---------- Click State Assertions (end-to-end) ----------

class TestClickStateAssertions:
    def setup_method(self):
        self.gen = PlaywrightGenerator()

    def test_click_with_settle_checkpoint_generates_assertion(self):
        """A click event with a settle checkpoint generates a text change assertion."""
        checkpoints = [
            _make_checkpoint(
                "cp-before", "session-1", "ev-nav", "http://localhost/page",
                visible_elements=[
                    _make_element("SPAN", "0%", testid="progress"),
                ],
                timestamp="2026-03-10T10:00:00Z",
            ),
            _make_checkpoint(
                "cp-after", "session-1", "ev-click", "http://localhost/page",
                visible_elements=[
                    _make_element("SPAN", "70%", testid="progress"),
                ],
                timestamp="2026-03-10T10:00:05Z",
            ),
        ]
        # Index checkpoints
        self.gen._index_checkpoints(checkpoints)

        event = _make_event("ev-click", "session-1", url="http://localhost/page")
        assertions = self.gen._click_state_assertions(event)

        assert len(assertions) == 1
        assert 'toContainText("70%")' in assertions[0]

    def test_click_without_checkpoint_returns_empty(self):
        """A click event with no matching checkpoint returns no assertions."""
        self.gen._index_checkpoints([])
        event = _make_event("ev-click", "session-1")
        assertions = self.gen._click_state_assertions(event)
        assert assertions == []

    def test_click_with_no_before_checkpoint_returns_empty(self):
        """When the settle checkpoint is the first in the session, no diff is possible."""
        checkpoints = [
            _make_checkpoint(
                "cp-settle", "session-1", "ev-click", "http://localhost/page",
                visible_elements=[
                    _make_element("SPAN", "70%", testid="progress"),
                ],
            ),
        ]
        self.gen._index_checkpoints(checkpoints)
        event = _make_event("ev-click", "session-1")
        assertions = self.gen._click_state_assertions(event)
        assert assertions == []

    def test_event_without_event_id_returns_empty(self):
        """An event with no event_id returns no assertions."""
        self.gen._index_checkpoints([])
        assertions = self.gen._click_state_assertions({})
        assert assertions == []
