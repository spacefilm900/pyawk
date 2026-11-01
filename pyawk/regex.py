"""Translate POSIX EREs (as used by AWK) into Python ``re`` patterns.

Differences handled:

* ``$`` anchors only at the very end of the string (Python's ``$`` also
  matches before a trailing newline) -> ``(?!\\n)\\Z``-free ``\\Z``.
* ``.`` matches newline (``re.DOTALL``).
* Bracket expressions: POSIX classes such as ``[:alpha:]``, a leading ``]``
  as a literal, and escaping of characters Python treats specially inside
  sets (``[``, ``&``, ``~``, ``|``).
* AWK escapes: ``\\/`` and ``\\"`` are literals, ``\\b`` is backspace (not a
  word boundary), octal ``\\ddd``; unknown escapes of letters become the
  literal letter.
"""

from __future__ import annotations

import re
from functools import lru_cache

from .errors import AwkRuntimeError

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
    """Translate the escape starting at s[i] == '\\\\'; return (text, next_i)."""
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
        else:
            out.append(c)
            i += 1
    return "".join(out)


def _bracket(s: str, i: int) -> tuple[str, int]:
    """Translate a bracket expression starting at s[i] == '['."""
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


@lru_cache(maxsize=512)
def compile_ere(ere: str) -> re.Pattern[str]:
    try:
        return re.compile(translate(ere), re.DOTALL)
    except re.error as exc:
        raise AwkRuntimeError(f"bad regular expression /{ere}/: {exc}") from None
