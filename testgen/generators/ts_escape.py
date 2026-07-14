"""Hardened emission of TypeScript/JavaScript string literals.

Generated Playwright ``.spec.ts`` files are downloaded and executed by
customers (``npx playwright test``). Any attacker-controllable captured DOM
value (test IDs, text content, ARIA role names, attribute values, URLs, CSS
selectors) that is string-formatted into those files is a stored code-injection
vector: a value like ``x"); execSync("id"); ("`` could break out of its string
literal and execute in the customer's CI/dev machine.

Every untrusted value emitted into generated code MUST go through
:func:`ts_string_literal`, which returns a *fully quoted* literal (including the
surrounding double quotes) so call sites cannot accidentally re-quote it wrong.
"""
from __future__ import annotations

import json

# Defense-in-depth cap on emitted literals. Captured strings are normally short
# (selectors, IDs, short text); anything longer is truncated before emission so
# a pathological value can't bloat the generated file.
_MAX_LITERAL_LEN = 250

# json.dumps() already escapes ", \\, and the C0 control characters. It does NOT
# escape the following, which we neutralise explicitly:
#   <            breaks out of an HTML <script> context (</script>)
#   `            string delimiter if the literal is pasted into a template literal
#   U+2028/U+2029  JS line/paragraph separators (historically terminate literals)
_EXTRA_ESCAPES = {
    "<": "\\u003c",
    "`": "\\u0060",
    " ": "\\u2028",
    " ": "\\u2029",
}


def ts_string_literal(value: object, *, max_len: int | None = _MAX_LITERAL_LEN) -> str:
    """Return a safe, fully-quoted TS/JS double-quoted string literal for ``value``.

    The returned string INCLUDES the surrounding double quotes, e.g.::

        ts_string_literal('Submit')            -> '"Submit"'
        ts_string_literal('x"); evil(); ("')   -> '"x\\"); evil(); (\\""'

    The runtime value of the emitted literal is preserved for legitimate inputs
    (only representation changes), so generated locators keep working.

    Args:
        value: Any value; ``None`` becomes an empty string, non-strings are
            coerced with ``str()``.
        max_len: Hard truncation applied to the raw string before quoting.
            Pass ``None`` to disable.
    """
    if value is None:
        s = ""
    elif isinstance(value, str):
        s = value
    else:
        s = str(value)

    if max_len is not None and len(s) > max_len:
        s = s[:max_len]

    # json.dumps produces a valid double-quoted JS/TS string literal with all
    # control chars, double quotes, and backslashes escaped.
    literal = json.dumps(s, ensure_ascii=False)

    for raw, escaped in _EXTRA_ESCAPES.items():
        literal = literal.replace(raw, escaped)
    # Neutralise template-literal interpolation just in case the literal is ever
    # embedded in a backtick context.
    literal = literal.replace("${", "\\u0024{")

    return literal


# Line terminators that can end a `//` comment (or splice a payload onto its own
# line). CR/LF are the obvious ones; U+2028/U+2029 are JS line/paragraph
# separators that also terminate a single-line comment in a JS engine.
_LINE_TERMINATORS = ("\r", "\n", " ", " ")

# Defense-in-depth cap for comment text (captured descriptions are short).
_MAX_COMMENT_LEN = 250


def ts_line_comment(value: object, *, max_len: int | None = _MAX_COMMENT_LEN) -> str:
    """Neutralise a value for safe use inside a ``// ...`` single-line comment.

    Generated specs embed attacker-controllable captured data (URLs, locator
    text) into ``// {{ description }}`` line comments. A raw newline in that
    value would end the comment and splice the remainder onto its own line as
    executable code::

        // Wait for navigation to /x
        maliciousCall()          <-- smuggled out of the comment

    We strip every line terminator (collapsing to a space) so the value can
    never leave its comment line. Returns bare text (no ``//`` prefix).
    """
    if value is None:
        return ""
    s = value if isinstance(value, str) else str(value)
    for ch in _LINE_TERMINATORS:
        s = s.replace(ch, " ")
    if max_len is not None and len(s) > max_len:
        s = s[:max_len]
    return s


def ts_block_comment(value: object, *, max_len: int | None = _MAX_COMMENT_LEN) -> str:
    """Neutralise a value for safe use inside a ``/* ... */`` block comment.

    Block comments have no escape mechanism, so an embedded ``*/`` would close
    the comment early and drop the remainder into code. We defang ``*/`` and
    strip line terminators. Superset of ``variables.py::_safe_block_comment``;
    that helper delegates here so the two stay in sync.
    """
    if value is None:
        return ""
    s = value if isinstance(value, str) else str(value)
    s = s.replace("*/", "* /")
    for ch in _LINE_TERMINATORS:
        s = s.replace(ch, " ")
    if max_len is not None and len(s) > max_len:
        s = s[:max_len]
    return s
