"""Tests for the test tooling itself: awk_tests/harness.py and regen_golden.py."""

import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "awk_tests"))
import harness  # noqa: E402


def _load_regen():
    """Import scripts/regen_golden.py as a module (scripts/ is not a package)."""
    spec = importlib.util.spec_from_file_location("regen_golden", REPO / "scripts" / "regen_golden.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _environ_case() -> harness.Case:
    return next(c for c in harness.discover(["environ"]) if c.path(".environ").exists())


def test_inherited_pyawk_t_variables_are_removed(monkeypatch: pytest.MonkeyPatch) -> None:
    # A stray PYAWK_T_ variable from the user's shell must not leak into
    # ENVIRON cases, or their output would depend on who runs them.
    monkeypatch.setenv("PYAWK_T_STRAY", "leak")
    proc = harness.run_case(_environ_case(), ["/bin/sh", "-c", "env", "sh"])
    assert "PYAWK_T_STRAY" not in proc.stdout


def _fake_case(tmp_path: Path, recorded: str | None) -> SimpleNamespace:
    out = tmp_path / "x.out"
    if recorded is not None:
        out.write_text(recorded)
    return SimpleNamespace(id="io/close_status", path=lambda ext: out, expected=lambda: out.read_text())


@pytest.mark.parametrize("recorded, problems", [("mawk\n", 0), ("zero\n", 1), (None, 1)])
def test_decided_case_must_match_chosen_reference(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, recorded: str | None, problems: int
) -> None:
    regen = _load_regen()
    # Pretend mawk says "mawk" and the other two say "zero" (a 2-1 split
    # against the decision), and that the decision follows mawk.
    answers = {"mawk": "mawk\n", "onetrue": "zero\n", "gawk": "zero\n"}
    monkeypatch.setattr(regen.harness, "run_case", lambda case, cmd: SimpleNamespace(
        stdout=next(v for k, v in answers.items() if regen.REFERENCES[k] == cmd)))
    monkeypatch.setattr(regen.harness, "read_decisions", lambda: {"io/close_status": "follow mawk"})
    assert regen.vote([_fake_case(tmp_path, recorded)]) == problems
