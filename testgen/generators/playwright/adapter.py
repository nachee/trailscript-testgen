from __future__ import annotations
"""Playwright adapter — extends base generator, renders Jinja2 templates.

Generates Playwright test scripts from flow data with assertions
derived from DOM checkpoints.
"""

import os
import re
from urllib.parse import urlparse, urljoin
from collections.abc import Iterator

from jinja2 import Environment, FileSystemLoader

from testgen.generators.base import ScriptGenerator
from testgen.generators.variables import generate_variables_file
from testgen.generators.ts_escape import ts_string_literal, ts_line_comment, ts_block_comment
from testgen.normalisation.element_normaliser import normalise_element
from testgen.normalisation.url_normaliser import normalise_url
from testgen.generators.playwright.dedup import _is_nav_click
from testgen.generators.playwright.event_typing import parse_flow_events


_TEMPLATE_DIR = os.path.join(os.path.dirname(__file__), "templates")


class PlaywrightGenerator(ScriptGenerator):
    """Playwright test script generator using Jinja2 templates."""

    def __init__(self):
        self._env = Environment(
            loader=FileSystemLoader(_TEMPLATE_DIR),
            trim_blocks=True,
            lstrip_blocks=True,
        )
        # Emit any untrusted captured value (URLs, keys, patterns) as a safely
        # escaped, fully-quoted TS string literal — see ts_escape.ts_string_literal.
        self._env.filters["ts_literal"] = ts_string_literal
        # Comment-context sinks: descriptions/flow names embed captured data into
        # `// ...` and `/* ... */` comments. These filters strip line terminators
        # (and defang `*/`) so a payload can't break out of the comment into code.
        self._env.filters["ts_line_comment"] = ts_line_comment
        self._env.filters["ts_block_comment"] = ts_block_comment
        self._variable_map: dict[str, str] = {}

    @property
    def framework_name(self) -> str:
        return "playwright"

    @property
    def file_extension(self) -> str:
        return ".spec.ts"

    def build_variable_map(self, flows: list[dict]) -> dict[str, str]:
        """Build a mapping from element locator to variable name.

        Used to replace hardcoded fill values with ``testVariables`` references
        so users can update test data in one place (``variables.ts``).
        """
        from testgen.generators.variables import _element_to_var_name

        var_map: dict[str, str] = {}
        for flow in flows:
            for target in self._iter_fill_targets(flow):
                element = normalise_element(target)
                if not element:
                    continue
                var_name = _element_to_var_name(element)
                if var_name and element not in var_map:
                    var_map[element] = var_name
        return var_map

    def _iter_fill_targets(self, flow: dict) -> Iterator[dict]:
        """Yield the target dict of each fill event in a flow.

        Prefers the typed ``event_schema.Event`` models; falls back to raw
        dict access when any event in the flow fails schema validation
        (lenient per-flow fallback — see event_typing.parse_flow_events).
        """
        events = flow.get("representative_events", [])
        typed = parse_flow_events(events)
        if typed is not None:
            for ev in typed:
                if ev.event_type == "fill" and ev.target is not None:
                    yield ev.target.model_dump()
        else:
            for event in events:
                if event.get("event_type") == "fill":
                    target = event.get("target")
                    if target:
                        yield target

    def generate_script(self, flow: dict, checkpoints: list[dict],
                        test_mode: str = "full",
                        variable_map: dict[str, str] | None = None,
                        screenshot_assertions: bool = False) -> str:
        """Generate a Playwright test script for a flow.

        test_mode controls what kind of test is generated:
        - "full": Complete test with navigation + interactions + assertions
        - "nav_only": Navigation steps only, stops before first interaction
        - "interaction_only": Starts with page.goto() to target page, interactions only
        - "smart_grouped": Grouped test with beforeEach + individual behavior tests

        variable_map maps element locators to variable names in testVariables.
        screenshot_assertions: when True, adds toHaveScreenshot() after action steps
        that have no assertions as a visual regression fallback.
        """
        # Reset per-script state
        self._checkpoint_counter = {}
        self._variable_map = variable_map or {}

        if test_mode == "smart_grouped":
            return self._generate_grouped_script(flow, checkpoints, screenshot_assertions)

        template = self._env.get_template("test.spec.ts.j2")

        if test_mode == "nav_only":
            steps = self._build_nav_only_steps(flow, checkpoints)
            suffix = " (navigation)"
        elif test_mode == "interaction_only":
            steps = self._build_interaction_only_steps(flow, checkpoints)
            suffix = " (interactions)"
        else:
            steps = self._build_steps(flow, checkpoints)
            suffix = ""

        # Screenshot fallback for assertion-less action steps
        if screenshot_assertions:
            steps = self._add_screenshot_fallback(steps)

        flow_name = self._flow_to_name(flow["canonical_pattern"])
        entry_url = self._get_entry_url(flow)

        # For interaction-only tests, go directly to the interaction page
        if test_mode == "interaction_only":
            entry_url = flow.get("_interaction_page_url") or entry_url

        # Detect whether any fill step uses a variable reference
        has_variables = any(
            "testVariables." in str(step.get("value", ""))
            for step in steps
            if step.get("type") == "fill"
        )

        return template.render(
            flow_name=flow_name + suffix,
            test_name=f"should complete {flow_name}{suffix}",
            canonical_pattern=flow["canonical_pattern"],
            popularity_score=flow.get("popularity_score", 0),
            base_url="",  # Will use baseURL from config
            entry_url=entry_url,
            steps=steps,
            has_variables=has_variables,
        )

    def _generate_grouped_script(self, flow: dict, checkpoints: list[dict],
                                  screenshot_assertions: bool = False) -> str:
        """Generate a grouped test with beforeEach navigation + per-behavior tests."""
        template = self._env.get_template("test.grouped.spec.ts.j2")
        behavior_flows = flow.get("_behavior_flows", [flow])

        flow_name = self._flow_to_name(flow["canonical_pattern"])
        entry_url = self._get_entry_url(flow)

        # Build beforeEach steps: navigation from the most popular flow
        before_each_steps = self._build_nav_only_steps(flow, checkpoints)

        # Build per-behavior steps
        behaviors = []
        all_steps = []
        for i, bflow in enumerate(behavior_flows):
            self._checkpoint_counter = {}
            interaction_steps = self._build_interaction_only_steps(bflow, checkpoints)
            if not interaction_steps:
                # Fall back to full steps if interaction-only extraction fails
                interaction_steps = self._build_steps(bflow, checkpoints)

            if screenshot_assertions:
                interaction_steps = self._add_screenshot_fallback(interaction_steps)

            behavior_name = self._derive_behavior_name(interaction_steps, i)
            behaviors.append({
                "test_name": behavior_name,
                "steps": interaction_steps,
            })
            all_steps.extend(interaction_steps)

        # Ensure unique test names (Playwright requires unique names within a describe)
        self._deduplicate_behavior_names(behaviors)

        has_variables = any(
            "testVariables." in str(step.get("value", ""))
            for step in all_steps
            if step.get("type") == "fill"
        )

        return template.render(
            flow_name=flow_name,
            canonical_pattern=flow["canonical_pattern"],
            popularity_score=flow.get("popularity_score", 0),
            behavior_count=len(behaviors),
            base_url="",
            entry_url=entry_url,
            before_each_steps=before_each_steps,
            behaviors=behaviors,
            has_variables=has_variables,
        )

    def _derive_behavior_name(self, steps: list[dict], index: int) -> str:
        """Derive a semantic behavior name from interaction steps.

        Examines the elements being interacted with to produce names like
        "counter", "accordion panels", "tabs, counter, progress bar" rather
        than generic "click" or "click (8x)".
        """
        if not steps:
            return f"behavior {index + 1}"

        # Collect semantic component names from element locators
        components: list[str] = []
        seen: set[str] = set()

        for step in steps:
            step_type = step.get("type", "")
            component = self._identify_component(step)
            if component and component not in seen:
                seen.add(component)
                components.append(component)

            # For form fill, just use "form fill" and stop
            if step_type == "fill":
                if "form fill" not in seen:
                    return "form fill"

        if components:
            # Limit to 3 components for readability
            name = ", ".join(components[:3])
            if len(components) > 3:
                name += f" (+{len(components) - 3} more)"
            return name
        return f"behavior {index + 1}"

    @staticmethod
    def _identify_component(step: dict) -> str | None:
        """Identify the UI component a step interacts with."""
        locator = step.get("locator", "") or step.get("description", "")
        step_type = step.get("type", "")

        if step_type == "check":
            # Toggle switches
            if "toggle" in locator.lower():
                return "toggle"
            return "checkbox"

        if step_type == "select_option":
            return "select"

        if step_type != "click":
            return None

        # Identify component by element name in the locator
        locator_lower = locator.lower()

        # Counter buttons
        if any(name in locator for name in ['"+"', '"−"', '"−"']):
            return "counter"

        # Progress bar buttons
        if '+ 10%' in locator or '- 10%' in locator:
            return "progress bar"
        if '"Reset"' in locator:
            return None  # Reset is a modifier, not a component

        # Modal
        if any(s in locator for s in ['"Open Modal"', '"×"', '"Got it!"']):
            return "modal"

        # Tabs
        if any(s in locator for s in ['"Overview"', '"Details"', '"Reviews"']):
            return "tabs"

        # Accordion panels (buttons with ▼ marker or FAQ-like text)
        if '▼' in locator or 'TestSite?' in locator or 'open source?' in locator:
            return "accordion"

        return None

    @staticmethod
    def _deduplicate_behavior_names(behaviors: list[dict]) -> None:
        """Ensure all test names are unique within a grouped test.

        Appends the first distinctive element name when duplicates exist.
        Falls back to index suffix.
        """
        name_counts: dict[str, int] = {}
        for b in behaviors:
            name = b["test_name"]
            name_counts[name] = name_counts.get(name, 0) + 1

        # Only process names that appear more than once
        duplicates = {name for name, count in name_counts.items() if count > 1}
        if not duplicates:
            return

        # Collect all element names per behavior to find unique ones
        behavior_elements: list[list[str]] = []
        for b in behaviors:
            elements = []
            for step in b.get("steps", []):
                locator = step.get("locator", "")
                if 'name: "' in locator:
                    start = locator.index('name: "') + 7
                    end = locator.index('"', start)
                    elements.append(locator[start:end])
            behavior_elements.append(elements)

        # For each duplicate group, find the first element that differs
        name_index: dict[str, int] = {}
        for i, b in enumerate(behaviors):
            name = b["test_name"]
            if name not in duplicates:
                continue

            name_index[name] = name_index.get(name, 0) + 1
            idx = name_index[name]

            # Find first element unique to this behavior vs others with the same name
            distinctive = None
            other_elements = set()
            for j, other_b in enumerate(behaviors):
                if j != i and other_b["test_name"] == name:
                    other_elements.update(behavior_elements[j])

            for elem in behavior_elements[i]:
                if elem not in other_elements:
                    distinctive = elem
                    break

            if not distinctive:
                # Fall back to step count difference
                step_count = len(b.get("steps", []))
                distinctive = f"{step_count} steps"

            b["test_name"] = f"{name} — {distinctive}"

    def generate_config(self, site_domain: str, flows: list[dict] | None = None) -> str:
        """Generate playwright.config.ts."""
        template = self._env.get_template("playwright.config.ts.j2")
        base_url = _extract_base_url(flows) if flows else f"https://{site_domain}"
        return template.render(
            site_domain=site_domain,
            base_url=base_url,
        )

    def generate_readme(self, flows: list[dict], site_domain: str,
                        dedup_strategy: str = "full",
                        original_flow_count: int | None = None,
                        total_sessions: int | None = None) -> str:
        """Generate README.md for the test suite.

        total_sessions should be computed from all flows BEFORE dedup so that
        strategies that drop flows (lean) still report the correct count.
        """
        template = self._env.get_template("README.md.j2")
        filenames = self._deduplicated_filenames(flows)
        flow_list = []
        for i, f in enumerate(flows):
            flow_list.append({
                "name": self._flow_to_name(f["canonical_pattern"]),
                "popularity_score": f.get("popularity_score", 0),
                "file_name": filenames[i],
                "test_mode": f.get("_test_mode", "full"),
            })
        computed_sessions = sum(f.get("session_count", 0) for f in flows)
        return template.render(
            site_domain=site_domain,
            total_sessions=total_sessions if total_sessions is not None else computed_sessions,
            flow_count=len(flows),
            flows=flow_list,
            dedup_strategy=dedup_strategy,
            original_flow_count=original_flow_count,
        )

    def generate_variables(self, flows: list[dict]) -> str:
        """Generate variables.ts file."""
        return generate_variables_file(flows)

    def _build_steps(self, flow: dict, checkpoints: list[dict]) -> list[dict]:
        """Build test steps from flow data.

        Uses original events when available (representative_events),
        falls back to significant_actions for backward compatibility.
        """
        if flow.get("representative_events"):
            return self._build_steps_from_events(flow, checkpoints)

        # Fallback: use significant_actions (lossy but backward compatible)
        steps = []
        actions = flow.get("significant_actions", [])
        checkpoint_map = self._index_checkpoints(checkpoints)

        for action in actions:
            step = self._action_to_step(action)
            if step:
                step["assertions"] = self._get_assertions(action, checkpoint_map)
                steps.append(step)

        return steps

    def _build_steps_from_events(self, flow: dict, checkpoints: list[dict]) -> list[dict]:
        """Build test steps from original events with full payload data."""
        steps = []
        events = flow["representative_events"]
        checkpoint_map = self._index_checkpoints(checkpoints)
        entry_url = self._get_entry_url(flow)
        skip_types = {"scroll", "hover", "focus", "api_request", "api_error", "page_load"}
        last_asserted_url = None  # Track last URL with checkpoint assertions

        i = 0
        seen_action = False  # Track whether we've seen a non-navigation action
        while i < len(events):
            event = events[i]
            event_type = event.get("event_type", "")

            if event_type in skip_types:
                i += 1
                continue

            if event_type == "navigation":
                payload = event.get("payload", {}) or {}
                to_url = payload.get("to_url", "")

                # Skip first navigation if it matches entry_url (redundant with page.goto)
                if not steps and normalise_url(to_url) == normalise_url(entry_url):
                    i += 1
                    continue

                # Skip orphaned navigations before any action — no click to trigger them
                if not seen_action:
                    i += 1
                    continue

                step = {
                    "type": "navigation",
                    "url": to_url,
                    "description": f"Wait for navigation to {to_url}",
                    "assertions": self._get_assertions(
                        {"type": "navigation", "url": to_url}, checkpoint_map
                    ),
                }
                steps.append(step)
                last_asserted_url = normalise_url(to_url)
                i += 1
                continue

            # Build step from original event data
            seen_action = True
            target = event.get("target")
            element = normalise_element(target) if target else None
            payload = event.get("payload", {}) or {}
            url = event.get("url", "")

            step = self._event_to_step(event_type, element, payload, url)
            if step:
                # Preserve original event for checkpoint linking
                if event_type == "click":
                    step["_event"] = event

                # Only emit checkpoint assertions when URL changes
                if normalise_url(url) != last_asserted_url:
                    step["assertions"] = self._get_assertions(
                        {"type": event_type, "url": url, "element": element},
                        checkpoint_map,
                    )
                    last_asserted_url = normalise_url(url)
                else:
                    step["assertions"] = []
                # State assertions always added (toHaveValue, toBeChecked, etc.)
                step["assertions"].extend(
                    self._state_assertions(event_type, element, payload)
                )

                # Pair click with immediately following navigation
                if event_type == "click":
                    nav_result = _peek_next_navigation(events, i + 1)
                    # Only pair click with navigation if the click could
                    # plausibly cause it (link click or element with href).
                    # Non-link clicks (accordion buttons, toggles) followed
                    # by an unrelated navigation event should NOT be paired.
                    click_target = event.get("target") or {}
                    click_tag = (click_target.get("tag") or "").upper()
                    click_dest = _link_destination(event, url)
                    click_can_navigate = click_tag == "A" or click_dest is not None

                    if nav_result and click_can_navigate:
                        nav_event, nav_index = nav_result
                        nav_payload = nav_event.get("payload", {}) or {}
                        nav_url = nav_payload.get("to_url", "")
                        # Validate: when the link's href destination is known,
                        # ensure the paired navigation actually goes there.
                        # In random-walk flows, navigation events can be
                        # misaligned with the click that caused them.
                        if click_dest and nav_url:
                            if urlparse(click_dest).path != urlparse(nav_url).path:
                                nav_url = click_dest
                        # Use target URL for the click's assertions (not source page)
                        step["assertions"] = self._get_assertions(
                            {"type": "navigation", "url": nav_url}, checkpoint_map
                        )
                        steps.append(step)
                        # Nav step only gets URL assertion (click already has full assertions)
                        nav_step = {
                            "type": "navigation",
                            "url": nav_url,
                            "description": f"Wait for navigation to {nav_url}",
                            "assertions": [
                                f"await expect(page).toHaveURL(/{_escape_regex(nav_url)}/)"
                            ] if nav_url else [],
                        }
                        steps.append(nav_step)
                        last_asserted_url = normalise_url(nav_url)
                        i = nav_index + 1
                        continue
                    else:
                        # No explicit navigation, but check if next event is on
                        # a different page (implicit navigation occurred)
                        next_url = _peek_next_url(events, i + 1)
                        if next_url and normalise_url(next_url) != normalise_url(url) and click_can_navigate:
                            # Prefer link's known destination over potentially
                            # misaligned next-event URL
                            target_url = next_url
                            if click_dest and urlparse(click_dest).path != urlparse(next_url).path:
                                target_url = click_dest
                            step["assertions"] = self._get_assertions(
                                {"type": "navigation", "url": target_url}, checkpoint_map
                            )
                            last_asserted_url = normalise_url(target_url)
                        elif click_dest and normalise_url(click_dest) != normalise_url(url):
                            # Fallback: use link's href as the destination URL
                            step["assertions"] = self._get_assertions(
                                {"type": "navigation", "url": click_dest},
                                checkpoint_map,
                            )
                            last_asserted_url = normalise_url(click_dest)
                        else:
                            # Click that stays on same page — use checkpoint diffing
                            step["assertions"].extend(
                                self._click_state_assertions(event)
                            )

                steps.append(step)

            i += 1

        # Post-process: collapse click→check, repetitive sequences, and dup nav assertions
        steps = self._collapse_click_check(steps)
        steps = self._collapse_repetitions(steps)
        steps = self._dedup_nav_assertions(steps)
        return steps

    def _build_nav_only_steps(self, flow: dict, checkpoints: list[dict]) -> list[dict]:
        """Build steps for navigation-only tests — stops before first interaction."""
        events = flow.get("representative_events", [])
        if not events:
            return []

        checkpoint_map = self._index_checkpoints(checkpoints)
        entry_url = self._get_entry_url(flow)
        skip_types = {"scroll", "hover", "focus", "api_request", "api_error", "page_load"}
        # Interaction types that signal we should stop
        interaction_types = {"fill", "check", "select_option", "press_key"}
        steps = []
        seen_action = False

        i = 0
        while i < len(events):
            event = events[i]
            event_type = event.get("event_type", "")

            if event_type in skip_types:
                i += 1
                continue

            # Stop at the first non-click, non-navigation interaction
            if event_type in interaction_types:
                break

            # For clicks: only include if they trigger navigation (link clicks)
            if event_type == "click":
                # Peek ahead: if the next event is a check/uncheck on the same
                # element, this click is part of a toggle interaction — stop here.
                if i + 1 < len(events) and events[i + 1].get("event_type") == "check":
                    next_target = events[i + 1].get("target")
                    current_target = event.get("target")
                    if (next_target and current_target
                            and normalise_element(next_target) == normalise_element(current_target)):
                        break

                target = event.get("target")
                url = event.get("url", "")

                # Use shared nav-click detection (handles both direct <a> tags
                # and contextual detection for child elements inside <a> tags).
                nav_result = _peek_next_navigation(events, i + 1)
                next_url = _peek_next_url(events, i + 1)
                dest_url = _link_destination(event, url) if target else None

                if not _is_nav_click(event, events, i):
                    # This click starts interactions — stop here
                    break

                seen_action = True
                element = normalise_element(target) if target else None
                step = self._event_to_step(event_type, element, event.get("payload", {}) or {}, url)
                if step:
                    # Get assertions for the destination page
                    actual_dest = None
                    if nav_result:
                        nav_payload = nav_result[0].get("payload", {}) or {}
                        actual_dest = nav_payload.get("to_url", "")
                    elif next_url and normalise_url(next_url) != normalise_url(url):
                        actual_dest = next_url
                    elif dest_url:
                        actual_dest = dest_url

                    if actual_dest:
                        # Prefer link's known destination over misaligned nav
                        if dest_url and urlparse(dest_url).path != urlparse(actual_dest).path:
                            actual_dest = dest_url
                        step["assertions"] = self._get_assertions(
                            {"type": "navigation", "url": actual_dest}, checkpoint_map
                        )
                    else:
                        step["assertions"] = []
                    steps.append(step)

                    # Skip the paired navigation event
                    if nav_result:
                        i = nav_result[1] + 1
                        continue

                i += 1
                continue

            if event_type == "navigation":
                payload = event.get("payload", {}) or {}
                to_url = payload.get("to_url", "")

                if not steps and normalise_url(to_url) == normalise_url(entry_url):
                    i += 1
                    continue
                if not seen_action:
                    i += 1
                    continue

                step = {
                    "type": "navigation",
                    "url": to_url,
                    "description": f"Wait for navigation to {to_url}",
                    "assertions": self._get_assertions(
                        {"type": "navigation", "url": to_url}, checkpoint_map
                    ),
                }
                steps.append(step)
                i += 1
                continue

            i += 1

        return steps

    def _build_interaction_only_steps(self, flow: dict, checkpoints: list[dict]) -> list[dict]:
        """Build steps for interaction-only tests — skips navigation, starts at target page."""
        events = flow.get("representative_events", [])
        if not events:
            return []

        checkpoint_map = self._index_checkpoints(checkpoints)
        skip_types = {"scroll", "hover", "focus", "api_request", "api_error", "page_load"}
        interaction_types = {"fill", "check", "select_option", "press_key", "click"}
        steps = []
        found_interaction = False
        last_asserted_url = None

        for i, event in enumerate(events):
            event_type = event.get("event_type", "")

            if event_type in skip_types:
                continue

            # Skip ALL navigation events in interaction-only mode.
            # Interactions should be on a single page (guaranteed by dedup
            # strategy), so any navigation is outside the interaction scope.
            if event_type == "navigation":
                continue

            # Skip navigation clicks before the interaction zone
            if not found_interaction and event_type == "click":
                # Check if this click triggers navigation.
                # Only treat as nav click if the element is a link (A tag or has href).
                target = event.get("target")
                url = event.get("url", "")
                target_tag = ((target.get("tag") or "").upper()) if target else ""
                nav_result = _peek_next_navigation(events, i + 1)
                next_url = _peek_next_url(events, i + 1)
                dest_url = _link_destination(event, url) if target else None
                click_can_navigate = target_tag == "A" or dest_url is not None

                is_nav_click = (
                    (nav_result is not None and click_can_navigate)
                    or (next_url and normalise_url(next_url) != normalise_url(url) and click_can_navigate)
                    or (dest_url and normalise_url(dest_url) != normalise_url(url))
                )

                if is_nav_click:
                    continue  # Skip navigation clicks

            found_interaction = True
            target = event.get("target")
            element = normalise_element(target) if target else None
            payload = event.get("payload", {}) or {}
            url = event.get("url", "")

            step = self._event_to_step(event_type, element, payload, url)
            if step:
                if event_type == "click":
                    step["_event"] = event

                if normalise_url(url) != last_asserted_url:
                    step["assertions"] = self._get_assertions(
                        {"type": event_type, "url": url, "element": element},
                        checkpoint_map,
                    )
                    last_asserted_url = normalise_url(url)
                else:
                    step["assertions"] = []
                step["assertions"].extend(
                    self._state_assertions(event_type, element, payload)
                )
                # Click state assertions via checkpoint diffing
                if event_type == "click":
                    step["assertions"].extend(
                        self._click_state_assertions(event)
                    )
                steps.append(step)

        steps = self._collapse_click_check(steps)
        steps = self._collapse_repetitions(steps)
        return steps

    @staticmethod
    def _collapse_click_check(steps: list[dict]) -> list[dict]:
        """Remove redundant click() when immediately followed by check()/uncheck() on same element.

        The tracker records both the raw click event and the semantic check event.
        Playwright's check()/uncheck() already performs the click, so the explicit
        click step is redundant.
        """
        if len(steps) < 2:
            return steps

        result = []
        i = 0
        while i < len(steps):
            if (
                i + 1 < len(steps)
                and steps[i].get("type") == "click"
                and steps[i + 1].get("type") == "check"
                and steps[i].get("locator") == steps[i + 1].get("locator")
            ):
                # Skip the click, keep only the check/uncheck
                result.append(steps[i + 1])
                i += 2
            else:
                result.append(steps[i])
                i += 1

        return result

    def _collapse_repetitions(self, steps: list[dict]) -> list[dict]:
        """Collapse N consecutive identical steps into a single step with repeat_count.

        Only collapses click steps (the most common repetition pattern: counter/progress buttons).
        Requires at least 3 identical consecutive steps to trigger.
        """
        if len(steps) < 3:
            return steps

        result = []
        i = 0
        while i < len(steps):
            # Count consecutive identical steps
            if steps[i].get("type") == "click":
                j = i + 1
                while (
                    j < len(steps)
                    and steps[j].get("type") == "click"
                    and steps[j].get("locator") == steps[i].get("locator")
                ):
                    j += 1

                count = j - i
                if count >= 3:
                    collapsed = dict(steps[i])
                    collapsed["repeat_count"] = count
                    collapsed["description"] = f"Click {collapsed.get('locator', '')} ({count} times)"
                    # Use the LAST event in the sequence for checkpoint lookup
                    last_step = steps[j - 1]
                    if "_event" in last_step:
                        collapsed["_event"] = last_step["_event"]
                    # Try each event in the sequence (last→first) for checkpoint assertions.
                    # Rapid clicks may only trigger checkpoints on some events.
                    assertions = []
                    for k in range(j - 1, i - 1, -1):
                        event = steps[k].get("_event", {})
                        if event:
                            assertions = self._click_state_assertions(event)
                            if assertions:
                                break
                    collapsed["assertions"] = assertions
                    result.append(collapsed)
                    i = j
                    continue

            result.append(steps[i])
            i += 1

        return result

    @staticmethod
    def _dedup_nav_assertions(steps: list[dict]) -> list[dict]:
        """Remove duplicated assertions when click-navigation and waitForURL target the same URL.

        When a click navigates to a new page and the next step is a waitForURL
        for the same URL, the click step already has full checkpoint assertions.
        Reduce the nav step to just the waitForURL call with a single toHaveURL.
        """
        if len(steps) < 2:
            return steps

        result = []
        for i, step in enumerate(steps):
            if (
                step.get("type") == "navigation"
                and i > 0
                and result
                and result[-1].get("type") == "click"
            ):
                prev = result[-1]
                prev_assertions = prev.get("assertions", [])
                # Check if the previous click already verified the URL
                has_url_assertion = any("toHaveURL" in a for a in prev_assertions)
                if has_url_assertion and len(step.get("assertions", [])) > 1:
                    # Keep only the toHaveURL assertion on the nav step
                    step = dict(step)
                    step["assertions"] = [
                        a for a in step["assertions"] if "toHaveURL" in a
                    ][:1]
            result.append(step)

        return result

    @staticmethod
    def _add_screenshot_fallback(steps: list[dict]) -> list[dict]:
        """Add toHaveScreenshot() after action steps that have no assertions.

        Acts as a visual regression safety net: the first test run creates
        baseline screenshots; subsequent runs catch visual changes.
        Only applies to interaction steps (click, fill, select, check) —
        navigation steps already have URL-based assertions.
        """
        action_types = {"click", "fill", "select_option", "check"}
        for i, step in enumerate(steps):
            if step.get("type") in action_types and not step.get("assertions"):
                action = step.get("type", "action")
                # Build a descriptive screenshot name from the step
                locator_hint = (
                    step.get("locator", "element")
                    .replace('"', "")
                    .replace("'", "")
                    .replace(" ", "-")
                    .replace("{", "")
                    .replace("}", "")
                )[:40]
                name = f"step-{i + 1}-{action}-{locator_hint}.png"
                step.setdefault("assertions", []).append(
                    f'await expect(page).toHaveScreenshot({ts_string_literal(name)})'
                )
        return steps

    def _event_to_step(self, event_type: str, element: str | None,
                       payload: dict, url: str) -> dict | None:
        """Convert an original event to a test step using real payload data."""
        locator = element or ""

        if event_type == "click":
            return {
                "type": "click",
                "locator": locator,
                "description": f"Click {locator}",
            }

        if event_type == "fill":
            value = payload.get("value", "test value")
            # Backward compat: old events may still have [REDACTED]
            if value == "[REDACTED]":
                value = "TestPass123!"
            # Use variable reference if this element has a mapped variable
            var_name = self._variable_map.get(locator)
            if var_name:
                value_expr = f"testVariables.{var_name}"
            else:
                value_expr = ts_string_literal(value)
            return {
                "type": "fill",
                "locator": locator,
                "value": value_expr,
                "description": f"Fill {locator}",
            }

        if event_type == "check":
            checked = payload.get("checked", True)
            return {
                "type": "check",
                "locator": locator,
                "checked": checked,
                "description": f"{'Check' if checked else 'Uncheck'} {locator}",
            }

        if event_type == "select_option":
            value = payload.get("value") or payload.get("label") or "option"
            return {
                "type": "select_option",
                "locator": locator,
                "value": ts_string_literal(value),
                "description": f"Select option in {locator}",
            }

        if event_type == "press_key":
            key = payload.get("key", "Enter")
            return {
                "type": "press_key",
                "locator": locator or "page",
                "key": key,
                "description": f"Press {key} on {locator or 'page'}",
            }

        return None

    def _action_to_step(self, action: dict) -> dict | None:
        """Convert a flow action to a test step (legacy fallback)."""
        action_type = action.get("type", "")
        element = action.get("element", "")
        url = action.get("url", "")

        if action_type == "navigation":
            return {
                "type": "navigation",
                "url": url,
                "description": f"Navigate to {url}",
            }

        if action_type == "click":
            return {
                "type": "click",
                "locator": element,
                "description": f"Click {element}",
            }

        if action_type == "fill":
            return {
                "type": "fill",
                "locator": element,
                "value": "'test-value'",
                "description": f"Fill {element}",
            }

        if action_type == "check":
            return {
                "type": "check",
                "locator": element,
                "checked": True,
                "description": f"Check {element}",
            }

        if action_type == "select_option":
            return {
                "type": "select_option",
                "locator": element,
                "value": "'option'",
                "description": f"Select option in {element}",
            }

        if action_type == "press_key":
            return {
                "type": "press_key",
                "locator": element or "page",
                "key": "Enter",
                "description": f"Press key on {element or 'page'}",
            }

        return None

    def _get_assertions(self, action: dict, checkpoint_map: dict) -> list[str]:
        """Generate Playwright assertions from checkpoint data."""
        assertions = []
        url = action.get("url", "")

        # URL assertion after navigation
        if action.get("type") == "navigation" and url:
            assertions.append(f"await expect(page).toHaveURL(/{_escape_regex(url)}/)")

        # Get checkpoint for this URL — use temporal matching when available
        checkpoint = None
        multi = getattr(self, "_checkpoints_by_url", {})
        if url in multi and len(multi[url]) > 1:
            # Track consumption: use next checkpoint for this URL each call
            counter = getattr(self, "_checkpoint_counter", {})
            idx = counter.get(url, 0)
            checkpoints_for_url = multi[url]
            checkpoint = checkpoints_for_url[min(idx, len(checkpoints_for_url) - 1)]
            counter[url] = idx + 1
            self._checkpoint_counter = counter
        else:
            checkpoint = checkpoint_map.get(url)
        if not checkpoint:
            return assertions

        # Visible element assertions (skip global chrome like navbars)
        global_elems = getattr(self, "_global_elements", set())
        added = 0
        for elem in (checkpoint.get("visible_elements") or []):
            if added >= 3:
                break
            selectors = elem.get("selectors", {})
            text = elem.get("text_content")
            role_sel = selectors.get("role")

            if role_sel and isinstance(role_sel, dict) and role_sel.get("name"):
                sig = f'role:{role_sel["role"]}:{role_sel["name"]}'
                if sig in global_elems:
                    continue
                locator = (
                    f'page.getByRole({ts_string_literal(role_sel["role"])}, '
                    f'{{ name: {ts_string_literal(role_sel["name"])} }})'
                )
                assertions.append(f"await expect({locator}).toBeVisible()")
                added += 1

            elif text and len(text) <= 50:
                sig = f'text:{text[:50]}'
                if sig in global_elems:
                    continue
                assertions.append(
                    f'await expect(page.getByText({ts_string_literal(text)})).toBeVisible()'
                )
                added += 1

        # Page title assertion
        title = checkpoint.get("page_title")
        if title:
            assertions.append(f'await expect(page).toHaveTitle(/{_escape_regex(title)}/)')

        return assertions[:5]  # Limit assertions per step

    def _state_assertions(self, event_type: str, element: str | None,
                          payload: dict) -> list[str]:
        """Generate assertions that verify the state change caused by the action."""
        if not element:
            return []
        assertions = []
        if event_type == "fill":
            value = payload.get("value", "")
            # Backward compat: old events may still have [REDACTED]
            if value == "[REDACTED]":
                value = "TestPass123!"
            if value:
                var_name = self._variable_map.get(element)
                if var_name:
                    assertions.append(
                        f"await expect(page.{element}).toHaveValue(testVariables.{var_name})"
                    )
                else:
                    assertions.append(
                        f"await expect(page.{element}).toHaveValue({ts_string_literal(value)})"
                    )
        elif event_type == "check":
            checked = payload.get("checked", True)
            if checked:
                assertions.append(f"await expect(page.{element}).toBeChecked()")
            else:
                assertions.append(f"await expect(page.{element}).not.toBeChecked()")
        elif event_type == "select_option":
            value = payload.get("value") or payload.get("label")
            if value:
                assertions.append(
                    f"await expect(page.{element}).toHaveValue({ts_string_literal(value)})"
                )
        return assertions

    def _click_state_assertions(self, event: dict) -> list[str]:
        """Generate assertions for click events by diffing before/after checkpoints.

        Looks up the checkpoint triggered by this click event (click-settle checkpoint),
        finds the most recent checkpoint before it in the same session, and diffs
        visible_elements to detect DOM state changes caused by the click.
        """
        event_id = str(event.get("event_id", ""))
        session_id = str(event.get("session_id", ""))

        if not event_id:
            return []

        # Find the checkpoint triggered by this click event
        after_cp = getattr(self, "_checkpoints_by_event", {}).get(event_id)
        if not after_cp:
            return []

        # Find the most recent checkpoint BEFORE this one in the same session
        session_cps = getattr(self, "_checkpoints_by_session", {}).get(session_id, [])
        before_cp = None
        for cp in session_cps:
            if str(cp.get("checkpoint_id", "")) == str(after_cp.get("checkpoint_id", "")):
                break
            before_cp = cp

        assertions = self._diff_visible_elements(before_cp, after_cp)

        # Fall back to click_context if visible_elements diff found nothing
        if not assertions and after_cp.get("click_context"):
            before_context = before_cp.get("click_context") if before_cp else None
            assertions = self._diff_click_context(before_context, after_cp["click_context"])

        return assertions

    def _diff_click_context(
        self, before: list[dict] | None, after: list[dict]
    ) -> list[str]:
        """Generate assertions from click context differences.

        Operates on the focused click_context elements (captured around the
        clicked element's DOM container) to detect state changes like counter
        values, progress bar widths, accordion expansions, and modal visibility.
        """
        assertions: list[str] = []
        before_map = self._build_element_identity_map(before) if before else {}

        for elem in after:
            if len(assertions) >= 3:
                break

            identity = self._element_identity(elem)
            locator = self._checkpoint_element_locator(elem)
            if not locator:
                # Try CSS selector from checkpoint
                css = elem.get("selectors", {}).get("css", "")
                if css:
                    locator = f'page.locator({ts_string_literal(css)})'
                else:
                    continue

            attrs = elem.get("attributes", {}) or {}

            if identity in before_map:
                old_elem = before_map[identity]
                old_attrs = old_elem.get("attributes", {}) or {}

                # Text content change (counter value, progress label, etc.)
                old_text = old_elem.get("text_content", "")
                new_text = elem.get("text_content", "")
                if old_text != new_text and new_text and len(new_text) <= 50:
                    assertions.append(
                        f'await expect({locator}).toContainText({ts_string_literal(new_text)})'
                    )
                    continue

                # aria-valuenow change (progress bars, sliders)
                old_val = old_attrs.get("aria-valuenow")
                new_val = attrs.get("aria-valuenow")
                if old_val != new_val and new_val is not None:
                    assertions.append(
                        f'await expect({locator}).toHaveAttribute("aria-valuenow", {ts_string_literal(new_val)})'
                    )
                    continue

                # aria-expanded change (accordions)
                old_exp = old_attrs.get("aria-expanded")
                new_exp = attrs.get("aria-expanded")
                if old_exp != new_exp and new_exp is not None:
                    assertions.append(
                        f'await expect({locator}).toHaveAttribute("aria-expanded", {ts_string_literal(new_exp)})'
                    )
                    continue

                # aria-hidden change (modals)
                old_hidden = old_attrs.get("aria-hidden")
                new_hidden = attrs.get("aria-hidden")
                if old_hidden != new_hidden and new_hidden is not None:
                    if new_hidden == "true":
                        assertions.append(f"await expect({locator}).toBeHidden()")
                    else:
                        assertions.append(f"await expect({locator}).toBeVisible()")
                    continue

                # Style change (width-based progress bars)
                old_style = old_attrs.get("style", "") or ""
                new_style = attrs.get("style", "") or ""
                if old_style != new_style and "width" in new_style:
                    import re
                    width_match = re.search(r"width:\s*(\d+(?:\.\d+)?%)", new_style)
                    if width_match:
                        assertions.append(
                            f'await expect({locator}).toHaveCSS("width", /{width_match.group(1)}/)'
                        )
                        continue
            else:
                # New element appeared (e.g., modal dialog opened, success message)
                new_text = elem.get("text_content", "")
                if new_text and len(new_text) <= 50:
                    assertions.append(f"await expect({locator}).toBeVisible()")

        return assertions

    def _diff_visible_elements(self, before: dict | None, after: dict) -> list[str]:
        """Generate assertions from visible element differences between two checkpoints."""
        if not before:
            return []  # Can't diff without a before checkpoint

        assertions: list[str] = []
        global_elems = getattr(self, "_global_elements", set())

        after_elems = after.get("visible_elements") or []
        before_map = self._build_element_identity_map(
            before.get("visible_elements") or []
        )

        for elem in after_elems:
            if len(assertions) >= 3:
                break

            identity = self._element_identity(elem)
            if identity in global_elems:
                continue

            locator = self._checkpoint_element_locator(elem)
            if not locator:
                continue

            if identity in before_map:
                # Element existed before — check for text change
                old_text = before_map[identity].get("text_content", "")
                new_text = elem.get("text_content", "")
                if old_text != new_text and new_text and len(new_text) <= 50:
                    assertions.append(
                        f'await expect({locator}).toContainText({ts_string_literal(new_text)})'
                    )
            else:
                # New element appeared after clicks
                assertions.append(f"await expect({locator}).toBeVisible()")

        return assertions

    @staticmethod
    def _element_identity(elem: dict) -> str:
        """Stable identity for an element based on structural selectors (not text)."""
        selectors = elem.get("selectors", {})
        testid = selectors.get("testid")
        if testid:
            return f"testid:{testid}"
        role = selectors.get("role")
        if role and isinstance(role, dict) and role.get("role"):
            return f"role:{role['role']}:{role.get('name', '')}"
        css = selectors.get("css", "")
        tag = elem.get("tag", "")
        return f"css:{css or tag}"

    @staticmethod
    def _checkpoint_element_locator(elem: dict) -> str | None:
        """Build a Playwright locator from a checkpoint element's selectors."""
        selectors = elem.get("selectors", {})
        role = selectors.get("role")
        if role and isinstance(role, dict) and role.get("name"):
            return (
                f'page.getByRole({ts_string_literal(role["role"])}, '
                f'{{ name: {ts_string_literal(role["name"])} }})'
            )
        testid = selectors.get("testid")
        if testid:
            return f'page.getByTestId({ts_string_literal(testid)})'
        text = elem.get("text_content", "")
        if text and len(text) <= 50:
            return f'page.getByText({ts_string_literal(text)})'
        return None

    @staticmethod
    def _build_element_identity_map(elements: list[dict]) -> dict[str, dict]:
        """Build a map from element identity to element dict."""
        result: dict[str, dict] = {}
        for elem in elements:
            identity = PlaywrightGenerator._element_identity(elem)
            result[identity] = elem
        return result

    def _index_checkpoints(self, checkpoints: list[dict]) -> dict:
        """Index checkpoints by URL for quick lookup.

        Stores all checkpoints per URL (ordered by timestamp) so that
        single-page flows can match steps to the temporally closest checkpoint.
        Also computes global_elements — elements visible in every checkpoint
        (navbar, footer, etc.) — so assertions can skip them.
        """
        # Group all checkpoints by URL (ordered by timestamp)
        multi: dict[str, list[dict]] = {}
        for cp in checkpoints:
            url = cp.get("url", "")
            if url:
                multi.setdefault(url, []).append(cp)

        # Store multi-checkpoint index for temporal matching
        self._checkpoints_by_url = multi

        # Legacy single-checkpoint index (first per URL)
        result: dict[str, dict] = {}
        for url, cps in multi.items():
            result[url] = cps[0]

        # Build event_id → checkpoint index for click-settle lookups
        self._checkpoints_by_event: dict[str, dict] = {}
        for cp in checkpoints:
            eid = cp.get("trigger_event_id")
            if eid:
                eid_str = str(eid)
                if eid_str not in self._checkpoints_by_event:
                    self._checkpoints_by_event[eid_str] = cp

        # Build session-ordered checkpoint lists for "before" lookups
        self._checkpoints_by_session: dict[str, list[dict]] = {}
        for cp in sorted(checkpoints, key=lambda c: c.get("timestamp", "")):
            sid = str(cp.get("session_id", ""))
            self._checkpoints_by_session.setdefault(sid, []).append(cp)

        # Detect elements that appear in every checkpoint (global chrome)
        if len(result) >= 2:
            element_sets = []
            for cp in result.values():
                sigs = set()
                for elem in (cp.get("visible_elements") or []):
                    selectors = elem.get("selectors", {})
                    role_sel = selectors.get("role")
                    if role_sel and isinstance(role_sel, dict) and role_sel.get("name"):
                        sigs.add(f'role:{role_sel["role"]}:{role_sel["name"]}')
                    elif elem.get("text_content"):
                        sigs.add(f'text:{elem["text_content"][:50]}')
                element_sets.append(sigs)
            self._global_elements = element_sets[0].intersection(*element_sets[1:])
        else:
            self._global_elements = set()

        return result

    def _flow_to_name(self, pattern: str) -> str:
        """Convert a flow pattern to a human-readable test name."""
        parts = pattern.split(" → ")
        if len(parts) >= 2:
            return f"{_path_to_name(parts[0])} to {_path_to_name(parts[-1])}"
        return _path_to_name(parts[0]) if parts else "unnamed-flow"

    def _flow_to_filename(self, pattern: str, index: int = 0) -> str:
        """Convert a flow pattern to a unique filename.

        Uses all path segments (not just first/last) to differentiate flows
        with the same endpoints. Truncates long names and appends a hash of the
        full pattern to prevent filesystem-level collisions from truncation or
        consecutive-name deduplication.
        """
        import hashlib as _hl

        parts = pattern.split(" → ")
        name_parts = [_path_to_name(p) for p in parts]
        # Deduplicate consecutive identical names
        deduped = [name_parts[0]]
        for p in name_parts[1:]:
            if p != deduped[-1]:
                deduped.append(p)
        name = "-to-".join(deduped) if len(deduped) > 1 else deduped[0]
        base = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")

        # Cap filename length to avoid filesystem truncation (255-char limit).
        # Always append a short hash when the name is long enough that
        # consecutive-dedup could cause two different patterns to collide.
        max_base = 180  # leaves room for hash + index + extension
        if len(base) > max_base:
            pattern_hash = _hl.md5(pattern.encode()).hexdigest()[:8]
            base = f"{base[:max_base]}-{pattern_hash}"

        if index > 0:
            base = f"{base}-{index + 1}"
        return base + self.file_extension

    def _deduplicated_filenames(self, flows: list[dict]) -> list[str]:
        """Generate unique filenames for a list of flows, adding index suffixes on collision."""
        filenames: list[str] = []
        all_names: set[str] = set()
        counter: dict[str, int] = {}
        for flow in flows:
            name = self._flow_to_filename(flow["canonical_pattern"], index=0)
            if name in all_names:
                counter.setdefault(name, 0)
                counter[name] += 1
                candidate = self._flow_to_filename(flow["canonical_pattern"], index=counter[name])
                # Keep incrementing until the suffixed name is also unique
                while candidate in all_names:
                    counter[name] += 1
                    candidate = self._flow_to_filename(flow["canonical_pattern"], index=counter[name])
                name = candidate
            all_names.add(name)
            filenames.append(name)
        return filenames

    def _get_entry_url(self, flow: dict) -> str:
        """Get the entry URL for a flow."""
        # Prefer representative_events if available
        events = flow.get("representative_events", [])
        if events:
            for event in events:
                if event.get("event_type") == "navigation":
                    payload = event.get("payload", {}) or {}
                    to_url = payload.get("to_url", "")
                    if to_url:
                        return to_url
            # No navigation event — use first event's URL
            if events:
                return events[0].get("url", "/")

        # Fallback: use significant_actions
        actions = flow.get("significant_actions", [])
        for action in actions:
            if action.get("type") == "navigation":
                return action.get("url", "/")
        return "/"


def _link_destination(event: dict, current_url: str) -> str | None:
    """Extract the destination URL from a click on a link element.

    If the event target is an <a> tag with an href, resolve it
    relative to the current page URL to get the destination.
    """
    target = event.get("target")
    if not target:
        return None
    tag = (target.get("tag") or "").upper()
    if tag != "A":
        return None
    attributes = target.get("attributes", {}) or {}
    href = attributes.get("href", "")
    if not href or href.startswith("#") or href.startswith("javascript:"):
        return None
    # Resolve relative URLs against the current page
    if href.startswith("http://") or href.startswith("https://"):
        return href
    if href.startswith("/"):
        # Absolute path — combine with origin from current_url
        parsed = urlparse(current_url)
        return f"{parsed.scheme}://{parsed.netloc}{href}"
    # Relative path — combine with current URL directory
    return urljoin(current_url, href)


def _peek_next_navigation(events: list[dict], start_index: int) -> tuple[dict, int] | None:
    """Look ahead for the next navigation event, skipping non-significant events.

    Returns (navigation_event, index) or None if no navigation found
    before the next significant non-navigation event.
    """
    skip_types = {"scroll", "hover", "focus", "blur", "input", "change", "submit",
                  "api_request", "api_error", "page_load"}
    for j in range(start_index, len(events)):
        evt_type = events[j].get("event_type", "")
        if evt_type == "navigation":
            return (events[j], j)
        if evt_type not in skip_types:
            return None
    return None


def _peek_next_url(events: list[dict], start_index: int) -> str | None:
    """Look ahead for the URL of the next significant event.

    Skips events that don't carry meaningful URL context.
    Returns the URL string or None if no more significant events found.
    """
    skip_types = {"scroll", "hover", "focus", "blur", "input", "change", "submit",
                  "api_request", "api_error", "page_load"}
    for j in range(start_index, len(events)):
        evt = events[j]
        evt_type = evt.get("event_type", "")
        if evt_type in skip_types:
            continue
        if evt_type == "navigation":
            payload = evt.get("payload", {}) or {}
            return payload.get("to_url", "")
        return evt.get("url", "")
    return None


def _path_to_name(path: str) -> str:
    """Convert a URL path to a readable name."""
    path = path.strip("/")
    if not path:
        return "home"
    # Replace dynamic segments
    path = path.replace(":uuid", "item").replace(":id", "item").replace(":slug", "page")
    return path.split("/")[-1] or "home"


def _escape_regex(s: str) -> str:
    """Escape special regex characters (including / for JS regex literals)."""
    return re.sub(r"[.*+?^${}()|[\]\\/]", r"\\\g<0>", s)


def _extract_base_url(flows: list[dict]) -> str:
    """Extract the base URL (scheme + host + port) from flow data.

    Inspects entry URLs from all flows and returns the most common origin.
    Falls back to https://{first_domain} if no URLs found.
    """
    from collections import Counter
    origins = Counter()
    for flow in (flows or []):
        events = flow.get("representative_events", [])
        for event in events:
            url = event.get("url", "")
            if url and url.startswith("http"):
                parsed = urlparse(url)
                origin = f"{parsed.scheme}://{parsed.netloc}"
                origins[origin] += 1
                break  # Only need one URL per flow
    if origins:
        return origins.most_common(1)[0][0]
    return "https://localhost"
