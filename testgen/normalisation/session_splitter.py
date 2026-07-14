from __future__ import annotations
"""Session splitter — splits events into sub-sessions by tab and idle gaps.

Two-stage splitting:
1. Split by tab_id — events from different browser tabs become separate sub-sessions.
2. Split by idle gaps — events within the same tab separated by 30+ minutes are split.

This prevents multi-tab event interleaving from creating nonsensical flow graphs.
"""

from collections import defaultdict
from datetime import datetime, timedelta, timezone

IDLE_THRESHOLD = timedelta(minutes=30)


def split_session_events(events: list[dict]) -> list[list[dict]]:
    """Split a list of events into sub-sessions by tab and idle gaps.

    First groups events by tab_id (so events from different browser tabs
    become separate sub-sessions), then splits each tab's events at
    30-minute idle gaps.

    Args:
        events: List of event dicts sorted by sequence, each with
                'timestamp' and optionally 'tab_id' fields.

    Returns:
        List of sub-sessions, each a list of events.
    """
    if not events:
        return []

    # Stage 1: Group events by tab_id
    tab_groups = _group_by_tab(events)

    # Stage 2: Split each tab's events at idle gaps
    sub_sessions = []
    for tab_events in tab_groups:
        sub_sessions.extend(_split_at_idle_gaps(tab_events))

    return sub_sessions


def _group_by_tab(events: list[dict]) -> list[list[dict]]:
    """Split events into groups when concurrent tabs are detected.

    The tracker creates a new tab_id on every page navigation (script
    reinitializes), so we can NOT just split on tab_id — that would break
    every multi-page flow.

    Instead, detect CONCURRENT tabs: events from different tab_ids whose
    timestamps overlap.  Only split when a second tab is actively receiving
    events while the first tab is still active.

    If no concurrent tabs are detected, returns all events as one group.
    """
    if len(events) < 2:
        return [events] if events else []

    # Group events by tab_id with their timestamp ranges
    tabs: dict[str | None, list[dict]] = defaultdict(list)
    for event in events:
        tabs[event.get("tab_id")].append(event)

    if len(tabs) <= 1:
        return [events]

    # Compute timestamp range for each tab_id
    tab_ranges: dict[str | None, tuple[datetime | None, datetime | None]] = {}
    for tab_id, tab_events in tabs.items():
        times = [_parse_timestamp(e.get("timestamp")) for e in tab_events]
        valid_times = [t for t in times if t is not None]
        if valid_times:
            tab_ranges[tab_id] = (min(valid_times), max(valid_times))

    # Detect temporal overlap between any pair of tab_ids.
    # Two tabs overlap if tab_A.start < tab_B.end AND tab_B.start < tab_A.end.
    tab_ids = list(tab_ranges.keys())
    has_concurrent = False
    concurrent_tabs: set[str | None] = set()

    for i in range(len(tab_ids)):
        for j in range(i + 1, len(tab_ids)):
            a_start, a_end = tab_ranges[tab_ids[i]]
            b_start, b_end = tab_ranges[tab_ids[j]]
            if a_start and a_end and b_start and b_end:
                if a_start < b_end and b_start < a_end:
                    has_concurrent = True
                    concurrent_tabs.add(tab_ids[i])
                    concurrent_tabs.add(tab_ids[j])

    if not has_concurrent:
        # No concurrent tabs — all tab_id changes are just page navigations.
        # Keep everything as one group.
        return [events]

    # Split: events from concurrent tabs become separate groups.
    # Events from non-concurrent tab_ids stay in the "main" group.
    main_events = []
    concurrent_groups: dict[str | None, list[dict]] = defaultdict(list)

    for event in events:
        tab_id = event.get("tab_id")
        if tab_id in concurrent_tabs:
            concurrent_groups[tab_id].append(event)
        else:
            main_events.append(event)

    result = []
    if main_events:
        result.append(main_events)
    for tab_id, tab_events in concurrent_groups.items():
        tab_events.sort(key=lambda e: (e.get("timestamp", ""), e.get("sequence", 0)))
        result.append(tab_events)
    return result


def _split_at_idle_gaps(events: list[dict]) -> list[list[dict]]:
    """Split events at 30-minute idle gaps."""
    if not events:
        return []

    sub_sessions = []
    current_sub = [events[0]]

    for i in range(1, len(events)):
        prev_time = _parse_timestamp(events[i - 1].get("timestamp", ""))
        curr_time = _parse_timestamp(events[i].get("timestamp", ""))

        if prev_time and curr_time and (curr_time - prev_time) > IDLE_THRESHOLD:
            sub_sessions.append(current_sub)
            current_sub = [events[i]]
        else:
            current_sub.append(events[i])

    if current_sub:
        sub_sessions.append(current_sub)

    return sub_sessions


def _parse_timestamp(ts) -> datetime | None:
    """Parse a timestamp — handles both datetime objects (from psycopg2) and ISO strings."""
    if isinstance(ts, datetime):
        return ts
    if not ts:
        return None
    try:
        ts_str = str(ts)
        if ts_str.endswith("Z"):
            ts_str = ts_str[:-1] + "+00:00"
        return datetime.fromisoformat(ts_str)
    except (ValueError, TypeError):
        return None
