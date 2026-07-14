"""Test deduplication strategies for Playwright test generation.

Four customer-selectable strategies that control how similar flows
(same interactions reached via different navigation routes) are handled:

- full:    No deduplication — every flow becomes a complete test.
- smart:   Full test for the most popular route; navigation-only for alternatives.
- lean:    One test per unique interaction set; alternatives are dropped entirely.
- modular: Interactions tested once (via direct goto); all routes get nav-only tests.
"""

from __future__ import annotations

import hashlib
from collections import defaultdict
from urllib.parse import urlparse, urljoin

from testgen.normalisation.element_normaliser import normalise_element, fingerprint_element


VALID_STRATEGIES = ("full", "smart", "lean", "modular")

# Event types that are not considered "interactions" for fingerprinting.
# Includes intermediate browser events (blur, input, change, submit) that
# can appear between a link click and its navigation event — without these,
# contextual nav-click detection in _is_nav_click breaks, causing
# _interactions_on_single_page to incorrectly see multiple pages.
_SKIP_TYPES = frozenset({
    "scroll", "hover", "focus", "blur", "navigation",
    "input", "change", "submit",
    "api_request", "api_error", "page_load",
})


def _is_nav_click(
    event: dict,
    events: list[dict] | None = None,
    event_index: int | None = None,
) -> bool:
    """Check if a click event is a navigation click.

    Returns True when EITHER:
    1. The click target is an <a> tag whose href resolves to a different page, OR
    2. The next significant event in the sequence is a navigation event (contextual
       detection — catches clicks on child elements inside <a> tags where the
       tracker records the innermost element instead of the <a> ancestor).

    Same-page anchors (#section) return False.
    """
    target = event.get("target")
    if target:
        tag = (target.get("tag") or "").upper()
        if tag == "A":
            attributes = target.get("attributes", {}) or {}
            href = attributes.get("href", "")
            if href and not href.startswith("#") and not href.startswith("javascript:"):
                current_url = event.get("url", "")
                if not current_url:
                    return True  # Can't compare, assume navigation

                if href.startswith(("http://", "https://")):
                    dest = href
                elif href.startswith("/"):
                    parsed = urlparse(current_url)
                    dest = f"{parsed.scheme}://{parsed.netloc}{href}"
                else:
                    dest = urljoin(current_url, href)

                if urlparse(dest).path != urlparse(current_url).path:
                    return True

    # Contextual detection: peek ahead for a navigation event following this click.
    # Catches clicks on child elements inside <a> tags (e.g. <span> inside <a>).
    if events is not None and event_index is not None:
        for j in range(event_index + 1, len(events)):
            next_type = events[j].get("event_type", "")
            if next_type == "navigation":
                return True
            if next_type in _SKIP_TYPES:
                continue
            break  # Next significant event is not navigation

    return False


def compute_action_fingerprint(flow: dict) -> str:
    """Hash the non-navigation interaction actions to identify functionally equivalent flows.

    Two flows with the same fingerprint perform the same interactions on
    the same elements — only their navigation paths differ.
    """
    events = flow.get("representative_events", [])
    actions: list[tuple[str, str]] = []
    for i, e in enumerate(events):
        if e.get("event_type") in _SKIP_TYPES:
            continue
        # Skip clicks on links that navigate to a different page
        if e.get("event_type") == "click" and _is_nav_click(e, events, i):
            continue
        element = fingerprint_element(e.get("target")) if e.get("target") else ""
        actions.append((e["event_type"], element))

    if not actions:
        # Flows with no interactions get a unique fingerprint
        # (prevents grouping navigation-only flows together)
        pattern = flow.get("canonical_pattern", "")
        return hashlib.md5(f"nav-only:{pattern}".encode()).hexdigest()

    return hashlib.md5(str(actions).encode()).hexdigest()


def group_flows_by_interaction(flows: list[dict]) -> dict[str, list[dict]]:
    """Group flows that share the same action fingerprint.

    Returns dict mapping fingerprint → list of flows,
    each list sorted by popularity_score descending (most popular first).
    """
    groups: dict[str, list[dict]] = defaultdict(list)
    for flow in flows:
        fp = compute_action_fingerprint(flow)
        flow["_action_fingerprint"] = fp
        groups[fp].append(flow)

    # Sort each group by popularity (highest first)
    for fp in groups:
        groups[fp].sort(key=lambda f: f.get("popularity_score", 0), reverse=True)

    return dict(groups)


def _find_interaction_start_index(flow: dict) -> int | None:
    """Find the index of the first non-navigation interaction event.

    Returns the index into representative_events where interactions begin,
    or None if the flow has no interactions.  Navigation clicks (link clicks
    that trigger page transitions) are skipped — they are not interactions.
    """
    events = flow.get("representative_events", [])
    for i, e in enumerate(events):
        if e.get("event_type") in _SKIP_TYPES:
            continue
        if e.get("event_type") == "click" and _is_nav_click(e, events, i):
            continue
        return i
    return None


def _has_navigation_prefix(flow: dict) -> bool:
    """Check if the flow navigates to a different page before interactions begin.

    Returns True when the flow's entry URL differs from the page where
    interactions happen — meaning there are navigation steps to split off
    as a separate nav-only test.
    """
    events = flow.get("representative_events", [])
    if not events:
        return False
    entry_url = events[0].get("url", "")
    interaction_url = _find_interaction_page_url(flow)
    if not entry_url or not interaction_url:
        return False
    return urlparse(entry_url).path != urlparse(interaction_url).path


def _find_interaction_page_url(flow: dict) -> str | None:
    """Find the URL of the page where interactions happen.

    Skips navigation clicks (link clicks to other pages) and returns the URL
    from the first actual interaction event.
    """
    events = flow.get("representative_events", [])
    for i, e in enumerate(events):
        if e.get("event_type") in _SKIP_TYPES:
            continue
        if e.get("event_type") == "click" and _is_nav_click(e, events, i):
            continue
        return e.get("url", "")
    return None


def _interactions_on_single_page(flow: dict) -> bool:
    """Check if all non-navigation interactions happen on a single page.

    Returns True when every interaction event shares the same URL path,
    meaning the flow can be cleanly split into nav-only + interaction-only.
    Returns False for interleaved flows (nav → interact → nav → interact)
    which should NOT be split.
    """
    events = flow.get("representative_events", [])
    interaction_paths: set[str] = set()
    for i, e in enumerate(events):
        if e.get("event_type") in _SKIP_TYPES:
            continue
        if e.get("event_type") == "click" and _is_nav_click(e, events, i):
            continue
        url = e.get("url", "")
        if url:
            interaction_paths.add(urlparse(url).path)
    return len(interaction_paths) <= 1


def group_flows_by_url_pattern(flows: list[dict]) -> dict[str, list[dict]]:
    """Group flows by canonical URL pattern.

    Returns dict mapping canonical_pattern → list of flows,
    each list sorted by popularity_score descending.
    """
    groups: dict[str, list[dict]] = defaultdict(list)
    for flow in flows:
        pattern = flow.get("canonical_pattern", "")
        groups[pattern].append(flow)
    for pattern in groups:
        groups[pattern].sort(key=lambda f: f.get("popularity_score", 0), reverse=True)
    return dict(groups)


def apply_dedup_strategy(
    flows: list[dict],
    strategy: str = "smart",
) -> list[dict]:
    """Apply a deduplication strategy to a list of flows.

    Returns a new list of flow dicts with an added `_test_mode` key:
    - "full":             Generate complete test (navigation + interactions + assertions)
    - "nav_only":         Generate navigation steps only, stop before interactions
    - "interaction_only": Generate interactions only, start with page.goto() to target page
    - "smart_grouped":    Grouped test file: beforeEach with route navigation,
                          individual test() blocks per behavior variant

    Flows excluded by the strategy are simply omitted from the returned list.
    """
    if strategy not in VALID_STRATEGIES:
        strategy = "smart"

    if strategy == "full":
        return _strategy_full(flows)
    elif strategy == "smart":
        return _strategy_smart(flows)
    elif strategy == "lean":
        return _strategy_lean(flows)
    elif strategy == "modular":
        return _strategy_modular(flows)

    return _strategy_smart(flows)


def _strategy_full(flows: list[dict]) -> list[dict]:
    """No deduplication — every flow gets a full test."""
    result = []
    for flow in flows:
        f = dict(flow)
        f["_test_mode"] = "full"
        result.append(f)
    return result


def _strategy_smart(flows: list[dict]) -> list[dict]:
    """Hybrid — grouped behaviors per URL pattern, nav-only for alternative routes.

    Two-phase approach:
    1. Fingerprint dedup: flows with identical interactions on different routes →
       keep most popular, nav-only for alternatives.
    2. URL-pattern grouping: remaining flows on the same page with different
       interactions → smart_grouped test (beforeEach + individual test blocks).
    """
    # Phase 1: Fingerprint-based dedup (same interactions, different routes)
    groups = group_flows_by_interaction(flows)
    kept_flows = []
    nav_only_flows = []
    for fp, group in groups.items():
        kept_flows.append(dict(group[0]))  # Most popular
        for flow in group[1:]:
            f = dict(flow)
            f["_test_mode"] = "nav_only"
            nav_only_flows.append(f)

    # Phase 2: Group kept flows by URL pattern for behavior merging
    url_groups = group_flows_by_url_pattern(kept_flows)
    result = []
    for pattern, pattern_flows in url_groups.items():
        if len(pattern_flows) == 1:
            pattern_flows[0]["_test_mode"] = "full"
            result.append(pattern_flows[0])
        else:
            # Multiple behaviors on the same page → grouped test
            base = dict(pattern_flows[0])  # Most popular as base
            base["_test_mode"] = "smart_grouped"
            base["_behavior_flows"] = pattern_flows
            result.append(base)

    result.extend(nav_only_flows)
    return result


def _strategy_lean(flows: list[dict]) -> list[dict]:
    """Drop duplicates — only the most popular flow per group gets a test."""
    groups = group_flows_by_interaction(flows)
    result = []

    for fp, group in groups.items():
        canonical = dict(group[0])
        canonical["_test_mode"] = "full"
        result.append(canonical)
        # Others are dropped entirely

    return result


def _strategy_modular(flows: list[dict]) -> list[dict]:
    """Segment decomposition — each behavior gets an interaction-only test,
    all routes get nav-only tests.

    Two-phase approach:
    1. Fingerprint dedup: flows with identical interactions on different routes →
       interaction-only for canonical, nav-only for all routes.
    2. URL-pattern grouping: remaining singleton flows on the same page →
       each becomes interaction-only (direct page.goto + interactions).
    """
    # Phase 1: Fingerprint-based dedup
    groups = group_flows_by_interaction(flows)
    kept_flows = []
    nav_only_flows = []

    for fp, group in groups.items():
        has_interactions = _find_interaction_start_index(group[0]) is not None

        if has_interactions and len(group) > 1:
            if _interactions_on_single_page(group[0]):
                # interaction_only for the canonical
                interaction_flow = dict(group[0])
                interaction_flow["_test_mode"] = "interaction_only"
                interaction_flow["_interaction_page_url"] = _find_interaction_page_url(group[0])
                kept_flows.append(interaction_flow)
                # nav_only for all routes
                for flow in group:
                    f = dict(flow)
                    f["_test_mode"] = "nav_only"
                    nav_only_flows.append(f)
            else:
                # Interleaved — keep canonical as full, nav_only for others
                kept_flows.append(dict(group[0]))
                for flow in group[1:]:
                    f = dict(flow)
                    f["_test_mode"] = "nav_only"
                    nav_only_flows.append(f)
        else:
            # Singleton or no interactions
            flow = dict(group[0])
            if has_interactions and _interactions_on_single_page(flow):
                if _has_navigation_prefix(flow):
                    # Singleton with nav prefix: split into interaction_only + nav_only
                    interaction_flow = dict(flow)
                    interaction_flow["_test_mode"] = "interaction_only"
                    interaction_flow["_interaction_page_url"] = _find_interaction_page_url(flow)
                    kept_flows.append(interaction_flow)
                    nav_flow = dict(flow)
                    nav_flow["_test_mode"] = "nav_only"
                    nav_only_flows.append(nav_flow)
                else:
                    # No nav prefix (starts on interaction page): interaction_only
                    flow["_test_mode"] = "interaction_only"
                    flow["_interaction_page_url"] = _find_interaction_page_url(flow)
                    kept_flows.append(flow)
            else:
                # No interactions or interleaved — Phase 2 will assign mode
                kept_flows.append(flow)

    # Phase 2: Group by URL pattern — convert singletons on the same page
    # to interaction_only when multiple behaviors exist on that page.
    url_groups = group_flows_by_url_pattern(kept_flows)
    result = []

    for pattern, pattern_flows in url_groups.items():
        if len(pattern_flows) == 1:
            flow = pattern_flows[0]
            if flow.get("_test_mode") not in ("interaction_only",):
                flow["_test_mode"] = "full"
            result.append(flow)
        else:
            # Multiple behaviors on the same page → each becomes interaction_only
            for flow in pattern_flows:
                if _interactions_on_single_page(flow):
                    flow["_test_mode"] = "interaction_only"
                    flow["_interaction_page_url"] = _find_interaction_page_url(flow)
                else:
                    flow["_test_mode"] = "full"
                result.append(flow)

    # Deduplicate nav_only flows — multiple singletons with the same
    # navigation route should produce only one nav-only test.
    seen_nav_patterns: set[str] = set()
    unique_nav_only: list[dict] = []
    for flow in nav_only_flows:
        pattern = flow.get("canonical_pattern", "")
        if pattern not in seen_nav_patterns:
            seen_nav_patterns.add(pattern)
            unique_nav_only.append(flow)
    result.extend(unique_nav_only)
    return result
