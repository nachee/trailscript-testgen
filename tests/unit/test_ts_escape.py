"""Security tests for TS/JS string-literal escaping in generated Playwright code.

These are the regression tests for the code-injection fix: attacker-controllable
captured DOM (test IDs, text, ARIA names, attribute values, URLs, CSS selectors)
is string-formatted into downloadable ``.spec.ts`` files. A malicious value must
never break out of its string literal.
"""
import json

from testgen.generators.ts_escape import (
    ts_string_literal,
    ts_line_comment,
    ts_block_comment,
)
from testgen.normalisation.element_normaliser import normalise_element
from testgen.generators.playwright.adapter import PlaywrightGenerator
from testgen.generators.variables import generate_variables_file

_LS = chr(0x2028)  # JS line separator
_PS = chr(0x2029)  # JS paragraph separator

# Payloads that would break out of a naive `"{value}"` interpolation.
ADVERSARIAL = [
    'x"); execSync("id"); ("',
    'plain"double',
    "plain'single",
    "back`tick",
    "interp${process.env.SECRET}",
    "line\nbreak",
    "carriage\rreturn",
    "tab\tchar",
    "back\\slash",
    "</script><script>alert(1)</script>",
    "close*/comment",
    'a" + require("child_process").execSync("id") + "b',
    "sep" + _LS + _PS + "arators",
]


def _round_trips(payload: str) -> None:
    """The emitted literal must be valid and decode back to the exact payload."""
    literal = ts_string_literal(payload)
    assert literal.startswith('"') and literal.endswith('"')
    # It is a valid JS/JSON string literal that decodes to the original value
    # (up to the length cap). No breakout: the raw payload never appears
    # unescaped between the surrounding quotes.
    decoded = json.loads(literal)
    assert decoded == payload[:250]


class TestTsStringLiteral:
    def test_clean_inputs_unchanged_semantics(self):
        # Legitimate selectors/text must still produce the obvious literal.
        assert ts_string_literal("Submit") == '"Submit"'
        assert ts_string_literal("submit-btn") == '"submit-btn"'
        assert ts_string_literal("#main-content") == '"#main-content"'
        assert ts_string_literal("input#email") == '"input#email"'
        assert ts_string_literal("+ 10%") == '"+ 10%"'

    def test_double_quote_is_escaped(self):
        lit = ts_string_literal('a"b')
        assert lit == '"a\\"b"'
        # Every interior double quote is backslash-escaped (none stands alone).
        assert lit.count('\\"') == 1
        assert json.loads(lit) == 'a"b'

    def test_backtick_and_interpolation_escaped(self):
        lit = ts_string_literal("`${x}`")
        assert "`" not in lit
        assert "${" not in lit

    def test_angle_bracket_escaped(self):
        lit = ts_string_literal("</script>")
        assert "<" not in lit  # neutralises </script> breakout

    def test_line_separators_escaped(self):
        lit = ts_string_literal("a" + _LS + "b" + _PS + "c")
        assert _LS not in lit
        assert _PS not in lit
        assert "\\u2028" in lit
        assert "\\u2029" in lit

    def test_newline_escaped(self):
        assert "\n" not in ts_string_literal("a\nb")[1:-1]

    def test_none_and_non_str(self):
        assert ts_string_literal(None) == '""'
        assert ts_string_literal(42) == '"42"'

    def test_length_cap(self):
        payload = "A" * 5000
        lit = ts_string_literal(payload)
        assert len(json.loads(lit)) == 250

    def test_all_adversarial_round_trip(self):
        for payload in ADVERSARIAL:
            _round_trips(payload)


class TestNormaliserNoBreakout:
    """Injection through element_normaliser selector sinks."""

    def test_testid_injection_contained(self):
        target = {"selectors": {"testid": 'x"); evil(); ("'}, "tag": "BUTTON"}
        result = normalise_element(target)
        # Locator is getByTestId(<literal>) — the payload lives inside the literal.
        assert result.startswith("getByTestId(")
        inner = result[len("getByTestId(") : -1]
        assert json.loads(inner) == 'x"); evil(); ("'

    def test_css_injection_contained(self):
        target = {"selectors": {"css": '"]); execSync("id"); //'}, "tag": "DIV"}
        result = normalise_element(target)
        assert result.startswith("locator(")
        inner = result[len("locator(") : -1]
        assert json.loads(inner) == '"]); execSync("id"); //'

    def test_role_name_injection_contained(self):
        target = {
            "selectors": {"role": {"role": "button", "name": 'a" }); evil(); ({ "b'}},
            "tag": "BUTTON",
        }
        result = normalise_element(target)
        # getByRole("button", { name: "<escaped>" })
        prefix = 'getByRole("button", { name: '
        assert result.startswith(prefix)
        assert result.endswith(" })")
        # The name literal round-trips to the payload — proving it's contained
        # inside a properly escaped literal, not broken out into code.
        name_literal = result[len(prefix) : -len(" })")]
        assert json.loads(name_literal) == 'a" }); evil(); ({ "b'

    def test_clean_selectors_still_work(self):
        assert normalise_element(
            {"selectors": {"role": {"role": "button", "name": "Submit"}}, "tag": "BUTTON"}
        ) == 'getByRole("button", { name: "Submit" })'
        assert normalise_element(
            {"selectors": {"testid": "submit-btn"}, "tag": "BUTTON"}
        ) == 'getByTestId("submit-btn")'
        assert normalise_element(
            {"selectors": {"css": "#main-content"}, "tag": "DIV"}
        ) == 'locator("#main-content")'


class TestGeneratedScriptNoBreakout:
    """End-to-end: adversarial captured DOM must not break the generated script."""

    @staticmethod
    def _event(event_type, url, target=None, payload=None, sequence=1):
        return {
            "event_id": f"evt-{sequence}",
            "session_id": "session-1",
            "tab_id": "tab-1",
            "sequence": sequence,
            "timestamp": "2026-03-06T14:00:00.000Z",
            "event_type": event_type,
            "url": url,
            "target": target,
            "payload": payload or {},
        }

    def test_injection_via_testid_text_and_fill(self):
        exploit = 'x"); require("child_process").execSync("touch /tmp/pwned"); ("'
        events = [
            self._event("navigation", "/", payload={"to_url": "/login"}, sequence=1),
            self._event("fill", "/login", target={
                "selectors": {"testid": exploit},
                "tag": "INPUT",
            }, payload={"value": exploit}, sequence=2),
            self._event("click", "/login", target={
                "selectors": {"role": {"role": "button", "name": exploit}},
                "tag": "BUTTON",
            }, sequence=3),
        ]
        flow = {
            "canonical_pattern": "/login",
            "representative_events": events,
            "popularity_score": 20.0,
            "session_count": 10,
        }
        script = PlaywrightGenerator().generate_script(flow, [])

        # The raw exploit substring (with its unescaped quotes) must never appear
        # verbatim — it can only survive in \"-escaped form inside a literal.
        assert exploit not in script
        assert 'execSync("touch /tmp/pwned")' not in script

    def test_injection_via_url(self):
        exploit_url = "/'); require('fs').unlinkSync('/etc/passwd'); ('"
        events = [
            self._event("navigation", "/", payload={"to_url": "/"}, sequence=1),
            self._event("click", "/", target={
                "selectors": {"role": {"role": "link", "name": "Go"}},
                "tag": "A", "attributes": {"href": exploit_url},
            }, sequence=2),
            self._event("navigation", "/", payload={"to_url": exploit_url}, sequence=3),
        ]
        flow = {
            "canonical_pattern": "/ → x",
            "representative_events": events,
            "popularity_score": 20.0,
            "session_count": 10,
        }
        script = PlaywrightGenerator().generate_script(flow, [])
        # The URL sinks are now double-quoted literals — no single-quoted
        # goto('...') / waitForURL('...') sink remains for the payload's single
        # quotes to break out of.
        assert "waitForURL('" not in script
        assert "page.goto('" not in script
        # And each emitted URL literal is a valid, fully-quoted string.
        for line in script.splitlines():
            line = line.strip()
            for sink in ("await page.goto(", "await page.waitForURL("):
                if line.startswith(sink) and line.endswith(");"):
                    literal = line[len(sink) : -len(");")]
                    json.loads(literal)  # raises if it broke out / is malformed

    def test_injection_via_click_context_assertion(self):
        exploit = 'v"); evil(); ("'
        events = [
            self._event("navigation", "http://localhost/i.html",
                        payload={"to_url": "http://localhost/i.html"}, sequence=1),
            self._event("click", "http://localhost/i.html", target={
                "tag": "BUTTON", "attributes": {}, "text_content": "Go", "role": "button",
            }, sequence=2),
        ]
        checkpoints = [
            {
                "checkpoint_id": "cp-nav", "session_id": "session-1",
                "trigger_event_id": "evt-1", "url": "http://localhost/i.html",
                "timestamp": "2026-03-10T10:00:00Z",
                "click_context": [
                    {"selectors": {"testid": "out"}, "tag": "SPAN", "text_content": "old"},
                ],
            },
            {
                "checkpoint_id": "cp-settle", "session_id": "session-1",
                "trigger_event_id": "evt-2", "url": "http://localhost/i.html",
                "timestamp": "2026-03-10T10:00:01Z",
                "click_context": [
                    {"selectors": {"testid": "out"}, "tag": "SPAN", "text_content": exploit},
                ],
            },
        ]
        flow = {
            "canonical_pattern": "http://localhost/i.html",
            "representative_events": events,
            "popularity_score": 10.0,
            "session_count": 5,
        }
        script = PlaywrightGenerator().generate_script(flow, checkpoints)
        # toContainText must carry the payload only as an escaped literal.
        assert exploit not in script
        assert "toContainText(" in script


class TestCommentHelpers:
    """Unit tests for the comment-context defang helpers (ITEM 2)."""

    def test_line_comment_strips_newlines(self):
        out = ts_line_comment("go to /x\nmaliciousStmt()")
        assert "\n" not in out
        assert "\r" not in out
        assert "maliciousStmt()" in out  # inert, but on the same line

    def test_line_comment_strips_js_line_separators(self):
        out = ts_line_comment("a" + _LS + "b" + _PS + "c")
        assert _LS not in out and _PS not in out

    def test_block_comment_defangs_terminator(self):
        out = ts_block_comment("x*/ maliciousStmt()")
        assert "*/" not in out
        assert "* /" in out

    def test_block_comment_strips_newlines(self):
        out = ts_block_comment("a\nb")
        assert "\n" not in out

    def test_none_is_empty(self):
        assert ts_line_comment(None) == ""
        assert ts_block_comment(None) == ""


class TestCommentSinkNoBreakout:
    """ITEM 2: flow_name / test_name and `//` description sinks must not break out.

    These trace back to captured URLs / DOM: ``flow_name``/``test_name`` were
    single-quoted (``test.describe('...')``) — a ``'`` broke out; ``description``
    was interpolated raw into a ``// ...`` comment — a newline broke out onto a
    code line.
    """

    @staticmethod
    def _event(event_type, url, target=None, payload=None, sequence=1):
        return {
            "event_id": f"evt-{sequence}", "session_id": "session-1",
            "tab_id": "tab-1", "sequence": sequence,
            "timestamp": "2026-03-06T14:00:00.000Z", "event_type": event_type,
            "url": url, "target": target, "payload": payload or {},
        }

    def test_flow_and_test_name_quote_breakout_contained(self):
        # The last path segment drives flow_name → test.describe(<name>) and
        # test(<name>). A single/double quote must not break out of the literal.
        exploit = "home\"); maliciousDescribe(); (\""
        flow = {
            "canonical_pattern": exploit,
            "representative_events": [
                self._event("navigation", "/", payload={"to_url": "/"}, sequence=1),
                self._event("click", "/", target={
                    "selectors": {"role": {"role": "button", "name": "Go"}},
                    "tag": "BUTTON",
                }, sequence=2),
            ],
            "popularity_score": 10.0, "session_count": 5,
        }
        script = PlaywrightGenerator().generate_script(flow, [])
        # The sinks are now double-quoted literals — no single-quoted describe/test.
        assert "test.describe('" not in script
        assert "test('" not in script
        # The injected call must never appear as an executable line — it may only
        # survive INERT inside the header block comment or an escaped literal.
        for line in script.splitlines():
            assert line.strip() != "maliciousDescribe();"
            assert not line.strip().startswith("maliciousDescribe(")
        # Each describe/test callee argument is a valid, fully-quoted JS literal.
        for line in script.splitlines():
            s = line.strip()
            if s.startswith("test.describe("):
                literal = s[len("test.describe("):].split(", () =>")[0]
                json.loads(literal)
            elif s.startswith("test(") and "async" in s:
                literal = s[len("test("):].split(", async")[0]
                json.loads(literal)

    def test_description_newline_stays_in_comment(self):
        # A navigation to_url with an embedded newline would, unescaped, splice
        # `MALICIOUSSTMT()` out of the `// ...` comment onto its own code line.
        malicious_url = "http://x/next\nMALICIOUSSTMT()"
        flow = {
            "canonical_pattern": "/ → /next",
            "representative_events": [
                self._event("navigation", "/", payload={"to_url": "/"}, sequence=1),
                self._event("click", "/", target={
                    "selectors": {"role": {"role": "button", "name": "Go"}},
                    "tag": "BUTTON",
                }, sequence=2),
                self._event("navigation", "/",
                            payload={"to_url": malicious_url}, sequence=3),
            ],
            "popularity_score": 10.0, "session_count": 5,
        }
        script = PlaywrightGenerator().generate_script(flow, [])
        # The description newline is collapsed: the payload stays on the single
        # `// Step ...` comment line (proving no break-out onto a code line).
        step_comment = next(
            (l for l in script.splitlines()
             if l.lstrip().startswith("//") and "MALICIOUSSTMT()" in l),
            None,
        )
        assert step_comment is not None, "description not rendered on a comment line"
        assert "Wait for navigation to" in step_comment  # same line — newline gone
        # The payload must never appear as a bare executable statement line. (It
        # may still appear escaped inside the waitForURL string literal — inert.)
        for line in script.splitlines():
            assert line.strip() != "MALICIOUSSTMT()"

    def test_config_base_url_quote_breakout_contained(self):
        # base_url flows into `baseURL: <literal>` in playwright.config.ts.
        exploit_domain = "x'); require('child_process'); ('"
        config = PlaywrightGenerator().generate_config(exploit_domain)
        # Old single-quoted sink is gone (payload can no longer close it).
        assert "baseURL: '" not in config
        # The require() call must never appear as an executable line — only inert
        # inside the header block comment or the escaped baseURL literal.
        for line in config.splitlines():
            assert not line.strip().startswith("require(")
        # The emitted baseURL is a valid, fully-quoted double-quoted literal.
        base_line = next(l.strip() for l in config.splitlines()
                         if l.strip().startswith("baseURL:"))
        literal = base_line[len("baseURL:"):].strip().rstrip(",")
        assert literal.startswith('"')
        json.loads(literal)


class TestVariablesNoBreakout:
    """Injection through the generated variables.ts file.

    variables.ts is imported by every generated spec and runs under
    `npx playwright test`, so its two attacker-influenced sinks — the fill
    value (string-literal context) and the description (block-comment context) —
    are the same RCE surface.
    """

    @staticmethod
    def _fill_event(target, value, sequence=1):
        return {
            "event_id": f"evt-{sequence}",
            "session_id": "session-1",
            "tab_id": "tab-1",
            "sequence": sequence,
            "timestamp": "2026-03-06T14:00:00.000Z",
            "event_type": "fill",
            "url": "/form",
            "target": target,
            "payload": {"value": value},
        }

    def test_value_and_description_no_breakout(self):
        value_exploit = '"; console.log(require("child_process").execSync("id")); x="'
        # testid flows into the description via `Form value for {locator}` and is
        # crafted to close the block comment and inject a statement.
        testid_exploit = "x*/;globalThis.PWNED=1;/*"
        flow = {
            "canonical_pattern": "/form",
            "representative_events": [
                self._fill_event(
                    {"selectors": {"testid": testid_exploit}, "tag": "INPUT"},
                    value_exploit,
                ),
            ],
            "popularity_score": 10.0,
            "session_count": 5,
        }
        out = generate_variables_file([flow])

        # 1. String-literal sink: the raw value (with its bare quotes) must never
        #    appear verbatim — only inside a properly escaped double-quoted
        #    literal.
        assert value_exploit not in out
        assert 'execSync("id")' not in out

        # 2. Block-comment sink: the injected `*/` must be defanged so it can't
        #    close the comment early. The payload may survive as INERT text
        #    inside the single-line comment, but must never appear as a bare
        #    statement on a line of its own.
        desc_line = next(
            l for l in out.splitlines()
            if l.strip().startswith("/**") and "Form value for" in l
        )
        assert desc_line.count("*/") == 1  # only the real terminator
        assert desc_line.rstrip().endswith("*/")
        for l in out.splitlines():
            if "globalThis.PWNED=1" in l:
                assert l.strip().startswith("/**"), (
                    "PWNED statement escaped the comment onto its own line"
                )

    def test_value_literal_round_trips(self):
        value_exploit = '"; evil(); x="'
        flow = {
            "canonical_pattern": "/form",
            "representative_events": [
                self._fill_event(
                    {"selectors": {"role": {"role": "textbox", "name": "Email"}}, "tag": "INPUT"},
                    value_exploit,
                ),
            ],
            "popularity_score": 10.0,
            "session_count": 5,
        }
        out = generate_variables_file([flow])
        # Find the emitted `email: <literal>,` line and confirm the literal
        # decodes back to the exploit (contained, not broken out).
        line = next(l for l in out.splitlines() if l.strip().startswith("email:"))
        literal = line.strip()[len("email: ") : -1]  # drop trailing comma
        assert json.loads(literal) == value_exploit

    def test_comment_breakout_defanged(self):
        # A locator engineered to escape the block comment must be neutralised.
        flow = {
            "canonical_pattern": "/form",
            "representative_events": [
                self._fill_event(
                    {"selectors": {"testid": "a*/\nglobalThis.PWNED=1;\n/*b"}, "tag": "INPUT"},
                    "safe",
                ),
            ],
            "popularity_score": 10.0,
            "session_count": 5,
        }
        out = generate_variables_file([flow])
        # No newline-smuggled statement and no early comment close inside the
        # description text.
        comment_lines = [l for l in out.splitlines() if l.strip().startswith("/**")]
        desc_line = next(l for l in comment_lines if "Form value for" in l)
        assert "*/" not in desc_line[: desc_line.rindex("*/")]  # only the real terminator
        assert "globalThis.PWNED=1" in desc_line  # present, but inert (single line, defanged)
        assert "\n" not in desc_line
