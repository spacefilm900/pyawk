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
        # gawk: an invalid byte becomes U+FFFD (onetrue-awk stops instead).
        assert charset.upper("a\udce9") == "A\ufffd"
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
