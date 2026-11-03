"""Unit tests for pyawk.regex: POSIX ERE parsing and leftmost-longest spans.

The broad coverage comes from tests/test_regex_differential.py (pyawk
against the majority of mawk, onetrue-awk and gawk); these tests pin the
individual rules and the engine's internals with small readable cases.
"""

import io
import re

import pytest

from pyawk.cli import run
from pyawk.errors import AwkRuntimeError
from pyawk.regex import LongestPattern, _safe, compile_ere, compile_span, parse


def awk(prog: str, data: str = "") -> str:
    out = io.StringIO()
    run(prog, [], (), stdout=out, stdin=io.BytesIO(data.encode()))
    return out.getvalue()


def span(ere: str, text: str) -> tuple[int, int] | None:
    m = compile_span(ere).search(text)
    return None if m is None else m.span()


# ------------------------------------------------- leftmost-longest
@pytest.mark.parametrize(
    "ere, text, expected",
    [
        ("a|ab", "abc", (0, 2)),  # Python alone would stop at "a"
        ("x(a|ab)(c|bcd)", "xabcd", (0, 5)),
        ("(a|ab)(c|bcd)", "abcd", (0, 4)),
        ("(a*)(ab)*b", "aabb", (0, 4)),
        ("b+|a+b*", "xaabbb", (1, 6)),  # leftmost start wins over length
        ("a{1,2}|a{3}", "aaa", (0, 3)),
        ("(^a|b)+", "abab", (0, 2)),  # ^ only matches at the start
        ("(a|b$)+", "aab", (0, 3)),
        ("q", "abc", None),
    ],
)
def test_leftmost_longest_span(ere: str, text: str, expected: tuple[int, int] | None) -> None:
    assert span(ere, text) == expected


def test_match_sub_gsub_split_use_longest() -> None:
    prog = (
        '{ match($0, /a|ab/); print RSTART, RLENGTH; s = $0; sub(/a|ab/, "<&>", s); print s;'
        ' t = $0; gsub(/a|ab/, "<&>", t); print t; n = split($0, A, /a|ab/); print n, A[1] "|" A[2] "|" A[3] }'
    )
    assert awk(prog, "xabyabz\n") == "2 2\nx<ab>yabz\nx<ab>y<ab>z\n3 x|y|z\n"


def test_regex_fs_and_rs_use_longest() -> None:
    assert awk('BEGIN { FS = "a|ab" } { print NF, $2 }', "1ab2\n") == "2 2\n"
    assert awk('BEGIN { RS = "x|xy" } { print }', "1xy2xy3") == "1\n2\n3\n"


def test_gsub_empty_matches() -> None:
    assert awk('{ gsub(/b*/, "-"); print }', "abc\n") == "-a-c-\n"


# ------------------------------------------------- parsing rules
@pytest.mark.parametrize(
    "ere, yes, no",
    [
        ("[[:alpha:][:digit:]]", ["a", "Z", "5"], ["-", " "]),
        ("[[:punct:]]", ["!", "~"], ["a", " "]),
        ("[]a]", ["]", "a"], ["b"]),
        ("[^]a]", ["b"], ["]", "a"]),
        ("[a-]", ["a", "-"], ["b"]),
        ("[-a]", ["a", "-"], ["b"]),
        (r"[\]]", ["]"], ["\\"]),  # a backslash escapes inside brackets (awk rule)
        ("[[.a.]]", ["a"], ["A", "."]),
        ("[[=a=]b]", ["a", "b"], ["A", "="]),
        ("[[.-.]a]", ["-", "a"], ["b"]),
        ("a{2}", ["aa"], ["a"]),
        ("^a{2,3}$", ["aa", "aaa"], ["a", "aaaa"]),
        ("^a{2}+$", ["aa", "aaaa"], ["aaa"]),  # (a{2})+
        ("a{,2}", ["a{,2}"], ["aa"]),  # no lower bound: a literal brace
        ("x^", [], ["x^", "x"]),  # a mid-pattern ^ is an anchor that cannot match
        ("a$b", [], ["a$b", "ab"]),  # a mid-pattern $ only matches at the end
        ("a)", ["a)"], ["a"]),  # an unmatched ) is literal
        (r"\w", ["w"], ["a"]),  # GNU escapes are plain letters
        (r"\x41", ["A"], ["x41"]),
        ("()a", ["a"], ["b"]),
    ],
)
def test_ere_rules(ere: str, yes: list[str], no: list[str]) -> None:
    pat = compile_ere(ere)
    for s in yes:
        assert pat.search(s), (ere, s)
    for s in no:
        assert not pat.search(s), (ere, s)


@pytest.mark.parametrize(
    "ere",
    ["*a", "a|", "|a", "(|b)", "a||b", "(a", "[a", "[[:word:]]", "[[:nope:]]", "[b-a]", "a{3,2}", "a{2", "[[.ab.]]"],
)
def test_ere_errors(ere: str) -> None:
    with pytest.raises(AwkRuntimeError):
        compile_ere(ere)


# ------------------------------------------------- settled decisions
# Each case is a choice where POSIX leaves the answer undefined or
# unspecified (see DESIGN.md, "Regex decisions"); the comment gives what
# the references do.  match() positions as (start, end) or None.
@pytest.mark.parametrize(
    "ere, text, expected",
    [
        # [a-c-e]: undefined.  mawk reads a-c, "-", e; onetrue-awk reads
        # a-e; gawk rejects it.  A complete split: we keep mawk's reading.
        ("[a-c-e]", "d", None),
        ("[a-c-e]", "-", (0, 1)),
        ("[a-c-e]", "e", (0, 1)),
        ("[a-c-e]", "b", (0, 1)),
        # POSIX's own examples: "-" as a range end (all agree).
        ("[%--]", "+", (0, 1)),
        ("[%--]", ".", None),
        ("[--@]", "5", (0, 1)),
        ("[--@]", ",", None),
        # Adjacent duplication symbols: undefined.  mawk and gawk read
        # a{1}{2} as (a{1}){2} and a{2}* as (a{2})* (onetrue-awk fails on
        # the first and reads the second as a{2,}).
        ("a{1}{2}", "aac", (0, 2)),
        ("a{1}{2}", "a", None),
        ("a{2}*", "aaa", (0, 2)),
        ("a{2}*", "a", (0, 0)),
        # a*+, a**, a?* all mean a* (all three agree).
        ("a*+", "aaa", (0, 3)),
        ("a**", "aaa", (0, 3)),
        ("a?*", "aaa", (0, 3)),
        # ^ after other items is still an anchor (POSIX; mawk and gawk;
        # onetrue-awk rejects b*^a).
        ("b*^a", "aac", (0, 1)),
        ("b*^a", "xaa", None),
        ("(a|^b)c", "bc", (0, 2)),
        ("(a|^b)c", "xbc", None),
    ],
)
def test_regex_decisions(ere: str, text: str, expected: tuple[int, int] | None) -> None:
    assert span(ere, text) == expected


@pytest.mark.parametrize(
    "ere",
    [
        # Empty alternatives: undefined.  onetrue-awk rejects them all,
        # gawk accepts them all, mawk rejects most (and gets a(|b)c and
        # x(a|)b wrong).  One consistent rule: always an error.
        "(a|)b",
        "a(|b)c",
        "x(a|)b",
        "a|b|",
        # A range whose end comes before its start: POSIX allows "matches
        # nothing" or "invalid".  gawk says invalid; mawk matches some
        # characters and onetrue-awk matches the empty string, neither of
        # which POSIX allows.  We say invalid.
        "[z-a]",
        # [a--@]: POSIX allows "invalid" or "the same as @".
        "[a--@]",
    ],
)
def test_regex_decisions_errors(ere: str) -> None:
    with pytest.raises(AwkRuntimeError):
        compile_ere(ere)


# ------------------------------------------------- engine choice
@pytest.mark.parametrize("ere", ["abc", "a[bc]d", "ab*", "a.c*", "^ab+$", "(ab|cd)x*", "a|b|c", "a*ab", "a{1,2}a"])
def test_safe_patterns_use_python(ere: str) -> None:
    assert _safe(parse(ere))
    assert isinstance(compile_span(ere), re.Pattern)


@pytest.mark.parametrize("ere", ["a|ab", "(ab)*", "(a|ab)c", "x(a|ab)", "(ab|a)(bc|c)?"])
def test_unsafe_patterns_use_automaton(ere: str) -> None:
    assert not _safe(parse(ere))
    assert isinstance(compile_span(ere), LongestPattern)


def test_safety_analysis_small_exhaustive() -> None:
    """Every pattern judged safe gives Python's longest match (small sample;
    scripts/regex_fuzz.py --verify-safety runs the full check)."""
    import itertools

    strings = ["".join(p) for n in range(5) for p in itertools.product("ab", repeat=n)]
    items = [a + q for a in ("a", "[ab]") for q in ("", "?", "*", "+", "{1,2}")]
    for n in (1, 2):
        for combo in itertools.product(items, repeat=n):
            ere = "".join(combo)
            tree = parse(ere)
            if not _safe(tree):
                continue
            lp = LongestPattern(tree, compile_ere(ere))
            for s in strings:
                m = lp.py.search(s)
                if m:
                    assert lp.longest(s, m.start()) == m.end(), (ere, s)


def test_self_loop_transitions_are_cached() -> None:
    lp = compile_span("(a|ab)*c")
    assert isinstance(lp, LongestPattern)
    assert lp.search("x" + "ab" * 50 + "c").span() == (1, 102)
    assert len(lp.cache) < 10


# ---------------------------------------------------------------- size limits
# A regex may come from the input (``$0 ~ $1``), and the automaton writes
# out every copy an interval asks for, so an unchecked pattern could use any
# amount of memory (CodeRabbit, PR #1): ``((a{0,200}){0,200}){0,200}`` took
# 1 GB and 3 s; with 1000s it would exhaust memory.  Two limits apply.
#
# Repetition counts: POSIX allows any limit (RE_DUP_MAX) of at least 255.
# onetrue-awk stops at 255, gawk at 32767, mawk has none.  The majority
# accepts a{0,1000} (gawk, mawk) and rejects a{0,100000} (onetrue, gawk),
# so pyawk takes gawk's 32767.


@pytest.mark.parametrize("ere", ["a{32767}", "a{0,32767}", "a{1000,}", "(ab){2,300}"])
def test_repetition_counts_up_to_32767_are_accepted(ere: str) -> None:
    parse(ere)


@pytest.mark.parametrize("ere", ["a{32768}", "a{0,32768}", "a{100000,}", "a{1,99999999999999999999}"])
def test_repetition_counts_above_32767_are_bad_regexes(ere: str) -> None:
    with pytest.raises(AwkRuntimeError, match="bad regular expression.*repetition count"):
        parse(ere)


# A count with thousands of digits must still be a bad regex.  Python
# refuses to convert a string of more than 4300 digits to an int (a guard
# against slow conversions), so converting first would crash with a Python
# ValueError instead (CodeRabbit, PR #1).  A regex can come from the input
# ($0 ~ $1), so this is reachable.  Leading zeros do not make a count big.
@pytest.mark.parametrize("ere", ["a{" + "9" * 5000 + "}", "a{1," + "9" * 5000 + "}", "a{" + "1" * 5000 + ",}"])
def test_counts_with_thousands_of_digits_are_bad_regexes(ere: str) -> None:
    with pytest.raises(AwkRuntimeError, match="bad regular expression.*repetition count"):
        parse(ere)


def test_unterminated_interval_with_thousands_of_digits_says_unterminated() -> None:
    with pytest.raises(AwkRuntimeError, match="bad regular expression.*unterminated"):
        parse("a{" + "9" * 5000)


def test_leading_zeros_do_not_count_towards_the_limit() -> None:
    parse("a{" + "0" * 5000 + "3}")
    parse("a{0" + "0" * 10 + ",00032767}")


def test_long_count_from_the_input_is_a_clean_error() -> None:
    import subprocess
    import sys
    from pathlib import Path

    res = subprocess.run(
        [sys.executable, "-m", "pyawk", "{ print ($0 ~ $1) }"],
        input="a{" + "9" * 5000 + "}\n",
        capture_output=True,
        text=True,
        cwd=Path(__file__).resolve().parent.parent, encoding="utf-8",
    )
    assert res.returncode == 2
    assert "Traceback" not in res.stderr
    assert "repetition count" in res.stderr


# Nested intervals multiply: no single count is large, but the automaton
# would be.  A pattern needing more than a million automaton states is
# refused before anything is built (a deliberate difference: gawk and mawk
# accept it, given enough memory and time).


@pytest.mark.parametrize("ere", ["((a{0,200}){0,200}){0,200}", "((a{0,1000}){0,1000}){0,1000}", "(a{32767}){32767}"])
def test_patterns_needing_huge_automata_are_bad_regexes(ere: str) -> None:
    with pytest.raises(AwkRuntimeError, match="bad regular expression.*too big"):
        compile_ere(ere)


def test_a_huge_dynamic_regex_is_a_runtime_error_not_a_hang() -> None:
    with pytest.raises(AwkRuntimeError, match="too big"):
        awk('{ print ($0 ~ $1) }', "((a{0,1000}){0,1000}){0,1000}\n")


@pytest.mark.parametrize("ere", ["(a|b{0,1000}){0,400}", "((a|aa){0,300}){0,300}", "(a|b){0,32767}"])
def test_large_but_reasonable_patterns_still_work(ere: str) -> None:
    assert compile_ere(ere).search("xaay") is not None
