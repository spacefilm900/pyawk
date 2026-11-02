"""Where the three reference awks are: mawk, onetrue-awk and gawk.

pyawk's tooling (regen_golden.py, regex_fuzz.py, the import scripts,
bench.py, gen_unicode_tables.py and some tests) compares pyawk with these
three programs.  Each one is found like this, first match wins:

1. An environment variable naming the program, either as a path
   (``/opt/onetrue-awk/a.out``, ``./a.out``) or as a name to look up on
   PATH (``gawk-5.4``):

   =========  =====================
   mawk       ``PYAWK_REF_MAWK``
   onetrue    ``PYAWK_REF_ONETRUE``
   gawk       ``PYAWK_REF_GAWK``
   =========  =====================

   If the variable is set but names nothing runnable, that is an error
   (``MissingReference``): a typo must not quietly fall back to some
   other awk.  An empty variable counts as unset.

2. The program's usual names on PATH: ``mawk``; ``original-awk``,
   ``onetrue-awk`` or ``bwk-awk`` (onetrue-awk is packaged under all
   three); ``gawk``.  Plain ``awk`` is never used: it is mawk on some
   systems, gawk or BWK awk on others.

The import scripts also need each suite's source: PYAWK_REF_ONETRUE_TESTDIR
(onetrue-awk's ``testdir/``, by default the one beside its program) and
PYAWK_REF_GAWK_TESTDIR (``test/`` in the gawk source tree), unless --src
is given.

There is deliberately no built-in default path: a location that exists
only on one machine would make the tools behave differently elsewhere.

gawk always runs as ``gawk --posix`` (its POSIX mode is the reference,
not its extensions), so ``command("gawk")`` adds that option.

The variables are named PYAWK_REF_*, not PYAWK_T_*: the test harness
removes every PYAWK_T_* variable before it runs a case (those belong to
the ENVIRON tests), and these must survive.
"""

from __future__ import annotations

import os
import shutil
from collections.abc import Iterable, Mapping
from pathlib import Path

# The variable that names each reference's program.
ENV_VARS = {
    "mawk": "PYAWK_REF_MAWK",
    "onetrue": "PYAWK_REF_ONETRUE",
    "gawk": "PYAWK_REF_GAWK",
}

# The import scripts also read each reference's own test suite (its source
# tree's test directory); this variable names it when --src does not.
TESTDIR_VARS = {
    "onetrue": "PYAWK_REF_ONETRUE_TESTDIR",
    "gawk": "PYAWK_REF_GAWK_TESTDIR",
}

# The names each reference is installed under, tried in this order on PATH.
PATH_NAMES = {
    "mawk": ("mawk",),
    "onetrue": ("original-awk", "onetrue-awk", "bwk-awk"),
    "gawk": ("gawk",),
}

# Options every run of a reference gets.
OPTIONS = {
    "mawk": [],
    "onetrue": [],
    "gawk": ["--posix"],
}


class MissingReference(LookupError):
    """A reference awk that cannot be found; the message says how to fix it."""


def program(name: str) -> str:
    """The absolute path of reference ``name``'s program, or MissingReference."""
    var = ENV_VARS[name]
    value = os.environ.get(var, "")
    if value:
        # shutil.which handles both forms: a path (it checks that the file
        # exists and may be run) and a bare name (it searches PATH).
        found = shutil.which(value)
        if not found:
            raise MissingReference(f"{var}={value} does not name a program that can be run")
        # The tools run awks from other directories, so "./a.out" must be
        # pinned down now, relative to where the user is.
        return os.path.abspath(found)
    for candidate in PATH_NAMES[name]:
        found = shutil.which(candidate)
        if found:
            return os.path.abspath(found)
    names = " or ".join(PATH_NAMES[name])
    raise MissingReference(f"{name} not found: set {var} to its program, or put {names} on PATH")


def command(name: str) -> list[str]:
    """The command line that runs reference ``name`` (program plus options)."""
    return [program(name), *OPTIONS[name]]


def commands(names: Iterable[str], explicit: Mapping[str, str | None] | None = None) -> dict[str, list[str]]:
    """{name: command} for the given references.  If any are missing, raise
    one MissingReference that lists them all (so a user setting things up
    learns everything that is missing in one go).

    ``explicit`` maps a name to a program given on the command line (the
    import scripts' --onetrue, --mawk, --gawk); it is used as it is, with
    the reference's usual options.  None there means "look it up"."""
    found, missing = {}, []
    for name in names:
        given = (explicit or {}).get(name)
        try:
            found[name] = [given, *OPTIONS[name]] if given else command(name)
        except MissingReference as exc:
            missing.append(str(exc))
    if missing:
        raise MissingReference("; ".join(missing))
    return found


def available() -> dict[str, list[str]]:
    """{name: command} for every reference that can be found (others left out)."""
    found = {}
    for name in ENV_VARS:
        try:
            found[name] = command(name)
        except MissingReference:
            pass
    return found


def test_dir(name: str, given: Path | None) -> Path:
    """The directory holding reference ``name``'s own test suite.

    First ``given`` (the import script's --src), then the TESTDIR_VARS
    variable; for onetrue-awk, which is normally built inside its source
    tree, also ``testdir/`` beside the program.  It must be a directory.
    """
    var = TESTDIR_VARS[name]
    if given is None and os.environ.get(var):
        given = Path(os.environ[var])
    if given is None and name == "onetrue":
        try:
            beside = Path(program("onetrue")).parent / "testdir"
        except MissingReference:
            beside = None
        if beside is not None and beside.is_dir():
            given = beside
    if given is None:
        raise MissingReference(f"no {name} test directory: give --src DIR or set {var}")
    if not given.is_dir():
        raise MissingReference(f"{name} test directory {given} is not a directory (from --src or {var})")
    return given
