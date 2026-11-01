"""Runtime behaviour of user-defined functions.

Every expected answer here was checked against the three reference awks
(onetrue-awk, gawk --posix, mawk); where they disagree, the comment says
which way pyawk goes and why.
"""

import io
import subprocess
import sys
from pathlib import Path

import pytest

from pyawk.cli import run
from pyawk.errors import AwkRuntimeError

REPO = Path(__file__).resolve().parent.parent


def awk(prog: str, data: str = "") -> str:
    out = io.StringIO()
    run(prog, (), (), stdout=out, stdin=io.BytesIO(data.encode()))
    return out.getvalue()


def test_call_and_return() -> None:
    assert awk("function add(a, b) { return a + b } BEGIN { print add(2, 3) }") == "5\n"
    # A call may come before the definition; calls concatenate like any value.
    assert awk('BEGIN { print f(1) f(2) "x" }\nfunction f(n) { return n * 10 }') == "1020x\n"
    assert awk("function f(x) { return f2(x) + 1 } function f2(y) { return y * 2 } BEGIN { print f(f(1)) }") == "7\n"


def test_return_without_value_is_uninitialized() -> None:
    prog = (
        "function f() { return } function g() { }\n"
        'BEGIN { x = f(); y = g(); print x + 0, "[" x "]", "[" y "]", length(y) }'
    )
    assert awk(prog) == "0 [] [] 0\n"


def test_return_forms() -> None:
    prog = (
        "function a() { return; } function b() { return (1) } function c() { return 7\n}\n"
        "BEGIN { print a() b() c(), f( ) }\nfunction f() { return /x/ }"
    )
    # ``return /x/`` returns the result of matching $0 (empty in BEGIN): 0.
    assert awk(prog) == "17 0\n"


def test_strnum_survives_return() -> None:
    # $1 is "10" read from input, a numeric string; it still compares as a
    # number after passing through a parameter and a return.
    assert awk("function id(v) { return v } { print (id($1) < 9) }", "10\n") == "0\n"


def test_scalars_are_passed_by_value() -> None:
    prog = 'function f(a) { a = "changed"; return a } BEGIN { x = "orig"; print f(x), x }'
    assert awk(prog) == "changed orig\n"


def test_array_elements_are_passed_by_value() -> None:
    prog = 'function f(v) { v = 9 } BEGIN { a[1] = 1; f(a[1]); f(a[2]); print a[1], (2 in a) }'
    # Passing a[2] creates the element (it is referenced), as in all three.
    assert awk(prog) == "1 1\n"


def test_arrays_are_passed_by_reference() -> None:
    prog = (
        "function fill(arr, n,   i) { for (i = 1; i <= n; i++) arr[i] = i * i; delete arr[1] }\n"
        "BEGIN { fill(sq, 3); print length(sq), sq[2], sq[3], (1 in sq) }"
    )
    assert awk(prog) == "2 4 9 0\n"


def test_delete_whole_array_parameter() -> None:
    prog = "function clear(a) { delete a } BEGIN { x[1]; x[2]; clear(x); print length(x) ; x[3] = 1; print length(x) }"
    assert awk(prog) == "0\n1\n"


def test_uninitialized_argument_becomes_array_in_caller() -> None:
    prog = "function f(a) { a[1] = 7 } BEGIN { f(x); print x[1] }"
    assert awk(prog) == "7\n"
    # ... through two levels of calls, and from a local of the caller.
    prog = (
        'function g(b) { b["k"] = 7 } function f(a) { g(a) } function h(   loc) { f(loc); return loc["k"] }\n'
        'BEGIN { f(y); print y["k"], h() }'
    )
    assert awk(prog) == "7 7\n"


def test_uninitialized_argument_used_as_scalar_leaves_caller_alone() -> None:
    assert awk('function f(a) { a = 5 } BEGIN { f(x); print "[" x "]" }') == "[]\n"


def test_delete_on_uninitialized_param_makes_caller_array() -> None:
    assert awk("function f(a) { delete a } BEGIN { f(x); x[1] = 2; print length(x) }") == "1\n"
    with pytest.raises(AwkRuntimeError):
        awk("function f(a) { delete a } BEGIN { f(x); x = 1 }")


def test_locals_start_uninitialized_each_call() -> None:
    prog = (
        "function f(n,   loc, arr) { loc = loc + 1; arr[n] = 1; return loc + length(arr) }\n"
        "BEGIN { print f(1), f(2), f(3) }"
    )
    assert awk(prog) == "2 2 2\n"
    prog = 'function f(   loc) { return (loc == 0) (loc == "") length(loc) } BEGIN { print f() }'
    assert awk(prog) == "110\n"


def test_parameters_shadow_globals() -> None:
    prog = 'function f(x) { x = 2; y = 3; return x } BEGIN { x = 1; print f(9), x, y }'
    assert awk(prog) == "2 1 3\n"


def test_recursive_local_arrays_are_fresh() -> None:
    prog = "function r(n,   a) { a[n] = 1; if (n > 1) r(n - 1); return length(a) } BEGIN { print r(5) }"
    assert awk(prog) == "1\n"


def test_builtins_on_locals_and_array_params() -> None:
    prog = """function f(arr, s,   n, w) {
    n = split("a b c", w); arr["n"] = n
    sub(/b/, "B", arr["x"]); s = "zzz"; gsub(/z/, "y", s)
    return n w[3] s length(arr) (("x") in arr)
}
BEGIN { a["x"] = "abc"; print f(a), a["x"], a["n"] }"""
    assert awk(prog) == "3cyyy21 aBc 3\n"


def test_getline_into_params(tmp_path: Path) -> None:
    data = tmp_path / "d"
    data.write_text("first\nsecond\n")
    prog = (
        f'function f(a, v) {{ getline v < "{data}"; getline a["k"] < "{data}"; return v }}\n'
        'BEGIN { v = "keep"; print f(arr), arr["k"], v }'
    )
    assert awk(prog) == "first second keep\n"


def test_fields_with_params() -> None:
    assert awk("function f(n) { return $n } { print f(2), f(NF) }", "a b c\n") == "b c\n"


def test_recursion() -> None:
    prog = """function fact(n) { return n <= 1 ? 1 : n * fact(n - 1) }
function fib(n) { return n < 2 ? n : fib(n - 1) + fib(n - 2) }
function ack(m, n) { if (m == 0) return n + 1; if (n == 0) return ack(m - 1, 1); return ack(m - 1, ack(m, n - 1)) }
BEGIN { print fact(10), fib(20), ack(2, 3), ack(3, 3) }"""
    assert awk(prog) == "3628800 6765 9 61\n"


def test_deep_recursion() -> None:
    # gawk and mawk manage this depth; onetrue-awk runs out of stack.
    prog = "function d(n) { return n == 0 ? 0 : 1 + d(n - 1) } BEGIN { print d(10000) }"
    assert awk(prog) == "10000\n"


def test_runaway_recursion_is_a_clean_error() -> None:
    with pytest.raises(AwkRuntimeError, match="too deep"):
        awk("function inf(n) { return inf(n + 1) } BEGIN { inf(0) }")


def test_exit_inside_function() -> None:
    prog = 'function stop(c) { exit c } BEGIN { stop(3); print "no" } END { print "end"; stop(4); print "no" }'
    out = io.StringIO()
    code = run(prog, (), (), stdout=out, stdin=io.BytesIO(b""))
    assert (out.getvalue(), code) == ("end\n", 4)


def test_break_inside_function_loop() -> None:
    prog = (
        "function f(   i) { for (i = 0; ; i++) if (i == 3) break; return i }\n"
        "BEGIN { while (1) { print f(); break } }"
    )
    assert awk(prog) == "3\n"


def test_function_in_pattern() -> None:
    assert awk("function big(x) { return x > 2 } big($1)", "1\n3\n5\n") == "3\n5\n"


def test_undefined_function_fails_only_when_called() -> None:
    assert awk('BEGIN { if (0) nosuch(1); print "ok" }') == "ok\n"
    with pytest.raises(AwkRuntimeError, match="undefined function nosuch"):
        awk("BEGIN { nosuch(1) }")


def test_extra_arguments_are_evaluated() -> None:
    assert awk("function g(a) { return a } BEGIN { print g(1, x++), x }") == "1 1\n"


def test_type_errors() -> None:
    with pytest.raises(AwkRuntimeError):  # scalar passed, used as array
        awk("function f(a) { a[1] = 1 } BEGIN { x = 5; f(x) }")
    with pytest.raises(AwkRuntimeError):  # array passed, used as scalar
        awk("function f(a) { return a + 1 } BEGIN { x[1]; f(x) }")
    with pytest.raises(AwkRuntimeError):  # array returned
        awk("function f(   a) { a[1]; return a } BEGIN { f() }")
    with pytest.raises(AwkRuntimeError):  # local array used as scalar
        awk("function f(   a) { a[1]; a = 2 } BEGIN { f() }")


def test_reading_a_variable_fixes_it_as_scalar() -> None:
    # All three references reject this: once x was read as a scalar, it
    # cannot become an array (directly, or through a function parameter).
    with pytest.raises(AwkRuntimeError):
        awk("BEGIN { print x; x[1] = 1 }")
    with pytest.raises(AwkRuntimeError):
        awk("function f(a) { a[1] = 1 } BEGIN { print x; f(x) }")


def test_too_many_arguments_warns_on_stderr() -> None:
    proc = subprocess.run(
        [sys.executable, "-m", "pyawk", "function g(a) { return a } BEGIN { print g(1, 2) }"],
        capture_output=True, text=True, cwd=REPO,
    )
    assert proc.stdout == "1\n"
    assert "warning" in proc.stderr and "g" in proc.stderr
    assert proc.returncode == 0
