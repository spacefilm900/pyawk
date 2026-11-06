"""Run every golden case through the pyawk CLI.

That is awk_tests/golden/* plus the imported suites in awk_tests/onetrue/
(onetrue-awk) and awk_tests/gawk/ (GNU awk).  For those, MANIFEST.tsv marks cases to
skip (deferred features, nondeterminism, reference disagreements) and
KNOWN_FAILURES lists cases pyawk is known to get wrong; those are reported
as expected failures ("xfail").  ``strict=True`` turns an unexpected pass
into a failure, so a fix forces the list to be updated.
"""

import difflib
import re

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
    problems = case.result_problems(proc)
    if not problems:
        return
    parts = [f"{case.id}: {', '.join(problems)}"]
    if "stdout mismatch" in problems:
        parts.append(
            "".join(
                difflib.unified_diff(
                    case.expected().splitlines(keepends=True),
                    proc.stdout.splitlines(keepends=True),
                    "expected",
                    "pyawk",
                )
            )
        )
    if any(p.startswith("exit status") for p in problems):
        parts.append(f"exit: got {proc.returncode}, expected {case.expected_exit()}")
    if "stderr mismatch" in problems:
        parts.append(
            "".join(
                difflib.unified_diff(
                    (case.expected_stderr() or "").splitlines(keepends=True),
                    proc.stderr.splitlines(keepends=True),
                    "expected-stderr",
                    "pyawk-stderr",
                )
            )
        )
    elif proc.stderr:
        parts.append(f"stderr: {proc.stderr}")
    pytest.fail("\n".join(parts))


# Operands that upstream passes on purpose but the program never opens as
# files: compare.awk clears ARGV[1] and ARGV[2] in BEGIN, and argcasfile
# sets ARGC=1 so the missing file after it is never read.
NOT_FILES = {"gawk/compare": {"0", "1"}, "gawk/argcasfile": {" /no/such/file"}}


def test_corpus_is_complete() -> None:
    for case in CASES:
        for operand in case.operands():
            # "-" is standard input, and "NAME=value" is an awk variable
            # assignment rather than a file name.
            if operand == "-" or re.match(r"[A-Za-z_][A-Za-z0-9_]*=", operand):
                continue
            if operand in NOT_FILES.get(case.id, set()):
                continue
            assert (case.directory / operand).exists(), (case.id, operand)
        assert case.path(".out").exists(), case.id
    assert {c.category for c in CASES} == set(harness.ALL_CATEGORIES)


# ``parametrize`` runs the test once per imported suite.
@pytest.mark.parametrize("category", list(harness.IMPORTED))
def test_manifest_covers_imported_suite(category: str) -> None:
    """Every imported case has a manifest row with a known status."""
    manifest = harness.read_manifest(harness.IMPORTED[category])
    names = {c.name for c in CASES if c.category == category}
    assert names == set(manifest), names ^ set(manifest)
    assert {status for status, _ in manifest.values()} <= {"run", "sorted", "skip"}
    # Every skip must say why.
    assert all(reason for status, reason in manifest.values() if status == "skip")


def test_overrides_name_real_cases() -> None:
    """Every hand-corrected case in golden/OVERRIDES exists and has a reason."""
    ids = {c.id for c in CASES}
    for case_id, reason in harness.read_overrides().items():
        assert case_id in ids, f"OVERRIDES names unknown case {case_id}"
        assert reason, f"OVERRIDES entry {case_id} needs a reason"
