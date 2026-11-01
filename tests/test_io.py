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


@pytest.mark.parametrize(
    ("expr", "value"),
    [
        ('"echo 5" | getline x + 1', "2"),
        ('"echo 5" | getline x - 2', "-1"),
        ('"echo 5" | getline x * 3 + 1', "4"),
        ('"echo 5" | getline x + 1 * 3', "4"),
        ('"echo 5" | getline x % 2', "1"),
        ('"echo 5" | getline x ^ 2 + 1', "2"),
        ('"echo 5" | getline x + 1 "b"', "2b"),
        ('"echo 5" | getline x "a" + 1', "11"),
        ('"echo 5" | getline + 1', "2"),
        ('"echo 5" | getline x - 1 < 1', "1"),
    ],
)
def test_cmd_getline_result_is_an_operand(expr: str, value: str) -> None:
    # POSIX grammar: ``unary_expr '|' simple_get`` is a complete operand, so
    # an arithmetic operator after it applies to getline's result (mawk and
    # gawk; onetrue-awk rejects some of these and reads others differently).
    assert awk(f'BEGIN {{ a = ({expr}); print a, x $0 }}')[0] == f"{value} 5\n"


# ---------------------------------------------------------------- nextfile
@pytest.fixture
def three_files(tmp_path: Path) -> list[str]:
    names = []
    for name, lines in (("a", 3), ("b", 2), ("c", 3)):
        path = tmp_path / name
        path.write_text("".join(f"{name}{i}\n" for i in range(1, lines + 1)))
        names.append(str(path))
    return names


def test_nextfile_skips_rest_of_file_and_resets_fnr(three_files: list[str]) -> None:
    out, code = awk('FNR == 2 { nextfile } { print FNR, NR, $0 } END { print NR, FNR, $0 }', "", *three_files)
    assert code == 0
    assert out == "1 1 a1\n1 3 b1\n1 5 c1\n6 2 c2\n"


def test_nextfile_from_inside_loops(three_files: list[str]) -> None:
    # The signal passes out of nested loops like ``next`` does.
    prog = "{ for (i = 0; i < 3; i++) while (1) { print $0; nextfile } }"
    assert awk(prog, "", *three_files)[0] == "a1\nb1\nc1\n"


def test_nextfile_closes_the_file_and_runs_later_assignments(three_files: list[str]) -> None:
    a, b, _ = three_files
    assert awk("{ print v, $0; nextfile }", "", a, "v=7", b)[0] == " a1\n7 b1\n"


def test_nextfile_on_stdin_discards_the_rest_of_it(three_files: list[str]) -> None:
    # mawk and gawk: a later "-" operand (or getline from "-") finds
    # standard input used up.  onetrue-awk carries on where it stopped.
    a = three_files[0]
    prog = '{ print FILENAME == "-" ? "stdin" : "file", $0; nextfile } END { print NR; print (getline x < "-") }'
    assert awk(prog, "s1\ns2\ns3\n", "-", a, "-")[0] == "stdin s1\nfile a1\n2\n0\n"


def test_nextfile_rule_order(three_files: list[str]) -> None:
    # Rules after the nextfile are not run for that record, as with next.
    out = awk('{ print "first", $0 } FNR == 1 { nextfile } { print "never", $0 }', "", *three_files)[0]
    assert out == "first a1\nfirst b1\nfirst c1\n"


# ---------------------------------------------------------------- fflush(name)
# fflush("name") on an open output: 0 on success, -1 if the flush fails,
# but a broken pipe is passed on so the program dies quietly by SIGPIPE.

class _FailingStream(io.StringIO):
    """A fake output stream whose flush() raises the error we give it."""

    def __init__(self, error: BaseException) -> None:
        super().__init__()
        self.error = error

    def flush(self) -> None:
        raise self.error


def _io_table_with(stream: io.StringIO) -> Any:
    """An I/O table with one open output, "out", writing to ``stream``."""
    from pyawk import runtime

    table = runtime.IOManager(io.StringIO(), io.BytesIO())
    table.outputs["out"] = (stream, None)
    return table


def test_named_fflush_returns_zero_on_success() -> None:
    assert _io_table_with(io.StringIO()).flush("out") == 0


def test_named_fflush_returns_minus_one_on_os_error() -> None:
    # A failed flush (disk full, say) must be reported, not hidden as 0.
    assert _io_table_with(_FailingStream(OSError(28, "No space"))).flush("out") == -1


def test_named_fflush_lets_broken_pipe_through() -> None:
    # A reader that has gone away must reach the CLI's SIGPIPE handling.
    with pytest.raises(BrokenPipeError):
        _io_table_with(_FailingStream(BrokenPipeError())).flush("out")


# ---------------------------------------------------------- close() of the
# special names.  POSIX: close returns zero if the close was successful.
def test_close_dev_stdout_after_printing_to_it_returns_zero_and_keeps_stdout() -> None:
    # All three references return 0.  Afterwards gawk keeps writing to
    # standard output, while mawk and onetrue-awk have closed it and lose
    # (or fail on) later output.  Closing the *name* "/dev/stdout" should
    # not take standard output away from the rest of the program, so we
    # follow gawk; the earlier output is flushed.
    assert awk('BEGIN { print "a" > "/dev/stdout"; r = close("/dev/stdout"); print r; print "b" }') == (
        "a\n0\nb\n",
        0,
    )


def test_close_dev_stderr_after_printing_to_it_returns_zero(capsys: pytest.CaptureFixture[str]) -> None:
    out, _ = awk('BEGIN { print "e" > "/dev/stderr"; print close("/dev/stderr"); print "x" > "/dev/stderr" }')
    assert out == "0\n"  # all three references


def test_close_of_a_special_name_never_opened_returns_minus_one() -> None:
    # Nothing was opened, so nothing was closed successfully (gawk).  mawk
    # and onetrue-awk close the real descriptor for /dev/stdout, and return
    # 0 for /dev/stderr; all three return -1 for "-".
    assert awk('BEGIN { print close("/dev/stdout"), close("/dev/stderr"), close("-"), close("/dev/stdin") }') == (
        "-1 -1 -1 -1\n",
        0,
    )


@pytest.mark.parametrize("name", ["-", "/dev/stdin"])
def test_close_of_stdin_read_by_getline_uses_it_up(name: str) -> None:
    # All three references (mawk crashes on /dev/stdin): close returns 0,
    # and standard input cannot be reopened, so later reads of it find
    # nothing -- getline < name, plain getline and the main loop.
    prog = f'BEGIN {{ getline x < "{name}"; print x, close("{name}")'
    prog += f'; print (getline y < "{name}"); print (getline z) }} {{ print "main", $0 }}'
    assert awk(prog, "1\n2\n3\n") == ("1 0\n0\n0\n", 0)
