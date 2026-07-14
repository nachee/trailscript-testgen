from __future__ import annotations
"""Variables file generator — extracts dynamic values from normalised flows.

Groups variables by resource type, adds format specs and usage comments.
"""

from testgen.normalisation.element_normaliser import normalise_element
from testgen.generators.ts_escape import ts_string_literal, ts_block_comment


def _safe_block_comment(text: str) -> str:
    """Neutralise a string for safe use inside a ``/* ... */`` block comment.

    ``description`` embeds the attacker-controllable normalised locator, so a
    ``*/`` would close the comment early (breaking out into code) and a newline
    could smuggle a statement onto its own line. Delegates to the shared
    ``ts_escape.ts_block_comment`` so the codegen comment-defang logic lives in
    one place.
    """
    return ts_block_comment(text, max_len=None)


def generate_variables_file(flows: list[dict]) -> str:
    """Generate a TypeScript variables file for test data.

    Args:
        flows: List of flow dicts with significant_actions and/or representative_events.

    Returns:
        TypeScript variables file content.
    """
    variables: dict[str, dict] = {}

    for flow in flows:
        # Legacy: significant_actions
        for action in flow.get("significant_actions", []):
            _extract_variables(action, variables)
        # New: representative_events (carries actual recorded values)
        for event in flow.get("representative_events", []):
            _extract_variables_from_event(event, variables)

    if not variables:
        return _render_empty_variables()

    return _render_variables(variables)


def _extract_variables_from_event(event: dict, variables: dict) -> None:
    """Extract dynamic values from a representative event."""
    event_type = event.get("event_type", "")
    if event_type != "fill":
        return
    target = event.get("target")
    if not target:
        return
    element = normalise_element(target)
    if not element:
        return
    var_name = _element_to_var_name(element)
    if not var_name or var_name in variables:
        return
    payload = event.get("payload", {}) or {}
    value = payload.get("value", "")
    if value == "[REDACTED]":
        value = "TestPass123!"
    variables[var_name] = {
        "type": "string",
        "description": f"Form value for {element}",
        "example": value,
    }


def _extract_variables(action: dict, variables: dict) -> None:
    """Extract dynamic values from a flow action."""
    url = action.get("url", "")
    element = action.get("element", "")
    action_type = action.get("type", "")

    # Extract URL parameters
    if ":uuid" in url or ":id" in url or ":slug" in url:
        param_name = _url_to_var_name(url)
        if param_name and param_name not in variables:
            variables[param_name] = {
                "type": "string",
                "description": f"Dynamic URL parameter from {url}",
                "example": _get_example_for_url_param(url),
            }

    # Extract form values from fill actions
    if action_type == "fill" and element:
        var_name = _element_to_var_name(element)
        if var_name and var_name not in variables:
            variables[var_name] = {
                "type": "string",
                "description": f"Form value for {element}",
                "example": "",
            }


def _url_to_var_name(url: str) -> str | None:
    """Convert a normalised URL to a variable name."""
    parts = url.strip("/").split("/")
    for i, part in enumerate(parts):
        if part.startswith(":"):
            prefix = parts[i - 1] if i > 0 else "resource"
            return f"{prefix}_{part[1:]}"
    return None


def _element_to_var_name(element: str) -> str | None:
    """Convert an element selector to a variable name.

    For getByRole locators, extracts the ``name`` option (e.g.
    ``getByRole("textbox", { name: "Full Name *" })`` → ``fullName``).
    Falls back to the role/first-string argument when no ``name`` is present.
    """
    import re as _re

    if element.startswith("getByRole"):
        # Prefer the human-readable { name: "..." } option
        name_match = _re.search(r'name:\s*"([^"]*)"', element)
        if name_match:
            return _to_camel_case(name_match.group(1))
        # Fallback: use the role string itself
        start = element.find('"')
        end = element.find('"', start + 1)
        if start >= 0 and end > start:
            return _to_camel_case(element[start + 1 : end])
        return None

    for prefix in ["getByTestId", "getByLabel", "getByPlaceholder"]:
        if element.startswith(prefix):
            start = element.find('"')
            end = element.find('"', start + 1)
            if start >= 0 and end > start:
                name = element[start + 1 : end]
                return _to_camel_case(name)
    return None


def _get_example_for_url_param(url: str) -> str:
    """Get an example value for a URL parameter type."""
    if ":uuid" in url:
        return "00000000-0000-4000-8000-000000000001"
    if ":id" in url:
        return "1"
    if ":slug" in url:
        return "example-item"
    return ""


def _to_camel_case(s: str) -> str:
    """Convert a string to camelCase.

    Strips non-alphanumeric characters (except hyphens/underscores/spaces)
    before converting, so inputs like 'john@example.com' become 'johnExampleCom'.
    """
    import re
    # Replace non-alphanumeric chars (except space, hyphen, underscore) with space
    s = re.sub(r"[^a-zA-Z0-9 \-_]", " ", s)
    s = s.replace("-", " ").replace("_", " ")
    parts = s.split()
    if not parts:
        return "unnamed"
    return parts[0].lower() + "".join(p.capitalize() for p in parts[1:])


def _render_variables(variables: dict) -> str:
    """Render the variables TypeScript file."""
    lines = [
        "/**",
        " * Test variables — dynamic values extracted from user flows.",
        " * Update these values for your test environment.",
        " *",
        " * Generated by TrailScript",
        " */",
        "",
        "export const testVariables = {",
    ]

    for name, info in variables.items():
        # description embeds the attacker-controlled locator → sanitise for the
        # block-comment context; example is the captured fill value → emit as a
        # properly escaped TS string literal.
        lines.append(f'  /** {_safe_block_comment(info["description"])} */')
        lines.append(f'  {name}: {ts_string_literal(info["example"])},')
        lines.append("")

    lines.append("};")
    lines.append("")

    return "\n".join(lines)


def _render_empty_variables() -> str:
    """Render an empty variables file."""
    return """/**
 * Test variables — no dynamic values detected.
 * Add custom test data here as needed.
 *
 * Generated by TrailScript
 */

export const testVariables = {};
"""
