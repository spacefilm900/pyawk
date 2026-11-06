"""Shared golden-test helpers used by pytest and the scripts in scripts/.

Layout: ``awk_tests/golden/<category>/<name>.awk`` with ``<name>.in`` (stdin
operand file, may be empty) and ``<name>.out`` (expected stdout).  An
optional ``<name>.args`` holds extra command-line options (shell syntax),
e.g. ``-F:``; they are placed before ``-f <name>.awk``.  Optional
``<name>.status`` holds the expected exit status (an integer; default 0
when the file is absent).  Optional ``<name>.err`` describes expected
stderr.  Without it, stderr is not checked.  With it, the first line may
be ``mode: portable``, ``mode: exact``, ``mode: substr`` or ``mode: regex``;
the rest of the file is the body.  ``portable`` means POSIX requires a
diagnostic (stderr must be non-empty) without locking the wording --
copying gawk text is not conformance.  ``exact`` / ``substr`` / ``regex``
are intentional implementation-specific checks.  A ``.err`` with no mode
line defaults to ``exact`` over the whole file.

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

A third suite, ``awk_tests/gawk/``, holds tests imported from GNU awk's
test directory by scripts/import_gawk.py.  It uses everything above plus:

* ``<name>.stdin`` (optional): data fed to the program on standard input.
  gawk's test rules mostly run ``gawk -f T.awk < T.in``, so those cases
  have a ``.stdin`` file and an empty ``.operands`` file (no file names on
  the command line).  Without ``.stdin`` the program gets empty input.

Cases in the ``utf8`` category run with ``LC_ALL=C.UTF-8`` (see LOCALES),
all others with ``LC_ALL=C``.

Two more files are available to every case (used by the ``environ``, ``io``
and ``nextfile`` golden categories):

* ``<name>.environ`` (optional): ``NAME=value`` lines added to the
  environment the awk runs with, so ENVIRON tests see known values (blank
  lines are skipped; any other line without a name and "=" is an error).
* Golden cases (the categories under ``golden/``) run in a *throwaway copy*
  of their category directory, made fresh for every run and deleted
  afterwards.  A program may therefore create, append to and read back
  files with relative names (``print > "out.txt"``) without touching the
  repository, and every run starts from the same files.
"""

# Treat type hints as text, so newer syntax such as ``list[str]`` is allowed.
from __future__ import annotations

import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from typing import NamedTuple
from pathlib import Path

ROOT = Path(__file__).resolve().parent
GOLDEN = ROOT / "golden"
REPO = ROOT.parent
CATEGORIES = (
    "basic", "fields", "patterns", "control", "arrays", "builtins", "functions", "ranges", "one_liners",
    "environ", "io", "nextfile", "regex_posix", "utf8", "errors",
)
# The locale (LC_ALL) each category runs in; every other category runs in
# the C locale, where a character is a byte.  The ``utf8`` cases test
# pyawk's character mode, so they run in a UTF-8 locale.
LOCALES = {"utf8": "C.UTF-8"}


def tolerant_output() -> None:
    """Never fail to print because of the locale.

    The scripts print case names, reasons and diffs, which may contain
    non-ASCII text (golden/OVERRIDES does).  Python encodes standard output
    and standard error with the locale's encoding, so under an ASCII locale
    such a print raised UnicodeEncodeError.  ``reconfigure`` keeps the
    encoding but writes a character it cannot encode as a backslash escape
    (``\\xe9`` for "é").  Every script's ``main()`` calls this first.
    """
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(errors="backslashreplace")


def locale_for(case_id: str) -> str:
    """The LC_ALL value for the case ``CATEGORY/NAME``."""
    # ``partition("/")[0]`` is the text before the first "/": the category.
    return LOCALES.get(case_id.partition("/")[0], "C")
ONETRUE = ROOT / "onetrue"
GAWK = ROOT / "gawk"
# The imported suites (not under golden/), keyed by category name.
IMPORTED = {"onetrue": ONETRUE, "gawk": GAWK}
# Every category we know about: the golden ones plus the imported suites.
ALL_CATEGORIES = (*CATEGORIES, *IMPORTED)


def category_dir(cat: str) -> Path:
    """Where a category's files live (the imported suites are not under golden/)."""
    # ``d.get(key, default)``: the imported suite's directory, else golden/<cat>.
    return IMPORTED.get(cat, GOLDEN / cat)


# How a ``.err`` file is interpreted (see Case.expected_stderr).
STDERR_MODES = frozenset({"portable", "exact", "substr", "regex"})


class StderrExpectation(NamedTuple):
    """Expected stderr check: a mode and, for non-portable modes, a body."""

    mode: str  # one of STDERR_MODES
    body: str  # unused for portable; text or pattern otherwise


# ``@dataclass`` generates the constructor from the annotated fields below,
# so ``Case("basic", "print_all", path)`` just works; ``frozen=True`` makes
# instances read-only.
@dataclass(frozen=True)
class Case:
    """One golden test: the files ``<directory>/<name>.{awk,in,out,args,status,err}``."""

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
        return shlex.split(p.read_text(encoding="utf-8")) if p.exists() else []

    def operands(self) -> list[str]:
        """The input files to name on the command line (usually just .in)."""
        p = self.path(".operands")
        return shlex.split(p.read_text(encoding="utf-8")) if p.exists() else [f"{self.name}.in"]

    def stdin_data(self) -> str | None:
        """What to feed on standard input (from ``.stdin``), or None for nothing."""
        p = self.path(".stdin")
        return p.read_text(encoding="utf-8", errors="surrogateescape") if p.exists() else None

    def command(self, awk: list[str]) -> list[str]:
        # ``[*a, *b, ...]`` builds one list from the items of a, then b, etc.
        return [*awk, *self.extra_args(), "-f", f"{self.name}.awk", *self.operands()]

    def extra_env(self) -> dict[str, str]:
        """Environment variables from ``<name>.environ`` (``NAME=value`` lines).

        The value is everything after the first "=" (it may be empty or
        contain spaces); blank lines are skipped.  A line with no "=" or
        with nothing before it is a mistake in the case, so it raises
        ValueError naming the file, the line number and the line.
        """
        p = self.path(".environ")
        if not p.exists():
            return {}
        result: dict[str, str] = {}
        # ``enumerate(..., 1)`` numbers the lines from 1, as editors do.
        for number, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
            if not line.strip():
                continue
            # ``partition`` splits at the first "="; ``equals`` is "" when
            # there is none.
            name, equals, value = line.partition("=")
            if not equals or not name:
                # ``{line!r}`` shows the line quoted, as Python writes it.
                raise ValueError(f"{p}:{number}: not NAME=value: {line!r}")
            result[name] = value
        return result

    @property
    def sandboxed(self) -> bool:
        """Golden cases run in a throwaway copy of their directory."""
        return self.directory.parent == GOLDEN

    def expected(self) -> str:
        return self.path(".out").read_text(encoding="utf-8", errors="surrogateescape")

    def expected_exit(self) -> int:
        """Expected process exit status: from ``.status``, or 0 if absent.

        The file holds one integer (leading and trailing whitespace is fine;
        a ``#`` comment may follow the number).  An empty or non-integer
        file is a mistake in the case and raises ValueError.
        """
        p = self.path(".status")
        if not p.exists():
            return 0
        # First non-empty line after stripping a ``#`` comment, then int().
        raw = p.read_text(encoding="utf-8")
        line = raw.splitlines()[0] if raw.strip() else ""
        number = line.partition("#")[0].strip()
        if not number:
            raise ValueError(f"{p}: empty status file")
        try:
            return int(number)
        except ValueError as exc:
            raise ValueError(f"{p}: not an integer: {line!r}") from exc

    def expected_stderr(self) -> StderrExpectation | None:
        """How to check stderr from ``.err``, or None when stderr is not checked.

        The file starts with an optional ``mode: NAME`` line (``portable``,
        ``exact``, ``substr`` or ``regex``); the rest is the body.  Without a
        mode line the whole file is an ``exact`` match (implementation-
        specific).  ``portable`` means stderr must be non-empty: POSIX says
        a diagnostic is required, not what it says, so this does not treat
        gawk or pyawk wording as conformance.  ``exact``, ``substr`` and
        ``regex`` lock an intentional implementation-specific expectation.
        """
        p = self.path(".err")
        if not p.exists():
            return None
        raw = p.read_text(encoding="utf-8", errors="surrogateescape")
        # Split off a leading ``mode: NAME`` line when present.
        first, sep, rest = raw.partition("\n")
        mode_line = first.partition("#")[0].strip()
        if mode_line.lower().startswith("mode:"):
            mode = mode_line.split(":", 1)[1].strip().lower()
            if mode not in STDERR_MODES:
                raise ValueError(f"{p}: unknown stderr mode {mode!r}")
            body = rest if sep else ""
            return StderrExpectation(mode, body)
        # No mode line: the whole file is an exact, implementation-specific check.
        return StderrExpectation("exact", raw)

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

    def stderr_matches(self, stderr: str, expect: StderrExpectation) -> bool:
        """Does ``stderr`` satisfy a ``.err`` expectation?"""
        if expect.mode == "portable":
            return stderr != ""
        if expect.mode == "exact":
            return stderr == expect.body
        # substr/regex: a single trailing newline on the body is ignored so
        # writing ``mode: substr`` then ``text`` then a newline still matches.
        body = expect.body[:-1] if expect.body.endswith("\n") else expect.body
        if expect.mode == "substr":
            return body in stderr
        if expect.mode == "regex":
            return re.search(body, stderr, flags=re.MULTILINE) is not None
        # expected_stderr already rejects unknown modes; this is a safeguard.
        raise ValueError(f"unknown stderr mode {expect.mode!r}")

    def result_problems(self, proc: subprocess.CompletedProcess[str] | object) -> list[str]:
        """Ways this run disagrees with the case: stdout, exit status, stderr.

        ``proc`` needs ``stdout``, ``stderr`` and ``returncode`` attributes
        (a ``CompletedProcess``, or a stand-in in tests).  An empty list
        means the run matched.  Stderr is only checked when ``.err`` exists.
        """
        problems: list[str] = []
        if not self.matches(proc.stdout):
            problems.append("stdout mismatch")
        want_exit = self.expected_exit()
        if proc.returncode != want_exit:
            problems.append(f"exit status {proc.returncode}, expected {want_exit}")
        want_err = self.expected_stderr()
        if want_err is not None and not self.stderr_matches(proc.stderr, want_err):
            problems.append("stderr mismatch")
        return problems


def read_manifest(directory: Path) -> dict[str, tuple[str, str]]:
    """Map case name -> (status, reason) from ``MANIFEST.tsv``, if present."""
    p = directory / "MANIFEST.tsv"
    result: dict[str, tuple[str, str]] = {}
    if not p.exists():
        return result
    for line in p.read_text(encoding="utf-8").splitlines():
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
    names = {line.partition("#")[0].strip() for line in p.read_text(encoding="utf-8").splitlines()}
    return names - {""}


def read_overrides() -> dict[str, str]:
    """Hand-corrected golden cases: ``{"CATEGORY/NAME": reason}``.

    ``golden/OVERRIDES`` lists cases whose ``.out`` was written by hand
    because the reference awk (mawk) is the odd one out among onetrue-awk,
    gawk and mawk.  Each line is ``CATEGORY/NAME  reason``; ``#`` starts a
    comment.  regen_golden.py never overwrites these ``.out`` files.
    """
    return _read_case_list(GOLDEN / "OVERRIDES")


def read_decisions() -> dict[str, str]:
    """Golden cases that follow the user's decision, not the majority.

    ``golden/DECISIONS`` (same format as OVERRIDES) lists cases where the
    references disagree with what the project chose to do, for example
    close() returning a command's exit status.  Their ``.out`` is still
    written by regen_golden.py (mawk gives the chosen answer); the list
    only tells ``regen_golden.py --vote`` not to report them.
    """
    return _read_case_list(GOLDEN / "DECISIONS")


def _read_case_list(p: Path) -> dict[str, str]:
    """Read a ``CATEGORY/NAME  reason`` file into ``{case id: reason}``."""
    if not p.exists():
        return {}
    out: dict[str, str] = {}
    for line in p.read_text(encoding="utf-8").splitlines():
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
    it; LC_ALL=C gives every awk the same, simple locale (C.UTF-8 for the
    ``utf8`` category, see LOCALES).  stdin is the
    case's ``.stdin`` data if it has one, and otherwise empty, so a program
    that wrongly reads it cannot hang.  Inherited ``PYAWK_T_*`` variables are
    dropped and ``.environ`` lines are added to the environment.

    Golden cases run in a throwaway copy of their category directory, so a
    program that writes files (``print > "out.txt"``) cannot litter the
    repository or see leftovers from an earlier run; the copy is deleted
    afterwards.  Imported cases run in place, as their importers expect.
    """
    # Start from our own environment, minus any PYAWK_T_ variables: those
    # names are reserved for ``.environ`` files, and a stray one exported
    # in the user's shell would otherwise show up in ENVIRON cases and make
    # their output depend on who runs them.
    env = {k: v for k, v in os.environ.items() if not k.startswith("PYAWK_T_")}
    env["PYTHONPATH"] = str(REPO) + os.pathsep + env.get("PYTHONPATH", "")
    env["LC_ALL"] = locale_for(case.id)
    env.update(case.extra_env())
    data = case.stdin_data()
    if not case.sandboxed:
        return _run(case, awk, case.directory, env, data, timeout)
    # ``with`` deletes the temporary directory (and anything the program
    # wrote there) when the block ends, even if the run fails.
    with tempfile.TemporaryDirectory(prefix="pyawk-golden-") as tmp:
        work = Path(tmp) / case.category
        shutil.copytree(case.directory, work)
        return _run(case, awk, work, env, data, timeout)


def _run(
    case: Case, awk: list[str], cwd: Path, env: dict[str, str], data: str | None, timeout: float
) -> subprocess.CompletedProcess[str]:
    """The subprocess call behind run_case, in working directory ``cwd``."""
    return subprocess.run(
        case.command(awk),
        cwd=cwd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="surrogateescape",
        timeout=timeout,
        env=env,
        # ``input=`` sends that text to the program's stdin; ``input=None``
        # (no .stdin file) still connects stdin, so give it /dev/null instead.
        input=data,
        stdin=subprocess.DEVNULL if data is None else None,
    )
