"""Tests for the test tooling itself: awk_tests/harness.py and regen_golden.py."""

import ast
import importlib.util
import os
import subprocess
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


# Stand-in commands for the three references, so these tests need none of
# them installed; regen.reference is patched to return these.
FAKE_REFS = {"mawk": ["fake-mawk"], "onetrue": ["fake-onetrue"], "gawk": ["fake-gawk", "--posix"]}


def _fake_refs(monkeypatch: pytest.MonkeyPatch, regen) -> None:
    monkeypatch.setattr(regen, "reference", lambda name: FAKE_REFS[name])


def _ref_name(cmd: list[str]) -> str:
    return next(k for k, v in FAKE_REFS.items() if v == cmd)


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
        out.write_text(recorded, encoding="utf-8")
    return SimpleNamespace(id="io/close_status", path=lambda ext: out, expected=lambda: out.read_text(encoding="utf-8"))


@pytest.mark.parametrize("recorded, problems", [("mawk\n", 0), ("zero\n", 1), (None, 1)])
def test_decided_case_must_match_chosen_reference(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, recorded: str | None, problems: int
) -> None:
    regen = _load_regen()
    # Pretend mawk says "mawk" and the other two say "zero" (a 2-1 split
    # against the decision), and that the decision follows mawk.
    answers = {"mawk": "mawk\n", "onetrue": "zero\n", "gawk": "zero\n"}
    _fake_refs(monkeypatch, regen)
    monkeypatch.setattr(regen.harness, "run_case", lambda case, cmd: SimpleNamespace(stdout=answers[_ref_name(cmd)]))
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
    _fake_refs(monkeypatch, regen)
    monkeypatch.setattr(regen.harness, "run_case", lambda case, cmd: SimpleNamespace(stdout=answers[_ref_name(cmd)]))
    monkeypatch.setattr(regen.harness, "read_decisions", lambda: {"io/close_status": "follow mawk"})
    assert regen.vote([_fake_case(tmp_path, recorded)]) == problems


def test_utf8_cases_run_in_a_utf8_locale(tmp_path: Path) -> None:
    # Cases in a category listed in harness.LOCALES run with that locale;
    # every other case runs with LC_ALL=C.
    for name in ("a.awk", "a.in"):
        (tmp_path / name).write_text("", encoding="utf-8")
    for category, expected in (("utf8", "C.UTF-8"), ("basic", "C")):
        case = harness.Case(category, "a", tmp_path)
        proc = harness.run_case(case, ["/bin/sh", "-c", 'echo "$LC_ALL"', "sh"])
        assert proc.stdout == expected + "\n"


def _utf8_case(tmp_path: Path, recorded: str) -> SimpleNamespace:
    out = tmp_path / "x.out"
    out.write_text(recorded, encoding="utf-8")
    return SimpleNamespace(id="utf8/x", path=lambda ext: out, expected=lambda: out.read_text(encoding="utf-8"))


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

    _fake_refs(monkeypatch, regen)

    def fake_run(case, cmd):
        name = _ref_name(cmd)
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
    case = SimpleNamespace(id="utf8/x", path=lambda ext: missing, expected=lambda: missing.read_text(encoding="utf-8"))
    _fake_refs(monkeypatch, regen)
    monkeypatch.setattr(regen.harness, "run_case", lambda case, cmd: SimpleNamespace(stdout="gawk\n"))
    monkeypatch.setattr(regen.harness, "read_decisions", lambda: {})
    monkeypatch.setattr(regen.harness, "read_overrides", lambda: {"utf8/x": "pyawk's choice"})
    assert regen.vote([case]) == 1
    assert "no .out file" in capsys.readouterr().out


def test_utf8_cases_are_regenerated_with_gawk(monkeypatch: pytest.MonkeyPatch) -> None:
    # With no --awk, the default reference is mawk, except for UTF-8 cases
    # (mawk works on bytes), which use gawk --posix.
    regen = _load_regen()
    _fake_refs(monkeypatch, regen)
    utf8 = SimpleNamespace(id="utf8/x")
    basic = SimpleNamespace(id="basic/x")
    assert regen.reference_command(utf8, None) == FAKE_REFS["gawk"]
    assert regen.reference_command(basic, None) == FAKE_REFS["mawk"]
    # An explicitly chosen awk is used as it is.
    assert regen.reference_command(utf8, ["/bin/other"]) == ["/bin/other"]


@pytest.mark.parametrize("args", [["--check", "basic/begin_end"], ["--vote", "basic/begin_end"]])
def test_regen_without_the_references_is_a_clear_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], args: list[str]
) -> None:
    regen = _load_regen()
    for var in ("PYAWK_REF_MAWK", "PYAWK_REF_ONETRUE", "PYAWK_REF_GAWK"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("PATH", str(tmp_path))  # nothing installed
    monkeypatch.setattr(sys, "argv", ["regen_golden.py", *args])
    assert regen.main() == 2
    err = capsys.readouterr().err
    assert "PYAWK_REF_MAWK" in err


# An ASCII locale: LC_ALL=C, with Python's own UTF-8 fallbacks turned off
# (PYTHONUTF8=0: no UTF-8 mode; PYTHONCOERCECLOCALE=0: C is not changed
# to C.UTF-8).  Then a file read or written without an explicit encoding
# uses ASCII and fails on the golden files' non-ASCII text.
ASCII_LOCALE = {"LC_ALL": "C", "PYTHONUTF8": "0", "PYTHONCOERCECLOCALE": "0"}

READ_EVERY_CASE = """
import sys
sys.path.insert(0, "awk_tests")
import harness
import codecs, locale
assert codecs.lookup(locale.getpreferredencoding(False)).name == "ascii"  # really an ASCII locale
harness.read_overrides()
harness.read_decisions()
cases = harness.discover()
for case in cases:
    case.extra_args(), case.operands(), case.extra_env(), case.stdin_data(), case.expected()
print(len(cases))
"""


def test_harness_reads_its_files_under_an_ascii_locale() -> None:
    """golden/OVERRIDES, the .args, .environ and other case files are UTF-8
    whatever the locale: the harness names the encoding when it reads them."""
    env = {k: v for k, v in os.environ.items() if not k.startswith(("LC_", "LANG"))} | ASCII_LOCALE
    res = subprocess.run([sys.executable, "-c", READ_EVERY_CASE], capture_output=True, text=True,
                         encoding="utf-8", errors="replace", cwd=REPO, env=env)
    assert res.returncode == 0, res.stderr
    assert int(res.stdout) == len(harness.discover())


def _unencoded_text_io(path: Path) -> list[str]:
    """Calls in ``path`` that read or write text without naming an encoding:
    ``read_text`` / ``write_text`` / ``open`` in text mode, ``bytes.decode()``
    with no codec, and subprocess calls with ``text=True``.  Without
    ``encoding=`` they use the locale's encoding, which may be ASCII."""
    bad = []
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if not isinstance(node, ast.Call):
            continue
        keywords = {k.arg: k.value for k in node.keywords}
        func = node.func
        name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", None)
        kind = None
        if "encoding" in keywords:
            pass
        elif name in ("read_text", "write_text"):
            kind = name
        elif name == "open":
            # open(path, mode) or path.open(mode); the default mode is "r".
            args = node.args[1:] if isinstance(func, ast.Name) else node.args
            mode = args[0] if args else keywords.get("mode")
            if not (isinstance(mode, ast.Constant) and "b" in mode.value):
                kind = "open"
        elif name == "decode" and not node.args:
            kind = "decode"
        elif name in ("run", "Popen", "check_output", "check_call"):
            text = keywords.get("text") or keywords.get("universal_newlines")
            if text is not None and not (isinstance(text, ast.Constant) and not text.value):
                kind = f"{name}(text=True)"
        if kind:
            bad.append(f"{path.relative_to(REPO)}:{node.lineno}: {kind}")
    return bad


def test_tools_and_tests_name_the_encoding_of_their_text_files() -> None:
    files = [REPO / "awk_tests" / "harness.py", *sorted((REPO / "scripts").glob("*.py")),
             *sorted((REPO / "tests").glob("*.py"))]
    bad = [line for path in files for line in _unencoded_text_io(path)]
    assert bad == []


TOLERANT_OUTPUT = """
import sys
sys.path.insert(0, "awk_tests")
import harness
harness.tolerant_output()
print("caf\\u00e9")
print("na\\u00efve", file=sys.stderr)
"""


def test_tolerant_output_under_an_ascii_locale() -> None:
    """The scripts print case names, reasons and diffs, which may not be
    ASCII (golden/OVERRIDES is not).  Under an ASCII locale such a print
    raised UnicodeEncodeError; after harness.tolerant_output() a character
    the terminal cannot show is written as a backslash escape instead."""
    env = {k: v for k, v in os.environ.items() if not k.startswith(("LC_", "LANG"))} | ASCII_LOCALE
    res = subprocess.run([sys.executable, "-c", TOLERANT_OUTPUT], capture_output=True, text=True,
                         encoding="utf-8", cwd=REPO, env=env)
    assert (res.returncode, res.stdout, res.stderr) == (0, "caf\\xe9\n", "na\\xefve\n")


@pytest.mark.parametrize("script", sorted(p.name for p in (REPO / "scripts").glob("*.py")
                                          if "def main(" in p.read_text(encoding="utf-8")))
def test_every_script_main_makes_its_output_tolerant(script: str) -> None:
    tree = ast.parse((REPO / "scripts" / script).read_text(encoding="utf-8"))
    main = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "main")
    calls = [n for n in ast.walk(main) if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)]
    assert any(c.func.attr == "tolerant_output" for c in calls)


def _case_with_environ(tmp_path: Path, text: str) -> harness.Case:
    (tmp_path / "t.environ").write_text(text, encoding="utf-8")
    return harness.Case("environ", "t", tmp_path)


def test_environ_file_valid_lines(tmp_path: Path) -> None:
    """``NAME=value`` lines: the value is everything after the first "=",
    spaces included, and may be empty; blank lines are skipped."""
    case = _case_with_environ(tmp_path, "A=1\n\nB=\nC= x=y \n   \n")
    assert case.extra_env() == {"A": "1", "B": "", "C": " x=y "}


@pytest.mark.parametrize("text, bad", [("A=1\nLC_ALL\n", 2), ("=v\n", 1)])
def test_environ_file_malformed_line_is_an_error(tmp_path: Path, text: str, bad: int) -> None:
    """A line with no "=" (it used to set the variable to "") or an empty
    name (it used to set a variable called "") is a mistake in the case:
    the error names the file, the line number and the line."""
    case = _case_with_environ(tmp_path, text)
    line = text.splitlines()[bad - 1]
    with pytest.raises(ValueError) as info:
        case.extra_env()
    assert str(info.value) == f"{tmp_path / 't.environ'}:{bad}: not NAME=value: {line!r}"
