"""Where the reference awks are found: awk_tests/references.py.

The tooling (regen_golden, regex_fuzz, the import scripts, bench,
gen_unicode_tables) compares pyawk with three reference awks: mawk,
onetrue-awk (BWK awk) and gawk, run as ``gawk --posix``.  Each one is found
by an environment variable naming its program (PYAWK_REF_MAWK,
PYAWK_REF_ONETRUE, PYAWK_REF_GAWK), or else by its usual names on PATH.

The last tests run the references themselves; a reference that is not
installed is skipped, with the reason saying how to point at it.
"""

import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "awk_tests"))
import harness  # noqa: E402
import references  # noqa: E402


def _program(directory: Path, name: str) -> Path:
    """A tiny executable stand-in for an awk."""
    exe = directory / name
    exe.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    exe.chmod(0o755)
    return exe


@pytest.fixture
def bare_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """No PYAWK_REF_ variables and an empty PATH directory to fill in."""
    for var in references.ENV_VARS.values():
        monkeypatch.delenv(var, raising=False)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    monkeypatch.setenv("PATH", str(bin_dir))
    return bin_dir


def test_the_three_references_and_their_variables() -> None:
    assert references.ENV_VARS == {
        "mawk": "PYAWK_REF_MAWK",
        "onetrue": "PYAWK_REF_ONETRUE",
        "gawk": "PYAWK_REF_GAWK",
    }


def test_variable_names_do_not_clash_with_the_harness_test_variables() -> None:
    # The harness removes every PYAWK_T_* variable before running a case
    # (they belong to the ENVIRON tests), so a reference variable must
    # never start that way.
    assert not any(var.startswith("PYAWK_T_") for var in references.ENV_VARS.values())


def test_harness_keeps_the_reference_variables(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    for name in ("a.awk", "a.in"):
        (tmp_path / name).write_text("", encoding="utf-8")
    monkeypatch.setenv("PYAWK_REF_GAWK", "/opt/gawk")
    proc = harness.run_case(harness.Case("basic", "a", tmp_path), ["/bin/sh", "-c", "env", "sh"])
    assert "PYAWK_REF_GAWK=/opt/gawk" in proc.stdout


def test_found_on_path_by_its_usual_names(bare_env: Path) -> None:
    mawk = _program(bare_env, "mawk")
    gawk = _program(bare_env, "gawk")
    onetrue = _program(bare_env, "original-awk")
    assert references.command("mawk") == [str(mawk)]
    assert references.command("gawk") == [str(gawk), "--posix"]
    assert references.command("onetrue") == [str(onetrue)]


@pytest.mark.parametrize("program_name", ["original-awk", "onetrue-awk", "bwk-awk"])
def test_onetrue_goes_by_several_names(bare_env: Path, program_name: str) -> None:
    exe = _program(bare_env, program_name)
    assert references.command("onetrue") == [str(exe)]


def test_plain_awk_is_never_taken_for_a_reference(bare_env: Path) -> None:
    # "awk" may be any of the three (BWK awk on macOS, mawk on Debian).
    _program(bare_env, "awk")
    assert references.available() == {}


def test_the_variable_wins_over_path(bare_env: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _program(bare_env, "gawk")
    mine = _program(tmp_path, "my-gawk")
    monkeypatch.setenv("PYAWK_REF_GAWK", str(mine))
    assert references.command("gawk") == [str(mine), "--posix"]


def test_the_variable_may_name_a_program_on_path(bare_env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    exe = _program(bare_env, "gawk-5.4")
    monkeypatch.setenv("PYAWK_REF_GAWK", "gawk-5.4")
    assert references.command("gawk") == [str(exe), "--posix"]


def test_a_relative_path_is_made_absolute(bare_env: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # The tools run awks in other directories (a case's own directory),
    # so ./a.out must keep meaning the a.out where the user typed it.
    exe = _program(tmp_path, "a.out")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("PYAWK_REF_ONETRUE", "./a.out")
    assert references.command("onetrue") == [str(exe)]


def test_a_variable_naming_nothing_runnable_is_an_error_not_a_fallback(
    bare_env: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A typo must not silently fall back to some other mawk on PATH.
    _program(bare_env, "mawk")
    monkeypatch.setenv("PYAWK_REF_MAWK", str(tmp_path / "no-such-mawk"))
    with pytest.raises(references.MissingReference) as err:
        references.command("mawk")
    assert "PYAWK_REF_MAWK" in str(err.value)
    assert "no-such-mawk" in str(err.value)
    assert "mawk" not in references.available()


def test_a_missing_reference_says_how_to_point_at_it(bare_env: Path) -> None:
    with pytest.raises(references.MissingReference) as err:
        references.command("onetrue")
    message = str(err.value)
    assert "PYAWK_REF_ONETRUE" in message
    assert "original-awk" in message


def test_an_empty_variable_counts_as_unset(bare_env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    exe = _program(bare_env, "mawk")
    monkeypatch.setenv("PYAWK_REF_MAWK", "")
    assert references.command("mawk") == [str(exe)]


def test_commands_reports_every_missing_reference_at_once(bare_env: Path) -> None:
    mawk = _program(bare_env, "mawk")
    assert references.commands(["mawk"]) == {"mawk": [str(mawk)]}
    with pytest.raises(references.MissingReference) as err:
        references.commands(["mawk", "onetrue", "gawk"])
    message = str(err.value)
    assert "PYAWK_REF_ONETRUE" in message and "PYAWK_REF_GAWK" in message
    assert "PYAWK_REF_MAWK" not in message


def test_available_lists_only_the_found_ones(bare_env: Path) -> None:
    exe = _program(bare_env, "mawk")
    assert references.available() == {"mawk": [str(exe)]}


def test_explicit_programs_override_the_lookup(bare_env: Path) -> None:
    # The import scripts' --onetrue/--mawk/--gawk options name a program
    # directly; gawk still gets --posix.
    mawk = _program(bare_env, "mawk")
    got = references.commands(["mawk", "gawk"], explicit={"gawk": "/opt/gawk", "mawk": None})
    assert got == {"mawk": [str(mawk)], "gawk": ["/opt/gawk", "--posix"]}


def test_test_dir_from_the_option_then_the_variable(
    bare_env: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    given, from_env = tmp_path / "given", tmp_path / "env"
    given.mkdir()
    from_env.mkdir()
    monkeypatch.setenv("PYAWK_REF_GAWK_TESTDIR", str(from_env))
    assert references.test_dir("gawk", given) == given
    assert references.test_dir("gawk", None) == from_env


def test_onetrue_test_dir_defaults_to_testdir_beside_its_program(
    bare_env: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # onetrue-awk is usually built in its source tree, where testdir/ lives.
    src = tmp_path / "onetrue-awk"
    (src / "testdir").mkdir(parents=True)
    monkeypatch.setenv("PYAWK_REF_ONETRUE", str(_program(src, "a.out")))
    monkeypatch.delenv("PYAWK_REF_ONETRUE_TESTDIR", raising=False)
    assert references.test_dir("onetrue", None) == src / "testdir"


@pytest.mark.parametrize("name, var", [("gawk", "PYAWK_REF_GAWK_TESTDIR"), ("onetrue", "PYAWK_REF_ONETRUE_TESTDIR")])
def test_a_missing_test_dir_says_how_to_give_one(
    bare_env: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, name: str, var: str
) -> None:
    monkeypatch.delenv(var, raising=False)
    with pytest.raises(references.MissingReference) as err:
        references.test_dir(name, None)
    assert var in str(err.value) and "--src" in str(err.value)
    # A directory that does not exist is no better than none.
    with pytest.raises(references.MissingReference):
        references.test_dir(name, tmp_path / "nowhere")


# --- The real references, where installed --------------------------------


def _require(name: str) -> list[str]:
    try:
        return references.command(name)
    except references.MissingReference as exc:
        pytest.skip(str(exc))


@pytest.mark.parametrize("name", ["mawk", "onetrue", "gawk"])
def test_reference_runs(name: str) -> None:
    cmd = _require(name)
    proc = subprocess.run([*cmd, "BEGIN { print 1 + 2 }"], capture_output=True, text=True, timeout=30,
                          env={**os.environ, "LC_ALL": "C"}, encoding="utf-8")
    assert (proc.returncode, proc.stdout) == (0, "3\n")


@pytest.mark.parametrize("name", ["mawk", "onetrue", "gawk"])
def test_reference_agrees_with_a_golden_case(name: str) -> None:
    # One plain case every reference agrees on: a quick check that the
    # program found really is an awk that the golden corpus fits.
    cmd = _require(name)
    case = next(c for c in harness.discover(["basic"]) if c.id == "basic/begin_end")
    assert harness.run_case(case, cmd).stdout == case.expected()
