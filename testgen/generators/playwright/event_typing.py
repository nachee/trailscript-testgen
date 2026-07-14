"""Bridge between the analysis DB storage shape and the canonical event schema.

The analysis service reads events from PostgreSQL in a *storage* shape:
flat ``url`` and ``page_context`` columns, no ``schema_version``. The
generated Pydantic models (``event_schema.Event``) validate the *wire* shape:
a required ``schema_version`` and a nested ``page`` object. These functions
reconstruct the wire shape from a DB row so the typed models can be used.
"""

from __future__ import annotations

import logging

from pydantic import ValidationError

from event_schema import Event

logger = logging.getLogger(__name__)

# Canonical envelope fields that carry straight through from a DB row.
_ENVELOPE_KEYS = (
    "event_id",
    "session_id",
    "tab_id",
    "sequence",
    "timestamp",
    "event_type",
    "target",
    "payload",
)


def reconstruct_event(row: dict) -> dict:
    """Map a DB-row event dict (storage shape) to the canonical wire shape.

    Folds the flat ``url`` column and the ``page_context`` JSONB (``title``,
    ``viewport``) back into a nested ``page`` object, and supplies
    ``schema_version`` (from the row if present, else ``1`` — safe today
    because migration 012 backfilled every row to 1 and the model only
    knows ``Literal[1]``).
    """
    # `url` is effectively NOT NULL from the writer; default to "" so a row
    # missing it degrades gracefully rather than raising inside this helper
    # (a raise here would escape parse_flow_events' ValidationError catch).
    page = {"url": row.get("url", "")}
    page.update(row.get("page_context") or {})
    out = {k: row[k] for k in _ENVELOPE_KEYS if k in row}
    out["page"] = page
    out["schema_version"] = row.get("schema_version", 1)
    return out


def parse_flow_events(events: list[dict]) -> list | None:
    """Reconstruct + validate every event in a flow.

    Returns the list of typed event variants (``Event.model_validate(...).root``)
    on full success, or ``None`` (with a logged warning) if ANY event fails
    validation. The ``None`` is the per-flow lenient-fallback signal: callers
    drop back to raw dict access for that flow rather than failing the run.

    Caveat for callers using the typed result: a dumped typed model
    (``ev.model_dump()``) materialises every optional field as an explicit
    ``None`` key, so it is NOT byte-identical to the original DB-row dict.
    Only ``.get()``-style / truthiness access is safe to assume equivalent
    between the typed and the raw-dict paths.
    """
    try:
        return [Event.model_validate(reconstruct_event(e)).root for e in events]
    except ValidationError as exc:
        # The event union is non-discriminated (32 variants), so a single bad
        # event yields a very large ValidationError. Keep WARNING bounded;
        # full detail goes to DEBUG.
        logger.warning(
            "Typed event parse failed for flow (%d events, %d validation errors); "
            "using dict fallback.",
            len(events),
            exc.error_count(),
        )
        logger.debug("Flow validation errors: %s", exc)
        return None
