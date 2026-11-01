"""Tokenizer for AWK source text.

Key responsibilities:

* Regex-vs-division disambiguation: a ``/`` starts an ERE literal unless the
  previous token can end an operand (name, number, string, ERE, ``)``,
  ``]``, a builtin such as ``length``, or a postfix ``++``/``--``).
* Newline handling: newlines are significant terminators, but are dropped
  after tokens that cannot end a statement (``,`` ``{`` ``&&`` ``||``
  ``do`` ``else``), and backslash-newline is a line continuation.
* String escape processing (``\\n``, ``\\t``, ``\\"``, ``\\/``, octal...).
"""

from __future__ import annotations

from dataclasses import dataclass

from .errors import AwkSyntaxError

KEYWORDS = frozenset(
    {
        "BEGIN", "END", "function", "func", "if", "else", "while", "for", "do",
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

# Longest operators first.
_OPERATORS = (
    "**=", "+=", "-=", "*=", "/=", "%=", "^=", "==", "<=", ">=", "!=", "++",
    "--", "&&", "||", ">>", "!~", "**",
    "{", "}", "(", ")", "[", "]", ";", ",", "+", "-", "*", "/", "%", "^",
    "!", ">", "<", "|", "?", ":", "~", "$", "=",
)

# After these tokens a '/' means division.
_OPERAND_END = frozenset({"NAME", "NUMBER", "STRING", "ERE", "BUILTIN", ")", "]", "++", "--", "$"})
# Newlines immediately following these tokens are insignificant.
_NEWLINE_EATERS = frozenset({",", "{", "&&", "||", "do", "else"})

_SIMPLE_ESCAPES = {
    '"': '"', "\\": "\\", "/": "/", "a": "\a", "b": "\b", "f": "\f",
    "n": "\n", "r": "\r", "t": "\t", "v": "\v",
}


@dataclass(frozen=True, slots=True)
class Token:
    type: str  # 'NUMBER', 'STRING', 'ERE', 'NAME', 'FUNC_NAME', 'BUILTIN',
    #            'NEWLINE', 'EOF', or the keyword/operator text itself.
    value: str
    line: int

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"Token({self.type!r}, {self.value!r}, line={self.line})"


def process_escapes(s: str) -> str:
    """Interpret AWK string escapes (used for literals and -v/ARGV assignments)."""
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
            out.append(chr(int(s[i + 1 : j], 8)))
            i = j
        else:
            # Unknown escape: keep the backslash (matches mawk/gawk behaviour).
            out.append("\\")
            out.append(d)
            i += 2
    return "".join(out)


class Lexer:
    def __init__(self, source: str) -> None:
        self.src = source
        self.pos = 0
        self.line = 1
        self.tokens: list[Token] = []

    # -- helpers ---------------------------------------------------------
    def _error(self, msg: str) -> AwkSyntaxError:
        return AwkSyntaxError(msg, self.line)

    def _prev_type(self) -> str | None:
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
                if self._prev_type() not in _NEWLINE_EATERS and self._prev_type() != "NEWLINE":
                    self._emit("NEWLINE", "\n")
                self.pos += 1
                self.line += 1
            elif c == '"':
                self._string()
            elif c == "/" and self._prev_type() not in _OPERAND_END:
                self._regex()
            elif c.isdigit() or (c == "." and self.pos + 1 < n and src[self.pos + 1].isdigit()):
                self._number()
            elif c.isalpha() or c == "_":
                self._word()
            else:
                self._operator()
        self._emit("EOF", "")
        return self.tokens

    def _string(self) -> None:
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
            self._emit("FUNC_NAME", word)
        else:
            self._emit("NAME", word)

    def _operator(self) -> None:
        for op in _OPERATORS:
            if self.src.startswith(op, self.pos):
                self.pos += len(op)
                if op == "**":
                    op = "^"
                elif op == "**=":
                    op = "^="
                self._emit(op, op)
                return
        raise self._error(f"unexpected character {self.src[self.pos]!r}")


def tokenize(source: str) -> list[Token]:
    return Lexer(source).tokenize()
