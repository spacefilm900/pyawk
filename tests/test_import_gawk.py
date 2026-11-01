"""scripts/import_gawk.py: cases pinned to gawk's .ok file.

A few imported cases (DECIDED, REGEX_POSIX) take gawk's .ok file as the
expected output even though the three reference awks disagree.  The
importer writes what the local gawk prints, so it must first check that the
local gawk really reproduces the .ok; otherwise a different gawk build
could slip its own output in under the label "gawk .ok" (CodeRabbit, PR #1).
"""

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def _load():
    """Import scripts/import_gawk.py as a module (scripts/ is not a package)."""
    spec = importlib.util.spec_from_file_location("import_gawk", ROOT / "scripts" / "import_gawk.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["import_gawk"] = mod  # @dataclass looks the module up here
    spec.loader.exec_module(mod)
    return mod


ig = _load()


def result(stdout: str, returncode: int = 0, stderr: str = "") -> object:
    return ig.RefResult(stdout=stdout, stderr=stderr, returncode=returncode, new_files=[])


# (case, the reference awk that must agree with gawk's .ok)
PINNED = [("close_status", "mawk"), ("regrange", "gawk")]


@pytest.mark.parametrize("name, agreeing", PINNED)
def test_pinned_case_uses_the_ok_when_local_gawk_reproduces_it(name: str, agreeing: str) -> None:
    out = "same\n"
    refs = {"mawk": result("other\n"), "gawk": result("other\n"), agreeing: result(out)}
    expected, note = ig.pinned_expected(name, out, refs, result(out), result(out))
    assert expected == out
    assert "gawk .ok" in note


@pytest.mark.parametrize("name, agreeing", PINNED)
def test_pinned_case_stops_when_local_gawk_differs_from_the_ok(name: str, agreeing: str) -> None:
    # The local gawk and the agreeing awk say the same thing, but not what the .ok says.
    local = "local build\n"
    refs = {"mawk": result(local), "gawk": result(local)}
    with pytest.raises(SystemExit, match="reproduce"):
        ig.pinned_expected(name, "upstream .ok\n", refs, result(local), result(local))


@pytest.mark.parametrize("name, agreeing", PINNED)
def test_pinned_case_stops_when_the_agreeing_awk_changes(name: str, agreeing: str) -> None:
    out = "same\n"
    refs = {"mawk": result("other\n"), "gawk": result("other\n")}
    with pytest.raises(SystemExit, match="review"):
        ig.pinned_expected(name, out, refs, result(out), result(out))


def test_other_cases_are_not_pinned() -> None:
    refs = {"mawk": result(""), "gawk": result("")}
    assert ig.pinned_expected("not_pinned", "", refs, result(""), result("")) is None
