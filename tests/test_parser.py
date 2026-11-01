import pytest

from pyawk import ast
from pyawk.errors import AwkSyntaxError
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


def test_nextfile_is_a_statement() -> None:
    body = parse("{ if (FNR == 2) nextfile; print }").rules[0].action.body
    assert type(body[0].then).__name__ == "NextFile"


@pytest.mark.parametrize(
    "src",
    [
        "BEGIN { nextfile }",
        "END { if (0) nextfile }",
        "function f() { nextfile } { f() }",
    ],
)
def test_nextfile_outside_a_rule_is_a_syntax_error(src: str) -> None:
    # mawk and gawk reject nextfile in BEGIN/END while parsing (onetrue-awk
    # only when it runs); mawk and onetrue-awk reject it in a function.
    with pytest.raises(AwkSyntaxError, match="nextfile"):
        parse(src)


def test_nextfile_is_a_keyword() -> None:
    with pytest.raises(AwkSyntaxError):
        parse("BEGIN { nextfile = 1 }")


# ------------------------------------------------------------ range patterns
def test_range_pattern_with_and_without_action() -> None:
    prog = parse("/a/, /b/ { print 1 }\nNR == 2, NR == 4")
    r1, r2 = prog.rules
    assert r1.pattern == ast.Regex("a") and r1.end == ast.Regex("b")
    assert r1.action is not None
    assert r2.pattern == ast.Compare("==", ast.Var("NR"), ast.Num(2.0))
    assert r2.end == ast.Compare("==", ast.Var("NR"), ast.Num(4.0))
    assert r2.action is None


def test_plain_rule_has_no_end_pattern() -> None:
    assert parse("/a/ { print }").rules[0].end is None


def test_range_comma_binds_loosest() -> None:
    # The comma is below every operator, so the whole of `a || b` is the
    # start condition (not `a || (b, /x/)`); the same goes for ?: and =.
    r = parse('$1 == "a" || $2, /x/').rules[0]
    assert isinstance(r.pattern, ast.Or) and r.end == ast.Regex("x")
    assert isinstance(parse("$1 > 2 ? 1 : 0, /5/").rules[0].pattern, ast.Cond)
    assert isinstance(parse("x = /2/, /4/").rules[0].pattern, ast.Assign)


def test_range_allows_newline_after_comma_only() -> None:
    assert parse("/a/,\n\n/b/").rules[0].end == ast.Regex("b")
    with pytest.raises(AwkSyntaxError):
        parse("/a/\n, /b/")


def test_range_rules_separated_by_semicolon() -> None:
    prog = parse("/1/, /2/; /5/")
    assert prog.rules[0].end == ast.Regex("2") and prog.rules[1].end is None


@pytest.mark.parametrize(
    "src",
    [
        "BEGIN, /x/ { print }",  # BEGIN/END cannot be range endpoints
        "/x/, END { print }",
        "/x/, BEGIN",
        "(/a/, /b/)",  # a range is not an expression: no nesting ...
        "/a/, /b/ || /c/, /d/",  # ... and only one comma
        "/a/, /b/, /c/",
        "/a/, { print }",  # the end condition is required
        "{ x = (/a/, /b/) }",
    ],
)
def test_range_pattern_errors(src: str) -> None:
    with pytest.raises(AwkSyntaxError):
        parse(src)


# ------------------------------------------------------- user-defined functions
def test_function_definition_and_call() -> None:
    prog = parse("function add(a, b) { return a + b }\nBEGIN { x = add(1, 2) }")
    fn = prog.functions["add"]
    assert fn.name == "add" and fn.params == ["a", "b"]
    assert fn.body.body == [ast.Return(ast.Binary("+", ast.Var("a"), ast.Var("b")))]
    call = prog.begin[0].body[0].expr.value
    assert call == ast.UserCall("add", [ast.Num(1.0), ast.Num(2.0)])


def test_function_definition_layouts() -> None:
    # A space before '(' is fine in a *definition*, and so are a newline
    # after a comma in the parameter list and newlines before the body.
    prog = parse("function f (a,\n b)\n\n{\n return\n}\n;BEGIN { f( ) }")
    assert prog.functions["f"].params == ["a", "b"]
    assert prog.functions["f"].body.body == [ast.Return(None)]
    assert prog.begin[0].body[0].expr == ast.UserCall("f", [])


def test_function_may_be_used_before_its_definition() -> None:
    prog = parse("BEGIN { print g(1) }\nfunction g(x) { return x }")
    assert list(prog.functions) == ["g"]


def test_call_to_undefined_function_parses() -> None:
    # Like onetrue-awk and gawk, an undefined function is an error only if
    # the call is actually executed (see test_runtime).
    assert parse("BEGIN { if (0) nosuch(1) }").functions == {}


def test_func_is_not_a_keyword() -> None:
    # gawk --posix and mawk treat ``func`` as an ordinary name.
    prog = parse("BEGIN { func = 1; print func }")
    assert prog.begin[0].body[0].expr == ast.Assign("=", ast.Var("func"), ast.Num(1.0))


@pytest.mark.parametrize(
    "bad",
    [
        "function f(a) { } function f(b) { }",  # defined twice
        "function f(f) { }",  # parameter named like its own function
        "function g() { } function f(g) { }",  # ... or like another function
        "function f(a, a) { }",  # duplicate parameter
        "function f(length) { }",  # builtin name as parameter
        "function f(NR) { }",  # special variable as parameter
        "function f(FS) { }",
        "function length(x) { }",  # builtin name as function name
        "function print(x) { }",  # keyword as function name
        "function NR(x) { }",  # special variable as function name
        "function f() { return 1 } BEGIN { f = 2 }",  # function used as variable
        "function f() { return 1 } BEGIN { print f }",
        "BEGIN { f[1] = 1 } function f() { }",  # ... as an array
        "BEGIN { x = f (1) } function f(a) { }",  # space before '(' in a call
        "BEGIN { delete f } function f() { }",
        "BEGIN { if (1 in f) print } function f() { }",
        "BEGIN { getline f } function f() { }",
        "BEGIN { for (f in a) ; } function f() { }",
        "BEGIN { for (k in f) ; } function f() { }",
        "BEGIN { return 1 }",  # return outside a function
        "{ return }",
        "function f() { next }",  # next inside a function
        "function f() { break }",  # break outside a loop
        "BEGIN { continue }",
        "function f(a { }",
        "function f(a,) { }",
        "function (a) { }",
        "function f(a)",
    ],
)
def test_function_syntax_errors(bad: str) -> None:
    with pytest.raises(AwkSyntaxError):
        parse(bad)


def test_func_keyword_error_mentions_function() -> None:
    with pytest.raises(AwkSyntaxError, match="'function'"):
        parse("func f(x) { return x }")


def test_function_name_as_variable_message() -> None:
    with pytest.raises(AwkSyntaxError, match="function f .*variable"):
        parse("function f(a) { } BEGIN { print f (1) }")


def test_too_many_arguments_is_a_warning() -> None:
    prog = parse("function g(a) { return a } BEGIN { g(1, 2); g(3) }")
    assert len(prog.warnings) == 1
    assert "g" in prog.warnings[0] and "2" in prog.warnings[0]


def test_loops_inside_functions_allow_break() -> None:
    prog = parse("function f() { while (1) { break }; for (;;) continue }")
    assert len(prog.functions["f"].body.body) == 2


def test_syntax_errors() -> None:
    for bad in ["BEGIN {", "{ print ( }", "{ 1 = 2 }", "{ x y = 3 }", "BEGIN { if x }"]:
        with pytest.raises(AwkSyntaxError):
            parse(bad)
