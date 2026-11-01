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
  and in the C locale ``[=a=]`` and ``[.a.]`` both mean "a".  An
  unterminated bracket, an unknown class, a reversed range (``[z-a]``) or
  a multi-character collating name is an error.

The parser builds a small syntax tree (``Chars``, ``Cat``, ``Alt``,
``Repeat``, ``BOL``, ``EOL``, ``EMPTY``); the Python pattern text, the
safety check and the NFA are all produced from that tree.
"""

# Treat type hints as text, so newer syntax such as ``tuple[str, int]`` is allowed.
from __future__ import annotations

import re
from collections.abc import Iterator
from functools import lru_cache

from .errors import AwkRuntimeError

# ----------------------------------------------------------- syntax tree
# Character sets are kept as sorted, merged ranges of code points:
# [a-cx] is ((97, 99), (120, 120)).  ``negated`` flips the set ([^...]).
Ranges = tuple[tuple[int, int], ...]


class Chars:
    """Match one character from a set (a literal, ``.``, or a bracket)."""

    __slots__ = ("ranges", "negated")

    def __init__(self, ranges: Ranges, negated: bool = False) -> None:
        self.ranges = ranges
        self.negated = negated

    def __contains__(self, ch: str) -> bool:
        o = ord(ch)
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


# POSIX named classes for the C locale, as used inside brackets.
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
        return self.alt()

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
            return ANY
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
        while k < len(s) and s[k].isdigit():
            k += 1
        if k == j:
            return None
        lo = int(s[j:k])
        hi: int | None = lo
        if k < len(s) and s[k] == ",":
            m = k + 1
            k = m
            while k < len(s) and s[k].isdigit():
                k += 1
            hi = int(s[m:k]) if k > m else None
        if k >= len(s) or s[k] != "}":
            raise self.error("unterminated interval {")
        if hi is not None and hi < lo:
            raise self.error("interval {n,m} with m < n")
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
        if d in _OCTAL:
            j = i + 1
            while j < len(s) and j < i + 4 and s[j] in _OCTAL:
                j += 1
            self.i = j
            return chr(int(s[i + 1 : j], 8))
        if d == "x" and i + 2 < len(s) and s[i + 2] in _HEX:
            j = i + 2
            while j < len(s) and j < i + 4 and s[j] in _HEX:
                j += 1
            self.i = j
            return chr(int(s[i + 2 : j], 16))
        self.i = i + 2
        return d

    def bracket(self) -> Chars:
        """Parse a bracket expression; self.i is just past the '['."""
        s = self.s
        negated = self.i < len(s) and s[self.i] == "^"
        if negated:
            self.i += 1
        ranges: list[tuple[int, int]] = []
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


def _emit(node: object) -> str:
    """The Python ``re`` text for a syntax tree."""
    t = type(node)
    if t is Chars:
        if not node.ranges:  # type: ignore[attr-defined]
            return "." if node.negated else "[^\\x00-\\U0010ffff]"  # type: ignore[attr-defined]
        if not node.negated and len(node.ranges) == 1 and node.ranges[0][0] == node.ranges[0][1]:  # type: ignore[attr-defined]
            return _char_text(node.ranges[0][0])  # type: ignore[attr-defined]
        body = "".join(
            _char_text(lo) if lo == hi else _char_text(lo) + ("" if hi == lo + 1 else "-") + _char_text(hi)
            for lo, hi in node.ranges  # type: ignore[attr-defined]
        )
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


_DFA_LIMIT = 10000  # cached DFA states per pattern before starting afresh


class LongestPattern:
    """A compiled ERE that reports POSIX leftmost-longest matches.

    Offers the part of ``re.Pattern``'s interface that pyawk uses:
    ``search(s, pos)`` and ``finditer(s)``, returning ``Span`` objects.
    """

    def __init__(self, tree: object, py: re.Pattern[str]) -> None:
        self.py = py
        self.pattern = py.pattern
        self.flags = py.flags
        self.nfa = _NFA(tree)
        self.cache: dict[tuple[frozenset[int], bool], _DState] = {}
        self.start_bol = self.state(frozenset((self.nfa.start,)), True)
        self.start_mid = self.state(frozenset((self.nfa.start,)), False)

    def state(self, kernel: frozenset[int], at_start: bool) -> _DState:
        """The DFA state for NFA states ``kernel`` plus everything reachable
        from them by free moves (the "epsilon closure")."""
        key = (kernel, at_start)
        d = self.cache.get(key)
        if d is None:
            if len(self.cache) >= _DFA_LIMIT:
                self.cache.clear()  # keep memory bounded on huge automata
            chars, accept = self.closure(kernel, at_start, False)
            accept_end = accept or self.closure(kernel, at_start, True)[1]
            d = self.cache[key] = _DState(chars, accept, accept_end)
        return d

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

    def step(self, d: _DState, ch: str) -> _DState | None:
        """The state after reading ``ch`` in state ``d`` (None: no match possible)."""
        sets, out1 = self.nfa.sets, self.nfa.out1
        kernel = frozenset(out1[s] for s in d.chars if ch in sets[s])  # type: ignore[operator]
        nxt = self.state(kernel, False) if kernel else None
        d.next[ch] = nxt
        return nxt

    def longest(self, s: str, start: int) -> int:
        """End of the longest match starting at ``start`` (-1 if none)."""
        d = self.start_bol if start == 0 else self.start_mid
        last = -1
        n = len(s)
        i = start
        step = self.step
        while True:
            if i == n:
                return i if d.accept_end else last
            if d.accept:
                last = i
            ch = s[i]
            nxt = d.next.get(ch, d)  # ``d`` itself stands for "not computed yet"
            if nxt is d:
                nxt = step(d, ch)
            if nxt is None:
                return last
            d = nxt
            i += 1

    def search(self, s: str, pos: int = 0) -> Span | None:
        """The leftmost-longest match at or after ``pos``, or None."""
        m = self.py.search(s, pos)  # Python finds the correct start
        if m is None:
            return None
        start = m.start()
        return Span(s, start, self.longest(s, start))

    def finditer(self, s: str) -> Iterator[Span]:
        """Successive non-overlapping matches, left to right."""
        pos, n = 0, len(s)
        while pos <= n:
            m = self.search(s, pos)
            if m is None:
                return
            yield m
            pos = m.end_ if m.end_ > m.start_ else m.start_ + 1


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


# ``@lru_cache`` remembers results: compiling the same regex text again (e.g.
# a pattern tested on every input line) returns the cached compiled pattern.
@lru_cache(maxsize=512)
def _compile(ere: str) -> tuple[re.Pattern[str], re.Pattern[str] | LongestPattern]:
    tree = parse(ere)
    try:
        py = re.compile(_emit(tree), re.DOTALL)
    except (re.error, RecursionError, OverflowError) as exc:
        raise AwkRuntimeError(f"bad regular expression /{ere}/: {exc}") from None
    return py, (py if _safe(tree) else LongestPattern(tree, py))


def compile_ere(ere: str) -> re.Pattern[str]:
    """Compile an AWK ERE for yes/no tests (and match starts).

    A bad regex is a runtime error, since it may be built at run time.
    """
    return _compile(ere)[0]


def compile_span(ere: str) -> re.Pattern[str] | LongestPattern:
    """Compile an AWK ERE for uses where the match's extent matters."""
    return _compile(ere)[1]
