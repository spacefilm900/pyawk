"""Translate POSIX EREs (as used by AWK) into Python ``re`` patterns.

Role in the pipeline: a helper for the runtime and builtins.  AWK regular
expressions (POSIX "extended regular expressions", EREs) look almost like
Python's, but differ in small ways, so instead of writing our own regex
engine we rewrite each ERE into Python syntax and let Python's ``re`` module
do the matching.  Entry point: ``compile_ere(text)``; ``translate`` is the
pure text-to-text step.

Differences handled:

* ``$`` anchors only at the very end of the string (Python's ``$`` also
  matches before a trailing newline), so it is translated to ``\\Z``.
* ``.`` matches newline (``re.DOTALL``).
* Bracket expressions: POSIX classes such as ``[:alpha:]``, a leading ``]``
  as a literal, and escaping of characters Python treats specially inside
  sets (``[``, ``&``, ``~``, ``|``).
* AWK escapes: ``\\/`` and ``\\"`` are literals, ``\\b`` is backspace (not a
  word boundary), octal ``\\ddd``; unknown escapes of letters become the
  literal letter.

* Stacked repeat operators (``a+?``, ``a++``, ``a?*``) keep their ERE
  meaning (see ``_repeat_run``) instead of Python's lazy or possessive one.

Known gap: Python picks the *first* alternative that matches, while POSIX
wants the *longest* match, so ``a|ab`` matches only "a" in "ab" here.
Also, an operator after an interval (``a{2}+``) still gets Python's
possessive or lazy meaning; the reference awks disagree among themselves
on that case.
"""

# Treat type hints as text, so newer syntax such as ``tuple[str, int]`` is allowed.
from __future__ import annotations

import re
from functools import lru_cache

from .errors import AwkRuntimeError

# POSIX named classes, as used inside brackets: [[:digit:]] -> [0-9].
_CLASSES = {
    "alpha": "a-zA-Z",
    "digit": "0-9",
    "alnum": "0-9a-zA-Z",
    "upper": "A-Z",
    "lower": "a-z",
    "space": " \\t\\n\\r\\f\\v",
    "blank": " \\t",
    "punct": re.escape("!\"#$%&'()*+,-./:;<=>?@[\\]^_`{|}~"),
    "xdigit": "0-9A-Fa-f",
    "cntrl": "\\x00-\\x1f\\x7f",
    "print": "\\x20-\\x7e",
    "graph": "\\x21-\\x7e",
    "word": "0-9a-zA-Z_",
}

_ESCAPES = {
    "n": "\\n", "t": "\\t", "r": "\\r", "f": "\\f", "v": "\\v", "a": "\\a",
    "b": "\\x08", "/": "/", '"': '"',
}


def _escape(s: str, i: int) -> tuple[str, int]:
    """Translate the escape starting at s[i] == '\\\\'; return (text, next_i).

    Returning a pair (a *tuple*) lets the caller write
    ``text, i = _escape(s, i)``, unpacking both results in one assignment.
    """
    if i + 1 >= len(s):
        return "\\\\", i + 1
    d = s[i + 1]
    if d in _ESCAPES:
        return _ESCAPES[d], i + 2
    if d in "01234567":
        j = i + 1
        while j < len(s) and j < i + 4 and s[j] in "01234567":
            j += 1
        return re.escape(chr(int(s[i + 1 : j], 8))), j
    if d.isalnum():
        # gawk-style extensions Python also understands; others -> literal.
        if d in "sSwWdD":
            return "\\" + d, i + 2
        return re.escape(d), i + 2
    return "\\" + d, i + 2


def translate(ere: str) -> str:
    """Rewrite an ERE as an equivalent Python regex string.

    Most characters mean the same in both syntaxes and are copied through;
    only escapes, ``$`` and bracket expressions need rewriting.
    """
    out: list[str] = []
    i = 0
    n = len(ere)
    while i < n:
        c = ere[i]
        if c == "\\":
            text, i = _escape(ere, i)
            out.append(text)
        elif c == "$":
            out.append(r"\Z")
            i += 1
        elif c == "[":
            text, i = _bracket(ere, i)
            out.append(text)
        elif c == "^" and i + 1 < n and ere[i + 1] in "+*?":
            # A repeat operator right after the ``^`` anchor repeats the
            # anchor itself (onetrue-awk and gawk; mawk rejects it, and
            # Python says "nothing to repeat").  ``^+`` is just ``^``;
            # ``^*`` and ``^?`` make the anchor optional, so both vanish.
            op, i = _repeat_run(ere, i + 1)
            if op == "+":
                out.append("^")
        elif c in "+*?":
            op, i = _repeat_run(ere, i)
            out.append(op)
        else:
            out.append(c)
            i += 1
    return "".join(out)


def _repeat_run(s: str, i: int) -> tuple[str, int]:
    """Collapse a run of repeat operators starting at s[i]; return (op, next_i).

    In an ERE each operator repeats everything before it, so ``a+?`` means
    ``(a+)?`` and ``a++`` means ``(a+)+``.  Python reads the same text
    differently: ``+?``, ``*?`` and ``??`` are "lazy" (match as little as
    possible) and, from Python 3.11, ``++``, ``*+`` and ``?+`` are
    "possessive" (never give characters back); ``?*`` is even an error.
    So the run is replaced by the single operator it is equivalent to:

    * only ``+`` (one or more, repeated) is still ``+``;
    * only ``?`` (optional, repeated) is still ``?``;
    * any mix, or any ``*``, allows zero or more, so it becomes ``*``.
    """
    j = i
    while j < len(s) and s[j] in "+*?":
        j += 1
    run = s[i:j]
    if j - i == 1:
        return run, j  # the common case: a single operator, unchanged
    if run == "+" * len(run):
        return "+", j
    if run == "?" * len(run):
        return "?", j
    return "*", j


def _bracket(s: str, i: int) -> tuple[str, int]:
    """Translate a bracket expression starting at s[i] == '['.

    In POSIX a ``]`` right after ``[`` or ``[^`` is a literal member, and
    ``[:alpha:]``-style class names may appear inside; Python knows neither,
    and treats some characters (``[ & ~ |``) specially inside sets.
    """
    n = len(s)
    j = i + 1
    parts = ["["]
    if j < n and s[j] == "^":
        parts.append("^")
        j += 1
    if j < n and s[j] == "]":
        parts.append("\\]")
        j += 1
    while j < n and s[j] != "]":
        c = s[j]
        if c == "[" and j + 1 < n and s[j + 1] in ":.=":
            kind = s[j + 1]
            end = s.find(kind + "]", j + 2)
            if end == -1:
                raise AwkRuntimeError(f"bad bracket expression in regex: {s!r}")
            name = s[j + 2 : end]
            if kind == ":":
                if name not in _CLASSES:
                    raise AwkRuntimeError(f"unknown character class [:{name}:]")
                parts.append(_CLASSES[name])
            else:
                # [.x.] and [=x=] (collating/equivalence) treated as literal x.
                parts.append(re.escape(name))
            j = end + 2
        elif c == "\\":
            text, j = _escape(s, j)
            parts.append(text)
        elif c in "[&~|":
            parts.append("\\" + c)
            j += 1
        else:
            parts.append(c)
            j += 1
    if j >= n:
        # Unterminated bracket: treat '[' literally.
        return "\\[", i + 1
    parts.append("]")
    return "".join(parts), j + 1


# ``@lru_cache`` remembers results: compiling the same regex text again (e.g.
# a pattern tested on every input line) returns the cached compiled pattern.
@lru_cache(maxsize=512)
def compile_ere(ere: str) -> re.Pattern[str]:
    """Compile an AWK ERE; a bad regex is a runtime error (it may be dynamic)."""
    try:
        return re.compile(translate(ere), re.DOTALL)
    except re.error as exc:
        raise AwkRuntimeError(f"bad regular expression /{ere}/: {exc}") from None
