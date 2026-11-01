"""Run every golden case in awk_tests/golden through the pyawk CLI."""

import difflib

import pytest

import harness

CASES = harness.discover()


@pytest.mark.parametrize("case", CASES, ids=[c.id for c in CASES])
def test_golden(case: harness.Case) -> None:
    proc = harness.run_case(case, harness.pyawk_command())
    expected = case.expected()
    if proc.stdout != expected:
        diff = "".join(
            difflib.unified_diff(
                expected.splitlines(keepends=True),
                proc.stdout.splitlines(keepends=True),
                "expected",
                "pyawk",
            )
        )
        pytest.fail(f"output mismatch for {case.id}\n{diff}\nstderr: {proc.stderr}")


def test_corpus_is_complete() -> None:
    for case in CASES:
        assert case.path(".in").exists(), case.id
        assert case.path(".out").exists(), case.id
    assert {c.category for c in CASES} == set(harness.CATEGORIES)
