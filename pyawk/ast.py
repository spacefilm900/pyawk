"""AST node definitions: the tree the parser builds and the runtime walks.

Pipeline: cli -> lexer -> parser -> **AST** -> runtime.  An *abstract syntax
tree* is the program's structure with the punctuation removed.  For example
``print $1 + 2`` becomes::

    Print(args=[Binary("+", Field(Num(1.0)), Num(2.0))])

Every class here is pure data with no behaviour; ``runtime.py`` decides what
each node *means*.  ``Program`` is the root: lists of BEGIN blocks, main
rules and END blocks.

This module is imported as ``pyawk.ast`` (always relatively inside the
package), so it never shadows the standard library ``ast`` module.

Python notes for this file:

* ``@dataclass`` turns a class with annotated fields (``name: str``) into a
  record type: Python writes the constructor, ``==`` and a readable repr, so
  ``Num(1.0) == Num(1.0)`` is True (the parser tests rely on that).
  ``slots=True`` stores fields compactly.
* ``Optional[X]`` means "an X or None"; ``list[Expr]`` is a list of
  expression nodes.  These hints document the shape of the tree; Python
  does not enforce them at run time.
"""

# Treat type hints as text (allows forward references and newer syntax).
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional, Union


class Node:
    """Base class for all AST nodes."""

    __slots__ = ()


# ---------------------------------------------------------------- expressions
class Expr(Node):
    """Base for nodes that produce a value."""

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
    """``$index``: ``$0`` is the whole record, ``$1`` the first field."""

    index: Expr


@dataclass(slots=True)
class Group(Expr):
    """A parenthesised expression list ``(a, b)``; only valid before ``in``
    or as the argument list of print/printf."""

    exprs: list[Expr]


@dataclass(slots=True)
class Assign(Expr):
    """``target op value``; target is a Var, Index or Field (an "lvalue")."""

    op: str  # '=', '+=', '-=', '*=', '/=', '%=', '^='
    target: Expr
    value: Expr


@dataclass(slots=True)
class Cond(Expr):
    """``test ? if_true : if_false``."""

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
    """``(subs) in array``: membership test that does not create the element."""

    subs: list[Expr]
    array: str


@dataclass(slots=True)
class Match(Expr):
    """``left ~ regex`` (or ``!~`` when negate is True)."""

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
    """String concatenation, written in AWK by juxtaposition: ``a b``."""

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
    """``++x`` / ``x++`` (prefix=False returns the value before the change)."""

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
    """All getline forms; ``target`` None means "set $0", ``source`` is the
    file name or command.  ``= None`` gives the field a default value."""

    kind: str  # 'simple' (main input), 'file' (< expr), 'cmd' (expr |)
    target: Optional[Expr] = None
    source: Optional[Expr] = None


# Node types that may appear on the left of "=" (things that can be assigned).
LVALUE_TYPES = (Var, Index, Field)


# ----------------------------------------------------------------- statements
class Stmt(Node):
    """Base for nodes that are executed for their effect (no value)."""

    __slots__ = ()


@dataclass(slots=True)
class Block(Stmt):
    # ``default_factory=list`` gives each Block its own fresh empty list
    # (a plain ``= []`` default would be shared between all instances).
    body: list[Stmt] = field(default_factory=list)


@dataclass(slots=True)
class ExprStmt(Stmt):
    expr: Expr


@dataclass(slots=True)
class Print(Stmt):
    """``print args [> dest]``; with no args it prints ``$0``."""

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
    """``for (var in array) body``: visits every key of the array."""

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
    """The whole parsed program: the root of the tree."""

    begin: list[Block] = field(default_factory=list)
    rules: list[Rule] = field(default_factory=list)
    end: list[Block] = field(default_factory=list)


AnyNode = Union[Expr, Stmt, Rule, Program]
