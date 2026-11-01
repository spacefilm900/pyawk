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
