"""In-process tests for behaviour not covered by stdout-only golden files."""

import io
import subprocess
import sys
from pathlib import Path

import pytest

from pyawk.builtins import format_printf, substitute, substr
from pyawk.cli import run
from pyawk.errors import AwkRuntimeError
from pyawk.regex import compile_ere

REPO = Path(__file__).resolve().parent.parent


def awk(prog: str, data: str = "", *operands: str, assignments=()) -> tuple[str, int]:
    out = io.StringIO()
    code = run(prog, operands, assignments, stdout=out, stdin=io.BytesIO(data.encode()))
    return out.getvalue(), code


def test_default_print_stdin() -> None:
    assert awk("{ print }", "a\nb\n") == ("a\nb\n", 0)


def test_last_record_without_newline() -> None:
    assert awk("{ print NR, $0 }", "a\nb") == ("1 a\n2 b\n", 0)


def test_exit_status() -> None:
    assert awk("BEGIN { exit 3 }") == ("", 3)
    assert awk("BEGIN { exit 1 } END { print \"end\" }") == ("end\n", 1)
    assert awk("{ exit 4 } END { exit }", "x\n") == ("", 4)


def test_v_assignment_and_operand_assignment(tmp_path: Path) -> None:
    f = tmp_path / "data"
    f.write_text("1\n2\n")
    out, _ = awk("{ print x, $0 }", "", "x=A", str(f), "x=B", str(f), assignments=[("x", "v")])
    assert out == "A 1\nA 2\nB 1\nB 2\n"
    assert awk("BEGIN { print x + 1 }", assignments=[("x", "41")])[0] == "42\n"
    assert awk("BEGIN { print x }", assignments=[("x", "a\\tb")])[0] == "a\tb\n"


def test_paragraph_mode() -> None:
    out, _ = awk('BEGIN { RS = "" } { print NR ": " $1 "-" $NF " (" NF ")" }', "\n\na b\nc\n\n\nd e\n\n")
    assert out == "1: a-c (3)\n2: d-e (2)\n"


def test_single_char_rs() -> None:
    assert awk('BEGIN { RS = ";" } { print $0 }', "a;b;c\n")[0] == "a\nb\nc\n\n"


def test_division_by_zero() -> None:
    with pytest.raises(AwkRuntimeError):
        awk("BEGIN { print 1 / 0 }")


def test_printf_formats() -> None:
    assert format_printf("%d %s %5.1f%%", [3.9, "x", 2.25]) == "3 x   2.2%"
    assert format_printf("%c%c", [65.0, "BCD"]) == "AB"
    assert format_printf("%x", [-1.0]) == "ffffffffffffffff"
    assert format_printf("%s %s", ["only"]) == "only "


def test_substr_edges() -> None:
    assert substr("hello", 2, 3) == "ell"
    assert substr("hello", 0) == "hello"
    assert substr("hello", 0, 2) == "h"  # POSIX/gawk (mawk returns "he")
    assert substr("hello", -1, 3) == "h"  # POSIX/gawk (mawk returns "hell")
    assert substr("hello", 1.5, 2) == "el"  # rounding, like gawk
    assert substr("hello", 10) == ""


def test_gsub_empty_matches() -> None:
    pat = compile_ere("x*")
    assert substitute(pat, "-", "abxd", True) == ("-a-b-d-", 4)
    assert substitute(compile_ere("^"), ">", "abc", True) == (">abc", 1)
    assert substitute(compile_ere("$"), "<", "abc", True) == ("abc<", 1)


def test_regex_dollar_does_not_match_before_newline() -> None:
    assert compile_ere("a$").search("a\n") is None
    assert compile_ere("a.b").search("a\nb") is not None


def test_cli_options(tmp_path: Path) -> None:
    prog = tmp_path / "p.awk"
    prog.write_text("{ print $2 }\n")
    data = tmp_path / "d"
    data.write_text("a:b:c\n")
    proc = subprocess.run(
        [sys.executable, "-m", "pyawk", "-F:", "-v", "x=1", "-f", str(prog), str(data)],
        capture_output=True, text=True, cwd=REPO,
    )
    assert proc.returncode == 0 and proc.stdout == "b\n"


def test_cli_syntax_error_exit_code() -> None:
    proc = subprocess.run(
        [sys.executable, "-m", "pyawk", "BEGIN {"], capture_output=True, text=True, cwd=REPO
    )
    assert proc.returncode == 2 and "syntax error" in proc.stderr


def test_print_to_file(tmp_path: Path) -> None:
    target = tmp_path / "out.txt"
    awk(f'BEGIN {{ print "a" > "{target}"; print "b" > "{target}"; close("{target}"); print "c" >> "{target}" }}')
    assert target.read_text() == "a\nb\nc\n"


def test_compound_assignment_evaluates_rhs_first() -> None:
    assert awk("BEGIN { x = 1; x += x += 2; print x }")[0] == "6\n"
    assert awk("BEGIN { i = 1; a[i++] += i; for (k in a) print k, a[k] }")[0] == "1 2\n"


def test_empty_separator_splits_characters() -> None:
    assert awk('BEGIN { n = split("abc", a, ""); print n, a[1], a[3] }')[0] == "3 a c\n"
    assert awk("{ print NF, $2 }", "xyz\n", assignments=[("FS", "")])[0] == "3 y\n"


def test_infinity_formatting() -> None:
    assert awk('BEGIN { print -log(0), log(0); printf "%f %5.1e\\n", -log(0), log(0) }')[0] == "+inf -inf\n+inf  -inf\n"
