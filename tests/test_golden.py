"""Run every golden case through the pyawk CLI.

That is awk_tests/golden/* plus the imported onetrue-awk suite in
awk_tests/onetrue/.  For the onetrue suite, MANIFEST.tsv marks cases to
skip (deferred features, nondeterminism, reference disagreements) and
KNOWN_FAILURES lists cases pyawk is known to get wrong; those are reported
as expected failures ("xfail").  ``strict=True`` turns an unexpected pass
into a failure, so a fix forces the list to be updated.
"""

import difflib

import pytest

import harness

CASES = harness.discover()


def _param(case: harness.Case):
    """Wrap a case for parametrize, attaching skip/xfail marks where needed."""
    marks = []
    if case.skipped:
        marks.append(pytest.mark.skip(reason=case.reason))
    elif case.known_failure:
        marks.append(pytest.mark.xfail(reason="listed in KNOWN_FAILURES", strict=True))
    return pytest.param(case, id=case.id, marks=marks)


@pytest.mark.parametrize("case", [_param(c) for c in CASES])
def test_golden(case: harness.Case) -> None:
    proc = harness.run_case(case, harness.pyawk_command())
    if not case.matches(proc.stdout):
        diff = "".join(
            difflib.unified_diff(
                case.expected().splitlines(keepends=True),
                proc.stdout.splitlines(keepends=True),
                "expected",
                "pyawk",
            )
        )
        pytest.fail(f"output mismatch for {case.id}\n{diff}\nstderr: {proc.stderr}")


def test_corpus_is_complete() -> None:
    for case in CASES:
        for operand in case.operands():
            assert (case.directory / operand).exists(), case.id
        assert case.path(".out").exists(), case.id
    assert {c.category for c in CASES} == set(harness.ALL_CATEGORIES)


def test_manifest_covers_onetrue_suite() -> None:
    """Every imported case has a manifest row with a known status."""
    manifest = harness.read_manifest(harness.ONETRUE)
    names = {c.name for c in CASES if c.category == "onetrue"}
    assert names == set(manifest), names ^ set(manifest)
    assert {status for status, _ in manifest.values()} <= {"run", "sorted", "skip"}
    # Every skip must say why.
    assert all(reason for status, reason in manifest.values() if status == "skip")
