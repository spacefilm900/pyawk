"""Focused regression tests for bugs found by the imported gawk test suite.

Each test names the gawk case (awk_tests/gawk/NAME.awk) that exposed the
bug; the expected results are what the reference awks (onetrue-awk,
gawk --posix, mawk) agree on.
"""

import io

import pytest

from pyawk.cli import run
from pyawk.errors import AwkSyntaxError
from pyawk.parser import parse


def awk(prog: str, data: str = "") -> str:
    out = io.StringIO()
    run(prog, (), (), stdout=out, stdin=io.BytesIO(data.encode()))
    return out.getvalue()


# ----------------------------------------------------------------- parser
def test_pipe_getline_can_be_concatenated() -> None:
    # getline3: ``cmd | getline x y`` is ``(cmd | getline x) y``.
    prog = 'BEGIN { cmd = "echo 3"; y = 7; print (cmd | getline x y); print x }'
    assert awk(prog) == "17\n3\n"


def test_assignment_as_right_operand_of_logical_and_comparison() -> None:
    # fieldassign: ``a && $0 = $1`` is ``a && ($0 = $1)``.  The references
    # accept an assignment after && || ~ !~ and the comparisons, but not
    # after arithmetic, concatenation or unary operators.
    assert awk('gsub(/./, "@") && $0 = $1 { print }', "ab cd\n") == "@@@@@\n"
    assert awk("BEGIN { y = 1 < x = 3; print y, x }") == "1 3\n"
    assert awk("BEGIN { y = 0 || x = 3; print y, x }") == "1 3\n"
    assert awk("BEGIN { y = 1 ~ x = 1; print y, x }") == "1 1\n"
    assert awk("BEGIN { i = 1; $i = 5; print $1, i }") == "5 1\n"
    for bad in ("y = 1 + x = 3", "y = -x = 1", 'y = "a" x = 3', "y = 2 ^ x = 3"):
        with pytest.raises(AwkSyntaxError):
            parse("BEGIN { " + bad + " }")


def test_print_and_delete_in_for_header() -> None:
    # forsimp: the init and step of ``for (;;)`` are simple statements, so
    # print, printf and delete are allowed (onetrue-awk, gawk; not mawk).
    assert awk("BEGIN { for (print 9; 0;); }") == "9\n"
    assert awk('BEGIN { for (print "a"; i < 2; print "s" i) i++ }') == "a\ns1\ns2\n"
    assert awk("BEGIN { a[1]; for (delete a[1]; 0;); print length(a) }") == "0\n"


def test_printf_without_arguments_prints_nothing() -> None:
    # printf0: onetrue-awk and gawk accept a bare printf.
    assert awk('BEGIN { printf\n print "X" }') == "X\n"


def test_dollar_with_unary_operator_and_increment() -> None:
    # prec: ``$+i++`` is ``$(+(i++))``; the field itself is not incremented.
    assert awk("BEGIN { $1 = i = 1; $+i++; $- -i++; print; print i }") == "1\n3\n"


def test_increment_binds_to_innermost_field() -> None:
    # memleak4 and parse1: ``$$a++`` increments ``$a``, then takes the
    # field it names, and ``$$a++++`` is ``$($a++)++``.
    prog = 'BEGIN { $0 = "AAAA"; x = $$$B++ ""*""+$$B++N; print x }'
    assert awk(prog) == "12\n"
    assert awk("BEGIN { a = 3 } { print $$a++++; print }", "3 4 5 6 7 8 9\n") == "7\n3 4 6 6 8 8 9\n"
