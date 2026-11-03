#!/usr/bin/env python3
"""Run the golden corpus (and the imported onetrue and gawk suites) against pyawk.

usage: scripts/run_golden.py [-v] [--diff] [--write-known-failures] [CATEGORY or CATEGORY/NAME ...]

Each case's stdout must match its ``.out`` file byte for byte (those files
were produced by reference awks; see regen_golden.py, import_onetrue.py
and import_gawk.py).
Cases whose MANIFEST.tsv status is "sorted" are compared line-order-blind,
and "skip" cases are not run at all but counted in the "skip" column.

Prints pass/fail/skip per category and exits 1 if any case fails, so it can
be used in automation.  Failures listed in a category's KNOWN_FAILURES file
are marked "(known)" but still count as failures here; pytest is the place
where they are tolerated.  --write-known-failures rewrites the
KNOWN_FAILURES file of each imported suite that was run (onetrue, gawk)
from this run's failures.
"""

from __future__ import annotations

import argparse
import difflib
import sys
from collections import defaultdict
from pathlib import Path

# Make awk_tests/ importable so we can share harness.py with the pytest suite.
# (``Path(...) / "x"`` joins path components.)  The import must therefore
# come after this line; "noqa: E402" tells the linter that is intentional.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "awk_tests"))
import harness  # noqa: E402


def main() -> int:
    harness.tolerant_output()
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("-v", "--verbose", action="store_true", help="list every case")
    ap.add_argument("--diff", action="store_true", help="show a diff for failures")
    ap.add_argument("--write-known-failures", action="store_true",
                    help="record this run's failures in each imported suite's KNOWN_FAILURES")
    ap.add_argument("cases", nargs="*")
    opts = ap.parse_args()
    # List comprehension: keep the cases the user selected (all, by default).
    cases = [c for c in harness.discover() if not opts.cases or c.id in opts.cases or c.category in opts.cases]
    # defaultdict: a dict that creates a missing entry (0, or an empty list)
    # the first time it is used, so counting needs no "is it there?" check.
    passed: dict[str, int] = defaultdict(int)
    skipped: dict[str, int] = defaultdict(int)
    failed: dict[str, list[str]] = defaultdict(list)
    for case in cases:
        if case.skipped:
            skipped[case.category] += 1
            if opts.verbose:
                print(f"SKIP {case.id}  ({case.reason})")
            continue
        proc = harness.run_case(case, harness.pyawk_command())
        ok = case.matches(proc.stdout)
        if ok:
            passed[case.category] += 1
        else:
            failed[case.category].append(case.name)
        if opts.verbose or not ok:
            known = " (known)" if case.known_failure and not ok else ""
            print(f"{'PASS' if ok else 'FAIL'} {case.id}{known}")
        if not ok and opts.diff:
            diff = difflib.unified_diff(
                case.expected().splitlines(keepends=True), proc.stdout.splitlines(keepends=True),
                "expected", "pyawk",
            )
            sys.stdout.writelines("    " + line for line in diff)
            if proc.stderr:
                print("    stderr:", proc.stderr.strip())
    print()
    # f-strings: ``{x:<12}`` inserts x left-aligned in 12 columns, ``>5`` right-aligned.
    print(f"{'category':<12} {'pass':>5} {'fail':>5} {'skip':>5}")
    total_p = total_f = total_s = 0
    for cat in harness.ALL_CATEGORIES:
        p, f, s = passed[cat], len(failed[cat]), skipped[cat]
        if p or f or s:
            # Long failure lists (the imported suites) are only counted here.
            names = f"   ({', '.join(failed[cat])})" if 0 < f <= 10 else ""
            print(f"{cat:<12} {p:>5} {f:>5} {s:>5}" + names)
        total_p += p
        total_f += f
        total_s += s
    print(f"{'TOTAL':<12} {total_p:>5} {total_f:>5} {total_s:>5}")

    if opts.write_known_failures:
        # ``.items()`` gives (name, directory) pairs.  Only suites that ran in
        # full are rewritten: "run_golden.py gawk" leaves the onetrue list
        # alone, and running a single case "gawk/NAME" changes nothing.
        for cat, directory in harness.IMPORTED.items():
            if opts.cases and cat not in opts.cases:
                continue
            path = directory / "KNOWN_FAILURES"
            lines = [f"# {cat} cases pyawk currently fails; written by scripts/run_golden.py --write-known-failures"]
            path.write_text("\n".join([*lines, *failed[cat]]) + "\n", encoding="utf-8")
            print(f"wrote {len(failed[cat])} names to {path}")
    return 1 if total_f else 0


# True only when run as a script (not when imported).
if __name__ == "__main__":
    sys.exit(main())
