from __future__ import annotations
"""Flow graph construction using NetworkX.

Builds a directed graph where:
- Nodes = normalised URL + significant actions
- Edges = transitions between nodes, weighted by session count
"""

import networkx as nx

from testgen.normalisation.url_normaliser import normalise_url
from testgen.normalisation.element_normaliser import normalise_element


def build_flow_graph(sessions_events: list[list[dict]]) -> nx.DiGraph:
    """Build a flow graph from multiple sessions' events.

    Args:
        sessions_events: List of sub-sessions, each a list of events sorted by sequence.

    Returns:
        NetworkX DiGraph with weighted edges.
    """
    graph = nx.DiGraph()

    for events in sessions_events:
        if len(events) < 2:
            continue

        prev_node = None

        for event in events:
            node = _event_to_node(event)
            if node is None:
                continue

            # Add or update node
            if graph.has_node(node):
                graph.nodes[node]["count"] = graph.nodes[node].get("count", 0) + 1
            else:
                graph.add_node(node, count=1, url=event.get("url", ""), event_type=event.get("event_type", ""))

            # Add edge from previous node
            if prev_node and prev_node != node:
                if graph.has_edge(prev_node, node):
                    graph[prev_node][node]["weight"] += 1
                else:
                    graph.add_edge(prev_node, node, weight=1)

            prev_node = node

    return graph


def _event_to_node(event: dict) -> str | None:
    """Convert an event to a graph node identifier.

    Combines normalised URL with the significant action to create
    a unique node identity.
    """
    event_type = event.get("event_type", "")

    # Skip non-significant events for flow construction
    if event_type in ("scroll", "hover", "focus", "api_request", "api_error", "page_load"):
        return None

    url = normalise_url(event.get("url", ""))
    target = event.get("target")
    element = normalise_element(target) if target else None

    if event_type == "navigation":
        payload = event.get("payload", {})
        to_url = normalise_url(payload.get("to_url", ""))
        return f"nav:{to_url}"

    if element:
        return f"{url}||{event_type}||{element}"

    return f"{url}||{event_type}"


def extract_flow_paths(
    graph: nx.DiGraph,
    max_paths: int = 500,
    cutoff: int = 12,
) -> list[list[str]]:
    """Extract popular paths from entry points to exit points in the graph.

    Uses weight-guided search to find the most-traversed paths efficiently,
    avoiding combinatorial explosion on dense graphs.

    Entry points: nodes with no incoming edges (true sources).
    Exit points: nodes with no outgoing edges.
    """
    if len(graph) == 0:
        return []

    # Only use true source nodes as entries (in_degree == 0)
    entry_nodes = [n for n in graph.nodes() if graph.in_degree(n) == 0]

    # Fall back to highest-count nav nodes if no true sources
    if not entry_nodes:
        nav_nodes = [(n, graph.nodes[n].get("count", 0))
                     for n in graph.nodes() if n.startswith("nav:")]
        nav_nodes.sort(key=lambda x: x[1], reverse=True)
        entry_nodes = [n for n, _ in nav_nodes[:5]] if nav_nodes else list(graph.nodes())[:1]

    exit_nodes = set(n for n in graph.nodes() if graph.out_degree(n) == 0)
    if not exit_nodes:
        exit_nodes = {list(graph.nodes())[-1]}

    paths = []
    seen_signatures: set[tuple] = set()

    for entry in entry_nodes:
        if len(paths) >= max_paths:
            break
        # Use weight-guided DFS: at each step, explore neighbours
        # in descending edge-weight order so popular paths come first.
        _weight_guided_dfs(
            graph, entry, exit_nodes, cutoff, max_paths,
            paths, seen_signatures,
        )

    return paths


def _weight_guided_dfs(
    graph: nx.DiGraph,
    start: str,
    exit_nodes: set[str],
    cutoff: int,
    max_paths: int,
    paths: list[list[str]],
    seen_signatures: set[tuple],
) -> None:
    """Iterative DFS that explores highest-weight edges first.

    Collects paths that reach an exit node with >= 3 nodes.
    Stops early once max_paths is reached.
    """
    # Stack entries: (current_node, path_so_far, visited_set)
    stack: list[tuple[str, list[str], set[str]]] = [(start, [start], {start})]

    while stack and len(paths) < max_paths:
        node, path, visited = stack.pop()

        # If we reached an exit node with enough steps, record the path
        if node in exit_nodes and len(path) >= 3:
            sig = tuple(path)
            if sig not in seen_signatures:
                seen_signatures.add(sig)
                paths.append(list(path))
                if len(paths) >= max_paths:
                    return

        # Don't expand beyond cutoff depth
        if len(path) >= cutoff:
            continue

        # Get neighbours sorted by weight descending (popular first)
        neighbours = []
        for nbr in graph.successors(node):
            if nbr not in visited:
                w = graph[node][nbr].get("weight", 0)
                neighbours.append((w, nbr))
        neighbours.sort()  # ascending so highest-weight gets popped last (DFS)

        for _w, nbr in neighbours:
            stack.append((nbr, path + [nbr], visited | {nbr}))
