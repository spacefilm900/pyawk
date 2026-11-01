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



# (format, argument, expected) for C integer conversions; expected values
# are what onetrue-awk, gawk --posix and mawk all print.
C_INT_CASES = [
    ("%#o", 0, "0"), ("%#o", 7, "07"), ("%#.0o", 0, "0"), ("%#5o", 255, " 0377"),
    ("%#x", 0, "0"), ("%#x", 255, "0xff"), ("%#X", 7, "0X7"), ("%#05x", 7, "0x007"),
    ("%#.3x", 0, "000"), ("%#.0x", 0, ""), ("%.0x", 0, ""),
    ("%.0d", 0, ""), ("%5.0d", 0, "     "), ("%-5.0d|", 0, "     |"), ("%.d", 0, ""),
    ("%+.0d", 0, "+"), ("% .0d", 0, " "),
    ("%05.1d", 7, "    7"), ("%02.1d", 2, " 2"), ("%08.3x", 7, "     007"),
    ("%+5.3d", -7, " -007"), ("%-+6d|", 7, "+7    |"), ("% 05d", -7, "-0007"),
    ("%0-5d|", 7, "7    |"), ("%+u", 7, "7"), ("%.3d", -7, "-007"),
    ("%#o", -7, "01777777777777777777771"), ("%u", -7, "18446744073709551609"),
    ("% x", 255, "ff"), ("%+x", 255, "ff"), ("%5X", 255, "   FF"), ("%-4o|", 8, "10  |"),
    ("%05u", 42, "00042"),
]


@pytest.mark.parametrize("fmt,arg,expected", C_INT_CASES)
def test_printf_c_integer_flags(fmt: str, arg: int, expected: str) -> None:
    assert format_printf(fmt, [float(arg)]) == expected


# C length modifiers (h, hh, l, ll, L, q, j, z, t) mean nothing in awk,
# where every number is a double.  mawk, onetrue-awk and gawk (without
# --posix) all skip them, so "%ld" prints the number like "%d".
# (CodeRabbit review of v1.0, finding 1.)
LENGTH_MODIFIER_CASES = [
    ("%ld", 5, "5"), ("%hd", 6, "6"), ("%lld", 7, "7"), ("%hhu", 9, "9"),
    ("%lx", 255, "ff"), ("%5.2lf", 2.25, " 2.25"), ("%Lf", 1.5, "1.500000"),
    ("%zd", 10, "10"), ("%jd", 11, "11"), ("%-4li|", 3, "3   |"), ("%lc", 65, "A"),
    # A modifier with no conversion letter after it is still copied verbatim.
    ("%l|", 1, "%l|"),
]


@pytest.mark.parametrize("fmt,arg,expected", LENGTH_MODIFIER_CASES)
def test_printf_ignores_c_length_modifiers(fmt: str, arg: float, expected: str) -> None:
    assert format_printf(fmt, [float(arg)]) == expected
    # and the same through a whole program, which uses the fast path
    assert awk(f'BEGIN {{ printf "{fmt}", {arg} }}')[0] == expected

def test_substr_edges() -> None:
    assert substr("hello", 2, 3) == "ell"
    assert substr("hello", 0) == "hello"
    assert substr("hello", 10) == ""
    assert substr("hello", 5, 10) == "o"
    assert substr("hello", 2, -1) == ""


def test_substr_start_below_one() -> None:
    # onetrue-awk and gawk (also --posix) clamp a start below 1 to 1 and
    # keep the full length; mawk (the outlier) shortens it differently.
    assert substr("hello", 0, 2) == "he"
    assert substr("hello", -1, 3) == "hel"
    assert substr("hello", -3, 1) == "h"
    assert substr("hello", -1) == "hello"


def test_substr_truncates_fractions() -> None:
    # All three references truncate both arguments toward zero.
    assert substr("hello", 1.5) == "hello"
    assert substr("hello", 2.7) == "ello"
    assert substr("hello", 3.5) == "llo"
    assert substr("hello", 1, 2.5) == "he"
    assert substr("hello", 1, 1.6) == "h"
    assert substr("hello", 1.5, 1.5) == "h"
    assert substr("hello", 0.5, 2) == "he"
    assert substr("hello", -0.5, 2) == "he"
    assert substr("hello", 1, 0.9) == ""


def test_substr_huge_and_special_values() -> None:
    # onetrue-awk and mawk overflow a C int here; we follow gawk.
    inf, nan = float("inf"), float("nan")
    assert substr("hello", 1, 1e30) == "hello"
    assert substr("hello", -1e30, 1e30) == "hello"
    assert substr("hello", 1e30) == ""
    assert substr("hello", 1, inf) == "hello"
    assert substr("hello", inf) == ""
    assert substr("hello", 2, nan) == ""
    assert substr("hello", nan) == "hello"


def test_gsub_empty_matches() -> None:
    pat = compile_ere("x*")
    assert substitute(pat, "-", "abxd", True) == ("-a-b-d-", 4)
    assert substitute(compile_ere("^"), ">", "abc", True) == (">abc", 1)
    assert substitute(compile_ere("$"), "<", "abc", True) == ("abc<", 1)


def test_regex_dollar_does_not_match_before_newline() -> None:
    assert compile_ere("a$").search("a\n") is None
    assert compile_ere("a.b").search("a\nb") is not None


# Stacked repetition operators keep their ERE meaning: ``a+?`` is
# ``(a+)?``, not Python's "lazy" ``+?``, and ``a++`` is ``(a+)+``, not
# Python 3.11's "possessive" ``++``.  Expected values are what mawk and
# onetrue-awk print (gawk --posix agrees on the first, fourth and later
# ones; for ``+?``, ``*?`` and ``??`` it gives the lazy answer, so the
# majority rule picks mawk and onetrue-awk).  (CodeRabbit v1.0, finding 2.)
STACKED_REPEAT_CASES = [
    ('"aaa" ~ /a++a/', "1"),
    ('match("aaa", /a+?/) " " RLENGTH', "1 3"),
    ('match("aaa", /a*?/) " " RLENGTH', "1 3"),
    ('match("baaa", /ba?+/) " " RLENGTH', "1 4"),
    ('match("baaa", /ba??/) " " RLENGTH', "1 2"),
    ('match("baaa", /ba?*/) " " RLENGTH', "1 4"),
    ('match("baaa", /ba+*/) " " RLENGTH', "1 4"),
    ('match("xaab", /(a)+?b/) " " RLENGTH', "2 3"),
    ('match("aaa", "a+?") " " RLENGTH', "1 3"),  # dynamic regex too
    ('match("ab+?c", /b[+?]/) " " RLENGTH', "2 2"),  # inside brackets: literal
    ('match("a+?b", /a\\+\\?b/) " " RLENGTH', "1 4"),  # escaped: literal
]


@pytest.mark.parametrize("expr,expected", STACKED_REPEAT_CASES)
def test_stacked_repetition_operators(expr: str, expected: str) -> None:
    assert awk(f"BEGIN {{ print {expr} }}")[0] == expected + "\n"


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


def test_fields_past_nf_are_empty_strings() -> None:
    """A field beyond NF is the empty *string*, not the uninitialised value.

    So ``$5 == 0`` is a string comparison ("" vs "0") and false, exactly as
    for an existing empty field; an unset variable, by contrast, equals both
    0 and "".  onetrue-awk, mawk and gawk all agree (DESIGN.md).
    """
    prog = '{ x = $9; print ($5 == 0), ($5 == ""), length($5), (x == 0), ($(NF+1) < 1), ($3 + 0), (u == 0) }'
    assert awk(prog, "a b\n")[0] == "0 1 0 0 1 0 1\n"
    # On an empty line even $1 is past NF.
    assert awk("{ print ($1 == 0), ($0 == 0), NF }", "\n")[0] == "0 0 0\n"
    # Fields created to fill a gap ($5 = ... or NF = ...) are empty strings too.
    prog = '{ $5 = "x"; print ($3 == 0), length($3); NF = 8; print ($7 == 0); $0 = "p q"; NF = 4; print ($4 == 0) }'
    assert awk(prog, "a b\n")[0] == "0 0\n0\n0\n"


def test_undefined_escape_in_sub_replacement() -> None:
    """ "\\&" in a string literal is just "&", i.e. "the matched text"."""
    prog = r'''BEGIN {
        s = "foo"; sub(/o/, "\&", s); print s      # & = matched text
        s = "foo"; sub(/o/, "\\&", s); print s     # \\& = a literal &
        s = "foo"; sub(/o/, "\\\\&", s); print s   # \\\\& = backslash + matched
        s = "foo"; gsub(/o/, "<\&>", s); print s
        print "\q", length("\q"), length("\&")
    }'''
    assert awk(prog)[0] == "foo\nf&o\nf\\oo\nf<o><o>\nq 1 1\n"


def test_undefined_escape_in_dynamic_regex() -> None:
    """A dynamic regex is a string first, so "a\\.b" is "a.b" (any char)."""
    prog = r'''BEGIN {
        print ("axb" ~ "a\.b"), ("axb" ~ "a\\.b"), ("axb" ~ /a\.b/)
        print ("&" ~ /\&/), ("q" ~ /\q/), ("a" ~ "a\$"), ("a$" ~ "a\$")
    }'''
    assert awk(prog)[0] == "1 0 0\n1 1 1 0\n"


def test_undefined_escape_in_command_line_assignments(tmp_path: Path) -> None:
    """-v and operand assignments get the same escape processing."""
    f = tmp_path / "data"
    f.write_text("x\n")
    prog = '{ s = "foo"; sub(/o/, w, s); print v, w, length(w), s, ("axb" ~ d) }'
    out, _ = awk(prog, "", r"w=\&", r"d=a\.b", str(f), assignments=[("v", r"\q")])
    assert out == "q & 1 foo 1\n"


def test_undefined_escape_in_field_separator() -> None:
    """-F, -v FS=, FS= operands and FS="..." all drop the backslash of "\\.",
    so a multi-character FS "b\\.c" is the regex b.c (onetrue-awk, gawk)."""
    prog = "{ print NF }"
    assert awk(prog, "abxcd\n", assignments=[("FS", r"b\.c")])[0] == "2\n"
    assert awk('BEGIN { FS = "b\\.c" } { print NF }', "abxcd\n")[0] == "2\n"
    # A single-character FS is never a regex, so "\\." is just a literal dot.
    assert awk(prog, "a.b\n", assignments=[("FS", r"\.")])[0] == "2\n"


def test_string_assigned_to_record_stays_a_string() -> None:
    # $0 = "0" keeps the type of the value assigned: a string, so it is
    # true; the fields split from it are still numeric strings (strnums).
    # A $0 rebuilt from its fields is a string too.  (All three references;
    # gawk tests nfldstr and zeroe0.)
    prog = 'BEGIN { $0 = "0"; print !$0, !$1; $0 = 0; print !$0; x = "0"; $0 = x; print !$0 }'
    assert awk(prog) == ("0 1\n1\n0\n", 0)
    assert awk('BEGIN { $0 = "00E0"; print ($0 && 1); $1 = "0"; print !$0, !$1 }') == ("1\n0 0\n", 0)
    # Input records and $0 = $1 (a strnum) stay numeric strings.
    assert awk('{ print !$0; $0 = $1; print !$0 }', "0\n") == ("1\n1\n", 0)


def test_split_ignores_paragraph_mode_newlines() -> None:
    # With RS = "", a newline separates *record* fields whatever FS is, but
    # split() uses only FS (or its own separator).  All three references;
    # gawk test rstest1.
    prog = 'BEGIN { RS = ""; FS = ":"; s = "a:b\\nc:d"; print split(s, a), length(a[2]), split(s, b, "x") }'
    assert awk(prog) == ("3 3 1\n", 0)
    assert awk('BEGIN { RS = ""; FS = ":" } { print NF }', "a:b\nc:d\n") == ("4\n", 0)


@pytest.mark.parametrize(
    "pattern,repl,text,global_,expected",
    [
        # Patterns that never match "" take re.subn's fast path; these
        # check that it keeps AWK's replacement rules (& and backslashes).
        ("[aeiou]", "<&>", "banana", True, ("b<a>n<a>n<a>", 3)),
        ("a", "<&>", "banana", False, ("b<a>nana", 1)),
        ("n", r"\&", "banana", True, ("ba&a&a", 2)),
        ("n", r"\\&", "banana", True, (r"ba\na\na", 2)),
        ("n", r"\1\g<0>", "an", True, (r"a\1\g<0>", 1)),
        ("x", "y", "banana", True, ("banana", 0)),
        # Patterns that can match "" take the general path.
        ("x*", "-", "abxd", True, ("-a-b-d-", 4)),
    ],
)
def test_substitute_paths(
    pattern: str, repl: str, text: str, global_: bool, expected: tuple[str, int]
) -> None:
    assert substitute(compile_ere(pattern), repl, text, global_) == expected


@pytest.mark.parametrize(
    "text,expected",
    [
        # Plain ASCII blanks, tabs and newlines take str.split()'s fast path.
        ("  a\tb  c \n", "3:[a][b][c]"),
        ("", "0:"),
        ("   ", "0:"),
        # Characters Python counts as whitespace but AWK does not must stay
        # inside fields (these take the regular-expression path).
        ("a\rb c", "2:[a\rb][c]"),
        ("x\vy\fz", "1:[x\vy\fz]"),
        ("q\x1cr s", "2:[q\x1cr][s]"),
        ("\u00e9 \u00a0z", "2:[\u00e9][\u00a0z]"),
    ],
)
def test_default_fs_split(text: str, expected: str) -> None:
    prog = 'BEGIN { n = split(s, a); r = n ":"; for (i = 1; i <= n; i++) r = r "[" a[i] "]"; print r }'
    assert awk(prog, assignments=[("s", text)]) == (expected + "\n", 0)


@pytest.mark.parametrize(
    "prog,expected",
    [
        # Fields used only as strings skip the strnum check (Compiler.string);
        # these pin down that the text is still right in every case.
        ("{ print $1 $2, length($3), $9 \"|\" }", "0x1-1 4 |\n"),
        ("{ a[$1]++; a[$1 + 0]++ } END { print a[\"0x1\"], a[0] }", "1 1\n"),
        ('{ CONVFMT = "%.2g"; $2 = 3.14159; print $2 "|" length($2) }', "3.1|3\n"),
        ('{ CONVFMT = "%.2g"; $2 = 3.14159; print $2 "" }', "3.1\n"),
        ('{ CONVFMT = "%.2g"; $2 = 3.14159; x = $2 ""; print x, length(x) }', "3.1 3\n"),
        ("{ NF = 2; i = 3; print $1 $2 $i \"|\" length($i) }", "0x1-1|0\n"),
        ("{ i = 2; print toupper($i) index($0, $i), $i ~ /^-/ }", "-15 1\n"),
    ],
)
def test_field_string_paths(prog: str, expected: str) -> None:
    assert awk(prog, "0x1 -1 abcd\n") == (expected, 0)


@pytest.mark.parametrize(
    "prog,expected",
    [
        # Assignment statements have their own closures (_assign_stmt);
        # these check order of evaluation and conversions match mawk.
        ("BEGIN { x += x += 2; print x; y = 1; y += y += 2; print y }", "4\n6\n"),
        ("BEGIN { i = 5; a[i++] += i; for (k in a) print k, a[k] }", "5 6\n"),
        ('BEGIN { s = "3x"; s += 1; t = "abc"; t -= 1; u ^= 2; v %= 3; print s, t, u, v }', "4 -1 0 0\n"),
        ("function f(p) { p += 1; q += p; return p } BEGIN { print f(2), q }", "3 3\n"),
    ],
)
def test_assignment_statements(prog: str, expected: str) -> None:
    assert awk(prog) == (expected, 0)


def test_assignment_statement_to_array_name_fails() -> None:
    with pytest.raises(AwkRuntimeError):
        awk("BEGIN { a[1] = 1; a += 2 }")


def test_printf_whole_format_matches_piecewise(monkeypatch: pytest.MonkeyPatch) -> None:
    """The one-shot "%" path (builtins._format_plan) must give exactly what
    formatting conversion by conversion gives, for every kind of argument."""
    from pyawk import builtins
    from pyawk.values import StrNum

    formats = [
        "%-10s|%8.2f|%5d|%x\n", "%s %s", "%d%%%i", "%5.1e %G %g", "%o %X %u", "%+d % d %05d",
        "%-5x|%3s|%.2s", "a %z b %d", "%d %d %d", "%f", "%10.3F",
    ]
    values = [
        0.0, -0.0, 1.0, -1.0, 255.0, -255.0, 3.75, -2.5, 1e30, -1e30, float("inf"), float("-inf"),
        float("nan"), "abc", "", "12x", StrNum(" 42 ", 42.0), StrNum("1e3", 1000.0),
    ]
    cases = []
    for fmt in formats:
        n = fmt.count("%") - 2 * fmt.count("%%")
        for i in range(len(values)):
            args = [values[(i + j) % len(values)] for j in range(n)]
            cases.append((fmt, args))
            cases.append((fmt, args[:-1]))  # one argument missing
    fast = [format_printf(f, a) for f, a in cases]
    monkeypatch.setattr(builtins, "_format_plan", lambda fmt: None)
    slow = [format_printf(f, a) for f, a in cases]
    assert fast == slow


@pytest.mark.parametrize(
    "prog,expected",
    [
        # sub/gsub on $0 (the default target) read and write the record
        # directly; the record must still be re-split and NF updated.
        ("{ n = gsub(/ /, \"  x \"); print n, NF, $2 }", "2 5 x\n"),
        ("{ $2 = \"q\"; print sub(/q/, \"Q\"), $0, $2 }", "1 a Q c Q\n"),
        ("{ NF = 2; sub(/$/, \"!\"); print; print NF }", "a b!\n2\n"),
        ("{ print sub(/z/, \"y\"), $0 }", "0 a b c\n"),
        ("{ $0 = 12.5; sub(/5/, \"x\"); print $0, $1 + 1 }", "12.x 13\n"),
    ],
)
def test_sub_on_record(prog: str, expected: str) -> None:
    assert awk(prog, "a b c\n") == (expected, 0)
