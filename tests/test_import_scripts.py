"""The import scripts stop cleanly when a reference awk or the upstream test
directory is missing, before they clear their destination directory."""

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod  # @dataclass looks the module up here
    spec.loader.exec_module(mod)
    return mod


def _program(directory: Path, name: str) -> Path:
    exe = directory / name
    exe.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    exe.chmod(0o755)
    return exe


@pytest.fixture
def bare_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    for var in ("PYAWK_REF_MAWK", "PYAWK_REF_ONETRUE", "PYAWK_REF_GAWK",
                "PYAWK_REF_ONETRUE_TESTDIR", "PYAWK_REF_GAWK_TESTDIR"):
        monkeypatch.delenv(var, raising=False)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    monkeypatch.setenv("PATH", str(bin_dir))
    return bin_dir


def _dest(tmp_path: Path, mod, monkeypatch: pytest.MonkeyPatch) -> Path:
    dest = tmp_path / "dest"
    dest.mkdir()
    (dest / "keep.awk").write_text("BEGIN {}\n", encoding="utf-8")
    monkeypatch.setattr(mod, "DEST", dest)
    return dest


@pytest.mark.parametrize("script", ["import_onetrue", "import_gawk"])
def test_missing_references_stop_before_anything_is_deleted(
    script: str, bare_env: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    mod = _load(script)
    dest = _dest(tmp_path, mod, monkeypatch)
    src = tmp_path / "src"
    src.mkdir()
    monkeypatch.setattr(sys, "argv", [f"{script}.py", "--src", str(src)])
    assert mod.main() == 2
    err = capsys.readouterr().err
    assert "PYAWK_REF_MAWK" in err and "PYAWK_REF_ONETRUE" in err and "PYAWK_REF_GAWK" in err
    assert (dest / "keep.awk").exists()


@pytest.mark.parametrize("script, var", [("import_onetrue", "PYAWK_REF_ONETRUE_TESTDIR"),
                                         ("import_gawk", "PYAWK_REF_GAWK_TESTDIR")])
def test_missing_test_dir_stops_before_anything_is_deleted(
    script: str, var: str, bare_env: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    mod = _load(script)
    dest = _dest(tmp_path, mod, monkeypatch)
    for name in ("mawk", "original-awk", "gawk"):
        _program(bare_env, name)
    monkeypatch.setattr(sys, "argv", [f"{script}.py"])
    assert mod.main() == 2
    assert var in capsys.readouterr().err
    assert (dest / "keep.awk").exists()
