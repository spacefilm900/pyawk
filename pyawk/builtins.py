"""Builtin functions and the printf/sprintf formatter.

Role in the pipeline: called by the runtime whenever the program uses a
builtin such as ``length``, ``substr``, ``gsub`` or ``printf``.  Entry
points: the ``BUILTINS`` table and ``format_printf``.

Pure helpers (``format_printf``, ``substr``, ``substitute`` ...) are kept
free of interpreter state so they can be unit tested; ``BUILTINS`` maps each
builtin name to an adapter ``fn(interp, arg_nodes) -> Value`` that the
runtime calls with the *unevaluated* argument nodes (needed for array
arguments, lvalue targets and regex literals).  For example ``split(s, a)``
must receive the *array* ``a`` itself, not its value, and ``sub(/x/, "y", v)``
must be able to assign back to ``v``.
"""

# Treat type hints as text, so newer syntax such as ``float | None`` is allowed.
from __future__ import annotations

import math
import re
import subprocess
import sys
import time
from typing import TYPE_CHECKING, Callable

from . import ast
from .errors import AwkRuntimeError
from .values import UNINIT, StrNum, Value, make_strnum, special_float_str, to_num, to_str

# ``TYPE_CHECKING`` is False when the program runs; the import below is only
# seen by type checkers.  runtime.py imports this module, so importing it back
# for real would create a circular import.  That is also why hints below
# write the class name in quotes: "Interpreter".
if TYPE_CHECKING:  # pragma: no cover
    from .runtime import Interpreter

# ----------------------------------------------------------------- printf
# One printf conversion: % flags width .precision letter, e.g. "%-5.2f".
# The four parenthesised groups capture those four parts.
_SPEC_RE = re.compile(r"%([-+ #0]*)(\*|\d+)?(?:\.(\*|\d*))?([a-zA-Z%])")


def _to_c_int(n: float) -> int:
    """Number -> integer for %d/%c/widths; inf and NaN become 0."""
    if n != n or n in (math.inf, -math.inf):
        return 0
    return int(n)


def format_printf(fmt: str, args: list[Value], convfmt: str = "%.6g") -> str:
    """Implement AWK printf/sprintf formatting (C semantics).

    The format is scanned for ``%`` conversions; text between them is copied.
    Each conversion consumes the next argument, and is rendered with Python's
    own ``%`` operator, whose rules are those of C's printf.  Missing
    arguments act as empty/zero rather than being an error.
    """
    out: list[str] = []
    pos = 0
    argi = 0

    # A function defined inside another can read the outer variables.
    # ``nonlocal argi`` is needed because it also *changes* one of them.
    def next_arg() -> Value:
        nonlocal argi
        if argi < len(args):
            v = args[argi]
            argi += 1
            return v
        argi += 1
        return UNINIT

    while True:
        m = _SPEC_RE.search(fmt, pos)
        if m is None:
            out.append(fmt[pos:])
            break
        out.append(fmt[pos : m.start()])
        pos = m.end()
        flags, width, prec, conv = m.groups()  # unpack the 4 regex groups
        if conv == "%":
            out.append("%")
            continue
        # "*" takes the width (or precision) from the next argument.
        if width == "*":
            w = _to_c_int(to_num(next_arg()))
            if w < 0:
                flags += "-"
                w = -w
            width = str(w)
        if prec == "*":
            p = _to_c_int(to_num(next_arg()))
            prec = str(p) if p >= 0 else None
        spec = "%" + flags + (width or "") + ("." + prec if prec is not None else "")
        if conv in "di":
            n = _to_c_int(to_num(next_arg()))
            out.append((spec + "d") % n)
        elif conv in "ouxX":
            n = _to_c_int(to_num(next_arg()))
            # C reinterprets negative numbers as unsigned 64-bit values here.
            if n < 0:
                n += 1 << 64
            out.append((spec + ("d" if conv == "u" else conv)) % n)
        elif conv in "eEfFgG":
            x = to_num(next_arg())
            if math.isinf(x) or math.isnan(x):
                special = special_float_str(x)
                text = special if conv.islower() else special.upper()
                out.append(("%" + flags.replace("0", "") + (width or "") + "s") % text)
            else:
                out.append((spec + conv) % x)
        elif conv == "c":
            # %c of a number is the character with that code; of a string,
            # its first character.
            v = next_arg()
            if type(v) is float:
                n = _to_c_int(v)
                ch = chr(n) if 0 <= n < 0x110000 else ""
            else:
                ch = to_str(v, convfmt)[:1]
            out.append(("%" + flags.replace("0", "") + (width or "") + "s") % ch)
        elif conv == "s":
            out.append((spec + "s") % to_str(next_arg(), convfmt))
        else:
            # Unknown conversion: emit verbatim (gawk does the same).
            out.append(m.group(0))
    return "".join(out)


# ------------------------------------------------------------- string ops
def substr(s: str, m: float, n: float | None = None) -> str:
    """POSIX substr with rounding of fractional positions (gawk semantics).

    AWK counts characters from 1, Python from 0.  We compute the 1-based
    half-open range [start, end), clip it to the string, and then slice
    with ``s[start-1 : end-1]`` (Python slices are also half-open).
    ``m != m`` tests for NaN.
    """
    if m != m:
        return ""
    start = _round(m)
    if n is None:
        end = math.inf
    else:
        if n != n:
            return ""
        end = start + _round(n) if n != math.inf else math.inf
    if start < 1:
        start = 1
    if end > len(s) + 1:
        end = len(s) + 1
    if end <= start:
        return ""
    return s[int(start) - 1 : int(end) - 1]


def _round(x: float) -> float:
    if x in (math.inf, -math.inf):
        return x
    return float(round(x))  # round-half-even, like C's rint()


def index(s: str, t: str) -> int:
    # str.find returns a 0-based position or -1, so +1 gives AWK's answer
    # (1-based position, 0 when not found).
    return s.find(t) + 1


def expand_replacement(repl: str) -> list[str | None]:
    """Pre-parse a sub/gsub replacement: ``None`` entries stand for ``&``.

    In the replacement ``&`` means "the matched text", ``\\&`` a literal
    ampersand and ``\\\\`` a literal backslash.  E.g. ``"<&>"`` becomes
    ``["<", None, ">"]``.
    """
    parts: list[str | None] = []
    buf: list[str] = []
    i = 0
    while i < len(repl):
        c = repl[i]
        if c == "\\" and i + 1 < len(repl) and repl[i + 1] in "&\\":
            buf.append(repl[i + 1])
            i += 2
        elif c == "&":
            if buf:
                parts.append("".join(buf))
                buf = []
            parts.append(None)
            i += 1
        else:
            buf.append(c)
            i += 1
    if buf:
        parts.append("".join(buf))
    return parts


def substitute(pattern: re.Pattern[str], repl: str, s: str, global_: bool) -> tuple[str, int]:
    """sub()/gsub() core with AWK empty-match rules; returns (result, count).

    We walk through ``s`` from position ``i``, copying text up to each match
    and the rendered replacement in its place.  Patterns that can match the
    empty string (``x*``) need care: an empty match is replaced, then one
    character is copied so the scan always moves forward, and an empty match
    directly after a non-empty one is skipped.  So
    ``gsub(/x*/, "-", "abxd")`` gives ``-a-b-d-``.
    """
    parts = expand_replacement(repl)

    def render(matched: str) -> str:
        # A *generator expression* (like a list comprehension, without
        # building the list): each part, with None replaced by the match.
        return "".join(matched if p is None else p for p in parts)

    out: list[str] = []
    i = 0
    count = 0
    last_end = -1
    n = len(s)
    while i <= n:
        m = pattern.search(s, i)
        if m is None:
            break
        st, en = m.span()
        if st == en and st == last_end:
            # Empty match right after the previous match: not allowed.
            if st >= n:
                break
            out.append(s[i : st + 1])
            i = st + 1
            continue
        out.append(s[i:st])
        out.append(render(m.group(0)))
        count += 1
        if st == en:
            if st < n:
                out.append(s[st])
            i = st + 1
        else:
            i = en
            last_end = en
        if not global_:
            break
    if i <= n:
        out.append(s[i:])
    return "".join(out), count


# ----------------------------------------------------------- adapters
# Each adapter below evaluates its own arguments (via ``it.eval``), checks
# the argument count, and calls a pure helper.  ``it`` is the Interpreter.
#
# Type of an adapter: a function taking (Interpreter, list of nodes) and
# returning a Value.
BuiltinFn = Callable[["Interpreter", list[ast.Expr]], Value]


def _argc(name: str, args: list[ast.Expr], lo: int, hi: int) -> None:
    if not lo <= len(args) <= hi:
        raise AwkRuntimeError(f"function {name} called with {len(args)} args")


def _length(it: "Interpreter", args: list[ast.Expr]) -> Value:
    """length, length(s), or length(array) (number of elements: an extension)."""
    _argc("length", args, 0, 1)
    if not args:
        return float(len(it.get_record_text()))
    a = args[0]
    if isinstance(a, ast.Var) and a.name in it.arrays:
        return float(len(it.arrays[a.name]))
    return float(len(it.str_of(it.eval(a))))


def _substr(it: "Interpreter", args: list[ast.Expr]) -> Value:
    _argc("substr", args, 2, 3)
    s = it.str_of(it.eval(args[0]))
    m = to_num(it.eval(args[1]))
    n = to_num(it.eval(args[2])) if len(args) == 3 else None
    return substr(s, m, n)


def _index(it: "Interpreter", args: list[ast.Expr]) -> Value:
    _argc("index", args, 2, 2)
    return float(index(it.str_of(it.eval(args[0])), it.str_of(it.eval(args[1]))))


def _split(it: "Interpreter", args: list[ast.Expr]) -> Value:
    """split(s, arr [, sep]): fill arr[1..n] and return n.

    The separator follows FS rules (" " = runs of blanks, one character =
    that character, longer = a regex); a /regex/ literal is used directly.
    """
    _argc("split", args, 2, 3)
    s = it.str_of(it.eval(args[0]))
    target = args[1]
    if not isinstance(target, ast.Var):
        raise AwkRuntimeError("split: second argument must be an array name")
    if len(args) == 3:
        sep = args[2]
        if isinstance(sep, ast.Regex):
            pieces = it.split_regex(s, sep.source)
        else:
            pieces = it.split_with_fs(s, it.str_of(it.eval(sep)))
    else:
        pieces = it.split_with_fs(s, it.str_of(it.get_var("FS")))
    arr = it.get_array(target.name)
    arr.clear()
    # enumerate(pieces, 1) yields (1, first), (2, second), ...  Keys are
    # strings because AWK array subscripts always are; elements are strnums.
    for k, piece in enumerate(pieces, 1):
        arr[str(k)] = make_strnum(piece)
    return float(len(pieces))


def _sub_common(it: "Interpreter", args: list[ast.Expr], global_: bool) -> Value:
    name = "gsub" if global_ else "sub"
    _argc(name, args, 2, 3)
    pattern = it.regex_of(args[0])
    repl = it.str_of(it.eval(args[1]))
    # The default target is $0.
    target: ast.Expr = args[2] if len(args) == 3 else ast.Field(ast.Num(0.0))
    if isinstance(target, ast.LVALUE_TYPES):
        # Resolve the target once, so ``sub(/a/, "b", arr[i++])`` increments
        # i only once even though we both read and write the element.
        ref = it.resolve(target)
        original = it.str_of(it.get_ref(ref))
        result, count = substitute(pattern, repl, original, global_)
        if count:  # only assign when something changed ($0 is not rebuilt otherwise)
            it.set_ref(ref, result)
        return float(count)
    # Non-lvalue target: compute and discard (gawk warns; mawk errors).
    # "_" is the conventional name for a value we ignore.
    _, count = substitute(pattern, repl, it.str_of(it.eval(target)), global_)
    return float(count)


def _sub(it: "Interpreter", args: list[ast.Expr]) -> Value:
    return _sub_common(it, args, False)


def _gsub(it: "Interpreter", args: list[ast.Expr]) -> Value:
    return _sub_common(it, args, True)


def _match(it: "Interpreter", args: list[ast.Expr]) -> Value:
    """match(s, re): sets RSTART (1-based, 0 if none) and RLENGTH (-1 if none)."""
    _argc("match", args, 2, 2)
    s = it.str_of(it.eval(args[0]))
    m = it.regex_of(args[1]).search(s)
    if m is None:
        start, length = 0.0, -1.0
    else:
        start, length = float(m.start() + 1), float(m.end() - m.start())
    it.set_var("RSTART", start)
    it.set_var("RLENGTH", length)
    return start


def _sprintf(it: "Interpreter", args: list[ast.Expr]) -> Value:
    if not args:
        raise AwkRuntimeError("sprintf: no format")
    # A list comprehension: evaluate every argument, collecting the results.
    vals = [it.eval(a) for a in args]
    return format_printf(it.str_of(vals[0]), vals[1:], it.convfmt)


def _math1(fn: Callable[[float], float], name: str) -> BuiltinFn:
    """Build an adapter for a one-argument math function such as sqrt.

    Python's math functions raise errors where C returns inf or NaN
    (e.g. log(0)); we return the C results, as other awks print them.
    The inner function ``impl`` remembers ``fn`` and ``name`` (a *closure*).
    """

    def impl(it: "Interpreter", args: list[ast.Expr]) -> Value:
        _argc(name, args, 1, 1)
        x = to_num(it.eval(args[0]))
        try:
            return float(fn(x))
        except (ValueError, OverflowError):
            if name == "log":
                return -math.inf if x == 0 else math.nan
            if name == "exp":
                return math.inf
            return math.nan

    return impl


def _int(it: "Interpreter", args: list[ast.Expr]) -> Value:
    _argc("int", args, 1, 1)
    x = to_num(it.eval(args[0]))
    if x != x or x in (math.inf, -math.inf):
        return x
    return float(math.trunc(x))


def _atan2(it: "Interpreter", args: list[ast.Expr]) -> Value:
    _argc("atan2", args, 2, 2)
    return math.atan2(to_num(it.eval(args[0])), to_num(it.eval(args[1])))


def _rand(it: "Interpreter", args: list[ast.Expr]) -> Value:
    _argc("rand", args, 0, 0)
    return it.rng.random()


def _srand(it: "Interpreter", args: list[ast.Expr]) -> Value:
    _argc("srand", args, 0, 1)
    prev = it.seed
    it.seed = to_num(it.eval(args[0])) if args else float(int(time.time()))
    it.rng.seed(it.seed)
    return prev


def _tolower(it: "Interpreter", args: list[ast.Expr]) -> Value:
    _argc("tolower", args, 1, 1)
    return it.str_of(it.eval(args[0])).lower()


def _toupper(it: "Interpreter", args: list[ast.Expr]) -> Value:
    _argc("toupper", args, 1, 1)
    return it.str_of(it.eval(args[0])).upper()


def _system(it: "Interpreter", args: list[ast.Expr]) -> Value:
    _argc("system", args, 1, 1)
    cmd = it.str_of(it.eval(args[0]))
    it.io.flush_all()
    rc = subprocess.call(cmd, shell=True)
    # Python reports "killed by signal N" as -N; awks report 256 + N.
    return float(rc if rc >= 0 else 256 - rc)


def _close(it: "Interpreter", args: list[ast.Expr]) -> Value:
    _argc("close", args, 1, 1)
    return float(it.io.close(it.str_of(it.eval(args[0]))))


def _fflush(it: "Interpreter", args: list[ast.Expr]) -> Value:
    _argc("fflush", args, 0, 1)
    it.io.flush_all()
    sys.stdout.flush()
    return 0.0


# Dispatch table: the runtime looks up a builtin by name here and calls it,
# instead of using a long if/elif chain over the names.
BUILTINS: dict[str, BuiltinFn] = {
    "length": _length,
    "substr": _substr,
    "index": _index,
    "split": _split,
    "sub": _sub,
    "gsub": _gsub,
    "match": _match,
    "sprintf": _sprintf,
    "sin": _math1(math.sin, "sin"),
    "cos": _math1(math.cos, "cos"),
    "exp": _math1(math.exp, "exp"),
    "log": _math1(math.log, "log"),
    "sqrt": _math1(math.sqrt, "sqrt"),
    "int": _int,
    "atan2": _atan2,
    "rand": _rand,
    "srand": _srand,
    "tolower": _tolower,
    "toupper": _toupper,
    "system": _system,
    "close": _close,
    "fflush": _fflush,
}

__all__ = ["BUILTINS", "format_printf", "substr", "index", "substitute", "StrNum"]
