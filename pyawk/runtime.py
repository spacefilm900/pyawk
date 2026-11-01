"""Tree-walking interpreter: records, fields, variables, I/O and evaluation.

Role in the pipeline: cli -> lexer -> parser -> AST -> **runtime**.  The
runtime receives the parsed ``ast.Program`` and runs it.  Entry point:
``Interpreter(program, operands, assignments).run()``, which returns the
exit status.

"Tree-walking" means there is no compilation step: to evaluate a node we
look at its type and run the matching method, which evaluates the node's
children the same way (recursively).

How an AWK program runs (``Interpreter.run``):

1. Run the BEGIN blocks.
2. Unless the program is only BEGIN blocks, read input records one at a
   time (lines, by default), from each file operand or else standard input.
   For each record, try every ``pattern { action }`` rule in order.
3. Run the END blocks, then close all files and pipes.

Main pieces in this file:

* control-flow exceptions (break/continue/next/exit),
* ``RecordReader``: splits an input stream into records according to RS,
* ``IOManager``: files and pipes opened by ``print >``, ``getline <`` etc.,
* ``Interpreter``: variables, arrays, fields and the evaluator itself.
"""

# Treat type hints as text, so newer syntax such as ``str | None`` is allowed.
from __future__ import annotations

import codecs
import io
import math
import os
import random
import re
import subprocess
import sys
import threading
from typing import IO, Any, Callable, Iterable

from . import ast
from .builtins import BUILTINS, format_printf
from .errors import AwkRuntimeError
from .lexer import process_escapes
from .regex import compile_ere
from .values import (
    UNINIT,
    StrNum,
    Value,
    compare,
    make_strnum,
    num_to_str,
    to_bool,
    to_num,
    to_str,
)

# Input bytes that are not valid UTF-8 are carried through unchanged
# ("surrogateescape"), so binary-ish data is not corrupted or rejected.
ENCODING = "utf-8"
ERRORS = "surrogateescape"
# An operand like ``n=5`` is an assignment, not a file name.
_ASSIGN_ARG_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*=")
_WS_FIELDS_RE = re.compile(r"[^ \t\n]+")


# ---------------------------------------------------------------- control flow
# break, continue, next and exit must jump out of arbitrarily deep nested
# method calls (the evaluator is recursive).  Python's way to do that is to
# *raise* an exception: it unwinds the calls until a matching ``try: ...
# except`` catches it.  So ``break`` raises _BreakEx and the loop methods
# catch it; ``next`` raises _NextEx, caught by the per-record loop; ``exit``
# raises ExitEx, caught in ``run``.  These are not errors, just jumps.
#
# ``class X(Exception): pass`` declares a new exception type with no extra
# behaviour; the leading underscore marks a name as private to this module.
class _BreakEx(Exception):
    pass


class _ContinueEx(Exception):
    pass


class _NextEx(Exception):
    pass


class ExitEx(Exception):
    """Raised by ``exit [code]``; carries the exit status."""

    def __init__(self, code: int) -> None:
        super().__init__(code)
        self.code = code


class _ReturnEx(Exception):
    """Raised by ``return [value]``; caught by the function call that is returning."""

    def __init__(self, value: Value) -> None:
        super().__init__()
        self.value = value


# ---------------------------------------------------------------- functions
# How many Python stack frames we allow.  Each level of AWK function call
# uses roughly a dozen nested Python calls (call -> block -> statement ->
# expression -> ... -> call), and Python's default limit of 1000 would stop
# recursion after ~80 AWK levels.  This allows AWK recursion depths of
# well over 10000 (as gawk and mawk manage); deeper runaway recursion is
# reported as an error rather than crashing.
RECURSION_LIMIT = 400_000
# Running out of the *operating-system* stack would crash Python outright,
# so the interpreter runs in a thread with a stack this large (bytes).
THREAD_STACK_SIZE = 1 << 30


class Cell:
    """One local variable of a running function call (a parameter).

    AWK variables are untyped until first used, and a parameter can turn
    out to be a scalar or an array depending on how the function body uses
    it.  So a cell has three slots, at most one of which is "active":

    * ``value``: the scalar value, once it is known to be a scalar.
    * ``array``: the Python dict holding the elements, once it is known to
      be an array.  Arrays are passed *by reference*: the caller's dict
      itself is put here, so changes are visible to the caller.
    * ``link``: for a parameter whose argument was an *untyped* variable of
      the caller (never used yet).  If the callee uses the parameter as an
      array, the caller's variable must become that same array; ``link``
      remembers where to create it: the caller's own Cell, or the name of a
      global variable (a ``str``).

    ``__slots__`` fixes the attribute names (smaller, faster objects).
    """

    __slots__ = ("value", "array", "link")

    def __init__(
        self,
        value: Value | None = None,
        array: dict[str, Value] | None = None,
        link: "Cell | str | None" = None,
    ) -> None:
        self.value = value
        self.array = array
        self.link = link


# ---------------------------------------------------------------- input
class RecordReader:
    """Reads records from a binary stream for a (possibly changing) RS.

    The program may change RS between records, so we cannot simply iterate
    over lines.  Instead we keep a text buffer ``buf`` and an offset
    ``start`` where the unread part begins, reading more data in chunks as
    needed.  RS rules: a single character separates records; ``""`` means
    "paragraph mode" (records separated by blank lines); anything longer is
    a regular expression (an extension in gawk and mawk).
    """

    CHUNK = 65536

    def __init__(self, stream: IO[bytes], proc: subprocess.Popen[bytes] | None = None) -> None:
        self.stream = stream
        self.proc = proc
        # An incremental decoder copes with a multi-byte UTF-8 character split
        # across two chunks.
        self.decoder = codecs.getincrementaldecoder(ENCODING)(errors=ERRORS)
        self.buf = ""
        self.start = 0
        self.eof = False

    def _fill(self) -> bool:
        """Read more data; return False at end of input."""
        if self.eof:
            return False
        # read1 (if the stream has it) returns what is available now instead
        # of waiting for a full chunk: important for interactive input/pipes.
        reader = getattr(self.stream, "read1", None)
        data = reader(self.CHUNK) if reader is not None else self.stream.read(self.CHUNK)
        if not data:
            self.eof = True
            tail = self.decoder.decode(b"", final=True)
            if tail:
                self._append(tail)
            return bool(tail)
        self._append(self.decoder.decode(data))
        return True

    def _append(self, text: str) -> None:
        # Drop the already-consumed prefix while appending, so the buffer
        # does not grow forever.
        if self.start:
            self.buf = self.buf[self.start :] + text
            self.start = 0
        else:
            self.buf += text

    def read_record(self, rs: str) -> str | None:
        """Return the next record (without its separator), or None at end."""
        if rs == "\n" or len(rs) == 1:
            return self._read_char(rs)
        if rs == "":
            return self._read_paragraph()
        return self._read_regex(rs)

    def _read_char(self, rs: str) -> str | None:
        scan = self.start
        while True:
            idx = self.buf.find(rs, scan)
            if idx != -1:
                rec = self.buf[self.start : idx]
                self.start = idx + 1
                return rec
            # Not found yet: read more, and resume the search where we stopped.
            scanned = len(self.buf) - self.start  # already searched, relative
            if not self._fill():
                break
            scan = self.start + scanned
        # End of input: a last record without a trailing separator still counts.
        if self.start < len(self.buf):
            rec = self.buf[self.start :]
            self.buf, self.start = "", 0  # tuple assignment: sets both at once
            return rec
        return None

    def _read_paragraph(self) -> str | None:
        # Skip leading newlines.
        while True:
            while self.start < len(self.buf) and self.buf[self.start] == "\n":
                self.start += 1
            if self.start < len(self.buf) or not self._fill():
                break
        if self.start >= len(self.buf):
            return None
        while True:
            idx = self.buf.find("\n\n", self.start)
            if idx != -1:
                rec = self.buf[self.start : idx]
                self.start = idx + 2
                return rec
            if not self._fill():
                break
        rec = self.buf[self.start :].rstrip("\n")
        self.buf, self.start = "", 0
        return rec

    def _read_regex(self, rs: str) -> str | None:
        # Simplest correct approach: read all input first, since a regex
        # match could otherwise be cut short at a chunk boundary.
        while self._fill():
            pass
        if self.start >= len(self.buf):
            return None
        pat = compile_ere(rs)
        pos = self.start
        while True:
            m = pat.search(self.buf, pos)
            if m is None:
                break
            if m.end() > m.start():  # ignore empty matches
                rec = self.buf[self.start : m.start()]
                self.start = m.end()
                return rec
            pos = m.start() + 1
            if pos > len(self.buf):
                break
        rec = self.buf[self.start :]
        self.buf, self.start = "", 0
        return rec

    def close(self) -> int:
        try:
            self.stream.close()
        except OSError:
            pass
        if self.proc is not None:
            return _wait_status(self.proc)
        return 0


def _wait_status(proc: subprocess.Popen[Any]) -> int:
    """Wait for a child process; "killed by signal N" (-N in Python) -> 256 + N."""
    rc = proc.wait()
    return rc if rc >= 0 else 256 - rc


# ---------------------------------------------------------------- output
class IOManager:
    """Tracks redirected output streams and getline input streams by name.

    In AWK the file name or command string *is* the handle: ``print > "f"``
    opens f once and keeps writing to it until ``close("f")``.  So we keep
    dictionaries from name to open stream.
    """

    def __init__(self, stdout: IO[str], stdin: IO[bytes]) -> None:
        self.stdout = stdout
        self.stdin = stdin
        # name -> (stream, process); the process is None for plain files.
        self.outputs: dict[str, tuple[IO[str], subprocess.Popen[bytes] | None]] = {}
        self.inputs: dict[str, RecordReader] = {}
        self.stdin_reader: RecordReader | None = None

    def get_stdin_reader(self) -> RecordReader:
        if self.stdin_reader is None:
            self.stdin_reader = RecordReader(self.stdin)
        return self.stdin_reader

    def get_output(self, kind: str, name: str) -> IO[str]:
        """Stream for ``>``, ``>>`` or ``|`` (kind) to ``name``, opening it once."""
        entry = self.outputs.get(name)
        if entry is not None:
            return entry[0]
        if name in ("/dev/stdout", "-"):
            return self.stdout
        if name == "/dev/stderr":
            return sys.stderr
        proc = None
        try:
            if kind == "|":
                # Flush first so our earlier output appears before the command's.
                self.flush_all()
                proc = subprocess.Popen(name, shell=True, stdin=subprocess.PIPE)
                assert proc.stdin is not None
                stream: IO[str] = io.TextIOWrapper(proc.stdin, encoding=ENCODING, errors=ERRORS, newline="")
            else:
                mode = "a" if kind == ">>" else "w"
                stream = open(name, mode, encoding=ENCODING, errors=ERRORS, newline="")
        except OSError as exc:
            raise AwkRuntimeError(f"can't redirect to {name} ({exc.strerror})") from None
        self.outputs[name] = (stream, proc)
        return stream

    def get_input_file(self, name: str) -> RecordReader | None:
        reader = self.inputs.get(name)
        if reader is not None:
            return reader
        if name in ("-", "/dev/stdin"):
            return self.get_stdin_reader()
        try:
            reader = RecordReader(open(name, "rb"))
        except OSError:
            return None
        self.inputs[name] = reader
        return reader

    def get_input_cmd(self, cmd: str) -> RecordReader:
        reader = self.inputs.get(cmd)
        if reader is None:
            self.flush_all()
            proc = subprocess.Popen(cmd, shell=True, stdout=subprocess.PIPE)
            assert proc.stdout is not None
            reader = RecordReader(proc.stdout, proc)
            self.inputs[cmd] = reader
        return reader

    def close(self, name: str) -> int:
        """close(name): returns the command's exit status, 0, or -1 if not open."""
        status = -1
        entry = self.outputs.pop(name, None)
        if entry is not None:
            stream, proc = entry
            self.stdout.flush()
            try:
                stream.close()
            except OSError:
                pass
            status = _wait_status(proc) if proc is not None else 0
        reader = self.inputs.pop(name, None)
        if reader is not None:
            status = reader.close()
        return status

    def flush_all(self) -> None:
        self.stdout.flush()
        for stream, _ in self.outputs.values():
            try:
                stream.flush()
            except OSError:
                pass

    def close_all(self) -> None:
        # Close redirections first (mawk order), then flush stdout.
        # list(...) takes a copy of the names, because close() removes
        # entries and Python forbids changing a dict while looping over it.
        for name in list(self.outputs):
            self.close(name)
        for name in list(self.inputs):
            self.close(name)
        self.stdout.flush()


# ---------------------------------------------------------------- interpreter
class Interpreter:
    """Runs one AWK program: holds all variables, arrays and record state.

    ``operands`` are the command-line arguments after the program (files and
    ``var=value`` assignments); ``assignments`` are the ``-v``/``-F``
    settings, applied before BEGIN.  ``stdout``/``stdin`` can be replaced
    (the tests do so).
    """

    def __init__(
        self,
        program: ast.Program,
        operands: Iterable[str] = (),
        assignments: Iterable[tuple[str, str]] = (),
        stdout: IO[str] | None = None,
        stdin: IO[bytes] | None = None,
        argv0: str = "awk",
    ) -> None:
        self.program = program
        # The local variables of the function call now running, as a dict
        # from parameter name to Cell; None when no function is running.
        # Each call makes a new dict and puts the caller's back on return,
        # so this is in effect a stack of call frames.  Names not in the
        # frame are global (self.vars / self.arrays).
        self.frame: dict[str, Cell] | None = None
        # ``a if cond else b`` is Python's conditional expression (C's ?:).
        self.out: IO[str] = stdout if stdout is not None else sys.stdout
        self.io = IOManager(self.out, stdin if stdin is not None else sys.stdin.buffer)
        self.vars: dict[str, Value] = {
            "FS": " ", "OFS": " ", "ORS": "\n", "RS": "\n", "NR": 0.0, "FNR": 0.0,
            "SUBSEP": "\x1c", "CONVFMT": "%.6g", "OFMT": "%.6g", "RSTART": 0.0,
            "RLENGTH": -1.0, "FILENAME": "",
        }
        # Scalars and arrays live in separate dictionaries (name -> value).
        # An AWK array is a Python dict from string key to value.
        self.arrays: dict[str, dict[str, Value]] = {}
        operands = list(operands)
        argv = {"0": argv0}
        for i, op in enumerate(operands, 1):
            argv[str(i)] = make_strnum(op)
        self.arrays["ARGV"] = argv  # type: ignore[assignment]
        self.vars["ARGC"] = float(len(operands) + 1)
        # A *dict comprehension*: builds {name: value} for every environment
        # variable, each wrapped as a strnum.
        self.arrays["ENVIRON"] = {k: make_strnum(v) for k, v in os.environ.items()}
        # -v and -F settings (with escape processing; see command_line_assign).
        for name, value in assignments:
            self.command_line_assign(name, value)

        # Record state.  Fields are split *lazily*: reading a record only
        # stores its text, and ``fields`` stays None until a program actually
        # uses $1, NF etc.  Many programs only look at $0, so this saves work.
        self.record = ""  # text of $0
        self.record_value: Value | None = None  # $0 as a strnum, made on demand
        self.fields: list[Value] | None = None  # $1..$NF once split, else None
        self.nf = 0  # valid only once fields is not None
        # FS in force when this record was read: POSIX says changing FS
        # affects the *next* record, so splitting later must use this value.
        self.record_fs = " "

        # Main input iteration.
        self.argi = 1
        self.reader: RecordReader | None = None
        self.opened_any = False
        # One "is this range open?" flag per main rule (see _in_range); only
        # range rules ``start, end`` use theirs.  ``[False] * n`` is a list
        # of n False values.
        self.range_open: list[bool] = [False] * len(program.rules)

        self.seed = 0.0
        self.rng = random.Random(0)
        self.exit_code = 0


        # Dispatch tables: map each node class to the method that handles it.
        # ``eval`` looks up ``type(node)`` here instead of a long chain of
        # ``if isinstance(...)`` tests.  ``self._num`` etc. are "bound
        # methods": calling one later passes this interpreter as ``self``.
        self._eval: dict[type, Callable[[Any], Value]] = {
            ast.Num: self._num, ast.Str: self._str, ast.Regex: self._regex_expr,
            ast.Var: self._var, ast.Index: self._index, ast.Field: self._field,
            ast.Assign: self._assign, ast.Cond: self._cond, ast.Or: self._or,
            ast.And: self._and, ast.In: self._in, ast.Match: self._match,
            ast.Compare: self._compare, ast.Concat: self._concat,
            ast.Binary: self._binary, ast.Unary: self._unary,
            ast.IncDec: self._incdec, ast.Call: self._call,
            ast.UserCall: self._user_call,
            ast.Getline: self._getline, ast.Group: self._group,
        }
        self._exec: dict[type, Callable[[Any], None]] = {
            ast.Block: self._block, ast.ExprStmt: self._expr_stmt,
            ast.Print: self._print, ast.Printf: self._printf, ast.If: self._if,
            ast.While: self._while, ast.DoWhile: self._do_while, ast.For: self._for,
            ast.ForIn: self._for_in, ast.Break: self._break,
            ast.Continue: self._continue, ast.Next: self._next,
            ast.Exit: self._exit, ast.Delete: self._delete,
            ast.Return: self._return,
        }

    # ------------------------------------------------------------ top level
    def run(self) -> int:
        """Run the program (see _run) with room for deep recursion.

        Deeply recursive AWK functions need a deep Python stack, so when
        the program defines functions we raise Python's recursion limit
        and run in a helper thread whose stack is large enough for it.
        (The main thread's stack size is fixed when the process starts.)
        """
        if not self.program.functions:
            return self._run()
        # A one-element list lets the thread hand back its result (or the
        # exception it raised), since a thread cannot return a value.
        result: list[Any] = []

        def target() -> None:
            try:
                result.append(self._run())
            except BaseException as exc:  # re-raised in this thread below
                result.append(exc)

        old_limit = sys.getrecursionlimit()
        try:
            old_size = threading.stack_size(THREAD_STACK_SIZE)
        except (ValueError, RuntimeError):
            # This platform will not give us a big stack: run normally,
            # with Python's usual (shallow, but safe) recursion limit.
            return self._run()
        sys.setrecursionlimit(max(old_limit, RECURSION_LIMIT))
        try:
            thread = threading.Thread(target=target)
            thread.start()
            thread.join()
        finally:
            threading.stack_size(old_size)
            sys.setrecursionlimit(old_limit)
        if isinstance(result[0], BaseException):
            raise result[0]
        return result[0]

    def _run(self) -> int:
        """BEGIN, the main record loop, END; returns the exit status.

        ``exit`` in BEGIN or a rule skips the remaining input but still runs
        END; ``exit`` in END stops at once.  ``finally`` makes sure output
        files and pipes are closed however we leave.
        """
        prog = self.program
        try:
            try:
                for block in prog.begin:
                    self._block(block)
                if prog.rules or prog.end:
                    self._main_loop()
            except ExitEx as e:
                self.exit_code = e.code
            try:
                for block in prog.end:
                    self._block(block)
            except ExitEx as e:
                self.exit_code = e.code
        # If these escape to here, they were used where they make no sense.
        # (``from None`` hides the internal exception from error reports.)
        except _NextEx:
            raise AwkRuntimeError("next used in BEGIN or END action") from None
        except (_BreakEx, _ContinueEx):
            raise AwkRuntimeError("break or continue outside a loop") from None
        except RecursionError:
            self.frame = None
            raise AwkRuntimeError("function call nesting too deep (runaway recursion?)") from None
        finally:
            try:
                self.io.close_all()
            except BrokenPipeError:
                pass
        return self.exit_code

    def _main_loop(self) -> None:
        """The implicit AWK loop: for each record, run each matching rule."""
        rules = self.program.rules
        while True:
            rec = self.next_main_record()
            if rec is None:
                return
            self.vars["NR"] = to_num(self.vars["NR"]) + 1.0
            self.vars["FNR"] = to_num(self.vars["FNR"]) + 1.0
            self.set_record(rec)
            try:
                # ``enumerate`` yields (position, rule) pairs; the position
                # indexes this rule's flag in self.range_open.
                for i, rule in enumerate(rules):
                    pat = rule.pattern
                    if rule.end is not None:
                        if not self._in_range(i, rule):
                            continue
                    elif pat is not None and not self.test(pat):
                        continue
                    if rule.action is None:  # a bare pattern means { print }
                        self.out.write(self.get_record_text() + self.str_of(self.vars["ORS"]))
                    else:
                        self._block(rule.action)
            except _NextEx:
                pass  # ``next``: abandon this record's rules, go to the next record

    def _in_range(self, i: int, rule: ast.Rule) -> bool:
        """Does range rule number ``i`` (``start, end``) select this record?

        Each range rule has one flag in self.range_open, True while the
        range is open.  The algorithm, which all three reference awks share:

        * Closed: test only ``start``.  If it fails, the record is not
          selected.  If it matches, the range opens *and* ``end`` is tested
          on this same record, so a record matching both is a range of one.
        * Open: test only ``end``.  The record is selected either way; if
          ``end`` matches, the range closes after this record.

        The end test happens *before* the action runs, so ``next`` or
        ``exit`` in the action cannot keep a range open.  A record skipped
        by ``next`` in an earlier rule, or swallowed by ``getline``, is
        never tested at all.  The flags are never reset between input
        files, so a range can continue from one file into the next.
        """
        if not self.range_open[i]:
            if not self.test(rule.pattern):  # type: ignore[arg-type]  # a range always has a start
                return False
        # Opened just now, or already open: does this record close it?
        self.range_open[i] = not self.test(rule.end)  # type: ignore[arg-type]
        return True

    def test(self, node: ast.Expr) -> bool:
        """Evaluate a condition; a bare /regex/ means ``$0 ~ /regex/``."""
        if type(node) is ast.Regex:
            return compile_ere(node.source).search(self.get_record_text()) is not None
        return to_bool(self.eval(node))

    # ------------------------------------------------------------ main input
    def next_main_record(self) -> str | None:
        """Next record from the main input, moving on to the next file as each
        one ends; None when all input is exhausted."""
        while True:
            if self.reader is None and not self._open_next_input():
                return None
            assert self.reader is not None
            rec = self.reader.read_record(self.str_of(self.vars["RS"]))
            if rec is not None:
                return rec
            if self.reader is not self.io.stdin_reader:
                self.reader.close()
            self.reader = None

    def _open_next_input(self) -> bool:
        """Advance through ARGV to the next file to read.

        ARGV is consulted as we go (not up front) because the program may
        change ARGV/ARGC in BEGIN.  Empty entries are skipped, ``var=value``
        entries are assigned at that point in the input, and if no file was
        named at all we read standard input.
        """
        argv = self.get_array("ARGV")
        while self.argi < to_num(self.vars["ARGC"]):
            key = str(self.argi)
            self.argi += 1
            arg = self.str_of(argv[key]) if key in argv else ""
            if arg == "":
                continue
            if _ASSIGN_ARG_RE.match(arg):
                # partition splits at the first "=" into (before, "=", after);
                # the three results are unpacked into three names ("_" unused).
                name, _, value = arg.partition("=")
                self.command_line_assign(name, value)
                continue
            self.opened_any = True
            if arg == "-" or arg == "/dev/stdin":
                self.reader = self.io.get_stdin_reader()
            else:
                try:
                    self.reader = RecordReader(open(arg, "rb"))
                except OSError as exc:
                    raise AwkRuntimeError(f"cannot open {arg} ({exc.strerror})") from None
            self.vars["FILENAME"] = arg
            self.vars["FNR"] = 0.0
            return True
        if not self.opened_any:
            self.opened_any = True
            self.reader = self.io.get_stdin_reader()
            self.vars["FNR"] = 0.0
            return True
        return False

    def command_line_assign(self, name: str, value: str) -> None:
        """Assignment from -v, -F (as FS) or a ``var=value`` operand.

        The value gets string-escape processing, so ``-F '\\t'`` means a tab,
        and is a strnum, so ``-v n=10`` compares as a number.
        """
        self.set_var(name, make_strnum(process_escapes(value)))

    # ------------------------------------------------------------ conversions
    # ``@property``: read as ``self.convfmt`` (no parentheses), computed each time.
    @property
    def convfmt(self) -> str:
        return to_str(self.vars["CONVFMT"])

    def str_of(self, v: Value) -> str:
        """String value of v; numbers are converted with CONVFMT."""
        if type(v) is str:
            return v
        if type(v) is StrNum:
            return v.s
        return num_to_str(v, self.convfmt)  # type: ignore[arg-type]

    def out_str(self, v: Value) -> str:
        """Like str_of, but ``print`` converts numbers with OFMT instead."""
        if type(v) is float:
            return num_to_str(v, to_str(self.vars["OFMT"]))
        return self.str_of(v)

    # ------------------------------------------------------------ records/fields
    def set_record(self, text: str) -> None:
        """Install a new $0; fields will be split from it when first needed."""
        self.record = text
        self.record_value = None
        self.fields = None
        self.record_fs = self.str_of(self.vars["FS"])

    def get_record_text(self) -> str:
        return self.record

    def _split_record(self) -> list[Value]:
        """Split $0 into fields if not done yet (lazy splitting); return them.

        Every field is a strnum: it compares as a number if it looks like one.
        """
        if self.fields is None:
            pieces = self.split_with_fs(self.record, self.record_fs, paragraph=self.vars["RS"] == "")
            self.fields = [make_strnum(p) for p in pieces]
            self.nf = len(self.fields)
        return self.fields

    def split_with_fs(self, s: str, fs: str, paragraph: bool = False) -> list[str]:
        """Split s by the FS rules (also used by split()).

        * ``" "`` (default): fields are runs of non-blanks; leading and
          trailing blanks are ignored.
        * one other character: split on exactly that character, so empty
          fields are possible (``a,,b`` has 3 fields with FS=",").
        * anything longer: a regular expression.
        ``paragraph`` is True when splitting a record in paragraph mode
        (RS=""): then a newline always separates fields too.  split() never
        passes it, since all three reference awks split strings by FS alone.
        """
        if fs == " ":
            return _WS_FIELDS_RE.findall(s)
        if s == "":
            return []
        if fs == "":
            # Unspecified by POSIX; gawk, mawk and BWK awk split into characters.
            return list(s)
        if len(fs) == 1 and fs != "\\" and not paragraph:
            return s.split(fs)
        # A single character is taken literally (re.escape), e.g. FS="|".
        source = re.escape(fs) if len(fs) == 1 else fs
        if paragraph:
            return self._split_compiled(s, compile_ere("\n|" + source if len(fs) == 1 else "\n|(" + source + ")"))
        return self._split_compiled(s, compile_ere(source))

    def split_regex(self, s: str, ere: str) -> list[str]:
        if s == "":
            return []
        return self._split_compiled(s, compile_ere(ere))

    # ``@staticmethod``: a plain function kept in the class; it has no ``self``.
    @staticmethod
    def _split_compiled(s: str, pat: re.Pattern[str]) -> list[str]:
        """Split s at each non-empty match of pat."""
        pieces: list[str] = []
        pos = 0
        for m in pat.finditer(s):
            if m.end() == m.start():
                continue
            pieces.append(s[pos : m.start()])
            pos = m.end()
        pieces.append(s[pos:])
        return pieces

    def get_field(self, i: int) -> Value:
        """$i.  $0 needs no splitting.

        A field past NF is the empty *string*, like an existing empty field,
        not the uninitialised value: so ``$5 == 0`` compares "" with "0" as
        strings and is false.  This is what onetrue-awk, mawk and gawk all
        do (DESIGN.md explains why we follow them rather than a literal
        reading of POSIX).
        """
        if i == 0:
            if self.record_value is None:
                self.record_value = make_strnum(self.record)
            return self.record_value
        if i < 0:
            raise AwkRuntimeError(f"trying to access out of range field {i}")
        fields = self._split_record()
        if i <= self.nf:
            return fields[i - 1]  # Python lists count from 0, AWK fields from 1
        return ""

    def set_field(self, i: int, v: Value) -> None:
        """Assign $i.

        Assigning $0 re-splits (lazily).  Assigning any other field changes
        the record: $0 is rebuilt by joining all fields with OFS, which is
        why ``$1 = $1`` is the classic idiom to re-format a line with a new
        OFS.  Assigning past NF adds empty fields and raises NF.
        """
        if i == 0:
            self.set_record(self.str_of(v))
            # $0 keeps the type of what was assigned: after $0 = "0" it is a
            # plain string (so true), not a numeric string; its fields are
            # still checked for numbers when split.  Numbers and strnums
            # need nothing here: get_field rebuilds a strnum from the text.
            # ``type(v) is str`` is an exact type test (StrNum is not str).
            if type(v) is str:
                self.record_value = v
            return
        if i < 0:
            raise AwkRuntimeError(f"trying to access out of range field {i}")
        fields = self._split_record()
        while len(fields) < i:
            fields.append("")  # gap fields are empty strings (see get_field)
        fields[i - 1] = v
        self.nf = len(fields)
        self._rebuild_record()

    def _rebuild_record(self) -> None:
        """Recompute $0 from the fields, joined with the current OFS."""
        assert self.fields is not None
        ofs = self.str_of(self.vars["OFS"])
        self.record = ofs.join(self.str_of(f) for f in self.fields)
        # A rebuilt $0 is a plain string, never a strnum (onetrue-awk and
        # gawk; mawk agrees except after an NF assignment).
        self.record_value = self.record

    def get_nf(self) -> float:
        self._split_record()
        return float(self.nf)

    def set_nf(self, v: Value) -> None:
        """Assigning NF truncates or extends the fields and rebuilds $0."""
        n = int(to_num(v))
        if n < 0:
            raise AwkRuntimeError(f"NF set to negative value {n}")
        fields = self._split_record()
        del fields[n:]  # remove everything from index n to the end
        while len(fields) < n:
            fields.append("")  # new fields are empty strings (see get_field)
        self.nf = n
        self._rebuild_record()

    # ------------------------------------------------------------ variables
    #
    # Inside a function call, a name is first looked up among the call's
    # local variables (self.frame, see Cell); otherwise it is global.
    # A variable's first use decides whether it is a scalar or an array,
    # and using it the other way afterwards is an error.
    def get_var(self, name: str) -> Value:
        """Scalar variable.  NF is special: it is computed from the fields."""
        frame = self.frame
        if frame is not None and name in frame:
            cell = frame[name]
            if cell.array is not None:
                raise AwkRuntimeError(f"can't use array {name} in scalar context")
            if cell.value is None:
                # First use, as a scalar: from now on it is one.
                cell.value = UNINIT
                cell.link = None
            return cell.value
        if name == "NF":
            return self.get_nf()
        v = self.vars.get(name)
        if v is None:
            if name in self.arrays:
                raise AwkRuntimeError(f"can't use array {name} in scalar context")
            # Reading an unset variable makes it a scalar (it can no longer
            # become an array), as in all three reference awks.
            self.vars[name] = v = UNINIT
        return v

    def set_var(self, name: str, v: Value) -> None:
        frame = self.frame
        if frame is not None and name in frame:
            cell = frame[name]
            if cell.array is not None:
                raise AwkRuntimeError(f"can't assign to {name}; it's an array name")
            cell.value = v
            cell.link = None  # a scalar now; the caller's variable is unaffected
            return
        if name == "NF":
            self.set_nf(v)
            return
        if name in self.arrays:
            raise AwkRuntimeError(f"can't assign to {name}; it's an array name")
        self.vars[name] = v

    def get_array(self, name: str) -> dict[str, Value]:
        frame = self.frame
        if frame is not None and name in frame:
            return self._cell_array(frame[name], name)
        return self._global_array(name)

    def _global_array(self, name: str) -> dict[str, Value]:
        arr = self.arrays.get(name)
        if arr is None:
            if name in self.vars or name == "NF":
                raise AwkRuntimeError(f"can't use scalar {name} as array")
            arr = self.arrays[name] = {}  # first use creates an empty array
        return arr

    def _cell_array(self, cell: Cell, name: str) -> dict[str, Value]:
        """The array a local variable holds, creating it on first use.

        If the cell is linked to an untyped variable of a caller, the array
        is created *there* (following the chain of links) and shared, so
        ``function f(a) { a[1] = 7 }  BEGIN { f(x); print x[1] }`` prints 7.
        """
        if cell.array is not None:
            return cell.array
        if cell.value is not None:
            raise AwkRuntimeError(f"can't use scalar {name} as array")
        link = cell.link
        if link is None:
            arr: dict[str, Value] = {}
        elif isinstance(link, Cell):
            arr = self._cell_array(link, name)
        else:
            arr = self._global_array(link)
        cell.array = arr
        cell.link = None
        return arr

    def var_kind(self, name: str) -> str:
        """"array", "scalar" or "untyped", *without* deciding the type
        (``length(x)`` must not turn an unused x into a scalar)."""
        frame = self.frame
        if frame is not None and name in frame:
            cell: Cell | str | None = frame[name]
            # Follow links until we reach a cell that knows, or a global.
            while isinstance(cell, Cell):
                if cell.array is not None:
                    return "array"
                if cell.value is not None:
                    return "scalar"
                cell = cell.link
            if cell is None:
                return "untyped"
            name = cell
        if name in self.arrays:
            return "array"
        if name in self.vars or name == "NF":
            return "scalar"
        return "untyped"

    def subscript(self, subs: list[ast.Expr]) -> str:
        """The string key for ``arr[i]`` or ``arr[i, j]`` (joined with SUBSEP)."""
        if len(subs) == 1:
            return self.str_of(self.eval(subs[0]))
        sep = self.str_of(self.vars["SUBSEP"])
        return sep.join(self.str_of(self.eval(e)) for e in subs)

    # lvalue references: resolve once, then get/set (avoids double evaluation)
    #
    # An "lvalue" is something that can be assigned to: a variable, an array
    # element or a field.  For ``arr[i++] += 1`` the subscript must be
    # evaluated only once, even though we read and then write the element.
    # ``resolve`` evaluates everything that has side effects and returns a
    # small tuple describing the location, e.g. ("var", "x"),
    # ("elem", <the dict>, "key") or ("field", 3); get_ref/set_ref then read
    # or write that location.
    def resolve(self, target: ast.Expr) -> tuple[Any, ...]:
        t = type(target)
        if t is ast.Var:
            return ("var", target.name)  # type: ignore[attr-defined]
        if t is ast.Index:
            return ("elem", self.get_array(target.name), self.subscript(target.subs))  # type: ignore[attr-defined]
        if t is ast.Field:
            return ("field", self._field_index(target.index))  # type: ignore[attr-defined]
        raise AwkRuntimeError("assignment to non-lvalue")

    def get_ref(self, ref: tuple[Any, ...]) -> Value:
        kind = ref[0]
        if kind == "var":
            return self.get_var(ref[1])
        if kind == "elem":
            arr, key = ref[1], ref[2]
            v = arr.get(key)
            if v is None:
                # Merely referencing arr[key] creates the element (AWK rule).
                v = arr[key] = UNINIT
            return v
        return self.get_field(ref[1])

    def set_ref(self, ref: tuple[Any, ...], v: Value) -> None:
        kind = ref[0]
        if kind == "var":
            self.set_var(ref[1], v)
        elif kind == "elem":
            ref[1][ref[2]] = v
        else:
            self.set_field(ref[1], v)

    def assign(self, target: ast.Expr, v: Value) -> None:
        self.set_ref(self.resolve(target), v)

    def regex_of(self, node: ast.Expr) -> re.Pattern[str]:
        """A /literal/ is used as is; any other expression's string value is
        treated as a regular expression ("dynamic regex")."""
        if type(node) is ast.Regex:
            return compile_ere(node.source)  # type: ignore[attr-defined]
        return compile_ere(self.str_of(self.eval(node)))

    # ------------------------------------------------------------ expressions
    def eval(self, node: ast.Expr) -> Value:
        """Evaluate an expression: find the handler for this node type in the
        dispatch table and call it with the node."""
        return self._eval[type(node)](node)

    def _num(self, node: ast.Num) -> Value:
        return node.value

    def _str(self, node: ast.Str) -> Value:
        return node.value

    def _regex_expr(self, node: ast.Regex) -> Value:
        # A bare /re/ used as a value means ($0 ~ /re/): 1 or 0.
        return 1.0 if compile_ere(node.source).search(self.record) else 0.0

    def _var(self, node: ast.Var) -> Value:
        return self.get_var(node.name)

    def _index(self, node: ast.Index) -> Value:
        # Reading arr[k] creates the element if missing (as get_ref does).
        arr = self.get_array(node.name)
        key = self.subscript(node.subs)
        v = arr.get(key)
        if v is None:
            v = arr[key] = UNINIT
        return v

    def _field_index(self, node: ast.Expr) -> int:
        n = to_num(self.eval(node))
        if n != n or n in (math.inf, -math.inf):
            raise AwkRuntimeError("invalid field index")
        return int(n)

    def _field(self, node: ast.Field) -> Value:
        return self.get_field(self._field_index(node.index))

    def _group(self, node: ast.Group) -> Value:
        raise AwkRuntimeError("parenthesised expression list used as a value")

    def _assign(self, node: ast.Assign) -> Value:
        ref = self.resolve(node.target)
        if node.op == "=":
            v = self.eval(node.value)
        else:
            # Evaluate the right-hand side first, then read the target
            # (matches gawk/mawk/BWK: ``x += x += 2`` with x == 1 gives 6).
            rhs = to_num(self.eval(node.value))
            v = _arith(node.op[0], to_num(self.get_ref(ref)), rhs)
        self.set_ref(ref, v)
        return v

    def _cond(self, node: ast.Cond) -> Value:
        return self.eval(node.if_true) if self.test(node.test) else self.eval(node.if_false)

    def _or(self, node: ast.Or) -> Value:
        # Python's ``or``/``and`` short-circuit like AWK's || and &&: the
        # right side is not evaluated if the left decides the answer.
        return 1.0 if self.test(node.left) or self.test(node.right) else 0.0

    def _and(self, node: ast.And) -> Value:
        return 1.0 if self.test(node.left) and self.test(node.right) else 0.0

    def _in(self, node: ast.In) -> Value:
        # ``(k in arr)`` tests membership without creating the element.
        return 1.0 if self.subscript(node.subs) in self.get_array(node.array) else 0.0

    def _match(self, node: ast.Match) -> Value:
        s = self.str_of(self.eval(node.left))
        found = self.regex_of(node.regex).search(s) is not None
        # ``!=`` between two booleans is "exclusive or": flips for ``!~``.
        return 1.0 if found != node.negate else 0.0

    def _compare(self, node: ast.Compare) -> Value:
        # values.compare decides numeric vs string comparison (strnum rules)
        # and returns negative, zero or positive, like C's strcmp.
        c = compare(self.eval(node.left), self.eval(node.right), self.convfmt)
        op = node.op
        if op == "==":
            r = c == 0
        elif op == "!=":
            r = c != 0
        elif op == "<":
            r = c < 0
        elif op == "<=":
            r = c <= 0
        elif op == ">":
            r = c > 0
        else:
            r = c >= 0
        return 1.0 if r else 0.0

    def _concat(self, node: ast.Concat) -> Value:
        return self.str_of(self.eval(node.left)) + self.str_of(self.eval(node.right))

    def _binary(self, node: ast.Binary) -> Value:
        return _arith(node.op, to_num(self.eval(node.left)), to_num(self.eval(node.right)))

    def _unary(self, node: ast.Unary) -> Value:
        if node.op == "!":
            return 0.0 if self.test(node.operand) else 1.0
        v = to_num(self.eval(node.operand))
        return -v if node.op == "-" else v

    def _incdec(self, node: ast.IncDec) -> Value:
        # ++x yields the new value, x++ the old one.
        ref = self.resolve(node.target)
        old = to_num(self.get_ref(ref))
        new = old + 1.0 if node.op == "++" else old - 1.0
        self.set_ref(ref, new)
        return new if node.prefix else old

    def _call(self, node: ast.Call) -> Value:
        # Builtins receive the argument *nodes*, not values (see builtins.py).
        return BUILTINS[node.name](self, node.args)

    def _user_call(self, node: ast.UserCall) -> Value:
        """Call a user-defined function.

        1. Evaluate the arguments left to right, in the *caller's* frame,
           binding each to the matching parameter (see _bind_arg).  Extra
           arguments are evaluated (for their side effects) and dropped;
           the parser already warned about them.
        2. Parameters with no argument are the function's local variables:
           fresh, untyped cells.
        3. Run the body with the new frame; ``return`` raises _ReturnEx
           carrying the value.  Falling off the end returns "uninitialized".
        4. Put the caller's frame back, however the body was left (return,
           exit, error): that is what ``finally`` guarantees.
        """
        fn = self.program.functions.get(node.name)
        if fn is None:
            # Not a parse error: AWK only complains if the call happens.
            raise AwkRuntimeError(f"calling undefined function {node.name}")
        params = fn.params
        frame: dict[str, Cell] = {}
        for i, arg in enumerate(node.args):
            if i < len(params):
                frame[params[i]] = self._bind_arg(arg)
            else:
                self.eval(arg)
        for p in params[len(node.args):]:
            frame[p] = Cell()
        saved = self.frame
        self.frame = frame
        try:
            self._block(fn.body)
        except _ReturnEx as r:
            return r.value
        finally:
            self.frame = saved
        return UNINIT

    def _bind_arg(self, arg: ast.Expr) -> Cell:
        """Make the callee's Cell for one argument.

        Scalars are passed by value: the cell gets a copy of the value.
        (Python strings and floats cannot be changed in place, so sharing
        the value object is as good as a copy.)  A bare variable name is
        special, because it may name an array (passed by reference: the
        cell shares the very same dict) or an untyped variable (the cell is
        linked to it, so that it can still become an array; see Cell).
        """
        if type(arg) is ast.Var:
            name = arg.name  # type: ignore[attr-defined]
            frame = self.frame
            if frame is not None and name in frame:
                c = frame[name]
                if c.array is not None:
                    return Cell(array=c.array)
                if c.value is not None:
                    return Cell(value=c.value)
                return Cell(link=c)  # an untyped local of the caller
            if name in self.arrays:
                return Cell(array=self.arrays[name])
            if name in self.vars or name == "NF":
                return Cell(value=self.get_var(name))
            return Cell(link=name)  # an untyped global
        return Cell(value=self.eval(arg))

    def _return(self, node: ast.Return) -> None:
        # Evaluating ``return arr`` for an array fails in get_var, as it
        # should: functions can only return scalars.
        value = UNINIT if node.value is None else self.eval(node.value)
        raise _ReturnEx(value)

    def _getline(self, node: ast.Getline) -> Value:
        """The getline forms; returns 1 (got a record), 0 (end) or -1 (error).

        * ``getline [var]``: next record of the main input; NR and FNR go up.
        * ``getline [var] < file``: from a file; NR/FNR unchanged.
        * ``cmd | getline [var]``: from a command's output; NR goes up.
        Without ``var`` the record replaces $0 (and so NF).
        """
        rs = self.str_of(self.vars["RS"])
        ref = None
        if node.kind == "simple" and node.target is not None:
            ref = self._getline_target(node.target)
        if node.kind == "simple":
            rec = self.next_main_record()
            if rec is None:
                return 0.0
            self.vars["NR"] = to_num(self.vars["NR"]) + 1.0
            self.vars["FNR"] = to_num(self.vars["FNR"]) + 1.0
        else:
            assert node.source is not None
            name = self.str_of(self.eval(node.source))
            if node.target is not None:
                ref = self._getline_target(node.target)
            if node.kind == "file":
                reader = self.io.get_input_file(name)
                if reader is None:
                    return -1.0
            else:
                reader = self.io.get_input_cmd(name)
            rec = reader.read_record(rs)
            if rec is None:
                return 0.0
            if node.kind == "cmd":
                self.vars["NR"] = to_num(self.vars["NR"]) + 1.0
        if ref is None:
            self.set_record(rec)
        else:
            self.set_ref(ref, make_strnum(rec))
        return 1.0

    def _getline_target(self, target: ast.Expr) -> tuple[Any, ...]:
        """Resolve getline's variable *before* reading, as gawk and mawk do.

        So the subscript in ``getline a[++c] < f`` is evaluated (and the
        element created) on every call, including the one that hits end of
        file or fails to open the file: the gawk test getline5 expects c = 2
        after reading a one-line file.  (onetrue-awk skips it, giving 1.)
        """
        ref = self.resolve(target)
        if ref[0] == "elem":
            self.get_ref(ref)  # referencing an element creates it
        return ref

    # ------------------------------------------------------------ statements
    def execute(self, node: ast.Stmt) -> None:
        """Run a statement via the statement dispatch table."""
        self._exec[type(node)](node)

    def _block(self, node: ast.Block) -> None:
        ex = self._exec  # local name: a small speed-up in this hot loop
        for stmt in node.body:
            ex[type(stmt)](stmt)

    def _expr_stmt(self, node: ast.ExprStmt) -> None:
        self.eval(node.expr)

    def _stream_for(self, redirect: str | None, dest: ast.Expr | None) -> IO[str]:
        if redirect is None:
            return self.out
        assert dest is not None
        return self.io.get_output(redirect, self.str_of(self.eval(dest)))

    def _print(self, node: ast.Print) -> None:
        if node.args:
            ofs = self.str_of(self.vars["OFS"])
            # Evaluate each argument and join the strings with OFS.
            text = ofs.join(self.out_str(self.eval(a)) for a in node.args)
        else:
            text = self.record
        stream = self._stream_for(node.redirect, node.dest)
        stream.write(text + self.str_of(self.vars["ORS"]))

    def _printf(self, node: ast.Printf) -> None:
        if not node.args:
            # A bare ``printf`` prints nothing, but still opens a redirection.
            self._stream_for(node.redirect, node.dest)
            return
        vals = [self.eval(a) for a in node.args]
        text = format_printf(self.str_of(vals[0]), vals[1:], self.convfmt)
        self._stream_for(node.redirect, node.dest).write(text)

    def _if(self, node: ast.If) -> None:
        if self.test(node.test):
            self.execute(node.then)
        elif node.orelse is not None:
            self.execute(node.orelse)

    # Loops: ``break`` and ``continue`` arrive as exceptions raised somewhere
    # inside the body (see the top of this file) and are caught here.
    def _while(self, node: ast.While) -> None:
        while self.test(node.test):
            try:
                self.execute(node.body)
            except _BreakEx:
                break
            except _ContinueEx:
                continue

    def _do_while(self, node: ast.DoWhile) -> None:
        while True:
            try:
                self.execute(node.body)
            except _BreakEx:
                break
            except _ContinueEx:
                pass
            if not self.test(node.test):
                break

    def _for(self, node: ast.For) -> None:
        # init and step are usually expressions, but may be print, printf
        # or delete statements (see Parser.parse_for_clause).
        if node.init is not None:
            self._for_clause(node.init)
        while node.test is None or self.test(node.test):
            try:
                self.execute(node.body)
            except _BreakEx:
                break
            except _ContinueEx:
                pass
            if node.step is not None:
                self._for_clause(node.step)

    def _for_clause(self, node: ast.Expr | ast.Stmt) -> None:
        if isinstance(node, ast.Stmt):
            self.execute(node)
        else:
            self.eval(node)

    def _for_in(self, node: ast.ForIn) -> None:
        arr = self.get_array(node.array)
        # Loop over a snapshot of the keys, since the body may add or delete
        # elements.  (The order is unspecified in AWK; ours is insertion order.)
        for key in list(arr):
            if key not in arr:
                continue  # deleted during the loop
            self.assign(node.var, key)
            try:
                self.execute(node.body)
            except _BreakEx:
                break
            except _ContinueEx:
                continue

    def _break(self, node: ast.Break) -> None:
        raise _BreakEx()

    def _continue(self, node: ast.Continue) -> None:
        raise _ContinueEx()

    def _next(self, node: ast.Next) -> None:
        raise _NextEx()

    def _exit(self, node: ast.Exit) -> None:
        # Bare ``exit`` keeps any status set by an earlier ``exit n``
        # (e.g. ``exit 3`` in a rule, then a plain ``exit`` in END).
        # ``& 0xFF`` keeps the low 8 bits, as the operating system does.
        code = self.exit_code
        if node.status is not None:
            code = int(to_num(self.eval(node.status))) & 0xFF
        raise ExitEx(code)

    def _delete(self, node: ast.Delete) -> None:
        arr = self.get_array(node.array)
        if node.subs is None:
            arr.clear()
        else:
            arr.pop(self.subscript(node.subs), None)


def _arith(op: str, x: float, y: float) -> float:
    """The arithmetic operators, on floating point numbers (AWK has no ints).

    ``%`` uses fmod (result has the sign of x, as in C), and ``^`` gives
    inf/nan where Python would raise an error.
    """
    if op == "+":
        return x + y
    if op == "-":
        return x - y
    if op == "*":
        return x * y
    if op == "/":
        if y == 0.0:
            raise AwkRuntimeError("division by zero")
        return x / y
    if op == "%":
        if y == 0.0:
            raise AwkRuntimeError("division by zero in %")
        return math.fmod(x, y)
    # '^'
    try:
        return math.pow(x, y)
    except OverflowError:
        return math.inf
    except ValueError:
        return math.nan
