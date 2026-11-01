#!/usr/bin/env python3
"""Benchmark pyawk against the reference awks on representative workloads.

usage: scripts/bench.py [--lines N] [--runs R] [--only NAME,...] [--awks pyawk,mawk,...]
                        [--path NAME=PROGRAM ...] [--save LABEL] [--compare LABEL]

Each workload is an AWK program plus (usually) a generated input file.  The
inputs are made by a seeded random generator, so every run of this script
sees byte-for-byte the same data; they are cached in ``.bench/`` (ignored
by git) and rebuilt only when the size or the generator changes.

Every program is run with each awk ``--runs`` times and the *best* (lowest)
wall-clock time is kept: the minimum is the least disturbed by other
activity on the machine, so it is the most repeatable figure.  Output goes
to a temporary file and is compared with the first reference awk's (mawk
if installed, else gawk, else onetrue-awk), so the benchmark doubles as a
quick correctness check (``DIFF`` marks a mismatch).

The reference awks are found by name on PATH (``mawk``; ``gawk``, run with
``--posix``; onetrue-awk as ``original-awk``, ``onetrue-awk`` or
``bwk-awk``), and by default every one that is installed is timed.
``--path NAME=PROGRAM`` points at one that is not on PATH, for example
``--path onetrue=$HOME/src/onetrue-awk/a.out``.

``--save LABEL`` stores the timings in ``.bench/LABEL.json``;
``--compare LABEL`` adds a column with the speedup of pyawk relative to a
saved run (for "before/after" tables).
"""

from __future__ import annotations

import argparse
import json
import os
import random
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
CACHE = REPO / ".bench"
# Bump this when the generator below changes, so cached inputs are rebuilt.
GENERATOR_VERSION = 1

# The awks we know how to time.  For each reference awk: the program names
# to look for on PATH (first found wins) and extra options to pass.  Plain
# "awk" is deliberately not in any list: it may be mawk, gawk or BWK awk
# depending on the system (on macOS it is BWK awk), so results labelled
# "mawk" could silently be about another awk.
REFERENCE_AWKS: dict[str, tuple[list[str], list[str]]] = {
    "mawk": (["mawk"], []),
    "gawk": (["gawk"], ["--posix"]),
    # onetrue-awk (BWK awk) is packaged under several names.
    "onetrue": (["original-awk", "onetrue-awk", "bwk-awk"], []),
}


def find_awks(paths: dict[str, str]) -> dict[str, list[str]]:
    """Return {name: command} for pyawk and every reference awk we can find.

    ``paths`` gives explicit locations (from ``--path NAME=PROGRAM``), e.g.
    an awk built from source that is not on PATH; the others are looked up
    by name with ``shutil.which`` (the same search the shell does).  Awks
    that are not found are simply left out.
    """
    awks = {"pyawk": [sys.executable, "-m", "pyawk"]}
    for name, (candidates, options) in REFERENCE_AWKS.items():
        if name in paths:
            found: str | None = paths[name]
        else:
            # ``next(..., None)``: the first candidate that is installed,
            # or None if none is.
            found = next((p for p in map(shutil.which, candidates) if p), None)
        if found:
            awks[name] = [found] + options
    return awks

WORDS = (
    "alpha bravo charlie delta echo foxtrot golf hotel india juliet kilo lima mike "
    "november oscar papa quebec romeo sierra tango uniform victor whiskey xray yankee zulu "
    "foo bar baz qux quux corge grault garply waldo fred plugh xyzzy thud"
).split()


@dataclass
class Workload:
    name: str
    program: str
    description: str
    uses_input: bool = True


WORKLOADS = [
    Workload("print1", "{ print $1 }", "print the first field"),
    Workload("sum", "{ s += $3 } END { printf \"%.2f\\n\", s }", "sum a numeric column"),
    Workload(
        "fields",
        "{ for (i = 1; i <= NF; i++) t += length($i); nf += NF; if (NF > 6) $2 = \"X\" } "
        "NR % 1000 == 0 { print } END { print nf, t }",
        "NF, a loop over every field, field assignment",
    ),
    Workload(
        "regex",
        "/[0-9]+5\\.|qu+x.*thud/ { c++ } $2 ~ /^(b|d)[a-z]+o$/ { d++ } $5 !~ /a/ { e++ } "
        "END { print c, d, e }",
        "regex match filters",
    ),
    Workload(
        "wordcount",
        "{ for (i = 1; i <= NF; i++) cnt[$i]++ } "
        "END { for (w in cnt) n++; print n, cnt[\"foo\"], cnt[\"zulu\"] }",
        "associative-array word counts",
    ),
    Workload("printf", "{ printf \"%-10s|%8.2f|%5d|%x\\n\", $2, $3, $4, $4 }", "printf formatting"),
    Workload("gsub", "{ n += gsub(/[aeiou]/, \"<&>\"); sub(/^[0-9]+/, \"#\") } NR % 100 == 0 { print } "
             "END { print n }", "gsub/sub on every record"),
    Workload(
        "funcs",
        "function fib(n) { return n < 2 ? n : fib(n - 1) + fib(n - 2) } "
        "function f(x) { return x * 2 + 1 } "
        "{ s += f($4) } END { print s, fib(22) }",
        "a function call per record plus recursive fib(22)",
    ),
    Workload(
        "begin_loop",
        "BEGIN { for (i = 0; i < 1000000; i++) { s += i % 7; if (i % 3 == 0) t = t \"\" } "
        "x = \"\"; for (i = 0; i < 100000; i++) x = x \"a\"; print s, length(x) }",
        "a BEGIN-only loop: arithmetic, comparisons, string building",
        uses_input=False,
    ),
]


def make_input(lines: int) -> Path:
    """Deterministic input: id, word, float, int, then 2-8 more words.

    Example line: ``17 delta 532.25 801 foo xray 42 hotel``.
    """
    CACHE.mkdir(exist_ok=True)
    path = CACHE / f"input-v{GENERATOR_VERSION}-{lines}.txt"
    if path.exists():
        return path
    rng = random.Random(20261101)
    with open(path.with_suffix(".tmp"), "w") as f:
        for i in range(1, lines + 1):
            extra = [rng.choice(WORDS) if rng.random() < 0.8 else str(rng.randint(0, 999))
                     for _ in range(rng.randint(2, 8))]
            f.write(f"{i} {rng.choice(WORDS)} {rng.uniform(0, 1000):.2f} {rng.randint(0, 100000)} "
                    + " ".join(extra) + "\n")
    path.with_suffix(".tmp").rename(path)
    return path


def time_one(cmd: list[str], runs: int) -> tuple[float, bytes]:
    """Best wall-clock time of ``runs`` runs, plus the output of the last one."""
    best = float("inf")
    out = b""
    env = dict(os.environ, LC_ALL="C", PYTHONPATH=str(REPO))
    for _ in range(runs):
        with tempfile.TemporaryFile() as tmp:
            start = time.perf_counter()
            proc = subprocess.run(cmd, stdout=tmp, stderr=subprocess.PIPE, env=env, cwd=REPO)
            elapsed = time.perf_counter() - start
            tmp.seek(0)
            out = tmp.read()
        if proc.returncode != 0:
            raise SystemExit(f"{' '.join(cmd[:3])}... failed: {proc.stderr.decode(errors='replace')}")
        best = min(best, elapsed)
    return best, out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--lines", type=int, default=200_000, help="input size (default 200000)")
    ap.add_argument("--runs", type=int, default=3, help="runs per measurement; the best is kept")
    ap.add_argument("--only", help="comma-separated workload names")
    ap.add_argument("--awks", help="comma-separated awks to time (default: all that are found)")
    ap.add_argument("--path", action="append", default=[], metavar="NAME=PROGRAM",
                    help="where to find a reference awk (mawk, gawk, onetrue); repeatable")
    ap.add_argument("--save", metavar="LABEL", help="save timings to .bench/LABEL.json")
    ap.add_argument("--compare", metavar="LABEL", help="show pyawk speedup against a saved run")
    opts = ap.parse_args()

    paths = {}
    for item in opts.path:
        name, sep, prog = item.partition("=")
        if not sep or name not in REFERENCE_AWKS:
            ap.error(f"--path wants NAME=PROGRAM with NAME one of {', '.join(REFERENCE_AWKS)}")
        paths[name] = prog
    available = find_awks(paths)
    awks = opts.awks.split(",") if opts.awks else list(available)
    missing = [a for a in awks if a not in available]
    if missing:
        ap.error(f"not found: {', '.join(missing)} (install it or give --path NAME=PROGRAM)")
    # Outputs are checked against, and pyawk's time divided by, this awk.
    ref = next((a for a in ("mawk", "gawk", "onetrue") if a in awks), None)
    selected = [w for w in WORKLOADS if not opts.only or w.name in opts.only.split(",")]
    data = make_input(opts.lines)
    baseline = json.loads((CACHE / f"{opts.compare}.json").read_text()) if opts.compare else None

    results: dict[str, dict[str, float]] = {}
    header = ["workload"] + awks + ([f"pyawk/{ref}"] if "pyawk" in awks and ref else [])
    if baseline:
        header.append(f"speedup vs {opts.compare}")
    print(f"# {opts.lines} input lines, best of {opts.runs} runs, seconds\n")
    print("| " + " | ".join(header) + " |")
    print("|" + "---|" * len(header))
    for w in selected:
        files = [str(data)] if w.uses_input else []
        times: dict[str, float] = {}
        outputs: dict[str, bytes] = {}
        for name in awks:
            times[name], outputs[name] = time_one(available[name] + [w.program] + files, opts.runs)
        results[w.name] = times
        cells = [w.name]
        for name in awks:
            mark = ""
            if ref and outputs[name] != outputs[ref]:
                mark = " DIFF"
            cells.append(f"{times[name]:.3f}{mark}")
        if "pyawk" in times and ref:
            cells.append(f"{times['pyawk'] / times[ref]:.1f}x")
        if baseline and w.name in baseline and "pyawk" in times:
            cells.append(f"{baseline[w.name]['pyawk'] / times['pyawk']:.2f}x")
        print("| " + " | ".join(cells) + " |", flush=True)
    if opts.save:
        CACHE.mkdir(exist_ok=True)
        (CACHE / f"{opts.save}.json").write_text(json.dumps(results, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
