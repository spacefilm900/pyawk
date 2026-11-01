"""AST node definitions.

This module is imported as ``pyawk.ast`` (always relatively inside the
package), so it never shadows the standard library ``ast`` module.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional, Union


class Node:
    """Base class for all AST nodes."""

    __slots__ = ()


# ---------------------------------------------------------------- expressions
class Expr(Node):
    __slots__ = ()


@dataclass(slots=True)
class Num(Expr):
    value: float


@dataclass(slots=True)
class Str(Expr):
    value: str


@dataclass(slots=True)
class Regex(Expr):
    """An ERE literal.  As a plain expression it means ``$0 ~ /re/``."""

    source: str


@dataclass(slots=True)
class Var(Expr):
    name: str


@dataclass(slots=True)
class Index(Expr):
    """``name[subs...]`` (multiple subscripts are joined with SUBSEP)."""

    name: str
    subs: list[Expr]


@dataclass(slots=True)
class Field(Expr):
    index: Expr


@dataclass(slots=True)
class Group(Expr):
    """A parenthesised expression list ``(a, b)``; only valid before ``in``
    or as the argument list of print/printf."""

    exprs: list[Expr]


@dataclass(slots=True)
class Assign(Expr):
    op: str  # '=', '+=', '-=', '*=', '/=', '%=', '^='
    target: Expr
    value: Expr


@dataclass(slots=True)
class Cond(Expr):
    test: Expr
    if_true: Expr
    if_false: Expr


@dataclass(slots=True)
class Or(Expr):
    left: Expr
    right: Expr


@dataclass(slots=True)
class And(Expr):
    left: Expr
    right: Expr


@dataclass(slots=True)
class In(Expr):
    subs: list[Expr]
    array: str


@dataclass(slots=True)
class Match(Expr):
    negate: bool
    left: Expr
    regex: Expr


@dataclass(slots=True)
class Compare(Expr):
    op: str  # '<', '<=', '==', '!=', '>', '>='
    left: Expr
    right: Expr


@dataclass(slots=True)
class Concat(Expr):
    left: Expr
    right: Expr


@dataclass(slots=True)
class Binary(Expr):
    op: str  # '+', '-', '*', '/', '%', '^'
    left: Expr
    right: Expr


@dataclass(slots=True)
class Unary(Expr):
    op: str  # '-', '+', '!'
    operand: Expr


@dataclass(slots=True)
class IncDec(Expr):
    op: str  # '++' or '--'
    prefix: bool
    target: Expr


@dataclass(slots=True)
class Call(Expr):
    """A builtin function call."""

    name: str
    args: list[Expr]


@dataclass(slots=True)
class Getline(Expr):
    kind: str  # 'simple' (main input), 'file' (< expr), 'cmd' (expr |)
    target: Optional[Expr] = None
    source: Optional[Expr] = None


LVALUE_TYPES = (Var, Index, Field)


# ----------------------------------------------------------------- statements
class Stmt(Node):
    __slots__ = ()


@dataclass(slots=True)
class Block(Stmt):
    body: list[Stmt] = field(default_factory=list)


@dataclass(slots=True)
class ExprStmt(Stmt):
    expr: Expr


@dataclass(slots=True)
class Print(Stmt):
    args: list[Expr]
    redirect: Optional[str] = None  # '>', '>>', '|'
    dest: Optional[Expr] = None


@dataclass(slots=True)
class Printf(Stmt):
    args: list[Expr]
    redirect: Optional[str] = None
    dest: Optional[Expr] = None


@dataclass(slots=True)
class If(Stmt):
    test: Expr
    then: Stmt
    orelse: Optional[Stmt] = None


@dataclass(slots=True)
class While(Stmt):
    test: Expr
    body: Stmt


@dataclass(slots=True)
class DoWhile(Stmt):
    body: Stmt
    test: Expr


@dataclass(slots=True)
class For(Stmt):
    init: Optional[Expr]
    test: Optional[Expr]
    step: Optional[Expr]
    body: Stmt


@dataclass(slots=True)
class ForIn(Stmt):
    var: Expr
    array: str
    body: Stmt


@dataclass(slots=True)
class Break(Stmt):
    pass


@dataclass(slots=True)
class Continue(Stmt):
    pass


@dataclass(slots=True)
class Next(Stmt):
    pass


@dataclass(slots=True)
class Exit(Stmt):
    status: Optional[Expr] = None


@dataclass(slots=True)
class Delete(Stmt):
    array: str
    subs: Optional[list[Expr]] = None  # None deletes the whole array


# -------------------------------------------------------------------- program
@dataclass(slots=True)
class Rule(Node):
    """``pattern { action }``; a missing pattern matches every record and a
    missing action means ``{ print }``."""

    pattern: Optional[Expr]
    action: Optional[Block]


@dataclass(slots=True)
class Program(Node):
    begin: list[Block] = field(default_factory=list)
    rules: list[Rule] = field(default_factory=list)
    end: list[Block] = field(default_factory=list)


AnyNode = Union[Expr, Stmt, Rule, Program]
