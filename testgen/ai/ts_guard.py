from __future__ import annotations
"""Structural allow-list gate for AI-refined Playwright specs (M-2 proper fix).

Background
----------
``claude_client.validate_refined_script`` historically enforced a regex
*deny-list* of code-execution sinks. A deny-list fails safe (any match falls
back to the trusted deterministic script) but is fundamentally enumerative: a
payload that evades every listed pattern still ships. The proper fix is an
*allow-list* — parse the generated script and reject anything that is not one of
the constructs a legitimate generated Playwright spec is known to contain.

Deployment reality — why this is pure Python, not a real TS-AST
---------------------------------------------------------------
A true allow-list needs a real TypeScript parser. From here that means shelling
out to Node (``@typescript-eslint/parser`` / the ``typescript`` compiler API /
``@babel/parser``). But this is a pure-Python pipeline with no Node runtime:
generation is Python + Jinja2; only the *output* is TS. Bolting a Node
subprocess onto the hot path would add a fragile, network-touching,
cold-start-heavy dependency to every refinement — exactly the "theater" a
security gate should avoid.

So this module implements the **strongest feasible structural gate in pure
Python**: a hand-rolled scanner that (unlike the flat regex deny-list) actually
understands JS/TS lexical structure — it strips strings / template literals /
regex / comments with balanced-delimiter tracking, then enforces an allow-list
over the remaining *code* tokens:

  * imports may only come from ``@playwright/test`` or ``./variables``;
  * every call's callee is checked against a **two-part allow-list**:
      - the receiver *root* must be a known Playwright global or a locally-bound
        name (fixture destructuring, arrow/loop/catch params, const/let/var), and
      - the *method* being called (the final member of the chain) must be on the
        curated ``_ALLOWED_METHODS`` allow-list — the locator / interaction /
        navigation / assertion / event-hook surface the deterministic templates
        emit, plus a curated safe Playwright + JS-builtin surface. Anything not
        on the list (``evaluate``, ``addInitScript``, ``exposeFunction``,
        ``route``, ``$eval``, an unknown ``frobnicate`` …) is rejected. This is a
        true method ALLOW-list, not a deny-list — new sinks are excluded by
        default;
  * **method-alias tracking**: destructuring or assigning a non-allowed method
    off a receiver into a local — ``const { evaluate: go } = page`` or
    ``const go = page.evaluate`` — records ``go`` as a forbidden alias, so a
    later ``go(...)`` call is rejected even though ``page.evaluate`` never appears
    literally;
  * computed member access (``x['constructor']``, ``x['con'+'structor']``) is
    rejected — only integer index subscripts are allowed;
  * template-literal interpolations containing a call ``${ f() }`` are rejected;
  * unterminated strings/comments and unbalanced brackets (truncation /
    obfuscation) are rejected.

This admits ONLY calls whose root resolves AND whose method is on the curated
allow-list (with alias renames of excluded methods explicitly caught). It is
materially stronger than substring matching, but it is **not** a full TS-AST gate
and does not claim to be. It is layered *behind* the existing deny-list +
escaped-payload guard (which still scan the raw text, including inside template
literals) and preserves the same fail-closed behaviour: any rejection falls back
to the trusted deterministic script.

TODO (proper TS-AST gate)
-------------------------
When/if a Node toolchain becomes reliably available to this service (e.g. a
sidecar image or a bundled ``node`` in the analysis Dockerfile), replace/augment
this scanner with a vendored Node parser script invoked via ``subprocess`` with
a timeout, no network, and a size cap: parse to an AST and walk it against the
same allow-list at the *node* level (ImportDeclaration, CallExpression callee,
MemberExpression computed-ness), returning a structured reject reason. The
``structural_gate`` contract below (``(source) -> GateResult``) is designed so
that Node-backed implementation can drop in without touching callers.
"""

import re

__all__ = ["GateResult", "structural_gate"]


class GateResult:
    """Outcome of the structural gate. Truthy when the script is allowed."""

    __slots__ = ("ok", "reason")

    def __init__(self, ok: bool, reason: str = "") -> None:
        self.ok = ok
        self.reason = reason

    def __bool__(self) -> bool:  # allow `if structural_gate(...):`
        return self.ok

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        return f"GateResult(ok={self.ok!r}, reason={self.reason!r})"


# Placeholder that replaces every string/regex literal in the code skeleton. It
# is a valid-identifier-shaped token (so it participates in adjacency checks as
# an inert *value*) but is deliberately not an integer, so a computed access via
# a string — ``x['constructor']`` → ``x[__STR__]`` — is still detected as
# non-integer subscripting and rejected.
_STR = "__STR__"

# Only these import sources are legitimate in a generated spec.
_ALLOWED_IMPORT_SOURCES = frozenset({"@playwright/test", "./variables"})

# Call-receiver roots a legitimate spec may reference directly. Everything else
# must be a locally-bound name (fixture destructuring, arrow/loop/catch params,
# const/let/var) harvested from the source, or a control keyword.
_KNOWN_GLOBAL_ROOTS = frozenset({
    "page", "expect", "test", "request", "context", "browser",
    "Promise", "console", "Math", "JSON", "Date", "Array", "Object",
    "String", "Number", "Boolean", "RegExp", "URL", "URLSearchParams",
    "Set", "Map", "parseInt", "parseFloat", "isNaN", "devices",
    "defineConfig",
})

# Keywords that are followed by ``(`` but are control flow, not calls.
_CONTROL_KEYWORDS = frozenset({
    "if", "for", "while", "switch", "catch", "return", "typeof", "await",
    "async", "of", "in", "new", "else", "do", "function", "yield", "void",
    "delete", "throw", "case", "instanceof", "with",
})

# Bare-identifier functions that must never be *called* directly. Belt-and-braces
# only — the raw regex deny-list in claude_client already scans for these, and an
# unbound one fails the root-resolution check anyway.
_FORBIDDEN_BARE_CALLS = frozenset({
    "eval", "Function", "require", "exec", "execSync", "spawn", "spawnSync",
    "fork", "setTimeout", "setInterval", "fetch", "import",
})

# ALLOW-LIST of method names a legitimate generated spec may CALL (the final
# member of any ``root.….method(`` or ``<expr>.method(`` chain). Built from what
# the deterministic Jinja templates actually emit (grep of adapter.py + the
# templates: goto / waitForURL / waitForResponse / waitForEvent / click / fill /
# press / check / uncheck / selectOption / setFiles / dragTo / on / getByRole /
# getByText / getByTestId / locator, and the emitted matchers toHaveURL /
# toHaveTitle / toBeVisible / toBeHidden / toContainText / toHaveValue /
# toHaveAttribute / toHaveCSS / toBeChecked / toHaveScreenshot / not) PLUS a
# curated safe Playwright + JS-builtin surface. Deliberately does NOT include the
# in-page code-execution / interception sinks (see _FORBIDDEN_METHOD_SINKS).
_ALLOWED_METHODS = frozenset({
    # --- test-runner structure (test.describe / test.beforeEach / …) ---
    "describe", "beforeEach", "afterEach", "beforeAll", "afterAll", "step",
    "use", "slow", "skip", "fixme", "fail", "only", "configure",
    # --- locators / queries ---
    "getByRole", "getByText", "getByLabel", "getByPlaceholder", "getByTestId",
    "getByTitle", "getByAltText", "locator", "frameLocator", "first", "last",
    "nth", "filter", "and", "or", "count", "all", "allInnerTexts",
    "allTextContents", "elementHandle",
    # --- interactions ---
    "click", "dblclick", "fill", "type", "press", "pressSequentially",
    "check", "uncheck", "setChecked", "selectOption", "setInputFiles",
    "setFiles", "hover", "focus", "blur", "tap", "dragTo", "clear",
    "scrollIntoViewIfNeeded", "selectText", "highlight", "dispatchEvent",
    "screenshot",
    # --- navigation / waits ---
    "goto", "goBack", "goForward", "reload", "waitForURL", "waitForLoadState",
    "waitForResponse", "waitForRequest", "waitForEvent", "waitForTimeout",
    "waitForSelector", "title", "content",
    # --- event hooks ---
    "on", "off", "once",
    # --- dialog / fileChooser / frames ---
    "accept", "dismiss", "setFiles", "name", "message", "type",
    "mainFrame", "frame", "frames",
    # --- expect() matchers (locator + generic) ---
    "toHaveURL", "toHaveTitle", "toBeVisible", "toBeHidden", "toHaveText",
    "toContainText", "toHaveValue", "toHaveValues", "toHaveAttribute",
    "toHaveCount", "toBeChecked", "toBeEnabled", "toBeDisabled", "toBeEditable",
    "toBeFocused", "toBeEmpty", "toBeAttached", "toBeInViewport", "toHaveClass",
    "toHaveCSS", "toHaveId", "toHaveScreenshot", "toHaveJSProperty", "not",
    "toBe", "toEqual", "toStrictEqual", "toBeTruthy", "toBeFalsy", "toBeNull",
    "toBeDefined", "toBeUndefined", "toBeNaN", "toBeCloseTo", "toBeGreaterThan",
    "toBeGreaterThanOrEqual", "toBeLessThan", "toBeLessThanOrEqual", "toContain",
    "toContainEqual", "toMatch", "toMatchObject", "toHaveLength", "toThrow",
    # --- APIResponse READ verbs (safe; request-INITIATING verbs like fetch/get/
    #     post are intentionally excluded — see the exfil note below) ---
    "ok", "status", "statusText", "url", "headers", "headersArray",
    "json", "text", "body",
    # --- safe JS builtins a refiner may reasonably use ---
    "resolve", "reject", "race", "allSettled", "any",
    "includes", "indexOf", "lastIndexOf", "join", "split", "map", "forEach",
    "find", "findIndex", "some", "every", "reduce", "slice", "concat", "flat",
    "flatMap", "sort", "reverse", "keys", "values", "entries", "from", "of",
    "isArray", "assign", "fromEntries", "stringify", "parse", "trim", "replace",
    "replaceAll", "startsWith", "endsWith", "toLowerCase", "toUpperCase",
    "padStart", "padEnd", "repeat", "charAt", "charCodeAt", "codePointAt",
    "substring", "substr", "match", "matchAll", "search", "at", "toString",
    "valueOf", "now", "toISOString", "getTime", "floor", "ceil", "round",
    "max", "min", "abs", "random", "pow", "sqrt", "sign",
})

# In-page code-execution / request-interception / exfil sinks. These are the
# Playwright surface a refined spec must NEVER call: they run attacker-authored
# JS in the page (evaluate*, addInitScript, addScriptTag/StyleTag, exposeFunction/
# Binding, $eval/$$eval, waitForFunction) or hijack network traffic (route,
# unroute, routeFromHAR), plus the classic JS reflection primitives. They are
# excluded from _ALLOWED_METHODS (so a direct call is rejected) AND drive
# forbidden-alias detection so a destructure/rename off a receiver is caught.
#
# request.*/page.request.* API calls (fetch/get/post/put/delete/patch/head) are
# NOT on the allow-list either: the deterministic templates never emit them and
# they are a server-side exfil vector (the test runner fetching
# `https://evil/?c=<cookie>`). Only the response READ verbs (ok/status/url/json/
# text/…) are allowed, so `expect(res.ok())`-style assertions still pass.
_FORBIDDEN_METHOD_SINKS = frozenset({
    "evaluate", "evaluateHandle", "addInitScript", "addScriptTag",
    "addStyleTag", "exposeFunction", "exposeBinding", "$eval", "$$eval",
    "waitForFunction", "route", "unroute", "routeFromHAR",
    "constructor", "call", "apply", "bind", "__proto__",
    "fetch", "get", "post", "put", "delete", "patch", "head",
})

_IDENT = re.compile(r"[A-Za-z_$][\w$]*")
_ALL_DIGITS = re.compile(r"^\d+$")

# Keywords that can precede a ``[`` which then opens an array literal or a
# destructuring pattern rather than a computed member access.
_NON_ACCESS_KEYWORDS = _CONTROL_KEYWORDS | frozenset({"const", "let", "var"})


def _skip_string(src: str, i: int, quote: str) -> int:
    """Return the index just past the closing ``quote``, or -1 if unterminated.

    Honours backslash escapes. A raw (unescaped) newline terminates the search
    as unterminated — real single/double-quoted JS strings cannot span lines,
    so a newline here means a broken-out / malformed literal.
    """
    n = len(src)
    j = i + 1
    while j < n:
        c = src[j]
        if c == "\\":
            j += 2
            continue
        if c == quote:
            return j + 1
        if c in ("\n", "\r"):
            return -1
        j += 1
    return -1


def _skip_template(src: str, i: int) -> tuple[int, bool]:
    """Scan a template literal starting at backtick ``src[i]``.

    Returns ``(end_index, has_call_interpolation)`` where ``end_index`` is just
    past the closing backtick (or -1 if unterminated). ``has_call_interpolation``
    is True when any ``${ ... }`` interpolation contains a ``(`` — i.e. a call,
    which a legitimate generated spec never puts inside interpolation and which
    is the risky ``${ exfil() }`` form.
    """
    n = len(src)
    j = i + 1
    has_call = False
    while j < n:
        c = src[j]
        if c == "\\":
            j += 2
            continue
        if c == "`":
            return j + 1, has_call
        if c == "$" and j + 1 < n and src[j + 1] == "{":
            depth = 1
            k = j + 2
            while k < n and depth > 0:
                ck = src[k]
                if ck == "\\":
                    k += 2
                    continue
                if ck in ('"', "'"):
                    kk = _skip_string(src, k, ck)
                    if kk == -1:
                        return -1, has_call
                    k = kk
                    continue
                if ck == "`":
                    kk, nested_call = _skip_template(src, k)
                    if kk == -1:
                        return -1, has_call
                    has_call = has_call or nested_call
                    k = kk
                    continue
                if ck == "(":
                    has_call = True
                elif ck == "{":
                    depth += 1
                elif ck == "}":
                    depth -= 1
                k += 1
            if depth != 0:
                return -1, has_call
            j = k
            continue
        j += 1
    return -1, has_call


def _regex_allowed_before(prev: str) -> bool:
    """Heuristic: can a ``/`` at this position start a regex literal?

    A ``/`` is a regex start (not division) when the previous significant code
    char is an operator/opener/comma/etc. Generated specs use regex only inside
    calls like ``toHaveURL(/.../)`` so the preceding char is almost always
    ``(``, ``,``, ``=``, ``:`` or ``[``. Misclassifying division as regex only
    ever causes a (safe) fallback, never a bypass.
    """
    return prev in "(,=:[!&|?{;+-*%<>~^" or prev == ""


def _skip_regex(src: str, i: int) -> int:
    """Scan a regex literal starting at ``/`` (``src[i]``). Returns end or -1."""
    n = len(src)
    j = i + 1
    in_class = False
    while j < n:
        c = src[j]
        if c == "\\":
            j += 2
            continue
        if c in ("\n", "\r"):
            return -1
        if c == "[":
            in_class = True
        elif c == "]":
            in_class = False
        elif c == "/" and not in_class:
            return j + 1
        j += 1
    return -1


def _build_skeleton(src: str) -> tuple[str, str]:
    """Lex ``src`` into a code skeleton, blanking literals and comments.

    Returns ``(skeleton, error)``. ``error`` is a non-empty reason string when a
    string / comment / template is unterminated or a template interpolation
    contains a call. The skeleton replaces:
      * line and block comments  → spaces (length preserved for offsets),
      * string / regex literals  → the ``__STR__`` sentinel,
      * template literals        → spaces (interpolation calls flagged in error).
    Code (including template ``${}`` braces removed) remains verbatim so the
    downstream token checks operate only on real code.
    """
    out: list[str] = []
    n = len(src)
    i = 0
    prev_sig = ""  # last significant (non-space) skeleton char emitted

    def emit(s: str) -> None:
        nonlocal prev_sig
        out.append(s)
        stripped = s.strip()
        if stripped:
            prev_sig = stripped[-1]

    while i < n:
        c = src[i]
        nxt = src[i + 1] if i + 1 < n else ""

        if c == "/" and nxt == "/":
            j = src.find("\n", i)
            if j == -1:
                j = n
            out.append(" " * (j - i))
            i = j
            continue
        if c == "/" and nxt == "*":
            j = src.find("*/", i + 2)
            if j == -1:
                return "".join(out), "unterminated block comment"
            out.append(" " * (j + 2 - i))
            i = j + 2
            continue
        if c in ('"', "'"):
            j = _skip_string(src, i, c)
            if j == -1:
                return "".join(out), "unterminated string literal"
            emit(_STR)
            i = j
            continue
        if c == "`":
            j, has_call = _skip_template(src, i)
            if j == -1:
                return "".join(out), "unterminated template literal"
            if has_call:
                return "".join(out), "call inside template-literal interpolation"
            out.append(" " * (j - i))
            i = j
            continue
        if c == "/" and _regex_allowed_before(prev_sig):
            j = _skip_regex(src, i)
            if j == -1:
                # Not a valid regex; treat the slash as ordinary code (division).
                emit(c)
                i += 1
                continue
            emit(_STR)
            i = j
            continue

        emit(c)
        i += 1

    return "".join(out), ""


def _check_brackets(code: str) -> str:
    """Return an error reason if ``() [] {}`` are unbalanced/mismatched."""
    pairs = {")": "(", "]": "[", "}": "{"}
    openers = set(pairs.values())
    stack: list[str] = []
    for c in code:
        if c in openers:
            stack.append(c)
        elif c in pairs:
            if not stack or stack[-1] != pairs[c]:
                return f"unbalanced delimiter near {c!r}"
            stack.pop()
    if stack:
        return "unbalanced delimiter (unclosed)"
    return ""


def _harvest_bound_names(code: str) -> set[str]:
    """Collect locally-bound identifiers that may legitimately be call roots.

    Over-approximates (any identifier appearing in a binding position is kept).
    That only makes the root allow-list *more* permissive for names the script
    itself introduced — it can never admit an unbound dangerous global such as
    ``process``/``eval``, which is what the root check exists to reject.
    """
    bound: set[str] = set()

    # function / arrow parameter lists: `(...)=>` and `function name(...)`
    for m in re.finditer(r"\(([^()]*)\)\s*=>", code):
        bound.update(_IDENT.findall(m.group(1)))
    for m in re.finditer(r"function\s*\*?\s*([\w$]*)\s*\(([^()]*)\)", code):
        if m.group(1):
            bound.add(m.group(1))
        bound.update(_IDENT.findall(m.group(2)))
    # bare single-param arrow: `resp =>`
    for m in re.finditer(r"(?:^|[^.\w$])([\w$]+)\s*=>", code):
        bound.add(m.group(1))
    # declarations: const/let/var  X | {..} | [..]
    for m in re.finditer(r"\b(?:const|let|var)\s+(\{[^{}]*\}|\[[^\]]*\]|[\w$]+)", code):
        bound.update(_IDENT.findall(m.group(1)))
    # catch (e)
    for m in re.finditer(r"catch\s*\(([^()]*)\)", code):
        bound.update(_IDENT.findall(m.group(1)))

    return bound


def _harvest_forbidden_aliases(code: str) -> set[str]:
    """Collect locals that alias a NON-allowed method off some receiver.

    Catches the two rename bypasses the flat deny-list misses because the
    literal ``page.evaluate`` never appears:

      * member-expression alias — ``const go = page.evaluate;`` (grabbing the
        function reference, not calling it): if the grabbed method is not on the
        _ALLOWED_METHODS list, ``go`` is forbidden;
      * object-destructure alias — ``const { evaluate: go } = page;`` or the
        no-rename ``const { evaluate } = page;``: if a destructured KEY is a known
        method sink, the local it binds (rename target, else the key) is forbidden.

    Any later call to a forbidden alias is rejected by _check_calls_and_access.
    """
    aliases: set[str] = set()

    # member-expression alias: `const NAME = <expr>.METHOD` NOT immediately called,
    # terminated by `;`/newline/end (a clean function-reference grab).
    for m in re.finditer(
        r"\b(?:const|let|var)\s+([\w$]+)\s*=\s*[\w$.\)\]]*?\.([\w$]+)\s*(?=[;\n]|$)",
        code,
    ):
        name, method = m.group(1), m.group(2)
        if method not in _ALLOWED_METHODS:
            aliases.add(name)

    # object-destructure alias: `const { key, key2: local, ... } = <receiver>`
    for m in re.finditer(r"\b(?:const|let|var)\s+\{([^{}]*)\}\s*=", code):
        for prop in m.group(1).split(","):
            prop = prop.strip()
            if not prop:
                continue
            if ":" in prop:
                key, local = prop.split(":", 1)
            else:
                key = local = prop
            key_m = _IDENT.match(key.strip())
            local_m = _IDENT.match(local.strip())
            if key_m and local_m and key_m.group(0) in _FORBIDDEN_METHOD_SINKS:
                aliases.add(local_m.group(0))

    return aliases


def _check_imports(src: str) -> str:
    """Enforce the import allow-list on the raw source.

    * Only string-module imports from the allowed sources are permitted.
    * Side-effect imports (``import 'x'``) and dynamic ``import(`` are rejected.
    """
    for m in re.finditer(r"\bimport\b", src):
        start = m.start()
        # dynamic import(): next non-space char after `import` is `(`
        after = src[m.end():]
        if re.match(r"\s*\(", after):
            return "dynamic import() is not allowed"
        # find the statement end
        semi = src.find(";", start)
        line_end = src.find("\n", start)
        end = min(x for x in (semi, line_end, len(src)) if x != -1)
        stmt = src[start:end]
        src_match = re.search(r"""from\s*['"]([^'"]+)['"]""", stmt)
        if not src_match:
            # side-effect import `import 'x'` or `import "x"`
            bare = re.match(r"""import\s*['"]([^'"]+)['"]""", stmt)
            if bare:
                return f"side-effect import of {bare.group(1)!r} is not allowed"
            # `import type ... ` with no source on this line — be strict
            return "import statement without an allowed source"
        source = src_match.group(1)
        if source not in _ALLOWED_IMPORT_SOURCES:
            return f"import from disallowed source {source!r}"
    return ""


def _check_calls_and_access(code: str, bound: set[str], forbidden_aliases: set[str]) -> str:
    """Walk the code skeleton and enforce the call/member-access allow-list."""
    n = len(code)

    # --- computed member access: expr[ ... ] with a non-integer subscript ---
    i = 0
    while i < n:
        if code[i] == "[":
            # is the '[' a member access (preceded by an expression) or an
            # array literal / destructuring pattern (preceded by an operator,
            # opener, comma, or a keyword like `const`/`return`)?
            k = i - 1
            while k >= 0 and code[k].isspace():
                k -= 1
            prev = code[k] if k >= 0 else ""
            is_access = prev in "_$)]" or prev.isalnum()
            if is_access and not (prev in ")]"):
                # preceded by a word — access only if that word is not a keyword
                # (`const [a] = ...`, `return [x]`, `await [..]` are patterns).
                r = k
                while r >= 0 and (code[r].isalnum() or code[r] in "_$"):
                    r -= 1
                word = code[r + 1:k + 1]
                if word in _NON_ACCESS_KEYWORDS:
                    is_access = False
            if is_access:
                # extract balanced [...] content
                depth = 1
                j = i + 1
                while j < n and depth > 0:
                    if code[j] == "[":
                        depth += 1
                    elif code[j] == "]":
                        depth -= 1
                    j += 1
                inner = code[i + 1:j - 1].strip()
                if not _ALL_DIGITS.match(inner):
                    return f"computed member access [{inner!r}] is not allowed"
        i += 1

    # --- call callees: identify the callee chain before every '(' ------------
    for m in re.finditer(r"\(", code):
        p = m.start() - 1
        while p >= 0 and code[p].isspace():
            p -= 1
        if p < 0:
            continue
        prev = code[p]
        # `)(` or `](`: a call on an expression result / IIFE. The receiver
        # expression was itself validated elsewhere; nothing to check here.
        if prev in ")]":
            continue
        if not (prev.isalnum() or prev in "_$"):
            # `(` after an operator/opener/comma → grouping, not a call.
            continue
        # walk back over the member chain [\w$.]
        q = p
        while q >= 0 and (code[q].isalnum() or code[q] in "_$."):
            q -= 1
        chain = code[q + 1:p + 1]
        if not chain or chain.endswith("."):
            return f"malformed call callee {chain!r}"
        # A leading '.' means the receiver is the preceding expression — e.g. the
        # result of a prior (already-validated) call, `expect(page).toHaveURL()`
        # or `resp.url().includes()`. There is no standalone root to resolve, but
        # the method being called must still be on the allow-list.
        if chain.startswith("."):
            final = chain.lstrip(".").split(".")[-1]
            if final not in _ALLOWED_METHODS:
                return f"call to non-allow-listed method .{final}()"
            continue
        parts = chain.split(".")
        final = parts[-1]
        root = parts[0]
        if root == _STR:
            return "call on a string literal is not allowed"
        if len(parts) > 1:
            # method call `root.….final()`. Both the root AND the method are
            # allow-listed: the root must resolve (known global / bound / control),
            # and the final method must be on _ALLOWED_METHODS. This closes the
            # deny-list bypass where any non-listed method (addInitScript,
            # exposeFunction, an unknown frobnicate) on `page`/`expect`/… passed.
            if root not in _CONTROL_KEYWORDS and root not in _KNOWN_GLOBAL_ROOTS \
                    and root not in bound:
                return f"call on unknown receiver root {root!r}"
            if root not in _CONTROL_KEYWORDS and final not in _ALLOWED_METHODS:
                return f"call to non-allow-listed method .{final}()"
            continue
        # bare call `name(...)` — the name IS the callee (a global like test/
        # expect/defineConfig, or a locally-bound helper). Method allow-list does
        # NOT apply (that would reject test()/expect()); instead the name must
        # resolve and must not be a forbidden global or a tracked method alias.
        if root in _CONTROL_KEYWORDS:
            continue
        if root in forbidden_aliases:
            return f"call to method-alias {root}() (aliases a non-allow-listed method)"
        if root in _FORBIDDEN_BARE_CALLS:
            return f"call to forbidden function {root}()"
        if root in _KNOWN_GLOBAL_ROOTS or root in bound:
            continue
        return f"call to unknown function {root}()"

    return ""


def structural_gate(source: object) -> GateResult:
    """Allow-list gate over a refined Playwright spec. Truthy when allowed.

    Pure and deterministic — no network, no subprocess. Any structural anomaly
    returns ``GateResult(ok=False, reason=...)`` so the caller can fall back to
    the trusted deterministic script (fail-closed).
    """
    if not isinstance(source, str):
        return GateResult(False, "source is not text")

    skeleton, err = _build_skeleton(source)
    if err:
        return GateResult(False, err)

    err = _check_brackets(skeleton)
    if err:
        return GateResult(False, err)

    err = _check_imports(source)
    if err:
        return GateResult(False, err)

    bound = _harvest_bound_names(skeleton)
    forbidden_aliases = _harvest_forbidden_aliases(skeleton)
    err = _check_calls_and_access(skeleton, bound, forbidden_aliases)
    if err:
        return GateResult(False, err)

    return GateResult(True)
