"""The regex differential corpus: patterns, inputs and the probe program.

Shared by scripts/regex_fuzz.py (which runs the corpus through the three
reference awks and records their majority answer) and
tests/test_regex_differential.py (which checks pyawk against that record,
without needing the references installed).

Every pattern becomes one small AWK program (``probe_program``).  For each
input line it prints, on one output line:

    match() result, RLENGTH, the ``~`` result, gsub count, gsub result,
    split count and the split pieces

so a single run checks where a match starts (RSTART), how long it is
(RLENGTH, the place where leftmost-*longest* matters), plain yes/no
matching, every match of a global substitution, and regex field splitting.
One program per pattern means a pattern that one awk rejects (a syntax
error) only affects its own entry.

The corpus has two parts:

* ``CURATED``: hand-picked patterns, grouped by category (alternation,
  nesting, quantifiers, intervals, brackets, classes, anchors, escapes,
  empty alternatives), each with inputs chosen to exercise it;
* ``random_patterns``: small EREs built by a seeded random generator over
  the alphabet {a, b, c}, so the same seed always gives the same corpus.
"""

from __future__ import annotations

import io
import random
from dataclasses import dataclass

# Inputs shared by the random patterns and most curated ones: every short
# string shape over a small alphabet that tends to separate engines.
ABC_INPUTS = (
    "", "a", "b", "c", "ab", "ba", "aa", "bb", "abc", "aab", "abb", "abab", "baab",
    "aaab", "abba", "bbaa", "cabbac", "abcabc", "aaaa", "babab", "acbca", "xyz",
)


@dataclass(frozen=True)
class Entry:
    """One corpus pattern: its category, ERE text and the input lines."""

    category: str
    pattern: str
    inputs: tuple[str, ...] = ABC_INPUTS

    @property
    def key(self) -> str:
        """Unique id used in the recorded results file."""
        return f"{self.category}:{self.pattern}"


def _c(category: str, patterns: list[str], inputs: tuple[str, ...] = ABC_INPUTS) -> list[Entry]:
    return [Entry(category, p, inputs) for p in patterns]


_WORDS = ("", "a", "ab", "abc", "abcd", "xabcdx", "aXbXc", "foobar", "foo bar", "barfoo", "xyyx", "aaa", "ba")
_BRACKET_INPUTS = (
    "", "a", "b", "z", "A", "-", "]", "[", "^", "\\", "\t", " ", ".", "*", "0", "9", "_", "a-z", "]a[",
    "x^y", "q\\n", "=", ":", "ab]", "!", "~", "{", "|",
)
_ESC_INPUTS = ("", "a.b", "axb", "a/b", 'a"b', "a\tb", "a\\b", "a+b", "a*b", "a$b", "a^b", "(a)", "[a]",
               "{a}", "a?b", "a|b", "ab", "nn", "tt", "a\\.b")

CURATED: list[Entry] = [
    *_c("alternation", [
        "a|ab", "ab|a", "a|ab|abc", "abc|ab|a", "(a|ab)(c|bcd)", "(a|ab)(c|bcd)(d*)", "x(a|ab)", "(a|b|ab|ba)*",
        "(ab|a)(bc|c)?", "(a|ab)b", "b|ab|aba", "(aa|a)a", "(a|aa)a", "(a|aa)*", "(aa|a)*b", "(a|aa)+",
        "(ab|a)*", "(a|ab)*c", "(b|ba|bab)", "c|ca|cab|cabb", "(a|b)(c|ab)", "(foo|foobar)", "(bar|foo)+",
    ], ABC_INPUTS + _WORDS),
    *_c("nested", [
        "((a|ab)(c|b))", "(a(b|bc))*", "((a)*)*", "((ab)*a)*", "(a*)*b", "(a*b*)*", "((a|b)*)c",
        "(a+|b+)*", "((a|b)b)+", "(a(ab)*)+", "((aa)+|a)+", "(ab*)+b", "((a)|b)+", "(((a)))",
        "(a*|b)*c", "((a*)(b*))*", "(a|(a(b|c)))+",
    ]),
    *_c("quantifiers", [
        "a*", "a+", "a?", "a*b", "a+b", "a?b", "ab*", "ab+", "ab?", "x*(xy)?", "a*ab", "a?a?a",
        "(ab)*", "(ab)+", "(ab)?", ".*", ".+", ".?", "a.*b", "a.+b", "a.?b", "[ab]*b", "[ab]+a",
        "a+?", "a*?", "a??", "a++", "a?*", "a*+", "(a+)?", "b*a*b*", "a*b?a*", "(a?b?)*", "(a?)+b",
        "ba*", "b+a+", "c?a*", "a**", "(a*)+", "(a+)*b",
        "*a", "+a", "?a", "a|*b", "(*a)", "(+a)", "a(?b)", "a)", ")", "(a", "a(b",
    ]),
    *_c("intervals", [
        "a{2}", "a{1,2}", "a{2,}", "a{0}", "a{0,1}", "a{0,0}b", "(ab){2}", "(a|b){2,3}", "a{2}+",
        "a{1}?", "a{2}*", "a{,2}", "a{1,3}b", "b{0,}a", "(a{1,2}){2}", "[ab]{3}", "a{3,5}",
        "a{", "a{x}", "a{1", "{", "a{1,2", "{1}", "a{2,1}", "ab{1}c", "a{1}{2}", "x{1,}y?",
    ], ABC_INPUTS + ("{", "a{", "a{x}", "a{1", "a{,2}", "{1}", "a{1,2", "xy", "xxy", "aaaaaa")),
    *_c("brackets", [
        "[a]", "[^a]", "[]a]", "[^]a]", "[a-]", "[-a]", "[^-a]", "[a-c]", "[a\\]]", "[\\]]", "[\\\\]",
        "[\\t]", "[\\n]", "[[]", "[a[]", "[]]", "[]-a]", "[!-]", "[^^]", "[x^]", "[*.]", "[.]", "[{|]",
        "[[=a=]]", "[[=a=]b]", "[[.a.]]", "[[.-.]]", "[[.].]]", "[a[.-.]z]", "[[=-=]]", "[[.^.]]",
        "[a-a]", "[%--]", "[--/]", "[\\-]", "[a\\-z]", "[\\^a]", "[q\\n]", "[\\/]", "[a-c-e]",
        "[]-]", "[^]]", "[=]", "[:]", "[[:]", "[a]]", "[\\.]",
        "[z-a]", "[b-a]", "[a", "a[", "[^", "[", "[[:foo:]]", "[[:alpha:]", "[[=ab=]]", "[[.ab.]]",
        "[[.space.]]", "[[=a]", "[[.a]", "[[=]=]]", "[[.[.]]", "[[:alpha:][.-.]]", "[[.a.]-c]",
    ], _BRACKET_INPUTS),
    *_c("classes", [
        "[[:alpha:]]", "[[:digit:]]", "[[:alnum:]]", "[[:upper:]]", "[[:lower:]]", "[[:space:]]",
        "[[:blank:]]", "[[:punct:]]", "[[:xdigit:]]", "[[:cntrl:]]", "[[:print:]]", "[[:graph:]]",
        "[^[:alpha:]]", "[[:digit:][:upper:]]", "[[:alpha:]_]+", "[^[:space:]]+", "[a[:digit:]]",
        "[[:alpha:]-]", "[[:digit:]-z]",
    ], _BRACKET_INPUTS + ("abc123", "A1 b2", "x_y-z", "\x01", "\x7f")),
    *_c("anchors", [
        "^a", "a$", "^a$", "^$", "^", "$", "x^a", "a$b", "(^a)", "(a$)", "(^a|b)", "(a|^b)", "(b|a$)",
        "^a|b$", "(^|b)a", "a(b|$)", "^*a", "^+a", "^?a", "a^", "$a", "(^a)*", "(a$)+", "^(a|ab)$",
        "^(ab|a)", "b(^a)", "(^)", "($)", "^^a", "a$$",
    ]),
    *_c("escapes", [
        "a\\.b", "a.b", "a\\/b", 'a\\"b', "a\\tb", "a\\\\b", "a\\+b", "a\\*b", "a\\$b", "a\\^b",
        "\\(a\\)", "\\[a\\]", "\\{a\\}", "a\\?b", "a\\|b", "\\n", "\\t", "a\\.\\b", "\\a", "\\y",
        "\\B", "\\w", "\\s", "\\d", "\\q", "\\141", "\\x41", "a\\b", "\\.",
    ], _ESC_INPUTS),
    *_c("empty", [
        "(a|)b", "(|a)b", "a||b", "a|", "|a", "(|)", "()", "()a", "a()", "(a|b|)", "(|a|b)+", "a(|b)c",
    ], ABC_INPUTS + ("ac", "abc")),
]


def random_patterns(count: int = 300, seed: int = 1) -> list[Entry]:
    """``count`` random small EREs over {a, b, c}, the same for the same seed.

    The grammar is a tiny recursive one: an alternation of concatenations
    of atoms, each atom optionally followed by one quantifier.  Depth and
    lengths are kept small so every pattern is readable and quick to run.
    Duplicates are dropped (so slightly fewer than ``count`` may appear).
    """
    rng = random.Random(seed)

    def atom(depth: int) -> str:
        r = rng.random()
        if r < 0.45:
            a = rng.choice("abc")
        elif r < 0.55:
            a = "."
        elif r < 0.65:
            a = rng.choice(["[ab]", "[^a]", "[bc]", "[^bc]"])
        elif r < 0.72 and depth < 2:
            return rng.choice(["^", "$"]) if rng.random() < 0.5 else "(" + alt(depth + 1) + ")"
        elif depth < 2:
            a = "(" + alt(depth + 1) + ")"
        else:
            a = rng.choice("ab")
        q = rng.random()
        if q < 0.15:
            a += "*"
        elif q < 0.25:
            a += "+"
        elif q < 0.35:
            a += "?"
        elif q < 0.38:
            a += rng.choice(["{2}", "{1,2}", "{0,1}", "{2,}"])
        return a

    def concat(depth: int) -> str:
        return "".join(atom(depth) for _ in range(rng.randint(1, 3)))

    def alt(depth: int) -> str:
        n = 1 if rng.random() < 0.55 else rng.randint(2, 3)
        return "|".join(concat(depth) for _ in range(n))

    seen: set[str] = set()
    out: list[Entry] = []
    for _ in range(count):
        p = alt(0)
        if p not in seen:
            seen.add(p)
            out.append(Entry("random", p))
    return out


# Patterns whose recorded answer comes from one named reference instead of
# the majority vote, each with the reason.  A vote is only meaningful when
# the references that agree do so for the same reason; in these cases the
# "majority" is two different bugs that happen to give the same answer.
#
# * Equivalence classes ``[=x=]`` and collating symbols ``[.x.]``: mawk
#   does not implement them at all (it reads them as ordinary bracket
#   members) and onetrue-awk only as the sole member of a bracket.  gawk
#   --posix follows POSIX in the C locale: ``[=a=]`` and ``[.a.]`` each
#   mean just "a"; a multi-character name such as ``[.ab.]`` or
#   ``[.space.]`` is an invalid collating element (an error).
# * Two random patterns that only fail because mawk rejects ``$(`` at the
#   start of a branch and onetrue-awk rejects ``^`` after other items; each
#   construct alone is accepted by the other two awks.
# * ``a(|b)c``: mawk accepts some empty alternatives but misparses them
#   (it reports ``x(a|)b`` matching "abc"), so its agreement with gawk
#   here is accidental; onetrue-awk's error is the consistent rule that
#   the majority applies to ``a|``, ``|a``, ``(|b)`` and ``(a|)b``.
# DESIGN.md lists these as decisions taken against the plain vote.
_COLLATING = "collating symbol / equivalence class: POSIX (gawk) rather than mawk/onetrue bugs"
_TWO_BUGS = "error only from two different bugs: mawk rejects $( at a branch start, onetrue a non-leading ^"
# The reference named "none" means: record nothing, test nothing.
OVERRIDES: dict[str, tuple[str, str]] = {
    **{
        p: ("gawk", _COLLATING)
        for p in (
            "[[=a=]]", "[[=a=]b]", "[[.a.]]", "[[.-.]]", "[[.].]]", "[a[.-.]z]", "[[=-=]]", "[[.^.]]",
            "[[=ab=]]", "[[.ab.]]", "[[.space.]]", "[[=a]", "[[.a]", "[[=]=]]", "[[.[.]]",
            "[[:alpha:][.-.]]", "[[.a.]-c]",
        )
    },
    "(c^|a+(a)b*|c+c)?|$(c+b(aa|[bc]bb?)|[ab][bc].|(bb?|ab?a?)+b)": ("gawk", _TWO_BUGS),
    # gawk's split() is wrong here too (an empty match at the start drops
    # the first character: split("ab", A, /x*^/) gives "b"), so not tested.
    "a+$([^a]+a*$|bcc)|(ac(a|a{2}ac|.b?b)|c{2,}|a*)^": ("none", _TWO_BUGS + "; gawk's split is wrong too"),
    "a(|b)c": ("onetrue", "empty alternative: mawk accepts it here only by misparsing; error as for a| and (|b)"),
}


def corpus() -> list[Entry]:
    """The whole corpus: curated entries first, then the random ones."""
    entries = CURATED + random_patterns()
    seen: set[str] = set()
    unique = []
    for e in entries:
        if e.key not in seen:
            seen.add(e.key)
            unique.append(e)
    return unique


def probe_program(pattern: str) -> str:
    """The AWK program that reports how ``pattern`` matches each input line.

    The pattern is pasted in as a regex literal ``/.../``; a ``/`` inside
    it must already be written ``\\/``.  Pieces of split() are joined with
    ``:`` after the count; ``<&>`` marks each gsub replacement.
    """
    p = "/" + pattern + "/"
    return (
        "{ s = $0; r = match(s, P); printf \"%d %d %d\", r, RLENGTH, (s ~ P)\n"
        "  t = s; n = gsub(P, \"<&>\", t); printf \" %d %s\", n, t\n"
        "  n = split(s, A, P); printf \" %d\", n\n"
        "  for (i = 1; i <= n; i++) printf \":%s\", A[i]\n"
        "  print \"\" }\n"
    ).replace("P", p)


def stdin_text(entry: Entry) -> str:
    return "".join(line + "\n" for line in entry.inputs)


ERROR = "ERROR"


def run_pyawk(entry: Entry) -> str:
    """Run the probe through pyawk in this process; its stdout, or ERROR."""
    from pyawk.cli import run
    from pyawk.errors import AwkError

    out = io.StringIO()
    try:
        status = run(
            probe_program(entry.pattern),
            stdout=out,
            stdin=io.BytesIO(stdin_text(entry).encode("utf-8", "surrogateescape")),
        )
    except AwkError:
        return ERROR
    return out.getvalue() if status == 0 else ERROR
