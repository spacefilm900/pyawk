#!/usr/bin/env python3
"""Differential regex testing: pyawk against mawk, onetrue-awk and gawk.

usage: scripts/regex_fuzz.py [--utf8] [--check] [--show N] [--category CAT] [--jobs J]
       scripts/regex_fuzz.py --explain ERE [--inputs a,b,...]
       scripts/regex_fuzz.py --verify-safety [N]

Without ``--check``: run every pattern of the corpus (awk_tests/regex_corpus.py:
curated categories plus seeded random EREs) through the three reference
awks, take the majority answer line by line, and write it to
awk_tests/regex_differential.json -- the recorded results that
tests/test_regex_differential.py checks pyawk against.  Then report how
pyawk compares.

With ``--check``: do not run the references; just compare pyawk with the
recorded file (what the pytest test does, but as a summary table).

Voting is per output line, i.e. per (pattern, input) pair.  A reference
that rejects the pattern (non-zero exit status) counts as answering
``ERROR`` on every line.  Where no two references agree, the line is
recorded as a *split* (``null``, with each reference's answer kept under
``splits``) and not tested; DESIGN.md lists such cases as open decisions.
Patterns in ``regex_corpus.OVERRIDES`` record one named reference's answer
instead of the vote (the reason is stored under ``override``).  The
references run with LC_ALL=C: the C locale is the one POSIX bracket
semantics are specified for here (under C.UTF-8, gawk's ``[=a=]`` also
matches "A").

``--utf8`` works on the UTF-8 corpus instead (regex_corpus.utf8_corpus,
recorded in awk_tests/regex_differential_utf8.json).  It runs under
LC_ALL=C.UTF-8 with only onetrue-awk and gawk --posix: mawk works on
bytes, so it has no say.  Where the two disagree, gawk's answer is
recorded (the project follows POSIX or gawk on UTF-8 questions; see
DESIGN.md, "Locale"), and the line lists onetrue under ``outvoted``.
``regex_corpus.UTF8_OVERRIDES`` records exceptions.

The references are found through PYAWK_REF_MAWK, PYAWK_REF_ONETRUE and
PYAWK_REF_GAWK, or else on PATH (see awk_tests/references.py); ``--check``
needs none of them.

``--show N`` prints the first N mismatching lines with the three
references' answers next to pyawk's.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "awk_tests"))
import harness  # noqa: E402
import references  # noqa: E402
import regex_corpus as rc  # noqa: E402

RESULTS = ROOT / "awk_tests" / "regex_differential.json"
UTF8_RESULTS = ROOT / "awk_tests" / "regex_differential_utf8.json"

# The three reference awks (found by awk_tests/references.py).
REFERENCES = ("mawk", "onetrue", "gawk")


# The references for the UTF-8 corpus, and the one that settles a split.
UTF8_REFERENCES = ("onetrue", "gawk")
UTF8_TIEBREAK = "gawk"

TIMEOUT = "TIMEOUT"


def run_reference(cmd: list[str], entry: rc.Entry) -> list[str]:
    """The probe's output lines from one reference awk (ERROR lines if it fails)."""
    try:
        proc = subprocess.run(
            [*cmd, rc.probe_program(entry.pattern)],
            env={**os.environ, "LC_ALL": entry.locale},
            input=rc.stdin_text(entry).encode("utf-8", "surrogateescape"),
            capture_output=True,
            timeout=10,
        )
    except subprocess.TimeoutExpired:
        # A reference that hangs (mawk does on some interval patterns) is
        # simply wrong on every line; it cannot win a vote.
        return [TIMEOUT] * len(entry.inputs)
    return split_lines(proc.stdout.decode("utf-8", "surrogateescape") if proc.returncode == 0 else rc.ERROR, entry)


def split_lines(out: str, entry: rc.Entry) -> list[str]:
    """One answer per input: the output lines, or ERROR throughout."""
    if out == rc.ERROR:
        return [rc.ERROR] * len(entry.inputs)
    lines = out.split("\n")[: len(entry.inputs)]
    return lines + [rc.ERROR] * (len(entry.inputs) - len(lines))


def regenerate(entries: list[rc.Entry], jobs: int, utf8: bool = False) -> dict:
    # Which references vote, and how a split is settled (see the docstring).
    voters = UTF8_REFERENCES if utf8 else REFERENCES
    overrides = rc.UTF8_OVERRIDES if utf8 else rc.OVERRIDES

    # Find the voters first: a missing one stops the run here, before any
    # work (raises references.MissingReference).
    commands = references.commands(voters)

    def answers(entry: rc.Entry) -> dict[str, list[str]]:
        return {name: run_reference(commands[name], entry) for name in voters}

    with ThreadPoolExecutor(jobs) as pool:
        all_answers = list(pool.map(answers, entries))
    records = []
    for entry, ans in zip(entries, all_answers):
        expected: list[str | None] = []
        splits: dict[str, dict[str, str]] = {}
        override = overrides.get(entry.pattern)
        for i, inp in enumerate(entry.inputs):
            if override:
                expected.append(None if override[0] == "none" else ans[override[0]][i])
                continue
            if utf8:
                # Two voters: their common answer, or else gawk's.
                expected.append(ans[UTF8_TIEBREAK][i])
                continue
            votes = Counter(ans[name][i] for name in REFERENCES)
            value, n = votes.most_common(1)[0]
            if n >= 2:
                expected.append(value)
            else:
                expected.append(None)
                splits[inp] = {name: ans[name][i] for name in REFERENCES}
        rec = {"category": entry.category, "pattern": entry.pattern, "inputs": list(entry.inputs),
               "expected": expected}
        if splits:
            rec["splits"] = splits
        if override:
            rec["override"] = f"{override[0]}: {override[1]}"
        # Which reference was outvoted where (handy when reading the file).
        outvoted = sorted({name for name in voters for i, e in enumerate(expected)
                           if e is not None and ans[name][i] != e})
        if outvoted:
            rec["outvoted"] = outvoted
        records.append(rec)
    if utf8:
        about = ("UTF-8 corpus under LC_ALL=C.UTF-8: onetrue-awk and gawk --posix, gawk's answer where they differ; "
                 "regenerate with scripts/regex_fuzz.py --utf8")
    else:
        about = "majority answers of mawk, onetrue-awk and gawk --posix; regenerate with scripts/regex_fuzz.py"
    return {"about": about, "records": records}


def compare(data: dict, show: int, category: str | None) -> int:
    """Print pyawk's mismatches per category; return the mismatching line count."""
    per_cat: dict[str, list[int]] = {}  # category -> [lines, bad lines, patterns, bad patterns, split lines]
    shown = 0
    total_bad = 0
    for rec in data["records"]:
        if category and rec["category"] != category:
            continue
        entry = rc.Entry(rec["category"], rec["pattern"], tuple(rec["inputs"]))
        got = split_lines(rc.run_pyawk(entry), entry)
        stats = per_cat.setdefault(rec["category"], [0, 0, 0, 0, 0])
        bad_here = 0
        for inp, exp, g in zip(rec["inputs"], rec["expected"], got):
            if exp is None:
                stats[4] += 1
                continue
            stats[0] += 1
            if g != exp:
                bad_here += 1
                if shown < show:
                    shown += 1
                    print(f"/{rec['pattern']}/ on {inp!r}: expected {exp!r}, pyawk {g!r}")
        stats[1] += bad_here
        stats[2] += 1
        stats[3] += bad_here > 0
        total_bad += bad_here
    print(f"{'category':<12} {'patterns':>8} {'bad':>5} {'lines':>6} {'bad':>5} {'split':>5}")
    sums = [0, 0, 0, 0, 0]
    for cat, (lines, bad, pats, badp, split) in per_cat.items():
        print(f"{cat:<12} {pats:>8} {badp:>5} {lines:>6} {bad:>5} {split:>5}")
        for i, v in enumerate((lines, bad, pats, badp, split)):
            sums[i] += v
    print(f"{'total':<12} {sums[2]:>8} {sums[3]:>5} {sums[0]:>6} {sums[1]:>5} {sums[4]:>5}")
    return total_bad


def explain(entry: rc.Entry) -> None:
    """Print each reference's and pyawk's answer for one pattern, per input."""
    voters = UTF8_REFERENCES if entry.utf8 else REFERENCES
    commands = references.commands(voters)
    answers = {name: run_reference(commands[name], entry) for name in voters}
    answers["pyawk"] = split_lines(rc.run_pyawk(entry), entry)
    print("columns: RSTART RLENGTH ~ gsub-count gsub-result split-count:pieces")
    for i, inp in enumerate(entry.inputs):
        print(f"{inp!r}")
        for name, lines in answers.items():
            print(f"    {name:<8} {lines[i]}")


def verify_safety(count: int) -> int:
    """Check regex._safe: wherever it trusts Python's match, it must be the longest.

    Exhaustive over runs of up to three single-character items (each atom
    with each repeat operator), then ``count`` seeded random items runs of
    length four and ``count`` random corpus-style EREs (with groups and
    alternation); every pattern judged safe is compared with the automaton
    on every string over {a, b, c} up to length 6.  Returns the number of
    unsafe verdicts found (0 expected).
    """
    import itertools
    import random
    import re

    from pyawk.regex import LongestPattern, _emit, _safe, parse

    strings = ["".join(p) for n in range(7) for p in itertools.product("abc", repeat=n)]
    items = [a + q for a in ("a", "b", "[ab]", ".") for q in ("", "?", "*", "+", "{1,2}", "{0,2}", "{2,}")]
    rnd = random.Random(7)
    patterns = ["".join(c) for n in (1, 2, 3) for c in itertools.product(items, repeat=n)]
    patterns += ["".join(rnd.choice(items) for _ in range(4)) for _ in range(count)]
    patterns += ["^" + p for p in rnd.sample(patterns, count // 4)]
    patterns += [p + "$" for p in rnd.sample(patterns, count // 4)]
    patterns += [e.pattern for e in rc.random_patterns(count, seed=11)]
    checked = bad = 0
    for pat in patterns:
        try:
            tree = parse(pat)
        except Exception:
            continue
        if not _safe(tree):
            continue
        checked += 1
        lp = LongestPattern(tree, re.compile(_emit(tree), re.DOTALL))
        for text in strings:
            for pos in range(len(text) + 1):
                m = lp.py.search(text, pos)
                if m is None:
                    break
                if lp.longest(text, m.start()) != m.end():
                    bad += 1
                    print(f"UNSAFE /{pat}/ on {text!r} from {pos}: python {m.span()}, longest end "
                          f"{lp.longest(text, m.start())}")
                    break
            else:
                continue
            break
    print(f"verify-safety: {checked} patterns judged safe checked, {bad} wrong")
    return bad


def main() -> int:
    harness.tolerant_output()
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--check", action="store_true", help="compare pyawk with the recorded file only")
    ap.add_argument("--utf8", action="store_true", help="the UTF-8 corpus, in a UTF-8 locale (no mawk)")
    ap.add_argument("--show", type=int, default=0, metavar="N", help="print the first N mismatches")
    ap.add_argument("--category", help="only this category")
    ap.add_argument("--jobs", type=int, default=8)
    ap.add_argument("--explain", metavar="ERE", help="show every reference's answer and pyawk's for one pattern")
    ap.add_argument("--inputs", help="with --explain: comma-separated inputs (default: the a/b/c set)")
    ap.add_argument("--verify-safety", type=int, nargs="?", const=1000, metavar="N",
                    help="check the safety analysis exhaustively (N random patterns per family)")
    args = ap.parse_args()
    if args.verify_safety is not None:
        return 1 if verify_safety(args.verify_safety) else 0
    try:
        return run(args)
    except references.MissingReference as exc:
        print(f"regex_fuzz: {exc}", file=sys.stderr)
        return 2


def run(args: argparse.Namespace) -> int:
    """main's work, once the options are read."""
    if args.explain is not None:
        inputs = tuple(args.inputs.split(",")) if args.inputs is not None else rc.ABC_INPUTS
        explain(rc.Entry("utf8-explain" if args.utf8 else "explain", args.explain, inputs))
        return 0
    results = UTF8_RESULTS if args.utf8 else RESULTS
    if args.check:
        data = json.loads(results.read_text(encoding="utf-8"))
    else:
        data = regenerate(rc.utf8_corpus() if args.utf8 else rc.corpus(), args.jobs, args.utf8)
        # ensure_ascii=True writes non-ASCII as \uXXXX escapes, which keeps the
        # invalid-byte stand-ins (lone surrogates) storable in a UTF-8 file.
        results.write_text(json.dumps(data, indent=1, ensure_ascii=args.utf8) + "\n", encoding="utf-8")
        n_split = sum(1 for r in data["records"] for e in r["expected"] if e is None)
        print(f"wrote {results.relative_to(ROOT)}: {len(data['records'])} patterns, {n_split} split lines")
    # The exit status tells scripts whether pyawk matched the record:
    # 0 if every line agreed, 1 if any did not.
    return 1 if compare(data, args.show, args.category) else 0


if __name__ == "__main__":
    sys.exit(main())
