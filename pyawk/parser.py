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

User-defined functions.  ``function name(params) { body }`` may appear
anywhere a rule may, and a function may be called before its definition.
So some checks can only be made once the whole program has been read; the
parser collects what it needs while parsing (every name used as a variable
or array, every call with its argument count) and checks it at the end in
``check_functions``.  A call is written ``name(args)`` with *no* space
before ``(``: the lexer turns such a word into a FUNC_NAME token.  Written
with a space, ``f (x)`` lexes as the variable ``f`` followed by the
grouping ``(x)`` (a concatenation), which is then an error if ``f`` is a
function, because a function name may not be used as a variable.

Python note: ``try: ... finally: ...`` runs the ``finally`` part however the
``try`` part ends (normally or by an error), so the pattern below of saving
``no_gt``, changing it, and restoring it in ``finally`` always restores it.
"""

# Treat type hints as text, so newer syntax such as ``Token | None`` is allowed.
from __future__ import annotations

from . import ast
from .errors import AwkSyntaxError
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
# The special variables.  POSIX forbids using one as a function name or a
# function parameter name (gawk enforces this for all of them; mawk and
# onetrue-awk for some), so pyawk rejects them all.
SPECIAL_VARS = frozenset(
    {
        "ARGC", "ARGV", "CONVFMT", "ENVIRON", "FILENAME", "FNR", "FS", "NF",
        "NR", "OFMT", "OFS", "ORS", "RLENGTH", "RS", "RSTART", "SUBSEP",
    }
)


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
        # Context for statements that are only legal in some places:
        # ``return`` needs an enclosing function (``in_function`` holds its
        # name, None outside functions), ``break``/``continue`` an enclosing
        # loop (``loop_depth`` counts the loops we are inside).
        self.in_function: str | None = None
        self.loop_depth = 0
        # "BEGIN" or "END" while parsing one of those actions, else None
        # (``next`` and ``nextfile`` are syntax errors there).
        self.in_special: str | None = None
        # Collected for the end-of-program checks (see check_functions):
        # (name, line) for every variable/array use, and (name, number of
        # arguments, line) for every user function call.
        self.var_uses: list[tuple[str, int]] = []
        self.calls: list[tuple[str, int, int]] = []

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
        self.check_functions(prog)
        return prog

    def check_functions(self, prog: ast.Program) -> None:
        """Checks that need the whole program, because a function may be
        used before it is defined.

        * A function name may not also be used as a variable or array (this
          also catches a call written with a space: ``f (x)``).
        * A parameter may not have the same name as any function.
        * Passing more arguments than a function has parameters is allowed
          with a warning (as onetrue-awk and gawk do; mawk refuses).
        A call of a function that is never defined is *not* an error here:
        like onetrue-awk and gawk, it fails only if it is actually executed.
        """
        funcs = prog.functions
        for name, line in self.var_uses:
            if name in funcs:
                raise AwkSyntaxError(
                    f"function {name} called with a space before '(' or used as a variable or array", line
                )
        for fn in funcs.values():
            for p in fn.params:
                if p in funcs:
                    raise AwkSyntaxError(f"function {fn.name}: parameter {p} has the name of a function", None)
        for name, nargs, line in self.calls:
            fn = funcs.get(name)
            if fn is not None and nargs > len(fn.params):
                prog.warnings.append(
                    f"line {line}: function {name} called with {nargs} args, accepts only {len(fn.params)}"
                )

    def parse_item(self, prog: ast.Program) -> None:
        """One top-level item: BEGIN {...}, END {...}, pattern, or pattern {...}."""
        t = self.tok
        if t.type in ("BEGIN", "END"):
            self.advance()
            self.skip_newlines()
            self.in_special = t.type
            try:
                block = self.parse_block()
            finally:
                self.in_special = None
            (prog.begin if t.type == "BEGIN" else prog.end).append(block)
        elif t.type == "function":
            self.parse_function(prog)
        elif t.type == "NAME" and t.value == "func" and self.peek(1).type == "FUNC_NAME":
            # ``func name(...)`` would otherwise parse as a pattern (variable
            # ``func`` concatenated with a call) and fail confusingly later.
            raise AwkSyntaxError("'func' is not POSIX awk; write 'function' to define a function", t.line)
        elif t.type == "{":
            prog.rules.append(ast.Rule(None, self.parse_block()))
        else:
            pattern = self.parse_pattern()
            # ``start, end``: a range pattern.  The comma is not an operator:
            # it is only recognised here, after a complete expression, so it
            # binds more loosely than everything (``a || b, c`` means
            # ``(a || b), c``) and a range can never appear *inside* an
            # expression.  As after any comma in AWK, newlines may follow it
            # (but not precede it: a newline before it ends the rule).
            end = None
            if self.accept(","):
                self.skip_newlines()
                if self.at("BEGIN", "END", "{"):
                    raise self.error("expected an expression after ',' in a range pattern")
                end = self.parse_pattern()
            if self.at("{"):
                prog.rules.append(ast.Rule(pattern, self.parse_block(), end))
            else:
                if not self.at("NEWLINE", ";", "EOF"):
                    raise self.error("expected newline or '{' after pattern")
                prog.rules.append(ast.Rule(pattern, None, end))

    def parse_pattern(self) -> ast.Expr:
        """One pattern expression.  A parenthesised list such as
        ``(/a/, /b/)`` is not a pattern: it is only valid before ``in``
        (all three reference awks reject it here too)."""
        e = self.parse_expr()
        self.check_no_group(e)
        return e

    def parse_function(self, prog: ast.Program) -> None:
        """``function name(p1, p2, ...) [newlines] { body }``.

        The name is a FUNC_NAME token when written ``f(`` and a NAME token
        when written ``f (`` (both are fine in a definition).  A builtin or
        keyword name lexes as something else and is rejected as a syntax
        error.
        """
        self.expect("function")
        t = self.tok
        if t.type not in ("NAME", "FUNC_NAME"):
            raise self.error("expected function name")
        name = self.advance().value
        if name in SPECIAL_VARS:
            raise AwkSyntaxError(f"{name} is a special variable and cannot be a function name", t.line)
        if name in prog.functions:
            raise AwkSyntaxError(f"function {name} is defined twice", t.line)
        self.expect("(")
        params: list[str] = []
        if not self.at(")"):
            while True:
                p = self.tok
                if p.type != "NAME":
                    raise self.error("expected parameter name")
                self.advance()
                if p.value == name:
                    raise AwkSyntaxError(f"function {name}: parameter has the same name as the function", p.line)
                if p.value in SPECIAL_VARS:
                    raise AwkSyntaxError(f"function {name}: special variable {p.value} cannot be a parameter", p.line)
                if p.value in params:
                    raise AwkSyntaxError(f"function {name}: parameter {p.value} appears twice", p.line)
                params.append(p.value)
                if not self.accept(","):
                    break
                self.skip_newlines()
        self.expect(")")
        self.skip_newlines()
        # Record the function *before* parsing the body, so the duplicate
        # check above sees it; the body may call the function (recursion).
        fn = ast.Function(name, params, ast.Block([]))
        prog.functions[name] = fn
        # Inside the body: ``return`` is allowed and no loop encloses us.
        saved = (self.in_function, self.loop_depth)
        self.in_function, self.loop_depth = name, 0
        try:
            fn.body = self.parse_block()
        finally:
            self.in_function, self.loop_depth = saved

    def name_use(self, tok: Token) -> str:
        """Note that the NAME token ``tok`` is used as a variable or array
        (for check_functions) and return the name."""
        self.var_uses.append((tok.value, tok.line))
        return tok.value

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
            return ast.While(test, self.parse_loop_body())
        if tt == "do":
            self.advance()
            self.skip_newlines()
            self.loop_depth += 1
            try:
                body = self.parse_statement()
            finally:
                self.loop_depth -= 1
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

    def parse_loop_body(self) -> ast.Stmt:
        """Body of a while/for loop, where ``break`` and ``continue`` are legal."""
        self.loop_depth += 1
        try:
            return self.parse_body()
        finally:
            self.loop_depth -= 1

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
            var = ast.Var(self.name_use(self.advance()))
            self.advance()
            array = self.name_use(self.advance())
            self.expect(")")
            return ast.ForIn(var, array, self.parse_loop_body())
        init = None if self.at(";") else self.parse_for_clause()
        self.expect(";")
        self.skip_newlines()
        test = None if self.at(";") else self.parse_paren_expr()
        self.expect(";")
        self.skip_newlines()
        step = None if self.at(")") else self.parse_for_clause()
        self.expect(")")
        return ast.For(init, test, step, self.parse_loop_body())

    def parse_for_clause(self) -> ast.Expr | ast.Stmt:
        """The init or step part of ``for (init; test; step)``.

        POSIX calls these *simple statements*: usually an expression such as
        ``i = 0`` or ``i++``, but ``print``, ``printf`` and ``delete`` are
        allowed too (onetrue-awk and gawk accept ``for (print 9; 0;)``;
        mawk does not).  Expressions are returned as expressions and
        statements as statements; the runtime tells them apart.
        """
        if self.at("print", "printf", "delete"):
            saved = self.no_gt
            self.no_gt = False
            try:
                return self.parse_simple_statement()
            finally:
                self.no_gt = saved
        return self.parse_paren_expr()

    def parse_simple_statement(self) -> ast.Stmt:
        t = self.tok
        tt = t.type
        if tt in ("print", "printf"):
            return self.parse_print()
        if tt in ("break", "continue"):
            # All three reference awks reject these outside a loop when
            # parsing.  (Inside a function, a loop in the *caller* does not
            # count: parse_function resets loop_depth.)
            if self.loop_depth == 0:
                raise AwkSyntaxError(f"{tt} is not inside a loop", t.line)
            self.advance()
            return ast.Break() if tt == "break" else ast.Continue()
        if tt == "next":
            # There is no current record in BEGIN or END.  POSIX leaves
            # ``next`` there undefined; mawk and gawk reject it while
            # parsing (onetrue-awk only when it runs), and so do we.
            if self.in_special is not None:
                raise AwkSyntaxError(f"next used in {self.in_special} action", t.line)
            # onetrue-awk and mawk reject ``next`` in a function body (gawk
            # allows it); we follow the majority.
            if self.in_function is not None:
                raise AwkSyntaxError(f"next is not allowed inside a function ({self.in_function})", t.line)
            self.advance()
            return ast.Next()
        if tt == "nextfile":
            # There is no current file in BEGIN or END: mawk and gawk reject
            # nextfile there while parsing (onetrue-awk only when it runs).
            # In a function, mawk and onetrue-awk reject it (gawk allows it),
            # just as for ``next``.
            if self.in_special is not None:
                raise AwkSyntaxError(f"nextfile used in {self.in_special} action", t.line)
            if self.in_function is not None:
                raise AwkSyntaxError(f"nextfile is not allowed inside a function ({self.in_function})", t.line)
            self.advance()
            return ast.NextFile()
        if tt == "return":
            if self.in_function is None:
                raise AwkSyntaxError("return outside a function", t.line)
            self.advance()
            if self.at(*_STMT_END):
                return ast.Return(None)
            return ast.Return(self.parse_expr())
        if tt == "exit":
            self.advance()
            if self.at(*_STMT_END):
                return ast.Exit(None)
            return ast.Exit(self.parse_expr())
        if tt == "delete":
            self.advance()
            name = self.name_use(self.expect("NAME"))
            if self.accept("["):
                subs = self.parse_expr_list_in_brackets()
                return ast.Delete(name, subs)
            return ast.Delete(name, None)
        return ast.ExprStmt(self.parse_expr())

    def parse_print(self) -> ast.Stmt:
        """print/printf with optional ``> file``, ``>> file`` or ``| command``."""
        kind = self.advance().type
        args: list[ast.Expr] = []
        if not self.at(";", "NEWLINE", "}", "EOF", ">", ">>", "|", ")"):
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
            # A bare ``printf`` (no format) prints nothing: onetrue-awk and
            # gawk accept it, mawk rejects it.  The runtime handles args == [].
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

    def maybe_assign(self, operand: ast.Expr) -> ast.Expr:
        """Turn ``operand`` into an assignment if an assignment operator follows.

        Used for the right operand of ``||``, ``&&``, ``~``, ``!~`` and the
        comparisons, where all three reference awks accept an assignment
        without parentheses: ``gsub(/x/, "y") && $0 = $1`` means
        ``... && ($0 = $1)``, and ``1 < x = 3`` means ``1 < (x = 3)``.
        (Their yacc grammars have the rule ``lvalue = expr`` at the same
        level as these operators.)  After arithmetic, concatenation or a
        unary operator (``1 + x = 3``) it stays a syntax error, reported by
        ``parse_expr`` when it finds the ``=``.
        """
        if self.tok.type in ASSIGN_OPS and isinstance(operand, ast.LVALUE_TYPES):
            op = self.advance().type
            self.skip_newlines()
            value = self.parse_expr()
            self.check_no_group(value)
            return ast.Assign(op, operand, value)
        return operand

    def parse_or(self) -> ast.Expr:
        # Left-associative levels use a loop: a || b || c -> (a || b) || c.
        left = self.parse_and()
        while self.accept("||"):
            self.skip_newlines()
            left = ast.Or(left, self.maybe_assign(self.parse_and()))
        return left

    def parse_and(self) -> ast.Expr:
        left = self.parse_in()
        while self.accept("&&"):
            self.skip_newlines()
            left = ast.And(left, self.maybe_assign(self.parse_in()))
        return left

    def parse_in(self) -> ast.Expr:
        left = self.parse_match()
        while self.at("in"):
            self.advance()
            name = self.name_use(self.expect("NAME"))
            # ``(i, j) in arr`` tests the multi-dimensional key i SUBSEP j.
            subs = left.exprs if isinstance(left, ast.Group) else [left]
            left = ast.In(subs, name)
        return left

    def parse_match(self) -> ast.Expr:
        left = self.parse_comparison()
        while self.at("~", "!~"):
            negate = self.advance().type == "!~"
            left = ast.Match(negate, left, self.maybe_assign(self.parse_comparison()))
        return left

    def parse_comparison(self) -> ast.Expr:
        left = self.parse_pipe_getline()
        t = self.tok.type
        # ``if`` rather than ``while``: comparisons do not chain (a < b < c is
        # an error).  Inside print arguments a bare '>' is left for redirection.
        if t in COMPARE_OPS and not (t == ">" and self.no_gt):
            self.advance()
            left = ast.Compare(t, left, self.maybe_assign(self.parse_pipe_getline()))
        return left

    def parse_pipe_getline(self) -> ast.Expr:
        left = self.parse_concat()
        while self.at("|") and self.peek(1).type == "getline":
            self.advance()
            self.advance()
            target = self.parse_optional_lvalue()
            left = self.parse_operand_tail(ast.Getline("cmd", target, left))
        return left

    def parse_operand_tail(self, left: ast.Expr) -> ast.Expr:
        """Carry on from ``cmd | getline [var]`` as from any other operand.

        In the POSIX grammar ``unary_expr '|' simple_get`` is a complete
        operand, so operators after it apply to getline's result:
        ``cmd | getline x + 1`` is ``(cmd | getline x) + 1`` and
        ``cmd | getline x y`` is ``(cmd | getline x) y`` (mawk and gawk;
        onetrue-awk agrees only for concatenation).  The steps below are
        parse_power .. parse_concat with the left operand already parsed.
        """
        if self.accept("^"):
            left = ast.Binary("^", left, self.parse_power_rhs())
        while self.at("*", "/", "%"):
            op = self.advance().type
            left = ast.Binary(op, left, self.parse_unary())
        while self.at("+", "-"):
            op = self.advance().type
            left = ast.Binary(op, left, self.parse_multiplicative())
        while self.tok.type in CONCAT_START:
            left = ast.Concat(left, self.parse_additive())
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
            name = self.name_use(self.advance())
            if self.accept("["):
                return ast.Index(name, self.parse_expr_list_in_brackets())
            return ast.Var(name)
        if self.accept("$"):
            return ast.Field(self.parse_field_operand())
        return None

    def parse_field_operand(self) -> ast.Expr:
        """What follows ``$``.  It binds tightly, so ``$NF-1`` is ``($NF) - 1``
        and ``$i++`` increments the field, not i.  Two less obvious cases,
        where all three references agree:

        * after a unary operator the operand runs on to a unary expression,
          increments included: ``$-i++`` is ``$(-(i++))`` and ``$-x^2`` is
          ``$(-(x^2))``, but ``$-x*2`` is ``($(-x)) * 2``;
        * a nested ``$`` takes a following ``++``/``--`` itself:
          ``$$a++`` is ``$(($a)++)`` and ``$$a++++`` is ``$(($a)++)++``
          (the outer increment is applied by parse_postfix).
        """
        if self.at("++", "--"):
            return self.parse_postfix()
        if self.at("-", "+", "!"):
            op = self.advance().type
            return ast.Unary(op, self.parse_unary())
        if self.accept("$"):
            inner = ast.Field(self.parse_field_operand())
            if self.at("++", "--"):
                return ast.IncDec(self.advance().type, False, inner)
            return inner
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
            self.name_use(t)
            if self.accept("["):
                return ast.Index(t.value, self.parse_expr_list_in_brackets())
            return ast.Var(t.value)
        if tt == "FUNC_NAME":
            self.advance()
            args = self.parse_call_args()
            self.calls.append((t.value, len(args), t.line))
            return ast.UserCall(t.value, args)
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
        return ast.Call(name, self.parse_call_args())

    def parse_call_args(self) -> list[ast.Expr]:
        """``( [expr, expr, ...] )`` after a builtin or user function name.

        Inside the parentheses ``>`` is a comparison again, even within a
        print statement, and newlines may follow the commas.
        """
        self.expect("(")
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
        return args


def parse(source: str) -> ast.Program:
    """Entry point: program text -> tree.  Raises AwkSyntaxError if malformed."""
    return Parser(tokenize(source)).parse_program()
