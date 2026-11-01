"""In-process tests for ENVIRON, input/output and nextfile.

The golden categories environ, io and nextfile check the visible output
against the reference awks; the tests here cover what those cannot see:
error reports, exit codes, stderr, and files written by the program.
"""

import io

import pytest

from pyawk.cli import run
from pyawk.errors import AwkRuntimeError


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
@pytest.mark.parametrize(
    "prog",
    ['BEGIN { getline x < "" }', 'BEGIN { getline < "" }', 'BEGIN { "" | getline }', 'BEGIN { print "x" | "" }'],
)
def test_null_file_or_command_name_is_an_error(prog: str) -> None:
    # onetrue-awk and gawk stop with a fatal error (mawk returns -1 / 0).
    with pytest.raises(AwkRuntimeError, match="null"):
        awk(prog)
