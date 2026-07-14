"""Snapshot tests for Playwright template output.

Verifies that known inputs produce expected script structure.
"""

from testgen.generators.playwright.adapter import PlaywrightGenerator


def _make_flow():
    """Legacy flow using significant_actions only (no representative_events)."""
    return {
        "canonical_pattern": "/login → /dashboard",
        "significant_actions": [
            {"url": "/login", "type": "navigation"},
            {"url": "/login", "type": "fill", "element": 'getByRole("textbox", { name: "Email" })'},
            {"url": "/login", "type": "click", "element": 'getByRole("button", { name: "Sign In" })'},
            {"url": "/dashboard", "type": "navigation"},
        ],
        "popularity_score": 45.0,
        "session_count": 45,
    }


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


def _make_flow_with_events():
    """Flow with representative_events for event-based step building."""
    events = [
        _make_event("navigation", "/",
                     payload={"to_url": "/login"}, sequence=1),
        _make_event("fill", "/login", target={
            "selectors": {"role": {"role": "textbox", "name": "Email"}, "placeholder": "user@example.com"},
            "tag": "INPUT",
        }, payload={"value": "user@example.com"}, sequence=2),
        _make_event("fill", "/login", target={
            "selectors": {"role": {"role": "textbox", "name": "Password"}, "placeholder": "Password"},
            "tag": "INPUT",
        }, payload={"value": "TestPass123!"}, sequence=3),
        _make_event("press_key", "/login", target={
            "selectors": {"role": {"role": "textbox", "name": "Password"}},
            "tag": "INPUT",
        }, payload={"key": "Tab"}, sequence=4),
        _make_event("click", "/login", target={
            "selectors": {"role": {"role": "button", "name": "Sign In"}},
            "tag": "BUTTON",
        }, sequence=5),
        _make_event("navigation", "/login",
                     payload={"to_url": "/dashboard", "trigger": "form_submit"}, sequence=6),
    ]
    return {
        "canonical_pattern": "/login → /dashboard",
        "significant_actions": [
            {"url": "/login", "type": "navigation"},
            {"url": "/login", "type": "fill", "element": 'getByRole("textbox", { name: "Email" })'},
            {"url": "/login", "type": "click", "element": 'getByRole("button", { name: "Sign In" })'},
            {"url": "/dashboard", "type": "navigation"},
        ],
        "representative_events": events,
        "popularity_score": 45.0,
        "session_count": 45,
    }


class TestPlaywrightOutput:
    def test_script_contains_test_describe(self):
        gen = PlaywrightGenerator()
        script = gen.generate_script(_make_flow(), [])
        assert "test.describe(" in script
        assert "test(" in script

    def test_script_contains_playwright_imports(self):
        gen = PlaywrightGenerator()
        script = gen.generate_script(_make_flow(), [])
        assert "import { test, expect } from '@playwright/test'" in script

    def test_script_contains_steps(self):
        gen = PlaywrightGenerator()
        script = gen.generate_script(_make_flow(), [])
        assert "page.goto" in script

    def test_config_contains_base_url(self):
        gen = PlaywrightGenerator()
        config = gen.generate_config("app.example.com")
        assert "https://app.example.com" in config
        assert "defineConfig" in config

    def test_readme_contains_flow_count(self):
        gen = PlaywrightGenerator()
        readme = gen.generate_readme([_make_flow()], "app.example.com")
        assert "Test Files: 1" in readme
        assert "app.example.com" in readme

    def test_variables_file_generated(self):
        gen = PlaywrightGenerator()
        content = gen.generate_variables([_make_flow()])
        assert "testVariables" in content

    def test_flow_to_filename(self):
        gen = PlaywrightGenerator()
        name = gen._flow_to_filename("/login → /dashboard")
        assert name.endswith(".spec.ts")
        assert "login" in name
        assert "dashboard" in name


class TestEventBasedSteps:
    """Tests for event-based step building (representative_events)."""

    def test_first_navigation_skipped_when_matching_entry(self):
        """First navigation should not produce a waitForURL since page.goto handles it."""
        gen = PlaywrightGenerator()
        script = gen.generate_script(_make_flow_with_events(), [])
        # page.goto should be present
        assert "page.goto" in script
        # Should NOT have a waitForURL for /login (entry URL, already navigated by goto)
        assert "waitForURL('/login')" not in script

    def test_press_key_uses_actual_key(self):
        """press_key should use the actual key from the event payload, not hardcoded Enter."""
        gen = PlaywrightGenerator()
        script = gen.generate_script(_make_flow_with_events(), [])
        assert '.press("Tab")' in script

    def test_fill_uses_actual_value(self):
        """fill should use the synthetic value from the event payload."""
        gen = PlaywrightGenerator()
        script = gen.generate_script(_make_flow_with_events(), [])
        assert "user@example.com" in script
        assert "TestPass123!" in script

    def test_click_paired_with_navigation(self):
        """Click followed by navigation should produce click then waitForURL in order."""
        gen = PlaywrightGenerator()
        script = gen.generate_script(_make_flow_with_events(), [])
        # Find positions of click and waitForURL
        click_pos = script.find('.click()')
        wait_pos = script.find("waitForURL")
        assert click_pos > 0, "Script should contain a click"
        assert wait_pos > 0, "Script should contain a waitForURL"
        assert click_pos < wait_pos, "Click should appear before its navigation waitForURL"

    def test_specific_locators_for_textboxes(self):
        """Textbox inputs with role names should produce specific getByRole locators."""
        gen = PlaywrightGenerator()
        script = gen.generate_script(_make_flow_with_events(), [])
        assert 'name: "Email"' in script
        assert 'name: "Password"' in script

    def test_legacy_fallback_still_works(self):
        """Flow without representative_events should use significant_actions."""
        gen = PlaywrightGenerator()
        script = gen.generate_script(_make_flow(), [])
        assert "page.goto" in script
        assert "test.describe(" in script

    def test_fill_generates_toHaveValue_assertion(self):
        """Fill steps should include a toHaveValue assertion for the filled value."""
        gen = PlaywrightGenerator()
        script = gen.generate_script(_make_flow_with_events(), [])
        assert 'toHaveValue("user@example.com")' in script

    def test_orphaned_navigation_skipped(self):
        """Navigation events before any action should be skipped (no orphaned waitForURL)."""
        events = [
            _make_event("navigation", "/",
                         payload={"to_url": "/"}, sequence=1),
            _make_event("navigation", "/",
                         payload={"to_url": "/about"}, sequence=2),
            _make_event("click", "/about", target={
                "selectors": {"role": {"role": "link", "name": "Home"}},
                "tag": "A",
            }, sequence=3),
            _make_event("navigation", "/about",
                         payload={"to_url": "/"}, sequence=4),
        ]
        flow = {
            "canonical_pattern": "/ → /about → /",
            "representative_events": events,
            "popularity_score": 20.0,
            "session_count": 10,
        }
        gen = PlaywrightGenerator()
        script = gen.generate_script(flow, [])
        # Should NOT have waitForURL as the first step (before any click)
        lines = script.split("\n")
        steps = [l.strip() for l in lines if l.strip().startswith("// Step")]
        if steps:
            first_step_idx = lines.index(next(l for l in lines if "// Step 1" in l))
            next_action = lines[first_step_idx + 1].strip()
            assert "waitForURL" not in next_action, "First step should not be an orphaned waitForURL"

    def test_regex_escapes_forward_slashes(self):
        """URLs with forward slashes should be escaped in regex literals."""
        from testgen.generators.playwright.adapter import _escape_regex
        result = _escape_regex("http://localhost:8080/")
        assert "\\/" in result
        # Should not produce // (unescaped double slash)
        assert "//" not in result


class TestNormalizedFillValues:
    """Tests for normalized fill value handling — values are pre-normalized by the tracker."""

    def test_normalized_fill_values_used_directly(self):
        """Fill events with pre-normalized synthetic values should be used as-is."""
        events = [
            _make_event("navigation", "/", payload={"to_url": "/login"}, sequence=1),
            _make_event("fill", "/login", target={
                "selectors": {"role": {"role": "textbox", "name": "Email"}},
                "tag": "INPUT",
            }, payload={"value": "user@example.com"}, sequence=2),
            _make_event("fill", "/login", target={
                "selectors": {"placeholder": "Password"},
                "tag": "INPUT",
            }, payload={"value": "TestPass123!"}, sequence=3),
            _make_event("click", "/login", target={
                "selectors": {"role": {"role": "button", "name": "Sign In"}},
                "tag": "BUTTON",
            }, sequence=4),
        ]
        flow = {
            "canonical_pattern": "/login",
            "representative_events": events,
            "popularity_score": 30.0,
            "session_count": 15,
        }
        gen = PlaywrightGenerator()
        script = gen.generate_script(flow, [])
        assert "user@example.com" in script
        assert "TestPass123!" in script
        assert "[REDACTED]" not in script

    def test_backward_compat_redacted_still_works(self):
        """Old events with [REDACTED] should still produce a usable test value."""
        events = [
            _make_event("navigation", "/", payload={"to_url": "/form"}, sequence=1),
            _make_event("fill", "/form", target={
                "selectors": {"placeholder": "Secret key"},
                "tag": "INPUT",
            }, payload={"value": "[REDACTED]"}, sequence=2),
        ]
        flow = {
            "canonical_pattern": "/form",
            "representative_events": events,
            "popularity_score": 10.0,
            "session_count": 5,
        }
        gen = PlaywrightGenerator()
        script = gen.generate_script(flow, [])
        assert "[REDACTED]" not in script
        assert "TestPass123!" in script


class TestLinkHrefFallback:
    """Tests for link href fallback when navigation events are missing."""

    def test_click_link_with_href_uses_destination_assertions(self):
        """Click on <a> with href should use href destination for assertions
        even without explicit navigation event or next event on different URL."""
        events = [
            _make_event("navigation", "/", payload={"to_url": "http://localhost:8080/"}, sequence=1),
            _make_event("click", "http://localhost:8080/", target={
                "selectors": {"role": {"role": "link", "name": "About Us"}},
                "tag": "A",
                "attributes": {"href": "/about.html"},
            }, sequence=2),
            # No navigation event and no further events
        ]
        checkpoints = [
            {
                "url": "http://localhost:8080/",
                "visible_elements": [
                    {"selectors": {"role": {"role": "heading", "name": "Welcome"}}, "text_content": "Welcome"},
                ],
                "page_title": "Home",
            },
            {
                "url": "http://localhost:8080/about.html",
                "visible_elements": [
                    {"selectors": {"role": {"role": "heading", "name": "About Us"}}, "text_content": "About Us"},
                ],
                "page_title": "About",
            },
        ]
        flow = {
            "canonical_pattern": "http://localhost:8080/ → http://localhost:8080/about.html",
            "representative_events": events,
            "popularity_score": 20.0,
            "session_count": 10,
        }
        gen = PlaywrightGenerator()
        script = gen.generate_script(flow, checkpoints)
        # Should use About page assertions (from href), not Home page
        assert "About Us" in script or "About" in script

    def test_link_destination_resolves_relative_urls(self):
        """_link_destination should resolve relative hrefs against the current URL."""
        from testgen.generators.playwright.adapter import _link_destination
        event = {
            "target": {
                "tag": "A",
                "attributes": {"href": "/about.html"},
            },
        }
        result = _link_destination(event, "http://localhost:8080/page.html")
        assert result == "http://localhost:8080/about.html"

    def test_link_destination_ignores_hash_links(self):
        """_link_destination should return None for anchor-only links."""
        from testgen.generators.playwright.adapter import _link_destination
        event = {
            "target": {
                "tag": "A",
                "attributes": {"href": "#section"},
            },
        }
        result = _link_destination(event, "http://localhost:8080/")
        assert result is None

    def test_link_destination_ignores_non_links(self):
        """_link_destination should return None for non-<a> elements."""
        from testgen.generators.playwright.adapter import _link_destination
        event = {
            "target": {
                "tag": "BUTTON",
                "attributes": {},
            },
        }
        result = _link_destination(event, "http://localhost:8080/")
        assert result is None


class TestGlobalChromeFiltering:
    """Tests for filtering global chrome elements from assertions."""

    def test_global_elements_filtered(self):
        """Elements appearing in every checkpoint should be filtered from assertions."""
        checkpoints = [
            {
                "url": "/page1",
                "visible_elements": [
                    {"selectors": {"role": {"role": "link", "name": "Home"}}, "text_content": "Home"},
                    {"selectors": {"role": {"role": "heading", "name": "Page 1 Title"}}, "text_content": "Page 1 Title"},
                ],
                "page_title": "Page 1",
            },
            {
                "url": "/page2",
                "visible_elements": [
                    {"selectors": {"role": {"role": "link", "name": "Home"}}, "text_content": "Home"},
                    {"selectors": {"role": {"role": "heading", "name": "Page 2 Title"}}, "text_content": "Page 2 Title"},
                ],
                "page_title": "Page 2",
            },
        ]
        gen = PlaywrightGenerator()
        gen._index_checkpoints(checkpoints)
        # "Home" link appears in both → should be global
        assert 'role:link:Home' in gen._global_elements
        # Page-specific headings should NOT be global
        assert 'role:heading:Page 1 Title' not in gen._global_elements


class TestAssertionBugFixes:
    """Tests for assertion placement bug fixes."""

    def test_click_implicit_nav_uses_destination_assertions(self):
        """Click causing implicit navigation should get destination page assertions."""
        events = [
            _make_event("navigation", "/", payload={"to_url": "/"}, sequence=1),
            _make_event("click", "/", target={
                "selectors": {"role": {"role": "link", "name": "About"}},
                "tag": "A",
            }, sequence=2),
            # No explicit navigation event — next event is on /about
            _make_event("click", "/about", target={
                "selectors": {"role": {"role": "link", "name": "Contact"}},
                "tag": "A",
            }, sequence=3),
        ]
        checkpoints = [
            {
                "url": "/",
                "visible_elements": [
                    {"selectors": {"role": {"role": "heading", "name": "Welcome Home"}}, "text_content": "Welcome Home"},
                ],
                "page_title": "Home Page",
            },
            {
                "url": "/about",
                "visible_elements": [
                    {"selectors": {"role": {"role": "heading", "name": "About Us"}}, "text_content": "About Us"},
                ],
                "page_title": "About Page",
            },
        ]
        flow = {
            "canonical_pattern": "/ → /about",
            "representative_events": events,
            "popularity_score": 30.0,
            "session_count": 15,
        }
        gen = PlaywrightGenerator()
        script = gen.generate_script(flow, checkpoints)
        # First click (on /) should get /about's assertions via implicit nav
        assert "About Us" in script
        # Source page heading should NOT appear (replaced by destination assertions)
        assert "Welcome Home" not in script

    def test_same_page_steps_skip_redundant_checkpoint_assertions(self):
        """Multiple steps on same page should not repeat checkpoint assertions."""
        events = [
            _make_event("navigation", "/", payload={"to_url": "/form"}, sequence=1),
            _make_event("fill", "/form", target={
                "selectors": {"role": {"role": "textbox", "name": "First Name"}},
                "tag": "INPUT",
            }, payload={"value": "test value"}, sequence=2),
            _make_event("fill", "/form", target={
                "selectors": {"role": {"role": "textbox", "name": "Last Name"}},
                "tag": "INPUT",
            }, payload={"value": "test value"}, sequence=3),
            _make_event("fill", "/form", target={
                "selectors": {"role": {"role": "textbox", "name": "Email"}},
                "tag": "INPUT",
            }, payload={"value": "user@example.com"}, sequence=4),
        ]
        checkpoints = [
            {
                "url": "/form",
                "visible_elements": [
                    {"selectors": {"role": {"role": "heading", "name": "Registration Form"}}, "text_content": "Registration Form"},
                ],
                "page_title": "Register",
            },
        ]
        flow = {
            "canonical_pattern": "/form",
            "representative_events": events,
            "popularity_score": 25.0,
            "session_count": 10,
        }
        gen = PlaywrightGenerator()
        script = gen.generate_script(flow, checkpoints)
        # Heading should appear only once (first fill step)
        assert script.count("Registration Form") == 1
        # State assertions should still appear for all fills
        assert 'toHaveValue("test value")' in script
        assert 'toHaveValue("user@example.com")' in script

    def test_paired_nav_step_only_has_url_assertion(self):
        """When click is paired with navigation, nav step should only assert the URL."""
        events = [
            _make_event("navigation", "/", payload={"to_url": "/page1"}, sequence=1),
            _make_event("click", "/page1", target={
                "selectors": {"role": {"role": "link", "name": "Go to Page 2"}},
                "tag": "A",
            }, sequence=2),
            _make_event("navigation", "/page1",
                         payload={"to_url": "/page2"}, sequence=3),
        ]
        checkpoints = [
            {
                "url": "/page2",
                "visible_elements": [
                    {"selectors": {"role": {"role": "heading", "name": "Page 2"}}, "text_content": "Page 2"},
                ],
                "page_title": "Page 2 Title",
            },
        ]
        flow = {
            "canonical_pattern": "/page1 → /page2",
            "representative_events": events,
            "popularity_score": 30.0,
            "session_count": 10,
        }
        gen = PlaywrightGenerator()
        script = gen.generate_script(flow, checkpoints)
        # The click step should have the destination page assertions
        assert "Page 2" in script
        # Find the waitForURL line and verify no toBeVisible follows it
        lines = script.split("\n")
        wait_line_idx = None
        for idx, line in enumerate(lines):
            if "waitForURL" in line:
                wait_line_idx = idx
                break
        assert wait_line_idx is not None, "Should have a waitForURL step"
        # Lines after waitForURL (up to next step or end) should not have toBeVisible
        nav_step_lines = lines[wait_line_idx:wait_line_idx + 5]
        nav_step_text = "\n".join(nav_step_lines)
        assert "toBeVisible" not in nav_step_text

    def test_peek_navigation_skips_intermediate_events(self):
        """Navigation should be found even with blur/change/submit between click and nav."""
        events = [
            _make_event("navigation", "/", payload={"to_url": "/form"}, sequence=1),
            _make_event("click", "/form", target={
                "selectors": {"role": {"role": "button", "name": "Submit"}},
                "tag": "BUTTON",
            }, sequence=2),
            _make_event("blur", "/form", target={
                "selectors": {"role": {"role": "button", "name": "Submit"}},
                "tag": "BUTTON",
            }, sequence=3),
            _make_event("submit", "/form", target={
                "selectors": {"role": {"role": "form"}},
                "tag": "FORM",
            }, sequence=4),
            _make_event("navigation", "/form",
                         payload={"to_url": "/success"}, sequence=5),
        ]
        flow = {
            "canonical_pattern": "/form → /success",
            "representative_events": events,
            "popularity_score": 20.0,
            "session_count": 10,
        }
        gen = PlaywrightGenerator()
        script = gen.generate_script(flow, [])
        # Click should be paired with navigation (not blocked by blur/submit)
        assert "waitForURL" in script
        click_pos = script.find(".click()")
        wait_pos = script.find("waitForURL")
        assert click_pos < wait_pos, "Click should be paired with the navigation"

    def test_multi_page_flow_assertion_placement(self):
        """Full flow: home -> about (implicit nav) -> contact (explicit nav)."""
        events = [
            _make_event("navigation", "/", payload={"to_url": "/"}, sequence=1),
            _make_event("click", "/", target={
                "selectors": {"role": {"role": "link", "name": "About"}},
                "tag": "A",
            }, sequence=2),
            # Implicit navigation (no nav event, next event is on /about)
            _make_event("click", "/about", target={
                "selectors": {"role": {"role": "link", "name": "Contact"}},
                "tag": "A",
            }, sequence=3),
            _make_event("navigation", "/about",
                         payload={"to_url": "/contact"}, sequence=4),
        ]
        checkpoints = [
            {
                "url": "/",
                "visible_elements": [
                    {"selectors": {"role": {"role": "heading", "name": "Home Heading"}}, "text_content": "Home Heading"},
                ],
                "page_title": "Home",
            },
            {
                "url": "/about",
                "visible_elements": [
                    {"selectors": {"role": {"role": "heading", "name": "About Heading"}}, "text_content": "About Heading"},
                ],
                "page_title": "About",
            },
            {
                "url": "/contact",
                "visible_elements": [
                    {"selectors": {"role": {"role": "heading", "name": "Contact Heading"}}, "text_content": "Contact Heading"},
                ],
                "page_title": "Contact",
            },
        ]
        flow = {
            "canonical_pattern": "/ → /about → /contact",
            "representative_events": events,
            "popularity_score": 40.0,
            "session_count": 20,
        }
        gen = PlaywrightGenerator()
        script = gen.generate_script(flow, checkpoints)
        # Home heading should NOT appear (click navigates away implicitly)
        assert "Home Heading" not in script
        # About heading should appear (destination of first click)
        assert "About Heading" in script
        # Contact heading should appear (destination of second click)
        assert "Contact Heading" in script
        # About heading should appear only once (dedup + nav-only assertions)
        assert script.count("About Heading") == 1


class TestVariableNameGeneration:
    """Tests for variable name generation from element selectors."""

    def test_special_chars_stripped_from_variable_names(self):
        """Variable names should be valid JS identifiers — no @ or . chars."""
        from testgen.generators.variables import _to_camel_case
        assert _to_camel_case("john@example.com") == "johnExampleCom"

    def test_hyphenated_names(self):
        from testgen.generators.variables import _to_camel_case
        assert _to_camel_case("first-name") == "firstName"

    def test_underscored_names(self):
        from testgen.generators.variables import _to_camel_case
        assert _to_camel_case("user_name") == "userName"

    def test_empty_string_returns_unnamed(self):
        from testgen.generators.variables import _to_camel_case
        assert _to_camel_case("@#$") == "unnamed"


class TestClickCheckCollapsing:
    """Tests for click→check/uncheck collapsing."""

    def test_click_then_check_collapses(self):
        """Click followed by check on same element should collapse to just check."""
        flow = {
            "canonical_pattern": "http://localhost/interactive.html",
            "popularity_score": 10.0,
            "session_count": 5,
            "representative_events": [
                _make_event("navigation", "http://localhost/interactive.html",
                            payload={"to_url": "http://localhost/interactive.html"}, sequence=1),
                _make_event("click", "http://localhost/interactive.html",
                            target={"tag": "INPUT", "attributes": {"id": "toggle-dark", "type": "checkbox"},
                                    "text_content": ""}, sequence=2),
                _make_event("check", "http://localhost/interactive.html",
                            target={"tag": "INPUT", "attributes": {"id": "toggle-dark", "type": "checkbox"},
                                    "text_content": ""},
                            payload={"checked": True}, sequence=3),
            ],
        }
        gen = PlaywrightGenerator()
        script = gen.generate_script(flow, [])

        # Should NOT have a click step — only the check step
        lines = script.split("\n")
        click_lines = [l for l in lines if ".click()" in l]
        check_lines = [l for l in lines if ".check()" in l]
        assert len(click_lines) == 0, "Click should be collapsed into check"
        assert len(check_lines) == 1

    def test_click_then_uncheck_collapses(self):
        """Click followed by uncheck on same element should collapse to just uncheck."""
        flow = {
            "canonical_pattern": "http://localhost/interactive.html",
            "popularity_score": 10.0,
            "session_count": 5,
            "representative_events": [
                _make_event("navigation", "http://localhost/interactive.html",
                            payload={"to_url": "http://localhost/interactive.html"}, sequence=1),
                _make_event("click", "http://localhost/interactive.html",
                            target={"tag": "INPUT", "attributes": {"id": "toggle-dark", "type": "checkbox"},
                                    "text_content": ""}, sequence=2),
                _make_event("check", "http://localhost/interactive.html",
                            target={"tag": "INPUT", "attributes": {"id": "toggle-dark", "type": "checkbox"},
                                    "text_content": ""},
                            payload={"checked": False}, sequence=3),
            ],
        }
        gen = PlaywrightGenerator()
        script = gen.generate_script(flow, [])

        lines = script.split("\n")
        click_lines = [l for l in lines if ".click()" in l]
        uncheck_lines = [l for l in lines if ".uncheck()" in l]
        assert len(click_lines) == 0, "Click should be collapsed into uncheck"
        assert len(uncheck_lines) == 1

    def test_click_on_different_element_not_collapsed(self):
        """Click followed by check on DIFFERENT element should NOT collapse."""
        flow = {
            "canonical_pattern": "http://localhost/interactive.html",
            "popularity_score": 10.0,
            "session_count": 5,
            "representative_events": [
                _make_event("navigation", "http://localhost/interactive.html",
                            payload={"to_url": "http://localhost/interactive.html"}, sequence=1),
                _make_event("click", "http://localhost/interactive.html",
                            target={"tag": "BUTTON", "attributes": {"id": "some-button"},
                                    "text_content": "Submit"}, sequence=2),
                _make_event("check", "http://localhost/interactive.html",
                            target={"tag": "INPUT", "attributes": {"id": "toggle-dark", "type": "checkbox"},
                                    "text_content": ""},
                            payload={"checked": True}, sequence=3),
            ],
        }
        gen = PlaywrightGenerator()
        script = gen.generate_script(flow, [])

        lines = script.split("\n")
        click_lines = [l for l in lines if ".click()" in l]
        check_lines = [l for l in lines if ".check()" in l]
        assert len(click_lines) >= 1, "Click on different element should be kept"
        assert len(check_lines) >= 1


class TestRepetitionCollapsing:
    """Tests for collapsing consecutive identical click steps."""

    def test_seven_identical_clicks_collapsed(self):
        """Seven consecutive identical clicks should become a for loop."""
        events = [
            _make_event("navigation", "http://localhost/interactive.html",
                        payload={"to_url": "http://localhost/interactive.html"}, sequence=1),
        ]
        for i in range(7):
            events.append(
                _make_event("click", "http://localhost/interactive.html",
                            target={"tag": "BUTTON", "attributes": {},
                                    "text_content": "+ 10%", "role": "button"},
                            sequence=i + 2)
            )
        flow = {
            "canonical_pattern": "http://localhost/interactive.html",
            "popularity_score": 10.0,
            "session_count": 5,
            "representative_events": events,
        }
        gen = PlaywrightGenerator()
        script = gen.generate_script(flow, [])

        assert "for (let i = 0; i < 7; i++)" in script
        # Should only have ONE click line for + 10%, inside the loop
        click_lines = [l.strip() for l in script.split("\n")
                       if ".click()" in l and "10%" in l]
        assert len(click_lines) == 1

    def test_two_identical_clicks_not_collapsed(self):
        """Two consecutive identical clicks should NOT be collapsed (threshold is 3)."""
        events = [
            _make_event("navigation", "http://localhost/interactive.html",
                        payload={"to_url": "http://localhost/interactive.html"}, sequence=1),
            _make_event("click", "http://localhost/interactive.html",
                        target={"tag": "BUTTON", "attributes": {},
                                "text_content": "+ 10%", "role": "button"}, sequence=2),
            _make_event("click", "http://localhost/interactive.html",
                        target={"tag": "BUTTON", "attributes": {},
                                "text_content": "+ 10%", "role": "button"}, sequence=3),
        ]
        flow = {
            "canonical_pattern": "http://localhost/interactive.html",
            "popularity_score": 10.0,
            "session_count": 5,
            "representative_events": events,
        }
        gen = PlaywrightGenerator()
        script = gen.generate_script(flow, [])

        assert "for (let i" not in script
        click_lines = [l for l in script.split("\n") if ".click()" in l and "10%" in l]
        assert len(click_lines) == 2


class TestNavOnlyGeneration:
    """Tests for nav-only test mode."""

    def test_nav_only_stops_before_interactions(self):
        """Nav-only mode should not include fill/check steps."""
        flow = {
            "canonical_pattern": "http://localhost/ → http://localhost/forms.html",
            "popularity_score": 10.0,
            "session_count": 5,
            "representative_events": [
                _make_event("navigation", "http://localhost/",
                            payload={"to_url": "http://localhost/"}, sequence=1),
                _make_event("click", "http://localhost/",
                            target={"tag": "A", "attributes": {"href": "forms.html"},
                                    "text_content": "Forms"}, sequence=2),
                _make_event("navigation", "http://localhost/forms.html",
                            payload={"to_url": "http://localhost/forms.html"}, sequence=3),
                _make_event("fill", "http://localhost/forms.html",
                            target={"tag": "INPUT", "attributes": {"id": "name"},
                                    "text_content": ""},
                            payload={"value": "Alice"}, sequence=4),
                _make_event("click", "http://localhost/forms.html",
                            target={"tag": "BUTTON", "attributes": {},
                                    "text_content": "Submit"}, sequence=5),
            ],
        }
        gen = PlaywrightGenerator()
        script = gen.generate_script(flow, [], test_mode="nav_only")

        assert ".fill(" not in script
        assert "Submit" not in script
        assert "(navigation)" in script


class TestConfigBaseUrl:
    """Tests for base URL extraction from flow data."""

    def test_config_extracts_base_url_from_flows(self):
        """Config should use actual protocol/port from flow URLs."""
        flows = [{
            "canonical_pattern": "http://localhost:8080/",
            "representative_events": [
                _make_event("navigation", "http://localhost:8080/",
                            payload={"to_url": "http://localhost:8080/"}, sequence=1),
            ],
            "popularity_score": 50.0,
            "session_count": 25,
        }]
        gen = PlaywrightGenerator()
        config = gen.generate_config("localhost:8080", flows=flows)
        assert "http://localhost:8080" in config
        # Should NOT have https
        assert "https://localhost" not in config

    def test_config_falls_back_to_https_without_flows(self):
        """Config without flows should default to https://domain."""
        gen = PlaywrightGenerator()
        config = gen.generate_config("app.example.com")
        assert "https://app.example.com" in config


class TestClickStateAssertionOutput:
    """Tests for click state assertions via checkpoint diffing in generated output."""

    def test_single_click_with_settle_checkpoint_generates_assertion(self):
        """A click with a settle checkpoint that shows text change should include assertion."""
        events = [
            _make_event("navigation", "http://localhost/interactive.html",
                        payload={"to_url": "http://localhost/interactive.html"}, sequence=1),
            _make_event("click", "http://localhost/interactive.html",
                        target={"tag": "BUTTON", "attributes": {},
                                "text_content": "+ 10%", "role": "button"},
                        sequence=2),
        ]
        # Checkpoint before (from navigation) and after (from click settle)
        checkpoints = [
            {
                "checkpoint_id": "cp-nav",
                "session_id": "session-1",
                "trigger_event_id": "evt-1",
                "url": "http://localhost/interactive.html",
                "timestamp": "2026-03-10T10:00:00Z",
                "visible_elements": [
                    {"selectors": {"testid": "progress"}, "tag": "SPAN", "text_content": "0%"},
                    {"selectors": {"role": {"role": "button", "name": "+ 10%"}}, "tag": "BUTTON", "text_content": "+ 10%"},
                ],
                "page_title": "Interactive",
            },
            {
                "checkpoint_id": "cp-settle",
                "session_id": "session-1",
                "trigger_event_id": "evt-2",
                "url": "http://localhost/interactive.html",
                "timestamp": "2026-03-10T10:00:01Z",
                "visible_elements": [
                    {"selectors": {"testid": "progress"}, "tag": "SPAN", "text_content": "10%"},
                    {"selectors": {"role": {"role": "button", "name": "+ 10%"}}, "tag": "BUTTON", "text_content": "+ 10%"},
                ],
                "page_title": "Interactive",
            },
        ]
        flow = {
            "canonical_pattern": "http://localhost/interactive.html",
            "popularity_score": 10.0,
            "session_count": 5,
            "representative_events": events,
        }
        gen = PlaywrightGenerator()
        script = gen.generate_script(flow, checkpoints)
        assert 'toContainText("10%")' in script

    def test_collapsed_clicks_get_post_loop_assertion(self):
        """Collapsed repetitive clicks should generate assertions after the for loop."""
        events = [
            _make_event("navigation", "http://localhost/interactive.html",
                        payload={"to_url": "http://localhost/interactive.html"}, sequence=1),
        ]
        for i in range(7):
            events.append(
                _make_event("click", "http://localhost/interactive.html",
                            target={"tag": "BUTTON", "attributes": {},
                                    "text_content": "+ 10%", "role": "button"},
                            sequence=i + 2)
            )
        # Navigation checkpoint (before) and settle checkpoint (after last click)
        checkpoints = [
            {
                "checkpoint_id": "cp-nav",
                "session_id": "session-1",
                "trigger_event_id": "evt-1",
                "url": "http://localhost/interactive.html",
                "timestamp": "2026-03-10T10:00:00Z",
                "visible_elements": [
                    {"selectors": {"testid": "progress"}, "tag": "SPAN", "text_content": "0%"},
                ],
                "page_title": "Interactive",
            },
            {
                "checkpoint_id": "cp-settle",
                "session_id": "session-1",
                "trigger_event_id": "evt-8",  # Last click event
                "url": "http://localhost/interactive.html",
                "timestamp": "2026-03-10T10:00:05Z",
                "visible_elements": [
                    {"selectors": {"testid": "progress"}, "tag": "SPAN", "text_content": "70%"},
                ],
                "page_title": "Interactive",
            },
        ]
        flow = {
            "canonical_pattern": "http://localhost/interactive.html",
            "popularity_score": 10.0,
            "session_count": 5,
            "representative_events": events,
        }
        gen = PlaywrightGenerator()
        script = gen.generate_script(flow, checkpoints)

        # Should have the for loop
        assert "for (let i = 0; i < 7; i++)" in script
        # Should have assertion on the changed progress value AFTER the loop
        assert 'toContainText("70%")' in script
        # The assertion should appear after the closing brace of the for loop
        loop_close = script.find("    }")
        assertion_pos = script.find('toContainText("70%")')
        assert assertion_pos > loop_close, "Assertion should appear after the for loop"

    def test_no_checkpoint_no_assertion(self):
        """Without settle checkpoints, click events should generate no state assertion."""
        events = [
            _make_event("navigation", "http://localhost/interactive.html",
                        payload={"to_url": "http://localhost/interactive.html"}, sequence=1),
            _make_event("click", "http://localhost/interactive.html",
                        target={"tag": "BUTTON", "attributes": {},
                                "text_content": "+ 10%", "role": "button"},
                        sequence=2),
        ]
        flow = {
            "canonical_pattern": "http://localhost/interactive.html",
            "popularity_score": 10.0,
            "session_count": 5,
            "representative_events": events,
        }
        gen = PlaywrightGenerator()
        script = gen.generate_script(flow, [])
        # No checkpoint → no toContainText or toBeVisible assertions
        assert "toContainText" not in script


class TestNonLinkClickNotPairedWithNavigation:
    """Issue #1: BUTTON clicks should not be paired with following navigation events."""

    def test_button_click_not_paired_with_nav(self):
        """A BUTTON click followed by navigation should NOT be paired with it.

        The navigation may still appear as a standalone step, but the click
        step itself should not get destination-page assertions.
        """
        events = [
            _make_event("navigation", "http://localhost/interactive.html",
                        payload={"to_url": "http://localhost/interactive.html"}, sequence=1),
            _make_event("click", "http://localhost/interactive.html",
                        target={"tag": "BUTTON", "attributes": {},
                                "text_content": "Toggle", "role": "button"},
                        sequence=2),
            # This navigation is caused by something else (e.g. JS redirect), not by the button
            _make_event("navigation", "http://localhost/interactive.html",
                        payload={"to_url": "http://localhost/other.html"}, sequence=3),
        ]
        checkpoints = [
            {
                "url": "http://localhost/other.html",
                "visible_elements": [
                    {"selectors": {"role": {"role": "heading", "name": "Other Page"}}, "text_content": "Other Page"},
                ],
                "page_title": "Other",
            },
        ]
        flow = {
            "canonical_pattern": "http://localhost/interactive.html",
            "popularity_score": 10.0,
            "session_count": 5,
            "representative_events": events,
        }
        gen = PlaywrightGenerator()
        script = gen.generate_script(flow, checkpoints)
        # Click step should NOT get destination-page assertions (Other Page)
        # The click and navigation should be separate steps
        lines = script.split("\n")
        click_line_idx = None
        for idx, line in enumerate(lines):
            if ".click()" in line:
                click_line_idx = idx
                break
        assert click_line_idx is not None, "Should have a click step"
        # Check lines between click and waitForURL — click step should NOT have Other Page assertion
        click_section = "\n".join(lines[click_line_idx:click_line_idx + 3])
        assert "Other Page" not in click_section

    def test_link_click_still_paired_with_nav(self):
        """An <a> click followed by navigation SHOULD still produce waitForURL."""
        events = [
            _make_event("navigation", "http://localhost/",
                        payload={"to_url": "http://localhost/"}, sequence=1),
            _make_event("click", "http://localhost/",
                        target={"tag": "A", "attributes": {"href": "/about.html"},
                                "text_content": "About"},
                        sequence=2),
            _make_event("navigation", "http://localhost/",
                        payload={"to_url": "http://localhost/about.html"}, sequence=3),
        ]
        flow = {
            "canonical_pattern": "/ → /about",
            "representative_events": events,
            "popularity_score": 10.0,
            "session_count": 5,
        }
        gen = PlaywrightGenerator()
        script = gen.generate_script(flow, [])
        assert "waitForURL" in script


class TestFilenameCollision:
    """Issue #2: Long filenames should be truncated with a hash suffix."""

    def test_long_pattern_truncated_with_hash(self):
        """Patterns producing filenames > 180 chars should be truncated + hashed."""
        long_pattern = " → ".join(f"http://localhost/page{i}.html" for i in range(20))
        gen = PlaywrightGenerator()
        filename = gen._flow_to_filename(long_pattern)
        assert len(filename) < 250
        assert filename.endswith(".spec.ts")

    def test_short_pattern_unchanged(self):
        """Short patterns should not be truncated."""
        gen = PlaywrightGenerator()
        filename = gen._flow_to_filename("/login → /dashboard")
        assert "login" in filename
        assert "dashboard" in filename
        assert len(filename) < 100

    def test_different_long_patterns_get_different_filenames(self):
        """Two long patterns that truncate to the same prefix get different hashes."""
        base = " → ".join(f"http://localhost/page{i}.html" for i in range(20))
        pattern_a = base + " → http://localhost/final-a.html"
        pattern_b = base + " → http://localhost/final-b.html"
        gen = PlaywrightGenerator()
        filename_a = gen._flow_to_filename(pattern_a)
        filename_b = gen._flow_to_filename(pattern_b)
        assert filename_a != filename_b


class TestVariableReferences:
    """Issue #5: Fill steps should use testVariables references when variable_map is provided."""

    def test_fill_uses_variable_reference(self):
        """Fill step for a mapped element uses testVariables.varName instead of literal."""
        events = [
            _make_event("navigation", "/form", payload={"to_url": "/form"}, sequence=1),
            _make_event("fill", "/form", target={
                "selectors": {"role": {"role": "textbox", "name": "Full Name"}},
                "tag": "INPUT",
            }, payload={"value": "Alice"}, sequence=2),
        ]
        flow = {
            "canonical_pattern": "/form",
            "representative_events": events,
            "popularity_score": 10.0,
            "session_count": 5,
        }
        variable_map = {
            'getByRole("textbox", { name: "Full Name" })': "fullName",
        }
        gen = PlaywrightGenerator()
        script = gen.generate_script(flow, [], variable_map=variable_map)
        assert "testVariables.fullName" in script
        assert '"Alice"' not in script  # literal should be replaced

    def test_fill_without_variable_map_uses_literal(self):
        """Fill step without variable_map should use literal value."""
        events = [
            _make_event("navigation", "/form", payload={"to_url": "/form"}, sequence=1),
            _make_event("fill", "/form", target={
                "selectors": {"role": {"role": "textbox", "name": "Full Name"}},
                "tag": "INPUT",
            }, payload={"value": "Alice"}, sequence=2),
        ]
        flow = {
            "canonical_pattern": "/form",
            "representative_events": events,
            "popularity_score": 10.0,
            "session_count": 5,
        }
        gen = PlaywrightGenerator()
        script = gen.generate_script(flow, [])
        assert '"Alice"' in script
        assert "testVariables" not in script

    def test_has_variables_flag_enables_import(self):
        """When variable_map produces references, the import line should appear."""
        events = [
            _make_event("navigation", "/form", payload={"to_url": "/form"}, sequence=1),
            _make_event("fill", "/form", target={
                "selectors": {"role": {"role": "textbox", "name": "Email"}},
                "tag": "INPUT",
            }, payload={"value": "a@b.com"}, sequence=2),
        ]
        flow = {
            "canonical_pattern": "/form",
            "representative_events": events,
            "popularity_score": 10.0,
            "session_count": 5,
        }
        variable_map = {
            'getByRole("textbox", { name: "Email" })': "email",
        }
        gen = PlaywrightGenerator()
        script = gen.generate_script(flow, [], variable_map=variable_map)
        assert "import { testVariables }" in script

    def test_toHaveValue_uses_variable_reference(self):
        """toHaveValue assertions should also use variable references."""
        events = [
            _make_event("navigation", "/form", payload={"to_url": "/form"}, sequence=1),
            _make_event("fill", "/form", target={
                "selectors": {"role": {"role": "textbox", "name": "Full Name"}},
                "tag": "INPUT",
            }, payload={"value": "Alice"}, sequence=2),
        ]
        flow = {
            "canonical_pattern": "/form",
            "representative_events": events,
            "popularity_score": 10.0,
            "session_count": 5,
        }
        variable_map = {
            'getByRole("textbox", { name: "Full Name" })': "fullName",
        }
        gen = PlaywrightGenerator()
        script = gen.generate_script(flow, [], variable_map=variable_map)
        assert "toHaveValue(testVariables.fullName)" in script

    def test_build_variable_map_extracts_from_flows(self):
        """build_variable_map should map element locators to variable names."""
        flows = [{
            "canonical_pattern": "/form",
            "representative_events": [
                _make_event("fill", "/form", target={
                    "selectors": {"role": {"role": "textbox", "name": "Full Name *"}},
                    "tag": "INPUT",
                }, payload={"value": "Alice"}, sequence=1),
                _make_event("fill", "/form", target={
                    "selectors": {"role": {"role": "textbox", "name": "Email"}},
                    "tag": "INPUT",
                }, payload={"value": "a@b.com"}, sequence=2),
            ],
            "popularity_score": 10.0,
            "session_count": 5,
        }]
        gen = PlaywrightGenerator()
        var_map = gen.build_variable_map(flows)
        # Should have entries for both fields
        assert len(var_map) == 2
        assert "fullName" in var_map.values()
        assert "email" in var_map.values()


class TestVariableNameFromGetByRole:
    """Issue #5: _element_to_var_name should extract name from getByRole, not role type."""

    def test_getByRole_extracts_name(self):
        """getByRole with name option should use the name, not the role type."""
        from testgen.generators.variables import _element_to_var_name
        result = _element_to_var_name('getByRole("textbox", { name: "Full Name *" })')
        assert result == "fullName"

    def test_getByRole_without_name_uses_role(self):
        """getByRole without name option falls back to role type."""
        from testgen.generators.variables import _element_to_var_name
        result = _element_to_var_name('getByRole("textbox")')
        assert result == "textbox"

    def test_getByLabel(self):
        from testgen.generators.variables import _element_to_var_name
        result = _element_to_var_name('getByLabel("Email Address")')
        assert result == "emailAddress"

    def test_getByPlaceholder(self):
        from testgen.generators.variables import _element_to_var_name
        result = _element_to_var_name('getByPlaceholder("Enter your name")')
        assert result == "enterYourName"

    def test_different_fields_get_different_names(self):
        """Two textbox fields with different names should produce different variable names."""
        from testgen.generators.variables import _element_to_var_name
        name1 = _element_to_var_name('getByRole("textbox", { name: "Full Name" })')
        name2 = _element_to_var_name('getByRole("textbox", { name: "Email" })')
        assert name1 != name2
        assert name1 == "fullName"
        assert name2 == "email"


class TestAssertionMisattribution:
    """Fix: link clicks should use the link's href destination for assertions,
    not a misaligned navigation event's to_url."""

    def test_misaligned_nav_uses_link_href(self):
        """When nav event's to_url differs from link's href, use href for assertions."""
        events = [
            _make_event("navigation", "http://localhost/",
                        payload={"to_url": "http://localhost/"}, sequence=1),
            # Click Forms link (href → forms.html)
            _make_event("click", "http://localhost/", target={
                "selectors": {"role": {"role": "link", "name": "Forms"}},
                "tag": "A",
                "attributes": {"href": "/forms.html"},
            }, sequence=2),
            # Misaligned navigation: says interactive.html (wrong!)
            _make_event("navigation", "http://localhost/",
                        payload={"to_url": "http://localhost/interactive.html"}, sequence=3),
        ]
        checkpoints = [
            {
                "url": "http://localhost/forms.html",
                "visible_elements": [
                    {"selectors": {"role": {"role": "heading", "name": "Contact Form"}},
                     "text_content": "Contact Form"},
                ],
                "page_title": "Forms",
            },
            {
                "url": "http://localhost/interactive.html",
                "visible_elements": [
                    {"selectors": {"role": {"role": "heading", "name": "Interactive"}},
                     "text_content": "Interactive"},
                ],
                "page_title": "Interactive",
            },
        ]
        flow = {
            "canonical_pattern": "/ → /forms",
            "representative_events": events,
            "popularity_score": 10.0,
            "session_count": 5,
        }
        gen = PlaywrightGenerator()
        script = gen.generate_script(flow, checkpoints)
        # Should use forms.html assertions (from href), NOT interactive.html
        assert "forms" in script.lower()
        # Should NOT have interactive.html assertions for the Forms click
        assert "Interactive" not in script

    def test_matching_nav_unchanged(self):
        """When nav event matches link's href, behavior unchanged."""
        events = [
            _make_event("navigation", "http://localhost/",
                        payload={"to_url": "http://localhost/"}, sequence=1),
            _make_event("click", "http://localhost/", target={
                "selectors": {"role": {"role": "link", "name": "About"}},
                "tag": "A",
                "attributes": {"href": "/about.html"},
            }, sequence=2),
            _make_event("navigation", "http://localhost/",
                        payload={"to_url": "http://localhost/about.html"}, sequence=3),
        ]
        checkpoints = [
            {
                "url": "http://localhost/about.html",
                "visible_elements": [
                    {"selectors": {"role": {"role": "heading", "name": "About Us"}},
                     "text_content": "About Us"},
                ],
                "page_title": "About",
            },
        ]
        flow = {
            "canonical_pattern": "/ → /about",
            "representative_events": events,
            "popularity_score": 10.0,
            "session_count": 5,
        }
        gen = PlaywrightGenerator()
        script = gen.generate_script(flow, checkpoints)
        assert "About Us" in script
        assert "about" in script.lower()


class TestReadmeSessionCount:
    """Fix: README should report total sessions from all flows before dedup."""

    def test_total_sessions_parameter_used(self):
        """When total_sessions is passed, it should be used instead of sum of surviving flows."""
        flows = [
            {
                "canonical_pattern": "/flow1",
                "popularity_score": 20.0,
                "session_count": 10,
                "_test_mode": "full",
            },
        ]
        gen = PlaywrightGenerator()
        # total_sessions=49 (from all flows before dedup), but surviving flows only sum to 10
        readme = gen.generate_readme(flows, "example.com", dedup_strategy="lean",
                                     original_flow_count=5, total_sessions=49)
        assert "49 recorded user sessions" in readme

    def test_fallback_when_no_total_sessions(self):
        """Without total_sessions, should fall back to sum of surviving flows."""
        flows = [
            {
                "canonical_pattern": "/flow1",
                "popularity_score": 20.0,
                "session_count": 10,
                "_test_mode": "full",
            },
            {
                "canonical_pattern": "/flow2",
                "popularity_score": 15.0,
                "session_count": 8,
                "_test_mode": "full",
            },
        ]
        gen = PlaywrightGenerator()
        readme = gen.generate_readme(flows, "example.com")
        assert "18 recorded user sessions" in readme


class TestReadmeStrategyLabel:
    """Fix: README should show dedup_strategy label for all strategies including full."""

    def test_full_strategy_shown(self):
        """The 'full' dedup strategy should now appear in README."""
        flows = [
            {
                "canonical_pattern": "/flow1",
                "popularity_score": 20.0,
                "session_count": 10,
                "_test_mode": "full",
            },
        ]
        gen = PlaywrightGenerator()
        readme = gen.generate_readme(flows, "example.com", dedup_strategy="full")
        assert "**Dedup strategy**: full" in readme

    def test_smart_strategy_shown(self):
        """The 'smart' dedup strategy should appear in README (unchanged behavior)."""
        flows = [
            {
                "canonical_pattern": "/flow1",
                "popularity_score": 20.0,
                "session_count": 10,
                "_test_mode": "full",
            },
        ]
        gen = PlaywrightGenerator()
        readme = gen.generate_readme(flows, "example.com", dedup_strategy="smart")
        assert "**Dedup strategy**: smart" in readme
