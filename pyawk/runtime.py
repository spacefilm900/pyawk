"""Tree-walking interpreter: records, fields, variables, I/O and evaluation."""

from __future__ import annotations

import codecs
import io
import math
import os
import random
import re
import subprocess
import sys
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

ENCODING = "utf-8"
ERRORS = "surrogateescape"
_ASSIGN_ARG_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*=")
_WS_FIELDS_RE = re.compile(r"[^ \t\n]+")


# ---------------------------------------------------------------- control flow
class _BreakEx(Exception):
    pass


class _ContinueEx(Exception):
    pass


class _NextEx(Exception):
    pass


class ExitEx(Exception):
    def __init__(self, code: int) -> None:
        super().__init__(code)
        self.code = code


# ---------------------------------------------------------------- input
class RecordReader:
    """Reads records from a binary stream for a (possibly changing) RS."""

    CHUNK = 65536

    def __init__(self, stream: IO[bytes], proc: subprocess.Popen[bytes] | None = None) -> None:
        self.stream = stream
        self.proc = proc
        self.decoder = codecs.getincrementaldecoder(ENCODING)(errors=ERRORS)
        self.buf = ""
        self.start = 0
        self.eof = False

    def _fill(self) -> bool:
        """Read more data; return False at end of input."""
        if self.eof:
            return False
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
        if self.start:
            self.buf = self.buf[self.start :] + text
            self.start = 0
        else:
            self.buf += text

    def read_record(self, rs: str) -> str | None:
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
            scanned = len(self.buf) - self.start  # already searched, relative
            if not self._fill():
                break
            scan = self.start + scanned
        if self.start < len(self.buf):
            rec = self.buf[self.start :]
            self.buf, self.start = "", 0
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
            if m.end() > m.start():
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
    rc = proc.wait()
    return rc if rc >= 0 else 256 - rc


# ---------------------------------------------------------------- output
class IOManager:
    """Tracks redirected output streams and getline input streams by name."""

    def __init__(self, stdout: IO[str], stdin: IO[bytes]) -> None:
        self.stdout = stdout
        self.stdin = stdin
        self.outputs: dict[str, tuple[IO[str], subprocess.Popen[bytes] | None]] = {}
        self.inputs: dict[str, RecordReader] = {}
        self.stdin_reader: RecordReader | None = None

    def get_stdin_reader(self) -> RecordReader:
        if self.stdin_reader is None:
            self.stdin_reader = RecordReader(self.stdin)
        return self.stdin_reader

    def get_output(self, kind: str, name: str) -> IO[str]:
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
        for name in list(self.outputs):
            self.close(name)
        for name in list(self.inputs):
            self.close(name)
        self.stdout.flush()


# ---------------------------------------------------------------- interpreter
class Interpreter:
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
        self.out: IO[str] = stdout if stdout is not None else sys.stdout
        self.io = IOManager(self.out, stdin if stdin is not None else sys.stdin.buffer)
        self.vars: dict[str, Value] = {
            "FS": " ", "OFS": " ", "ORS": "\n", "RS": "\n", "NR": 0.0, "FNR": 0.0,
            "SUBSEP": "\x1c", "CONVFMT": "%.6g", "OFMT": "%.6g", "RSTART": 0.0,
            "RLENGTH": -1.0, "FILENAME": "",
        }
        self.arrays: dict[str, dict[str, Value]] = {}
        operands = list(operands)
        argv = {"0": argv0}
        for i, op in enumerate(operands, 1):
            argv[str(i)] = make_strnum(op)
        self.arrays["ARGV"] = argv  # type: ignore[assignment]
        self.vars["ARGC"] = float(len(operands) + 1)
        self.arrays["ENVIRON"] = {k: make_strnum(v) for k, v in os.environ.items()}
        for name, value in assignments:
            self.command_line_assign(name, value)

        # Record state.
        self.record = ""
        self.record_value: Value | None = None
        self.fields: list[Value] | None = None
        self.nf = 0
        self.record_fs = " "

        # Main input iteration.
        self.argi = 1
        self.reader: RecordReader | None = None
        self.opened_any = False

        self.seed = 0.0
        self.rng = random.Random(0)
        self.exit_code = 0

        self._eval: dict[type, Callable[[Any], Value]] = {
            ast.Num: self._num, ast.Str: self._str, ast.Regex: self._regex_expr,
            ast.Var: self._var, ast.Index: self._index, ast.Field: self._field,
            ast.Assign: self._assign, ast.Cond: self._cond, ast.Or: self._or,
            ast.And: self._and, ast.In: self._in, ast.Match: self._match,
            ast.Compare: self._compare, ast.Concat: self._concat,
            ast.Binary: self._binary, ast.Unary: self._unary,
            ast.IncDec: self._incdec, ast.Call: self._call,
            ast.Getline: self._getline, ast.Group: self._group,
        }
        self._exec: dict[type, Callable[[Any], None]] = {
            ast.Block: self._block, ast.ExprStmt: self._expr_stmt,
            ast.Print: self._print, ast.Printf: self._printf, ast.If: self._if,
            ast.While: self._while, ast.DoWhile: self._do_while, ast.For: self._for,
            ast.ForIn: self._for_in, ast.Break: self._break,
            ast.Continue: self._continue, ast.Next: self._next,
            ast.Exit: self._exit, ast.Delete: self._delete,
        }

    # ------------------------------------------------------------ top level
    def run(self) -> int:
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
        except _NextEx:
            raise AwkRuntimeError("next used in BEGIN or END action") from None
        except (_BreakEx, _ContinueEx):
            raise AwkRuntimeError("break or continue outside a loop") from None
        finally:
            try:
                self.io.close_all()
            except BrokenPipeError:
                pass
        return self.exit_code

    def _main_loop(self) -> None:
        rules = self.program.rules
        while True:
            rec = self.next_main_record()
            if rec is None:
                return
            self.vars["NR"] = to_num(self.vars["NR"]) + 1.0
            self.vars["FNR"] = to_num(self.vars["FNR"]) + 1.0
            self.set_record(rec)
            try:
                for rule in rules:
                    pat = rule.pattern
                    if pat is not None and not self.test(pat):
                        continue
                    if rule.action is None:
                        self.out.write(self.get_record_text() + self.str_of(self.vars["ORS"]))
                    else:
                        self._block(rule.action)
            except _NextEx:
                pass

    def test(self, node: ast.Expr) -> bool:
        if type(node) is ast.Regex:
            return compile_ere(node.source).search(self.get_record_text()) is not None
        return to_bool(self.eval(node))

    # ------------------------------------------------------------ main input
    def next_main_record(self) -> str | None:
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
        argv = self.get_array("ARGV")
        while self.argi < to_num(self.vars["ARGC"]):
            key = str(self.argi)
            self.argi += 1
            arg = self.str_of(argv[key]) if key in argv else ""
            if arg == "":
                continue
            if _ASSIGN_ARG_RE.match(arg):
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
        self.set_var(name, make_strnum(process_escapes(value)))

    # ------------------------------------------------------------ conversions
    @property
    def convfmt(self) -> str:
        return to_str(self.vars["CONVFMT"])

    def str_of(self, v: Value) -> str:
        if type(v) is str:
            return v
        if type(v) is StrNum:
            return v.s
        return num_to_str(v, self.convfmt)  # type: ignore[arg-type]

    def out_str(self, v: Value) -> str:
        if type(v) is float:
            return num_to_str(v, to_str(self.vars["OFMT"]))
        return self.str_of(v)

    # ------------------------------------------------------------ records/fields
    def set_record(self, text: str) -> None:
        self.record = text
        self.record_value = None
        self.fields = None
        self.record_fs = self.str_of(self.vars["FS"])

    def get_record_text(self) -> str:
        return self.record

    def _split_record(self) -> list[Value]:
        if self.fields is None:
            self.fields = [make_strnum(p) for p in self.split_with_fs(self.record, self.record_fs)]
            self.nf = len(self.fields)
        return self.fields

    def split_with_fs(self, s: str, fs: str) -> list[str]:
        paragraph = self.vars["RS"] == ""
        if fs == " ":
            return _WS_FIELDS_RE.findall(s)
        if s == "":
            return []
        if fs == "":
            # Unspecified by POSIX; gawk, mawk and BWK awk split into characters.
            return list(s)
        if len(fs) == 1 and fs != "\\" and not paragraph:
            return s.split(fs)
        source = re.escape(fs) if len(fs) == 1 else fs
        if paragraph:
            return self._split_compiled(s, compile_ere("\n|" + source if len(fs) == 1 else "\n|(" + source + ")"))
        return self._split_compiled(s, compile_ere(source))

    def split_regex(self, s: str, ere: str) -> list[str]:
        if s == "":
            return []
        return self._split_compiled(s, compile_ere(ere))

    @staticmethod
    def _split_compiled(s: str, pat: re.Pattern[str]) -> list[str]:
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
        if i == 0:
            if self.record_value is None:
                self.record_value = make_strnum(self.record)
            return self.record_value
        if i < 0:
            raise AwkRuntimeError(f"trying to access out of range field {i}")
        fields = self._split_record()
        if i <= self.nf:
            return fields[i - 1]
        return UNINIT

    def set_field(self, i: int, v: Value) -> None:
        if i == 0:
            self.set_record(self.str_of(v))
            return
        if i < 0:
            raise AwkRuntimeError(f"trying to access out of range field {i}")
        fields = self._split_record()
        while len(fields) < i:
            fields.append(UNINIT)
        fields[i - 1] = v
        self.nf = len(fields)
        self._rebuild_record()

    def _rebuild_record(self) -> None:
        assert self.fields is not None
        ofs = self.str_of(self.vars["OFS"])
        self.record = ofs.join(self.str_of(f) for f in self.fields)
        self.record_value = None

    def get_nf(self) -> float:
        self._split_record()
        return float(self.nf)

    def set_nf(self, v: Value) -> None:
        n = int(to_num(v))
        if n < 0:
            raise AwkRuntimeError(f"NF set to negative value {n}")
        fields = self._split_record()
        del fields[n:]
        while len(fields) < n:
            fields.append(UNINIT)
        self.nf = n
        self._rebuild_record()

    # ------------------------------------------------------------ variables
    def get_var(self, name: str) -> Value:
        if name == "NF":
            return self.get_nf()
        v = self.vars.get(name)
        if v is None:
            if name in self.arrays:
                raise AwkRuntimeError(f"can't use array {name} in scalar context")
            return UNINIT
        return v

    def set_var(self, name: str, v: Value) -> None:
        if name == "NF":
            self.set_nf(v)
            return
        if name in self.arrays:
            raise AwkRuntimeError(f"can't assign to {name}; it's an array name")
        self.vars[name] = v

    def get_array(self, name: str) -> dict[str, Value]:
        arr = self.arrays.get(name)
        if arr is None:
            if name in self.vars:
                raise AwkRuntimeError(f"can't use scalar {name} as array")
            arr = self.arrays[name] = {}
        return arr

    def subscript(self, subs: list[ast.Expr]) -> str:
        if len(subs) == 1:
            return self.str_of(self.eval(subs[0]))
        sep = self.str_of(self.vars["SUBSEP"])
        return sep.join(self.str_of(self.eval(e)) for e in subs)

    # lvalue references: resolve once, then get/set (avoids double evaluation)
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
        if type(node) is ast.Regex:
            return compile_ere(node.source)  # type: ignore[attr-defined]
        return compile_ere(self.str_of(self.eval(node)))

    # ------------------------------------------------------------ expressions
    def eval(self, node: ast.Expr) -> Value:
        return self._eval[type(node)](node)

    def _num(self, node: ast.Num) -> Value:
        return node.value

    def _str(self, node: ast.Str) -> Value:
        return node.value

    def _regex_expr(self, node: ast.Regex) -> Value:
        return 1.0 if compile_ere(node.source).search(self.record) else 0.0

    def _var(self, node: ast.Var) -> Value:
        return self.get_var(node.name)

    def _index(self, node: ast.Index) -> Value:
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
        return 1.0 if self.test(node.left) or self.test(node.right) else 0.0

    def _and(self, node: ast.And) -> Value:
        return 1.0 if self.test(node.left) and self.test(node.right) else 0.0

    def _in(self, node: ast.In) -> Value:
        return 1.0 if self.subscript(node.subs) in self.get_array(node.array) else 0.0

    def _match(self, node: ast.Match) -> Value:
        s = self.str_of(self.eval(node.left))
        found = self.regex_of(node.regex).search(s) is not None
        return 1.0 if found != node.negate else 0.0

    def _compare(self, node: ast.Compare) -> Value:
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
        ref = self.resolve(node.target)
        old = to_num(self.get_ref(ref))
        new = old + 1.0 if node.op == "++" else old - 1.0
        self.set_ref(ref, new)
        return new if node.prefix else old

    def _call(self, node: ast.Call) -> Value:
        return BUILTINS[node.name](self, node.args)

    def _getline(self, node: ast.Getline) -> Value:
        rs = self.str_of(self.vars["RS"])
        if node.kind == "simple":
            rec = self.next_main_record()
            if rec is None:
                return 0.0
            self.vars["NR"] = to_num(self.vars["NR"]) + 1.0
            self.vars["FNR"] = to_num(self.vars["FNR"]) + 1.0
        else:
            assert node.source is not None
            name = self.str_of(self.eval(node.source))
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
        if node.target is None:
            self.set_record(rec)
        else:
            self.assign(node.target, make_strnum(rec))
        return 1.0

    # ------------------------------------------------------------ statements
    def execute(self, node: ast.Stmt) -> None:
        self._exec[type(node)](node)

    def _block(self, node: ast.Block) -> None:
        ex = self._exec
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
            text = ofs.join(self.out_str(self.eval(a)) for a in node.args)
        else:
            text = self.record
        stream = self._stream_for(node.redirect, node.dest)
        stream.write(text + self.str_of(self.vars["ORS"]))

    def _printf(self, node: ast.Printf) -> None:
        vals = [self.eval(a) for a in node.args]
        text = format_printf(self.str_of(vals[0]), vals[1:], self.convfmt)
        self._stream_for(node.redirect, node.dest).write(text)

    def _if(self, node: ast.If) -> None:
        if self.test(node.test):
            self.execute(node.then)
        elif node.orelse is not None:
            self.execute(node.orelse)

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
        if node.init is not None:
            self.eval(node.init)
        while node.test is None or self.test(node.test):
            try:
                self.execute(node.body)
            except _BreakEx:
                break
            except _ContinueEx:
                pass
            if node.step is not None:
                self.eval(node.step)

    def _for_in(self, node: ast.ForIn) -> None:
        arr = self.get_array(node.array)
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
