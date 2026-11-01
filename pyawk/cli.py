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
import sys
from typing import IO, Sequence

from . import __version__
from .errors import AwkError
from .parser import parse
from .runtime import Interpreter

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
            if not eq or not name.isidentifier():
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
                if path == "-":
                    chunks.append(sys.stdin.read())
                else:
                    with open(path, encoding="utf-8", errors="surrogateescape") as f:
                        chunks.append(f.read())
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
    """Let stdout/stderr pass through bytes that are not valid UTF-8 (the input
    side does the same), and write "\\n" untranslated on every platform."""
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="surrogateescape", newline="")  # type: ignore[union-attr]


def run(
    source: str,
    operands: Sequence[str] = (),
    assignments: Sequence[tuple[str, str]] = (),
    stdout: IO[str] | None = None,
    stdin: IO[bytes] | None = None,
) -> int:
    """Parse and run an AWK program (used by the CLI and in-process tests)."""
    program = parse(source)
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
    _configure_stdio()
    try:
        source, assignments, operands = parse_args(argv)
        # parse_args folded -F into ``assignments`` as ("FS", value).  The
        # runtime applies all of them before BEGIN: Interpreter.__init__ calls
        # Interpreter.command_line_assign (runtime.py), which runs
        # lexer.process_escapes on the value, so ``-F '\t'`` means a tab,
        # exactly as for -v, and an undefined escape loses its backslash
        # (``-F 'b\.c'`` is the regex b.c, as in onetrue-awk and gawk).
        return run(source, operands, assignments)
    except UsageError as exc:
        sys.stderr.write(f"pyawk: {exc}\n{USAGE}")
        return 2
    except AwkError as exc:
        sys.stdout.flush()
        sys.stderr.write(f"pyawk: {exc}\n")
        return 2
    except BrokenPipeError:
        # The reader went away (e.g. ``pyawk ... | head``).  Point stdout at
        # /dev/null so Python's final flush at exit does not complain again.
        devnull = os.open(os.devnull, os.O_WRONLY)
        os.dup2(devnull, sys.stdout.fileno())
        return 2
    except KeyboardInterrupt:
        return 130  # conventional status for "interrupted by Ctrl-C" (128 + 2)


# The public names of this module (what ``from pyawk.cli import *`` gives).
__all__ = ["main", "run", "parse_args"]
