"""Command line interface: ``pyawk [-F fs] [-v var=val]... ('prog' | -f file...) [operand...]``.

Role in the pipeline: **cli** -> lexer -> parser -> AST -> runtime.  This is
the front door.  It works out the program text and settings from the command
line, then hands the text to the parser and the resulting tree to the
runtime.  It also turns errors into a message on stderr and exit status 2.

Entry points:

* ``main(argv)``: what ``python -m pyawk`` runs; returns the exit status.
* ``run(source, ...)``: parse and run a program in-process (used by tests).
* ``parse_args(argv)``: just the option handling.
"""

# Treat type hints as text, so newer syntax such as ``IO[str] | None`` is allowed.
from __future__ import annotations

import os
import signal
import sys
import threading
from typing import IO, Sequence

from . import __version__, charset
from .errors import AwkError
from .parser import parse
from .runtime import Interpreter, run_with_big_stack

USAGE = (
    "usage: pyawk [-F fs][-v var=value][-f progfile | 'prog'][file ...]\n"
)


class UsageError(Exception):
    """Bad command line (unknown option, missing program ...)."""


def parse_args(argv: Sequence[str]) -> tuple[str, list[tuple[str, str]], list[str]]:
    """Return (program_source, assignments, operands).  -F is folded into
    the assignments as ``FS=...`` so it is applied before BEGIN.

    Options come first; the first argument that is not an option is the
    program text (unless -f gave program files), and everything after it is
    an operand (input files or ``var=value``), passed on as ARGV.

    The result is a *tuple* of three values; callers unpack it with
    ``source, assignments, operands = parse_args(argv)``.
    """
    assignments: list[tuple[str, str]] = []
    progfiles: list[str] = []
    i = 0
    args = list(argv)

    # A helper defined inside this function, so it can see ``args`` and ``i``.
    # It accepts both ``-Fx`` and ``-F x``.  ``nonlocal i`` is required because
    # it advances the outer loop's position ``i`` (assigning to ``i`` would
    # otherwise create a new local variable).
    def option_value(flag: str) -> str:
        nonlocal i
        arg = args[i]
        if len(arg) > 2:
            return arg[2:]
        i += 1
        if i >= len(args):
            raise UsageError(f"option {flag} requires an argument")
        return args[i]

    while i < len(args):
        arg = args[i]
        if arg == "--":
            i += 1
            break
        if arg == "--version" or arg == "-V":
            raise SystemExit(_print_version())
        if arg in ("-h", "--help"):
            sys.stdout.write(USAGE)
            raise SystemExit(0)
        # "-" alone means standard input, so it is an operand, not an option.
        if not arg.startswith("-") or arg == "-":
            break
        if arg.startswith("-F"):
            assignments.append(("FS", option_value("-F")))
        elif arg.startswith("-v"):
            spec = option_value("-v")
            # partition("=") splits at the first "=" into three parts:
            # "x=a=b" -> ("x", "=", "a=b"); with no "=" the middle part is "".
            name, eq, value = spec.partition("=")
            if not eq or not (name.isidentifier() and name.isascii()):
                raise UsageError(f"improper assignment: -v {spec}")
            assignments.append((name, value))
        elif arg.startswith("-f"):
            progfiles.append(option_value("-f"))
        else:
            raise UsageError(f"unknown option {arg}")
        i += 1

    if progfiles:
        chunks = []
        for path in progfiles:
            try:
                # Read raw bytes and decode them as the locale says
                # (charset.decode), like any other input.
                if path == "-":
                    chunks.append(charset.decode(sys.stdin.buffer.read()))
                else:
                    with open(charset.to_os(path), "rb") as f:
                        chunks.append(charset.decode(f.read()))
            except OSError as exc:
                raise UsageError(f"couldn't open file {path}: {exc.strerror}") from None
        # Several -f files act like one program, in the order given.
        source = "\n".join(chunks)
    else:
        if i >= len(args):
            raise UsageError("no program given")
        source = args[i]
        i += 1
    return source, assignments, args[i:]


def _print_version() -> int:
    sys.stdout.write(f"pyawk {__version__}\n")
    return 0


def _configure_stdio() -> None:
    """Make stdout/stderr encode text the way the locale mode says
    (charset.py), so the bytes we read are the bytes we write, and write
    "\\n" untranslated on every platform."""
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding=charset.ENCODING, errors=charset.ERRORS, newline="")  # type: ignore[union-attr]


def _flush_stdout_or_give_up() -> None:
    """Flush standard output before an error message; if even that fails
    (the disk is full), point it at /dev/null so the text still waiting
    there is thrown away instead of failing again when Python exits."""
    try:
        sys.stdout.flush()
    except OSError:
        devnull = os.open(os.devnull, os.O_WRONLY)
        os.dup2(devnull, sys.stdout.fileno())
        os.close(devnull)


def run(
    source: str,
    operands: Sequence[str] = (),
    assignments: Sequence[tuple[str, str]] = (),
    stdout: IO[str] | None = None,
    stdin: IO[bytes] | None = None,
    utf8: bool | None = None,
) -> int:
    """Parse and run an AWK program (used by the CLI and in-process tests).

    ``utf8`` chooses the locale mode (charset.py): True for UTF-8 mode,
    False for the C locale, None to decide from LC_ALL, LC_CTYPE and LANG.
    The mode is set before parsing, since it changes what string escapes
    and regular expressions mean.
    """
    charset.set_mode(charset.locale_is_utf8() if utf8 is None else utf8)
    try:
        program = parse(source)
    except RecursionError:
        # The parser calls itself once or more for each nesting level, e.g.
        # about 15 times per parenthesis, so a program with ~65 nested
        # parentheses already exceeds Python's default limit of 1000 calls.
        # Such programs are rare, so rather than always paying for a helper
        # thread we try again only now, with the big stack and high limit.
        # If even that is not enough, the RecursionError reaches main().
        program = run_with_big_stack(lambda: parse(source))
    # Non-fatal complaints found while parsing (e.g. a function called with
    # more arguments than it has parameters) go to stderr, then we run.
    for warning in program.warnings:
        sys.stderr.write(f"pyawk: warning: {warning}\n")
    interp = Interpreter(program, operands, assignments, stdout=stdout, stdin=stdin)
    return interp.run()


def main(argv: Sequence[str] | None = None) -> int:
    """Run pyawk with a command line (default: the real one); return the exit
    status: the program's ``exit`` code, or 2 for any error."""
    if argv is None:
        argv = sys.argv[1:]
    # The locale decides between characters and bytes (charset.py).  Set the
    # mode first: the arguments, the program text and all I/O depend on it.
    charset.undo_python_locale_coercion()
    charset.set_mode(charset.locale_is_utf8())
    argv = [charset.from_os(a) for a in argv]
    _configure_stdio()
    try:
        source, assignments, operands = parse_args(argv)
        # parse_args folded -F into ``assignments`` as ("FS", value).  The
        # runtime applies all of them before BEGIN: Interpreter.__init__ calls
        # Interpreter.command_line_assign (runtime.py), which runs
        # lexer.process_escapes on the value, so ``-F '\t'`` means a tab,
        # exactly as for -v, and an undefined escape loses its backslash
        # (``-F 'b\.c'`` is the regex b.c, as in onetrue-awk and gawk).
        return run(source, operands, assignments, utf8=charset.UTF8)
    except UsageError as exc:
        sys.stderr.write(f"pyawk: {exc}\n{USAGE}")
        return 2
    except AwkError as exc:
        _flush_stdout_or_give_up()
        sys.stderr.write(f"pyawk: {exc}\n")
        return 2
    except BrokenPipeError:
        # The reader went away: our standard output (``pyawk ... | head``)
        # or a command we print to (``print | "head -1"``).  A C program is
        # simply killed by the SIGPIPE signal at that point, and mawk,
        # onetrue-awk and gawk all end that way (the shell reports status
        # 141).  Python ignores SIGPIPE and raises this exception instead,
        # so we restore the default action and send the signal to ourselves.
        # First point stdout at /dev/null, so nothing else tries to write
        # to the broken pipe on the way out.
        devnull = os.open(os.devnull, os.O_WRONLY)
        os.dup2(devnull, sys.stdout.fileno())
        if hasattr(signal, "SIGPIPE"):  # not on Windows
            signal.signal(signal.SIGPIPE, signal.SIG_DFL)
            os.kill(os.getpid(), signal.SIGPIPE)
        return 2
    except OSError as exc:
        # Any other failure of the operating system that the interpreter
        # did not turn into an AWK error (for example a write to standard
        # output on a full disk): report it briefly rather than as a
        # Python traceback, and exit with status 2 like the C awks.
        _flush_stdout_or_give_up()
        sys.stderr.write(f"pyawk: {exc.strerror or exc}\n")
        return 2
    except RecursionError:
        # Parsing or compiling a program nested too deeply even for the big
        # stack (tens of thousands of levels); running it is caught inside
        # the interpreter.  Report it like any other error, not as a trace.
        sys.stdout.flush()
        sys.stderr.write("pyawk: program is nested too deeply\n")
        return 2
    except KeyboardInterrupt:
        # 130 is the conventional status for "interrupted by Ctrl-C" (128 + 2).
        if threading.active_count() > 1:
            # The program is still running in the helper thread (see
            # runtime.run_with_big_stack), maybe in the middle of writing to
            # stdout.  A normal Python shutdown would then try to flush
            # stdout, find it busy and abort with "Fatal Python error".  So
            # leave at once with os._exit, which skips the shutdown steps.
            # Unflushed output is lost, just as when a C awk is killed by
            # Ctrl-C.
            sys.stderr.flush()
            os._exit(130)
        return 130


# The public names of this module (what ``from pyawk.cli import *`` gives).
__all__ = ["main", "run", "parse_args"]
