#!/usr/bin/env python3
"""Run the golden corpus against pyawk and print pass/fail per category.

usage: scripts/run_golden.py [-v] [--diff] [CATEGORY or CATEGORY/NAME ...]
"""

from __future__ import annotations

import argparse
import difflib
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "awk_tests"))
import harness  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("-v", "--verbose", action="store_true", help="list every case")
    ap.add_argument("--diff", action="store_true", help="show a diff for failures")
    ap.add_argument("cases", nargs="*")
    opts = ap.parse_args()
    cases = [c for c in harness.discover() if not opts.cases or c.id in opts.cases or c.category in opts.cases]
    passed: dict[str, int] = defaultdict(int)
    failed: dict[str, list[str]] = defaultdict(list)
    for case in cases:
        proc = harness.run_case(case, harness.pyawk_command())
        expected = case.expected()
        ok = proc.stdout == expected
        if ok:
            passed[case.category] += 1
        else:
            failed[case.category].append(case.name)
        if opts.verbose or not ok:
            print(f"{'PASS' if ok else 'FAIL'} {case.id}")
        if not ok and opts.diff:
            diff = difflib.unified_diff(
                expected.splitlines(keepends=True), proc.stdout.splitlines(keepends=True),
                "expected", "pyawk",
            )
            sys.stdout.writelines("    " + line for line in diff)
            if proc.stderr:
                print("    stderr:", proc.stderr.strip())
    print()
    print(f"{'category':<12} {'pass':>5} {'fail':>5}")
    total_p = total_f = 0
    for cat in harness.CATEGORIES:
        p, f = passed[cat], len(failed[cat])
        if p or f:
            print(f"{cat:<12} {p:>5} {f:>5}" + (f"   ({', '.join(failed[cat])})" if f else ""))
        total_p += p
        total_f += f
    print(f"{'TOTAL':<12} {total_p:>5} {total_f:>5}")
    return 1 if total_f else 0


if __name__ == "__main__":
    sys.exit(main())
