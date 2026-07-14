"""End-to-end example: recorded events -> flow analysis -> Playwright spec.

Run from the repo root:

    pip install -e ".[dev]"
    python examples/generate_from_events.py

This uses hand-written event dicts to stay self-contained. In production these
rows come straight out of the events table (one list per session), exactly the
shape a browser tracker emits.
"""

from __future__ import annotations

import logging

# The generator prefers strictly-typed `event_schema.Event` models but falls
# back to raw dicts when a payload doesn't fully validate — which is expected
# for the trimmed sample events below. Quiet that info-level fallback notice so
# the example output stays focused on the generated spec.
logging.getLogger("testgen.generators.playwright.event_typing").setLevel(logging.ERROR)

from testgen.normalisation.session_splitter import split_session_events
from testgen.graph.flow_builder import build_flow_graph
from testgen.graph.path_extractor import extract_popular_paths_from_sessions
from testgen.generators.playwright.dedup import apply_dedup_strategy
from testgen.generators.playwright.adapter import PlaywrightGenerator


def _event(seq, event_type, url, target=None, payload=None):
    return {
        "event_id": f"evt-{seq}",
        "session_id": "session-1",
        "tab_id": "tab-1",
        "sequence": seq,
        "timestamp": f"2026-03-06T14:00:{seq:02d}.000Z",
        "event_type": event_type,
        "url": url,
        "target": target,
        "payload": payload or {},
    }


def _login_session():
    """A single 'log in' journey, the kind many real users repeat."""
    return [
        _event(1, "navigation", "/", payload={"to_url": "/login"}),
        _event(2, "fill", "/login", target={
            "selectors": {"testid": "email"}, "tag": "INPUT",
        }, payload={"value": "user@example.com"}),
        _event(3, "fill", "/login", target={
            "selectors": {"testid": "password"}, "tag": "INPUT",
        }, payload={"value": "hunter2"}),
        _event(4, "click", "/login", target={
            "selectors": {"role": {"role": "button", "name": "Sign in"}}, "tag": "BUTTON",
        }),
        _event(5, "navigation", "/login", payload={"to_url": "/dashboard"}),
    ]


def main() -> None:
    # Multiple recordings of the same journey -> the flow becomes "popular".
    raw_sessions = [_login_session() for _ in range(4)]
    total_sessions = len(raw_sessions)

    # 1. Split each recording into coherent sub-sessions.
    sub_sessions = []
    for events in raw_sessions:
        sub_sessions.extend(split_session_events(events))

    # 2. Build the directed flow graph (useful for inspection / debugging).
    graph = build_flow_graph(sub_sessions)
    print(f"flow graph: {graph.number_of_nodes()} nodes, {graph.number_of_edges()} edges")

    # 3. Extract flows walked by at least `threshold` percent of sessions.
    flows = extract_popular_paths_from_sessions(sub_sessions, total_sessions, threshold_percent=5.0)
    print(f"popular flows: {len(flows)}")

    # 4. Collapse near-duplicate flows into distinct test files.
    flows = apply_dedup_strategy(flows, "smart")

    # 5. Generate a Playwright spec per flow.
    generator = PlaywrightGenerator()
    variable_map = generator.build_variable_map(flows)
    for i, flow in enumerate(flows):
        spec = generator.generate_script(flow, checkpoints=[], variable_map=variable_map)
        print(f"\n----- generated spec #{i + 1} -----\n")
        print(spec)


if __name__ == "__main__":
    main()
