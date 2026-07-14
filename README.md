# trailscript-testgen

Turn recorded user-interaction event streams into runnable **Playwright** end-to-end tests.

Given the events a browser tracker emits as real users move through a site — clicks, fills,
navigations, checkpoints — this library finds the flows people actually walk, deduplicates
near-identical journeys into distinct test files, and generates deterministic Playwright specs
from Jinja2 templates. An optional pass refines the output with the Claude API behind a strict
allow-list security gate.

This is the analysis-and-generation core extracted from **TrailScript**, an automated
E2E-test-generation product. The surrounding service (Celery workers, PostgreSQL reads, R2
storage, the customer dashboard) is intentionally left out — this repo is the pure,
self-contained pipeline: **events → flow graph → popular paths → dedup → codegen**.

The events it consumes are produced by [`trailscript-tracker`](https://github.com/nachee/trailscript-tracker)
and typed by [`trailscript-event-schema`](https://github.com/nachee/trailscript-event-schema), whose
generated Pydantic models are vendored here as `event_schema/`.

```
 raw events ──▶ session split ──▶ flow graph ──▶ popular paths ──▶ dedup ──▶ Playwright spec
   (dicts)      (normalisation)   (NetworkX)     (thresholded)   (strategies)  (Jinja2 [+ Claude])
```

## Why it's interesting

- **Flow analysis, not path enumeration.** Sessions are normalised to canonical URL/element
  patterns and counted directly, so popularity scoring stays linear instead of exploding
  combinatorially over graph paths (`testgen/graph/`).
- **Multiple selector strategies, ranked.** Each element is resolved to the most stable
  Playwright locator available — `getByRole`, `getByTestId`, `getByLabel`, text, then CSS/XPath
  fallbacks — with a fingerprint used for deduplication (`testgen/normalisation/`).
- **Four dedup strategies.** `full`, `smart`, `lean`, and `modular` trade coverage against test
  count differently (`testgen/generators/playwright/dedup.py`).
- **Deterministic codegen with a typed contract.** Generation is pure Python + Jinja2. Events are
  validated against generated Pydantic models (`event_schema`) with a graceful raw-dict fallback.
- **LLM refinement behind a real security boundary.** The optional Claude pass treats the model
  round-trip as untrusted on *both* sides: captured DOM text is wrapped as inert data to blunt
  prompt injection, and the returned script must pass a structural **allow-list gate**
  (`testgen/ai/ts_guard.py`) — a hand-rolled JS/TS lexical scanner that rejects anything outside
  the known-safe Playwright surface — before it's ever used. See that module's docstring for the
  full threat model.

## Install

Requires Python 3.11+.

```bash
pip install -e ".[dev]"     # core + test tooling
# optional LLM refinement layer:
pip install -e ".[ai]"      # adds the anthropic client (needs ANTHROPIC_API_KEY at runtime)
```

## Quick start

```bash
python examples/generate_from_events.py
```

That script feeds a handful of hand-written "log in" recordings through the whole pipeline and
prints the generated spec. Abbreviated output:

```typescript
import { test, expect } from '@playwright/test';
import { testVariables } from './variables';

test.describe("login to dashboard", () => {
  test("should complete login to dashboard", async ({ page }) => {
    await page.goto("/login");

    await page.getByTestId("email").fill(testVariables.email);
    await expect(page.getByTestId("email")).toHaveValue(testVariables.email);

    await page.getByRole("button", { name: "Sign in" }).click();

    await page.waitForURL("/dashboard");
    await expect(page).toHaveURL(/\/dashboard/);
  });
});
```

Programmatically, the pipeline is five calls:

```python
from testgen.normalisation.session_splitter import split_session_events
from testgen.graph.flow_builder import build_flow_graph
from testgen.graph.path_extractor import extract_popular_paths_from_sessions
from testgen.generators.playwright.dedup import apply_dedup_strategy
from testgen.generators.playwright.adapter import PlaywrightGenerator

sub_sessions = [ss for events in raw_sessions for ss in split_session_events(events)]
build_flow_graph(sub_sessions)  # optional: inspect the directed graph
flows = extract_popular_paths_from_sessions(sub_sessions, total_sessions=len(raw_sessions),
                                            threshold_percent=5.0)
flows = apply_dedup_strategy(flows, "smart")

gen = PlaywrightGenerator()
variable_map = gen.build_variable_map(flows)
spec = gen.generate_script(flows[0], checkpoints=[], variable_map=variable_map)
```

## Layout

```
testgen/
├── normalisation/      URL + element canonicalisation, session/exclusion splitting
├── graph/              NetworkX flow-graph construction and popular-path extraction
├── generators/
│   ├── base.py         generator interface + shared TS-escaping helpers
│   └── playwright/     Playwright adapter, dedup strategies, event typing, Jinja2 templates
├── flow_library/       flow-to-flow comparison (drift detection across runs)
└── ai/                 optional Claude refinement + the allow-list security gate
event_schema/           vendored generated Pydantic models (the event wire contract)
examples/               runnable end-to-end example
tests/                  363 unit + snapshot tests
```

## Tests

```bash
pytest            # 363 passing
```

## Event shape

Events are plain dicts (or `event_schema.Event` models). The fields the pipeline reads:

| field | meaning |
|---|---|
| `event_type` | `navigation` \| `click` \| `fill` \| `select` \| … |
| `url` | page the event happened on |
| `target.selectors` | ranked locator strategies (`role`, `testid`, `label`, `text`, `css`, `xpath`) |
| `target.tag` | element tag name |
| `payload` | event-specific data (`to_url` for navigation, `value` for fills, …) |
| `sequence` / `timestamp` | ordering within a session |

See `examples/generate_from_events.py` for a complete, minimal instance of each.

## License

MIT — see [LICENSE](LICENSE).
