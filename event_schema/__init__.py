"""Pydantic v2 models generated from ``packages/event-schema/src`` (Zod).

The Zod source remains the single source of truth — never edit ``event.py``
or ``checkpoint.py`` by hand. Regenerate with ``make python-codegen`` from
the repo root.

Public API
----------

``Event``
    Discriminated-union RootModel covering every event variant. Call
    ``Event.model_validate(event_dict)`` to parse and validate, then read
    ``.root`` to get the typed event variant (``Event1`` … ``Event32``).
``Checkpoint``
    Checkpoint envelope BaseModel. Call ``Checkpoint.model_validate(d)``
    to parse and validate; the result has typed fields directly (no
    ``.root`` unwrap needed).

Example
-------

::

    from event_schema import Event, Checkpoint

    parsed = Event.model_validate(raw_event_dict).root
    if parsed.event_type == "click":
        # parsed.payload is typed as the click payload schema
        ...

    cp = Checkpoint.model_validate(raw_checkpoint_dict)
    print(cp.checkpoint_id, cp.url)

Note
----

The generated modules also expose top-level ``Model`` classes that wrap
``Event`` / ``Checkpoint`` in another ``RootModel`` — an artefact of the
JSON Schema's $ref wrapper. They are redundant and intentionally not
re-exported here.
"""

from .checkpoint import Checkpoint
from .event import Event

__all__ = ["Event", "Checkpoint"]
