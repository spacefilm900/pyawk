"""In-process tests for ENVIRON, input/output and nextfile.

The golden categories environ, io and nextfile check the visible output
against the reference awks; the tests here cover what those cannot see:
error reports, exit codes, stderr, and files written by the program.
"""

import io
import signal
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from pyawk.cli import run
from pyawk.errors import AwkRuntimeError

REPO = Path(__file__).resolve().parent.parent


def awk(prog: str, data: str = "", *operands: str) -> tuple[str, int]:
    """Run ``prog`` in this process; return (stdout text, exit status)."""
    out = io.StringIO()
    code = run(prog, operands, (), stdout=out, stdin=io.BytesIO(data.encode()))
    return out.getvalue(), code


# ---------------------------------------------------------------- ENVIRON
def test_environ_reflects_process_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PYAWK_UNIT", " 42 ")
    assert awk('BEGIN { v = ENVIRON["PYAWK_UNIT"]; print "[" v "]", (v == 42), (v < 5) }')[0] == "[ 42 ] 1 0\n"


def test_environ_is_an_array_name() -> None:
    # All three references refuse to use ENVIRON as a scalar.
    with pytest.raises(AwkRuntimeError):
        awk("BEGIN { ENVIRON = 1 }")
    with pytest.raises(AwkRuntimeError):
        awk("BEGIN { print ENVIRON + 0 }")


# ---------------------------------------------------------------- I/O
def pyawk_process(prog: str, **kwargs: Any) -> subprocess.Popen[str]:
    """Start ``python -m pyawk prog`` as a real process (stdout piped)."""
    return subprocess.Popen(
        [sys.executable, "-m", "pyawk", prog], stdout=subprocess.PIPE, text=True, cwd=REPO, **kwargs
    )


def test_reader_going_away_kills_us_with_sigpipe() -> None:
    # Like `awk ... | head -1`: when the reader of our standard output
    # exits, mawk, onetrue-awk and gawk are all killed by SIGPIPE, which a
    # shell reports as status 141.  Python shows that as -SIGPIPE.
    proc = pyawk_process("BEGIN { for (i = 0; i < 1000000; i++) print i }", stderr=subprocess.PIPE)
    assert proc.stdout is not None and proc.stderr is not None
    assert proc.stdout.readline() == "0\n"
    proc.stdout.close()
    assert proc.wait(timeout=20) == -signal.SIGPIPE
    assert proc.stderr.read() == ""  # silently, like the reference awks


def test_command_going_away_kills_us_with_sigpipe() -> None:
    # print | "head -1": head exits after one line; the next write kills
    # us with SIGPIPE (mawk and onetrue-awk; gawk reports a fatal error).
    proc = pyawk_process('BEGIN { for (i = 0; i < 1000000; i++) print i | "head -1"; print "not reached" }')
    out, _ = proc.communicate(timeout=20)
    assert out == "0\n"
    assert proc.returncode == -signal.SIGPIPE


@pytest.mark.parametrize(
    "prog",
    ['BEGIN { getline x < "" }', 'BEGIN { getline < "" }', 'BEGIN { "" | getline }', 'BEGIN { print "x" | "" }'],
)
def test_null_file_or_command_name_is_an_error(prog: str) -> None:
    # onetrue-awk and gawk stop with a fatal error (mawk returns -1 / 0).
    with pytest.raises(AwkRuntimeError, match="null"):
        awk(prog)


def test_reader_gone_before_final_flush_kills_us_with_sigpipe() -> None:
    # The reader exits before we write anything; the failure only shows
    # when our output is flushed at the end.
    proc = pyawk_process('BEGIN { system("sleep 0.3"); print "x" }', stderr=subprocess.PIPE)
    assert proc.stdout is not None and proc.stderr is not None
    proc.stdout.close()
    assert proc.wait(timeout=20) == -signal.SIGPIPE
    assert proc.stderr.read() == ""
