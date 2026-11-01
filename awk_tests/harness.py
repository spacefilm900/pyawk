"""Shared golden-test helpers used by pytest and the scripts in scripts/.

Layout: ``awk_tests/golden/<category>/<name>.awk`` with ``<name>.in`` (stdin
operand file, may be empty) and ``<name>.out`` (expected stdout).  An
optional ``<name>.args`` holds extra command-line options (shell syntax),
e.g. ``-F:``; they are placed before ``-f <name>.awk``.

Each case runs as ``<awk> [args] -f <name>.awk <name>.in`` with the
category directory as the working directory.  The same function runs
either pyawk or a reference awk, which is what makes the outputs comparable.
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
CATEGORIES = ("basic", "fields", "patterns", "control", "arrays", "builtins", "one_liners")


# ``@dataclass`` generates the constructor from the annotated fields below,
# so ``Case("basic", "print_all", path)`` just works; ``frozen=True`` makes
# instances read-only.
@dataclass(frozen=True)
class Case:
    """One golden test: the files ``<directory>/<name>.{awk,in,out,args}``."""

    category: str
    name: str
    directory: Path

    # ``@property``: read as ``case.id`` without parentheses.
    @property
    def id(self) -> str:
        return f"{self.category}/{self.name}"

    def path(self, ext: str) -> Path:
        return self.directory / f"{self.name}{ext}"

    def extra_args(self) -> list[str]:
        p = self.path(".args")
        return shlex.split(p.read_text()) if p.exists() else []

    def command(self, awk: list[str]) -> list[str]:
        # ``[*a, *b, ...]`` builds one list from the items of a, then b, etc.
        return [*awk, *self.extra_args(), "-f", f"{self.name}.awk", f"{self.name}.in"]

    def expected(self) -> str:
        return self.path(".out").read_text(encoding="utf-8", errors="surrogateescape")


def discover(categories: tuple[str, ...] = CATEGORIES) -> list[Case]:
    """All cases, one per ``.awk`` file, in a stable (sorted) order."""
    cases: list[Case] = []
    for cat in categories:
        d = GOLDEN / cat
        if not d.is_dir():
            continue
        for awk_file in sorted(d.glob("*.awk")):
            cases.append(Case(cat, awk_file.stem, d))
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
