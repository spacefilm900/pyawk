"""Tokenizer (lexer) for AWK source text: the first stage after the CLI.

Pipeline: cli -> **lexer** -> parser -> AST -> runtime.  The lexer turns the
program text into a flat list of ``Token`` objects (a word, a number, an
operator, ...) that the parser then arranges into a tree.  Entry points:
``tokenize(source)`` and ``process_escapes(text)``.

Key responsibilities:

* Regex-vs-division disambiguation: a ``/`` starts an ERE literal unless the
  previous token can end an operand (name, number, string, ERE, ``)``,
  ``]``, a builtin such as ``length``, or a postfix ``++``/``--``).
* Newline handling: newlines are significant terminators, but are dropped
  after tokens that cannot end a statement (``,`` ``{`` ``&&`` ``||``
  ``do`` ``else``), and backslash-newline is a line continuation.
* String escape processing (``\\n``, ``\\t``, ``\\"``, ``\\/``, octal...).
"""

# Treat type hints as text, so newer syntax such as ``str | None`` is allowed.
from __future__ import annotations

# ``dataclass`` writes the boilerplate (constructor, equality, ...) for a
# class that is mostly a bundle of named fields; see ``Token`` below.
from dataclasses import dataclass

from .errors import AwkSyntaxError

# ``frozenset`` is an unchangeable set: fast "is this word a keyword?" tests.
# ``func`` (an old synonym for ``function`` that only onetrue-awk and plain
# gawk still accept) is deliberately *not* here: like gawk --posix and mawk,
# pyawk treats it as an ordinary name.  The parser gives a helpful error when
# a program starts a definition with it.
KEYWORDS = frozenset(
    {
        "BEGIN", "END", "function", "if", "else", "while", "for", "do",
        "break", "continue", "next", "nextfile", "exit", "return", "delete",
        "in", "getline", "print", "printf",
    }
)

BUILTINS = frozenset(
    {
        "length", "substr", "index", "split", "sub", "gsub", "match", "sprintf",
        "sin", "cos", "atan2", "exp", "log", "sqrt", "int", "rand", "srand",
        "tolower", "toupper", "system", "close", "fflush",
    }
)

# Longest operators first, so "+=" is recognised before "+" and "**=" before
# "**" (the lexer takes the first entry that matches).
_OPERATORS = (
    "**=", "+=", "-=", "*=", "/=", "%=", "^=", "==", "<=", ">=", "!=", "++",
    "--", "&&", "||", ">>", "!~", "**",
    "{", "}", "(", ")", "[", "]", ";", ",", "+", "-", "*", "/", "%", "^",
    "!", ">", "<", "|", "?", ":", "~", "$", "=",
)

# After these tokens a '/' means division.  The problem: ``a / b / c`` is
# division but ``x ~ /b/`` is a regular expression, and only context tells
# them apart.  If the previous token could end an operand (a value), a '/'
# must be the division operator; otherwise it starts a regex literal.
_OPERAND_END = frozenset({"NAME", "NUMBER", "STRING", "ERE", "BUILTIN", ")", "]", "++", "--", "$"})
# Newlines immediately following these tokens are insignificant (AWK lets a
# statement continue on the next line after them, e.g. after "&&").
_NEWLINE_EATERS = frozenset({",", "{", "&&", "||", "do", "else"})

# A dict maps keys to values: the letter after a backslash -> the character.
_SIMPLE_ESCAPES = {
    '"': '"', "\\": "\\", "/": "/", "a": "\a", "b": "\b", "f": "\f",
    "n": "\n", "r": "\r", "t": "\t", "v": "\v",
}


# ``@dataclass`` generates __init__ and __eq__ from the annotated fields below;
# ``frozen=True`` makes tokens read-only, ``slots=True`` keeps them compact.
@dataclass(frozen=True, slots=True)
class Token:
    """One lexical unit, e.g. Token("NUMBER", "3.5", line=2)."""

    type: str  # 'NUMBER', 'STRING', 'ERE', 'NAME', 'FUNC_NAME', 'BUILTIN',
    #            'NEWLINE', 'EOF', or the keyword/operator text itself.
    value: str
    line: int

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"Token({self.type!r}, {self.value!r}, line={self.line})"


def process_escapes(s: str) -> str:
    """Interpret AWK string escapes (used for literals and -v/ARGV assignments).

    Turns the two characters backslash + ``n`` into a real newline, and so on.
    Octal escapes take at most three digits: ``\\0101`` is ``\\010`` + ``1``.
    Any other backslash pair loses its backslash (``\\q`` is ``q``); a lone
    backslash at the very end is kept as it is.
    """
    if "\\" not in s:
        return s
    out: list[str] = []
    i = 0
    n = len(s)
    while i < n:
        c = s[i]
        if c != "\\" or i + 1 >= n:
            out.append(c)
            i += 1
            continue
        d = s[i + 1]
        if d in _SIMPLE_ESCAPES:
            out.append(_SIMPLE_ESCAPES[d])
            i += 2
        elif d in "01234567":
            j = i + 1
            while j < n and j < i + 4 and s[j] in "01234567":
                j += 1
            # ``s[a : b]`` is a slice: characters a..b-1.  int(..., 8) reads
            # base 8; chr() turns the code number into a character.
            out.append(chr(int(s[i + 1 : j], 8)))
            i = j
        else:
            # Undefined escape such as "\q", "\&" or "\.": POSIX leaves it
            # undefined; we drop the backslash and keep the character, as
            # onetrue-awk and gawk do (mawk keeps both).  This also applies
            # to -v/operand assignments and to strings later used as dynamic
            # regexes, so "a\.b" is the regex a.b.  See DESIGN.md.
            out.append(d)
            i += 2
    return "".join(out)


class Lexer:
    """Scans the source left to right, appending to ``self.tokens``.

    ``pos`` is the index of the next unread character and ``line`` the current
    source line (for error messages).  Each ``_xxx`` helper consumes one kind
    of token and advances ``pos`` past it.
    """

    def __init__(self, source: str) -> None:
        self.src = source
        self.pos = 0
        self.line = 1
        self.tokens: list[Token] = []

    # -- helpers ---------------------------------------------------------
    def _error(self, msg: str) -> AwkSyntaxError:
        return AwkSyntaxError(msg, self.line)

    def _prev_type(self) -> str | None:
        # ``A if cond else B`` is Python's conditional expression (AWK's ?:).
        # ``tokens[-1]`` is the last element of the list.
        return self.tokens[-1].type if self.tokens else None

    def _emit(self, type_: str, value: str) -> None:
        self.tokens.append(Token(type_, value, self.line))

    # -- main loop -------------------------------------------------------
    def tokenize(self) -> list[Token]:
        src = self.src
        n = len(src)
        while self.pos < n:
            c = src[self.pos]
            if c in " \t\r":
                self.pos += 1
            # Backslash-newline: line continuation, produces no token at all.
            elif c == "\\" and src.startswith("\n", self.pos + 1):
                self.pos += 2
                self.line += 1
            elif c == "\\" and src.startswith("\r\n", self.pos + 1):
                self.pos += 3
                self.line += 1
            elif c == "#":
                while self.pos < n and src[self.pos] != "\n":
                    self.pos += 1
            elif c == "\n":
                # Emit one NEWLINE token for a run of blank lines, and none at
                # all after a token such as "," or "{" that cannot end a statement.
                if self._prev_type() not in _NEWLINE_EATERS and self._prev_type() != "NEWLINE":
                    self._emit("NEWLINE", "\n")
                self.pos += 1
                self.line += 1
            elif c == '"':
                self._string()
            elif c == "/" and self._prev_type() not in _OPERAND_END:
                self._regex()  # regex-vs-division decision: see _OPERAND_END
            elif c.isdigit() or (c == "." and self.pos + 1 < n and src[self.pos + 1].isdigit()):
                self._number()
            elif c.isalpha() or c == "_":
                self._word()
            else:
                self._operator()
        self._emit("EOF", "")
        return self.tokens

    def _string(self) -> None:
        """Read a "..." literal.  Escapes are collected raw, then decoded once."""
        src = self.src
        i = self.pos + 1
        buf: list[str] = []
        while True:
            if i >= len(src) or src[i] == "\n":
                raise self._error("unterminated string")
            c = src[i]
            if c == '"':
                break
            if c == "\\" and i + 1 < len(src):
                if src[i + 1] == "\n":  # continuation inside a string
                    i += 2
                    self.line += 1
                    continue
                buf.append(src[i : i + 2])
                i += 2
                continue
            buf.append(c)
            i += 1
        self.pos = i + 1
        self._emit("STRING", process_escapes("".join(buf)))

    def _regex(self) -> None:
        """Read a /.../ literal.  The text is kept as-is for regex.py, except
        that ``\\/`` becomes ``/``.  A '/' inside a bracket expression such as
        ``[/]`` does not end the regex, so we track whether we are inside one.
        """
        src = self.src
        i = self.pos + 1
        buf: list[str] = []
        in_bracket = False
        while True:
            if i >= len(src) or src[i] == "\n":
                raise self._error("unterminated regular expression")
            c = src[i]
            if c == "\\" and i + 1 < len(src):
                if src[i + 1] == "/":
                    buf.append("/")
                else:
                    buf.append(src[i : i + 2])
                i += 2
                continue
            if in_bracket:
                if c == "]":
                    in_bracket = False
                # Copy a class such as [:alpha:] whole, so its ']' does not
                # look like the end of the bracket expression.
                elif c == "[" and src.startswith(("[:", "[.", "[="), i):
                    close = src.find(src[i + 1] + "]", i + 2)
                    if close != -1:
                        buf.append(src[i : close + 2])
                        i = close + 2
                        continue
            elif c == "[":
                in_bracket = True
                buf.append(c)
                i += 1
                # A ']' right after '[' or '[^' is a literal member.
                if i < len(src) and src[i] == "^":
                    buf.append("^")
                    i += 1
                if i < len(src) and src[i] == "]":
                    buf.append("]")
                    i += 1
                continue
            elif c == "/":
                break
            buf.append(c)
            i += 1
        self.pos = i + 1
        self._emit("ERE", "".join(buf))

    def _number(self) -> None:
        """Read 12, 1.5, .5, 1e3, 2E-4.  ``1e`` is the number 1 then the name e."""
        src = self.src
        i = self.pos
        n = len(src)
        while i < n and src[i].isdigit():
            i += 1
        if i < n and src[i] == ".":
            i += 1
            while i < n and src[i].isdigit():
                i += 1
        if i < n and src[i] in "eE":
            j = i + 1
            if j < n and src[j] in "+-":
                j += 1
            if j < n and src[j].isdigit():
                while j < n and src[j].isdigit():
                    j += 1
                i = j
        self._emit("NUMBER", src[self.pos : i])
        self.pos = i

    def _word(self) -> None:
        """Read a name and classify it: keyword, builtin, function call, variable."""
        src = self.src
        i = self.pos
        while i < len(src) and (src[i].isalnum() or src[i] == "_"):
            i += 1
        word = src[self.pos : i]
        self.pos = i
        if word in KEYWORDS:
            self._emit(word, word)
        elif word in BUILTINS:
            self._emit("BUILTIN", word)
        elif i < len(src) and src[i] == "(":
            # AWK distinguishes ``f(x)`` (a call: no space allowed) from
            # ``f (x)`` (variable f concatenated with x).
            self._emit("FUNC_NAME", word)
        else:
            self._emit("NAME", word)

    def _operator(self) -> None:
        for op in _OPERATORS:
            if self.src.startswith(op, self.pos):
                self.pos += len(op)
                # ``**`` is a common extension spelling of ``^`` (power).
                if op == "**":
                    op = "^"
                elif op == "**=":
                    op = "^="
                self._emit(op, op)
                return
        raise self._error(f"unexpected character {self.src[self.pos]!r}")


def tokenize(source: str) -> list[Token]:
    """Convenience entry point: the full token list, ending with an EOF token."""
    return Lexer(source).tokenize()
