"""Characters versus bytes: the locale mode pyawk runs in.

AWK's string functions count *characters*.  What a character is depends
on the locale, read from the environment as POSIX describes: the first
of LC_ALL, LC_CTYPE and LANG that is set and not empty decides.

* **UTF-8 mode** -- the locale name contains "UTF-8" or "utf8" (in any
  letter case), e.g. ``LANG=en_GB.UTF-8``.  Input is decoded as UTF-8, so
  "é" (two bytes) is one character for length(), substr(), index(),
  match(), split(), printf widths, FS, RS and the regex ``.``; character
  classes such as ``[[:alpha:]]`` and toupper/tolower cover all of
  Unicode (tables taken from gawk, see unicode_tables.py).

* **C mode** -- anything else, including no locale at all.  Every byte
  is one character, classes are ASCII-only, and toupper/tolower change
  only a-z and A-Z: the classic byte-exact behaviour.

How the bytes are kept intact.  Python strings hold characters, so bytes
from files must be decoded on the way in and encoded on the way out.

* In C mode the codec is Latin-1, which maps byte N to the character
  with code N and back: every byte sequence round-trips exactly.
* In UTF-8 mode the codec is UTF-8 with Python's "surrogateescape" error
  handler.  A byte that is not part of valid UTF-8 becomes one of the
  otherwise unused code points U+DC80..U+DCFF ("an escaped byte"), and is
  turned back into the same byte on output.  So invalid input never
  crashes pyawk and is written back unchanged.  Each such byte counts as
  one character, as in gawk and onetrue-awk.

The mode is a process-wide setting, chosen once per run by ``set_mode``
(cli.run calls it).  Other modules read ``charset.UTF8`` and friends at
the moment they need them, never copying them at import time.
"""

from __future__ import annotations

import os
from typing import Callable, Mapping

# The current mode.  These are module globals, changed only by set_mode().
UTF8 = False
ENCODING = "latin-1"  # the codec for all input and output
ERRORS = "surrogateescape"  # how undecodable bytes are kept (UTF-8 mode)

# Escaped bytes (see the module docstring) live in this range.
ESCAPED_LO, ESCAPED_HI = 0xDC80, 0xDCFF

# Functions to call when the mode changes, e.g. to empty caches of
# compiled regexes (a ``.`` compiled in one mode is wrong in the other).
_on_change: list[Callable[[], None]] = []


def locale_is_utf8(environ: Mapping[str, str] | None = None) -> bool:
    """True when the locale named by the environment uses UTF-8.

    POSIX order: LC_ALL overrides LC_CTYPE, which overrides LANG; an
    empty variable counts as unset.
    """
    env = os.environ if environ is None else environ
    for name in ("LC_ALL", "LC_CTYPE", "LANG"):
        value = env.get(name, "")
        if value:
            v = value.lower()
            return "utf-8" in v or "utf8" in v
    return False


def undo_python_locale_coercion() -> None:
    """Put back LC_CTYPE as the user set it, if Python changed it.

    When the locale is "C" (e.g. ``LANG=C`` with LC_ALL unset), Python
    "coerces" it at startup: it sets LC_CTYPE=C.UTF-8 in its own
    environment (PEP 538).  pyawk would then wrongly see a UTF-8 locale,
    and so would the commands it starts.  On Linux the environment the
    process was started with is still readable in /proc/self/environ, so
    we restore LC_CTYPE from there.  (Elsewhere nothing is done; setting
    LC_ALL avoids the coercion everywhere.)
    """
    try:
        with open("/proc/self/environ", "rb") as f:
            started_with = f.read().split(b"\0")
    except OSError:
        return
    original = None
    for item in started_with:
        if item.startswith(b"LC_CTYPE="):
            original = item[len(b"LC_CTYPE=") :]
    if os.environb.get(b"LC_CTYPE") != original:
        if original is None:
            del os.environb[b"LC_CTYPE"]
        else:
            os.environb[b"LC_CTYPE"] = original


def set_mode(utf8: bool) -> None:
    """Switch between UTF-8 mode (True) and C mode (False)."""
    global UTF8, ENCODING
    if utf8 == UTF8:
        return
    UTF8 = utf8
    ENCODING = "utf-8" if utf8 else "latin-1"
    for hook in _on_change:
        hook()


def on_change(hook: Callable[[], None]) -> None:
    """Register ``hook`` to be called whenever set_mode changes the mode."""
    _on_change.append(hook)


# ---------------------------------------------------------- outside world
# Python hands us the command line and the environment already decoded,
# using its own idea of the encoding (normally UTF-8 with surrogateescape).
# os.fsencode() recovers the original bytes, which we then decode the way
# the current mode says, so "é" on the command line is the same character
# as "é" read from a file.


def from_os(s: str) -> str:
    """A command-line argument or environment string, in pyawk's terms."""
    if s.isascii():  # the common case: nothing to convert
        return s
    return os.fsencode(s).decode(ENCODING, ERRORS)


def to_os(s: str) -> bytes:
    """A string as bytes for the operating system (file names, commands)."""
    return s.encode(ENCODING, ERRORS)


def decode(data: bytes) -> str:
    """Bytes read from a file (e.g. a -f program file) -> text."""
    return data.decode(ENCODING, ERRORS)


# ------------------------------------------------- bytes from escapes
def byte_char(b: int) -> str:
    """The character standing for byte ``b`` (from an escape such as \\351).

    Only the low 8 bits count, so "\\777" is byte 0xFF as in all three
    reference awks.  In UTF-8 mode a byte of 0x80 or more on its own is
    not a character, so it is represented as an escaped byte; join_bytes()
    then merges a valid sequence of them into the character it encodes.
    """
    b &= 0xFF
    if UTF8 and b >= 0x80:
        return chr(0xDC00 + b)
    return chr(b)


def join_bytes(s: str) -> str:
    """Merge escaped bytes in ``s`` that form valid UTF-8 into characters.

    "\\303\\251" in a program is the two bytes of "é"; awks treat it as
    the single character é.  Encoding back to bytes and decoding again does
    exactly that, leaving any invalid bytes escaped.  (C mode: no change.)
    """
    if not UTF8:
        return s
    return s.encode("utf-8", ERRORS).decode("utf-8", ERRORS)


# ------------------------------------------------------- case mapping
# str.translate(table) replaces each character found in the dict ``table``
# (code -> code) and leaves the others alone.
_ASCII_UPPER = {c: c - 32 for c in range(ord("a"), ord("z") + 1)}
_ASCII_LOWER = {c: c + 32 for c in range(ord("A"), ord("Z") + 1)}
_unicode_tables: tuple[dict[int, int], dict[int, int]] | None = None


def _unicode_case() -> tuple[dict[int, int], dict[int, int]]:
    """The UTF-8 mode toupper/tolower tables, built on first use."""
    global _unicode_tables
    if _unicode_tables is None:
        from .unicode_tables import LOWER, UPPER

        def expand(runs: tuple[tuple[int, int, int, int], ...]) -> dict[int, int]:
            # Escaped (invalid) bytes are not in the tables, so they pass
            # through unchanged.  gawk turns them into U+FFFD here (but not
            # in most other functions) and onetrue-awk stops with an error;
            # keeping the bytes is consistent with the rest of pyawk.  See
            # DESIGN.md, "Locales and UTF-8".
            return {c: c + delta for first, last, step, delta in runs for c in range(first, last + 1, step)}

        _unicode_tables = (expand(UPPER), expand(LOWER))
    return _unicode_tables


def upper(s: str) -> str:
    """toupper(): ASCII letters in C mode, all of Unicode in UTF-8 mode."""
    if s.isascii():
        return s.upper()  # fast, and the same answer in both modes
    return s.translate(_unicode_case()[0] if UTF8 else _ASCII_UPPER)


def lower(s: str) -> str:
    """tolower(): the mirror image of upper()."""
    if s.isascii():
        return s.lower()
    return s.translate(_unicode_case()[1] if UTF8 else _ASCII_LOWER)


# ------------------------------------------------------------ printf %c
def char_for_code(n: int) -> str:
    """What printf "%c" prints for the number ``n``.

    C mode: the byte ``n`` modulo 256 (gawk, mawk; -1 is byte 0xFF).
    UTF-8 mode, following gawk (which asks the C library's wcrtomb):
    a valid code point gives that character; 0x110000..0x7FFFFFFF give
    the old 5- and 6-byte style UTF-8 encoding that glibc still writes
    (onetrue-awk prints U+FFFD instead); anything else -- negative
    numbers, surrogates, larger values -- falls back to the low byte.
    """
    if not UTF8:
        return chr(n & 0xFF)
    if 0 <= n < 0x110000 and not 0xD800 <= n <= 0xDFFF:
        return chr(n)
    if 0x110000 <= n <= 0x7FFFFFFF:
        # Extended UTF-8: a lead byte announcing k continuation bytes,
        # each continuation byte carrying 6 bits of the number.
        k = 3 if n < 0x200000 else 4 if n < 0x4000000 else 5
        lead = (0xF0, 0xF8, 0xFC)[k - 3]
        out = [lead | (n >> (6 * k))]
        out += [0x80 | ((n >> (6 * i)) & 0x3F) for i in range(k - 1, -1, -1)]
        return "".join(chr(0xDC00 + b) for b in out)
    return byte_char(n)
