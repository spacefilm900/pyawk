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


@pytest.mark.parametrize("recorded, problems", [("mawk\n", 0), ("gawk\n", 1), (None, 1)])
def test_decided_case_with_three_way_split_follows_the_decision(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, recorded: str | None, problems: int
) -> None:
    # When all three references disagree, a case listed in DECISIONS must
    # still be judged against the chosen reference's answer, not rejected
    # as SPLIT (CodeRabbit, PR #1).
    regen = _load_regen()
    answers = {"mawk": "mawk\n", "onetrue": "onetrue\n", "gawk": "gawk\n"}
    monkeypatch.setattr(regen.harness, "run_case", lambda case, cmd: SimpleNamespace(
        stdout=next(v for k, v in answers.items() if regen.REFERENCES[k] == cmd)))
    monkeypatch.setattr(regen.harness, "read_decisions", lambda: {"io/close_status": "follow mawk"})
    assert regen.vote([_fake_case(tmp_path, recorded)]) == problems


def test_utf8_cases_run_in_a_utf8_locale(tmp_path: Path) -> None:
    # Cases in a category listed in harness.LOCALES run with that locale;
    # every other case runs with LC_ALL=C.
    for name in ("a.awk", "a.in"):
        (tmp_path / name).write_text("")
    for category, expected in (("utf8", "C.UTF-8"), ("basic", "C")):
        case = harness.Case(category, "a", tmp_path)
        proc = harness.run_case(case, ["/bin/sh", "-c", 'echo "$LC_ALL"', "sh"])
        assert proc.stdout == expected + "\n"


def _utf8_case(tmp_path: Path, recorded: str) -> SimpleNamespace:
    out = tmp_path / "x.out"
    out.write_text(recorded)
    return SimpleNamespace(id="utf8/x", path=lambda ext: out, expected=lambda: out.read_text())


@pytest.mark.parametrize(
    "recorded, overridden, problems", [("gawk\n", False, 0), ("onetrue\n", False, 1), ("mine\n", True, 0)]
)
def test_utf8_vote_follows_gawk_without_mawk(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, recorded: str, overridden: bool, problems: int
) -> None:
    # mawk works on bytes only, so it has no vote on UTF-8 cases; when
    # onetrue-awk and gawk disagree, the .out must be gawk's answer (or the
    # case is hand-corrected in OVERRIDES).
    regen = _load_regen()
    answers = {"onetrue": "onetrue\n", "gawk": "gawk\n"}
    ran = []

    def fake_run(case, cmd):
        name = next(k for k, v in regen.REFERENCES.items() if v == cmd)
        ran.append(name)
        return SimpleNamespace(stdout=answers[name])

    monkeypatch.setattr(regen.harness, "run_case", fake_run)
    monkeypatch.setattr(regen.harness, "read_decisions", lambda: {})
    monkeypatch.setattr(regen.harness, "read_overrides", lambda: {"utf8/x": "pyawk's choice"} if overridden else {})
    assert regen.vote([_utf8_case(tmp_path, recorded)]) == problems
    assert "mawk" not in ran


def test_overridden_utf8_case_without_an_out_file_is_a_problem(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # A hand-corrected case needs its hand-corrected .out; without one there
    # is no recorded answer, so the vote must not report success
    # (CodeRabbit, PR #1).
    regen = _load_regen()
    missing = tmp_path / "x.out"  # never written
    case = SimpleNamespace(id="utf8/x", path=lambda ext: missing, expected=lambda: missing.read_text())
    monkeypatch.setattr(regen.harness, "run_case", lambda case, cmd: SimpleNamespace(stdout="gawk\n"))
    monkeypatch.setattr(regen.harness, "read_decisions", lambda: {})
    monkeypatch.setattr(regen.harness, "read_overrides", lambda: {"utf8/x": "pyawk's choice"})
    assert regen.vote([case]) == 1
    assert "no .out file" in capsys.readouterr().out


def test_utf8_cases_are_regenerated_with_gawk() -> None:
    regen = _load_regen()
    utf8 = SimpleNamespace(id="utf8/x")
    basic = SimpleNamespace(id="basic/x")
    assert regen.reference_command(utf8, regen.REFERENCES["mawk"]) == regen.REFERENCES["gawk"]
    assert regen.reference_command(basic, regen.REFERENCES["mawk"]) == regen.REFERENCES["mawk"]
    # An explicitly chosen awk is used as it is.
    assert regen.reference_command(utf8, ["/bin/other"]) == ["/bin/other"]
