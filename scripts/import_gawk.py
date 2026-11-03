#!/usr/bin/env python3
"""Import tests from GNU awk's test suite (gawk-5.4.1/test).

usage: scripts/import_gawk.py [--src DIR] [--onetrue AWK] [--mawk AWK] [--gawk AWK]

The reference awks default to $PYAWK_REF_ONETRUE, $PYAWK_REF_MAWK and
$PYAWK_REF_GAWK, else to the programs on PATH; the source defaults to
$PYAWK_REF_GAWK_TESTDIR.
See awk_tests/references.py.

gawk's ``test/Makefile.am`` names its tests in lists.  We import two:

* ``BASIC_TESTS`` -- about 300 tests of core awk behaviour;
* ``NEED_POSIX`` -- the tests gawk itself runs with ``--posix``.  (gawk
  5.4.1 has no list called ``POSIX_TESTS``; NEED_POSIX is the closest
  thing, and all but one of its tests are also in BASIC_TESTS.)

How gawk runs a test
--------------------
Most tests have no rule of their own in Makefile.am: the helper script
``Gentests`` generates one, which runs

    gawk -f T.awk [flags] [< T.in] > _T 2>&1 || echo EXIT CODE: $? >> _T

and compares ``_T`` with ``T.ok``.  The flags come from the other lists
(``--posix`` for NEED_POSIX, ``--lint`` for NEED_LINT, and so on).  Note
three differences from our harness:

1. the input file is fed on *standard input*, not named as an operand;
2. error messages (stderr) are mixed into ``T.ok``;
3. a nonzero exit status adds a final line ``EXIT CODE: N``.

We handle 1 with the harness's ``.stdin`` file plus an empty ``.operands``
file.  For 2 and 3 we rebuild the stdout-only part of ``T.ok`` (our harness
compares stdout only; see "Expected output" below).

About 25 tests do have their own rule in Makefile.am (extra operands,
pipelines, several programs...).  The simple ones are described by hand in
SPECIAL below; the rest are skipped with the reason in CUSTOM_SKIP.  The
script stops with an error if it meets a custom rule that is in neither,
so a newer gawk release cannot slip one past unnoticed.

Expected output
---------------
gawk's ``.ok`` files record what gawk does in its *default* mode, which
includes many extensions.  We only trust a ``.ok`` file when the majority
of our three reference awks (onetrue-awk, mawk, gawk --posix) produce the
same stdout:

* at least two of the three agree with the ``.ok`` -> use it;
* otherwise, if two of the three agree with each other -> use their output
  (the MANIFEST row says so);
* otherwise -> skip, "no majority".

A case is also skipped when its ``.ok`` is only gawk error text (an
error-message test: our messages are worded differently, and stderr is not
compared), when the majority of references reject the program, or when the
program uses a feature from the skip list in ``static_skip_reason``.

Output files (layout as in awk_tests/harness.py):

    NAME.awk        the program (for skipped tests without one: a comment)
    NAME.stdin      data for standard input (gawk's T.in), if any
    NAME.in         input file named on the command line, if any
    NAME.operands   command-line operands (often empty)
    NAME.args       options before -f, e.g. "-v SORT=sort", if any
    NAME.out        expected stdout
    MANIFEST.tsv    name, status, reason, refs (as for the onetrue suite)

Re-running the script regenerates everything except KNOWN_FAILURES, so the
files in awk_tests/gawk/ should never be edited by hand.
"""

from __future__ import annotations

import argparse
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
DEST = REPO / "awk_tests" / "gawk"

# Make the pyawk package importable without installing it, so we can ask
# its parser which programs use features that are not implemented yet.
sys.path.insert(0, str(REPO))
from pyawk.errors import AwkSyntaxError, AwkUnsupportedError  # noqa: E402
from pyawk.parser import parse  # noqa: E402

sys.path.insert(0, str(REPO / "awk_tests"))
import harness  # noqa: E402
import references  # noqa: E402


@dataclass
class Spec:
    """How to run one test: what our harness files will say.

    ``field(default_factory=list)`` gives every Spec its own fresh empty list
    (a plain ``= []`` default would be shared between all instances).
    """

    args: list[str] = field(default_factory=list)  # options before -f
    operands: list[str] = field(default_factory=list)  # after -f NAME.awk
    stdin_from_in: bool = False  # feed T.in on standard input
    stdin_text: str | None = None  # or feed this literal text
    copy_in: bool = False  # copy T.in as NAME.in (named in operands)
    note: str = ""  # anything worth saying in the MANIFEST


# Tests with a custom Makefile rule that we can replicate.  Paths like
# "$(srcdir)/x.in" become plain "x.in", since each case runs in its directory.
SPECIAL: dict[str, Spec] = {
    # gawk -f argcasfile.awk ARGC=1 ' /no/such/file' < argcasfile.in
    "argcasfile": Spec(operands=["ARGC=1", " /no/such/file"], stdin_from_in=True),
    # gawk -v INPUT=arynocls.in -f arynocls.awk
    "arynocls": Spec(args=["-v", "INPUT=arynocls.in"], copy_in=True),
    # gawk -f compare.awk 0 1 compare.in
    "compare": Spec(operands=["0", "1", "compare.in"], copy_in=True),
    # gawk -f exitval1.awk  (the exit status is always appended to the output)
    "exitval1": Spec(),
    # gawk -f fsspcoln.awk 'FS=[ :]+' fsspcoln.in
    "fsspcoln": Spec(operands=["FS=[ :]+", "fsspcoln.in"], copy_in=True),
    # gawk -f getline2.awk getline2.awk getline2.awk  (reads its own source)
    "getline2": Spec(operands=["getline2.awk", "getline2.awk"]),
    # Upstream runs two programs over getlnbuf.in; we import the first.
    "getlnbuf": Spec(operands=["getlnbuf.in"], copy_in=True, note="first of two programs only"),
    # gawk -f longwrds.awk -v SORT=sort < longwrds.in
    "longwrds": Spec(args=["-v", "SORT=sort"], stdin_from_in=True),
    # echo A B C D E | tr -d '\12\15' | gawk '{ print $NF }' - nors.in
    # (the program is given on the command line upstream; we write it to nors.awk)
    "nors": Spec(operands=["-", "nors.in"], stdin_text="A B C D E", copy_in=True),
    # gawk -f redfilnm.awk srcdir=. redfilnm.in
    "redfilnm": Spec(operands=["srcdir=.", "redfilnm.in"], copy_in=True),
    # LC_ALL=C gawk -f spacere.awk  (our harness always uses LC_ALL=C)
    "spacere": Spec(),
    # gawk -f tweakfld.awk tweakfld.in
    "tweakfld": Spec(operands=["tweakfld.in"], copy_in=True),
}
# Programs that upstream passes on the command line rather than in a file.
INLINE_PROGRAMS = {"nors": "{ print $NF }\n"}

# Tests with a custom Makefile rule we do not replicate, and why.
CUSTOM_SKIP: dict[str, str] = {
    "argarray": "custom rule: stdin pipe, '-' operand and directory-dependent ARGV",
    "awkpath": "custom rule: AWKPATH search (gawk extension)",
    "case-check": "custom rule: en_US.UTF-8 locale and alternative .ok files",
    "clobber": "custom rule: writes and compares a file",
    "eofsrc1": "custom rule: two -f program files",
    "messages": "custom rule: compares separate stdout, stderr and file outputs",
    "nofile": "custom rule: error message for a missing file",
    "posix-inf": "custom rule: en_US.UTF-8 locale and alternative .ok files",
    "rsnulbig": "custom rule: pipeline of three awk commands",
    "rsnulbig2": "custom rule: pipeline of three awk commands",
}

# Cases where the references' majority goes against a decision the user
# made (recorded in awk_tests/golden/DECISIONS and DESIGN.md).  These run
# with gawk's own .ok as the expected output, but only if mawk agrees with
# it, so the expected answer is still produced by two real awks.
DECIDED: dict[str, str] = {
    "close_status": (
        "user decision: close() of a command returns its exit status (mawk and gawk; "
        "onetrue-awk and gawk --posix return 0)"
    ),
}

# Cases where the vote is split by mawk and onetrue-awk bugs in POSIX
# bracket expressions, which pyawk resolves as gawk --posix does (the same
# decision as regex_corpus.OVERRIDES; see DESIGN.md "Regular expressions").
# They run with gawk's .ok, but only while gawk --posix reproduces it.
REGEX_POSIX: dict[str, str] = {
    "regrange": (
        "regex decision: collating symbols in ranges ([a-[.e.]], [[.c.]-[.z.]]) follow POSIX "
        "as gawk --posix does; mawk ignores [. .] and onetrue-awk only accepts it alone"
    ),
}

# Lines of gawk diagnostics inside a .ok file ("gawk: file:3: fatal: ...",
# "gawk: warning: ...") and the "EXIT CODE: 2" line the Makefile adds.
DIAG = re.compile(r"^(gawk: |EXIT CODE: )")


def makefile_lists(makefile: str) -> dict[str, list[str]]:
    """Every ``NAME = word word \\ <newline> word ...`` list in Makefile.am."""
    # Join backslash-continued lines first, so each list is on one line.
    text = makefile.replace("\\\n", " ")
    lists = {}
    for m in re.finditer(r"^([A-Z_0-9]+)\s*=(.*)$", text, re.M):
        lists[m.group(1)] = m.group(2).split()
    return lists


def makefile_targets(makefile: str) -> set[str]:
    """Names with an explicit ``name:`` rule (Gentests skips those)."""
    return set(re.findall(r"^([A-Za-z_][-A-Za-z0-9_.]*):", makefile, re.M))


@dataclass
class RefResult:
    """What one reference awk did with one program."""

    stdout: str
    stderr: str
    returncode: int
    new_files: list[str]  # files the program created in its directory


def run_ref(awk: list[str], name: str, case_dir: Path, spec: Spec, merge_stderr: bool = False) -> RefResult:
    """Run one awk on one case inside a throwaway copy of its files.

    The command and environment match harness.run_case.  With merge_stderr,
    stderr goes into stdout (as in gawk's Makefile) and ``stderr`` is empty.
    """
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        for f in case_dir.glob(f"{name}.*"):
            shutil.copy(f, work / f.name)
        before = set(os.listdir(work))
        env = dict(os.environ, LC_ALL="C")
        stdin_file = work / f"{name}.stdin"
        data = stdin_file.read_text(encoding="utf-8", errors="surrogateescape") if stdin_file.exists() else None
        try:
            proc = subprocess.run(
                [*awk, *spec.args, "-f", f"{name}.awk", *spec.operands],
                cwd=work, stdout=subprocess.PIPE, stderr=subprocess.STDOUT if merge_stderr else subprocess.PIPE,
                text=True, encoding="utf-8", errors="surrogateescape", timeout=20, env=env,
                input=data, stdin=subprocess.DEVNULL if data is None else None,
            )
        except subprocess.TimeoutExpired:
            return RefResult("", "timeout", -1, [])
        created = sorted(set(os.listdir(work)) - before)
        return RefResult(proc.stdout, proc.stderr or "", proc.returncode, created)


# Features we skip, as (regex, reason).  The first match wins.
SKIP_PATTERNS: list[tuple[str, str]] = [
    (r"\bgensub\s*\(", "gawk extension: gensub"),
    (r"\bpatsplit\s*\(", "gawk extension: patsplit"),
    (r"\basorti?\s*\(", "gawk extension: asort/asorti"),
    (r"\b(strtonum|and|or|xor|compl|lshift|rshift)\s*\(", "gawk extension: strtonum/bit functions"),
    (r"\b(isarray|typeof|mkbool)\s*\(", "gawk extension: isarray/typeof"),
    (r"\b(systime|strftime|mktime)\s*\(", "time-dependent: systime/strftime/mktime"),
    (r"\b(dcgettext|dcngettext|bindtextdomain)\s*\(|\bTEXTDOMAIN\b|(?<![\w$])_\"", "locale/i18n: gettext"),
    (r"\b(FPAT|FIELDWIDTHS)\b", "gawk extension: FPAT/FIELDWIDTHS"),
    (r"\bIGNORECASE\b", "gawk extension: IGNORECASE"),
    (r"\b(PROCINFO|SYMTAB|FUNCTAB|ARGIND|ERRNO|BINMODE|LINT)\b", "gawk extension: special variable"),
    (r"\bRT\b", "gawk extension: RT"),
    (r"\b(BEGINFILE|ENDFILE)\b", "gawk extension: BEGINFILE/ENDFILE"),
    (r"^\s*@(include|load|namespace)\b", "gawk extension: @include/@load/@namespace"),
    (r"@\w+\s*\(", "gawk extension: indirect function call"),
    (r"\|&", "gawk extension: coprocess |&"),
    (r"/inet[46]?/", "network: /inet special file"),
    (r"\b(rand|srand)\s*\(", "nondeterministic: rand/srand sequence is implementation-specific"),
    (r"\bsleep\b", "timing-dependent: sleep"),
]


def static_skip_reason(source: str) -> str | None:
    """A reason to skip this program based on its text alone, or None."""
    # Ask pyawk's own parser about deferred features (none at present).
    # Other syntax errors are not skips: if the references accept the
    # program, pyawk rejecting it is a bug we want to see.
    try:
        parse(source)
    except AwkUnsupportedError as e:
        m = re.search(r": (.+?) is not supported yet", str(e))
        return f"deferred: {m.group(1) if m else e}"
    except (AwkSyntaxError, RecursionError):
        pass
    # Comments could mention a feature without using it, so drop them first
    # (roughly: a "#" outside a string or regex runs to the end of the line).
    code = "\n".join(re.sub(r'^((?:[^#"/]|"(?:[^"\\]|\\.)*"|/(?:[^/\\\n]|\\.)*/)*)#.*$', r"\1", line)
                     for line in source.splitlines())
    for pattern, reason in SKIP_PATTERNS:
        if re.search(pattern, code, re.M):
            return reason
    return None


def failed(r: RefResult) -> bool:
    """True if the awk stopped with an error (nonzero exit plus a message)."""
    return r.returncode != 0 and r.stderr.strip() != ""


def strip_diagnostics(ok: str) -> str:
    """The .ok text without gawk messages, their source/caret lines and EXIT CODE."""
    out, after_diag = [], False
    for line in ok.splitlines(keepends=True):
        if DIAG.match(line):
            after_diag = True  # gawk may follow with the source line and a "^" marker
            continue
        if after_diag and (re.match(r"^\s*\^", line) or line.startswith("gawk: ")):
            continue
        after_diag = False
        out.append(line)
    return "".join(out)


def expected_from_ok(
    ok: str, exact: RefResult, exact_sep: RefResult, always_exit: bool
) -> tuple[str | None, bool, str]:
    """Work out the stdout part of gawk's .ok file.

    ``exact`` is gawk run the way the Makefile runs it (same flags, stderr
    merged into stdout); ``exact_sep`` is the same run with stderr kept apart.
    Returns (stdout or None if unknown, whether gawk stopped with an error, note).
    """
    combined = exact.stdout
    if exact.returncode != 0 or always_exit:
        combined += f"EXIT CODE: {exact.returncode}\n"
    if combined == ok:
        # Our gawk reproduces the .ok, so its separate stdout is the part we want.
        return exact_sep.stdout, failed(exact_sep), "local gawk reproduces .ok"
    if not any(DIAG.match(line) for line in ok.splitlines()):
        return ok, False, "local gawk differs from .ok; .ok has no diagnostics"
    return None, False, "local gawk differs from .ok, which mixes in diagnostics"


def pinned_expected(
    name: str, ok: str, refs: dict[str, RefResult], exact: RefResult, exact_sep: RefResult
) -> tuple[str, str] | None:
    """The expected stdout and note for a case pinned to gawk's .ok file.

    Returns None if ``name`` is in neither DECIDED nor REGEX_POSIX.  A pinned
    case takes its expected output from the local gawk (the stdout part of
    the .ok), so two things are checked first, and the import stops if
    either fails: the local gawk must really reproduce the .ok (another
    gawk build might not), and the reference awk named for the case must
    still agree with it.
    """
    if name in DECIDED:
        agreeing, label, why = "mawk", "mawk", DECIDED[name]
    elif name in REGEX_POSIX:
        agreeing, label, why = "gawk", "gawk --posix", REGEX_POSIX[name]
    else:
        return None
    note = expected_from_ok(ok, exact, exact_sep, name == "exitval1")[2]
    if note != "local gawk reproduces .ok":
        raise SystemExit(f"{name}: the local gawk does not reproduce gawk's .ok ({note})")
    ref = refs[agreeing]
    if ref.stdout != exact_sep.stdout or failed(ref):
        which = "DECIDED" if name in DECIDED else "REGEX_POSIX"
        raise SystemExit(f"{name}: {label} no longer matches gawk's .ok; review {which}")
    return exact_sep.stdout, f"expected: gawk .ok (agree: {label}); {why}"


def classify(source: str, ok: str, refs: dict[str, RefResult], exact: RefResult, exact_sep: RefResult,
             always_exit: bool) -> tuple[str, str, str, str | None]:
    """Decide (status, reason, refs-note, expected stdout) for one case."""
    # ``for (k in a)`` visits keys in an unspecified order, so compare sorted lines.
    unordered = re.search(r"\bfor\s*\(\s*\w+\s+in\s+", source) is not None

    def norm(stdout: str, err: bool) -> str:
        out = "".join(sorted(stdout.splitlines(keepends=True))) if unordered else stdout
        # An awk that stopped with an error is marked, so it only "agrees"
        # with another awk that also stopped with an error.
        return ("<error>" + out) if err else out

    keys = {k: norm(r.stdout, failed(r)) for k, r in refs.items()}
    errs = [k for k, r in refs.items() if failed(r)]
    ok_stdout, ok_err, ok_note = expected_from_ok(ok, exact, exact_sep, always_exit)
    note = ok_note + (("; error from: " + ",".join(errs)) if errs else "")
    status, reason = ("sorted", "for-in order is unspecified") if unordered else ("run", "")

    skip = static_skip_reason(source)
    if skip:
        return "skip", skip, note, None
    writers = sorted({f for r in refs.values() for f in r.new_files})
    if writers:
        return "skip", "writes files: " + " ".join(writers), note, None
    # Only diagnostics, not even an empty line of real output (lint tests
    # often print blank lines between their warnings, and those count).
    if ok and strip_diagnostics(ok) == "":
        return "skip", "error-message test: .ok is only gawk error text", note, None

    if ok_stdout is not None:
        ok_key = norm(ok_stdout, ok_err)
        agree = [k for k, v in keys.items() if v == ok_key]
        if len(agree) >= 2:
            if ok_err:
                reason = (reason + "; " if reason else "") + "program stops with an error; only stdout compared"
            return status, reason, f"expected: gawk .ok (agree: {','.join(agree)}); {note}", ok_stdout
    # The .ok is not backed by a majority: fall back to the references' majority.
    # ``Counter`` counts how many references produced each output.
    (best, votes), = Counter(keys.values()).most_common(1)
    if votes < 2:
        return "skip", "no majority among references", note, None
    voters = [k for k, v in keys.items() if v == best]
    if best.startswith("<error>"):
        if "gawk" in voters:
            return "skip", "rejected by gawk --posix and " + "/".join(v for v in voters if v != "gawk"), note, None
        return "skip", "references reject it: " + ",".join(voters), note, None
    # Use the raw (unsorted) output of one of the majority awks.
    chosen = "gawk" if "gawk" in voters else voters[0]
    return status, reason, f"expected: majority ({','.join(voters)}), not .ok; {note}", refs[chosen].stdout


def main() -> int:
    harness.tolerant_output()
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--src", type=Path, help="gawk's test/ directory (default: $PYAWK_REF_GAWK_TESTDIR)")
    ap.add_argument("--onetrue", help="onetrue-awk program (default: $PYAWK_REF_ONETRUE, else on PATH)")
    ap.add_argument("--mawk", help="mawk program (default: $PYAWK_REF_MAWK, else on PATH)")
    ap.add_argument("--gawk", help="gawk program, run with --posix (default: $PYAWK_REF_GAWK, else on PATH)")
    opts = ap.parse_args()
    # Find everything first: a missing awk or test directory must stop the
    # import before it clears awk_tests/gawk/.
    try:
        awks = references.commands(("onetrue", "mawk", "gawk"),
                                   explicit={"onetrue": opts.onetrue, "mawk": opts.mawk, "gawk": opts.gawk})
        opts.src = references.test_dir("gawk", opts.src)
    except references.MissingReference as exc:
        print(f"import_gawk: {exc}", file=sys.stderr)
        return 2

    makefile = (opts.src / "Makefile.am").read_text(encoding="utf-8")
    lists = makefile_lists(makefile)
    targets = makefile_targets(makefile)
    # ``dict.fromkeys`` keeps the first occurrence of each name, in order.
    names = list(dict.fromkeys(lists["BASIC_TESTS"] + lists["NEED_POSIX"]))

    def in_list(name: str, *list_names: str) -> bool:
        return any(name in lists.get(n, []) for n in list_names)

    known = DEST / "KNOWN_FAILURES"
    known_text = known.read_text(encoding="utf-8") if known.exists() else None
    if DEST.exists():
        shutil.rmtree(DEST)
    DEST.mkdir(parents=True)
    if known_text is not None:
        known.write_text(known_text, encoding="utf-8")

    rows = []
    for name in sorted(names):
        awk_src = opts.src / f"{name}.awk"
        in_src = opts.src / f"{name}.in"
        source = INLINE_PROGRAMS.get(name)
        if source is None and awk_src.exists():
            source = awk_src.read_text(encoding="utf-8", errors="surrogateescape")

        # --- skips decided by how upstream runs the test ---
        pre_skip = None
        if name in lists.get("RUN_SHELL", []) or (opts.src / f"{name}.sh").exists():
            pre_skip = f"shell-script test ({name}.sh)"
        elif name in targets and name not in SPECIAL:
            if name not in CUSTOM_SKIP:
                raise SystemExit(f"{name}: custom Makefile rule not reviewed; add it to SPECIAL or CUSTOM_SKIP")
            pre_skip = CUSTOM_SKIP[name]
        elif in_list(name, "NEED_LOCALE_EN", "NEED_LOCALE_GR", "NEED_LOCALE_JP", "NEED_LOCALE_RU"):
            pre_skip = "locale/i18n: needs a UTF-8 or national locale"
        elif in_list(name, "NEED_TRADITIONAL"):
            pre_skip = "gawk mode: needs --traditional"
        elif source is None:
            pre_skip = "no program file"
        if pre_skip:
            # Keep a placeholder so the manifest and the files line up.
            (DEST / f"{name}.awk").write_text(f"# not imported from gawk's test suite: {pre_skip}\n", encoding="utf-8")
            (DEST / f"{name}.operands").write_text("", encoding="utf-8")
            (DEST / f"{name}.out").write_text("", encoding="utf-8")
            rows.append((name, "skip", pre_skip, ""))
            print(f"skip   {name:<22} {pre_skip}")
            continue

        # --- write the case files ---
        spec = SPECIAL.get(name) or Spec(stdin_from_in=in_src.exists())
        (DEST / f"{name}.awk").write_text(source, encoding="utf-8", errors="surrogateescape")
        if spec.stdin_from_in:
            shutil.copy(in_src, DEST / f"{name}.stdin")
        if spec.stdin_text is not None:
            (DEST / f"{name}.stdin").write_text(spec.stdin_text, encoding="utf-8")
        if spec.copy_in:
            shutil.copy(in_src, DEST / f"{name}.in")
        (DEST / f"{name}.operands").write_text(shlex.join(spec.operands) + ("\n" if spec.operands else ""),
                                               encoding="utf-8")
        if spec.args:
            (DEST / f"{name}.args").write_text(shlex.join(spec.args) + "\n", encoding="utf-8")

        # --- run the references ---
        refs = {k: run_ref(cmd, name, DEST, spec) for k, cmd in awks.items()}
        # gawk exactly as its Makefile runs this test, to decode the .ok file.
        flags = []
        if in_list(name, "NEED_POSIX"):
            flags.append("--posix")
        if in_list(name, "NEED_LINT"):
            flags.append("--lint")
        exact = run_ref([awks["gawk"][0], *flags], name, DEST, spec, merge_stderr=True)
        exact_sep = run_ref([awks["gawk"][0], *flags], name, DEST, spec)
        ok_file = opts.src / f"{name}.ok"
        ok = ok_file.read_text(encoding="utf-8", errors="surrogateescape") if ok_file.exists() else ""
        status, reason, note, expected = classify(source, ok, refs, exact, exact_sep, name == "exitval1")
        pinned = pinned_expected(name, ok, refs, exact, exact_sep)
        if pinned is not None:
            status, reason = "run", ""
            expected, note = pinned
        if spec.note:
            note = spec.note + "; " + note
        (DEST / f"{name}.out").write_text(expected or "", encoding="utf-8", errors="surrogateescape")
        rows.append((name, status, reason, note))
        print(f"{status:<6} {name:<22} {reason}  [{note}]")

    with open(DEST / "MANIFEST.tsv", "w", encoding="utf-8") as f:
        f.write("# generated by scripts/import_gawk.py -- do not edit\n")
        f.write("name\tstatus\treason\trefs\n")
        for row in rows:
            f.write("\t".join(col.replace("\t", " ").replace("\n", " ") for col in row) + "\n")
    counts = Counter(status for _, status, _, _ in rows)
    print(f"\nimported {len(rows)} cases:", ", ".join(f"{k}={v}" for k, v in sorted(counts.items())))
    return 0


if __name__ == "__main__":
    sys.exit(main())
