from __future__ import annotations
import re as _re

from testgen.generators.ts_escape import ts_string_literal

"""Element normalisation — select the best selector strategy for an element.

Priority order (most stable first):
1. role + name (accessibility-based, specific)
2. data-testid / aria-label
3. placeholder (for form inputs)
4. text content (for buttons, links)
5. role without name (structural roles only)
6. CSS selector
7. tag fallback
"""

# Structural/landmark roles where a name-less getByRole is still useful —
# these are typically unique on a page (one <nav>, one <main>, etc.)
_STRUCTURAL_ROLES = frozenset({
    "navigation", "main", "banner", "contentinfo", "complementary",
    "form", "region", "search", "alert", "alertdialog", "dialog",
    "log", "status", "timer", "toolbar", "menu", "menubar",
    "tablist", "tree", "treegrid", "grid", "table", "list",
    "heading", "img", "figure", "separator", "group",
})


def normalise_element(target: dict | None) -> str | None:
    """Normalise a target element to a stable identifier string.

    Args:
        target: Event target dict with selectors, tag, attributes, text_content

    Returns:
        Normalised element identifier string, or None if no target
    """
    if not target:
        return None

    selectors = target.get("selectors", {}) or {}
    tag = target.get("tag", "")
    attributes = target.get("attributes", {}) or {}
    text = target.get("text_content")

    # NOTE: every value below is attacker-controllable captured DOM and is
    # emitted into a downloadable .spec.ts file. It MUST be quoted via
    # ts_string_literal (returns a fully-quoted literal) — never f-string
    # interpolated inside hand-written quotes.

    # 1. Role + name (most resilient when specific)
    role = selectors.get("role")
    if role and isinstance(role, dict) and role.get("role"):
        name = role.get("name", "")
        if name:
            return (
                f'getByRole({ts_string_literal(role["role"])}, '
                f'{{ name: {ts_string_literal(_truncate(name))} }})'
            )
        # Structural roles are often unique on a page — safe without a name
        if role["role"] in _STRUCTURAL_ROLES:
            return f'getByRole({ts_string_literal(role["role"])})'
        # Interaction roles (textbox, button, etc.) without a name are too
        # generic — fall through to more specific selectors below

    # 2. data-testid
    testid = selectors.get("testid") or attributes.get("data-testid")
    if testid:
        return f'getByTestId({ts_string_literal(testid)})'

    # 3. aria-label
    aria_label = attributes.get("aria-label")
    if aria_label:
        return f'getByLabel({ts_string_literal(_truncate(aria_label))})'

    # 4. Placeholder (for inputs)
    placeholder = selectors.get("placeholder")
    if placeholder:
        return f'getByPlaceholder({ts_string_literal(_truncate(placeholder))})'

    # 5. Text content (for buttons, links)
    if text and tag in ("BUTTON", "A", "LABEL") and len(text) <= 50:
        return f'getByText({ts_string_literal(_truncate(text))})'

    # 6. CSS selector fallback
    css = selectors.get("css")
    if css:
        return f'locator({ts_string_literal(css)})'

    # 7. Tag-only fallback
    if tag:
        return f'locator({ts_string_literal(tag.lower())})'

    return None


def fingerprint_element(target: dict | None) -> str:
    """Coarse element identity for fingerprinting — stable across sessions.

    Unlike normalise_element (which picks the *best* selector for code generation),
    this produces a deterministic identity string by combining ALL available
    identity signals.  Two events on the same logical element should always
    return the same fingerprint, even when different sessions capture slightly
    different selector subsets.

    Returns:
        Coarse identity string, or "" if no target.
    """
    if not target:
        return ""

    tag = (target.get("tag") or "").upper()
    selectors = target.get("selectors", {}) or {}
    attributes = target.get("attributes", {}) or {}

    # Collect all identity signals available (order doesn't matter because
    # we include all of them, not just the "best" one).
    parts: list[str] = [tag] if tag else []

    # Role + name — most stable identity when present
    role = selectors.get("role")
    if role and isinstance(role, dict) and role.get("role"):
        parts.append(f'role:{role["role"]}')
        name = role.get("name", "")
        if name:
            # Aggressive whitespace normalisation for stability
            name = _re.sub(r"\s+", " ", name).strip()
            parts.append(f"name:{name}")

    # data-testid — deliberately stable across sessions
    testid = selectors.get("testid") or attributes.get("data-testid")
    if testid:
        parts.append(f"testid:{testid}")

    # CSS selector — structural, usually stable
    css = selectors.get("css")
    if css:
        parts.append(f"css:{css}")

    # Intentionally omit: text_content, placeholder, aria-label —
    # these can vary across sessions (dynamic text, different user data).

    return "|".join(parts) if parts else ""


def get_selector_priority(target: dict | None) -> int:
    """Get the priority level of the best available selector (lower = better).

    Returns:
        Priority level 1-7, or 99 if no selector available.
    """
    if not target:
        return 99

    selectors = target.get("selectors", {}) or {}
    attributes = target.get("attributes", {}) or {}
    text = target.get("text_content")
    tag = target.get("tag", "")

    role = selectors.get("role")
    if role and isinstance(role, dict) and role.get("role"):
        if role.get("name") or role["role"] in _STRUCTURAL_ROLES:
            return 1
    if selectors.get("testid") or attributes.get("data-testid"):
        return 2
    if attributes.get("aria-label"):
        return 3
    if selectors.get("placeholder"):
        return 4
    if text and tag in ("BUTTON", "A", "LABEL") and len(text) <= 50:
        return 5
    if selectors.get("css"):
        return 6
    if tag:
        return 7
    return 99


def _truncate(s: str, max_len: int = 50) -> str:
    """Collapse whitespace and truncate a captured string for readable selectors.

    Escaping is NOT done here — callers must pass the result through
    ``ts_string_literal`` (single source of truth for string-literal escaping)
    before emitting it into generated code.
    """
    # Collapse all whitespace (newlines, tabs, multi-spaces) to single spaces
    s = _re.sub(r"\s+", " ", s).strip()
    if len(s) > max_len:
        return s[:max_len] + "..."
    return s
