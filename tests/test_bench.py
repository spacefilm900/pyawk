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


def fake_which(installed: dict[str, str]):
    """A stand-in for shutil.which that knows only the given programs."""
    return lambda name: installed.get(name)


def test_no_machine_specific_paths() -> None:
    text = (REPO / "scripts" / "bench.py").read_text()
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
    fake.write_text("")
    awks = bench.find_awks({"onetrue": str(fake)})
    assert awks["onetrue"] == [str(fake)]
