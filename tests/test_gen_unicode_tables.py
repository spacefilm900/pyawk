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
    monkeypatch.delenv("PYAWK_REF_GAWK", raising=False)
    exe = _fake_gawk(tmp_path)
    monkeypatch.setenv("PATH", str(tmp_path))
    assert _load().default_gawk() == str(exe)


def test_default_gawk_is_the_reference_gawk(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # PYAWK_REF_GAWK names the reference gawk for all the tooling.
    _fake_gawk(tmp_path)
    mine = tmp_path / "mine"
    mine.mkdir()
    exe = _fake_gawk(mine)
    monkeypatch.setenv("PATH", str(tmp_path))
    monkeypatch.setenv("PYAWK_REF_GAWK", str(exe))
    assert _load().default_gawk() == str(exe)


def test_default_gawk_without_one_on_path_is_the_plain_name(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PYAWK_REF_GAWK", raising=False)
    monkeypatch.setenv("PATH", str(tmp_path))  # an empty directory
    assert _load().default_gawk() == "gawk"


def test_missing_gawk_is_a_clear_error(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    mod = _load()
    assert mod.main(["--gawk", str(tmp_path / "no-such-gawk"), "--check"]) == 2
    assert "cannot run" in capsys.readouterr().err


# The module is written and compared as UTF-8 whatever the locale says
# (CodeRabbit, PR #1).  It is ASCII today, but gawk's version line goes into
# it; with the platform's default codec a non-ASCII character there would
# fail to write (or compare differently) in an ASCII locale.  The child runs
# in such a locale: C, with Python's UTF-8 mode and locale coercion off.
_CHILD = """
import importlib.util, sys
from pathlib import Path
spec = importlib.util.spec_from_file_location("g", sys.argv[1])
g = importlib.util.module_from_spec(spec)
spec.loader.exec_module(g)
g.OUT = Path(sys.argv[2])
g.ROOT = g.OUT.parent
g.ask_gawk = lambda gawk: ()
g.render = lambda: "# gawk 5.4 \\u00e9dition\\n"
assert g.main(["--gawk", "x"]) == 0
assert g.main(["--gawk", "x", "--check"]) == 0
"""


def test_module_is_written_and_checked_as_utf8_in_an_ascii_locale(tmp_path: Path) -> None:
    import os
    import subprocess
    import sys

    out = tmp_path / "unicode_tables.py"
    env = dict(os.environ, LC_ALL="C", PYTHONUTF8="0", PYTHONCOERCECLOCALE="0")
    res = subprocess.run(
        [sys.executable, "-c", _CHILD, str(REPO / "scripts" / "gen_unicode_tables.py"), str(out)],
        env=env,
        capture_output=True,
        text=True,
    )
    assert res.returncode == 0, res.stderr
    assert out.read_bytes() == "# gawk 5.4 \u00e9dition\n".encode("utf-8")
