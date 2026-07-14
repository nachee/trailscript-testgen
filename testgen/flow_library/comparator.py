from __future__ import annotations
"""Flow comparison engine.

Compares newly detected flows against the existing flow library to classify
each flow as: new, changed, unchanged, or no_longer_occurring.
"""

from typing import Any


def compare_flows(
    new_flows: list[dict[str, Any]],
    existing_flows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Compare new flows against existing library.

    Returns a list of comparison results with:
      - canonical_pattern
      - status: 'new' | 'changed' | 'unchanged' | 'inactive'
      - new_flow: the new flow data (or None for inactive)
      - existing_flow: the existing flow data (or None for new)
    """
    existing_by_pattern = {f["canonical_pattern"]: f for f in existing_flows}
    new_by_pattern = {f["canonical_pattern"]: f for f in new_flows}

    results = []

    # Check each new flow against existing library
    for pattern, new_flow in new_by_pattern.items():
        existing = existing_by_pattern.get(pattern)
        if existing is None:
            results.append({
                "canonical_pattern": pattern,
                "status": "new",
                "new_flow": new_flow,
                "existing_flow": None,
            })
        elif _flow_changed(new_flow, existing):
            results.append({
                "canonical_pattern": pattern,
                "status": "changed",
                "new_flow": new_flow,
                "existing_flow": existing,
            })
        else:
            results.append({
                "canonical_pattern": pattern,
                "status": "unchanged",
                "new_flow": new_flow,
                "existing_flow": existing,
            })

    # Check for flows that no longer occur
    for pattern, existing in existing_by_pattern.items():
        if pattern not in new_by_pattern and existing.get("status") == "active":
            results.append({
                "canonical_pattern": pattern,
                "status": "inactive",
                "new_flow": None,
                "existing_flow": existing,
            })

    return results


def _flow_changed(new_flow: dict, existing_flow: dict) -> bool:
    """Determine if a flow has meaningfully changed."""
    # Compare significant actions
    new_actions = new_flow.get("significant_actions", [])
    existing_actions = existing_flow.get("significant_actions", [])

    if len(new_actions) != len(existing_actions):
        return True

    for new_action, old_action in zip(new_actions, existing_actions):
        if new_action.get("type") != old_action.get("type"):
            return True
        if new_action.get("url") != old_action.get("url"):
            return True
        if new_action.get("element") != old_action.get("element"):
            return True

    return False
