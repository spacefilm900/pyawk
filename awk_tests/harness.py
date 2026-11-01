"""Shared golden-test helpers used by pytest and the scripts in scripts/.

Layout: ``awk_tests/golden/<category>/<name>.awk`` with ``<name>.in`` (stdin
operand file, may be empty) and ``<name>.out`` (expected stdout).  An
optional ``<name>.args`` holds extra command-line options (shell syntax),
e.g. ``-F:``; they are placed before ``-f <name>.awk``.

Each case runs as ``<awk> [args] -f <name>.awk <name>.in`` with the
category directory as the working directory.  The same function runs
either pyawk or a reference awk, which is what makes the outputs comparable.

A second suite, ``awk_tests/onetrue/``, holds the test programs imported
from onetrue-awk by scripts/import_onetrue.py.  It uses the same files plus:

* ``<name>.operands`` (optional): replaces the single ``<name>.in`` operand
  (shell syntax), e.g. ``p.1.in p.1.in`` to read the input twice;
* ``MANIFEST.tsv``: per-case status -- ``run``, ``sorted`` (compare the
  output lines after sorting, because the program prints in the
  unspecified ``for (k in a)`` order) or ``skip`` -- with a reason;
* ``KNOWN_FAILURES`` (optional): names of cases pyawk is known to fail,
  one per line (``#`` starts a comment).  pytest reports them as expected
  failures, so the suite stays green while they are being fixed, and
  complains as soon as one starts passing so the list stays accurate.
"""

# Treat type hints as text, so newer syntax such as ``list[str]`` is allowed.
from __future__ import annotations

import os
import shlex
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent
GOLDEN = ROOT / "golden"
REPO = ROOT.parent
CATEGORIES = ("basic", "fields", "patterns", "control", "arrays", "builtins", "functions", "ranges", "one_liners")
ONETRUE = ROOT / "onetrue"
# Every category we know about: the golden ones plus the imported suite.
ALL_CATEGORIES = (*CATEGORIES, "onetrue")


def category_dir(cat: str) -> Path:
    """Where a category's files live (the onetrue suite is not under golden/)."""
    return ONETRUE if cat == "onetrue" else GOLDEN / cat


# ``@dataclass`` generates the constructor from the annotated fields below,
# so ``Case("basic", "print_all", path)`` just works; ``frozen=True`` makes
# instances read-only.
@dataclass(frozen=True)
class Case:
    """One golden test: the files ``<directory>/<name>.{awk,in,out,args}``."""

    category: str
    name: str
    directory: Path
    # The fields below have defaults, so golden cases can leave them out.
    status: str = "run"  # "run", "sorted" or "skip" (from MANIFEST.tsv)
    reason: str = ""  # why it is skipped / sorted
    known_failure: bool = False  # listed in KNOWN_FAILURES

    # ``@property``: read as ``case.id`` without parentheses.
    @property
    def id(self) -> str:
        return f"{self.category}/{self.name}"

    def path(self, ext: str) -> Path:
        return self.directory / f"{self.name}{ext}"

    def extra_args(self) -> list[str]:
        p = self.path(".args")
        return shlex.split(p.read_text()) if p.exists() else []

    def operands(self) -> list[str]:
        """The input files to name on the command line (usually just .in)."""
        p = self.path(".operands")
        return shlex.split(p.read_text()) if p.exists() else [f"{self.name}.in"]

    def command(self, awk: list[str]) -> list[str]:
        # ``[*a, *b, ...]`` builds one list from the items of a, then b, etc.
        return [*awk, *self.extra_args(), "-f", f"{self.name}.awk", *self.operands()]

    def expected(self) -> str:
        return self.path(".out").read_text(encoding="utf-8", errors="surrogateescape")

    @property
    def skipped(self) -> bool:
        return self.status == "skip"

    def matches(self, stdout: str) -> bool:
        """Does this output count as correct?  (Order-blind for "sorted".)"""
        expected = self.expected()
        if self.status == "sorted":
            # ``sorted(...)`` returns the lines in order; keepends=True keeps
            # each line's "\n" so a missing final newline still counts.
            return sorted(stdout.splitlines(keepends=True)) == sorted(expected.splitlines(keepends=True))
        return stdout == expected


def read_manifest(directory: Path) -> dict[str, tuple[str, str]]:
    """Map case name -> (status, reason) from ``MANIFEST.tsv``, if present."""
    p = directory / "MANIFEST.tsv"
    result: dict[str, tuple[str, str]] = {}
    if not p.exists():
        return result
    for line in p.read_text().splitlines():
        if not line or line.startswith("#") or line.startswith("name\t"):
            continue  # blank, comment or the header row
        name, status, reason, *_rest = line.split("\t")
        result[name] = (status, reason)
    return result


def read_known_failures(directory: Path) -> set[str]:
    """Names listed in ``KNOWN_FAILURES`` (text after ``#`` is a comment)."""
    p = directory / "KNOWN_FAILURES"
    if not p.exists():
        return set()
    # A set comprehension; ``partition("#")[0]`` is the text before any "#".
    names = {line.partition("#")[0].strip() for line in p.read_text().splitlines()}
    return names - {""}


def read_overrides() -> dict[str, str]:
    """Hand-corrected golden cases: ``{"CATEGORY/NAME": reason}``.

    ``golden/OVERRIDES`` lists cases whose ``.out`` was written by hand
    because the reference awk (mawk) is the odd one out among onetrue-awk,
    gawk and mawk.  Each line is ``CATEGORY/NAME  reason``; ``#`` starts a
    comment.  regen_golden.py never overwrites these ``.out`` files.
    """
    p = GOLDEN / "OVERRIDES"
    if not p.exists():
        return {}
    out: dict[str, str] = {}
    for line in p.read_text().splitlines():
        line = line.partition("#")[0].strip()
        if line:
            # ``split(None, 1)`` splits once, on the first run of whitespace.
            case_id, reason = (line.split(None, 1) + [""])[:2]
            out[case_id] = reason.strip()
    return out


def discover(categories: tuple[str, ...] = ALL_CATEGORIES) -> list[Case]:
    """All cases, one per ``.awk`` file, in a stable (sorted) order."""
    cases: list[Case] = []
    for cat in categories:
        d = category_dir(cat)
        if not d.is_dir():
            continue
        manifest = read_manifest(d)
        known = read_known_failures(d)
        for awk_file in sorted(d.glob("*.awk")):
            name = awk_file.stem  # file name without the final ".awk"
            # ``.get(key, default)``: cases missing from the manifest just run.
            status, reason = manifest.get(name, ("run", ""))
            cases.append(Case(cat, name, d, status, reason, name in known))
    return cases


def pyawk_command() -> list[str]:
    """Run pyawk with the same Python that is running us (e.g. the venv's)."""
    return [sys.executable, "-m", "pyawk"]


def run_case(case: Case, awk: list[str], timeout: float = 20.0) -> subprocess.CompletedProcess[str]:
    """Run one case with the given awk command and capture its output.

    PYTHONPATH lets ``python -m pyawk`` find the package without installing
    it; LC_ALL=C gives every awk the same, simple locale.  stdin is empty so
    a program that wrongly reads it cannot hang.
    """
    env = dict(os.environ)
    env["PYTHONPATH"] = str(REPO) + os.pathsep + env.get("PYTHONPATH", "")
    env["LC_ALL"] = "C"
    return subprocess.run(
        case.command(awk),
        cwd=case.directory,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="surrogateescape",
        timeout=timeout,
        env=env,
        stdin=subprocess.DEVNULL,
    )
