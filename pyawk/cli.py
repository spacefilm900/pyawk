"""Command line interface: ``pyawk [-F fs] [-v var=val]... ('prog' | -f file...) [operand...]``."""

from __future__ import annotations

import os
import sys
from typing import IO, Sequence

from . import __version__
from .errors import AwkError
from .lexer import process_escapes
from .parser import parse
from .runtime import Interpreter

USAGE = (
    "usage: pyawk [-F fs][-v var=value][-f progfile | 'prog'][file ...]\n"
)


class UsageError(Exception):
    pass


def parse_args(argv: Sequence[str]) -> tuple[str, list[tuple[str, str]], list[str]]:
    """Return (program_source, assignments, operands).  -F is folded into
    the assignments as ``FS=...`` so it is applied before BEGIN."""
    assignments: list[tuple[str, str]] = []
    progfiles: list[str] = []
    i = 0
    args = list(argv)

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
        if not arg.startswith("-") or arg == "-":
            break
        if arg.startswith("-F"):
            assignments.append(("FS", option_value("-F")))
        elif arg.startswith("-v"):
            spec = option_value("-v")
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
    interp = Interpreter(program, operands, assignments, stdout=stdout, stdin=stdin)
    return interp.run()


def main(argv: Sequence[str] | None = None) -> int:
    if argv is None:
        argv = sys.argv[1:]
    _configure_stdio()
    try:
        source, assignments, operands = parse_args(argv)
        # -F values get escape processing like -v values ("\t" -> tab).
        return run(source, operands, assignments)
    except UsageError as exc:
        sys.stderr.write(f"pyawk: {exc}\n{USAGE}")
        return 2
    except AwkError as exc:
        sys.stdout.flush()
        sys.stderr.write(f"pyawk: {exc}\n")
        return 2
    except BrokenPipeError:
        devnull = os.open(os.devnull, os.O_WRONLY)
        os.dup2(devnull, sys.stdout.fileno())
        return 2
    except KeyboardInterrupt:
        return 130


__all__ = ["main", "run", "parse_args", "process_escapes"]
