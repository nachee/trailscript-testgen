from __future__ import annotations
"""Popular path extraction — extract flows above popularity threshold.

Calculates popularity scores as percentage of total sessions
that match each detected flow pattern.
"""

import hashlib
from collections import defaultdict
from urllib.parse import urlparse, urljoin

import networkx as nx

from testgen.graph.flow_builder import extract_flow_paths, _event_to_node
from testgen.normalisation.element_normaliser import fingerprint_element
from testgen.normalisation.url_normaliser import normalise_url
from testgen.normalisation.url_normaliser import normalise_url_sequence


# Event types ignored for interaction fingerprinting (same as dedup._SKIP_TYPES
# plus navigation, which is always non-interactive).
_FINGERPRINT_SKIP = frozenset({
    "scroll", "hover", "focus", "blur", "navigation",
    "input", "change", "submit",
    "api_request", "api_error", "page_load",
})


def _detect_viewport_class(events: list[dict]) -> str:
    """Detect viewport class from session events.

    Examines the page_context.viewport field on events to determine
    whether this session ran on a mobile or desktop viewport.

    Returns "mobile" if the majority of events have viewport width < 768px,
    "desktop" otherwise.
    """
    mobile_count = 0
    total = 0
    for event in events:
        page_context = event.get("page_context")
        if not page_context:
            continue
        # page_context may be a dict (from JSON) or already parsed
        if isinstance(page_context, str):
            try:
                import json
                page_context = json.loads(page_context)
            except (ValueError, TypeError):
                continue
        viewport = page_context.get("viewport") if isinstance(page_context, dict) else None
        if viewport and isinstance(viewport, dict):
            width = viewport.get("width")
            if isinstance(width, (int, float)):
                total += 1
                if width < 768:
                    mobile_count += 1

    if total > 0 and mobile_count > total / 2:
        return "mobile"
    return "desktop"


def _is_link_nav_click(event: dict) -> bool:
    """Lightweight nav-click check for fingerprinting (no contextual peek).

    Returns True when the click target is an <a> tag whose href resolves
    to a different page.  This is simpler than dedup._is_nav_click (no
    contextual detection) but sufficient for session-level grouping.
    """
    target = event.get("target")
    if not target:
        return False
    tag = (target.get("tag") or "").upper()
    if tag != "A":
        return False
    attributes = target.get("attributes", {}) or {}
    href = attributes.get("href", "")
    if not href or href.startswith("#") or href.startswith("javascript:"):
        return False
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
    return urlparse(dest).path != urlparse(current_url).path


def _compute_session_fingerprint(events: list[dict]) -> str:
    """Compute an interaction fingerprint for a session's events.

    Hashes (event_type, element_identity) tuples for non-navigation
    interactions, producing a stable fingerprint that groups sessions
    performing the same interactions regardless of navigation route.
    """
    actions: list[tuple[str, str]] = []
    for e in events:
        event_type = e.get("event_type", "")
        if event_type in _FINGERPRINT_SKIP:
            continue
        if event_type == "click" and _is_link_nav_click(e):
            continue
        element = fingerprint_element(e.get("target")) if e.get("target") else ""
        actions.append((event_type, element))

    if not actions:
        return ""
    return hashlib.md5(str(actions).encode()).hexdigest()


def extract_popular_paths(
    graph: nx.DiGraph,
    total_sessions: int,
    threshold_percent: float = 5.0,
) -> list[dict]:
    """Extract popular flow paths above the given popularity threshold.

    Args:
        graph: Flow graph built from session events.
        total_sessions: Total number of sessions analysed.
        threshold_percent: Minimum popularity score (% of sessions) to include.

    Returns:
        List of flow dicts with canonical_pattern, significant_actions,
        popularity_score, and path nodes.
    """
    if total_sessions == 0:
        return []

    all_paths = extract_flow_paths(graph)

    # Group paths by canonical pattern (normalised URL sequence)
    pattern_groups: dict[str, list[list[str]]] = {}
    for path in all_paths:
        urls = _extract_urls_from_path(path)
        pattern = normalise_url_sequence(urls)
        if pattern not in pattern_groups:
            pattern_groups[pattern] = []
        pattern_groups[pattern].append(path)

    # Calculate popularity and filter by threshold
    popular_flows = []
    for pattern, paths in pattern_groups.items():
        # Estimate session count from edge weights
        session_count = _estimate_session_count(graph, paths)
        popularity = (session_count / total_sessions) * 100

        if popularity >= threshold_percent:
            # Pick the most common variant
            representative_path = max(paths, key=lambda p: _path_weight(graph, p))
            actions = _extract_actions_from_path(representative_path)

            popular_flows.append({
                "canonical_pattern": pattern,
                "significant_actions": actions,
                "popularity_score": round(popularity, 2),
                "session_count": session_count,
                "path_nodes": representative_path,
            })

    # Sort by popularity (most popular first)
    popular_flows.sort(key=lambda f: f["popularity_score"], reverse=True)

    return popular_flows


def _extract_urls_from_path(path: list[str]) -> list[str]:
    """Extract URL sequence from a path of node identifiers."""
    urls = []
    for node in path:
        if node.startswith("nav:"):
            urls.append(node[4:])  # Remove "nav:" prefix
        else:
            # Non-nav nodes use || delimiter: url||event_type||element
            url = node.split("||")[0] if "||" in node else node
            urls.append(url)
    return urls


def _extract_actions_from_path(path: list[str]) -> list[dict]:
    """Extract significant actions from a path."""
    actions = []
    for node in path:
        if node.startswith("nav:"):
            # Navigation nodes: nav:{full_url}
            actions.append({
                "url": node[4:],
                "type": "navigation",
            })
        elif "||" in node:
            # Action nodes: url||event_type||element
            parts = node.split("||", 2)
            action = {
                "url": parts[0],
                "type": parts[1],
            }
            if len(parts) >= 3:
                action["element"] = parts[2]
            actions.append(action)
    return actions


def _estimate_session_count(graph: nx.DiGraph, paths: list[list[str]]) -> int:
    """Estimate how many sessions followed these paths.

    Uses the minimum edge weight along the path as the count.
    """
    max_count = 0
    for path in paths:
        min_weight = float("inf")
        for i in range(len(path) - 1):
            if graph.has_edge(path[i], path[i + 1]):
                weight = graph[path[i]][path[i + 1]].get("weight", 0)
                min_weight = min(min_weight, weight)
            else:
                min_weight = 0
                break
        if min_weight != float("inf"):
            max_count = max(max_count, int(min_weight))
    return max_count


def _path_weight(graph: nx.DiGraph, path: list[str]) -> int:
    """Calculate total weight of a path."""
    total = 0
    for i in range(len(path) - 1):
        if graph.has_edge(path[i], path[i + 1]):
            total += graph[path[i]][path[i + 1]].get("weight", 0)
    return total


def extract_popular_paths_from_sessions(
    sub_sessions: list[list[dict]],
    total_sessions: int,
    threshold_percent: float = 5.0,
) -> list[dict]:
    """Extract popular flows by grouping sub-sessions by canonical URL pattern.

    Instead of enumerating graph paths (which explodes combinatorially),
    this converts each sub-session into a normalised pattern and counts
    how many sessions match each pattern directly.

    Args:
        sub_sessions: List of sub-sessions, each a list of event dicts.
        total_sessions: Total number of sessions (denominator for popularity).
        threshold_percent: Minimum popularity score (% of sessions) to include.

    Returns:
        List of flow dicts with canonical_pattern, significant_actions,
        popularity_score, session_count, and path_nodes.
    """
    if total_sessions == 0 or not sub_sessions:
        return []

    # Convert each sub-session to (canonical_pattern, path_nodes, actions)
    pattern_groups: dict[str, list[dict]] = {}

    for events in sub_sessions:
        # Convert events to node identifiers (same as flow graph uses)
        path_nodes = []
        for event in events:
            node = _event_to_node(event)
            if node is not None:
                path_nodes.append(node)

        if len(path_nodes) < 3:
            continue

        urls = _extract_urls_from_path(path_nodes)
        pattern = normalise_url_sequence(urls)

        # Include viewport class in pattern so mobile sessions aren't
        # merged with desktop ones (different UI, different interactions).
        viewport_class = _detect_viewport_class(events)
        if viewport_class != "desktop":
            pattern = f"[{viewport_class}] {pattern}"

        if pattern not in pattern_groups:
            pattern_groups[pattern] = []
        pattern_groups[pattern].append({
            "path_nodes": path_nodes,
            "actions": _extract_actions_from_path(path_nodes),
            "events": events,
        })

    # Sub-group each URL-pattern group by interaction fingerprint so that
    # sessions visiting the same pages but performing different interactions
    # become separate flows.  Each dedup strategy can then decide how to
    # merge or split them downstream.
    popular_flows = []
    for pattern, entries in pattern_groups.items():
        # First check if the whole URL group passes the threshold — if not,
        # none of its sub-groups will either.
        group_popularity = (len(entries) / total_sessions) * 100
        if group_popularity < threshold_percent:
            continue

        # Sub-group by interaction fingerprint
        interaction_groups: dict[str, list[dict]] = defaultdict(list)
        for entry in entries:
            fp = _compute_session_fingerprint(entry["events"])
            interaction_groups[fp].append(entry)

        for fp, sub_entries in interaction_groups.items():
            session_count = len(sub_entries)
            popularity = (session_count / total_sessions) * 100

            if popularity < threshold_percent:
                continue

            # Pick the variant with the most action nodes (non-navigation),
            # breaking ties by total path length — this favours sessions
            # that captured more user interactions over navigation-heavy ones.
            representative = max(
                sub_entries,
                key=lambda e: (
                    sum(1 for n in e["path_nodes"] if not n.startswith("nav:")),
                    len(e["path_nodes"]),
                ),
            )

            popular_flows.append({
                "canonical_pattern": pattern,
                "significant_actions": representative["actions"],
                "popularity_score": round(popularity, 2),
                "session_count": session_count,
                "path_nodes": representative["path_nodes"],
                "representative_events": representative["events"],
            })

    # Fallback: rescue below-threshold flows with unique interactions.
    #
    # Complex multi-page flows (cross-page, mobile hamburger, site tours)
    # often produce slightly different URL sequences each time (pages visited
    # in a different order), so they never aggregate by exact pattern.
    #
    # Solution: group below-threshold patterns by PAGE SET (sorted unique
    # pages visited) instead of exact sequence.  If the page-set group has
    # enough sessions with unique interactions, rescue them.
    if popular_flows:
        covered_fingerprints = {
            _compute_session_fingerprint(f["representative_events"])
            for f in popular_flows
        }

        # Collect all below-threshold sessions and group by page set
        page_set_groups: dict[str, list[dict]] = defaultdict(list)
        for pattern, entries in pattern_groups.items():
            group_popularity = (len(entries) / total_sessions) * 100
            if group_popularity >= threshold_percent:
                continue

            for entry in entries:
                # Compute page set key: sorted unique pages
                urls = _extract_urls_from_path(entry["path_nodes"])
                unique_pages = sorted(set(normalise_url(u) for u in urls))
                page_set_key = " + ".join(unique_pages)
                # Carry the original pattern for the flow dict
                entry["_original_pattern"] = pattern
                page_set_groups[page_set_key].append(entry)

        for page_set_key, entries in page_set_groups.items():
            if len(entries) < 2:
                continue  # Too few sessions, likely noise

            # Sub-group by interaction fingerprint within this page set
            interaction_groups: dict[str, list[dict]] = defaultdict(list)
            for entry in entries:
                fp = _compute_session_fingerprint(entry["events"])
                interaction_groups[fp].append(entry)

            for fp, sub_entries in interaction_groups.items():
                if fp in covered_fingerprints or fp == "":
                    continue
                if len(sub_entries) < 2:
                    continue

                session_count = len(sub_entries)
                popularity = (session_count / total_sessions) * 100

                representative = max(
                    sub_entries,
                    key=lambda e: (
                        sum(1 for n in e["path_nodes"] if not n.startswith("nav:")),
                        len(e["path_nodes"]),
                    ),
                )

                # Use page set as canonical pattern for rescued flows
                popular_flows.append({
                    "canonical_pattern": representative.get("_original_pattern", page_set_key),
                    "significant_actions": representative["actions"],
                    "popularity_score": round(popularity, 2),
                    "session_count": session_count,
                    "path_nodes": representative["path_nodes"],
                    "representative_events": representative["events"],
                    "_rescued": True,
                })
                covered_fingerprints.add(fp)

    popular_flows.sort(key=lambda f: f["popularity_score"], reverse=True)
    return popular_flows
