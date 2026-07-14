from __future__ import annotations
"""AI refinement post-processor for generated scripts.

Orchestrates the Claude API to improve deterministic scripts.
"""

import logging
from typing import Any

from .claude_client import refine_script

logger = logging.getLogger(__name__)


def refine_scripts(
    scripts: list[dict[str, Any]],
    flows: list[dict[str, Any]],
    checkpoints_by_flow: dict[str, list[dict]] | None = None,
) -> list[dict[str, Any]]:
    """Refine a list of generated scripts using AI.

    Args:
        scripts: List of { flow_pattern, content, filename } dicts
        flows: The flow data corresponding to each script
        checkpoints_by_flow: Optional mapping of flow pattern → checkpoint data

    Returns:
        Updated scripts with refined content and ai_refined flag
    """
    if not checkpoints_by_flow:
        checkpoints_by_flow = {}

    flow_by_pattern = {f["canonical_pattern"]: f for f in flows}
    refined = []

    for script in scripts:
        pattern = script.get("flow_pattern", "")
        flow = flow_by_pattern.get(pattern, {})
        checkpoints = checkpoints_by_flow.get(pattern, [])

        try:
            improved_content = refine_script(
                script["content"],
                flow,
                checkpoints,
            )

            refined.append({
                **script,
                "content": improved_content,
                "ai_refined": True,
            })
            logger.info("AI refined script for flow: %s", pattern)

        except Exception as e:
            logger.warning("AI refinement failed for %s: %s", pattern, e)
            refined.append({
                **script,
                "ai_refined": False,
            })

    return refined
