"""Tests for scripts/gen_unicode_tables.py, which writes pyawk/unicode_tables.py.

The full generation asks gawk about every code point (slow, and it needs
gawk), so these tests cover the script's plumbing only.
"""

import importlib.util
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent


def _load():
    """Import the script as a module (scripts/ is not a package)."""
    spec = importlib.util.spec_from_file_location("gen_unicode_tables", REPO / "scripts" / "gen_unicode_tables.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _fake_gawk(directory: Path) -> Path:
    exe = directory / "gawk"
    exe.write_text("#!/bin/sh\nexit 0\n")
    exe.chmod(0o755)
    return exe


# The default gawk is the one on PATH, not a path that exists only on the
# machine the script was written on (CodeRabbit, PR #1); --gawk overrides it.
def test_default_gawk_is_the_one_on_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    exe = _fake_gawk(tmp_path)
    monkeypatch.setenv("PATH", str(tmp_path))
    assert _load().default_gawk() == str(exe)


def test_default_gawk_without_one_on_path_is_the_plain_name(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PATH", str(tmp_path))  # an empty directory
    assert _load().default_gawk() == "gawk"


def test_missing_gawk_is_a_clear_error(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    mod = _load()
    assert mod.main(["--gawk", str(tmp_path / "no-such-gawk"), "--check"]) == 2
    assert "cannot run" in capsys.readouterr().err
