"""AWK value model: strings, numbers and "strnums".

AWK values are dual-natured.  We represent them with three Python types:

* ``float``  -- a pure number (numeric constants, arithmetic results).
* ``str``    -- a pure string (string constants, concatenation results,
  input text that does *not* look numeric).
* ``StrNum`` -- a *numeric string*: text that came from input (fields,
  ``getline var``, ``split()`` elements, ``ARGV``, ``-v`` assignments)
  and looks like a number.  It keeps its original text for output but
  behaves as a number in comparisons and Boolean tests (POSIX semantics).

The uninitialized value ``UNINIT`` is a ``StrNum`` with text ``""`` and
value ``0``: it compares equal to both ``0`` and ``""``.
"""

from __future__ import annotations

import math
import re
from typing import Union

__all__ = [
    "StrNum",
    "UNINIT",
    "Value",
    "is_numeric_string",
    "make_strnum",
    "to_num",
    "to_str",
    "num_to_str",
    "to_bool",
    "is_numeric",
    "compare",
]


class StrNum:
    """A string that also carries a numeric value (POSIX "numeric string")."""

    __slots__ = ("s", "n")

    def __init__(self, s: str, n: float) -> None:
        self.s = s
        self.n = n

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"StrNum({self.s!r}, {self.n!r})"

    def __eq__(self, other: object) -> bool:
        return isinstance(other, StrNum) and other.s == self.s and other.n == self.n

    def __hash__(self) -> int:
        return hash((self.s, self.n))


Value = Union[float, str, StrNum]

UNINIT: StrNum = StrNum("", 0.0)

# A complete numeric string (leading/trailing blanks allowed).
_NUMERIC_RE = re.compile(
    r"[ \t\n\r\f\v]*[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?[ \t\n\r\f\v]*\Z"
)
# The longest numeric prefix used by string-to-number conversion.
_PREFIX_RE = re.compile(r"[ \t\n\r\f\v]*([-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?)")

# Integral values below this magnitude print as integers (mawk/one-true-awk
# behave the same way; gawk prints every integral value with %d).
_INT_PRINT_LIMIT = 2.0**64


def is_numeric_string(s: str) -> bool:
    """Return True if *s* looks like a number per POSIX strtod rules (subset)."""
    return _NUMERIC_RE.match(s) is not None


def make_strnum(s: str) -> Value:
    """Classify input text: a ``StrNum`` if it looks numeric, else ``str``."""
    if s and _NUMERIC_RE.match(s):
        return StrNum(s, float(s.strip(" \t\n\r\f\v")))
    return s


def str_to_num(s: str) -> float:
    """Convert a string using its longest numeric prefix (0 if none)."""
    m = _PREFIX_RE.match(s)
    if m is None:
        return 0.0
    return float(m.group(1))


def to_num(v: Value) -> float:
    if type(v) is float:
        return v
    if type(v) is StrNum:
        return v.n
    return str_to_num(v)  # type: ignore[arg-type]


def special_float_str(n: float) -> str:
    """Render inf/nan with an explicit sign, as gawk 4.2+ and mawk do."""
    sign = "-" if math.copysign(1.0, n) < 0 else "+"
    return sign + ("nan" if n != n else "inf")


def num_to_str(n: float, fmt: str) -> str:
    """Number to string: integers as ``%d``, otherwise via *fmt* (CONVFMT/OFMT)."""
    if n == n and n not in (math.inf, -math.inf):
        if n == int(n) and abs(n) < _INT_PRINT_LIMIT:
            return str(int(n))
    else:
        return special_float_str(n)
    try:
        return fmt % n
    except (TypeError, ValueError):
        return "%.6g" % n


def to_str(v: Value, convfmt: str = "%.6g") -> str:
    if type(v) is str:
        return v
    if type(v) is StrNum:
        return v.s
    return num_to_str(v, convfmt)  # type: ignore[arg-type]


def to_bool(v: Value) -> bool:
    if type(v) is float:
        return v != 0.0
    if type(v) is StrNum:
        return v.n != 0.0
    return v != ""


def is_numeric(v: Value) -> bool:
    """True for values that compare numerically (numbers and strnums)."""
    return type(v) is not str


def compare(a: Value, b: Value, convfmt: str = "%.6g") -> int:
    """Three-way POSIX comparison: numeric if both numeric, else string."""
    if type(a) is not str and type(b) is not str:
        x = to_num(a)
        y = to_num(b)
    else:
        x = to_str(a, convfmt)  # type: ignore[assignment]
        y = to_str(b, convfmt)  # type: ignore[assignment]
    if x < y:
        return -1
    if x > y:
        return 1
    if x == y:
        return 0
    return 1  # NaN involved: treat as unordered/"not equal"
