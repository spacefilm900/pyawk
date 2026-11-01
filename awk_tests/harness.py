"""Shared golden-test helpers used by pytest and the scripts in scripts/.

Layout: ``awk_tests/golden/<category>/<name>.awk`` with ``<name>.in`` (stdin
operand file, may be empty) and ``<name>.out`` (expected stdout).  An
optional ``<name>.args`` holds extra command-line options (shell syntax),
e.g. ``-F:``; they are placed before ``-f <name>.awk``.

Each case runs as ``<awk> [args] -f <name>.awk <name>.in`` with the
category directory as the working directory.
"""

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


@dataclass(frozen=True)
class Case:
    category: str
    name: str
    directory: Path

    @property
    def id(self) -> str:
        return f"{self.category}/{self.name}"

    def path(self, ext: str) -> Path:
        return self.directory / f"{self.name}{ext}"

    def extra_args(self) -> list[str]:
        p = self.path(".args")
        return shlex.split(p.read_text()) if p.exists() else []

    def command(self, awk: list[str]) -> list[str]:
        return [*awk, *self.extra_args(), "-f", f"{self.name}.awk", f"{self.name}.in"]

    def expected(self) -> str:
        return self.path(".out").read_text(encoding="utf-8", errors="surrogateescape")


def discover(categories: tuple[str, ...] = CATEGORIES) -> list[Case]:
    cases: list[Case] = []
    for cat in categories:
        d = GOLDEN / cat
        if not d.is_dir():
            continue
        for awk_file in sorted(d.glob("*.awk")):
            cases.append(Case(cat, awk_file.stem, d))
    return cases


def pyawk_command() -> list[str]:
    return [sys.executable, "-m", "pyawk"]


def run_case(case: Case, awk: list[str], timeout: float = 20.0) -> subprocess.CompletedProcess[str]:
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
