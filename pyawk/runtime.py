"""The runtime: records, fields, variables, I/O and the main loop.

Role in the pipeline: cli -> lexer -> parser -> AST -> compiler ->
**runtime**.  The runtime receives the parsed ``ast.Program``, has
compiler.py turn it into Python closures, and runs those.  Entry point:
``Interpreter(program, operands, assignments).run()``, which returns the
exit status.

The work is split in two: compiler.py decides what each piece of the
program *does* (once, before running), and this file holds the *state*
the compiled code works on (variables, the current record and its fields,
open files) together with the operations on it that are too large or too
rare to inline into the compiled code.

How an AWK program runs (``Interpreter.run``):

1. Run the BEGIN blocks.
2. Unless the program is only BEGIN blocks, read input records one at a
   time (lines, by default), from each file operand or else standard input.
   For each record, try every ``pattern { action }`` rule in order.
3. Run the END blocks, then close all files and pipes.

Main pieces in this file:

* ``ExitEx``, the exception that implements ``exit``,
* ``Cell``: one local variable of a running function call,
* ``RecordReader``: splits an input stream into records according to RS,
* ``IOManager``: files and pipes opened by ``print >``, ``getline <`` etc.,
* ``Interpreter``: variables, arrays, fields and the record loop.
"""

# Treat type hints as text, so newer syntax such as ``str | None`` is allowed.
from __future__ import annotations

import codecs
import io
import os
import random
import re
import subprocess
import sys
import threading
from typing import IO, Any, Callable, Iterable, TypeVar

from . import ast
from .compiler import NEXT, NEXTFILE, CompiledProgram, Compiler, NextFromFunction
from .errors import AwkRuntimeError
from .lexer import process_escapes
from .regex import LONG_TEXT, LongestPattern, compile_span, for_long
from .values import UNINIT, StrNum, Value, make_strnum, num_to_str, to_num, to_str

# Input bytes that are not valid UTF-8 are carried through unchanged
# ("surrogateescape"), so binary-ish data is not corrupted or rejected.
ENCODING = "utf-8"
ERRORS = "surrogateescape"
# An operand like ``n=5`` is an assignment, not a file name.
_ASSIGN_ARG_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*=")
_WS_FIELDS_RE = re.compile(r"[^ \t\n]+")


# ---------------------------------------------------------------- control flow
# ``exit`` may happen anywhere, even inside a function called from the
# middle of an expression, and must abandon everything up to ``run``.
# Python's way to do that is to *raise* an exception: it unwinds the calls
# until a matching ``try: ... except`` catches it.  (break, continue, next
# and return travel as return values instead; see compiler.py.)
class ExitEx(Exception):
    """Raised by ``exit [code]``; carries the exit status."""

    def __init__(self, code: int) -> None:
        super().__init__(code)
        self.code = code


# ---------------------------------------------------------------- functions
# How many Python stack frames we allow.  Each level of AWK function call
# runs a few nested compiled closures (function body -> statement ->
# expression -> call), about four for a simple recursive function such as
# ``function r(n) { if (n == 0) return 0; return 1 + r(n - 1) }`` and more
# when the recursive call sits deeper inside loops or expressions.
# Python's default limit of 1000 would stop recursion after ~250 AWK
# levels; this limit allows about 100 000 levels of that function (gawk
# and mawk manage well over 10 000), and deeper runaway recursion is
# reported as an error rather than crashing.
RECURSION_LIMIT = 400_000
# Running out of the *operating-system* stack would crash Python outright,
# so the interpreter runs in a thread with a stack this large (bytes).
THREAD_STACK_SIZE = 1 << 30

# ``TypeVar`` names "some type": run_with_big_stack returns whatever type
# the function it is given returns.
_T = TypeVar("_T")


def run_with_big_stack(fn: Callable[[], _T]) -> _T:
    """Call ``fn()`` in a helper thread with a big stack and a high
    recursion limit, and return its result (or raise its exception).

    Used for deeply recursive AWK functions and for very deeply nested
    programs (see cli.run).  Three safety rules:

    * If this platform will not give us a big stack, or the operating
      system refuses to start the thread (``RuntimeError: can't start new
      thread``, e.g. under ``ulimit -v``), ``fn`` simply runs here instead,
      with Python's usual (shallow, but safe) recursion limit.
    * The thread is a *daemon* thread: Python does not wait for daemon
      threads when the program ends.  On Ctrl-C the main thread stops
      waiting (KeyboardInterrupt, see cli.main) while the helper may still
      be inside an endless AWK loop; a normal thread would keep the
      process alive.
    * The stack size and recursion limit are put back afterwards, however
      we leave (``finally``).
    """
    # A one-element list lets the thread hand back its result (or the
    # exception it raised), since a thread cannot return a value.
    result: list[Any] = []

    def target() -> None:
        try:
            result.append(fn())
        except BaseException as exc:  # re-raised in the calling thread below
            result.append(exc)

    old_limit = sys.getrecursionlimit()
    try:
        old_size = threading.stack_size(THREAD_STACK_SIZE)
    except (ValueError, RuntimeError):
        return fn()  # no big stacks here: run normally
    # The limit is raised before the thread starts, so it is in force from
    # the thread's first instruction.
    sys.setrecursionlimit(max(old_limit, RECURSION_LIMIT))
    try:
        thread = threading.Thread(target=target, daemon=True)
        try:
            thread.start()
        except RuntimeError:
            # The thread could not be started; restore the old stack size
            # and limit (``finally`` below) and run in this thread instead.
            started = False
        else:
            started = True
            thread.join()  # wait for it; Ctrl-C raises KeyboardInterrupt here
    finally:
        threading.stack_size(old_size)
        sys.setrecursionlimit(old_limit)
    if not started:
        return fn()
    if isinstance(result[0], BaseException):
        raise result[0]
    return result[0]  # type: ignore[no-any-return]


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
        pat = compile_span(rs)
        if len(self.buf) - self.start > LONG_TEXT:
            # Avoid Python's quadratic worst case (see regex.py); the
            # automaton marks every match start in one pass over the buffer,
            # shared by all the records read from it.
            pat = for_long(pat)
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


def _check_name(name: str, what: str) -> None:
    """Refuse an empty file or command name, as onetrue-awk and gawk do
    (mawk quietly fails instead: getline returns -1, print does nothing)."""
    if name == "":
        raise AwkRuntimeError(f"null file name in {what} redirection")


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
        # The special names "/dev/stdout" and "/dev/stderr" (used by print)
        # and "-" and "/dev/stdin" (used by getline <) that the program has
        # used since it last closed them.  They are never really opened,
        # so they are not in self.outputs or self.inputs; this set lets
        # close() tell "open" (returns 0) from "never opened" (returns -1).
        self.specials_used: set[str] = set()

    def get_stdin_reader(self) -> RecordReader:
        if self.stdin_reader is None:
            self.stdin_reader = RecordReader(self.stdin)
        return self.stdin_reader

    def use_up_stdin(self) -> None:
        """Make standard input look finished: drop what is buffered and read
        no more.  It cannot be reopened, so after close("-") or ``nextfile``
        every later read of it finds nothing (mawk and gawk).  We do not
        read the rest first, which could wait forever on a terminal."""
        reader = self.get_stdin_reader()
        reader.buf, reader.start, reader.eof = "", 0, True

    def get_output(self, kind: str, name: str) -> IO[str]:
        """Stream for ``>``, ``>>`` or ``|`` (kind) to ``name``, opening it once."""
        entry = self.outputs.get(name)
        if entry is not None:
            return entry[0]
        _check_name(name, "print")
        # "-" is *not* standard output here: mawk and onetrue-awk write to a
        # file called "-" (only gawk treats it as stdout).  For getline,
        # "-" does mean standard input (get_input_file).
        if name == "/dev/stdout":
            self.specials_used.add(name)
            return self.stdout
        if name == "/dev/stderr":
            self.specials_used.add(name)
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
        _check_name(name, "getline")
        if name in ("-", "/dev/stdin"):
            self.specials_used.add(name)
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
            _check_name(cmd, "getline")
            self.flush_all()
            proc = subprocess.Popen(cmd, shell=True, stdout=subprocess.PIPE)
            assert proc.stdout is not None
            reader = RecordReader(proc.stdout, proc)
            self.inputs[cmd] = reader
        return reader

    def close(self, name: str) -> int:
        """close(name): returns the command's exit status, 0, or -1 if not open.

        Our own pending standard output is *not* flushed first (mawk and
        onetrue-awk don't either), so a command's output can come before
        lines we printed after starting it.  Starting a command does flush
        everything, so lines printed before it always come first.

        The special names are never really opened (see specials_used).
        Closing one that the program used returns 0, as in all three
        reference awks:

        * "/dev/stdout" and "/dev/stderr" are flushed but stay usable (as in
          gawk; mawk and onetrue-awk really close the descriptor, so later
          output to standard output is lost);
        * "-" and "/dev/stdin" use up standard input, since it cannot be
          reopened: later getline < "-", plain getline and the main input
          loop all find nothing (all three references agree).

        A special name the program never used returns -1 like any other
        name that is not open (gawk).
        """
        if name in self.specials_used:
            self.specials_used.discard(name)
            if name == "/dev/stdout":
                self.stdout.flush()
            elif name == "/dev/stderr":
                sys.stderr.flush()
            else:
                self.use_up_stdin()
            return 0
        status = -1
        entry = self.outputs.pop(name, None)
        if entry is not None:
            stream, proc = entry
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

    def flush(self, name: str) -> int:
        """fflush(name): 0 if ``name`` is an open output flushed successfully, else -1."""
        if name == "/dev/stdout":
            self.stdout.flush()
            return 0
        if name == "/dev/stderr":
            sys.stderr.flush()
            return 0
        entry = self.outputs.get(name)
        if entry is None:
            return -1  # not open, or open only for reading
        try:
            entry[0].flush()
        except BrokenPipeError:
            # The reader has gone away: let this reach the CLI, which ends
            # the program quietly the way a C awk dies of SIGPIPE.
            raise
        except OSError:
            return -1  # any other failure (disk full, ...) is reported
        return 0

    def close_all(self) -> None:
        # Close redirections first, waiting for commands to finish, then
        # flush stdout: a command's output comes before our own buffered
        # output (mawk and gawk; onetrue-awk flushes stdout first).
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
        # The local variables of the function call now running, as a list
        # of Cells (one per parameter, in order); None when no function is
        # running.  Each call makes a new list and puts the caller's back on
        # return, so this is in effect a stack of call frames.  The compiler
        # knows which names are parameters; every other name is global
        # (self.vars / self.arrays).
        self.frame: list[Cell] | None = None
        # The value of the ``return`` statement that just ran (compiler.py).
        self.ret_value: Value = UNINIT
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

        # Record state.  Fields are split *lazily*: reading a record only
        # stores its text, and ``fields`` stays None until a program actually
        # uses $1, NF etc.  Many programs only look at $0, so this saves work.
        self.record = ""  # text of $0
        self.record_value: Value | None = None  # $0 as a strnum, made on demand
        # $1..$NF once split, else None.  An entry stays None until that
        # field is first read: deciding whether text looks like a number
        # (make_strnum) costs time, and most programs read few fields.
        self.fields: list[Value | None] | None = None
        self.field_text: list[str] = []  # the split text behind ``fields``
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

        # -v and -F settings (with escape processing; see command_line_assign).
        # They run last, once every attribute above exists: assigning NF
        # (``-v NF=3``) rebuilds the record, which needs the record state.
        for name, value in assignments:
            self.command_line_assign(name, value)

    # ------------------------------------------------------------ top level
    def run(self) -> int:
        """Run the program (see _run) with room for deep recursion.

        Deeply recursive AWK functions need a deep Python stack, so when
        the program defines functions we raise Python's recursion limit
        and run in a helper thread whose stack is large enough for it.
        (The main thread's stack size is fixed when the process starts.)
        """
        if not self.program.functions:
            try:
                prog = self._compile()
            except RecursionError:
                # Compiling recurses about twice per level of a long chain
                # such as ``1 + 1 + ... + 1``, so a few hundred terms exceed
                # Python's default limit.  Compile (again) and run with the
                # big stack; the compiled closures nest as deeply, so the
                # program must run there too.
                return run_with_big_stack(self._run)
            return self._run(prog)
        return run_with_big_stack(self._run)

    def _compile(self) -> CompiledProgram:
        # Compiling never fails: mistakes that AWK reports only when the
        # code runs are compiled into code that raises then.  (It can run
        # out of stack, though; see run.)
        return Compiler(self).compile_program(self.program)

    def _run(self, prog: CompiledProgram | None = None) -> int:
        """BEGIN, the main record loop, END; returns the exit status.

        ``exit`` in BEGIN or a rule skips the remaining input but still runs
        END; ``exit`` in END stops at once.  ``finally`` makes sure output
        files and pipes are closed however we leave.
        """
        if prog is None:
            prog = self._compile()
        finished = False  # True once END is done with no error in flight
        try:
            try:
                for block in prog.begin:
                    self._run_special(block, "BEGIN")
                if prog.rules or prog.end:
                    self._main_loop(prog)
            except ExitEx as e:
                self.exit_code = e.code
            try:
                for block in prog.end:
                    self._run_special(block, "END")
            except ExitEx as e:
                self.exit_code = e.code
            finished = True
        # (``from None`` hides the internal exception from error reports.)
        except RecursionError:
            self.frame = None
            raise AwkRuntimeError("function call nesting too deep (runaway recursion?)") from None
        finally:
            try:
                self.io.close_all()
            except BrokenPipeError:
                # The reader of our output has gone away.  After a normal
                # finish, let cli.main turn that into death by SIGPIPE, as
                # for a C awk; otherwise keep the error already on its way.
                if finished:
                    raise
        return self.exit_code

    @staticmethod
    def _run_special(block: Any, which: str) -> None:
        """Run a compiled BEGIN or END block (``which`` names it)."""
        # The parser rejects ``next`` written directly in BEGIN or END, but
        # a function called from here may still execute one.  There is no
        # record to skip, so (like gawk) report it as a fatal error.
        try:
            block()
        except NextFromFunction:
            raise AwkRuntimeError(f"next called from a function in {which}") from None

    def _main_loop(self, prog: CompiledProgram) -> None:
        """The implicit AWK loop: for each record, run each matching rule."""
        # ``enumerate`` yields (position, rule) pairs; the position indexes
        # this rule's flag in self.range_open.  Each rule is a tuple
        # (pattern, range end, action) of compiled closures (or None).
        rules = list(enumerate(prog.rules))
        vars_ = self.vars
        while True:
            rec = self.next_main_record()
            if rec is None:
                return
            nr = vars_["NR"]
            vars_["NR"] = (nr if type(nr) is float else to_num(nr)) + 1.0
            fnr = vars_["FNR"]
            vars_["FNR"] = (fnr if type(fnr) is float else to_num(fnr)) + 1.0
            self.set_record(rec)
            # (A ``try`` costs nothing in Python unless something is raised.)
            try:
                for i, (pat, end, action) in rules:
                    if end is not None:
                        if not self._in_range(i, pat, end):  # type: ignore[arg-type]
                            continue
                    elif pat is not None and not pat():
                        continue
                    if action is None:  # a bare pattern means { print }
                        self.out.write(self.record + self.str_of(vars_["ORS"]))
                        continue
                    sig = action()
                    if sig is NEXT:
                        break  # ``next``: abandon this record's rules
                    if sig is NEXTFILE:
                        self.skip_file()  # and abandon the rest of the file
                        break
            except NextFromFunction:
                # ``next`` inside a function called from a pattern or an
                # action: abandon this record's rules just the same.
                pass

    def _in_range(self, i: int, start: Any, end: Any) -> bool:
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
            if not start():
                return False
        # Opened just now, or already open: does this record close it?
        self.range_open[i] = not end()
        return True

    # ------------------------------------------------------------ main input
    def next_main_record(self) -> str | None:
        """Next record from the main input, moving on to the next file as each
        one ends; None when all input is exhausted."""
        while True:
            if self.reader is None and not self._open_next_input():
                return None
            assert self.reader is not None
            rs = self.vars["RS"]
            if type(rs) is not str:
                rs = self.str_of(rs)
            # A one-character RS (usually newline) is by far the most common
            # case, so call its reader directly rather than via read_record.
            if len(rs) == 1:
                rec = self.reader._read_char(rs)
            else:
                rec = self.reader.read_record(rs)
            if rec is not None:
                return rec
            if self.reader is not self.io.stdin_reader:
                self.reader.close()
            self.reader = None

    def skip_file(self) -> None:
        """``nextfile``: drop the rest of the current input file, so the next
        record comes from the next operand (and FNR starts again at 1).

        Standard input cannot be reopened, so skipping it means it is used
        up: a later "-" operand or ``getline < "-"`` finds nothing, as in
        mawk and gawk (onetrue-awk carries on where it stopped); see
        IOManager.use_up_stdin.
        """
        reader = self.reader
        if reader is None:
            return
        if reader is self.io.stdin_reader:
            self.io.use_up_stdin()
        else:
            reader.close()
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

    # ------------------------------------------------------------ records/fields
    def set_record(self, text: str) -> None:
        """Install a new $0; fields will be split from it when first needed."""
        self.record = text
        self.record_value = None
        self.fields = None
        self.record_fs = self.str_of(self.vars["FS"])

    def split_record(self) -> list[Value | None]:
        """Split $0 into fields if not done yet (lazy splitting); return them.

        Every field is a strnum: it compares as a number if it looks like
        one.  That check is itself lazy: the list starts out full of None
        and get_field classifies a field's text when it is first read.
        """
        if self.fields is None:
            pieces = self.split_with_fs(self.record, self.record_fs, paragraph=self.vars["RS"] == "")
            self.field_text = pieces
            self.nf = len(pieces)
            # ``[None] * n`` is a list of n None values.
            self.fields = [None] * self.nf
        return self.fields

    def _all_fields(self) -> list[Value]:
        """The fields, every one classified: needed before changing them."""
        fields = self.split_record()
        if None in fields:
            text = self.field_text
            for i, v in enumerate(fields):
                if v is None:
                    fields[i] = make_strnum(text[i])
        return fields  # type: ignore[return-value]  # no None left

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
            # Fast path: Python's own str.split() (no argument) splits at
            # runs of whitespace and drops leading/trailing whitespace, which
            # is AWK's rule, but its idea of whitespace is wider than AWK's
            # blank/tab/newline.  For pure-ASCII text the extra characters are
            # \r \v \f and \x1c-\x1f; if none is present the two agree.
            # ``in`` on a string is a fast C scan, cheaper than a regex.
            if (
                s.isascii()
                and "\r" not in s
                and "\v" not in s
                and "\f" not in s
                and "\x1c" not in s
                and "\x1d" not in s
                and "\x1e" not in s
                and "\x1f" not in s
            ):
                return s.split()
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
            return self._split_compiled(s, compile_span("\n|" + source if len(fs) == 1 else "\n|(" + source + ")"))
        return self._split_compiled(s, compile_span(source))

    def split_regex(self, s: str, ere: str) -> list[str]:
        if s == "":
            return []
        return self._split_compiled(s, compile_span(ere))

    # ``@staticmethod``: a plain function kept in the class; it has no ``self``.
    @staticmethod
    def _split_compiled(s: str, pat: re.Pattern[str] | LongestPattern) -> list[str]:
        """Split s at each non-empty match of pat."""
        pieces: list[str] = []
        pos = 0
        if len(s) > LONG_TEXT:
            pat = for_long(pat)  # Python's search could be quadratic here
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
        fields = self.fields
        if fields is None:
            fields = self.split_record()
        if i <= self.nf:
            v = fields[i - 1]  # Python lists count from 0, AWK fields from 1
            if v is None:
                v = fields[i - 1] = make_strnum(self.field_text[i - 1])
            return v
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
        fields = self._all_fields()
        while len(fields) < i:
            fields.append("")  # gap fields are empty strings (see get_field)
        fields[i - 1] = v
        self.nf = len(fields)
        self._rebuild_record()

    def _rebuild_record(self) -> None:
        """Recompute $0 from the fields, joined with the current OFS."""
        assert self.fields is not None
        ofs = self.str_of(self.vars["OFS"])
        # Most fields are already strings (or strnums, whose .s is their
        # text); only numbers need converting with CONVFMT.  A list built
        # with [...] joins faster than a generator.
        str_of = self.str_of
        self.record = ofs.join([f if type(f) is str else str_of(f) for f in self.fields])
        # A rebuilt $0 is a plain string, never a strnum (onetrue-awk and
        # gawk; mawk agrees except after an NF assignment).
        self.record_value = self.record

    def get_nf(self) -> float:
        if self.fields is None:
            self.split_record()
        return float(self.nf)

    def set_nf(self, v: Value) -> None:
        """Assigning NF truncates or extends the fields and rebuilds $0."""
        n = int(to_num(v))
        if n < 0:
            raise AwkRuntimeError(f"NF set to negative value {n}")
        fields = self._all_fields()
        del fields[n:]  # remove everything from index n to the end
        while len(fields) < n:
            fields.append("")  # new fields are empty strings (see get_field)
        self.nf = n
        self._rebuild_record()

    # ------------------------------------------------------------ variables
    #
    # These methods deal with *global* variables only.  Local variables
    # (function parameters) are handled by the code compiler.py generates,
    # since it knows at compile time which names are parameters.
    # A variable's first use decides whether it is a scalar or an array,
    # and using it the other way afterwards is an error.
    def get_var(self, name: str) -> Value:
        """Global scalar variable.  NF is special: it is computed from the fields."""
        if name == "NF":
            return self.get_nf()
        v = self.vars.get(name)
        if v is None:
            v = self.new_global_scalar(name)
        return v

    def new_global_scalar(self, name: str) -> Value:
        """First read of an unset global: it becomes a scalar (it can no
        longer become an array), as in all three reference awks."""
        if name in self.arrays:
            raise AwkRuntimeError(f"can't use array {name} in scalar context")
        self.vars[name] = UNINIT
        return UNINIT

    def set_var(self, name: str, v: Value) -> None:
        if name == "NF":
            self.set_nf(v)
            return
        if name in self.arrays:
            raise AwkRuntimeError(f"can't assign to {name}; it's an array name")
        self.vars[name] = v

    def get_array(self, name: str) -> dict[str, Value]:
        return self.global_array(name)

    def global_array(self, name: str) -> dict[str, Value]:
        arr = self.arrays.get(name)
        if arr is None:
            if name in self.vars or name == "NF":
                raise AwkRuntimeError(f"can't use scalar {name} as array")
            arr = self.arrays[name] = {}  # first use creates an empty array
        return arr

    def cell_array(self, cell: Cell, name: str) -> dict[str, Value]:
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
            arr = self.cell_array(link, name)
        else:
            arr = self.global_array(link)
        cell.array = arr
        cell.link = None
        return arr

    def var_kind(self, name: str) -> str:
        """"array", "scalar" or "untyped" for a global, *without* deciding
        the type (``length(x)`` must not turn an unused x into a scalar)."""
        if name in self.arrays:
            return "array"
        if name in self.vars or name == "NF":
            return "scalar"
        return "untyped"
