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

Role in the pipeline: used by the runtime and builtins whenever a value is
converted or compared.  Main entry points: ``make_strnum`` (classify input
text), ``to_num`` / ``to_str`` / ``to_bool`` (conversions), ``num_to_str``
(number formatting) and ``compare`` (AWK's comparison rule).

Python note: ``type(v) is float`` checks the exact type of a value; it is used
below (instead of the more general ``isinstance``) because it is fast and the
three value types never subclass each other.
"""

# Treat type hints as text, so newer syntax such as ``X | None`` is allowed.
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
    """A string that also carries a numeric value (POSIX "numeric string").

    ``s`` is the original text (used when printing, so ``"1.0"`` prints as
    ``1.0``, not ``1``) and ``n`` its numeric value (used for comparisons).
    """

    # ``__slots__`` fixes the attribute list, making each object smaller and
    # faster; fields are created by the million, so it is worth it.
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


# A type alias: "a Value is a float, a str or a StrNum".  Used only in hints.
Value = Union[float, str, StrNum]

UNINIT: StrNum = StrNum("", 0.0)

# A complete numeric string (leading/trailing blanks allowed).
_NUMERIC_RE = re.compile(
    r"[ \t\n\r\f\v]*[-+]?(?:[0-9]+\.?[0-9]*|\.[0-9]+)(?:[eE][-+]?[0-9]+)?[ \t\n\r\f\v]*\Z"
)
# The longest numeric prefix used by string-to-number conversion.
_PREFIX_RE = re.compile(r"[ \t\n\r\f\v]*([-+]?(?:[0-9]+\.?[0-9]*|\.[0-9]+)(?:[eE][-+]?[0-9]+)?)")

# Integral values below this magnitude print as integers (mawk/one-true-awk
# behave the same way; gawk prints every integral value with %d).
_INT_PRINT_LIMIT = 2.0**64


def is_numeric_string(s: str) -> bool:
    """Return True if *s* looks like a number per POSIX strtod rules (subset)."""
    return _NUMERIC_RE.match(s) is not None


# Characters a numeric string can start with.  A *frozenset* is a set that
# cannot change; testing ``c in`` it is a single hash lookup.
_NUM_START = frozenset("0123456789+-. \t\n\r\f\v")


def make_strnum(s: str) -> Value:
    """Classify input text: a ``StrNum`` if it looks numeric, else ``str``.

    This runs for every field a program looks at, so it has two shortcuts
    before the full regular expression: text whose first character cannot
    start a number (most words) is a string at once, and plain ASCII digits
    (the most common numbers) are a number at once.
    """
    if not s or s[0] not in _NUM_START:
        return s
    if s.isascii() and s.isdigit():
        return StrNum(s, float(s))
    if _NUMERIC_RE.match(s):
        return StrNum(s, float(s.strip(" \t\n\r\f\v")))
    return s


def str_to_num(s: str) -> float:
    """Convert a string using its longest numeric prefix (0 if none)."""
    m = _PREFIX_RE.match(s)
    if m is None:
        return 0.0
    return float(m.group(1))


def to_num(v: Value) -> float:
    """AWK's string-to-number rule: numbers as-is, strings by numeric prefix."""
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
    # int() fails only for infinities (OverflowError) and NaN (ValueError);
    # ``try``/``except`` costs nothing when no error happens, so the common
    # case (an ordinary number) pays for a single int() call.
    try:
        i = int(n)
    except (OverflowError, ValueError):
        return special_float_str(n)
    # Whole numbers print without a decimal point (``3``, not ``3.0``).
    if i == n and -_INT_PRINT_LIMIT < n < _INT_PRINT_LIMIT:
        return str(i)
    # ``fmt % n`` is Python's printf-style formatting, e.g. "%.6g" % 0.1.
    # A malformed user-supplied CONVFMT falls back to the POSIX default.
    try:
        return fmt % n
    except (TypeError, ValueError):
        return "%.6g" % n


def to_str(v: Value, convfmt: str = "%.6g") -> str:
    """Number-to-string via CONVFMT; strnums keep their original text."""
    if type(v) is str:
        return v
    if type(v) is StrNum:
        return v.s
    return num_to_str(v, convfmt)  # type: ignore[arg-type]


def to_bool(v: Value) -> bool:
    """AWK truth: numbers are true if non-zero, strings if non-empty.

    A strnum counts as a number, so an input field ``0.0`` is false while the
    string constant ``"0"`` is true.
    """
    if type(v) is float:
        return v != 0.0
    if type(v) is StrNum:
        return v.n != 0.0
    return v != ""


def is_numeric(v: Value) -> bool:
    """True for values that compare numerically (numbers and strnums)."""
    return type(v) is not str


def compare(a: Value, b: Value, convfmt: str = "%.6g") -> int:
    """Three-way POSIX comparison: numeric if both numeric, else string.

    Returns -1, 0 or 1 (less, equal, greater).  This is the strnum rule: a
    field holding ``10`` is greater than one holding ``9.5`` (numbers), but the
    string constant ``"10"`` is less than ``"9"`` (character by character).
    """
    # Numbers and strnums both count as numeric; any plain string forces a
    # string comparison, converting numbers with CONVFMT.
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
