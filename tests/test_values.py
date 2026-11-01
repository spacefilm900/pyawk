import math

from pyawk.values import (
    UNINIT,
    StrNum,
    compare,
    is_numeric_string,
    make_strnum,
    num_to_str,
    to_bool,
    to_num,
    to_str,
)


def test_numeric_string_detection() -> None:
    for s in ["1", " 1 ", "-1.5", "+.5", "1e3", "1E-3", "10.", "\t7\n"]:
        assert is_numeric_string(s), s
    for s in ["", " ", "abc", "1a", "0x1A", "1e", ".", "+", "inf", "nan", "1 2"]:
        assert not is_numeric_string(s), s


def test_make_strnum() -> None:
    v = make_strnum(" 10 ")
    assert isinstance(v, StrNum) and v.n == 10.0 and v.s == " 10 "
    assert make_strnum("abc") == "abc"
    assert make_strnum("") == ""


def test_only_ascii_digits_are_numeric() -> None:
    # Python's regex \d and str.isdigit() also accept other scripts' digits
    # ("\uff15" is a fullwidth 5); all three reference awks do not.
    assert make_strnum("\uff15") == "\uff15"
    assert make_strnum("1\uff15") == "1\uff15"
    assert to_num("\uff15") == 0.0
    assert to_num("1\uff15") == 1.0
    assert make_strnum("007") == StrNum("007", 7.0)


def test_to_num_prefix() -> None:
    assert to_num("3x") == 3.0
    assert to_num("  -2.5e1abc") == -25.0
    assert to_num("abc") == 0.0
    assert to_num(".") == 0.0
    assert to_num(UNINIT) == 0.0


def test_num_to_str() -> None:
    assert num_to_str(3.0, "%.6g") == "3"
    assert num_to_str(-0.0, "%.6g") == "0"
    assert num_to_str(0.1 + 0.2, "%.6g") == "0.3"
    assert num_to_str(1e30, "%.6g") == "1e+30"
    assert num_to_str(2.0**53, "%.6g") == "9007199254740992"
    assert num_to_str(math.inf, "%.6g") == "+inf"
    assert num_to_str(-math.inf, "%.6g") == "-inf"
    assert num_to_str(3.14159, "%.2f") == "3.14"


def test_to_str_and_bool() -> None:
    assert to_str(StrNum("1.0", 1.0)) == "1.0"
    assert to_bool(StrNum("0.0", 0.0)) is False
    assert to_bool("0") is True  # string constant "0" is a non-empty string
    assert to_bool(0.0) is False
    assert to_bool(UNINIT) is False


def test_compare_semantics() -> None:
    assert compare(2.0, 10.0) < 0
    assert compare("2", "10") > 0  # string constants compare as strings
    assert compare(StrNum("2", 2.0), StrNum("10", 10.0)) < 0  # strnums numerically
    assert compare(StrNum("10.0", 10.0), 10.0) == 0
    assert compare(StrNum("10.0", 10.0), "10") != 0  # string vs strnum -> string
    assert compare(UNINIT, 0.0) == 0 and compare(UNINIT, "") == 0
    assert compare("abc", 9.0) > 0
