"""Tests for the test tooling itself: awk_tests/harness.py and regen_golden.py."""

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "awk_tests"))
import harness  # noqa: E402


def _environ_case() -> harness.Case:
    return next(c for c in harness.discover(["environ"]) if c.path(".environ").exists())


def test_inherited_pyawk_t_variables_are_removed(monkeypatch: pytest.MonkeyPatch) -> None:
    # A stray PYAWK_T_ variable from the user's shell must not leak into
    # ENVIRON cases, or their output would depend on who runs them.
    monkeypatch.setenv("PYAWK_T_STRAY", "leak")
    proc = harness.run_case(_environ_case(), ["/bin/sh", "-c", "env", "sh"])
    assert "PYAWK_T_STRAY" not in proc.stdout
