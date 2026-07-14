from __future__ import annotations
"""URL exclusion flow splitting.

Splits sessions at excluded URLs and drops partial flows with < 3 events.
"""

import fnmatch
import re
from typing import Any
from urllib.parse import urlparse


def split_by_exclusions(
    events: list[dict[str, Any]],
    exclusion_patterns: list[str],
) -> list[list[dict[str, Any]]]:
    """Split a session's events at excluded URL boundaries.

    Args:
        events: Ordered list of events for a session
        exclusion_patterns: URL patterns to exclude (supports * wildcards)

    Returns:
        List of event groups (sub-sessions), each with >= min_events events
    """
    if not exclusion_patterns:
        return [events] if events else []

    segments: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []

    for event in events:
        url = event.get("url", "")
        if _is_excluded(url, exclusion_patterns):
            if current:
                segments.append(current)
                current = []
        else:
            current.append(event)

    if current:
        segments.append(current)

    # Drop segments with fewer than 3 events (partial flows)
    return [seg for seg in segments if len(seg) >= 3]


def _is_excluded(url: str, patterns: list[str]) -> bool:
    """Check if a URL matches any exclusion pattern."""
    parsed = urlparse(url)
    path = parsed.path or "/"

    for pattern in patterns:
        # Exact match on full URL
        if url == pattern:
            return True
        # Wildcard match on path
        if fnmatch.fnmatch(path, pattern):
            return True
        # Try matching with pattern as path only
        parsed_pattern = urlparse(pattern)
        if parsed_pattern.scheme and fnmatch.fnmatch(url, pattern):
            return True

    return False
