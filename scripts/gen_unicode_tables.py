#!/usr/bin/env python3
"""Generate pyawk/unicode_tables.py from gawk running under C.UTF-8.

usage: scripts/gen_unicode_tables.py [--gawk PATH] [--check]

The gawk used is the one on PATH unless --gawk names another.  The tables
depend on the gawk and C library versions (the module records gawk's), so
--check passes only with the same ones; the committed module came from
GNU Awk 5.4.1 with glibc's C.UTF-8 locale.

In a UTF-8 locale pyawk's character classes (``[[:alpha:]]`` and the rest)
and toupper/tolower follow gawk, which takes them from the C library's
locale tables.  Python's own ``str.isalpha()`` and ``str.upper()`` follow
a different Unicode version and different rules (for example Python calls
many symbols "printable" that glibc does not), so instead of guessing we
ask gawk about every code point and store its answers.

How: write every Unicode scalar value (except newline and the surrogates,
which are not characters) one per line, then have gawk report, for each
line, which classes it matches and what toupper/tolower make of it.

The result is a plain Python module of range tuples; ``--check`` compares
a fresh run with the committed module instead of writing it.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "pyawk" / "unicode_tables.py"
CLASSES = ["alpha", "digit", "alnum", "upper", "lower", "space", "blank", "punct", "xdigit", "cntrl", "print", "graph"]

# Every code point we ask about: all scalar values except "\n" (it ends a
# line) and the surrogates U+D800..U+DFFF (not valid in UTF-8).  gawk sees
# "\n" only as a record separator, so its classes are filled in by hand
# below: it is a space and a control character, as in the C locale.
CODE_POINTS = [c for c in range(0x110000) if c != 10 and not 0xD800 <= c <= 0xDFFF]

GAWK_PROGRAM = r"""
{
    # One line of flags per input line: a letter for each class matched.
    f = ""
    if ($0 ~ /^[[:alpha:]]$/)  f = f "a"
    if ($0 ~ /^[[:digit:]]$/)  f = f "d"
    if ($0 ~ /^[[:alnum:]]$/)  f = f "n"
    if ($0 ~ /^[[:upper:]]$/)  f = f "u"
    if ($0 ~ /^[[:lower:]]$/)  f = f "l"
    if ($0 ~ /^[[:space:]]$/)  f = f "s"
    if ($0 ~ /^[[:blank:]]$/)  f = f "b"
    if ($0 ~ /^[[:punct:]]$/)  f = f "p"
    if ($0 ~ /^[[:xdigit:]]$/) f = f "x"
    if ($0 ~ /^[[:cntrl:]]$/)  f = f "c"
    if ($0 ~ /^[[:print:]]$/)  f = f "r"
    if ($0 ~ /^[[:graph:]]$/)  f = f "g"
    print f > FLAGS
    print toupper($0) > UPPER
    print tolower($0) > LOWER
}
"""
LETTERS = dict(zip("adnulsbpxcrg", CLASSES))


def ranges(cps: list[int]) -> list[tuple[int, int]]:
    """Sorted code points -> (first, last) runs of consecutive values."""
    out: list[tuple[int, int]] = []
    for c in cps:
        if out and c == out[-1][1] + 1:
            out[-1] = (out[-1][0], c)
        else:
            out.append((c, c))
    return out


def case_runs(mapping: dict[int, int]) -> list[tuple[int, int, int, int]]:
    """Compress a code-point mapping into (first, last, step, delta) runs.

    A run covers first, first+step, ..., last, each mapping to itself plus
    delta.  Most of Unicode's case pairs come in such runs (step 1 for
    blocks like A-Z, step 2 for the alternating Latin Extended-A letters).
    """
    out: list[list[int]] = []
    for c in sorted(mapping):
        d = mapping[c] - c
        if out:
            first, last, step, delta = out[-1]
            if delta == d and (c - last == step or (first == last and c - last <= 2)):
                out[-1] = [first, c, c - last if first == last else step, d]
                continue
        out.append([c, c, 1, d])
    return [tuple(r) for r in out]  # type: ignore[misc]


def ask_gawk(gawk: str) -> tuple[dict[str, list[int]], dict[int, int], dict[int, int], str]:
    with tempfile.TemporaryDirectory() as tmp:
        t = Path(tmp)
        (t / "in").write_bytes(b"".join(chr(c).encode("utf-8") + b"\n" for c in CODE_POINTS))
        env = dict(os.environ, LC_ALL="C.UTF-8")
        outputs = [f"-v{name}={t}/{name.lower()}" for name in ("FLAGS", "UPPER", "LOWER")]
        cmd = [gawk, *outputs, GAWK_PROGRAM, str(t / "in")]
        subprocess.run(cmd, env=env, check=True)
        flags = (t / "flags").read_bytes().split(b"\n")[:-1]
        upper = (t / "upper").read_bytes().split(b"\n")[:-1]
        lower = (t / "lower").read_bytes().split(b"\n")[:-1]
        version = subprocess.run(
            [gawk, "--version"], capture_output=True, encoding="utf-8", errors="replace"
        ).stdout.splitlines()[0]
    assert len(flags) == len(upper) == len(lower) == len(CODE_POINTS), "gawk output out of step"
    members: dict[str, list[int]] = {name: [] for name in CLASSES}
    for c, f in zip(CODE_POINTS, flags):
        for letter in f.decode():
            members[LETTERS[letter]].append(c)
    # "\n" (left out above) is a space and a control character.
    for name in ("space", "cntrl"):
        members[name] = sorted(members[name] + [10])

    def mapping(lines: list[bytes]) -> dict[int, int]:
        out = {}
        for c, line in zip(CODE_POINTS, lines):
            s = line.decode("utf-8")
            assert len(s) == 1, f"U+{c:04X} maps to {len(s)} characters"
            if ord(s) != c:
                out[c] = ord(s)
        return out

    return members, mapping(upper), mapping(lower), version


def render(members, upper, lower, version: str) -> str:
    lines = [
        '"""Unicode character classes and case mappings for UTF-8 locales.',
        "",
        "GENERATED by scripts/gen_unicode_tables.py -- do not edit by hand.",
        f"Source: {version}, LC_ALL=C.UTF-8 (so, in effect, the C library's tables).",
        "",
        "CLASSES maps a POSIX class name to sorted, non-overlapping",
        "(first, last) code-point ranges.  UPPER and LOWER hold the case",
        "mappings as (first, last, step, delta) runs: each code point",
        "first, first+step, ..., last maps to itself plus delta.",
        '"""',
        "",
        "CLASSES = {",
    ]
    for name in CLASSES:
        rs = ranges(members[name])
        lines.append(f"    {name!r}: (")
        for i in range(0, len(rs), 5):
            lines.append("        " + " ".join(f"(0x{a:X}, 0x{b:X})," for a, b in rs[i : i + 5]))
        lines.append("    ),")
    lines.append("}")
    for title, mp in (("UPPER", upper), ("LOWER", lower)):
        lines.append("")
        lines.append(f"{title} = (")
        runs = case_runs(mp)
        for i in range(0, len(runs), 3):
            lines.append("    " + " ".join(f"(0x{a:X}, 0x{b:X}, {s}, {d})," for a, b, s, d in runs[i : i + 3]))
        lines.append(")")
    return "\n".join(lines) + "\n"


def default_gawk() -> str:
    """The gawk found on PATH (or just "gawk", to fail with a clear message)."""
    return shutil.which("gawk") or "gawk"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--gawk", default=None, help="the gawk to ask (default: gawk on PATH)")
    ap.add_argument("--check", action="store_true", help="compare with the committed module, do not write")
    args = ap.parse_args(argv)
    gawk = args.gawk or default_gawk()
    try:
        text = render(*ask_gawk(gawk))
    except (OSError, subprocess.CalledProcessError) as exc:
        # No such program, not executable, or gawk itself failed.
        print(f"gen_unicode_tables: cannot run {gawk}: {exc}", file=sys.stderr)
        return 2
    if args.check:
        same = OUT.exists() and OUT.read_text(encoding="utf-8") == text
        print("unicode_tables.py is up to date" if same else "unicode_tables.py differs from a fresh run")
        return 0 if same else 1
    # Written and compared as UTF-8 whatever the locale, so --check and
    # the write agree everywhere (the module is ASCII unless gawk's
    # version line is not).
    OUT.write_text(text, encoding="utf-8")
    print(f"wrote {OUT.relative_to(ROOT)} ({len(text)} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
