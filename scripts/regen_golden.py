#!/usr/bin/env python3
"""Regenerate golden ``.out`` files by running the system awk (default mawk).

usage: scripts/regen_golden.py [--awk AWK] [--check | --vote] [CATEGORY/NAME ...]

--check  compare the system awk against the existing .out files instead of
         writing them (useful to spot reference-implementation drift).
--vote   run all three reference awks (mawk, onetrue-awk, gawk --posix) and
         report, per case, whether they agree with each other and with the
         .out file.  Writes nothing.  Use it on new cases: the .out must be
         the majority answer, and a case where mawk is outvoted needs an
         OVERRIDES entry.

The ``utf8`` category runs in a UTF-8 locale.  mawk works on bytes, so it
is left out there: those .out files come from gawk --posix (the project
follows gawk on UTF-8 questions, see DESIGN.md), --vote compares only
onetrue-awk and gawk, and a case where pyawk deliberately differs from
gawk is hand-corrected in OVERRIDES.

Cases listed in awk_tests/golden/OVERRIDES have hand-corrected .out files
(mawk is the outlier there; see DESIGN.md).  They are never rewritten, and
under --check a difference from mawk is reported as expected, not counted.

The reference awks are found through PYAWK_REF_MAWK, PYAWK_REF_ONETRUE
and PYAWK_REF_GAWK, or else on PATH (see awk_tests/references.py); --awk
names another awk to use instead of mawk.

The ``.out`` files are the "golden" answers that run_golden.py and the
pytest suite compare pyawk against, so they are made with a real awk.
"""

from __future__ import annotations

import argparse
import shlex
import sys
from pathlib import Path

# Make awk_tests/ importable (shared harness.py); see run_golden.py.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "awk_tests"))
import harness  # noqa: E402
import references  # noqa: E402

# The three reference awks (see DESIGN.md, "Golden corpus and mawk").
REFERENCES = ("mawk", "onetrue", "gawk")

# The reference that writes and checks the .out files unless --awk says
# otherwise.
DEFAULT_REFERENCE = "mawk"

# The reference whose answer the DECISIONS cases follow (close() keeps a
# command's exit status, as mawk does).
DECISION_REFERENCE = "mawk"

# What UTF-8 cases are generated with and judged by (see the docstring).
UTF8_REFERENCE = "gawk"


def is_utf8(case: harness.Case) -> bool:
    return harness.locale_for(case.id) != "C"


def reference(name: str) -> list[str]:
    """The command that runs reference ``name``; raises
    references.MissingReference (with a how-to-fix message) if it is not
    installed."""
    return references.command(name)


def reference_command(case: harness.Case, awk: list[str] | None) -> list[str]:
    """The awk that writes or checks this case's .out: the one chosen with
    --awk if any; otherwise mawk, except that mawk cannot do UTF-8 cases,
    so gawk does those."""
    if awk is not None:
        return awk
    return reference(UTF8_REFERENCE if is_utf8(case) else DEFAULT_REFERENCE)


def vote(cases: list[harness.Case]) -> int:
    """Print how the references compare on each case; return the problem count.

    A case is fine when at least two references agree and the ``.out``
    file holds their answer, or when the case is listed in DECISIONS and
    the ``.out`` holds the chosen reference's answer.  Anything else (an
    undecided three-way split, or a ``.out`` that is not the majority or
    decided answer) is printed and counted.
    """
    problems = 0
    decisions = harness.read_decisions()
    overrides = harness.read_overrides()
    for case in cases:
        if is_utf8(case):
            problems += vote_utf8(case, overrides)
            continue
        outputs = {name: harness.run_case(case, reference(name)).stdout for name in REFERENCES}
        # Group the references by identical output: {output: [names]}.
        groups: dict[str, list[str]] = {}
        for name, out in outputs.items():
            groups.setdefault(out, []).append(name)
        majority = max(groups, key=lambda out: len(groups[out]))
        expected = case.expected() if case.path(".out").exists() else None
        if len(groups) == 1 and expected == majority:
            continue  # all three agree with the .out file: nothing to say
        dissent = [", ".join(names) for out, names in groups.items() if out != majority]
        if case.id in decisions:
            # A decided case follows mawk's answer (see DECISIONS), even
            # when all three references disagree, so this comes first.  Only
            # call it a success if the recorded .out really holds that
            # answer; a stale or missing .out is a problem like any other.
            if expected is None:
                problems += 1
                print(f"DECIDED-MISSING {case.id}: no .out file")
            elif expected != outputs[DECISION_REFERENCE]:
                problems += 1
                print(f"DECIDED-WRONG {case.id}: .out is not the {DECISION_REFERENCE} answer")
            else:
                print(f"DECIDED {case.id}: {decisions[case.id]}")
        elif len(groups[majority]) < 2:
            problems += 1
            print(f"SPLIT {case.id}: no two references agree")
        elif expected != majority:
            problems += 1
            print(f"WRONG {case.id}: .out is not the majority answer ({', '.join(groups[majority])})")
        else:
            print(f"OK    {case.id}: majority {', '.join(groups[majority])}; outvoted: {'; '.join(dissent)}")
    print(f"voted on {len(cases)} cases, {problems} problems")
    return problems


def vote_utf8(case: harness.Case, overrides: dict[str, str]) -> int:
    """The vote for one UTF-8 case (mawk has no say): 1 if it is a problem.

    The .out must be gawk's answer, unless the case is hand-corrected in
    OVERRIDES; a different onetrue-awk answer is reported, not counted.
    """
    outputs = {name: harness.run_case(case, reference(name)).stdout for name in ("onetrue", "gawk")}
    expected = case.expected() if case.path(".out").exists() else None
    if expected is None:
        # Nothing recorded at all: a problem even for a hand-corrected case,
        # whose correction lives in exactly this file.
        print(f"MISSING {case.id}: no .out file")
        return 1
    if case.id in overrides:
        print(f"KEEP  {case.id}: hand-corrected .out: {overrides[case.id]}")
        return 0
    if expected != outputs[UTF8_REFERENCE]:
        print(f"WRONG {case.id}: .out is not the {UTF8_REFERENCE} answer")
        return 1
    if outputs["onetrue"] != expected:
        print(f"OK    {case.id}: follows {UTF8_REFERENCE}; onetrue differs")
    return 0


def main() -> int:
    harness.tolerant_output()
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--awk", help="awk command to use instead of mawk (and gawk for utf8 cases)")
    ap.add_argument("--check", action="store_true", help="only compare, do not write")
    ap.add_argument("--vote", action="store_true", help="compare all three references with each other and .out")
    ap.add_argument("cases", nargs="*", help="limit to CATEGORY or CATEGORY/NAME")
    opts = ap.parse_args()
    # Split "--awk 'gawk --posix'" into a command plus arguments, shell-style.
    awk = shlex.split(opts.awk) if opts.awk else None
    try:
        return run(opts, awk)
    except references.MissingReference as exc:
        print(f"regen_golden: {exc}", file=sys.stderr)
        return 2


def run(opts: argparse.Namespace, awk: list[str] | None) -> int:
    """main's work, once the options are read."""
    # Only the hand-written golden categories: the onetrue suite's .out files
    # come from onetrue-awk and are regenerated by import_onetrue.py instead.
    golden = harness.discover(harness.CATEGORIES)
    selected = [c for c in golden if not opts.cases or c.id in opts.cases or c.category in opts.cases]
    # Find every reference this run needs before touching any file, so a
    # missing one stops the run cleanly instead of half-way through.
    if opts.vote:
        needed: tuple[str, ...] = REFERENCES
    elif awk is not None:
        needed = ()
    else:
        needed = (DEFAULT_REFERENCE,) + ((UTF8_REFERENCE,) if any(is_utf8(c) for c in selected) else ())
    references.commands(needed)
    if opts.vote:
        return 1 if vote(selected) else 0
    overrides = harness.read_overrides()
    bad = 0
    for case in selected:
        proc = harness.run_case(case, reference_command(case, awk))
        if proc.returncode != 0:
            print(f"WARN {case.id}: exit {proc.returncode}: {proc.stderr.strip()}")
        if case.id in overrides:
            # Hand-corrected answer: report, but never overwrite or count it.
            same = proc.stdout == case.expected()
            note = "matches the reference now; consider removing the override" if same else "differs (expected)"
            print(f"KEEP {case.id}: hand-corrected .out, {note}: {overrides[case.id]}")
            continue
        if opts.check:
            if proc.stdout != case.expected():
                bad += 1
                print(f"DIFF {case.id}")
        else:
            case.path(".out").write_text(proc.stdout, encoding="utf-8", errors="surrogateescape")
    action = "checked" if opts.check else "regenerated"
    # The utf8 cases use gawk when the default mawk was chosen; say so.
    utf8_cases = [c for c in selected if is_utf8(c)]
    used = opts.awk or " ".join(reference(DEFAULT_REFERENCE))
    if utf8_cases and awk is None:
        used += f" ({len(utf8_cases)} utf8 cases with {' '.join(reference_command(utf8_cases[0], awk))})"
    print(f"{action} {len(selected)} cases with {used}" + (f", {bad} differ" if opts.check else ""))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
