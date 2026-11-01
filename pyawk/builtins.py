"""Builtin functions and the printf/sprintf formatter.

Role in the pipeline: called by the runtime whenever the program uses a
builtin such as ``length``, ``substr``, ``gsub`` or ``printf``.  Entry
points: the ``BUILTINS`` table and ``format_printf``.

Pure helpers (``format_printf``, ``substr``, ``substitute`` ...) are kept
free of interpreter state so they can be unit tested; ``BUILTINS`` maps each
builtin name to a compiler ``fn(compiler, arg_nodes) -> closure`` that
compiler.py calls once per call site with the *unevaluated* argument nodes
(needed for array arguments, lvalue targets and regex literals).  For
example ``split(s, a)`` must receive the *array* ``a`` itself, not its
value, and ``sub(/x/, "y", v)`` must be able to assign back to ``v``.
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
# seen by type checkers.  compiler.py imports this module, so importing it
# back for real would create a circular import.  That is also why hints
# below write the class name in quotes: "Compiler".
if TYPE_CHECKING:  # pragma: no cover
    from .compiler import Compiler

# ----------------------------------------------------------------- printf
# One printf conversion: % flags width .precision letter, e.g. "%-5.2f".
# The four parenthesised groups capture those four parts.
_SPEC_RE = re.compile(r"%([-+ #0]*)(\*|\d+)?(?:\.(\*|\d*))?([a-zA-Z%])")


def _to_c_int(n: float) -> int:
    """Number -> integer for %d/%c/widths; inf and NaN become 0."""
    if n != n or n in (math.inf, -math.inf):
        return 0
    return int(n)


def _format_c_int(flags: str, width: str | None, prec: str | None, conv: str, n: int) -> str:
    """Format the integer n like C's printf conversions %d %i %o %u %x %X.

    Python's own ``%`` operator differs from C for integers: ``"%#o" % 8``
    gives "0o10" (C: "010"), ``"%#x" % 0`` gives "0x0" (C: "0"),
    ``"%.0d" % 0`` gives "0" (C: nothing) and the 0 flag is not ignored
    when a precision is given.  So integers are built by hand, in C's order:

    1. digits of the absolute value in the right base; the precision is the
       minimum number of digits (zero-padded), and precision 0 with value 0
       prints no digits at all;
    2. ``#``: octal must start with a 0 digit; hex gets 0x/0X unless n is 0;
    3. a sign (``-``, or ``+``/space when those flags are given) for %d/%i;
    4. padding to the width: spaces on the right with ``-``, zeros after the
       sign/prefix with ``0`` (ignored if a precision is given), otherwise
       spaces on the left.
    """
    signed = conv in "di"
    if not signed and n < 0:
        n += 1 << 64  # C reinterprets negatives as unsigned 64-bit values
    base_fmt = {"o": "o", "x": "x", "X": "X"}.get(conv, "d")
    digits = format(abs(n), base_fmt)  # format(255, "x") == "ff"
    if prec is not None:
        p = int(prec or "0")  # "%.d" means precision 0
        digits = "" if p == 0 and n == 0 else digits.rjust(p, "0")
    prefix = ""
    if signed:
        if n < 0:
            prefix = "-"
        elif "+" in flags:
            prefix = "+"
        elif " " in flags:
            prefix = " "
    if "#" in flags:
        if conv == "o" and not digits.startswith("0"):
            digits = "0" + digits
        elif conv in "xX" and n != 0:
            prefix = "0" + conv
    w = int(width or "0")
    body = prefix + digits
    if len(body) >= w:
        return body
    if "-" in flags:
        return body.ljust(w)
    if "0" in flags and prec is None:
        return prefix + digits.rjust(w - len(prefix), "0")
    return body.rjust(w)


# A parsed format is a list of pieces: literal text (a str) or a
# conversion (a _Spec tuple).  Parsing the same format string for every
# record is wasted work, so parsed formats are kept in a small cache.
# (flags, width, precision, conversion letter, the original text, and a
# ready-made Python format for the cases Python's ``%`` gets right.)
_Spec = tuple[str, str | None, str | None, str, str, str | None]
_FORMAT_CACHE: dict[str, list[str | _Spec]] = {}
_FORMAT_CACHE_MAX = 256


def _parse_format(fmt: str) -> list[str | _Spec]:
    pieces = _FORMAT_CACHE.get(fmt)
    if pieces is not None:
        return pieces
    pieces = []
    pos = 0
    for m in _SPEC_RE.finditer(fmt):
        if m.start() > pos:
            pieces.append(fmt[pos : m.start()])
        pos = m.end()
        flags, width, prec, conv = m.groups()  # unpack the 4 regex groups
        star = width == "*" or prec == "*"
        if conv == "%":
            pieces.append("%")  # "%%" (with any flags): a literal %
        elif conv not in "diouxXeEfFgGcs" and not star:
            # Unknown conversion: emitted verbatim (gawk does the same).
            # With a "*" it still uses up arguments, so it stays a spec.
            pieces.append(m.group(0))
        else:
            pyfmt = None
            if not star:
                spec = "%" + flags + (width or "") + ("." + prec if prec is not None else "")
                if conv in "eEfFgGs":
                    pyfmt = spec + conv
                elif conv in "diouxX" and prec is None and "#" not in flags:
                    # Without a precision or "#", Python formats integers
                    # exactly like C (see _format_c_int for the exceptions),
                    # except that C ignores "+" and " " for unsigned ones.
                    if conv in "di" or ("+" not in flags and " " not in flags):
                        pyfmt = spec + ("d" if conv in "diu" else conv)
            pieces.append((flags, width, prec, conv, m.group(0), pyfmt))
    if pos < len(fmt):
        pieces.append(fmt[pos:])
    if len(_FORMAT_CACHE) >= _FORMAT_CACHE_MAX:
        _FORMAT_CACHE.clear()
    _FORMAT_CACHE[fmt] = pieces
    return pieces


def format_printf(fmt: str, args: list[Value], convfmt: str = "%.6g") -> str:
    """Implement AWK printf/sprintf formatting (C semantics).

    The format is split into ``%`` conversions and the text between them
    (see _parse_format); text is copied.  Each conversion consumes the
    next argument, and is rendered with Python's own ``%`` operator, whose
    rules are those of C's printf (integers with a precision or ``#`` are
    the exception, see _format_c_int).  Missing arguments act as empty/zero
    rather than being an error.
    """
    out: list[str] = []
    argi = 0
    nargs = len(args)
    for piece in _parse_format(fmt):
        if type(piece) is str:
            out.append(piece)
            continue
        flags, width, prec, conv, raw, pyfmt = piece  # type: ignore[misc]
        # "*" takes the width (or precision) from the next argument.
        if width == "*":
            w = _to_c_int(to_num(args[argi] if argi < nargs else UNINIT))
            argi += 1
            if w < 0:
                flags += "-"
                w = -w
            width = str(w)
        if prec == "*":
            p = _to_c_int(to_num(args[argi] if argi < nargs else UNINIT))
            argi += 1
            prec = str(p) if p >= 0 else None
        v = args[argi] if argi < nargs else UNINIT  # the argument to format
        argi += 1
        if conv in "diouxX":
            n = _to_c_int(v if type(v) is float else to_num(v))
            if pyfmt is not None and (n >= 0 or conv in "di"):
                out.append(pyfmt % n)
            else:  # (this also handles negative numbers for %o %u %x %X)
                out.append(_format_c_int(flags, width, prec, conv, n))
        elif conv in "eEfFgG":
            x = v if type(v) is float else to_num(v)
            if math.isinf(x) or math.isnan(x):
                special = special_float_str(x)
                text = special if conv.islower() else special.upper()
                out.append(("%" + flags.replace("0", "") + (width or "") + "s") % text)
            elif pyfmt is not None:
                out.append(pyfmt % x)
            else:
                spec = "%" + flags + (width or "") + ("." + prec if prec is not None else "")
                out.append((spec + conv) % x)
        elif conv == "c":
            # %c of a number is the character with that code; of a string,
            # its first character.
            if type(v) is float:
                c = _to_c_int(v)
                ch = chr(c) if 0 <= c < 0x110000 else ""
            else:
                ch = to_str(v, convfmt)[:1]
            out.append(("%" + flags.replace("0", "") + (width or "") + "s") % ch)
        elif conv == "s":
            text = v if type(v) is str else to_str(v, convfmt)
            if pyfmt is not None:
                out.append(pyfmt % text)
            else:
                spec = "%" + flags + (width or "") + ("." + prec if prec is not None else "")
                out.append((spec + "s") % text)
        else:
            # Unknown conversion after a "*": emitted verbatim, as above.
            argi -= 1  # it uses no argument of its own
            out.append(raw)
    return "".join(out)


# ------------------------------------------------------------- string ops
def substr(s: str, m: float, n: float | None = None) -> str:
    """substr(s, m[, n]): up to n characters of s starting at position m.

    Behaviour of onetrue-awk and gawk (mawk differs only for a start
    below 1 combined with a length):

    * both m and n are truncated toward zero (2.7 -> 2, -0.5 -> 0);
    * a start below 1 is treated as 1 *without* shortening the length,
      so ``substr("hello", 0, 2)`` is "he" (POSIX would give "h");
    * a negative length gives "", a length past the end stops at the end.

    Infinite, NaN and huge values follow gawk (the other two overflow a C
    int there): a NaN start counts as 1, a NaN length gives "".

    AWK counts characters from 1, Python from 0, so the result is the
    Python slice ``s[start-1 : start-1+length]``.
    """
    size = len(s)
    # Clamp *before* converting to int: int() of inf or NaN would fail,
    # and clamping keeps huge values such as 1e30 harmless.
    if m != m:  # NaN is the only value not equal to itself
        start = 1
    else:
        start = _trunc_clamp(m, 1, size + 1)
    if n is None:
        length = size + 1 - start
    elif n != n:
        return ""
    else:
        length = _trunc_clamp(n, 0, size + 1 - start)
    return s[start - 1 : start - 1 + length]


def _trunc_clamp(x: float, low: int, high: int) -> int:
    """Truncate x toward zero, then force it into [low, high].

    Clamping happens first for infinities, since ``math.trunc(inf)``
    raises an error; ±inf simply become ``high`` or ``low``.
    """
    if x <= low:
        return low
    if x >= high:
        return high
    return math.trunc(x)


def index(s: str, t: str) -> int:
    # str.find returns a 0-based position or -1, so +1 gives AWK's answer
    # (1-based position, 0 when not found).
    return s.find(t) + 1


def expand_replacement(repl: str) -> list[str | None]:
    r"""Pre-parse a sub/gsub replacement: ``None`` entries stand for ``&``.

    In the replacement ``&`` means "the matched text".  Backslashes only
    matter in front of ``&``: in a run of backslashes followed by ``&``,
    each pair stands for one literal backslash, and an odd one left over
    makes the ``&`` literal.  A run *not* followed by ``&`` is copied
    unchanged.  So ``\&`` is a literal ``&``, ``\\&`` a backslash then
    the match, but ``\\x`` stays two backslashes.  This is mawk's rule;
    it is the majority answer in every case we tried (onetrue-awk differs
    for four or more backslashes before ``&``, gawk --posix halves a run
    not followed by ``&``).  E.g. ``"<&>"`` becomes ``["<", None, ">"]``.
    """
    parts: list[str | None] = []
    buf: list[str] = []
    i = 0
    n = len(repl)
    while i < n:
        c = repl[i]
        if c == "\\":
            j = i
            while j < n and repl[j] == "\\":
                j += 1
            run = j - i  # number of consecutive backslashes
            if j < n and repl[j] == "&":
                # ``divmod`` gives the quotient and remainder at once.
                pairs, odd = divmod(run, 2)
                buf.append("\\" * pairs)
                if odd:
                    buf.append("&")
                    j += 1  # the & has been consumed as a literal
            else:
                buf.append("\\" * run)
            i = j
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


# Caches for substitute(): the replacement as a Python re template, and
# whether a pattern can match the empty string.  Programs use few distinct
# patterns and replacements, so these stay small; they are emptied if a
# program generates many.
_TEMPLATE_CACHE: dict[str, str] = {}
_NONEMPTY_CACHE: dict[re.Pattern[str], bool] = {}
_SUB_CACHE_MAX = 256


def _template(repl: str) -> str:
    r"""The AWK replacement ``repl`` as a template for Python's ``re.sub``.

    In a template, ``\g<0>`` stands for the whole match and a backslash
    must be doubled to be literal.
    """
    t = _TEMPLATE_CACHE.get(repl)
    if t is None:
        if len(_TEMPLATE_CACHE) >= _SUB_CACHE_MAX:
            _TEMPLATE_CACHE.clear()
        t = _TEMPLATE_CACHE[repl] = "".join(
            r"\g<0>" if p is None else p.replace("\\", "\\\\") for p in expand_replacement(repl)
        )
    return t


def _never_matches_empty(pattern: re.Pattern[str]) -> bool:
    """True if ``pattern`` can only match one or more characters.

    Python's regex parser computes the shortest possible match length;
    the parser is an internal module, so if it is missing or changes we
    answer False and substitute() takes its general path.
    """
    answer = _NONEMPTY_CACHE.get(pattern)
    if answer is None:
        try:
            from re import _parser  # type: ignore[attr-defined]

            answer = _parser.parse(pattern.pattern, pattern.flags).getwidth()[0] > 0
        except Exception:  # any failure: be safe
            answer = False
        if len(_NONEMPTY_CACHE) >= _SUB_CACHE_MAX:
            _NONEMPTY_CACHE.clear()
        _NONEMPTY_CACHE[pattern] = answer
    return answer


def substitute(pattern: re.Pattern[str], repl: str, s: str, global_: bool) -> tuple[str, int]:
    """sub()/gsub() core with AWK empty-match rules; returns (result, count).

    Fast path: when the pattern cannot match the empty string, AWK's rules
    are exactly those of Python's ``re.subn`` (leftmost matches, each
    search starting where the previous match ended), which runs in C.

    General path: we walk through ``s`` from position ``i``, copying text
    up to each match and the rendered replacement in its place.  Patterns
    that can match the empty string (``x*``) need care: an empty match is
    replaced, then one character is copied so the scan always moves
    forward, and an empty match directly after a non-empty one is skipped.
    So ``gsub(/x*/, "-", "abxd")`` gives ``-a-b-d-``.  (Python's re.sub
    differs here: it gives ``-a-b--d-``.)
    """
    if _never_matches_empty(pattern):
        return pattern.subn(_template(repl), s, 0 if global_ else 1)
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


# ----------------------------------------------------------- compilers
# Each builtin has a small *compiler* here: given the Compiler and the
# argument nodes of one call, it returns a closure that performs that call
# (see compiler.py for what closures are and why we compile).  Arguments
# arrive as *nodes*, not values, because some builtins need more than a
# value: ``split(s, a)`` needs the array ``a`` itself, ``sub(/x/, "y", v)``
# must assign back to ``v``, and a /regex/ argument is compiled once.
#
# Type of a builtin compiler: (Compiler, list of nodes) -> closure.
Thunk = Callable[[], Value]
BuiltinFn = Callable[["Compiler", list[ast.Expr]], Thunk]


def _arity(name: str, args: list[ast.Expr], lo: int, hi: int) -> Thunk | None:
    """None if the call has an allowed number of arguments, else a closure
    that reports the mistake when the call runs (not before: AWK only
    complains about calls that actually happen)."""
    if lo <= len(args) <= hi:
        return None
    msg = f"function {name} called with {len(args)} args"

    def fail() -> Value:
        raise AwkRuntimeError(msg)

    return fail


def _length(c: "Compiler", args: list[ast.Expr]) -> Thunk:
    """length, length(s), or length(array) (number of elements: an extension)."""
    if bad := _arity("length", args, 0, 1):  # ``:=`` assigns inside the test
        return bad
    it = c.it
    if not args:
        return lambda: float(len(it.record))
    a = args[0]
    e = c.string(a)  # only the string is needed: cheaper for fields
    if isinstance(a, ast.Var):
        # A bare name may be an array (count its elements) or a variable
        # never used yet: length 0, and it stays untyped, so it may still
        # become an array later (onetrue-awk and mawk; gawk disagrees).
        kind, arr = c.var_kind(a.name), c.array(a.name)

        def length_name() -> Value:
            k = kind()
            if k == "array":
                return float(len(arr()))
            if k == "untyped":
                return 0.0
            return float(len(e()))

        return length_name
    return lambda: float(len(e()))


def _substr(c: "Compiler", args: list[ast.Expr]) -> Thunk:
    if bad := _arity("substr", args, 2, 3):
        return bad
    s, m = c.string(args[0]), c.expr(args[1])
    if len(args) == 2:
        return lambda: substr(s(), to_num(m()))
    n = c.expr(args[2])

    def substr3() -> Value:
        text = s()
        start = to_num(m())
        return substr(text, start, to_num(n()))

    return substr3


def _index(c: "Compiler", args: list[ast.Expr]) -> Thunk:
    if bad := _arity("index", args, 2, 2):
        return bad
    s, t = c.string(args[0]), c.string(args[1])

    def index_() -> Value:
        text = s()
        return float(index(text, t()))

    return index_


def _split(c: "Compiler", args: list[ast.Expr]) -> Thunk:
    """split(s, arr [, sep]): fill arr[1..n] and return n.

    The separator follows FS rules (" " = runs of blanks, one character =
    that character, longer = a regex); a /regex/ literal is used directly.
    """
    if bad := _arity("split", args, 2, 3):
        return bad
    it, sval, s = c.it, c.sval, c.expr(args[0])
    target = args[1]
    if not isinstance(target, ast.Var):

        def not_array() -> Value:
            s()  # the string is evaluated before the mistake is noticed
            raise AwkRuntimeError("split: second argument must be an array name")

        return not_array
    arr = c.array(target.name)
    pieces_of: Callable[[str], list[str]]
    if len(args) == 3 and isinstance(args[2], ast.Regex):
        source = args[2].source
        pieces_of = lambda text: it.split_regex(text, source)  # noqa: E731
    elif len(args) == 3:
        sep = c.expr(args[2])
        pieces_of = lambda text: it.split_with_fs(text, sval(sep()))  # noqa: E731
    else:
        fs = c.scalar_getter("FS")
        pieces_of = lambda text: it.split_with_fs(text, sval(fs()))  # noqa: E731

    def split() -> Value:
        pieces = pieces_of(sval(s()))
        a = arr()
        a.clear()
        # enumerate(pieces, 1) yields (1, first), (2, second), ...  Keys are
        # strings because AWK array subscripts always are; elements are strnums.
        for k, piece in enumerate(pieces, 1):
            a[str(k)] = make_strnum(piece)
        return float(len(pieces))

    return split


def _sub_common(c: "Compiler", args: list[ast.Expr], global_: bool) -> Thunk:
    if bad := _arity("gsub" if global_ else "sub", args, 2, 3):
        return bad
    pattern, repl, sval = c.regex(args[0]), c.expr(args[1]), c.sval
    # The default target is $0.
    target: ast.Expr = args[2] if len(args) == 3 else ast.Field(ast.Num(0.0))
    if isinstance(target, ast.LVALUE_TYPES):
        # Resolve the target once, so ``sub(/a/, "b", arr[i++])`` increments
        # i only once even though we both read and write the element.
        resolve, get_ref, set_ref = c.ref(target), c.get_ref, c.set_ref

        def sub() -> Value:
            pat = pattern()
            r = sval(repl())
            ref = resolve()
            result, count = substitute(pat, r, sval(get_ref(ref)), global_)
            if count:  # only assign when something changed ($0 is not rebuilt otherwise)
                set_ref(ref, result)
            return float(count)

        return sub
    # Non-lvalue target: compute and discard (gawk warns; mawk errors).
    e = c.expr(target)

    def sub_value() -> Value:
        pat = pattern()
        r = sval(repl())
        # "_" is the conventional name for a value we ignore.
        _, count = substitute(pat, r, sval(e()), global_)
        return float(count)

    return sub_value


def _sub(c: "Compiler", args: list[ast.Expr]) -> Thunk:
    return _sub_common(c, args, False)


def _gsub(c: "Compiler", args: list[ast.Expr]) -> Thunk:
    return _sub_common(c, args, True)


def _match(c: "Compiler", args: list[ast.Expr]) -> Thunk:
    """match(s, re): sets RSTART (1-based, 0 if none) and RLENGTH (-1 if none)."""
    if bad := _arity("match", args, 2, 2):
        return bad
    s, pattern = c.string(args[0]), c.regex(args[1])
    set_start, set_length = c.scalar_setter("RSTART"), c.scalar_setter("RLENGTH")

    def match() -> Value:
        text = s()
        m = pattern().search(text)
        if m is None:
            start, length = 0.0, -1.0
        else:
            start, length = float(m.start() + 1), float(m.end() - m.start())
        set_start(start)
        set_length(length)
        return start

    return match


def _sprintf(c: "Compiler", args: list[ast.Expr]) -> Thunk:
    if not args:
        return _fail("sprintf: no format")
    parts, sval, vars_ = [c.expr(a) for a in args], c.sval, c.vars

    def sprintf() -> Value:
        # A list comprehension: evaluate every argument, collecting the results.
        vals = [p() for p in parts]
        return format_printf(sval(vals[0]), vals[1:], to_str(vars_["CONVFMT"]))

    return sprintf


def _fail(msg: str) -> Thunk:
    def fail() -> Value:
        raise AwkRuntimeError(msg)

    return fail


def _math1(fn: Callable[[float], float], name: str) -> BuiltinFn:
    """Build the compiler for a one-argument math function such as sqrt.

    Python's math functions raise errors where C returns inf or NaN
    (e.g. log(0)); we return the C results, as other awks print them.
    The inner functions remember ``fn`` and ``name`` (closures).
    """

    def compile_math(c: "Compiler", args: list[ast.Expr]) -> Thunk:
        if bad := _arity(name, args, 1, 1):
            return bad
        e = c.expr(args[0])

        def impl() -> Value:
            x = to_num(e())
            try:
                return float(fn(x))
            except (ValueError, OverflowError):
                if name == "log":
                    return -math.inf if x == 0 else math.nan
                if name == "exp":
                    return math.inf
                return math.nan

        return impl

    return compile_math


def _int(c: "Compiler", args: list[ast.Expr]) -> Thunk:
    if bad := _arity("int", args, 1, 1):
        return bad
    e = c.expr(args[0])

    def int_() -> Value:
        x = to_num(e())
        if x != x or x in (math.inf, -math.inf):
            return x
        return float(math.trunc(x))

    return int_


def _atan2(c: "Compiler", args: list[ast.Expr]) -> Thunk:
    if bad := _arity("atan2", args, 2, 2):
        return bad
    y, x = c.expr(args[0]), c.expr(args[1])

    def atan2() -> Value:
        yv = to_num(y())
        return math.atan2(yv, to_num(x()))

    return atan2


def _rand(c: "Compiler", args: list[ast.Expr]) -> Thunk:
    if bad := _arity("rand", args, 0, 0):
        return bad
    it = c.it
    return lambda: it.rng.random()


def _srand(c: "Compiler", args: list[ast.Expr]) -> Thunk:
    if bad := _arity("srand", args, 0, 1):
        return bad
    it = c.it
    e = c.expr(args[0]) if args else None

    def srand() -> Value:
        prev = it.seed
        it.seed = to_num(e()) if e is not None else float(int(time.time()))
        it.rng.seed(it.seed)
        return prev

    return srand


def _tolower(c: "Compiler", args: list[ast.Expr]) -> Thunk:
    if bad := _arity("tolower", args, 1, 1):
        return bad
    e = c.string(args[0])
    return lambda: e().lower()


def _toupper(c: "Compiler", args: list[ast.Expr]) -> Thunk:
    if bad := _arity("toupper", args, 1, 1):
        return bad
    e = c.string(args[0])
    return lambda: e().upper()


def _system(c: "Compiler", args: list[ast.Expr]) -> Thunk:
    if bad := _arity("system", args, 1, 1):
        return bad
    e, sval, it = c.expr(args[0]), c.sval, c.it

    def system() -> Value:
        cmd = sval(e())
        it.io.flush_all()
        rc = subprocess.call(cmd, shell=True)
        # Python reports "killed by signal N" as -N; awks report 256 + N.
        return float(rc if rc >= 0 else 256 - rc)

    return system


def _close(c: "Compiler", args: list[ast.Expr]) -> Thunk:
    if bad := _arity("close", args, 1, 1):
        return bad
    e, sval, it = c.expr(args[0]), c.sval, c.it
    return lambda: float(it.io.close(sval(e())))


def _fflush(c: "Compiler", args: list[ast.Expr]) -> Thunk:
    if bad := _arity("fflush", args, 0, 1):
        return bad
    it = c.it

    def fflush() -> Value:
        it.io.flush_all()
        sys.stdout.flush()
        return 0.0

    return fflush


# Dispatch table: the compiler looks up a builtin by name here, instead of
# using a long if/elif chain over the names.
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
