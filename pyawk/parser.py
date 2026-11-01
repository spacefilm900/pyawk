"""Recursive-descent parser producing a :class:`pyawk.ast.Program`.

Pipeline: cli -> lexer -> **parser** -> AST -> runtime.  The parser reads
the token list from the lexer and builds the tree of ``ast`` nodes that the
runtime executes.  Entry point: ``parse(source)``.

"Recursive descent" means one method per grammar rule, each calling the
methods for the rules nested inside it.  For expressions there is one
method per precedence level: ``parse_or`` calls ``parse_and`` for its
operands, which calls ``parse_in``, and so on down to ``parse_primary``.
Because a lower level is always parsed first, it binds more tightly:
``1 + 2 * 3`` groups as ``1 + (2 * 3)``.

Expression precedence, lowest to highest (POSIX):

    assignment  = += -= *= /= %= ^=      (right)
    ?:                                   (right)
    ||                                   (left)
    &&                                   (left)
    in                                   (left)
    ~ !~                                 (left)
    < <= != == > >=   and  expr | getline (non-associative)
    concatenation                        (left)
    + -                                  (left)
    * / %                                (left)
    unary + - !
    ^                                    (right)
    ++ -- (pre/post)
    $
    grouping ()

Inside ``print``/``printf`` argument lists an unparenthesised ``>`` is an
output redirection, not a comparison (tracked with ``self.no_gt``).

Python note: ``try: ... finally: ...`` runs the ``finally`` part however the
``try`` part ends (normally or by an error), so the pattern below of saving
``no_gt``, changing it, and restoring it in ``finally`` always restores it.
"""

# Treat type hints as text, so newer syntax such as ``Token | None`` is allowed.
from __future__ import annotations

from . import ast
from .errors import AwkSyntaxError, AwkUnsupportedError
from .lexer import Token, tokenize

ASSIGN_OPS = frozenset({"=", "+=", "-=", "*=", "/=", "%=", "^="})
COMPARE_OPS = frozenset({"<", "<=", "==", "!=", ">", ">="})
# Tokens that may begin an operand of implicit concatenation.  '+', '-' and
# '!' are deliberately excluded: ``a -1`` is subtraction.
CONCAT_START = frozenset(
    {"NUMBER", "STRING", "ERE", "NAME", "FUNC_NAME", "BUILTIN", "$", "(", "++", "--"}
)
# Tokens that end a simple statement without being consumed.
_STMT_END = frozenset({";", "NEWLINE", "}", "EOF"})


class Parser:
    """Holds the token list and a cursor (``pos``) into it.

    Helper vocabulary used throughout: ``at(t)`` asks "is the current token
    of type t?", ``accept(t)`` consumes it only if so, ``expect(t)`` consumes
    it or raises a syntax error, ``advance()`` consumes unconditionally.
    """

    def __init__(self, tokens: list[Token]) -> None:
        self.tokens = tokens
        self.pos = 0
        self.no_gt = False

    # ------------------------------------------------------------ utilities
    def peek(self, offset: int = 0) -> Token:
        i = self.pos + offset
        if i < len(self.tokens):
            return self.tokens[i]
        return self.tokens[-1]

    # ``@property`` lets ``self.tok`` be read like a field while running code.
    @property
    def tok(self) -> Token:
        return self.tokens[self.pos]

    def advance(self) -> Token:
        t = self.tokens[self.pos]
        if t.type != "EOF":
            self.pos += 1
        return t

    # ``*types`` collects any number of arguments into a tuple: at("+", "-").
    def at(self, *types: str) -> bool:
        return self.tokens[self.pos].type in types

    def accept(self, type_: str) -> Token | None:
        if self.tokens[self.pos].type == type_:
            return self.advance()
        return None

    def expect(self, type_: str) -> Token:
        if self.tokens[self.pos].type != type_:
            raise self.error(f"expected {type_!r}")
        return self.advance()

    def error(self, msg: str) -> AwkSyntaxError:
        t = self.tok
        shown = "end of input" if t.type == "EOF" else repr(t.value if t.type != "NEWLINE" else "newline")
        return AwkSyntaxError(f"{msg}, found {shown}", t.line)

    def skip_newlines(self) -> None:
        while self.tokens[self.pos].type == "NEWLINE":
            self.pos += 1

    def skip_terminators(self) -> None:
        while self.tokens[self.pos].type in ("NEWLINE", ";"):
            self.pos += 1

    # -------------------------------------------------------------- program
    def parse_program(self) -> ast.Program:
        prog = ast.Program()
        self.skip_terminators()
        while not self.at("EOF"):
            self.parse_item(prog)
            self.skip_terminators()
        return prog

    def parse_item(self, prog: ast.Program) -> None:
        """One top-level item: BEGIN {...}, END {...}, pattern, or pattern {...}."""
        t = self.tok
        if t.type == "BEGIN":
            self.advance()
            self.skip_newlines()
            prog.begin.append(self.parse_block())
        elif t.type == "END":
            self.advance()
            self.skip_newlines()
            prog.end.append(self.parse_block())
        elif t.type in ("function", "func"):
            raise AwkUnsupportedError("user-defined functions", t.line)
        elif t.type == "{":
            prog.rules.append(ast.Rule(None, self.parse_block()))
        else:
            pattern = self.parse_expr()
            if self.at(","):
                raise AwkUnsupportedError("range patterns", self.tok.line)
            if self.at("{"):
                prog.rules.append(ast.Rule(pattern, self.parse_block()))
            else:
                if not self.at("NEWLINE", ";", "EOF"):
                    raise self.error("expected newline or '{' after pattern")
                prog.rules.append(ast.Rule(pattern, None))

    # ----------------------------------------------------------- statements
    def parse_block(self) -> ast.Block:
        self.expect("{")
        body: list[ast.Stmt] = []
        while True:
            self.skip_terminators()
            if self.accept("}"):
                return ast.Block(body)
            if self.at("EOF"):
                raise self.error("missing '}'")
            body.append(self.parse_statement())

    def end_simple(self) -> None:
        """A simple statement ends at ';' or newline, or just before '}'."""
        if self.at(";", "NEWLINE"):
            self.advance()
        elif not self.at("}", "EOF"):
            raise self.error("expected ';' or newline")

    def parse_statement(self) -> ast.Stmt:
        t = self.tok
        tt = t.type
        if tt == "{":
            return self.parse_block()
        if tt == ";":
            self.advance()
            return ast.Block([])
        if tt == "if":
            return self.parse_if()
        if tt == "while":
            self.advance()
            self.expect("(")
            test = self.parse_paren_expr()
            self.expect(")")
            return ast.While(test, self.parse_body())
        if tt == "do":
            self.advance()
            self.skip_newlines()
            body = self.parse_statement()
            self.skip_terminators()
            self.expect("while")
            self.expect("(")
            test = self.parse_paren_expr()
            self.expect(")")
            self.end_simple()
            return ast.DoWhile(body, test)
        if tt == "for":
            return self.parse_for()
        stmt = self.parse_simple_statement()
        self.end_simple()
        return stmt

    def parse_body(self) -> ast.Stmt:
        """Body of if/while/for: optional newlines then one statement."""
        self.skip_newlines()
        return self.parse_statement()

    def parse_paren_expr(self) -> ast.Expr:
        """An expression inside (...) of if/while/for, where '>' is comparison."""
        saved = self.no_gt
        self.no_gt = False
        try:
            self.skip_newlines()
            e = self.parse_expr()
            self.skip_newlines()
            return e
        finally:
            self.no_gt = saved

    def parse_if(self) -> ast.If:
        self.expect("if")
        self.expect("(")
        test = self.parse_paren_expr()
        self.expect(")")
        then = self.parse_body()
        # Look past ';' and newlines for an ``else``; if there is none, rewind
        # so the terminators still end the enclosing statement list.
        save = self.pos
        self.skip_terminators()
        if self.accept("else"):
            self.skip_newlines()
            return ast.If(test, then, self.parse_statement())
        self.pos = save
        return ast.If(test, then, None)

    def parse_for(self) -> ast.Stmt:
        self.expect("for")
        self.expect("(")
        # for (name in array): recognised by looking 4 tokens ahead, since a
        # C-style loop such as ``for (i = 0; ...)`` also starts with a name.
        if (
            self.peek().type == "NAME"
            and self.peek(1).type == "in"
            and self.peek(2).type == "NAME"
            and self.peek(3).type == ")"
        ):
            var = ast.Var(self.advance().value)
            self.advance()
            array = self.advance().value
            self.expect(")")
            return ast.ForIn(var, array, self.parse_body())
        init = None if self.at(";") else self.parse_simple_expr_stmt()
        self.expect(";")
        self.skip_newlines()
        test = None if self.at(";") else self.parse_paren_expr()
        self.expect(";")
        self.skip_newlines()
        step = None if self.at(")") else self.parse_simple_expr_stmt()
        self.expect(")")
        return ast.For(init, test, step, self.parse_body())

    def parse_simple_expr_stmt(self) -> ast.Expr:
        return self.parse_paren_expr()

    def parse_simple_statement(self) -> ast.Stmt:
        t = self.tok
        tt = t.type
        if tt in ("print", "printf"):
            return self.parse_print()
        if tt == "break":
            self.advance()
            return ast.Break()
        if tt == "continue":
            self.advance()
            return ast.Continue()
        if tt == "next":
            self.advance()
            return ast.Next()
        if tt == "nextfile":
            raise AwkUnsupportedError("nextfile", t.line)
        if tt == "return":
            raise AwkUnsupportedError("return (user-defined functions)", t.line)
        if tt == "exit":
            self.advance()
            if self.at(*_STMT_END):
                return ast.Exit(None)
            return ast.Exit(self.parse_expr())
        if tt == "delete":
            self.advance()
            name = self.expect("NAME").value
            if self.accept("["):
                subs = self.parse_expr_list_in_brackets()
                return ast.Delete(name, subs)
            return ast.Delete(name, None)
        return ast.ExprStmt(self.parse_expr())

    def parse_print(self) -> ast.Stmt:
        """print/printf with optional ``> file``, ``>> file`` or ``| command``."""
        kind = self.advance().type
        args: list[ast.Expr] = []
        if not self.at(";", "NEWLINE", "}", "EOF", ">", ">>", "|"):
            saved = self.no_gt
            self.no_gt = True
            try:
                args = self.parse_expr_list()
            finally:
                self.no_gt = saved
            # ``print (a, b)`` is the same as ``print a, b``.
            if len(args) == 1 and isinstance(args[0], ast.Group):
                args = args[0].exprs
        for a in args:
            self.check_no_group(a)
        redirect = dest = None
        if self.at(">", ">>", "|"):
            redirect = self.advance().type
            # Parsed at concatenation level (like gawk): ``> "a" "b"`` writes to
            # file "ab"; a ?: or comparison target needs parentheses.
            saved = self.no_gt
            self.no_gt = True
            try:
                dest = self.parse_concat()
            finally:
                self.no_gt = saved
        if kind == "printf":
            if not args:
                raise self.error("printf: no format")
            return ast.Printf(args, redirect, dest)
        return ast.Print(args, redirect, dest)

    def check_no_group(self, e: ast.Expr) -> None:
        if isinstance(e, ast.Group):
            raise AwkSyntaxError("parenthesised list is only valid before 'in' or after print", self.tok.line)

    # ---------------------------------------------------------- expressions
    def parse_expr_list(self) -> list[ast.Expr]:
        exprs = [self.parse_expr()]
        while self.accept(","):
            self.skip_newlines()
            exprs.append(self.parse_expr())
        return exprs

    def parse_expr_list_in_brackets(self) -> list[ast.Expr]:
        saved = self.no_gt
        self.no_gt = False
        try:
            exprs = self.parse_expr_list()
        finally:
            self.no_gt = saved
        self.expect("]")
        return exprs

    def parse_expr(self) -> ast.Expr:
        """Assignment level (right associative).

        We parse the left side as an ordinary expression, then check that it
        is assignable.  Recursing into ``parse_expr`` for the right side makes
        ``a = b = 1`` group as ``a = (b = 1)``.
        """
        left = self.parse_ternary()
        if self.tok.type in ASSIGN_OPS:
            if not isinstance(left, ast.LVALUE_TYPES):
                raise self.error("assignment to non-lvalue")
            op = self.advance().type
            self.skip_newlines()
            right = self.parse_expr()
            self.check_no_group(right)
            return ast.Assign(op, left, right)
        return left

    def parse_ternary(self) -> ast.Expr:
        test = self.parse_or()
        if self.accept("?"):
            self.skip_newlines()
            a = self.parse_expr()
            self.skip_newlines()
            self.expect(":")
            self.skip_newlines()
            b = self.parse_expr()
            return ast.Cond(test, a, b)
        return test

    def parse_or(self) -> ast.Expr:
        # Left-associative levels use a loop: a || b || c -> (a || b) || c.
        left = self.parse_and()
        while self.accept("||"):
            self.skip_newlines()
            left = ast.Or(left, self.parse_and())
        return left

    def parse_and(self) -> ast.Expr:
        left = self.parse_in()
        while self.accept("&&"):
            self.skip_newlines()
            left = ast.And(left, self.parse_in())
        return left

    def parse_in(self) -> ast.Expr:
        left = self.parse_match()
        while self.at("in"):
            self.advance()
            name = self.expect("NAME").value
            # ``(i, j) in arr`` tests the multi-dimensional key i SUBSEP j.
            subs = left.exprs if isinstance(left, ast.Group) else [left]
            left = ast.In(subs, name)
        return left

    def parse_match(self) -> ast.Expr:
        left = self.parse_comparison()
        while self.at("~", "!~"):
            negate = self.advance().type == "!~"
            left = ast.Match(negate, left, self.parse_comparison())
        return left

    def parse_comparison(self) -> ast.Expr:
        left = self.parse_pipe_getline()
        t = self.tok.type
        # ``if`` rather than ``while``: comparisons do not chain (a < b < c is
        # an error).  Inside print arguments a bare '>' is left for redirection.
        if t in COMPARE_OPS and not (t == ">" and self.no_gt):
            self.advance()
            left = ast.Compare(t, left, self.parse_pipe_getline())
        return left

    def parse_pipe_getline(self) -> ast.Expr:
        left = self.parse_concat()
        while self.at("|") and self.peek(1).type == "getline":
            self.advance()
            self.advance()
            target = self.parse_optional_lvalue()
            left = ast.Getline("cmd", target, left)
        return left

    def parse_concat(self) -> ast.Expr:
        """Concatenation has no operator: two operands side by side.

        We keep concatenating while the next token can *start* an operand.
        ``-`` is excluded (see CONCAT_START), so ``a -1`` is subtraction and
        ``a " " -1`` is ``a (" " - 1)``, exactly as POSIX grammar says.
        """
        left = self.parse_additive()
        while self.tok.type in CONCAT_START:
            left = ast.Concat(left, self.parse_additive())
        return left

    def parse_additive(self) -> ast.Expr:
        left = self.parse_multiplicative()
        while self.at("+", "-"):
            op = self.advance().type
            left = ast.Binary(op, left, self.parse_multiplicative())
        return left

    def parse_multiplicative(self) -> ast.Expr:
        left = self.parse_unary()
        while self.at("*", "/", "%"):
            op = self.advance().type
            left = ast.Binary(op, left, self.parse_unary())
        return left

    def parse_unary(self) -> ast.Expr:
        # Unary minus binds looser than ^, so -2^2 is -(2^2) = -4.
        if self.at("!", "-", "+"):
            op = self.advance().type
            return ast.Unary(op, self.parse_unary())
        return self.parse_power()

    def parse_power(self) -> ast.Expr:
        base = self.parse_postfix()
        # Right associative via recursion: 2^3^2 is 2^(3^2) = 512.
        if self.accept("^"):
            return ast.Binary("^", base, self.parse_power_rhs())
        return base

    def parse_power_rhs(self) -> ast.Expr:
        # The exponent may carry its own sign: 2^-1 is 0.5.
        if self.at("!", "-", "+"):
            op = self.advance().type
            return ast.Unary(op, self.parse_power_rhs())
        return self.parse_power()

    def parse_postfix(self) -> ast.Expr:
        """Prefix ``++x`` / ``--x`` and postfix ``x++`` / ``x--``."""
        if self.at("++", "--"):
            op = self.advance().type
            target = self.parse_postfix()
            if not isinstance(target, ast.LVALUE_TYPES):
                raise self.error(f"{op} applied to non-lvalue")
            return ast.IncDec(op, True, target)
        e = self.parse_primary()
        if isinstance(e, ast.LVALUE_TYPES) and self.at("++", "--"):
            return ast.IncDec(self.advance().type, False, e)
        return e

    def parse_optional_lvalue(self) -> ast.Expr | None:
        """The optional variable after ``getline`` (None if there is none)."""
        if self.at("NAME"):
            name = self.advance().value
            if self.accept("["):
                return ast.Index(name, self.parse_expr_list_in_brackets())
            return ast.Var(name)
        if self.accept("$"):
            return ast.Field(self.parse_field_operand())
        return None

    def parse_field_operand(self) -> ast.Expr:
        """What follows ``$``: binds tightly, so ``$NF-1`` is ``($NF) - 1``
        and ``$i++`` increments the field, not i."""
        if self.at("++", "--"):
            return self.parse_postfix()
        if self.at("-", "+", "!"):
            op = self.advance().type
            return ast.Unary(op, self.parse_field_operand())
        return self.parse_primary()

    def parse_primary(self) -> ast.Expr:
        """The highest precedence level: literals, variables, $, (...), calls."""
        t = self.tok
        tt = t.type
        if tt == "NUMBER":
            self.advance()
            return ast.Num(float(t.value))
        if tt == "STRING":
            self.advance()
            return ast.Str(t.value)
        if tt == "ERE":
            self.advance()
            return ast.Regex(t.value)
        if tt == "$":
            self.advance()
            return ast.Field(self.parse_field_operand())
        if tt == "(":
            self.advance()
            saved = self.no_gt
            self.no_gt = False
            try:
                self.skip_newlines()
                exprs = [self.parse_expr()]
                while self.accept(","):
                    self.skip_newlines()
                    exprs.append(self.parse_expr())
                self.skip_newlines()
                self.expect(")")
            finally:
                self.no_gt = saved
            # One expression in parentheses is just that expression (the
            # tree's shape already records the grouping); a list is a Group.
            if len(exprs) == 1:
                return exprs[0] if not isinstance(exprs[0], ast.Group) else exprs[0]
            return ast.Group(exprs)
        if tt == "NAME":
            self.advance()
            if self.accept("["):
                return ast.Index(t.value, self.parse_expr_list_in_brackets())
            return ast.Var(t.value)
        if tt == "FUNC_NAME":
            raise AwkUnsupportedError(f"user-defined function call {t.value}()", t.line)
        if tt == "BUILTIN":
            return self.parse_builtin()
        if tt == "getline":
            self.advance()
            target = self.parse_optional_lvalue()
            if self.accept("<"):
                return ast.Getline("file", target, self.parse_getline_source())
            return ast.Getline("simple", target, None)
        raise self.error("unexpected token")

    def parse_getline_source(self) -> ast.Expr:
        # High precedence operand: ``getline < "a" "b"`` is (getline < "a") "b".
        return self.parse_postfix()

    def parse_builtin(self) -> ast.Expr:
        t = self.advance()
        name = t.value
        if not self.at("("):
            # ``length`` without parentheses means length($0).
            if name == "length":
                return ast.Call("length", [])
            raise self.error(f"expected '(' after {name}")
        self.advance()
        saved = self.no_gt
        self.no_gt = False
        try:
            self.skip_newlines()
            args: list[ast.Expr] = []
            if not self.at(")"):
                args = self.parse_expr_list()
            self.skip_newlines()
            self.expect(")")
        finally:
            self.no_gt = saved
        for a in args:
            self.check_no_group(a)
        return ast.Call(name, args)


def parse(source: str) -> ast.Program:
    """Entry point: program text -> tree.  Raises AwkSyntaxError if malformed."""
    return Parser(tokenize(source)).parse_program()
