import pytest

from pyawk.errors import AwkSyntaxError
from pyawk.lexer import process_escapes, tokenize


def types(src: str) -> list[str]:
    return [t.type for t in tokenize(src)]


def test_regex_vs_division() -> None:
    assert types("a / b / c") == ["NAME", "/", "NAME", "/", "NAME", "EOF"]
    assert types("/b/") == ["ERE", "EOF"]
    assert types("x ~ /a\\/b/")[2] == "ERE"
    assert tokenize("x ~ /a\\/b/")[2].value == "a/b"
    assert types("(1) / 2") == ["(", "NUMBER", ")", "/", "NUMBER", "EOF"]
    assert types("NF / 2") == ["NAME", "/", "NUMBER", "EOF"]
    assert types("length / 2") == ["BUILTIN", "/", "NUMBER", "EOF"]
    assert types("x++ / 2") == ["NAME", "++", "/", "NUMBER", "EOF"]
    assert types("print /x/") == ["print", "ERE", "EOF"]
    assert types("a /= 2") == ["NAME", "/=", "NUMBER", "EOF"]
    assert types("$0 ~ /=/") == ["$", "NUMBER", "~", "ERE", "EOF"]


def test_regex_with_slash_in_bracket() -> None:
    toks = tokenize("/[/]x/")
    assert toks[0].type == "ERE" and toks[0].value == "[/]x"


def test_newline_handling() -> None:
    assert types("a,\nb") == ["NAME", ",", "NAME", "EOF"]
    assert types("a &&\n\nb") == ["NAME", "&&", "NAME", "EOF"]
    assert types("{\n x }") == ["{", "NAME", "}", "EOF"]
    assert types("a\n\n\nb") == ["NAME", "NEWLINE", "NAME", "EOF"]
    assert types("a \\\n + b") == ["NAME", "+", "NAME", "EOF"]
    assert types("a # comment\nb") == ["NAME", "NEWLINE", "NAME", "EOF"]


def test_numbers_and_strings() -> None:
    toks = tokenize('1 2.5 .5 1e3 1E-2 1e "a\\tb\\"c\\101"')
    assert [t.value for t in toks[:5]] == ["1", "2.5", ".5", "1e3", "1E-2"]
    assert toks[5].value == "1" and toks[6].value == "e"
    assert toks[7].value == 'a\tb"cA'


def test_keywords_builtins_funcnames() -> None:
    assert types("if while getline length substr foo foo(") == [
        "if", "while", "getline", "BUILTIN", "BUILTIN", "NAME", "FUNC_NAME", "(", "EOF",
    ]


def test_operators_longest_match() -> None:
    assert types("a**=2") == ["NAME", "^=", "NUMBER", "EOF"]
    assert types("a>>b!~c") == ["NAME", ">>", "NAME", "!~", "NAME", "EOF"]


def test_errors() -> None:
    with pytest.raises(AwkSyntaxError):
        tokenize('"unterminated')
    with pytest.raises(AwkSyntaxError):
        tokenize("/unterminated")
    with pytest.raises(AwkSyntaxError):
        tokenize("a @ b")


def test_process_escapes() -> None:
    assert process_escapes(r"a\nb") == "a\nb"
    assert process_escapes(r"\t") == "\t"
    assert process_escapes(r"\q") == "\\q"
    assert process_escapes("\\") == "\\"
    assert process_escapes(r"\0101") == "\x081"  # at most three octal digits
