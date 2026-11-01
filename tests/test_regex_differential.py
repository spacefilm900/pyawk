"""pyawk's regex matching against the recorded majority of three awks.

awk_tests/regex_differential.json holds, for every pattern of the corpus in
awk_tests/regex_corpus.py, what mawk, onetrue-awk and gawk --posix answer by
majority vote (see scripts/regex_fuzz.py, which regenerates it).  Each
pattern is one test: pyawk runs the same probe program in-process and every
recorded line must match.  Lines where the references split three ways are
recorded as ``null`` and skipped.  The references are not needed here.

KNOWN_FAILURES lists pattern keys pyawk is known to get wrong (expected
failures, ``strict`` so a fix forces the list to shrink).
"""

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "awk_tests"))
import regex_corpus as rc  # noqa: E402

RECORDS = json.loads((ROOT / "awk_tests" / "regex_differential.json").read_text())["records"]

KNOWN_FAILURES: set[str] = set()


def _param(rec: dict):
    key = f"{rec['category']}:{rec['pattern']}"
    marks = [pytest.mark.xfail(reason="regex KNOWN_FAILURES", strict=True)] if key in KNOWN_FAILURES else []
    return pytest.param(rec, id=key, marks=marks)


@pytest.mark.parametrize("rec", [_param(r) for r in RECORDS])
def test_regex_differential(rec: dict) -> None:
    entry = rc.Entry(rec["category"], rec["pattern"], tuple(rec["inputs"]))
    out = rc.run_pyawk(entry)
    got = [rc.ERROR] * len(entry.inputs) if out == rc.ERROR else out.split("\n")
    bad = [
        f"  {inp!r}: expected {exp!r}, got {g!r}"
        for inp, exp, g in zip(entry.inputs, rec["expected"], got)
        if exp is not None and g != exp
    ]
    assert not bad, f"/{rec['pattern']}/:\n" + "\n".join(bad)


def test_corpus_and_record_agree() -> None:
    """The record covers exactly the current corpus (regenerate if not)."""
    assert [(e.category, e.pattern, list(e.inputs)) for e in rc.corpus()] == [
        (r["category"], r["pattern"], r["inputs"]) for r in RECORDS
    ]


def _load_fuzz():
    """Import scripts/regex_fuzz.py as a module (scripts/ is not a package)."""
    import importlib.util

    spec = importlib.util.spec_from_file_location("regex_fuzz", ROOT / "scripts" / "regex_fuzz.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.mark.parametrize("mismatches, status", [(0, 0), (3, 1)])
def test_fuzz_check_exit_status_reports_mismatches(
    monkeypatch: pytest.MonkeyPatch, mismatches: int, status: int
) -> None:
    # ``regex_fuzz.py --check`` must fail (non-zero status) when pyawk
    # disagrees with the record, so scripts can detect it (CodeRabbit).
    fuzz = _load_fuzz()
    monkeypatch.setattr(fuzz, "compare", lambda data, show, category: mismatches)
    monkeypatch.setattr(sys, "argv", ["regex_fuzz.py", "--check"])
    assert fuzz.main() == status
