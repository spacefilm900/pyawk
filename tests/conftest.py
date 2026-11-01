"""pytest loads this file automatically before the tests in this directory.

It makes two directories importable without installing anything: the
repository root (for the ``pyawk`` package) and awk_tests/ (for the shared
golden-test ``harness`` module).
"""

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "awk_tests"))


import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def c_locale(monkeypatch):
    """Run every test in the C locale unless it chooses otherwise.

    The locale decides between bytes and characters (pyawk/charset.py), and
    the machine running the tests may well use a UTF-8 locale.  Setting
    LC_ALL here covers in-process runs and pyawk processes the tests start;
    tests of UTF-8 mode set the mode or the variable themselves.
    """
    monkeypatch.setenv("LC_ALL", "C")
    yield
    from pyawk import charset

    charset.set_mode(False)
