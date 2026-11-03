"""Tests for scripts/bench.py's choice of awks (CodeRabbit v1.0, finding 6).

The benchmark must not depend on the author's machine: the reference awks
are looked up by name on PATH (``shutil.which``), and only the ones that
are installed are timed unless others are named explicitly.
"""

import importlib.util
import shutil
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent

# scripts/ is not a package, so load bench.py from its path.
_spec = importlib.util.spec_from_file_location("bench", REPO / "scripts" / "bench.py")
assert _spec is not None and _spec.loader is not None
bench = importlib.util.module_from_spec(_spec)
sys.modules["bench"] = bench  # @dataclass looks the module up by name
_spec.loader.exec_module(bench)


@pytest.fixture(autouse=True)
def no_reference_variables(monkeypatch: pytest.MonkeyPatch) -> None:
    # The PYAWK_REF_* variables of whoever runs the tests must not change
    # what these tests see; the tests that need one set it themselves.
    for var in ("PYAWK_REF_MAWK", "PYAWK_REF_ONETRUE", "PYAWK_REF_GAWK"):
        monkeypatch.delenv(var, raising=False)


def fake_which(installed: dict[str, str]):
    """A stand-in for shutil.which that knows only the given programs."""
    return lambda name: installed.get(name)


def test_no_machine_specific_paths() -> None:
    text = (REPO / "scripts" / "bench.py").read_text(encoding="utf-8")
    assert "/workspace" not in text
    assert "/usr/bin/awk" not in text


def test_only_installed_awks_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(shutil, "which", fake_which({"mawk": "/opt/bin/mawk"}))
    awks = bench.find_awks({})
    assert awks == {"pyawk": [sys.executable, "-m", "pyawk"], "mawk": ["/opt/bin/mawk"]}


def test_gawk_runs_in_posix_mode_and_onetrue_by_its_names(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(shutil, "which", fake_which({"gawk": "/x/gawk", "original-awk": "/x/original-awk"}))
    awks = bench.find_awks({})
    assert awks["gawk"] == ["/x/gawk", "--posix"]
    assert awks["onetrue"] == ["/x/original-awk"]
    assert "mawk" not in awks  # plain "awk" is never assumed to be mawk


def test_explicit_paths_override_lookup(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(shutil, "which", fake_which({}))
    fake = tmp_path / "a.out"
    fake.write_text("", encoding="utf-8")
    awks = bench.find_awks({"onetrue": str(fake)})
    assert awks["onetrue"] == [str(fake)]


def test_relative_explicit_path_is_resolved_against_the_callers_directory(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # The awks run with the repository as their working directory, so
    # ``--path onetrue=./a.out`` given elsewhere must be made absolute first
    # (CodeRabbit, PR #1).  A bare name is still looked up on PATH.
    monkeypatch.setattr(shutil, "which", fake_which({}))
    monkeypatch.chdir(tmp_path)
    awks = bench.find_awks({"onetrue": "./a.out", "gawk": "gawk"})
    assert awks["onetrue"] == [str(tmp_path / "a.out")]
    assert awks["gawk"] == ["gawk", "--posix"]


def test_absolute_explicit_path_is_kept(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(shutil, "which", fake_which({}))
    assert bench.find_awks({"mawk": "/opt/x/mawk"})["mawk"] == ["/opt/x/mawk"]


@pytest.mark.parametrize("locale", ["C", "C.UTF-8"])
def test_locale_is_passed_to_the_awks(locale: str) -> None:
    # --locale sets LC_ALL for every timed command (default C, i.e. bytes).
    _, out = bench.time_one(["sh", "-c", 'printf %s "$LC_ALL"'], runs=1, locale=locale)
    assert out == locale.encode()
    _, out = bench.time_one(["sh", "-c", 'printf %s "$LC_ALL"'], runs=1)
    assert out == b"C"


def test_reference_variables_choose_the_awks(monkeypatch: pytest.MonkeyPatch) -> None:
    # PYAWK_REF_* (see awk_tests/references.py) wins over PATH, as in the
    # rest of the tooling; --path still wins over both.
    monkeypatch.setattr(shutil, "which", fake_which({"gawk": "/usr/bin/gawk", "/opt/gawk": "/opt/gawk",
                                                     "/opt/mawk": "/opt/mawk"}))
    monkeypatch.setenv("PYAWK_REF_GAWK", "/opt/gawk")
    monkeypatch.setenv("PYAWK_REF_MAWK", "/opt/mawk")
    awks = bench.find_awks({"mawk": "/x/mawk"})
    assert awks["gawk"] == ["/opt/gawk", "--posix"]
    assert awks["mawk"] == ["/x/mawk"]


def test_a_bad_reference_variable_is_reported_and_left_out(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(shutil, "which", fake_which({"gawk": "/usr/bin/gawk"}))
    monkeypatch.setenv("PYAWK_REF_GAWK", "/no/such/gawk")
    assert "gawk" not in bench.find_awks({})
    assert "PYAWK_REF_GAWK=/no/such/gawk" in capsys.readouterr().err
