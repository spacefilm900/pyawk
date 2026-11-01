r"""POSIX extended regular expressions (EREs) for AWK.

Role in the pipeline: a helper for the compiler, the runtime and the
builtins.  Entry points:

* ``compile_ere(text)``: a Python ``re`` pattern with the ERE's meaning.
  Good for every *yes/no* question (``$0 ~ /re/``, a bare ``/re/``
  pattern) and for finding where the leftmost match *starts*.
* ``compile_span(text)``: an object for the questions where the exact
  extent of a match matters (match()'s RSTART/RLENGTH, sub/gsub, regex
  field splitting, a regex RS).  It is the same Python pattern when that
  is provably correct, otherwise a ``LongestPattern`` (below).

Why two engines
---------------
POSIX says a regex matches the *leftmost-longest* text: of all the places
where a match could start, take the first; of all the matches starting
there, take the longest.  Python's ``re`` (like Perl's) is a *backtracking*
matcher: it tries the alternatives of ``a|ab`` in order and stops at the
first one that lets the rest of the pattern succeed ("leftmost-first").
The start position is the same either way, since both try every start
from left to right, but the length can differ::

    match("xabc", /a|ab/)       POSIX: RSTART 2, RLENGTH 2 ("ab")
                                Python's first choice: "a", length 1
    match("xxy", /x*(xy)?/)      POSIX: "xxy" (3); Python: "xx" (2)

Most patterns cannot tell the two apart (``[0-9]+``, ``^ *``, ``foo``,
``a.*b``), and for those Python's matcher, which runs in C, is kept.
``_safe`` decides this conservatively from the pattern's structure.  For
the rest, ``LongestPattern`` asks Python only where the match starts, then
finds the longest match from there with an automaton:

* The ERE is turned into a *Thompson NFA* (nondeterministic finite
  automaton): a graph whose nodes are numbered states and whose edges are
  either "consume one character from this set" or "free" moves (epsilon
  edges) for alternation and repetition.  The pattern matches a string
  when some path from the start state to the final state spells it.
* Rather than trying the paths one at a time (backtracking), we follow all
  of them at once: the current "position" is the *set* of NFA states we
  could be in.  Each such set becomes one state of a DFA (deterministic
  automaton), built lazily, i.e. only when the input first needs it, and
  cached, so a pattern used on every input line pays the construction cost
  once.  Running the DFA over the text and remembering the last position
  where the set contained the final state gives the longest match, in time
  proportional to the text length.

Parsing follows what the reference awks (onetrue-awk, mawk, gawk --posix)
do, by majority where they differ; DESIGN.md ("Regular expressions")
lists the decisions.  In short:

* ``{n}``, ``{n,}``, ``{n,m}`` are intervals; a ``{`` not followed by a
  digit is a literal; ``{n`` without its ``}`` or ``{2,1}`` is an error.
* Each repeat operator applies to everything before it: ``a{2}+`` is
  ``(a{2})+``, ``a+?`` is ``(a+)?`` (never Python's lazy ``+?``).
* An operator with nothing to repeat (``*a``, ``(+a)``, ``a|?b``) is an
  error, except right after ``^``: ``^+`` is ``^``, ``^*``/``^?`` vanish.
* Empty alternatives (``a|``, ``(|b)``) are errors; ``()`` is allowed.
* An unmatched ``(`` is an error; an unmatched ``)`` is a literal.
* ``^`` and ``$`` are anchors anywhere; ``$`` is the very end only (Python's
  ``$`` would also match before a final newline, so we emit ``\Z``).
* ``.`` and negated brackets match newline (``re.DOTALL``).
* Escapes: ``\n \t \r \f \v \a``, ``\b`` (backspace), octal ``\ddd``, hex
  ``\xhh``; any other escaped character is itself (``\.``, ``\/``, and
  ``\w``, ``\s``, ``\y`` are the plain letters, as in all three awks).
* Brackets: a leading ``]`` is a member, ``-`` is literal first or last,
  backslash escapes work inside, ``[:class:]`` names are the POSIX ones,
  and ``[=a=]`` and ``[.a.]`` both mean "a".  An unterminated bracket, an
  unknown class, a reversed range (``[z-a]``) or a multi-character
  collating name is an error.
* Locale (charset.py).  In the C locale a character is a byte and the
  classes are ASCII.  In UTF-8 mode a character is a Unicode character,
  the classes come from gawk's tables (unicode_tables.py), escapes such as
  ``\303\251`` that spell one character are that character, and ``.``
  and negated brackets do not match an invalid byte (as in gawk).

The parser builds a small syntax tree (``Chars``, ``Cat``, ``Alt``,
``Repeat``, ``BOL``, ``EOL``, ``EMPTY``); the Python pattern text, the
safety check and the NFA are all produced from that tree.
"""

# Treat type hints as text, so newer syntax such as ``tuple[str, int]`` is allowed.
from __future__ import annotations

import re
import weakref
from bisect import bisect_right
from collections.abc import Callable, Iterator
from functools import lru_cache
from typing import Literal

from . import charset
from .errors import AwkRuntimeError

# ----------------------------------------------------------- syntax tree
# Character sets are kept as sorted, merged ranges of code points:
# [a-cx] is ((97, 99), (120, 120)).  ``negated`` flips the set ([^...]).
Ranges = tuple[tuple[int, int], ...]


class Chars:
    """Match one character from a set (a literal, ``.``, or a bracket)."""

    __slots__ = ("ranges", "negated", "_starts")

    def __init__(self, ranges: Ranges, negated: bool = False) -> None:
        self.ranges = ranges
        self.negated = negated
        # Big sets (Unicode classes have hundreds of ranges) are searched
        # by bisection on the range starts instead of one by one.
        self._starts = tuple(lo for lo, _ in ranges) if len(ranges) > 8 else None

    def __contains__(self, ch: str) -> bool:
        o = ord(ch)
        if self._starts is not None:
            # bisect_right finds how many range starts are <= o; the range
            # before that point is the only one that can hold o.
            i = bisect_right(self._starts, o) - 1
            return (i >= 0 and o <= self.ranges[i][1]) != self.negated
        for lo, hi in self.ranges:
            if lo <= o <= hi:
                return not self.negated
        return self.negated


class Cat:
    """Concatenation: the items one after another."""

    __slots__ = ("items",)

    def __init__(self, items: list) -> None:
        self.items = items


class Alt:
    """Alternation: any one of the branches."""

    __slots__ = ("branches",)

    def __init__(self, branches: list) -> None:
        self.branches = branches


class Repeat:
    """``node`` repeated min..max times (max None: unbounded)."""

    __slots__ = ("node", "min", "max")

    def __init__(self, node: object, lo: int, hi: int | None) -> None:
        self.node = node
        self.min = lo
        self.max = hi


class _Leaf:
    """The three childless nodes; each exists once and is compared by identity."""

    __slots__ = ("name",)

    def __init__(self, name: str) -> None:
        self.name = name


BOL = _Leaf("^")  # beginning of the string
EOL = _Leaf("$")  # end of the string
EMPTY = _Leaf("()")  # matches the empty string


def _merge(ranges: list[tuple[int, int]]) -> Ranges:
    """Sort ranges and merge overlapping or touching ones."""
    out: list[tuple[int, int]] = []
    for lo, hi in sorted(ranges):
        if out and lo <= out[-1][1] + 1:
            if hi > out[-1][1]:
                out[-1] = (out[-1][0], hi)
        else:
            out.append((lo, hi))
    return tuple(out)


def _literal(ch: str) -> Chars:
    return Chars(((ord(ch), ord(ch)),))


ANY = Chars((), negated=True)  # ``.``: the complement of nothing
# ``.`` in UTF-8 mode: any character, but not an invalid byte (charset.py
# keeps those as the code points U+DC80..U+DCFF).  gawk's ``.`` does not
# match an invalid byte either.
_ESCAPED_BYTES = (charset.ESCAPED_LO, charset.ESCAPED_HI)
ANY_UTF8 = Chars((_ESCAPED_BYTES,), negated=True)


def _r(spec: str) -> list[tuple[int, int]]:
    """Ranges from a compact spec: "a-zA-Z_" -> [(97, 122), (65, 90), (95, 95)]."""
    out = []
    i = 0
    while i < len(spec):
        if i + 2 < len(spec) and spec[i + 1] == "-":
            out.append((ord(spec[i]), ord(spec[i + 2])))
            i += 3
        else:
            out.append((ord(spec[i]), ord(spec[i])))
            i += 1
    return out


# POSIX named classes for the C locale, as used inside brackets.  UTF-8
# mode uses unicode_tables.CLASSES instead (see _Parser.bracket_element).
_CLASSES: dict[str, list[tuple[int, int]]] = {
    "alpha": _r("a-zA-Z"),
    "digit": _r("0-9"),
    "alnum": _r("0-9a-zA-Z"),
    "upper": _r("A-Z"),
    "lower": _r("a-z"),
    "space": _r(" \t\n\r\f\v"),
    "blank": _r(" \t"),
    "punct": _r("!-/:-@[-`{-~"),
    "xdigit": _r("0-9A-Fa-f"),
    "cntrl": [(0, 31), (127, 127)],
    "print": [(32, 126)],
    "graph": [(33, 126)],
}

_ESCAPES = {"n": "\n", "t": "\t", "r": "\r", "f": "\f", "v": "\v", "a": "\a", "b": "\b"}
_OCTAL = "01234567"
_HEX = "0123456789abcdefABCDEF"


# ---------------------------------------------------------------- parser
class _Parser:
    """Recursive-descent parser: ERE text -> syntax tree.

    Grammar (``|`` binds loosest, then concatenation, then the postfix
    repeat operators)::

        alt    := cat ('|' cat)*
        cat    := (atom postfix*)*
        atom   := '(' alt? ')' | '[' bracket ']' | '.' | '^' | '$'
                  | '\\' char | char
        postfix:= '*' | '+' | '?' | '{' n [',' [m]] '}'
    """

    def __init__(self, ere: str) -> None:
        self.s = ere
        self.i = 0
        self.depth = 0  # open parentheses; a ')' at depth 0 is a literal

    def error(self, why: str) -> AwkRuntimeError:
        return AwkRuntimeError(f"bad regular expression /{self.s}/: {why}")

    def parse(self) -> object:
        tree = self.alt()
        # Refuse a pattern whose automaton would be enormous now, before
        # anything is built (see NFA_LIMIT).
        if _nfa_size(tree) > NFA_LIMIT:
            raise self.error(f"too big (needs more than {NFA_LIMIT} automaton states)")
        return tree

    def alt(self) -> object:
        branches = []
        empty_branch = False
        while True:
            start = self.i
            branches.append(self.cat())
            empty_branch |= self.i == start
            if self.i < len(self.s) and self.s[self.i] == "|":
                self.i += 1
                continue
            break
        if len(branches) == 1:
            return branches[0]
        if empty_branch:
            # mawk and onetrue-awk reject ``a|``, ``|a``, ``(a|)``; gawk
            # accepts them as "or nothing".
            raise self.error("empty alternative")
        # (a|b|[cd]) is the set [abcd]: one Chars node is simpler and,
        # unlike an alternation, never needs the longest-match engine.
        if all(type(b) is Chars and not b.negated for b in branches):
            return Chars(_merge([r for b in branches for r in b.ranges]))
        return Alt(branches)

    def cat(self) -> object:
        s, items = self.s, []
        while self.i < len(s):
            c = s[self.i]
            if c == "|" or (c == ")" and self.depth > 0):
                break
            atom = self.atom()
            if atom is not None:
                items.append(self.postfix(atom))
        if not items:
            return EMPTY
        return items[0] if len(items) == 1 else Cat(items)

    def atom(self) -> object | None:
        s = self.s
        c = s[self.i]
        if c == "(":
            self.i += 1
            self.depth += 1
            node = EMPTY if self.i < len(s) and s[self.i] == ")" else self.alt()
            if self.i >= len(s) or s[self.i] != ")":
                raise self.error("unmatched (")
            self.i += 1
            self.depth -= 1
            return node
        if c in "*+?" or (c == "{" and self.interval() is not None):
            raise self.error(f"{c} with nothing to repeat")
        if c == "^":
            # A repeat operator right after ``^`` repeats the anchor
            # (onetrue-awk and gawk; mawk rejects it): ``^+`` is just
            # ``^``, while ``^*`` and ``^?`` make it optional, so they vanish.
            self.i += 1
            ops = ""
            while self.i < len(s) and s[self.i] in "*+?":
                ops += s[self.i]
                self.i += 1
            return BOL if ops.strip("+") == "" else None
        self.i += 1
        if c == "$":
            return EOL
        if c == ".":
            return ANY_UTF8 if charset.UTF8 else ANY
        if c == "[":
            return self.bracket()
        if c == "\\":
            self.i -= 1
            return _literal(self.escape())
        return _literal(c)  # includes a literal ')', '{', ']' or '}'

    def postfix(self, node: object) -> object:
        s = self.s
        while self.i < len(s):
            c = s[self.i]
            if c in "*+?":
                self.i += 1
                lo, hi = {"*": (0, None), "+": (1, None), "?": (0, 1)}[c]
            elif c == "{":
                iv = self.interval()
                if iv is None:
                    break  # a literal '{', the start of the next atom
                lo, hi, self.i = iv
            else:
                break
            node = _repeat(node, lo, hi)
        return node

    def interval(self) -> tuple[int, int | None, int] | None:
        """Parse ``{n}``, ``{n,}`` or ``{n,m}`` at self.i: (n, m, next index).

        None if the ``{`` is a literal (no digit follows it, as in ``a{``,
        ``{x}`` or ``{,3}``).  Malformed intervals are errors.
        """
        s, j = self.s, self.i + 1
        k = j
        while k < len(s) and s[k] in "0123456789":
            k += 1
        if k == j:
            return None
        lo = int(s[j:k])
        hi: int | None = lo
        if k < len(s) and s[k] == ",":
            m = k + 1
            k = m
            while k < len(s) and s[k] in "0123456789":
                k += 1
            hi = int(s[m:k]) if k > m else None
        if k >= len(s) or s[k] != "}":
            raise self.error("unterminated interval {")
        if hi is not None and hi < lo:
            raise self.error("interval {n,m} with m < n")
        if max(lo, hi or 0) > RE_DUP_MAX:
            raise self.error(f"repetition count above {RE_DUP_MAX}")
        return lo, hi, k + 1

    def escape(self) -> str:
        """The character meant by the escape at self.i (s[self.i] == '\\')."""
        s, i = self.s, self.i
        if i + 1 >= len(s):
            self.i = i + 1
            return "\\"  # a trailing backslash is itself
        d = s[i + 1]
        if d in _ESCAPES:
            self.i = i + 2
            return _ESCAPES[d]
        number = self.byte_escape(i)
        if number is not None:
            b, self.i = number
            ch = charset.byte_char(b)
            if charset.UTF8 and ch >= "\udcc0":
                ch = self.utf8_sequence(b & 0xFF, ch)
            return ch
        self.i = i + 2
        return d

    def byte_escape(self, i: int) -> tuple[int, int] | None:
        """(value, end) of an octal ``\\ddd`` or hex ``\\xhh`` escape at i, else None."""
        s = self.s
        if i + 1 >= len(s) or s[i] != "\\":
            return None
        d = s[i + 1]
        if d in _OCTAL:
            j = i + 1
            while j < len(s) and j < i + 4 and s[j] in _OCTAL:
                j += 1
            return int(s[i + 1 : j], 8), j
        if d == "x" and i + 2 < len(s) and s[i + 2] in _HEX:
            j = i + 2
            while j < len(s) and j < i + 4 and s[j] in _HEX:
                j += 1
            return int(s[i + 2 : j], 16), j
        return None

    def utf8_sequence(self, lead: int, lone: str) -> str:
        """UTF-8 mode: after an escape for the lead byte ``lead`` (0xC0 or
        more), read the escapes for its continuation bytes and return the
        character they spell, so ``/\\303\\251+/`` means ``/é+/`` (gawk).
        If they do not spell a valid character, return ``lone`` (the lead
        byte on its own) and read nothing more."""
        # The lead byte says how many continuation bytes follow.
        need = 1 if lead < 0xE0 else 2 if lead < 0xF0 else 3
        raw, j = [lead], self.i
        for _ in range(need):
            number = self.byte_escape(j)
            if number is None or not 0x80 <= number[0] & 0xFF <= 0xBF:
                return lone
            raw.append(number[0] & 0xFF)
            j = number[1]
        try:
            ch = bytes(raw).decode("utf-8")
        except UnicodeDecodeError:  # e.g. an overlong form such as \300\200
            return lone
        self.i = j
        return ch

    def bracket(self) -> Chars:
        """Parse a bracket expression; self.i is just past the '['."""
        s = self.s
        negated = self.i < len(s) and s[self.i] == "^"
        if negated:
            self.i += 1
        ranges: list[tuple[int, int]] = []
        if negated and charset.UTF8:
            ranges.append(_ESCAPED_BYTES)  # [^a] does not match an invalid byte either
        first = True
        while True:
            if self.i >= len(s):
                raise self.error("unterminated [")
            if s[self.i] == "]" and not first:
                self.i += 1
                break
            first = False  # a ']' first in the list is a member
            lo = self.bracket_element()
            if isinstance(lo, list):  # a [:class:]
                ranges += lo
                continue
            if self.i + 1 < len(s) and s[self.i] == "-" and s[self.i + 1] != "]":
                self.i += 1
                hi = self.bracket_element()
                if isinstance(hi, list):
                    raise self.error("character class as range end")
                if ord(hi) < ord(lo):
                    raise self.error(f"invalid range {lo}-{hi}")
                ranges.append((ord(lo), ord(hi)))
            else:
                ranges.append((ord(lo), ord(lo)))
        return Chars(_merge(ranges), negated)

    def bracket_element(self) -> str | list[tuple[int, int]]:
        """One bracket member: a character, or a class's ranges."""
        s, i = self.s, self.i
        c = s[i]
        if c == "[" and i + 1 < len(s) and s[i + 1] in ":.=":
            kind = s[i + 1]
            end = s.find(kind + "]", i + 2)
            if end == -1:
                raise self.error(f"unterminated [{kind}")
            name = s[i + 2 : end]
            self.i = end + 2
            if kind == ":":
                if name not in _CLASSES:
                    raise self.error(f"unknown character class [:{name}:]")
                if charset.UTF8:
                    from .unicode_tables import CLASSES  # loaded on first use

                    return list(CLASSES[name])
                return _CLASSES[name]
            # [.x.] (collating symbol) and [=x=] (equivalence class): in
            # the C locale every collating element is a single character
            # and is equivalent only to itself.
            if len(name) != 1:
                raise self.error(f"invalid collating element [{kind}{name}{kind}]")
            return name
        if c == "\\":
            return self.escape()
        self.i = i + 1
        return c


# Limits that keep a regex (which may come from the input) from using
# unbounded memory.  The automaton writes out every copy an interval asks
# for, so ``a{1000}`` is 1000 states and nested intervals multiply.
#
# RE_DUP_MAX: the largest count allowed in ``{n,m}``.  POSIX lets each
# implementation choose any value of at least 255; onetrue-awk uses 255,
# gawk 32767, mawk has no limit.  pyawk follows gawk (the majority accepts
# ``a{0,1000}`` and rejects ``a{0,100000}``).
RE_DUP_MAX = 32767
# NFA_LIMIT: the most automaton states a pattern may need.  A million
# states take roughly 100 MB.  Only nested intervals get near it, e.g.
# ``((a{0,200}){0,200}){0,200}`` (8 million); gawk and mawk accept those,
# given the memory, but here they are refused as "too big".
NFA_LIMIT = 1_000_000


def _nfa_size(node: object) -> int:
    """How many states _NFA.build makes for ``node`` (counted, not built).

    Mirrors build() case by case.  The counts are exact Python integers,
    so even absurd nestings are measured without building anything.
    """
    t = type(node)
    if t is Chars:
        return 1
    if t is Cat:
        return sum(_nfa_size(x) for x in node.items)  # type: ignore[attr-defined]
    if t is Alt:
        branches = node.branches  # type: ignore[attr-defined]
        return sum(_nfa_size(b) for b in branches) + len(branches) - 1  # one SPLIT per extra branch
    if t is Repeat:
        x, lo, hi = _nfa_size(node.node), node.min, node.max  # type: ignore[attr-defined]
        optional = 1 + x if hi is None else (hi - lo) * (1 + x)  # loop, or one SPLIT + copy each
        return optional + lo * x
    if node is BOL or node is EOL:
        return 1
    return 0  # EMPTY


def _repeat(node: object, lo: int, hi: int | None) -> object:
    """Repeat(node, lo, hi), simplified where an anchor or nothing is repeated."""
    if node is EMPTY:
        return EMPTY
    if node is BOL or node is EOL:
        # An anchor matches no text, so repeating it once is the same as
        # once; allowing zero times makes it always true.
        return node if lo > 0 else EMPTY
    return Repeat(node, lo, hi)


# ------------------------------------------------- Python pattern text
def _char_text(o: int) -> str:
    """One code point for a Python pattern, escaped unless plainly safe."""
    ch = chr(o)
    if ch.isascii() and ch.isalnum():
        return ch
    if o < 0x100:
        return f"\\x{o:02x}"
    return f"\\u{o:04x}" if o < 0x10000 else f"\\U{o:08x}"


def _ranges_text(ranges: Ranges) -> str:
    """The inside of a Python ``[...]`` for these ranges."""
    return "".join(
        _char_text(lo) if lo == hi else _char_text(lo) + ("" if hi == lo + 1 else "-") + _char_text(hi)
        for lo, hi in ranges
    )


# Python's regex compiler stores the part of a character set below U+10000
# as a table (one lookup per character) but the part above it as a list of
# ranges that is checked one by one.  So with a Unicode class, which has
# hundreds of ranges above U+10000 (CJK extensions, historic scripts,
# emoji), every ordinary character that is *not* in the set went through
# that whole list: gsub(/[[:alpha:]]x/, ...) ran about four times slower
# in UTF-8 mode than in the C locale.  Such sets are therefore written as
#     (?:[the part below U+10000]|(?=[\U00010000-\U0010ffff])[the rest])
# where the look-ahead ``(?=...)`` is a single range test that lets
# ordinary characters skip the long list.  The meaning is unchanged: still
# exactly one character from the same set.  Small sets (and everything in
# the C locale, where all characters are below U+0100) keep the plain form.
_ASTRAL = 0x10000
_SPLIT_ABOVE = 16  # ranges above U+FFFF before a set is split


def _complement(ranges: Ranges) -> Ranges:
    """All code points (0 to U+10FFFF) that are not in ``ranges``."""
    out: list[tuple[int, int]] = []
    nxt = 0  # the first code point not yet accounted for
    for lo, hi in sorted(ranges):
        if lo > nxt:
            out.append((nxt, lo - 1))
        nxt = max(nxt, hi + 1)
    if nxt <= 0x10FFFF:
        out.append((nxt, 0x10FFFF))
    return tuple(out)


def _big_set_text(ranges: Ranges, negated: bool) -> str | None:
    """The split form described above, or None if the plain form will do."""
    if sum(1 for _, hi in ranges if hi >= _ASTRAL) <= _SPLIT_ABOVE:
        return None
    if negated:  # [^...] is written as the set of everything else
        ranges = _complement(ranges)
    low = [(lo, min(hi, _ASTRAL - 1)) for lo, hi in ranges if lo < _ASTRAL]
    high = [(max(lo, _ASTRAL), hi) for lo, hi in ranges if hi >= _ASTRAL]
    look = "(?=[" + _char_text(_ASTRAL) + "-" + _char_text(0x10FFFF) + "])"
    if not low and not high:  # [^...] of every character: matches nothing
        return "[^\\x00-\\U0010ffff]"
    if not low:  # grouped, so that a following * or + covers both parts
        return "(?:" + look + "[" + _ranges_text(high) + "])"
    if not high:
        return "[" + _ranges_text(low) + "]"
    return "(?:[" + _ranges_text(low) + "]|" + look + "[" + _ranges_text(high) + "])"


def _emit(node: object) -> str:
    """The Python ``re`` text for a syntax tree."""
    t = type(node)
    if t is Chars:
        big = _big_set_text(node.ranges, node.negated)  # type: ignore[attr-defined]
        if big is not None:
            return big
        if not node.ranges:  # type: ignore[attr-defined]
            return "." if node.negated else "[^\\x00-\\U0010ffff]"  # type: ignore[attr-defined]
        if not node.negated and len(node.ranges) == 1 and node.ranges[0][0] == node.ranges[0][1]:  # type: ignore[attr-defined]
            return _char_text(node.ranges[0][0])  # type: ignore[attr-defined]
        body = _ranges_text(node.ranges)  # type: ignore[attr-defined]
        return "[" + ("^" if node.negated else "") + body + "]"  # type: ignore[attr-defined]
    if t is Cat:
        return "".join("(?:" + _emit(x) + ")" if type(x) is Alt else _emit(x) for x in node.items)  # type: ignore[attr-defined]
    if t is Alt:
        return "|".join(_emit(b) for b in node.branches)  # type: ignore[attr-defined]
    if t is Repeat:
        inner = node.node  # type: ignore[attr-defined]
        text = _emit(inner) if type(inner) is Chars else "(?:" + _emit(inner) + ")"
        lo, hi = node.min, node.max  # type: ignore[attr-defined]
        # Each repeat gets its own (?:...) group above, so Python never
        # reads two operators in a row as its lazy ``+?`` or possessive ``++``.
        if hi is None:
            op = "*" if lo == 0 else "+" if lo == 1 else f"{{{lo},}}"
        else:
            op = "?" if (lo, hi) == (0, 1) else f"{{{lo}}}" if lo == hi else f"{{{lo},{hi}}}"
        return text + op
    if node is BOL:
        return "^"
    if node is EOL:
        return r"\Z"
    return ""  # EMPTY


# -------------------------------------------------------- safety check
# When does Python's first-found match have the POSIX (longest) length?
#
# * When every match of the pattern has the same length ("fixed length",
#   e.g. ``cat|dog``, ``[0-9]{3}``): whichever match Python finds is as
#   long as any other.
# * For a concatenation X Y with X of fixed length: every way of matching
#   X ends at the same place, so Y always starts at the same position and
#   Python's result is |X| plus whatever it finds for Y.  So X Y is safe
#   when Y is.
# * A run of single-character items, each optionally repeated (``a*b+c?``,
#   ``[0-9]+\.[0-9]*``, ``x.*y``), plus anchors.  Greedy repetition of a
#   single character set gives the longest overall match here; this was
#   checked exhaustively for all such patterns of up to four items over a
#   small alphabet, against the automaton, on every string up to length 6
#   (scripts/regex_fuzz.py --verify-safety repeats that check).
# Anything else (an alternation of different lengths, a repeated group
# whose matches can differ in length) goes to the automaton.
def _fixed_len(node: object) -> int | None:
    t = type(node)
    if t is Chars:
        return 1
    if t is Cat:
        total = 0
        for x in node.items:  # type: ignore[attr-defined]
            n = _fixed_len(x)
            if n is None:
                return None
            total += n
        return total
    if t is Alt:
        lengths = {_fixed_len(b) for b in node.branches}  # type: ignore[attr-defined]
        return lengths.pop() if len(lengths) == 1 else None
    if t is Repeat:
        n = _fixed_len(node.node)  # type: ignore[attr-defined]
        if n is not None and node.min == node.max:  # type: ignore[attr-defined]
            return n * node.min  # type: ignore[attr-defined]
        return 0 if n == 0 else None
    return 0  # anchors, EMPTY


def _simple_item(node: object) -> bool:
    return type(node) is Chars or node is BOL or node is EOL or node is EMPTY or (
        type(node) is Repeat and type(node.node) is Chars  # type: ignore[attr-defined]
    )


def _safe(node: object) -> bool:
    """True if Python's match is always the longest one (see above)."""
    if _fixed_len(node) is not None:
        return True
    items = node.items if type(node) is Cat else [node]  # type: ignore[attr-defined]
    for k, x in enumerate(items):
        if _fixed_len(x) is None:
            return all(_simple_item(y) for y in items[k:])
    return True


# ------------------------------------------------ backtracking check
# Python's matcher explores the ways a pattern can match one at a time, and
# on a failing text it may try all of them.  For ``(a*)*b`` on "aaaa..."
# that is every way of cutting the a's into groups: 2**n tries.  The
# automata never do that (they follow all ways at once), but they run in
# Python, not C, so we keep Python's matcher for patterns where it is
# provably cheap, and send everything else to the automata.
#
# The patterns kept are a "run" (a concatenation, or a single item) whose
# items are each one of:
#
#   * one character set (``a``, ``.``, ``[0-9]``), an anchor, or ``()``;
#   * a character set repeated (``a*``, ``[0-9]+``, ``x?``, ``a{2,5}``);
#   * an alternation of plain strings (``(foo|bar)``), with the product of
#     the alternation sizes in the run at most _MAX_CHOICES;
#
# or an alternation of such runs.  Within a run, a variable-length item
# X (a repeat whose min and max differ) is *forced* when no character X
# can take could also be taken by what follows it, i.e. by the first
# character of the following items up to and including the first one that
# must match something.  ``[0-9]+\.`` has a forced ``[0-9]+``: when Python
# gives back a digit, ``\.`` fails on it at once, so retrying costs O(1).
# Otherwise X is *ambiguous* (``.*`` in ``a.*b``): each of its O(n)
# lengths may be retried with the rest of the pattern.  That is still cheap
# if the rest is cheap, so we allow ONE ambiguous item, provided every
# variable item after it is forced and takes no character the ambiguous one
# can take (so the later items cannot re-scan the same text for each try).
# A second ambiguous item multiplies the tries (``.*x.*y`` is cubic).
#
# Even so, Python tries each start position, and each try may scan to the
# end of the text: O(n**2) per search on a failing text (0.3 s for
# ``[0-9]+5\.`` on 10 000 fives).  So for texts longer than LONG_TEXT,
# patterns with any variable item switch to the automata (``for_long``).
_MAX_CHOICES = 64


def _overlap(a: Chars, b: Chars) -> bool:
    """True if some character is in both sets."""
    if a.negated and b.negated:
        return True  # each excludes finitely many ranges: plenty is left in both
    if a.negated:
        a, b = b, a
    if not b.negated:
        return any(lo1 <= hi2 and lo2 <= hi1 for lo1, hi1 in a.ranges for lo2, hi2 in b.ranges)
    # a lists characters, b is "anything but ...": they overlap unless each
    # of a's ranges lies inside one of b's (merged, so non-adjacent) ranges.
    return not all(any(lo2 <= lo1 and hi1 <= hi2 for lo2, hi2 in b.ranges) for lo1, hi1 in a.ranges)


class _Item:
    """What the backtracking check needs to know about one item of a run."""

    __slots__ = ("first", "variable", "mandatory", "choices", "chars")

    def __init__(self, first: list[Chars], variable: bool, mandatory: bool, choices: int, chars: Chars | None) -> None:
        self.first = first  # the sets its first character can come from
        self.variable = variable  # it can match texts of different lengths
        self.mandatory = mandatory  # it always consumes something (or is $)
        self.choices = choices  # how many alternatives Python may try
        self.chars = chars  # for a repeat: the repeated set


def _plain_string(node: object) -> bool:
    return type(node) is Chars or (type(node) is Cat and all(type(x) is Chars for x in node.items))  # type: ignore[attr-defined]


def _item(node: object) -> _Item | None:
    """Classify one item of a run (None: not allowed in a run)."""
    t = type(node)
    if t is Chars:
        return _Item([node], False, True, 1, None)  # type: ignore[list-item]
    if node is BOL or node is EMPTY:
        return _Item([], False, False, 1, None)  # matches no text: transparent
    if node is EOL:
        return _Item([], False, True, 1, None)  # nothing can follow it
    if t is Repeat and type(node.node) is Chars:  # type: ignore[attr-defined]
        lo, hi = node.min, node.max  # type: ignore[attr-defined]
        if hi == 0:
            return _Item([], False, False, 1, None)
        return _Item([node.node], lo != hi, lo > 0, 1, node.node)  # type: ignore[attr-defined]
    if t is Alt and all(_plain_string(b) for b in node.branches):  # type: ignore[attr-defined]
        first = [b if type(b) is Chars else b.items[0] for b in node.branches]  # type: ignore[attr-defined]
        return _Item(first, False, True, len(first), None)
    return None


def _run_safe(node: object) -> bool:
    items = [_item(x) for x in (node.items if type(node) is Cat else [node])]  # type: ignore[attr-defined]
    if any(it is None for it in items):
        return False
    choices = 1
    for it in items:
        choices *= it.choices  # type: ignore[union-attr]
    if choices > _MAX_CHOICES:
        return False
    ambiguous: Chars | None = None  # the set of the ambiguous item, once seen
    for k, it in enumerate(items):
        if not it.variable:  # type: ignore[union-attr]
            continue
        follow: list[Chars] = []
        for later in items[k + 1 :]:
            follow += later.first  # type: ignore[union-attr]
            if later.mandatory:  # type: ignore[union-attr]
                break
        forced = not any(_overlap(it.chars, f) for f in follow)  # type: ignore[union-attr, arg-type]
        if ambiguous is not None and (not forced or _overlap(ambiguous, it.chars)):  # type: ignore[union-attr, arg-type]
            return False
        if not forced:
            ambiguous = it.chars  # type: ignore[union-attr]
    return True


def _backtrack_safe(node: object) -> bool:
    """True if Python's matcher needs at most O(n) work per start position
    (and O(n**2) per search) on any text of length n (see above)."""
    return all(_run_safe(b) for b in (node.branches if type(node) is Alt else [node]))  # type: ignore[attr-defined]


def _has_variable(node: object) -> bool:
    """True if the tree contains a repeat whose min and max differ."""
    t = type(node)
    if t is Repeat:
        return node.min != node.max or _has_variable(node.node)  # type: ignore[attr-defined]
    if t is Cat:
        return any(_has_variable(x) for x in node.items)  # type: ignore[attr-defined]
    if t is Alt:
        return any(_has_variable(b) for b in node.branches)  # type: ignore[attr-defined]
    return False


def _reverse(node: object) -> object:
    """The tree matching the reversed texts: ``ab*$`` becomes ``^b*a``."""
    t = type(node)
    if t is Cat:
        return Cat([_reverse(x) for x in reversed(node.items)])  # type: ignore[attr-defined]
    if t is Alt:
        return Alt([_reverse(b) for b in node.branches])  # type: ignore[attr-defined]
    if t is Repeat:
        return Repeat(_reverse(node.node), node.min, node.max)  # type: ignore[attr-defined]
    if node is BOL:
        return EOL
    if node is EOL:
        return BOL
    return node  # a character set, or EMPTY


# ------------------------------------------------------------- the NFA
# States are numbered; for state s, _kind[s] says what it does:
_CHAR, _SPLIT, _EPS, _BOL, _EOL, _MATCH = range(6)
#   _CHAR:  consume one character in sets[s], go to out1[s]
#   _SPLIT: free move to out1[s] and to out2[s]
#   _EPS:   free move to out1[s]
#   _BOL / _EOL: free move to out1[s], allowed only at the start / end
#   _MATCH: the pattern has matched


class _NFA:
    def __init__(self, tree: object) -> None:
        self.kind: list[int] = []
        self.sets: list[Chars | None] = []
        self.out1: list[int] = []
        self.out2: list[int] = []
        final = self.new(_MATCH)
        self.start = self.build(tree, final)

    def new(self, kind: int, chars: Chars | None = None, out1: int = -1, out2: int = -1) -> int:
        self.kind.append(kind)
        self.sets.append(chars)
        self.out1.append(out1)
        self.out2.append(out2)
        return len(self.kind) - 1

    def build(self, node: object, nxt: int) -> int:
        """Add states for ``node`` that continue to state ``nxt``; return the entry state.

        Building back to front lets each piece be wired straight to its
        successor, with no patching of dangling edges afterwards.
        """
        t = type(node)
        if t is Chars:
            return self.new(_CHAR, node, nxt)  # type: ignore[arg-type]
        if t is Cat:
            for x in reversed(node.items):  # type: ignore[attr-defined]
                nxt = self.build(x, nxt)
            return nxt
        if t is Alt:
            entries = [self.build(b, nxt) for b in node.branches]  # type: ignore[attr-defined]
            s = entries[-1]
            for e in reversed(entries[:-1]):
                s = self.new(_SPLIT, None, e, s)
            return s
        if t is Repeat:
            x, lo, hi = node.node, node.min, node.max  # type: ignore[attr-defined]
            if hi is None:
                # x*: a loop state that either enters x (which comes back
                # to the loop) or leaves.
                loop = self.new(_SPLIT, None, -1, nxt)
                self.out1[loop] = self.build(x, loop)
                tail = loop
            else:
                # x{0,k} as nested optionals: (x(x(x)?)?)?
                tail = nxt
                for _ in range(hi - lo):
                    split = self.new(_SPLIT, None, -1, nxt)
                    self.out1[split] = self.build(x, tail)
                    tail = split
            for _ in range(lo):  # then lo mandatory copies in front
                tail = self.build(x, tail)
            return tail
        if node is BOL:
            return self.new(_BOL, None, nxt)
        if node is EOL:
            return self.new(_EOL, None, nxt)
        return nxt  # EMPTY


class _DState:
    """A DFA state: a set of NFA states we could be in, with its transitions."""

    __slots__ = ("chars", "accept", "accept_end", "next")

    def __init__(self, chars: tuple[int, ...], accept: bool, accept_end: bool) -> None:
        self.chars = chars  # the _CHAR states in the set
        self.accept = accept  # the set contains the final state
        self.accept_end = accept_end  # ... if this is the end of the text
        self.next: dict[str, _DState | None] = {}  # filled in lazily


# Cached DFA states per automaton.  When a pattern needs more (an automaton
# can in principle have 2**(NFA size) states, e.g. ``(a|b)*a(a|b){20}``), the
# cache is emptied (see _DFA._forget) and states are rebuilt as the text
# needs them.  That bounds memory, and the time stays linear: each input character builds at
# most one new state, which costs time proportional to the pattern's size.
_DFA_LIMIT = 10000
_UNSEEN = object()  # marker: "no transition cached for this character"


class _DFA:
    """A lazily built DFA over one NFA (see the module docstring).

    ``unanchored=True`` puts the NFA's start state back into the set after
    every character, so a match may begin anywhere: the DFA then answers
    "does a match end here?" for every position in a single pass.  Such a
    DFA never gets stuck (its set always holds the start state).  With
    ``unanchored=False`` the match must begin where the scan begins, and
    ``step`` returns None once no match can continue.
    """

    def __init__(self, nfa: _NFA, unanchored: bool) -> None:
        self.nfa = nfa
        self.inject = frozenset((nfa.start,)) if unanchored else frozenset()
        self.cache: dict[tuple[frozenset[int], bool], _DState] = {}
        self.initial_states: dict[bool | None, _DState] = {}
        self.skip = self._skipper() if unanchored else None

    def _skipper(self) -> re.Pattern[str] | None:
        """A Python regex matching any character that can begin a match.

        An unanchored scan spends most of its time "idle": in the state that
        holds just the start state's closure, waiting for a character that
        can begin a match.  Characters that cannot leave it idle can be
        skipped with this regex, which searches in C.  None when skipping
        would not help (a set like ``.`` or ``[^x]``, or a pattern that
        matches the empty string, which is never idle).
        """
        chars, accept = self.closure(frozenset((self.nfa.start,)), False, False)
        sets = [self.nfa.sets[c] for c in chars]
        if accept or not sets or any(c.negated for c in sets):  # type: ignore[union-attr]
            return None
        return re.compile("|".join(_emit(c) for c in sets), re.DOTALL)

    def idle(self) -> _DState:
        """The state of an unanchored scan between possible matches (not at the start)."""
        d = self.initial_states.get(None)
        if d is None:
            d = self.initial_states[None] = self.state(frozenset((self.nfa.start,)), False)
        return d

    def initial(self, at_start: bool) -> _DState:
        """The state before reading anything; ``at_start``: at the text's start, so ``^`` holds."""
        d = self.initial_states.get(at_start)
        if d is None:
            d = self.initial_states[at_start] = self.state(frozenset((self.nfa.start,)), at_start)
        return d

    def state(self, kernel: frozenset[int], at_start: bool) -> _DState:
        """The DFA state for NFA states ``kernel`` plus everything reachable
        from them by free moves (the "epsilon closure")."""
        key = (kernel, at_start)
        d = self.cache.get(key)
        if d is None:
            if len(self.cache) >= _DFA_LIMIT:
                self._forget()
            chars, accept = self.closure(kernel, at_start, False)
            accept_end = accept or self.closure(kernel, at_start, True)[1]
            d = self.cache[key] = _DState(chars, accept, accept_end)
        return d

    def _forget(self) -> None:
        """Empty the cache to keep memory bounded on huge automata.

        Emptying the dictionary is not enough on its own.  A scan in
        progress still holds its current state and the idle state, and
        every old state's ``next`` table links it to other states, old and
        (soon) new; through those links the whole history would stay
        reachable and nothing would ever be freed.  So the tables are
        emptied too: each state then stands alone, and a scan holding one
        simply works its transitions out again (they come out the same).
        The initial states are kept (with empty tables), so the scans'
        "is this the idle state?" tests stay valid.
        """
        keep = {id(d) for d in self.initial_states.values()}
        kept = {key: d for key, d in self.cache.items() if id(d) in keep}
        for d in self.cache.values():
            d.next.clear()
        self.cache.clear()
        self.cache.update(kept)  # the same dict: LongestPattern.cache refers to it

    def closure(self, kernel: frozenset[int], at_start: bool, at_end: bool) -> tuple[tuple[int, ...], bool]:
        nfa = self.nfa
        kind, out1, out2 = nfa.kind, nfa.out1, nfa.out2
        seen: set[int] = set()
        stack = list(kernel)
        chars: list[int] = []
        accept = False
        while stack:
            s = stack.pop()
            if s in seen:
                continue
            seen.add(s)
            k = kind[s]
            if k == _CHAR:
                chars.append(s)
            elif k == _SPLIT:
                stack.append(out1[s])
                stack.append(out2[s])
            elif k == _MATCH:
                accept = True
            elif k == _EPS or (k == _BOL and at_start) or (k == _EOL and at_end):
                stack.append(out1[s])
        return tuple(sorted(chars)), accept

    def scan(self, s: str, marks: bytearray | None) -> bool:
        """Run this (unanchored) DFA over ``s``, from position 0.

        With ``marks`` None: stop and return True at the first position where
        a match ends (False if there is none).  Otherwise record every such
        position p as ``marks[len(s) - p] = 1`` and return False; for the
        reversed DFA, scanning the reversed text, len(s) - p is the position
        in the original text where a match starts (see LongestPattern.starts).
        """
        n = len(s)
        d = self.initial(True)
        step = self.step
        skip, idle = self.skip, self.idle()
        i = 0
        while i < n:
            if d.accept:
                if marks is None:
                    return True
                marks[n - i] = 1
            if d is idle and skip is not None:
                # Nothing can happen until a character that may begin a
                # match: let Python's C code find the next one.
                m = skip.search(s, i)
                if m is None:
                    break  # still idle at the end, as if we had read the rest
                i = m.start()
            ch = s[i]
            nxt = d.next.get(ch)  # an unanchored DFA never gets stuck,
            d = nxt if nxt is not None else step(d, ch)  # type: ignore[assignment]  # so None means "not cached"
            i += 1
        if d.accept_end:  # at the end of the text (``$`` holds)
            if marks is None:
                return True
            marks[0] = 1
        return False

    def step(self, d: _DState, ch: str) -> _DState | None:
        """The state after reading ``ch`` in state ``d`` (None: no match possible)."""
        sets, out1 = self.nfa.sets, self.nfa.out1
        kernel = frozenset(out1[s] for s in d.chars if ch in sets[s]) | self.inject  # type: ignore[operator]
        nxt = self.state(kernel, False) if kernel else None
        d.next[ch] = nxt
        return nxt


class LongestPattern:
    """A compiled ERE that reports POSIX leftmost-longest matches.

    Offers the part of ``re.Pattern``'s interface that pyawk uses:
    ``search(s, pos)`` and ``finditer(s)``, returning ``Span`` objects, plus
    ``found(s)`` for a plain yes/no answer.

    ``py`` is the Python form of the pattern when Python may be trusted to
    find where matches *start* (the pattern is ``_backtrack_safe``), or
    None.  Without it every answer comes from automata, each in time
    proportional to the text length:

    * ``found``: one left-to-right pass of an unanchored DFA, stopping at
      the first position where some match ends.
    * ``search``: one right-to-left pass, over the *reversed* pattern
      (``_reverse``), marks every position where a match starts; the
      leftmost mark at or after ``pos`` is the POSIX start.  The marks are
      kept for the last string seen, so the repeated searches of gsub,
      split and FS (each continuing from the previous match) share one pass.
      An anchored DFA then finds the longest match from that start.
    """

    def __init__(self, tree: object, py: re.Pattern[str] | None) -> None:
        self.tree = tree
        self.py = py
        self.pattern = py.pattern if py is not None else _emit(tree)
        self.flags = re.DOTALL
        self.nfa = _NFA(tree)
        self.fwd = _DFA(self.nfa, unanchored=False)  # longest match from a given start
        self.cache = self.fwd.cache  # (kept for tests and debugging)
        self._scan: _DFA | None = None  # unanchored, for found(); built on first use
        self._rev: _DFA | None = None  # reversed pattern, for match starts
        self._twin: LongestPattern | None = None
        self._marked: str | None = None  # the string self._marks belongs to
        self._marks = bytearray()
        self._probed: str | None = None  # the string self._budget belongs to
        self._budget = 0

    def dfas(self) -> list[_DFA]:
        """The automata built so far (for tests)."""
        return [d for d in (self.fwd, self._scan, self._rev) if d is not None]

    def longest(self, s: str, start: int) -> int:
        """End of the longest match starting at ``start`` (-1 if none)."""
        dfa = self.fwd
        d: _DState | None = dfa.initial(start == 0)
        last = -1
        n = len(s)
        i = start
        step = dfa.step
        while True:
            if i == n:
                return i if d.accept_end else last  # type: ignore[union-attr]
            if d.accept:  # type: ignore[union-attr]
                last = i
            ch = s[i]
            nxt = d.next.get(ch, _UNSEEN)  # type: ignore[union-attr]
            if nxt is _UNSEEN:  # this transition has not been computed yet
                nxt = step(d, ch)  # type: ignore[arg-type]
            if nxt is None:
                return last
            d = nxt  # type: ignore[assignment]
            i += 1

    def found(self, s: str) -> bool | None:
        """True if the pattern matches somewhere in ``s``, else None
        (None rather than False, like ``re.search``, so callers can test
        ``is not None`` either way)."""
        if self.py is not None:
            return True if self.py.search(s) is not None else None
        dfa = self._scan
        if dfa is None:
            dfa = self._scan = _DFA(self.nfa, unanchored=True)
        return True if dfa.scan(s, None) else None

    def starts(self, s: str) -> bytearray:
        """marks[i] == 1 where some match of the pattern starts at position i.

        A match starting at i ends somewhere at or after i, so scanning the
        text backwards with the reversed pattern, unanchored (it may "start"
        at any end position), the positions where that scan is in an
        accepting state are exactly the match starts.  The reversed
        pattern's ``^`` stands for the original ``$`` (it holds only at the
        beginning of the backwards scan, the end of the text) and its ``$``
        for the original ``^`` (only at position 0).
        """
        if s is self._marked:
            return self._marks
        dfa = self._rev
        if dfa is None:
            dfa = self._rev = _DFA(_NFA(_reverse(self.tree)), unanchored=True)
        marks = bytearray(len(s) + 1)
        dfa.scan(s[::-1], marks)  # ``s[::-1]``: s reversed (a C-speed copy)
        self._marked, self._marks = s, marks
        return marks

    def search(self, s: str, pos: int = 0) -> Span | None:
        """The leftmost-longest match at or after ``pos``, or None."""
        if self.py is not None:
            m = self.py.search(s, pos)  # Python finds the correct start
            if m is None:
                return None
            start = m.start()
        elif s is not self._marked:
            span = self._probe(s, pos)
            if span is not False:
                return span
        # A match starting at i never looks at text before i (EREs have no
        # look-behind; ``^`` only checks i == 0), so the marks do not depend
        # on pos.  ``bytearray.find`` scans in C.
        start = start if self.py is not None else self.starts(s).find(1, pos)
        if start < 0:
            return None
        return Span(s, start, self.longest(s, start))

    def _probe(self, s: str, pos: int) -> Span | None | Literal[False]:
        """Try to find the leftmost match the quick way, on a work budget.

        Usually only a few positions can begin a match (those holding a
        character from the pattern's first set, which a Python regex finds
        in C), and trying them one by one, left to right, with the anchored
        DFA, quickly finds the leftmost one.  But each failed try may read
        far ahead, so this is quadratic in the worst case: the tries are
        therefore given a budget of characters, shared by all searches in
        the same string (gsub, split and FS search it again and again) and
        proportional to its length.  Returns the match, None if there is
        none, or False once the budget runs out: the caller then marks all
        the match starts in one linear pass (``starts``) instead.
        """
        n = len(s)
        if s is not self._probed:
            self._probed, self._budget = s, 2 * n + 64
        budget = self._budget
        if budget <= 0:
            return False
        dfa = self._scan
        if dfa is None:
            dfa = self._scan = _DFA(self.nfa, unanchored=True)
        skip = dfa.skip
        fwd = self.fwd
        step = fwd.step
        i = pos
        while True:
            if i == 0 or skip is None or i == n:
                c = i  # try every position (``^`` changes the first set at 0)
            else:
                m = skip.search(s, i)
                c = m.start() if m is not None else n  # n: an empty match at the end (``$``)?
            # The anchored scan of ``longest``, counting what it reads.
            d: _DState | None = fwd.initial(c == 0)
            last = -1
            j = c
            stop = min(n, c + budget)
            while True:
                if j >= stop:
                    if j == n:
                        if d.accept_end:  # type: ignore[union-attr]
                            last = j
                        break
                    self._budget = 0  # out of budget, and the answer is not known yet
                    return False
                if d.accept:  # type: ignore[union-attr]
                    last = j
                ch = s[j]
                nxt = d.next.get(ch, _UNSEEN)  # type: ignore[union-attr]
                if nxt is _UNSEEN:
                    nxt = step(d, ch)  # type: ignore[arg-type]
                if nxt is None:
                    break
                d = nxt  # type: ignore[assignment]
                j += 1
            budget -= j - c + 1
            if last >= 0 or c >= n:
                self._budget = budget
                return Span(s, c, last) if last >= 0 else None
            if budget <= 0:
                self._budget = 0
                return False
            i = c + 1

    def finditer(self, s: str) -> Iterator[Span]:
        """Successive non-overlapping matches, left to right."""
        pos, n = 0, len(s)
        while pos <= n:
            m = self.search(s, pos)
            if m is None:
                return
            yield m
            pos = m.end_ if m.end_ > m.start_ else m.start_ + 1

    def long_twin(self) -> LongestPattern:
        """This pattern with automata only, for long texts (see ``for_long``)."""
        if self.py is None or not _has_variable(self.tree):
            return self  # no quadratic risk
        if self._twin is None:
            self._twin = LongestPattern(self.tree, None)
        return self._twin


class Span:
    """A match found by LongestPattern, shaped like Python's ``re.Match``."""

    __slots__ = ("string", "start_", "end_")

    def __init__(self, string: str, start: int, end: int) -> None:
        self.string, self.start_, self.end_ = string, start, end

    def start(self) -> int:
        return self.start_

    def end(self) -> int:
        return self.end_

    def span(self) -> tuple[int, int]:
        return self.start_, self.end_

    def group(self, _: int = 0) -> str:
        return self.string[self.start_ : self.end_]


# -------------------------------------------------------------- entry
def parse(ere: str) -> object:
    """The syntax tree of an ERE (raises AwkRuntimeError if malformed)."""
    return _Parser(ere).parse()


def translate(ere: str) -> str:
    """The ERE rewritten as an equivalent Python regex string."""
    return _emit(parse(ere))


# Texts longer than this are searched with automata only, whatever the
# pattern (when it has a variable-length part): see "backtracking check".
LONG_TEXT = 2048

# For each Python pattern whose search can be quadratic: its syntax tree,
# replaced by the automata-only twin once one is needed.  A "weak"
# dictionary forgets an entry when its pattern is no longer used elsewhere.
_TWINS: weakref.WeakKeyDictionary[re.Pattern[str], object] = weakref.WeakKeyDictionary()

Pattern = re.Pattern[str] | LongestPattern


# ``@lru_cache`` remembers results: compiling the same regex text again (e.g.
# a pattern tested on every input line) returns the cached compiled pattern.
@lru_cache(maxsize=512)
def _compile(ere: str) -> tuple[Pattern, Pattern]:
    tree = parse(ere)
    try:
        py = re.compile(_emit(tree), re.DOTALL)
    except (re.error, RecursionError, OverflowError) as exc:
        raise AwkRuntimeError(f"bad regular expression /{ere}/: {exc}") from None
    if not _backtrack_safe(tree):
        lp = LongestPattern(tree, None)
        return lp, lp
    if _has_variable(tree):
        _TWINS[py] = tree
    return py, (py if _safe(tree) else LongestPattern(tree, py))


# The same regex text means different things in the two locale modes (what
# ``.`` or ``[[:alpha:]]`` match), so the cache is emptied when the mode changes.
charset.on_change(_compile.cache_clear)


def compile_ere(ere: str) -> Pattern:
    """Compile an AWK ERE for yes/no tests and match starts: a Python
    pattern when that is safe from backtracking blowups, else automata.

    A bad regex is a runtime error, since it may be built at run time.
    """
    return _compile(ere)[0]


def compile_span(ere: str) -> Pattern:
    """Compile an AWK ERE for uses where the match's extent matters."""
    return _compile(ere)[1]


def for_long(pat: Pattern) -> Pattern:
    """The pattern to search a text longer than LONG_TEXT with: ``pat``
    itself, or an automata-only twin when Python could take quadratic time."""
    if type(pat) is LongestPattern:
        return pat.long_twin()  # type: ignore[union-attr]
    twin = _TWINS.get(pat)  # type: ignore[arg-type]
    if twin is None:
        return pat
    if type(twin) is not LongestPattern:
        twin = _TWINS[pat] = LongestPattern(twin, None)  # type: ignore[index]
    return twin  # type: ignore[return-value]


def matcher(pat: Pattern) -> Callable[[str], object]:
    """A function answering "does ``pat`` match somewhere in this text?"
    with a non-None value for yes (for ``~`` and bare ``/re/`` patterns)."""
    if type(pat) is LongestPattern:
        return pat.found  # type: ignore[union-attr]
    search = pat.search
    if pat not in _TWINS:  # type: ignore[operator]
        return search  # never slow: use Python's matcher as it is

    def test(s: str) -> object:
        if len(s) > LONG_TEXT:
            return for_long(pat).found(s)  # type: ignore[union-attr]
        return search(s)

    return test
