"""No regular expression may take exponential (or worse than linear) time.

Python's ``re`` is a backtracking matcher.  On a pattern with nested or
overlapping repetition, such as ``(a*)*b``, a line with no match makes it
try every way of dividing the text among the repetitions: about 2**n ways
for n characters.  30 ``a``s took close to a minute.  The reference awks
use automata (DFAs) and answer instantly; so must pyawk.

Each case runs in a child process, so a regression shows up as a clean
timeout failure instead of a hung test run.  The child times the AWK work
itself (not Python's start-up) and prints the seconds taken.

The cases use every place a regex is matched: ``~``, match(), sub(),
gsub(), split() and a regex FS.  Their answers on short inputs are also
checked against the three reference awks by the differential corpus (the
``pathological`` category of awk_tests/regex_corpus.py).
"""

import subprocess
import sys
from pathlib import Path

import pytest

from pyawk.regex import _backtrack_safe, compile_ere, compile_span, parse

ROOT = Path(__file__).resolve().parent.parent

# One probe per pattern P: every regex use on each input line, then the
# field count with P as FS.  Prints: match() RLENGTH ~ sub gsub split NF.
PROBE = (
    '{ r = match($0, P); m = ($0 ~ P); s = $0; n1 = sub(P, "<&>", s); t = $0; n2 = gsub(P, "<&>", t);'
    " k = split($0, A, P); print r, RLENGTH, m, n1, n2, k }"
)
FS_PROBE = 'BEGIN { FS = ENVIRON["PYAWK_T_FS"] } { print NF }'

# The child: run both probes on the input and report the time taken.
CHILD = """
import io, os, sys, time
sys.path.insert(0, {root!r})
from pyawk.cli import run
pattern, data = sys.argv[1], sys.stdin.read()
os.environ["PYAWK_T_FS"] = pattern  # FS from ENVIRON: no string-escape processing
probe, fs_probe = sys.argv[2], sys.argv[3]
out = io.StringIO()
start = time.perf_counter()
run(probe.replace("P", "/" + pattern + "/"), [], (), stdout=out, stdin=io.BytesIO(data.encode()))
run(fs_probe, [], (), stdout=out, stdin=io.BytesIO(data.encode()))
print(time.perf_counter() - start)
sys.stdout.write(out.getvalue())
"""


def timed(pattern: str, line: str, limit: float = 30.0) -> tuple[float, list[str]]:
    """(seconds, output lines) for both probes on one input line."""
    proc = subprocess.run(
        [sys.executable, "-c", CHILD.format(root=str(ROOT)), pattern, PROBE, FS_PROBE],
        input=line + "\n",
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=limit,  # a hang becomes subprocess.TimeoutExpired, a failure
    )
    assert proc.returncode == 0, proc.stderr
    first, *rest = proc.stdout.split("\n")
    return float(first), rest[:-1]


NO_MATCH = ["0 -1 0 0 0 1", "1"]  # not found anywhere; split and FS give one piece

# (pattern, a long line on which it never matches).
PATHOLOGICAL = [
    ("(a*)*b", "a"),
    ("(a|a)*b", "a"),
    ("(a+)+b", "a"),
    ("(a|aa)+$", "a"),  # the line ends in "!", so $ never follows the a's
    ("([a-z]+)*[0-9]", "abcdefghij"),
    ("(a|aa)*c", "a"),
    ("(x+x+)+y", "x"),
    ("^(a+)+$", "a"),
    ("(.*)*x", "abc"),
    ("(a*b*)*c", "ab"),
    ("((a|b)*b)*c", "ab"),
    ("(a?){25}a{25}", "a"),  # pattern-size blowup: mandatory a's outnumber the text
]


def _line(unit: str, n: int, pattern: str) -> str:
    text = (unit * n)[:n]
    if pattern.endswith("$"):
        text = text[:-1] + "!"
    return text


@pytest.mark.parametrize("pattern, unit", PATHOLOGICAL)
def test_short_non_matching_line_is_fast(pattern: str, unit: str) -> None:
    # 24 characters for the interval case (its 25 mandatory a's cannot fit),
    # 40 for the rest: Python's backtracking needed minutes or more here.
    n = 24 if "{25}" in pattern else 40
    seconds, out = timed(pattern, _line(unit, n, pattern))
    assert out == NO_MATCH
    assert seconds < 1.0, f"/{pattern}/ took {seconds:.2f}s on {n} characters"


@pytest.mark.parametrize("pattern, unit", PATHOLOGICAL)
def test_long_non_matching_line_is_linear(pattern: str, unit: str) -> None:
    # A quadratic matcher needs minutes on 100 000 characters; a linear one
    # well under a second per regex use.  The bound is generous for slow
    # machines while still ruling out anything worse than linear.
    if "{25}" in pattern:
        pytest.skip("only meaningful below 25 characters (it matches longer lines)")
    seconds, out = timed(pattern, _line(unit, 100_000, pattern), limit=60)
    assert out == NO_MATCH
    assert seconds < 5.0, f"/{pattern}/ took {seconds:.2f}s on 100 000 characters"


@pytest.mark.parametrize(
    "pattern, line, expected",
    [
        # Matches near the end of a long line: start, length and the split.
        ("(a*)*b", "a" * 5000 + "b", ["1 5001 1 1 1 2", "2"]),
        ("(a|aa)+$", "x" + "a" * 5000, ["2 5000 1 1 1 2", "2"]),
        ("([a-z]+)*[0-9]", "-" + "abc" * 2000 + "7", ["2 6001 1 1 1 2", "2"]),
    ],
)
def test_long_matching_lines(pattern: str, line: str, expected: list[str]) -> None:
    seconds, out = timed(pattern, line)
    assert out == expected
    assert seconds < 5.0


def test_quadratic_backtracking_is_capped_on_long_lines() -> None:
    """Patterns left to Python (one ambiguous repetition, like ``[0-9]+5``)
    can still cost time proportional to the square of the line length;
    long lines go to the automaton instead, so 100 000 digits stay fast."""
    seconds, out = timed("[0-9]+5\\.", "5" * 100_000)
    assert out == NO_MATCH
    assert seconds < 5.0


# ------------------------------------------------ the safety analysis
@pytest.mark.parametrize(
    "ere",
    ["abc", "[0-9]+", "^ *", "a.*b", "x[ab]*y?", "foo|bar", "[0-9]+5\\.|qu+x.*thud", "^(b|d)[a-z]+o$",
     "(a|a)*b", "(foo|bar)baz", "a{2,5}b"],
)
def test_backtrack_safe_patterns_keep_python(ere: str) -> None:
    import re

    assert _backtrack_safe(parse(ere))
    assert isinstance(compile_ere(ere), re.Pattern)


@pytest.mark.parametrize(
    "ere",
    ["(a*)*b", "(a+)+b", "(a|aa)+$", "([a-z]+)*[0-9]", ".*x.*y", "a*a*b", "(ab|a)*", "(a|ab)(c|bcd)*",
     "(ab|a.)(ab|a.)(ab|a.)(ab|a.)(ab|a.)(ab|a.)(ab|a.)x"],
)
def test_risky_patterns_use_the_automaton(ere: str) -> None:
    from pyawk.regex import LongestPattern

    assert not _backtrack_safe(parse(ere))
    assert isinstance(compile_ere(ere), LongestPattern)
    assert isinstance(compile_span(ere), LongestPattern)


def test_dfa_cache_limit_stays_linear_and_correct(monkeypatch: pytest.MonkeyPatch) -> None:
    """``(a|b)*a(a|b){12}`` needs 2**13 DFA states.  With the cache limit set
    far below that, states are rebuilt over and over; each input character
    then costs at most one new state, so the scan stays linear, and the
    answers must not change."""
    import random
    import time

    from pyawk import regex

    ere = "(a|b)*a(a|b){12}c"
    rng = random.Random(3)
    text = "".join(rng.choice("ab") for _ in range(20_000)) + "c"
    expected = regex.LongestPattern(parse(ere), None).search(text)
    monkeypatch.setattr(regex, "_DFA_LIMIT", 64)
    lp = regex.LongestPattern(parse(ere), None)
    start = time.perf_counter()
    got = lp.search(text)
    assert time.perf_counter() - start < 10.0
    assert got is not None and expected is not None and got.span() == expected.span()
    assert max(len(d.cache) for d in lp.dfas()) <= 64


def test_dfa_cache_limit_really_bounds_memory(monkeypatch: pytest.MonkeyPatch) -> None:
    """Emptying the cache must let the old states go (CodeRabbit, PR #1).

    A scan in progress holds on to one old state (its current one, and the
    "idle" state), and old states keep their transitions, which link them
    to every state built later; so if those links stay, nothing built
    during a long scan is ever freed.  Count the live DFA states (with a
    garbage collection each time the cache is emptied) during one long
    search: they must stay a small multiple of the limit."""
    import gc
    import random

    from pyawk import regex

    live = [0]  # live _DState objects; a list so the class below can change it
    peak = [0]

    class CountedState(regex._DState):
        __slots__ = ()

        def __init__(self, *args: object) -> None:
            super().__init__(*args)  # type: ignore[arg-type]
            live[0] += 1

        def __del__(self) -> None:
            live[0] -= 1

    original_state = regex._DFA.state

    def state(self: regex._DFA, kernel: frozenset[int], at_start: bool) -> regex._DState:
        full = len(self.cache) >= regex._DFA_LIMIT
        d = original_state(self, kernel, at_start)
        if full:  # the cache was just emptied: what is still alive?
            gc.collect()
            peak[0] = max(peak[0], live[0])
        return d

    monkeypatch.setattr(regex, "_DState", CountedState)
    monkeypatch.setattr(regex._DFA, "state", state)
    monkeypatch.setattr(regex, "_DFA_LIMIT", 64)
    rng = random.Random(5)
    text = "".join(rng.choice("ab") for _ in range(20_000)) + "a" + "b" * 12 + "c"
    lp = regex.LongestPattern(parse("(a|b)*a(a|b){12}c"), None)
    assert lp.found(text)
    assert lp.search(text) is not None
    assert 0 < peak[0] <= 4 * 64


def test_regex_rs_on_long_input_is_linear() -> None:
    # A regex RS is searched over the whole (buffered) input.
    prog = 'BEGIN { RS = "(a*)*b" } { n++; len += length($0) } END { print n, len }'
    for data, want in (("a" * 100_000, "1 100000"), (("c" * 999 + "b") * 100, "100 99900")):
        proc = subprocess.run(
            [sys.executable, "-m", "pyawk", prog], input=data, capture_output=True, text=True, timeout=30, cwd=ROOT,
            encoding="utf-8",
        )
        assert proc.stdout.strip() == want, proc.stderr


# ------------------------------------------- skip-ahead and quick probing
@pytest.mark.parametrize(
    "ere, text, found, spans",
    [
        ("(x|y)+z|$", "abc", True, [(3, 3)]),  # only the empty match at the end
        ("(x|y)+z|$", "xxz-", True, [(0, 3), (4, 4)]),
        ("^(a|ab)+c|b", "abac", True, [(0, 4)]),  # ``^``: position 0 has its own first set
        ("^(a|ab)+c|b", "xabac", True, [(2, 3)]),
        ("(a|ab)+c", "xyz", None, []),
        ("(ab|a)*", "xab", True, [(0, 0), (1, 3), (3, 3)]),  # empty matches: nothing to skip
    ],
)
def test_skip_ahead_edges(ere: str, text: str, found: bool | None, spans: list[tuple[int, int]]) -> None:
    from pyawk.regex import LongestPattern

    lp = LongestPattern(parse(ere), None)
    assert lp.found(text) is found
    assert [m.span() for m in lp.finditer(text)] == spans
    marks_only = LongestPattern(parse(ere), None)
    marks_only._probed, marks_only._budget = text, 0  # no budget: the one-pass marks
    assert [m.span() for m in marks_only.finditer(text)] == spans


def test_probing_falls_back_to_one_pass() -> None:
    """``a*b|c`` on a run of a's ending in "c": every try reads to the end
    and fails, which would be quadratic; the shared budget runs out and the
    one-pass marks take over, so 100 000 characters stay fast."""
    seconds, out = timed("a*(a*)*b|c", "a" * 100_000 + "c")
    assert out == ["100001 1 1 1 1 2", "2"]
    assert seconds < 5.0


def test_compile_cache_bounds_the_automaton_states_it_keeps(monkeypatch: pytest.MonkeyPatch) -> None:
    """The cache of compiled regexes must not keep unlimited automata
    (CodeRabbit, PR #1, security review).

    Each pattern may need up to NFA_LIMIT states, and a dynamic regex
    taken from the input can be a different big pattern on every line.
    The cache used to keep 512 of them whatever their size: patterns like
    ``(a|b{0,1000}){0,400}`` (800 000 states, about 48 MB each) could fill
    many gigabytes.  The cache now drops its oldest entries
    once the states it keeps pass CACHE_STATE_LIMIT.  Here the limit is
    made small, and twenty patterns of about 20 000 states each are
    compiled: the states still alive afterwards must stay within the limit
    plus the newest pattern."""
    import gc

    from pyawk import regex

    def live_states() -> int:
        gc.collect()
        return sum(len(o.kind) for o in gc.get_objects() if type(o) is regex._NFA)

    regex._compile.cache_clear()
    monkeypatch.setattr(regex, "CACHE_STATE_LIMIT", 50_000, raising=False)
    try:
        before = live_states()
        sizes = []
        for i in range(20):
            ere = f"(a|b{{0,100}}){{0,{95 + i}}}"
            sizes.append(regex._nfa_size(parse(ere)))
            assert compile_span(ere).search("ab" * 20) is not None
        assert live_states() - before <= 50_000 + max(sizes)
        # The newest pattern is kept, so using it again costs nothing.
        assert compile_span(ere) is compile_span(ere)
    finally:
        regex._compile.cache_clear()


def test_compile_cache_still_keeps_small_patterns() -> None:
    """Small patterns cost almost nothing, so hundreds of them stay cached
    (the old 512-entry limit), and a repeated one is not recompiled."""
    from pyawk import regex

    regex._compile.cache_clear()
    try:
        first = [compile_ere(f"x{i}y") for i in range(500)]
        assert all(compile_ere(f"x{i}y") is p for i, p in enumerate(first))
    finally:
        regex._compile.cache_clear()
