"""Locale handling: C mode (bytes) and UTF-8 mode (characters).

The reference behaviour quoted in the comments was observed with
onetrue-awk, gawk --posix and mawk under LC_ALL=C and LC_ALL=C.UTF-8;
DESIGN.md ("Locales and UTF-8") lists the decisions.
"""

from __future__ import annotations

import pytest

from pyawk import charset


@pytest.fixture
def utf8():
    """Run the test in UTF-8 mode, and return to C mode afterwards."""
    charset.set_mode(True)
    yield
    charset.set_mode(False)


# ------------------------------------------------------------ detection
@pytest.mark.parametrize(
    "env, expected",
    [
        ({}, False),
        ({"LANG": "en_GB.UTF-8"}, True),
        ({"LANG": "C.utf8"}, True),
        ({"LANG": "de_DE.Utf-8"}, True),
        ({"LANG": "C"}, False),
        ({"LANG": "en_GB.ISO-8859-1"}, False),
        # LC_ALL beats LC_CTYPE beats LANG; empty means unset.
        ({"LC_ALL": "C", "LANG": "en_GB.UTF-8"}, False),
        ({"LC_ALL": "", "LC_CTYPE": "C.UTF-8", "LANG": "C"}, True),
        ({"LC_CTYPE": "POSIX", "LANG": "C.UTF-8"}, False),
        ({"LC_ALL": "en_US.UTF-8", "LC_CTYPE": "C"}, True),
    ],
)
def test_locale_detection(env, expected):
    assert charset.locale_is_utf8(env) is expected


# ------------------------------------------------------ the tables
def test_unicode_tables_are_sorted_and_disjoint():
    from pyawk.unicode_tables import CLASSES, LOWER, UPPER

    for name, ranges in CLASSES.items():
        for (a, b), (c, _) in zip(ranges, ranges[1:]):
            assert a <= b < c - 1, name  # sorted, not even touching
    for runs in (UPPER, LOWER):
        for first, last, step, _ in runs:
            assert (last - first) % step == 0


def test_unicode_tables_spot_checks():
    from pyawk.unicode_tables import CLASSES

    def member(name: str, ch: str) -> bool:
        return any(a <= ord(ch) <= b for a, b in CLASSES[name])

    assert member("alpha", "é") and member("alpha", "ж") and member("alpha", "中")
    assert not member("alpha", "1") and not member("alpha", "€")
    assert member("upper", "Ж") and member("lower", "ж")
    assert member("space", "\u3000") and member("blank", "\u3000")
    assert member("space", "\n") and member("cntrl", "\n")
    assert member("punct", "€") and member("print", "€")
    # glibc's digit and xdigit stay ASCII, as POSIX requires.
    assert not member("digit", "٣") and not member("xdigit", "Ａ")


def test_case_mapping():
    assert charset.upper("abc é") == "ABC é"  # C mode: ASCII only
    assert charset.lower("ÀBC") == "Àbc"
    charset.set_mode(True)
    try:
        assert charset.upper("straße é ж") == "STRAßE É Ж"  # ß has no 1-char upper case
        assert charset.lower("ÀÉЖ") == "àéж"
        assert charset.upper("ŉ") == "ŉ"
        # An invalid byte stays as it is (gawk makes it U+FFFD, onetrue-awk
        # stops with an error; see DESIGN.md).
        assert charset.upper("a\udce9") == "A\udce9"
    finally:
        charset.set_mode(False)


# ----------------------------------------------------------- bytes
def test_byte_char_and_join(utf8):
    é = charset.join_bytes(charset.byte_char(0o303) + charset.byte_char(0o251))
    assert é == "é"
    assert charset.join_bytes(charset.byte_char(0o351)) == "\udce9"  # invalid alone
    assert charset.byte_char(0o777) == "\udcff"  # only the low 8 bits count


def test_byte_char_c_mode():
    assert charset.byte_char(0o351) == "\xe9"
    assert charset.join_bytes("\xc3\xa9") == "\xc3\xa9"  # two characters


@pytest.mark.parametrize(
    "n, utf8_bytes, c_bytes",
    [
        (65, "41", "41"),
        (233, "c3a9", "e9"),
        (256, "c480", "00"),
        (9786, "e298ba", "3a"),
        (-1, "ff", "ff"),
        (0xD800, "00", "00"),  # a surrogate is no character: low byte (gawk)
        (0x110000, "f4908080", "00"),  # gawk; onetrue-awk prints U+FFFD
        (0x7FFFFFFF, "fdbfbfbfbfbf", "ff"),
        (2**31, "00", "00"),
    ],
)
def test_char_for_code(n, utf8_bytes, c_bytes):
    assert charset.to_os(charset.char_for_code(n)).hex() == c_bytes
    charset.set_mode(True)
    try:
        assert charset.to_os(charset.char_for_code(n)).hex() == utf8_bytes
    finally:
        charset.set_mode(False)


# ------------------------------------------------- whole programs
# These run pyawk as a separate process, the way a user would, so the
# locale variables, decoding of input and encoding of output are all
# exercised.  Programs and data are bytes written as Latin-1 strings
# ("\xc3\xa9" is the two bytes of é in UTF-8); "C" is the output expected
# in the C locale, "UTF-8" the one expected under LC_ALL=C.UTF-8.  Where
# the references disagree, the comment says whom we follow.

import os  # noqa: E402  (kept next to the tests that need it)
import subprocess  # noqa: E402
import sys  # noqa: E402
from pathlib import Path  # noqa: E402

REPO = Path(__file__).resolve().parent.parent


def pyawk(args: list[str], data: bytes = b"", locale: str = "C.UTF-8", cwd: str | None = None, env=None):
    """Run pyawk; ``args`` are Latin-1 strings standing for raw bytes."""
    extra = {k: os.fsdecode(v.encode("latin-1")) for k, v in (env or {}).items()}
    full_env = dict(os.environ, LC_ALL=locale, PYTHONPATH=str(REPO), **extra)
    argv = [sys.executable.encode(), b"-m", b"pyawk"] + [a.encode("latin-1") for a in args]
    return subprocess.run(argv, input=data, capture_output=True, env=full_env, cwd=cwd)


CASES = [
    # (program, input, output in UTF-8 mode, output in C mode)
    ("{ print length($0) }", b"h\xc3\xa9llo\n", b"5\n", b"6\n"),
    ("{ print substr($0, 2, 3) }", b"h\xc3\xa9llo\n", b"\xc3\xa9ll\n", b"\xc3\xa9l\n"),
    ('{ print index($0, "l") }', b"h\xc3\xa9llo\n", b"3\n", b"4\n"),
    ("{ print match($0, /l+/), RSTART, RLENGTH }", b"h\xc3\xa9llo\n", b"3 3 2\n", b"4 4 2\n"),
    ('{ n = split($0, a, ""); print n, a[2] }', b"h\xc3\xa9!\n", b"3 \xc3\xa9\n", b"4 \xc3\n"),
    (
        '{ printf "[%4s][%.1s][%-3s]\\n", $0, $0, "\xc3\xa9" }',
        b"\xc3\xa9a\n",
        b"[  \xc3\xa9a][\xc3\xa9][\xc3\xa9  ]\n",
        b"[ \xc3\xa9a][\xc3][\xc3\xa9 ]\n",
    ),
    ('BEGIN { printf "%c%c%c\\n", 233, "\xc3\xa9x", 9786 }', b"", b"\xc3\xa9\xc3\xa9\xe2\x98\xba\n", b"\xe9\xc3:\n"),
    (
        '{ print toupper($0), tolower("\xc3\x80B") }',
        b"\xc3\xa9a\xc3\x9f\n",
        b"\xc3\x89A\xc3\x9f \xc3\xa0b\n",
        b"\xc3\xa9A\xc3\x9f \xc3\x80b\n",
    ),
    # Invalid UTF-8: each bad byte is one character and comes out unchanged.
    ("{ print length($0); print }", b"a\xffb\n", b"3\na\xffb\n", b"3\na\xffb\n"),
    # gawk's substr turns the bad byte into U+FFFD (so gawk prints "3 0");
    # pyawk keeps the byte, as gawk's own split() and index() do.
    ('{ print length($0), substr($0, 2, 1) == "\\377" }', b"a\xff\xc3\xa9\n", b"3 1\n", b"4 1\n"),
    # Escapes for the two bytes of é make one character (gawk, onetrue-awk).
    ('BEGIN { print length("\\303\\251"), length("\\351"), "\\303\\251" }', b"", b"1 1 \xc3\xa9\n", b"2 1 \xc3\xa9\n"),
    ('BEGIN { print length("\\303" "\\251") }', b"", b"1\n", b"2\n"),
    ("{ print ($0 ~ /^.$/), ($0 ~ /^[^a]$/) }", b"\xc3\xa9\n", b"1 1\n", b"0 0\n"),
    # . and [^a] do not match an invalid byte (gawk; onetrue-awk says 1 1 1),
    # but the escape for that byte does.
    ("{ print ($0 ~ /^.$/), ($0 ~ /\\377/), ($0 ~ /^[^a]$/) }", b"\xff\n", b"0 1 0\n", b"1 1 1\n"),
    # Classes follow Unicode in UTF-8 mode (gawk; onetrue-awk is ASCII-only).
    (
        "{ print ($0 ~ /^[[:alpha:]]+$/), ($0 ~ /^[[:upper:]]/), ($0 ~ /[[:punct:]]/) }",
        b"\xc3\x89t\xc3\xa9\n",
        b"1 1 0\n",
        b"0 0 0\n",
    ),
    ("{ print match($0, /\\303\\251+/), RLENGTH }", b"x\xc3\xa9\xc3\xa9\n", b"2 2\n", b"2 2\n"),
    ("{ print match($0, /[\\303\\251]+/), RLENGTH }", b"x\xc3\xa9\xc3\xa9\n", b"2 2\n", b"2 4\n"),
    ("{ print match($0, /[\xc3\xa0-\xc3\xbf]+/), RLENGTH }", b"x\xc3\xa9\xc3\xa8y\n", b"2 2\n", b"2 4\n"),
    ("{ print ($0 ~ /^\xc3\xa9*$/) }", b"\xc3\xa9\xc3\xa9\n", b"1\n", b"0\n"),
    ('{ gsub(/./, "<&>"); print }', b"\xc3\xa9a\n", b"<\xc3\xa9><a>\n", b"<\xc3><\xa9><a>\n"),
    ('BEGIN { FS = "[\xc3\xa9]" } { print NF }', b"a\xc3\xa9b\xc3\xa9c\n", b"3\n", b"5\n"),
    ('BEGIN { FS = "\xc3\xa9" } { print NF, $2 }', b"a\xc3\xa9b\xc3\xa9c\n", b"3 b\n", b"3 b\n"),
    ('BEGIN { FS = "[^x]" } { print NF }', b"\xc3\xa9\xc3\xa9\n", b"3\n", b"5\n"),
    # The default FS splits at space, tab and newline only, not at other
    # Unicode spaces such as U+3000 or U+00A0 (all references).
    ("{ print NF }", b"a\xe3\x80\x80b \xc2\xa0c\n", b"2\n", b"2\n"),
    (
        'BEGIN { RS = "\xc3\xa9" } { print NR ": " $0 }',
        b"a\xc3\xa9b\xc3\xa9c",
        b"1: a\n2: b\n3: c\n",
        b"1: a\n2: b\n3: c\n",
    ),
    ('BEGIN { RS = "[\xc3\xa9]" } END { print NR }', b"a\xc3\xa9b", b"2\n", b"3\n"),
    # Texts over 2048 characters are searched by the automata, not by
    # Python's re: same answers.
    (
        "{ s = $0; while (length(s) < 3000) s = s s; print length(s), (s ~ /^[[:alpha:]]+$/), match(s, /a/) }",
        b"\xc3\xa9\n",
        b"4096 1 0\n",
        b"4096 0 0\n",
    ),
    ('{ s = $0; while (length(s) < 3000) s = s s; print gsub(/[^\\377]/, "", s), length(s) }', b"\xc3\xa9\xff\n",
     b"2048 2048\n", b"2048 1024\n"),
]


@pytest.mark.parametrize("prog, data, utf8_out, c_out", CASES)
def test_program_in_both_locales(prog, data, utf8_out, c_out):
    for locale, expected in (("C.UTF-8", utf8_out), ("C", c_out)):
        p = pyawk([prog], data, locale)
        assert (p.stdout, p.stderr) == (expected, b""), locale


def test_command_line_environment_and_files_in_utf8(tmp_path):
    name = "\xc3\xa9t\xc3\xa9.txt"  # été.txt, as bytes
    (tmp_path / os.fsdecode(name.encode("latin-1"))).write_bytes(b"\xc3\xa9\n")
    prog = '{ print FILENAME, length(FILENAME), length($0), length(v), length(ENVIRON["X"]) }'
    p = pyawk(["-v", "v=\xc3\xa9", prog, name], cwd=str(tmp_path), env={"X": "\xc3\xa9\xc3\xa9"})
    assert (p.stdout, p.stderr) == (b"\xc3\xa9t\xc3\xa9.txt 7 1 1 2\n", b"")
    p = pyawk(["-v", "v=\xc3\xa9", prog, name], cwd=str(tmp_path), env={"X": "\xc3\xa9\xc3\xa9"}, locale="C")
    assert (p.stdout, p.stderr) == (b"\xc3\xa9t\xc3\xa9.txt 9 2 2 4\n", b"")


@pytest.mark.parametrize("locale", ["C", "C.UTF-8"])
def test_output_files_and_commands_keep_their_bytes(tmp_path, locale):
    prog = (
        'BEGIN { print "x" > "\xc3\xb6.out"; close("\xc3\xb6.out");'
        ' print "y" | "cat > \xc3\xa4.out"; close("cat > \xc3\xa4.out");'
        ' system("echo z > \xc3\xbc.out") }'
    )
    p = pyawk([prog], locale=locale, cwd=str(tmp_path))
    assert p.returncode == 0, p.stderr
    names = sorted(os.fsencode(n) for n in os.listdir(tmp_path))
    assert names == sorted("\xc3\xb6.out \xc3\xa4.out \xc3\xbc.out".encode("latin-1").split())


def test_program_file_in_utf8(tmp_path):
    (tmp_path / "p.awk").write_bytes(b'BEGIN { print length("\xc3\xa9t\xc3\xa9") }\n')
    assert pyawk(["-f", str(tmp_path / "p.awk")]).stdout == b"3\n"
    assert pyawk(["-f", str(tmp_path / "p.awk")], locale="C").stdout == b"5\n"


@pytest.mark.parametrize("locale", ["C", "C.UTF-8"])
def test_non_ascii_names_are_syntax_errors(locale):
    # All three references reject a letter such as é in a variable name.
    p = pyawk(["BEGIN { \xc3\xa9 = 1 }"], locale=locale)
    assert p.returncode == 2 and b"syntax error" in p.stderr


def test_locale_variables_and_precedence():
    prog = 'BEGIN { print length("\xc3\xa9") }'
    assert pyawk([prog], env={"LC_CTYPE": "C"}, locale="").stdout == b"2\n"
    assert pyawk([prog], env={"LC_CTYPE": "", "LANG": "en_US.utf8"}, locale="").stdout == b"1\n"
    # LANG=C alone: Python itself sets LC_CTYPE=C.UTF-8 at startup (PEP 538);
    # pyawk undoes that, for itself and for the commands it runs.
    prog2 = 'BEGIN { print length("\xc3\xa9"); system("echo $LC_CTYPE") }'
    p = pyawk([prog2], env={"LC_CTYPE": "", "LANG": "C"}, locale="")
    assert p.stdout == b"2\n\n"


@pytest.mark.parametrize("locale", ["C", "C.UTF-8"])
def test_non_ascii_assignment_names_are_rejected(locale):
    # -v é=1: onetrue-awk, gawk and mawk all refuse it and stop.
    p = pyawk(["-v", "\xc3\xa9=1", 'BEGIN { print "ran" }'], locale=locale)
    assert p.returncode == 2 and p.stdout == b"" and b"improper assignment" in p.stderr


@pytest.mark.parametrize("locale", ["C", "C.UTF-8"])
def test_only_ascii_digits_count_in_printf_specs_and_intervals(locale):
    # "٣" is ARABIC-INDIC DIGIT THREE.  Python's str.isdigit() says it is a
    # digit; awk does not.  printf copies the bad spec as text (gawk), and
    # a{٣} is not an interval, so it matches the literal text "a{٣}".
    p = pyawk(['BEGIN { printf "%\xd9\xa3d|\\n", 5 }'], locale=locale)
    assert p.stdout == b"%\xd9\xa3d|\n"
    p = pyawk(["{ print ($0 ~ /^a{\xd9\xa3}$/), ($0 ~ /^a{3}$/) }"], b"aaa\n", locale)
    assert p.stdout == b"0 1\n"


def test_equivalence_class_holds_only_the_character_itself():
    # pyawk compares characters by code point, so [=é=] is just é.  gawk
    # uses glibc's equivalence classes (e also matches); onetrue-awk does
    # not match even é.  DESIGN.md lists this as a choice.
    p = pyawk(["{ print ($0 ~ /^[[=\xc3\xa9=]]$/) }"], b"\xc3\xa9\ne\n\xc3\x89\n")
    assert p.stdout == b"1\n0\n0\n"


# ------------------------------------- big sets in Python patterns
def test_big_sets_skip_the_long_range_list(utf8):
    # A Unicode class has hundreds of ranges above U+FFFF; its Python form
    # puts a one-range look-ahead in front of them (see _big_set_text).
    from pyawk import regex

    text = regex.compile_ere("[[:alpha:]]x").pattern
    assert "(?=[\\U00010000-\\U0010ffff])" in text
    assert "(?=" not in regex.compile_ere("[a-zé]x").pattern  # small sets: plain
    charset.set_mode(False)
    assert "(?=" not in regex.compile_ere("[[:alpha:]]x").pattern  # C locale: ASCII


@pytest.mark.parametrize(
    "ere", ["[[:alpha:]]", "[^[:alpha:]]", "[[:alpha:]]+", "^[^[:alnum:]]*$", "x[[:punct:][:digit:]]{2}y"]
)
def test_big_sets_match_the_same_characters(utf8, ere):
    # The split form must accept exactly what the tree's own set test
    # (and the automaton used for long texts) accepts.
    from pyawk import regex

    samples = ["a", "é", "ж", "中", "\U00020000", "\U0001f600", "\U00010400", " ", "1", "!", "\u3000",
               "\udcff", "x1!y", "x!!y", "\U00010FFF", "\U0010FFFF", ""]
    pat = regex.compile_ere(ere)
    long_pat = regex.for_long(pat)
    for s in samples:
        short = pat.search(s)
        long_ = long_pat.search(s)
        assert (short and short.span()) == (long_ and long_.span()), (ere, s)


def test_complement_covers_everything_else():
    from pyawk.regex import _complement

    assert _complement(((0, 9), (20, 29))) == ((10, 19), (30, 0x10FFFF))
    assert _complement(((0, 0x10FFFF),)) == ()
    assert _complement(((5, 9), (7, 12))) == ((0, 4), (13, 0x10FFFF))


def test_negated_big_set_of_everything_matches_nothing():
    import re

    from pyawk.regex import _big_set_text

    # 17 ranges above U+FFFF that together with 0..U+FFFF cover everything.
    step = (0x110000 - 0x10000) // 17
    ranges = ((0, 0xFFFF),) + tuple(
        (0x10000 + i * step, 0x10FFFF if i == 16 else 0x10000 + (i + 1) * step - 1) for i in range(17)
    )
    text = _big_set_text(ranges, negated=True)
    assert text is not None and not re.search(text, "a\U0001f600\U0010ffff")
    assert re.fullmatch(_big_set_text(ranges, negated=False), "\U0001f600")


def test_substitution_cache_is_keyed_by_pattern_text():
    # Hashing a compiled pattern rehashes its whole program, which is big
    # for Unicode classes; the cache uses the pattern's text instead.
    import re

    from pyawk import builtins

    builtins._NONEMPTY_CACHE.clear()
    assert builtins.substitute(re.compile("a+"), "x", "baab", True) == ("bxb", 1)
    assert list(builtins._NONEMPTY_CACHE) == [("a+", re.compile("a+").flags)]

