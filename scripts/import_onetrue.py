#!/usr/bin/env python3
"""Import the onetrue-awk ("The One True AWK", Kernighan's awk) test programs.

usage: scripts/import_onetrue.py [--src DIR] [--onetrue AWK] [--mawk AWK] [--gawk AWK]

The reference awks default to $PYAWK_REF_ONETRUE, $PYAWK_REF_MAWK and
$PYAWK_REF_GAWK, else to the programs on PATH; the source defaults to
$PYAWK_REF_ONETRUE_TESTDIR (else testdir/ beside the onetrue program).
See awk_tests/references.py.

The onetrue-awk repository has a directory ``testdir/`` with two families of
small programs:

* ``t.*`` -- about 160 programs that each exercise one construct.  Upstream
  runs them as ``awk -f t.X test.data``.
* ``p.*`` -- about 60 programs from chapters 1-2 of the AWK book.  Upstream
  runs them as ``awk -f p.X test.countries test.countries`` (the same input
  file twice, which exercises FNR versus NR).

This script copies each program into ``awk_tests/onetrue/`` using the layout
of our golden corpus (see awk_tests/harness.py):

    NAME.awk        the program
    NAME.in         its input (a copy of test.data or test.countries)
    NAME.operands   p.* only: "NAME.in NAME.in", so the input is read twice
    NAME.out        expected output, produced by the onetrue-awk binary

It then classifies every case and writes ``MANIFEST.tsv``, one line per case
with four tab-separated columns:

    name     the case name, e.g. t.fun or p.24
    status   run    -- compare pyawk's output with NAME.out exactly
             sorted -- compare after sorting the lines (the program prints
                       from ``for (k in a)``, whose order is unspecified)
             skip   -- do not run; see the reason
    reason   why a case is skipped (or why it is compared sorted)
    refs     how the three reference awks (onetrue, mawk, gawk --posix)
             compared on this program

Re-running the script regenerates everything except KNOWN_FAILURES and
NOTICE.md, so the files in awk_tests/onetrue/ should never be edited by
hand aside from those.
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
DEST = REPO / "awk_tests" / "onetrue"

# Make the pyawk package importable without installing it, so we can ask
# its parser which programs use features that are not implemented yet.
sys.path.insert(0, str(REPO))
from pyawk.errors import AwkSyntaxError, AwkUnsupportedError  # noqa: E402
from pyawk.parser import parse  # noqa: E402

sys.path.insert(0, str(REPO / "awk_tests"))
import harness  # noqa: E402
import references  # noqa: E402

# Upstream file that is named like a test but is really a helper program
# that upstream never runs on test.countries; we leave it out.
NOT_TESTS = {"p.table"}


@dataclass
class RefResult:
    """What one reference awk did with one program."""

    stdout: str
    stderr: str
    returncode: int
    new_files: list[str]  # files the program created in its directory


def run_ref(awk: list[str], name: str, case_dir: Path, operands: list[str]) -> RefResult:
    """Run one reference awk on one case, inside a throwaway copy of its files.

    Running in a scratch directory means a program that writes files (e.g.
    ``print > "foo.odd"``) cannot litter the repository, and lets us notice
    that it did so: such a program's real output is in those files, which
    our harness does not compare.
    """
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        for ext in (".awk", ".in"):
            shutil.copy(case_dir / f"{name}{ext}", work / f"{name}{ext}")
        before = set(os.listdir(work))
        # Same command shape and environment as harness.run_case uses.
        env = dict(os.environ, LC_ALL="C")
        try:
            proc = subprocess.run(
                [*awk, "-f", f"{name}.awk", *operands],
                cwd=work, capture_output=True, text=True, encoding="utf-8",
                errors="surrogateescape", timeout=20, env=env, stdin=subprocess.DEVNULL,
            )
        except subprocess.TimeoutExpired:
            return RefResult("", "timeout", -1, [])
        created = sorted(set(os.listdir(work)) - before)
        return RefResult(proc.stdout, proc.stderr, proc.returncode, created)


def static_skip_reason(source: str) -> str | None:
    """Return a reason to skip this program based on its text, or None.

    Order matters only for which reason is reported when several apply.
    """
    # 1. Ask pyawk's own parser: it raises AwkUnsupportedError for the
    #    features still deferred (none at present; nextfile was the last).
    #    Any other syntax error is *not* a skip: if the references accept the
    #    program, pyawk failing to parse it is a bug we want to see.
    try:
        parse(source)
    except AwkUnsupportedError as e:
        # str(e) looks like "syntax error at source line 1: FEATURE
        # is not supported yet (...)"; keep just the feature name so the
        # reasons group nicely in the report.
        m = re.search(r": (.+?) is not supported yet", str(e))
        return f"deferred: {m.group(1) if m else e}"
    except AwkSyntaxError:
        pass
    # 2. Features the task lists as out of scope even though pyawk accepts them.
    # A string literal of two or more characters assigned to RS: POSIX leaves
    # multi-character RS unspecified (gawk/mawk treat it as a regex).
    for m in re.finditer(r'\bRS\s*=\s*"((?:[^"\\]|\\.)*)"', source):
        text = m.group(1).encode().decode("unicode_escape")
        if len(text) > 1:
            return "deferred: multi-character RS"
    # 3. Output that legitimately differs between runs or implementations.
    if re.search(r"\b(rand|srand|systime)\s*\(", source):
        return "nondeterministic: rand/srand sequence is implementation-specific"
    return None


def failed(r: RefResult) -> bool:
    """True if the awk stopped with an error (nonzero exit plus a message)."""
    return r.returncode != 0 and r.stderr.strip() != ""


def classify(name: str, source: str, refs: dict[str, RefResult]) -> tuple[str, str, str]:
    """Decide (status, reason, refs-note) for one case."""
    # ``for (k in a)`` visits keys in an unspecified order; each awk has its
    # own hash order (pyawk uses insertion order), so compare sorted lines.
    unordered = re.search(r"\bfor\s*\(\s*\w+\s+in\s+", source) is not None

    def norm(r: RefResult) -> str:
        out = "".join(sorted(r.stdout.splitlines(keepends=True))) if unordered else r.stdout
        # An awk that stopped with an error message did not "agree" with
        # anyone, even if the partial output happens to match, so mark it.
        return ("<error>" + out) if failed(r) else out

    o, m, g = (norm(refs[k]) for k in ("onetrue", "mawk", "gawk"))
    if o == m == g:
        note = "agree"
    elif o == g:
        note = "mawk differs"
    elif o == m:
        note = "gawk --posix differs"
    elif m == g:
        note = "onetrue differs (mawk == gawk --posix)"
    else:
        note = "all three differ"
    # Also note reference errors, which usually mean a non-POSIX construct.
    errs = [k for k, r in refs.items() if failed(r)]
    if errs:
        note += "; error from: " + ",".join(errs)

    status, reason = ("sorted", "for-in order is unspecified") if unordered else ("run", "")
    skip = static_skip_reason(source)
    if skip:
        return "skip", skip, note
    if refs["onetrue"].new_files:
        return "skip", "writes files: " + " ".join(refs["onetrue"].new_files), note
    if failed(refs["onetrue"]):
        return "skip", "onetrue reports an error", note
    if failed(refs["mawk"]) and failed(refs["gawk"]):
        return "skip", "onetrue extension: mawk and gawk --posix reject it", note
    if o != m and o != g:
        # Either an onetrue-specific behaviour (mawk and gawk agree on
        # something else) or genuinely unspecified behaviour.
        return "skip", "onetrue quirk / unspecified: references disagree", note
    return status, reason, note


def main() -> int:
    harness.tolerant_output()
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--src", type=Path, help="onetrue-awk's testdir/ (default: $PYAWK_REF_ONETRUE_TESTDIR, "
                    "else testdir/ beside the onetrue program)")
    ap.add_argument("--onetrue", help="onetrue-awk program (default: $PYAWK_REF_ONETRUE, else on PATH)")
    ap.add_argument("--mawk", help="mawk program (default: $PYAWK_REF_MAWK, else on PATH)")
    ap.add_argument("--gawk", help="gawk program, run with --posix (default: $PYAWK_REF_GAWK, else on PATH)")
    opts = ap.parse_args()
    # Find everything first: a missing awk or test directory must stop the
    # import before it clears awk_tests/onetrue/.
    try:
        awks = references.commands(("onetrue", "mawk", "gawk"),
                                   explicit={"onetrue": opts.onetrue, "mawk": opts.mawk, "gawk": opts.gawk})
        opts.src = references.test_dir("onetrue", opts.src)
    except references.MissingReference as exc:
        print(f"import_onetrue: {exc}", file=sys.stderr)
        return 2

    # Keep our own files across the rebuild. Imported cases are regenerated;
    # KNOWN_FAILURES and the licence notices are not upstream test data.
    keep_names = ('KNOWN_FAILURES', 'NOTICE.md')
    kept = {}
    for name in keep_names:
        path = DEST / name
        if path.exists():
            kept[name] = path.read_bytes()
    if DEST.exists():
        shutil.rmtree(DEST)
    DEST.mkdir(parents=True)
    for name, data in kept.items():
        (DEST / name).write_bytes(data)

    names = sorted(p.name for p in opts.src.glob("[tp].*") if p.name not in NOT_TESTS)
    rows = []
    for name in names:
        source = (opts.src / name).read_text(encoding="utf-8", errors="surrogateescape")
        data = "test.data" if name.startswith("t.") else "test.countries"
        (DEST / f"{name}.awk").write_text(source, encoding="utf-8", errors="surrogateescape")
        shutil.copy(opts.src / data, DEST / f"{name}.in")
        operands = [f"{name}.in"]
        if name.startswith("p."):
            operands = [f"{name}.in", f"{name}.in"]
            (DEST / f"{name}.operands").write_text(" ".join(operands) + "\n", encoding="utf-8")

        # A dict comprehension: one RefResult per reference awk.
        refs = {k: run_ref(cmd, name, DEST, operands) for k, cmd in awks.items()}
        (DEST / f"{name}.out").write_text(refs["onetrue"].stdout, encoding="utf-8", errors="surrogateescape")
        status, reason, note = classify(name, source, refs)
        rows.append((name, status, reason, note))
        print(f"{status:<6} {name:<12} {reason}  [{note}]")

    with open(DEST / "MANIFEST.tsv", "w", encoding="utf-8") as f:
        f.write("# generated by scripts/import_onetrue.py -- do not edit\n")
        f.write("name\tstatus\treason\trefs\n")
        for row in rows:
            # Tabs/newlines inside a field would break the format.
            f.write("\t".join(col.replace("\t", " ").replace("\n", " ") for col in row) + "\n")
    counts: dict[str, int] = {}
    for _, status, _, _ in rows:
        counts[status] = counts.get(status, 0) + 1
    print(f"\nimported {len(rows)} cases:", ", ".join(f"{k}={v}" for k, v in sorted(counts.items())))
    return 0


if __name__ == "__main__":
    sys.exit(main())
