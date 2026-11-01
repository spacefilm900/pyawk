import pytest

from pyawk import ast
from pyawk.errors import AwkSyntaxError, AwkUnsupportedError
from pyawk.parser import parse


def expr(src: str) -> ast.Expr:
    prog = parse("BEGIN { " + src + " }")
    stmt = prog.begin[0].body[0]
    assert isinstance(stmt, ast.ExprStmt)
    return stmt.expr


def test_program_structure() -> None:
    prog = parse("BEGIN { x = 1 }\n/re/\n$1 > 2 { print }\n{ n++ }\nEND { print n }")
    assert len(prog.begin) == 1 and len(prog.end) == 1
    assert len(prog.rules) == 3
    assert prog.rules[0].action is None and isinstance(prog.rules[0].pattern, ast.Regex)
    assert prog.rules[2].pattern is None


def test_precedence_arith() -> None:
    e = expr("x = 1 + 2 * 3")
    assert isinstance(e, ast.Assign)
    assert e.value == ast.Binary("+", ast.Num(1.0), ast.Binary("*", ast.Num(2.0), ast.Num(3.0)))


def test_power_right_assoc_and_unary_minus() -> None:
    e = expr("x = -2 ^ 3 ^ 2")
    assert e.value == ast.Unary("-", ast.Binary("^", ast.Num(2.0), ast.Binary("^", ast.Num(3.0), ast.Num(2.0))))


def test_concat_lower_than_additive() -> None:
    e = expr('x = 1 + 2 "a"')
    assert e.value == ast.Concat(ast.Binary("+", ast.Num(1.0), ast.Num(2.0)), ast.Str("a"))


def test_minus_is_binary_not_concat() -> None:
    e = expr("x = a -1")
    assert e.value == ast.Binary("-", ast.Var("a"), ast.Num(1.0))


def test_comparison_lower_than_concat() -> None:
    e = expr('x = "a" "b" < "c"')
    assert isinstance(e.value, ast.Compare)
    assert isinstance(e.value.left, ast.Concat)


def test_match_in_and_or_ternary() -> None:
    e = expr('x = a ~ /b/ && k in arr || c ? 1 : 2')
    assert isinstance(e.value, ast.Cond)
    test = e.value.test
    assert isinstance(test, ast.Or)
    assert isinstance(test.left, ast.And)
    assert isinstance(test.left.left, ast.Match)
    assert isinstance(test.left.right, ast.In)


def test_assignment_right_assoc() -> None:
    e = expr("a = b += 3")
    assert isinstance(e, ast.Assign) and isinstance(e.value, ast.Assign)
    assert e.value.op == "+="


def test_field_precedence() -> None:
    assert expr("$i++") == ast.IncDec("++", False, ast.Field(ast.Var("i")))
    assert expr("x = $NF - 1").value == ast.Binary("-", ast.Field(ast.Var("NF")), ast.Num(1.0))
    assert expr("x = $++i").value == ast.Field(ast.IncDec("++", True, ast.Var("i")))


def test_grouping_in() -> None:
    e = expr("x = (1, 2) in a")
    assert e.value == ast.In([ast.Num(1.0), ast.Num(2.0)], "a")


def test_print_redirect_vs_comparison() -> None:
    stmt = parse('BEGIN { print a > "f" }').begin[0].body[0]
    assert isinstance(stmt, ast.Print) and stmt.redirect == ">"
    stmt = parse('BEGIN { print (a > 1) }').begin[0].body[0]
    assert isinstance(stmt, ast.Print) and stmt.redirect is None
    assert isinstance(stmt.args[0], ast.Compare)
    stmt = parse('BEGIN { print (a, b) > "f" }').begin[0].body[0]
    assert len(stmt.args) == 2 and stmt.redirect == ">"
    stmt = parse('BEGIN { print "x" | "sort" }').begin[0].body[0]
    assert stmt.redirect == "|"


def test_getline_forms() -> None:
    assert expr("getline") == ast.Getline("simple", None, None)
    assert expr("getline x < f") == ast.Getline("file", ast.Var("x"), ast.Var("f"))
    assert expr('"cmd" | getline') == ast.Getline("cmd", None, ast.Str("cmd"))


def test_control_statements_parse() -> None:
    src = """
    {
        if (x) y = 1; else y = 2
        if (x)
            y = 1
        else
            y = 2
        while (i < 3) i++
        do i--; while (i > 0)
        for (i = 0; i < 3; i++) ;
        for (k in a) delete a[k]
        for (;;) break
        next
    }
    """
    body = parse(src).rules[0].action.body
    kinds = [type(s).__name__ for s in body]
    assert kinds == ["If", "If", "While", "DoWhile", "For", "ForIn", "For", "Next"]
    assert body[0].orelse is not None and body[1].orelse is not None


def test_deferred_features_error_clearly() -> None:
    with pytest.raises(AwkUnsupportedError):
        parse("function f(a) { return a }")
    with pytest.raises(AwkUnsupportedError):
        parse("/a/, /b/")
    with pytest.raises(AwkUnsupportedError):
        parse("{ f(1) }")


def test_syntax_errors() -> None:
    for bad in ["BEGIN {", "{ print ( }", "{ 1 = 2 }", "{ x y = 3 }", "BEGIN { if x }"]:
        with pytest.raises(AwkSyntaxError):
            parse(bad)
