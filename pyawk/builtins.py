"""Builtin functions and the printf/sprintf formatter.

Pure helpers (``format_printf``, ``substr``, ``substitute`` ...) are kept
free of interpreter state so they can be unit tested; ``BUILTINS`` maps each
builtin name to an adapter ``fn(interp, arg_nodes) -> Value`` that the
runtime calls with the *unevaluated* argument nodes (needed for array
arguments, lvalue targets and regex literals).
"""

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

if TYPE_CHECKING:  # pragma: no cover
    from .runtime import Interpreter

# ----------------------------------------------------------------- printf
_SPEC_RE = re.compile(r"%([-+ #0]*)(\*|\d+)?(?:\.(\*|\d*))?([a-zA-Z%])")


def _to_c_int(n: float) -> int:
    if n != n or n in (math.inf, -math.inf):
        return 0
    return int(n)


def format_printf(fmt: str, args: list[Value], convfmt: str = "%.6g") -> str:
    """Implement AWK printf/sprintf formatting (C semantics)."""
    out: list[str] = []
    pos = 0
    argi = 0

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
        flags, width, prec, conv = m.groups()
        if conv == "%":
            out.append("%")
            continue
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
    """POSIX substr with rounding of fractional positions (gawk semantics)."""
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
    return s.find(t) + 1


def expand_replacement(repl: str) -> list[str | None]:
    """Pre-parse a sub/gsub replacement: ``None`` entries stand for ``&``."""
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
    """sub()/gsub() core with AWK empty-match rules; returns (result, count)."""
    parts = expand_replacement(repl)

    def render(matched: str) -> str:
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
BuiltinFn = Callable[["Interpreter", list[ast.Expr]], Value]


def _argc(name: str, args: list[ast.Expr], lo: int, hi: int) -> None:
    if not lo <= len(args) <= hi:
        raise AwkRuntimeError(f"function {name} called with {len(args)} args")


def _length(it: "Interpreter", args: list[ast.Expr]) -> Value:
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
    for k, piece in enumerate(pieces, 1):
        arr[str(k)] = make_strnum(piece)
    return float(len(pieces))


def _sub_common(it: "Interpreter", args: list[ast.Expr], global_: bool) -> Value:
    name = "gsub" if global_ else "sub"
    _argc(name, args, 2, 3)
    pattern = it.regex_of(args[0])
    repl = it.str_of(it.eval(args[1]))
    target: ast.Expr = args[2] if len(args) == 3 else ast.Field(ast.Num(0.0))
    if isinstance(target, ast.LVALUE_TYPES):
        ref = it.resolve(target)
        original = it.str_of(it.get_ref(ref))
        result, count = substitute(pattern, repl, original, global_)
        if count:
            it.set_ref(ref, result)
        return float(count)
    # Non-lvalue target: compute and discard (gawk warns; mawk errors).
    _, count = substitute(pattern, repl, it.str_of(it.eval(target)), global_)
    return float(count)


def _sub(it: "Interpreter", args: list[ast.Expr]) -> Value:
    return _sub_common(it, args, False)


def _gsub(it: "Interpreter", args: list[ast.Expr]) -> Value:
    return _sub_common(it, args, True)


def _match(it: "Interpreter", args: list[ast.Expr]) -> Value:
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
    vals = [it.eval(a) for a in args]
    return format_printf(it.str_of(vals[0]), vals[1:], it.convfmt)


def _math1(fn: Callable[[float], float], name: str) -> BuiltinFn:
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
    return float(rc if rc >= 0 else 256 - rc)


def _close(it: "Interpreter", args: list[ast.Expr]) -> Value:
    _argc("close", args, 1, 1)
    return float(it.io.close(it.str_of(it.eval(args[0]))))


def _fflush(it: "Interpreter", args: list[ast.Expr]) -> Value:
    _argc("fflush", args, 0, 1)
    it.io.flush_all()
    sys.stdout.flush()
    return 0.0


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
