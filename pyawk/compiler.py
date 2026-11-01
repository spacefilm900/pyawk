"""Compile the AST into Python closures, once, before the program runs.

Role in the pipeline: cli -> lexer -> parser -> AST -> **compiler** ->
runtime.  ``Compiler(interp).compile_program(program)`` turns every node of
the tree into a small Python function, and the runtime calls those.

Why compile at all?
-------------------
The first version of pyawk was a *tree-walker*: to evaluate ``x + 1`` it
looked at the node's type, found the matching method in a table, and that
method evaluated the two children the same way.  All of that looking-up
(``type(node)``, a dict lookup, reading ``node.op``, comparing it with
"+", "-", ...) happened again every time the expression ran: a million
records meant a million identical decisions.

Compiling makes each decision once.  ``x + 1`` becomes::

    def add():
        a = left()                        # the closure compiled for x
        return (a if type(a) is float else to_num(a)) + 1.0

and the runtime simply calls ``add()``.  The work that remains is only
the work the expression really needs.

Closures
--------
A *closure* is a function defined inside another function that keeps
using the outer function's local variables after the outer one has
returned.  In ``_binary`` below, the inner ``add`` refers to ``left`` and
``c``; Python stores those values with the ``add`` function object, so each
compiled ``+`` carries its own operands.  Reading such a captured variable
is as fast as reading a local one, much faster than ``self.x`` or
``node.op``.  Every ``def`` inside a ``_something`` method here makes one
closure per AST node it compiles.

The three kinds of compiled function:

* an **expression** (``Thunk``) takes no arguments and returns an AWK
  value (float, str or StrNum);
* a **condition** (``CondFn``) returns a Python bool directly; ``if``,
  ``while``, patterns, ``&&`` and ``!`` use these, which saves turning
  True into 1.0 and back again;
* a **statement** (``StmtFn``) returns None normally, or a *signal* (see
  below) for ``break``, ``continue``, ``next``, ``nextfile`` and ``return``.

Control flow as return values
-----------------------------
``break`` must leave the enclosing loop, which may be several nested
statements away.  The tree-walker raised an exception for it.  Here a
statement returns a signal object instead (``BREAK``, ``CONTINUE``,
``NEXT``, ``NEXTFILE`` or ``RETURN``), and every block and loop passes on
a signal it does not handle.  The parser guarantees that
``break``/``continue`` occur only inside loops, ``return`` only inside
functions and ``nextfile`` never in a function, so those signals only
travel through statements, never through an expression.  ``next`` *may*
appear in a function (POSIX allows it), and the call can be in the middle
of an expression; so a function body that ends with NEXT raises
``NextFromFunction`` instead, which the main loop catches.  ``exit`` and
errors also raise exceptions, for the same reason.

Local variables
---------------
A function's parameters are known when it is compiled, so each one gets a
fixed position.  A running call's local variables are a Python list of
``Cell`` objects (``interp.frame``); a local is read as
``interp.frame[position]``, with no dictionary lookup by name.  Every other
name is global and lives in ``interp.vars`` (scalars) or
``interp.arrays`` (arrays).
"""

# Treat type hints as text, so newer syntax such as ``X | None`` is allowed.
from __future__ import annotations

import math
from typing import TYPE_CHECKING, Any, Callable

from . import ast
from .builtins import BUILTINS, format_printf
from .errors import AwkRuntimeError
from .regex import LongestPattern, compile_span, matcher
from .regex import compile_ere as _compile_test
from .values import UNINIT, StrNum, Value, make_strnum, num_to_str, to_num, to_str

if TYPE_CHECKING:  # pragma: no cover - only for type checkers (avoids a cycle)
    import re

    from .runtime import Cell, Interpreter

# Types of the compiled functions (see the module docstring).
Thunk = Callable[[], Value]
CondFn = Callable[[], bool]
StmtFn = Callable[[], object]


class Signal:
    """A control-flow signal returned by a statement (see the docstring)."""

    __slots__ = ("name",)

    def __init__(self, name: str) -> None:
        self.name = name

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return self.name


# There is exactly one object of each; tests are ``sig is BREAK``.
BREAK = Signal("break")
CONTINUE = Signal("continue")
NEXT = Signal("next")
NEXTFILE = Signal("nextfile")
# ``return``: the value travels in ``interp.ret_value``, set just before.
RETURN = Signal("return")


class NextFromFunction(Exception):
    """``next`` executed inside a user-defined function.

    A plain NEXT signal cannot leave a function, because the call may be
    part of an expression (``x = f() + 1``).  So the call raises this
    instead; Interpreter._main_loop catches it and moves on to the next
    record, and Interpreter._run_special turns it into an error in BEGIN
    or END, where there is no record to skip.
    """


def _raiser(msg: str) -> Thunk:
    """A compiled expression that fails with ``msg`` when (and if) it runs.

    AWK reports many mistakes only when the faulty code is executed (a call
    of an undefined function in a branch never taken is fine), so the
    compiler turns them into a function that raises later.
    """

    def fail() -> Value:
        raise AwkRuntimeError(msg)

    return fail


def _const(v: Any) -> Callable[[], Any]:
    def const() -> Any:
        return v

    return const


def _div(x: float, y: float) -> float:
    if y == 0.0:
        raise AwkRuntimeError("division by zero")
    return x / y


def _mod(x: float, y: float) -> float:
    # fmod: the result has the sign of x, as in C (Python's % differs).
    if y == 0.0:
        raise AwkRuntimeError("division by zero in %")
    return math.fmod(x, y)


def _pow(x: float, y: float) -> float:
    # math.pow raises where C returns inf or NaN.
    try:
        return math.pow(x, y)
    except OverflowError:
        return math.inf
    except ValueError:
        return math.nan


def _add(x: float, y: float) -> float:
    return x + y


def _sub(x: float, y: float) -> float:
    return x - y


def _mul(x: float, y: float) -> float:
    return x * y


# The arithmetic operators as two-argument functions.
ARITH: dict[str, Callable[[float, float], float]] = {
    "+": _add, "-": _sub, "*": _mul, "/": _div, "%": _mod, "^": _pow,
}


def _flatten_concat(node: ast.Expr, out: list[ast.Expr]) -> None:
    """``a b c`` parses as Concat(Concat(a, b), c); collect [a, b, c]."""
    # A loop down the left spine (not recursion) copes with long chains.
    rights: list[ast.Expr] = []
    while type(node) is ast.Concat:
        rights.append(node.right)  # type: ignore[attr-defined]
        node = node.left  # type: ignore[attr-defined]
    out.append(node)
    out.extend(reversed(rights))


def _field_number(n: float) -> int:
    """The field number for ``$n``; NaN and infinity are errors."""
    try:
        return int(n)
    except (ValueError, OverflowError):
        raise AwkRuntimeError("invalid field index") from None


def _int_const(node: ast.Expr) -> int | None:
    """The value of a Num node that holds a small whole number, else None."""
    if type(node) is ast.Num:
        v = node.value  # type: ignore[attr-defined]
        if v == v and v not in (math.inf, -math.inf) and v == int(v) and abs(v) < 2**53:
            return int(v)
    return None


class Compiler:
    """Turns AST nodes into closures that run against one Interpreter."""

    def __init__(self, it: "Interpreter") -> None:
        self.it = it
        # While compiling a function body: parameter name -> position in
        # the frame list.  None while compiling top-level code.
        self.locals: dict[str, int] | None = None
        # name -> [compiled body]; a one-element list is filled in after
        # the body is compiled, so a function can call itself (the call
        # site is compiled before the body it calls is finished).
        self.bodies: dict[str, list[StmtFn]] = {}
        vars_ = it.vars
        self.vars = vars_

        # String value of v, converting numbers with CONVFMT.  Used by
        # nearly everything, so it is a plain function (no ``self``).
        def sval(v: Value) -> str:
            t = type(v)
            if t is str:
                return v  # type: ignore[return-value]
            if t is StrNum:
                return v.s  # type: ignore[union-attr]
            return num_to_str(v, to_str(vars_["CONVFMT"]))  # type: ignore[arg-type]

        self.sval = sval

    # ------------------------------------------------------------ program
    def compile_program(self, prog: ast.Program) -> "CompiledProgram":
        for name in prog.functions:
            self.bodies[name] = []
        begin = [self.block(b) for b in prog.begin]
        rules = [self.rule(r) for r in prog.rules]
        end = [self.block(b) for b in prog.end]
        for name, fn in prog.functions.items():
            # ``{p: i for ...}`` is a dict comprehension: param -> position.
            # With a repeated name the last position wins, as before.
            self.locals = {p: i for i, p in enumerate(fn.params)}
            self.bodies[name].append(self.block(fn.body))
            self.locals = None
        return CompiledProgram(begin, rules, end)

    def rule(self, rule: ast.Rule) -> tuple[CondFn | None, CondFn | None, StmtFn | None]:
        pat = None if rule.pattern is None else self.cond(rule.pattern)
        end = None if rule.end is None else self.cond(rule.end)
        action = None if rule.action is None else self.block(rule.action)
        return pat, end, action

    # ------------------------------------------------------------ statements
    def stmt(self, node: ast.Stmt) -> StmtFn:
        method: Callable[[Any], StmtFn] = getattr(self, "_s_" + type(node).__name__)
        return method(node)

    def block(self, node: ast.Block) -> StmtFn:
        return self._s_Block(node)

    def _s_Block(self, node: ast.Block) -> StmtFn:
        stmts = [self.stmt(s) for s in node.body]
        if not stmts:
            return _const(None)
        if len(stmts) == 1:
            return stmts[0]  # a block of one statement is that statement
        if len(stmts) == 2:
            s1, s2 = stmts

            def block2() -> object:
                sig = s1()
                if sig is not None:
                    return sig
                return s2()

            return block2

        def block() -> object:
            for s in stmts:
                sig = s()
                if sig is not None:
                    return sig  # pass break/continue/next/return outwards
            return None

        return block

    def _s_ExprStmt(self, node: ast.ExprStmt) -> StmtFn:
        if type(node.expr) is ast.Assign and (stmt := self._assign_stmt(node.expr)) is not None:
            return stmt
        e = self.expr(node.expr, value_used=False)
        if type(node.expr) is ast.IncDec:
            # ``i++`` compiled with value_used=False already returns None,
            # so it can serve as the statement itself: one call fewer.
            return e  # type: ignore[return-value]

        def expr_stmt() -> None:
            e()

        return expr_stmt

    def _assign_stmt(self, node: ast.Assign) -> StmtFn | None:
        """The statements ``x = e``, ``x += e``, ``a[k] = e``, ``a[k] += e``
        (and the other ``op=``) as closures that return None, so they can
        be statements directly, without the expr_stmt wrapper.  Same
        evaluation order as the expression forms in _e_Assign.  Returns
        None for other targets, which keep the general path."""
        target, tt = node.target, type(node.target)
        op = node.op
        if tt is ast.Var and self._is_plain_global(target.name):  # type: ignore[attr-defined]
            name, vars_ = target.name, self.vars  # type: ignore[attr-defined]
            arrays, new_global = self.it.arrays, self.it.new_global_scalar
            value = self.expr(node.value)
            if op == "=":

                def assign_global_stmt() -> None:
                    v = value()
                    if name in arrays:
                        raise AwkRuntimeError(f"can't assign to {name}; it's an array name")
                    vars_[name] = v

                return assign_global_stmt  # type: ignore[return-value]
            if op == "+=":

                def add_global_stmt() -> None:
                    # ``total += x``: the commonest compound assignment.
                    rhs = value()
                    if type(rhs) is not float:
                        rhs = to_num(rhs)
                    try:
                        cur = vars_[name]
                    except KeyError:
                        cur = new_global(name)  # raises if name is an array
                    vars_[name] = (cur if type(cur) is float else to_num(cur)) + rhs  # type: ignore[operator]

                return add_global_stmt  # type: ignore[return-value]
            fn = ARITH[op[0]]

            def assign_op_global_stmt() -> None:
                rhs = value()
                if type(rhs) is not float:
                    rhs = to_num(rhs)
                try:
                    cur = vars_[name]
                except KeyError:
                    cur = new_global(name)
                vars_[name] = fn(cur if type(cur) is float else to_num(cur), rhs)  # type: ignore[arg-type]

            return assign_op_global_stmt  # type: ignore[return-value]
        if tt is ast.Index:
            get_arr, key = self.array(target.name), self.subscript(target.subs)  # type: ignore[attr-defined]
            value = self.expr(node.value)
            if op == "=":

                def assign_elem_stmt() -> None:
                    arr = get_arr()
                    k = key()
                    arr[k] = value()

                return assign_elem_stmt  # type: ignore[return-value]
            fn = ARITH[op[0]]

            def assign_op_elem_stmt() -> None:
                arr = get_arr()
                k = key()
                rhs = to_num(value())
                cur = arr.get(k, UNINIT)
                arr[k] = fn(cur if type(cur) is float else to_num(cur), rhs)  # type: ignore[arg-type]

            return assign_op_elem_stmt  # type: ignore[return-value]
        return None

    def _output_stream(self, node: ast.Print | ast.Printf) -> Callable[[], Any]:
        """Closure giving the stream a print/printf writes to."""
        it = self.it
        if node.redirect is None:
            out = it.out
            return lambda: out
        assert node.dest is not None
        dest, kind, sval, io = self.expr(node.dest), node.redirect, self.sval, it.io
        return lambda: io.get_output(kind, sval(dest()))

    def _s_Print(self, node: ast.Print) -> StmtFn:
        vars_, sval, it = self.vars, self.sval, self.it

        # print converts numbers with OFMT rather than CONVFMT.
        def ostr(v: Value) -> str:
            if type(v) is float:
                return num_to_str(v, to_str(vars_["OFMT"]))
            return sval(v)

        args = [self.expr(a) for a in node.args]
        if node.redirect is None:
            write = it.out.write  # a bound method: calling it needs no lookup
            if not args:

                def print_record() -> None:
                    ors = vars_["ORS"]
                    write(it.record + (ors if type(ors) is str else sval(ors)))

                return print_record
            if len(args) == 1:
                (a,) = args

                def print1() -> None:
                    v = a()
                    text = v if type(v) is str else ostr(v)
                    ors = vars_["ORS"]
                    write(text + (ors if type(ors) is str else sval(ors)))  # type: ignore[operator]

                return print1

            def print_n() -> None:
                ofs = vars_["OFS"]
                if type(ofs) is not str:
                    ofs = sval(ofs)
                text = ofs.join([ostr(a()) for a in args])  # type: ignore[union-attr]
                ors = vars_["ORS"]
                write(text + (ors if type(ors) is str else sval(ors)))

            return print_n

        stream = self._output_stream(node)

        def print_to() -> None:
            # Same order as above: OFS, the arguments, then the destination.
            if args:
                ofs = sval(vars_["OFS"])
                text = ofs.join([ostr(a()) for a in args])
            else:
                text = it.record
            s = stream()
            s.write(text + sval(vars_["ORS"]))

        return print_to

    def _s_Printf(self, node: ast.Printf) -> StmtFn:
        stream = self._output_stream(node)
        if not node.args:
            # A bare ``printf`` prints nothing, but still opens a redirection.
            def printf_nothing() -> None:
                stream()

            return printf_nothing
        args = [self.expr(a) for a in node.args]
        sval, vars_ = self.sval, self.vars

        def printf() -> None:
            vals = [a() for a in args]
            text = format_printf(sval(vals[0]), vals[1:], to_str(vars_["CONVFMT"]))
            stream().write(text)

        return printf

    def _s_If(self, node: ast.If) -> StmtFn:
        test, then = self.cond(node.test), self.stmt(node.then)
        if node.orelse is None:

            def if_() -> object:
                if test():
                    return then()
                return None

            return if_
        orelse = self.stmt(node.orelse)

        def if_else() -> object:
            if test():
                return then()
            return orelse()

        return if_else

    # Loops: a body's signal is handled here if it is break or continue;
    # anything else (next, return) is passed outwards.
    def _s_While(self, node: ast.While) -> StmtFn:
        test, body = self.cond(node.test), self.stmt(node.body)

        def while_() -> object:
            while test():
                sig = body()
                if sig is not None:
                    if sig is BREAK:
                        break
                    if sig is not CONTINUE:
                        return sig
            return None

        return while_

    def _s_DoWhile(self, node: ast.DoWhile) -> StmtFn:
        test, body = self.cond(node.test), self.stmt(node.body)

        def do_while() -> object:
            while True:
                sig = body()
                if sig is not None:
                    if sig is BREAK:
                        break
                    if sig is not CONTINUE:
                        return sig
                if not test():
                    break
            return None

        return do_while

    def _for_clause(self, node: ast.Expr | ast.Stmt | None) -> Callable[[], object] | None:
        # init and step are usually expressions, but may be print, printf
        # or delete statements (see Parser.parse_for_clause).
        if node is None:
            return None
        if isinstance(node, ast.Stmt):
            return self.stmt(node)
        return self.expr(node, value_used=False)

    def _s_For(self, node: ast.For) -> StmtFn:
        init, step = self._for_clause(node.init), self._for_clause(node.step)
        test = self.cond(node.test) if node.test is not None else _const(True)
        body = self.stmt(node.body)

        def for_() -> object:
            if init is not None:
                init()
            while test():
                sig = body()
                if sig is not None:
                    if sig is BREAK:
                        break
                    if sig is not CONTINUE:
                        return sig
                if step is not None:
                    step()
            return None

        return for_

    def _s_ForIn(self, node: ast.ForIn) -> StmtFn:
        get_arr, body = self.array(node.array), self.stmt(node.body)
        if type(node.var) is ast.Var:
            assign = self.scalar_setter(node.var.name)  # type: ignore[attr-defined]
        else:
            resolve, set_ref = self.ref(node.var), self.set_ref

            def assign(key: Value) -> None:
                set_ref(resolve(), key)

        def for_in() -> object:
            arr = get_arr()
            # Loop over a snapshot of the keys, since the body may add or
            # delete elements.  (The order is unspecified in AWK; ours is
            # insertion order.)
            for key in list(arr):
                if key not in arr:
                    continue  # deleted during the loop
                assign(key)
                sig = body()
                if sig is not None:
                    if sig is BREAK:
                        break
                    if sig is not CONTINUE:
                        return sig
            return None

        return for_in

    def _s_Break(self, node: ast.Break) -> StmtFn:
        return _const(BREAK)

    def _s_Continue(self, node: ast.Continue) -> StmtFn:
        return _const(CONTINUE)

    def _s_Next(self, node: ast.Next) -> StmtFn:
        return _const(NEXT)

    def _s_NextFile(self, node: ast.NextFile) -> StmtFn:
        return _const(NEXTFILE)

    def _s_Return(self, node: ast.Return) -> StmtFn:
        it = self.it
        # Evaluating ``return arr`` for an array fails in the scalar getter,
        # as it should: functions can only return scalars.
        value = self.expr(node.value) if node.value is not None else _const(UNINIT)

        def return_() -> object:
            it.ret_value = value()
            return RETURN

        return return_

    def _s_Exit(self, node: ast.Exit) -> StmtFn:
        # Imported here: runtime.py imports this module.
        from .runtime import ExitEx

        it = self.it
        status = None if node.status is None else self.expr(node.status)

        def exit_() -> object:
            # Bare ``exit`` keeps any status set by an earlier ``exit n``
            # (e.g. ``exit 3`` in a rule, then a plain ``exit`` in END).
            # ``& 0xFF`` keeps the low 8 bits, as the operating system does.
            code = it.exit_code
            if status is not None:
                code = int(to_num(status())) & 0xFF
            raise ExitEx(code)

        return exit_

    def _s_Delete(self, node: ast.Delete) -> StmtFn:
        get_arr = self.array(node.array)
        if node.subs is None:

            def delete_all() -> None:
                get_arr().clear()

            return delete_all
        key = self.subscript(node.subs)

        def delete() -> None:
            arr = get_arr()
            arr.pop(key(), None)

        return delete

    # ------------------------------------------------------------ conditions
    def cond(self, node: ast.Expr) -> CondFn:
        """Compile an expression used as a truth value; returns a bool."""
        t = type(node)
        if t is ast.Regex:
            # A bare /regex/ as a condition means ``$0 ~ /regex/``.
            it = self.it
            static, dynamic = self.tester(node)
            if static is not None:
                return lambda: static(it.record) is not None
            return lambda: dynamic()(it.record) is not None
        if t is ast.Compare:
            return self._compare(node)  # type: ignore[arg-type]
        if t is ast.And:
            left, right = self.cond(node.left), self.cond(node.right)  # type: ignore[attr-defined]
            # Python's ``and``/``or`` short-circuit like AWK's && and ||.
            return lambda: left() and right()
        if t is ast.Or:
            left, right = self.cond(node.left), self.cond(node.right)  # type: ignore[attr-defined]
            return lambda: left() or right()
        if t is ast.Unary and node.op == "!":  # type: ignore[attr-defined]
            inner = self.cond(node.operand)  # type: ignore[attr-defined]
            return lambda: not inner()
        if t is ast.Match:
            return self._match(node)  # type: ignore[arg-type]
        if t is ast.In:
            return self._in(node)  # type: ignore[arg-type]
        if t is ast.Num:
            return _const(node.value != 0.0)  # type: ignore[attr-defined]
        e = self.expr(node)

        def truth() -> bool:
            # AWK truth (values.to_bool, inlined): numbers are true if
            # non-zero, strings if non-empty, strnums count as numbers.
            v = e()
            t = type(v)
            if t is float:
                return v != 0.0
            if t is StrNum:
                return v.n != 0.0  # type: ignore[union-attr]
            return v != ""

        return truth

    def _bool_value(self, node: ast.Expr) -> Thunk:
        """A condition used as a value: 1 or 0."""
        c = self.cond(node)
        return lambda: 1.0 if c() else 0.0

    def _compare(self, node: ast.Compare) -> CondFn:
        """``<`` ``<=`` ``==`` ``!=`` ``>`` ``>=`` as a bool.

        Numeric if neither side is a plain string (numbers and strnums are
        both numeric), else a string comparison.  NaN compares unequal to
        everything and is "greater" for ordering (as values.compare does):
        that is why ``>`` is written ``not x <= y`` (true when NaN is
        involved) and ``>=`` as ``not x < y``.
        """
        op = node.op
        left, sval = self.expr(node.left), self.sval
        if op == "<":
            test = _lt
        elif op == "<=":
            test = _le
        elif op == ">":
            test = _gt
        elif op == ">=":
            test = _ge
        elif op == "==":
            test = _eq
        else:
            test = _ne
        if type(node.right) is ast.Num:
            # Comparing with a number constant: the right side is known to
            # be numeric, so only the left side's type matters.  Its string
            # form (needed when the left side is a string) is fixed for
            # whole numbers and depends on CONVFMT otherwise.
            c = node.right.value  # type: ignore[attr-defined]
            k = _int_const(node.right)
            c_str = str(k) if k is not None and abs(k) < 2**63 else None
            vars_ = self.vars

            def compare_const() -> bool:
                a = left()
                ta = type(a)
                if ta is float:
                    return test(a, c)
                if ta is StrNum:
                    return test(a.n, c)  # type: ignore[union-attr]
                s = c_str if c_str is not None else num_to_str(c, to_str(vars_["CONVFMT"]))
                return test(a, s)

            return compare_const
        right = self.expr(node.right)

        def compare() -> bool:
            a = left()
            b = right()
            if type(a) is float and type(b) is float:  # the common case first
                return test(a, b)
            if type(a) is str or type(b) is str:
                return test(sval(a), sval(b))
            return test(
                a if type(a) is float else a.n,  # type: ignore[union-attr]
                b if type(b) is float else b.n,  # type: ignore[union-attr]
            )

        return compare

    def _match(self, node: ast.Match) -> CondFn:
        left = self.string(node.left)
        static, dynamic = self.tester(node.regex)
        # The regex operand is evaluated before the left one, as before.
        if static is not None:
            if node.negate:
                return lambda: static(left()) is None
            return lambda: static(left()) is not None
        if node.negate:
            return lambda: dynamic()(left()) is None
        return lambda: dynamic()(left()) is not None

    def _in(self, node: ast.In) -> CondFn:
        # ``(k in arr)`` tests membership without creating the element.
        # The key is computed before the array is looked up.
        key, arr = self.subscript(node.subs), self.array(node.array)

        def in_() -> bool:
            k = key()
            return k in arr()

        return in_

    def tester(self, node: ast.Expr) -> tuple[Callable[[str], object] | None, Callable[[], Callable[[str], object]]]:
        """Compile the right side of ``~`` (or a bare /regex/ condition).

        The result is used as ``test(text) is not None``.  For a /literal/
        that is ``(test, ...)``, with ``test`` fixed now: regex.matcher picks
        Python's search or the automaton (see regex.py, "backtracking
        check").  Otherwise ``(None, get)``: ``get()`` evaluates the regex
        expression and returns the test for it, remembering the last one.
        """
        if type(node) is ast.Regex:
            try:
                test = matcher(_compile_test(node.source))  # type: ignore[attr-defined]
                return test, lambda: test
            except AwkRuntimeError:
                pass  # a bad regex is reported when it is used, not before
        pat = self.regex(node)
        last_pattern: object = None
        last_test: Callable[[str], object] | None = None

        def get() -> Callable[[str], object]:
            nonlocal last_pattern, last_test
            p = pat()
            if p is not last_pattern:
                last_test = matcher(p)
                last_pattern = p
            return last_test  # type: ignore[return-value]

        return None, get

    def regex(self, node: ast.Expr, span: bool = False) -> Callable[[], "re.Pattern[str] | LongestPattern"]:
        """A closure giving the compiled regex for a match operand.

        A /literal/ is compiled now, once.  Any other expression's string
        value is used as a regex ("dynamic regex"); the last one compiled
        at this place in the program is remembered, since it is usually
        the same string every time.

        ``span=True`` is for callers that need the exact extent of a match
        (match(), sub, gsub): they get regex.compile_span's POSIX
        leftmost-longest matcher; ``~`` only needs yes/no (regex.compile_ere).
        """
        compile_fn = compile_span if span else _compile_test
        if type(node) is ast.Regex:
            source = node.source  # type: ignore[attr-defined]
            try:
                pattern = compile_fn(source)
            except AwkRuntimeError:
                # A bad regex is reported when it is used, not before.
                return lambda: compile_fn(source)
            return _const(pattern)
        e = self.string(node)
        last_source: str | None = None
        last_pattern: re.Pattern[str] | LongestPattern | None = None

        def dynamic() -> re.Pattern[str] | LongestPattern:
            # ``nonlocal``: assign to the enclosing function's variables
            # (without it, assigning would make new local ones).
            nonlocal last_source, last_pattern
            s = e()
            if s != last_source:
                last_pattern = compile_fn(s)
                last_source = s
            return last_pattern  # type: ignore[return-value]

        return dynamic

    # ------------------------------------------------------------ expressions
    def expr(self, node: ast.Expr, value_used: bool = True) -> Thunk:
        """Compile an expression.  With ``value_used=False`` (an expression
        statement) the result may be anything, which lets ``i++`` skip
        computing the old value."""
        t = type(node)
        if t is ast.IncDec:
            return self._incdec(node, value_used)  # type: ignore[arg-type]
        method: Callable[[Any], Thunk] = getattr(self, "_e_" + t.__name__)
        return method(node)

    def _e_Num(self, node: ast.Num) -> Thunk:
        return _const(node.value)

    def _e_Str(self, node: ast.Str) -> Thunk:
        return _const(node.value)

    def _e_Regex(self, node: ast.Regex) -> Thunk:
        return self._bool_value(node)  # a bare /re/ as a value: ($0 ~ /re/)

    def _e_Group(self, node: ast.Group) -> Thunk:
        return _raiser("parenthesised expression list used as a value")

    def _e_Var(self, node: ast.Var) -> Thunk:
        return self.scalar_getter(node.name)

    def _e_Index(self, node: ast.Index) -> Thunk:
        get_arr, key = self.array(node.name), self.subscript(node.subs)

        def index() -> Value:
            arr = get_arr()
            k = key()
            # ``try``/``except KeyError`` is the fast way to read a dict
            # entry that is usually there.  Reading arr[k] creates the
            # element if missing (AWK rule).
            try:
                return arr[k]
            except KeyError:
                arr[k] = UNINIT
                return UNINIT

        return index

    def _e_Field(self, node: ast.Field) -> Thunk:
        it = self.it
        k = _int_const(node.index)
        if k is not None and k > 0:
            i = k - 1  # Python lists count from 0, AWK fields from 1

            def field_const() -> Value:
                # Interpreter.get_field, inlined for the common ``$3``.
                fields = it.fields
                if fields is None:
                    fields = it.split_record()
                if i < len(fields):
                    v = fields[i]
                    if v is None:
                        v = fields[i] = make_strnum(it.field_text[i])
                    return v
                return ""  # a field past NF is the empty string

            return field_const
        if k is not None:  # $0, or a negative constant (an error when run)
            get_field, index = it.get_field, self._field_index(node.index)
            return lambda: get_field(index())
        e, get_field = self.expr(node.index), it.get_field

        def field_var() -> Value:
            # ``$i``: like field_const, with the index computed each time.
            n = e()
            if type(n) is not float:
                n = to_num(n)
            if 1.0 <= n < 1e9:  # type: ignore[operator]  # False for NaN
                i = int(n) - 1  # type: ignore[arg-type]
                fields = it.fields
                if fields is None:
                    fields = it.split_record()
                if i < len(fields):
                    v = fields[i]
                    if v is None:
                        v = fields[i] = make_strnum(it.field_text[i])
                    return v
                return ""
            # $0, a negative index, NaN, infinity: the general path.
            return get_field(_field_number(n))  # type: ignore[arg-type]

        return field_var

    def string(self, node: ast.Expr) -> Callable[[], str]:
        """Closure giving the *string* value of ``node``.

        Many places only want a string: array subscripts, ``length``,
        regex matching, concatenation.  For a field (``$3`` or ``$i``) the
        string is simply the field's text, so we can skip deciding whether
        it looks like a number (the strnum check that reading a field as a
        value needs).  For anything else this is ``sval(expr())`` with the
        conversion written out, so it costs no extra call.
        """
        if type(node) is ast.Str:
            return _const(node.value)  # type: ignore[attr-defined]
        sval, vars_ = self.sval, self.vars
        if type(node) is ast.Field:
            it = self.it
            k = _int_const(node.index)
            if k is not None and k > 0:
                i = k - 1

                def field_const_str() -> str:
                    fields = it.fields
                    if fields is None:
                        fields = it.split_record()
                    if i < len(fields):
                        v = fields[i]
                        if v is None:  # not classified yet: its text is the string
                            return it.field_text[i]
                        return v if type(v) is str else sval(v)  # type: ignore[return-value]
                    return ""

                return field_const_str
            if k is None:
                e, get_field = self.expr(node.index), it.get_field

                def field_var_str() -> str:
                    n = e()
                    if type(n) is not float:
                        n = to_num(n)
                    if 1.0 <= n < 1e9:  # type: ignore[operator]  # False for NaN
                        i = int(n) - 1  # type: ignore[arg-type]
                        fields = it.fields
                        if fields is None:
                            fields = it.split_record()
                        if i < len(fields):
                            v = fields[i]
                            if v is None:
                                return it.field_text[i]
                            return v if type(v) is str else sval(v)  # type: ignore[return-value]
                        return ""
                    return sval(get_field(_field_number(n)))  # type: ignore[arg-type]

                return field_var_str
        e = self.expr(node)

        def string() -> str:
            v = e()
            t = type(v)
            if t is str:
                return v  # type: ignore[return-value]
            if t is StrNum:
                return v.s  # type: ignore[union-attr]
            return num_to_str(v, to_str(vars_["CONVFMT"]))  # type: ignore[arg-type]

        return string

    def _field_index(self, node: ast.Expr) -> Callable[[], int]:
        if (k := _int_const(node)) is not None:  # ``:=`` assigns inside an expression
            return _const(k)
        e = self.expr(node)

        def field_index() -> int:
            v = e()
            return _field_number(v if type(v) is float else to_num(v))  # type: ignore[arg-type]

        return field_index

    def _e_Assign(self, node: ast.Assign) -> Thunk:
        target, value = node.target, self.expr(node.value)
        tt = type(target)
        if node.op == "=":
            if tt is ast.Var:
                name = target.name  # type: ignore[attr-defined]
                if self._is_plain_global(name):
                    vars_, arrays = self.vars, self.it.arrays

                    def assign_global() -> Value:
                        v = value()
                        if name in arrays:
                            raise AwkRuntimeError(f"can't assign to {name}; it's an array name")
                        vars_[name] = v
                        return v

                    return assign_global
                setter = self.scalar_setter(name)

                def assign_var() -> Value:
                    v = value()
                    setter(v)
                    return v

                return assign_var
            if tt is ast.Index:
                get_arr, key = self.array(target.name), self.subscript(target.subs)  # type: ignore[attr-defined]

                def assign_elem() -> Value:
                    # Array, then key, then value: the order AWK specifies.
                    arr = get_arr()
                    k = key()
                    arr[k] = v = value()
                    return v

                return assign_elem
            if tt is ast.Field:
                index, set_field = self._field_index(target.index), self.it.set_field  # type: ignore[attr-defined]

                def assign_field() -> Value:
                    i = index()
                    v = value()
                    set_field(i, v)
                    return v

                return assign_field
            return _raiser("assignment to non-lvalue")
        # Compound assignment ``x op= e``.  The target's location (array
        # and key, or field number) is found first; then the right-hand side
        # is evaluated *before* the target is read (as in gawk, mawk and
        # BWK awk: ``x += x += 2`` with x == 1 gives 6).
        fn = ARITH[node.op[0]]
        if tt is ast.Var and self._is_plain_global(target.name):  # type: ignore[attr-defined]
            # ``total += x`` on a global: the common case, inlined.
            name, vars_, new_global = target.name, self.vars, self.it.new_global_scalar  # type: ignore[attr-defined]

            def assign_op_global() -> Value:
                rhs = value()
                if type(rhs) is not float:
                    rhs = to_num(rhs)
                cur = vars_.get(name)
                if cur is None:
                    cur = new_global(name)  # raises if name is an array
                vars_[name] = v = fn(cur if type(cur) is float else to_num(cur), rhs)  # type: ignore[arg-type]
                return v

            return assign_op_global
        if tt is ast.Index:
            get_arr, key = self.array(target.name), self.subscript(target.subs)  # type: ignore[attr-defined]

            def assign_op_elem() -> Value:
                arr = get_arr()
                k = key()
                rhs = to_num(value())
                cur = arr.get(k, UNINIT)
                arr[k] = v = fn(cur if type(cur) is float else to_num(cur), rhs)  # type: ignore[arg-type]
                return v

            return assign_op_elem
        resolve, get_ref, set_ref = self.ref(target), self.get_ref, self.set_ref

        def assign_op() -> Value:
            ref = resolve()
            rhs = to_num(value())
            v = fn(to_num(get_ref(ref)), rhs)
            set_ref(ref, v)
            return v

        return assign_op

    def _e_Cond(self, node: ast.Cond) -> Thunk:
        test, yes, no = self.cond(node.test), self.expr(node.if_true), self.expr(node.if_false)
        return lambda: yes() if test() else no()

    def _e_Or(self, node: ast.Or) -> Thunk:
        return self._bool_value(node)

    def _e_And(self, node: ast.And) -> Thunk:
        return self._bool_value(node)

    def _e_In(self, node: ast.In) -> Thunk:
        return self._bool_value(node)

    def _e_Match(self, node: ast.Match) -> Thunk:
        return self._bool_value(node)

    def _e_Compare(self, node: ast.Compare) -> Thunk:
        return self._bool_value(node)

    def _e_Concat(self, node: ast.Concat) -> Thunk:
        nodes: list[ast.Expr] = []
        _flatten_concat(node, nodes)
        parts = [self.string(n) for n in nodes]
        if len(parts) == 2:
            a, b = parts
            return lambda: a() + b()
        # Each part is converted to a string right after it is evaluated
        # (CONVFMT could change in between), then all are joined at once.
        return lambda: "".join([p() for p in parts])

    def _e_Binary(self, node: ast.Binary) -> Thunk:
        left = self.expr(node.left)
        op = node.op
        if type(node.right) is ast.Num and (op in "+-*" or (op in "/%" and node.right.value != 0.0)):  # type: ignore[attr-defined]
            c = node.right.value  # type: ignore[attr-defined]
            # Specialised closures for ``x + 1`` and friends.  Converting
            # a value to a number is inlined: floats (the usual case) skip
            # the call to to_num.
            if op == "+":
                def add_const() -> Value:
                    a = left()
                    return (a if type(a) is float else to_num(a)) + c  # type: ignore[operator]
                return add_const
            if op == "-":
                def sub_const() -> Value:
                    a = left()
                    return (a if type(a) is float else to_num(a)) - c  # type: ignore[operator]
                return sub_const

            if op == "/":  # c is not 0, so no check is needed
                def div_const() -> Value:
                    a = left()
                    return (a if type(a) is float else to_num(a)) / c  # type: ignore[operator]
                return div_const
            if op == "%":
                fmod = math.fmod  # a local name is found faster than math.fmod

                def mod_const() -> Value:
                    a = left()
                    return fmod(a if type(a) is float else to_num(a), c)  # type: ignore[arg-type]
                return mod_const

            def mul_const() -> Value:
                a = left()
                return (a if type(a) is float else to_num(a)) * c  # type: ignore[operator]
            return mul_const
        right = self.expr(node.right)
        if op == "+":
            def add() -> Value:
                a = left()
                b = right()
                return (a if type(a) is float else to_num(a)) + (b if type(b) is float else to_num(b))  # type: ignore[operator]
            return add
        if op == "-":
            def sub() -> Value:
                a = left()
                b = right()
                return (a if type(a) is float else to_num(a)) - (b if type(b) is float else to_num(b))  # type: ignore[operator]
            return sub
        if op == "*":
            def mul() -> Value:
                a = left()
                b = right()
                return (a if type(a) is float else to_num(a)) * (b if type(b) is float else to_num(b))  # type: ignore[operator]
            return mul
        fn = ARITH[op]

        def arith() -> Value:
            a = left()
            b = right()
            return fn(a if type(a) is float else to_num(a), b if type(b) is float else to_num(b))  # type: ignore[arg-type]

        return arith

    def _e_Unary(self, node: ast.Unary) -> Thunk:
        if node.op == "!":
            c = self.cond(node.operand)
            return lambda: 0.0 if c() else 1.0
        e = self.expr(node.operand)
        if node.op == "-":
            return lambda: -to_num(e())
        return lambda: to_num(e())

    def _incdec(self, node: ast.IncDec, value_used: bool) -> Thunk:
        """``++x`` gives the new value, ``x++`` the old one."""
        delta = 1.0 if node.op == "++" else -1.0
        # When the value is thrown away, prefix and postfix are the same.
        want_new = node.prefix or not value_used
        target = node.target
        if type(target) is ast.Var and self._is_plain_global(target.name):  # type: ignore[attr-defined]
            name, vars_, new_global = target.name, self.vars, self.it.new_global_scalar  # type: ignore[attr-defined]
            if not value_used:

                def incdec_global_stmt() -> None:
                    # The statement ``i++``: no result needed.
                    v = vars_.get(name)
                    if v is None:
                        v = new_global(name)
                    vars_[name] = (v if type(v) is float else to_num(v)) + delta  # type: ignore[operator]

                return incdec_global_stmt  # type: ignore[return-value]

            def incdec_global() -> Value:
                v = vars_.get(name)
                if v is None:
                    v = new_global(name)
                old = v if type(v) is float else to_num(v)
                # A name found in vars is never also an array, so no check.
                vars_[name] = new = old + delta  # type: ignore[operator]
                return new if want_new else old

            return incdec_global
        if type(target) is ast.Index:
            get_arr, key = self.array(target.name), self.subscript(target.subs)  # type: ignore[attr-defined]
            if not value_used:

                def incdec_elem_stmt() -> None:
                    # The statement ``count[$1]++``: no result needed.
                    arr = get_arr()
                    k = key()
                    v = arr.get(k, UNINIT)
                    arr[k] = (v if type(v) is float else to_num(v)) + delta  # type: ignore[operator]

                return incdec_elem_stmt  # type: ignore[return-value]

            def incdec_elem() -> Value:
                arr = get_arr()
                k = key()
                v = arr.get(k, UNINIT)
                old = v if type(v) is float else to_num(v)
                arr[k] = new = old + delta  # type: ignore[operator]
                return new if want_new else old

            return incdec_elem
        resolve, get_ref, set_ref = self.ref(target), self.get_ref, self.set_ref

        def incdec() -> Value:
            ref = resolve()
            old = to_num(get_ref(ref))
            new = old + delta
            set_ref(ref, new)
            return new if want_new else old

        if not value_used:
            # Statements must return None (anything else would look like
            # a break/continue/... signal to the enclosing block).
            def incdec_stmt() -> None:
                incdec()

            return incdec_stmt  # type: ignore[return-value]

        return incdec

    def _e_Call(self, node: ast.Call) -> Thunk:
        # Each builtin compiles its own call (see builtins.py).
        return BUILTINS[node.name](self, node.args)

    def _e_UserCall(self, node: ast.UserCall) -> Thunk:
        """Call a user-defined function.

        1. Evaluate the arguments left to right, in the *caller's* frame,
           making the callee's Cell for each parameter (see _binder).
           Extra arguments are evaluated (for their side effects) and
           dropped; the parser already warned about them.
        2. Parameters with no argument are the function's local variables:
           fresh, untyped cells.
        3. Run the body with the new frame.  ``return`` stores its value in
           ``it.ret_value`` and signals RETURN; falling off the end returns
           "uninitialized"; ``next`` raises NextFromFunction.
        4. Put the caller's frame back, however the body was left (return,
           exit, error): that is what ``finally`` guarantees.
        """
        from .runtime import Cell

        it, name = self.it, node.name
        holder = self.bodies.get(name)
        if holder is None:
            # Not a parse error: AWK only complains if the call happens.
            return _raiser(f"calling undefined function {name}")
        nparams = len(self.it.program.functions[name].params)
        binders = [self._binder(a) for a in node.args[:nparams]]
        extras = [self.expr(a) for a in node.args[nparams:]]
        n_locals = nparams - len(binders)

        def call() -> Value:
            frame = [b() for b in binders]
            for e in extras:
                e()
            if n_locals:
                frame.extend([Cell() for _ in range(n_locals)])
            saved = it.frame
            it.frame = frame
            try:
                sig = holder[0]()
            finally:
                it.frame = saved
            if sig is RETURN:
                return it.ret_value
            if sig is NEXT:
                raise NextFromFunction
            return UNINIT

        return call

    def _binder(self, arg: ast.Expr) -> Callable[[], "Cell"]:
        """Make the callee's Cell for one argument.

        Scalars are passed by value: the cell gets a copy of the value.
        (Python strings and floats cannot be changed in place, so sharing
        the value object is as good as a copy.)  A bare variable name is
        special, because it may name an array (passed by reference: the
        cell shares the very same dict) or an untyped variable (the cell is
        linked to it, so that it can still become an array; see Cell).
        """
        from .runtime import Cell

        it = self.it
        if type(arg) is ast.Var:
            name = arg.name  # type: ignore[attr-defined]
            idx = self._local_index(name)
            if idx is not None:

                def bind_local() -> Cell:
                    c = it.frame[idx]  # type: ignore[index]
                    if c.array is not None:
                        return Cell(None, c.array)
                    if c.value is not None:
                        return Cell(c.value)
                    return Cell(None, None, c)  # an untyped local of the caller

                return bind_local
            arrays, vars_, get = it.arrays, self.vars, self.scalar_getter(name)

            def bind_global() -> Cell:
                if name in arrays:
                    return Cell(None, arrays[name])
                if name in vars_ or name == "NF":
                    return Cell(get())
                return Cell(None, None, name)  # an untyped global

            return bind_global
        e = self.expr(arg)
        return lambda: Cell(e())

    def _e_Getline(self, node: ast.Getline) -> Thunk:
        """The getline forms; returns 1 (got a record), 0 (end) or -1 (error).

        * ``getline [var]``: next record of the main input; NR and FNR go up.
        * ``getline [var] < file``: from a file; NR/FNR unchanged.
        * ``cmd | getline [var]``: from a command's output; NR/FNR unchanged.
        Without ``var`` the record replaces $0 (and so NF).

        POSIX says ``cmd | getline`` increments NR, but mawk, onetrue-awk
        and gawk all leave it alone, and so do we (see DESIGN.md).

        The variable is resolved *before* reading, as gawk and mawk do, so
        the subscript in ``getline a[++c] < f`` is evaluated (and the
        element created) on every call, including the one that hits end of
        file or fails to open the file: the gawk test getline5 expects c = 2
        after reading a one-line file.  (onetrue-awk skips it, giving 1.)
        """
        it, vars_, sval, kind = self.it, self.vars, self.sval, node.kind
        get_ref, set_ref = self.get_ref, self.set_ref
        resolve = None if node.target is None else self.ref(node.target)
        source = None if node.source is None else self.expr(node.source)

        def target() -> tuple[Any, ...] | None:
            if resolve is None:
                return None
            ref = resolve()
            if ref[0] == "elem":
                get_ref(ref)  # referencing an element creates it
            return ref

        def getline() -> Value:
            rs = sval(vars_["RS"])
            if kind == "simple":
                ref = target()
                rec = it.next_main_record()
                if rec is None:
                    return 0.0
                vars_["NR"] = to_num(vars_["NR"]) + 1.0
                vars_["FNR"] = to_num(vars_["FNR"]) + 1.0
            else:
                assert source is not None
                name = sval(source())
                ref = target()
                if kind == "file":
                    reader = it.io.get_input_file(name)
                    if reader is None:
                        return -1.0
                else:
                    reader = it.io.get_input_cmd(name)
                rec = reader.read_record(rs)
                if rec is None:
                    return 0.0
            if ref is None:
                it.set_record(rec)
            else:
                set_ref(ref, make_strnum(rec))
            return 1.0

        return getline

    # ------------------------------------------------------------ variables
    def _local_index(self, name: str) -> int | None:
        """Position of ``name`` in the frame if it is a parameter of the
        function being compiled, else None (a global)."""
        if self.locals is None:
            return None
        return self.locals.get(name)

    def _is_plain_global(self, name: str) -> bool:
        """A global scalar with no special behaviour (NF has some)."""
        return name != "NF" and self._local_index(name) is None

    def scalar_getter(self, name: str) -> Thunk:
        """Closure reading the scalar variable ``name``."""
        it = self.it
        idx = self._local_index(name)
        if idx is not None:

            def get_local() -> Value:
                cell = it.frame[idx]  # type: ignore[index]
                v = cell.value
                if v is None:
                    if cell.array is not None:
                        raise AwkRuntimeError(f"can't use array {name} in scalar context")
                    # First use, as a scalar: from now on it is one.
                    cell.value = v = UNINIT
                    cell.link = None
                return v

            return get_local
        if name == "NF":
            return it.get_nf
        vars_, new_global = self.vars, it.new_global_scalar

        def get_global() -> Value:
            # ``try`` costs nothing when no exception happens, and
            # ``vars_[name]`` is quicker than a ``vars_.get(name)`` call.
            try:
                return vars_[name]
            except KeyError:
                return new_global(name)

        return get_global

    def scalar_setter(self, name: str) -> Callable[[Value], None]:
        """Closure assigning to the scalar variable ``name``."""
        it = self.it
        idx = self._local_index(name)
        if idx is not None:

            def set_local(v: Value) -> None:
                cell = it.frame[idx]  # type: ignore[index]
                if cell.array is not None:
                    raise AwkRuntimeError(f"can't assign to {name}; it's an array name")
                cell.value = v
                cell.link = None  # a scalar now; the caller's variable is unaffected

            return set_local
        set_var = it.set_var
        return lambda v: set_var(name, v)

    def array(self, name: str) -> Callable[[], dict[str, Value]]:
        """Closure giving the array (a dict) named ``name``."""
        it = self.it
        idx = self._local_index(name)
        if idx is not None:
            cell_array = it.cell_array

            def local_array() -> dict[str, Value]:
                cell = it.frame[idx]  # type: ignore[index]
                arr = cell.array
                if arr is None:
                    arr = cell_array(cell, name)
                return arr

            return local_array
        arrays_get, global_array = it.arrays.get, it.global_array

        def array() -> dict[str, Value]:
            arr = arrays_get(name)
            if arr is None:
                arr = global_array(name)
            return arr

        return array

    def var_kind(self, name: str) -> Callable[[], str]:
        """Closure returning "array", "scalar" or "untyped" for ``name``,
        *without* deciding the type (``length(x)`` must not turn an unused
        x into a scalar)."""
        from .runtime import Cell

        it = self.it
        idx = self._local_index(name)
        if idx is None:
            return lambda: it.var_kind(name)

        def local_kind() -> str:
            cell: Cell | str | None = it.frame[idx]  # type: ignore[index]
            # Follow links until we reach a cell that knows, or a global.
            while isinstance(cell, Cell):
                if cell.array is not None:
                    return "array"
                if cell.value is not None:
                    return "scalar"
                cell = cell.link
            if cell is None:
                return "untyped"
            return it.var_kind(cell)

        return local_kind

    def subscript(self, subs: list[ast.Expr]) -> Callable[[], str]:
        """Closure giving the key for ``arr[i]`` or ``arr[i, j]``: each
        subscript as a string, joined with SUBSEP."""
        sval = self.sval
        if len(subs) == 1:
            node = subs[0]
            if type(node) is ast.Str:
                return _const(node.value)  # type: ignore[attr-defined]
            k = _int_const(node)
            if k is not None:
                return _const(str(k))  # whole numbers print the same under any CONVFMT
            return self.string(node)
        parts, vars_ = [self.string(e) for e in subs], self.vars

        def multi_key() -> str:
            sep = sval(vars_["SUBSEP"])
            return sep.join([p() for p in parts])

        return multi_key

    # lvalue references: resolve once, then get/set (avoids double evaluation)
    #
    # An "lvalue" is something that can be assigned to: a variable, an array
    # element or a field.  For ``arr[i++] += 1`` the subscript must be
    # evaluated only once, even though we read and then write the element.
    # The closure returned by ``ref`` evaluates everything that has side
    # effects and returns a small tuple describing the location:
    # ("var", getter, setter), ("elem", <the dict>, "key") or ("field", 3).
    # get_ref/set_ref then read or write that location.
    def ref(self, target: ast.Expr) -> Callable[[], tuple[Any, ...]]:
        t = type(target)
        if t is ast.Var:
            name = target.name  # type: ignore[attr-defined]
            return _const(("var", self.scalar_getter(name), self.scalar_setter(name)))
        if t is ast.Index:
            get_arr, key = self.array(target.name), self.subscript(target.subs)  # type: ignore[attr-defined]

            def elem_ref() -> tuple[Any, ...]:
                arr = get_arr()
                return ("elem", arr, key())

            return elem_ref
        if t is ast.Field:
            index = self._field_index(target.index)  # type: ignore[attr-defined]
            return lambda: ("field", index())

        def bad() -> tuple[Any, ...]:
            raise AwkRuntimeError("assignment to non-lvalue")

        return bad

    def get_ref(self, ref: tuple[Any, ...]) -> Value:
        kind = ref[0]
        if kind == "var":
            return ref[1]()
        if kind == "elem":
            arr, key = ref[1], ref[2]
            v = arr.get(key)
            if v is None:
                # Merely referencing arr[key] creates the element (AWK rule).
                v = arr[key] = UNINIT
            return v
        return self.it.get_field(ref[1])

    def set_ref(self, ref: tuple[Any, ...], v: Value) -> None:
        kind = ref[0]
        if kind == "var":
            ref[2](v)
        elif kind == "elem":
            ref[1][ref[2]] = v
        else:
            self.it.set_field(ref[1], v)


# Comparison tests used by Compiler._compare (see its docstring for NaN).
def _lt(x: Any, y: Any) -> bool:
    return x < y


def _le(x: Any, y: Any) -> bool:
    return x <= y


def _gt(x: Any, y: Any) -> bool:
    return not x <= y


def _ge(x: Any, y: Any) -> bool:
    return not x < y


def _eq(x: Any, y: Any) -> bool:
    return x == y


def _ne(x: Any, y: Any) -> bool:
    return x != y


class CompiledProgram:
    """The compiled BEGIN blocks, main rules and END blocks.

    Each rule is a tuple (pattern, range end, action): the pattern and end
    are conditions or None, the action a statement or None (``{ print }``).
    """

    def __init__(
        self,
        begin: list[StmtFn],
        rules: list[tuple[CondFn | None, CondFn | None, StmtFn | None]],
        end: list[StmtFn],
    ) -> None:
        self.begin = begin
        self.rules = rules
        self.end = end
