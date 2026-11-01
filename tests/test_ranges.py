"""Range patterns ``start, end { action }`` at run time.

Every expected output below was checked against all three reference awks
(onetrue-awk, gawk --posix and mawk), which agree on all of these cases.
"""

from pathlib import Path

from test_runtime import awk

# Input used by most tests: "start" opens a range and "end" closes it.
# Line 6 contains both words, so a range opens and closes on that one line.
DATA = "a\nstart\nx\nend\ny\nstart end\nz\nstart\nw\n"


def lines(*nums: int) -> str:
    """The DATA records with the given (1-based) record numbers."""
    recs = DATA.splitlines()
    return "".join(recs[n - 1] + "\n" for n in nums)


def test_regex_range_without_action_prints() -> None:
    # 2-4 is a normal range; 6 opens and closes on the same record; 8 opens
    # a range whose end never comes, so it runs to the end of input.
    assert awk("/start/, /end/", DATA) == (lines(2, 3, 4, 6, 8, 9), 0)


def test_expression_endpoints() -> None:
    assert awk("NR == 2, NR == 4 { print NR }", DATA) == ("2\n3\n4\n", 0)


def test_end_never_matches_runs_to_eof() -> None:
    assert awk("NR == 2, /never/", DATA) == (lines(2, 3, 4, 5, 6, 7, 8, 9), 0)


def test_range_restarts_after_it_ends() -> None:
    prog = '$0 == "start", $0 == "end" { print NR }'
    assert awk(prog, DATA) == ("2\n3\n4\n8\n9\n", 0)


def test_each_rule_has_its_own_range_state() -> None:
    prog = '/start/, /end/ { print "A" NR } /x/, /y/ { print "B" NR }'
    assert awk(prog, DATA)[0].split() == ["A2", "A3", "B3", "A4", "B4", "B5", "A6", "A8", "A9"]


def test_identical_range_rules_are_independent() -> None:
    # Two textually identical rules must not share state.
    assert awk("NR == 2, NR == 3 { print 1 } NR == 2, NR == 3 { print 2 }", DATA)[0] == "1\n2\n1\n2\n"


def test_start_tested_only_while_closed_end_only_while_open() -> None:
    # s counts evaluations of the start condition, e of the end condition.
    # Records 1-3 are one range (start tested on 1, end on 1, 2, 3), then
    # start is tested on 4 and the range runs to the end (end on 4, 5, 6).
    prog = "(s++ >= 0 && $1 % 3 == 1), (e++ >= 0 && $1 % 3 == 0) { print $1 } END { print s, e }"
    assert awk(prog, "1\n2\n3\n4\n5\n6\n")[0] == "1\n2\n3\n4\n5\n6\n2 6\n"


def test_next_inside_range_action() -> None:
    # The end condition is checked *before* the action runs, so ``next`` in
    # the action does not stop the range from closing on its end record.
    prog = '/start/, /end/ { print "in", NR; next } { print "out", NR }'
    out = awk(prog, DATA)[0].splitlines()
    assert out == ["out 1", "in 2", "in 3", "in 4", "out 5", "in 6", "out 7", "in 8", "in 9"]


def test_next_in_earlier_rule_skips_range_evaluation() -> None:
    # Record 3 never reaches the range rule, so its end condition is not
    # tested there; the range stays open and still closes on record 4.
    assert awk('NR == 3 { next } /start/, /end/ { print "r", NR }', DATA)[0].split("\n")[:-1] == [
        "r 2", "r 4", "r 6", "r 8", "r 9",
    ]


def test_next_skipping_the_end_record_keeps_range_open() -> None:
    # Record 4 ("end") never reaches the range rule, so the range stays
    # open through record 5 and closes on record 6 ("start end").
    assert awk("NR == 4 { next } /start/, /end/ { print NR }", DATA)[0] == "2\n3\n5\n6\n8\n9\n"


def test_getline_inside_range_action() -> None:
    # getline consumes the following record, which is then never tested
    # against the range's end: record 4 ("end") is swallowed by the getline
    # on record 3, so the range stays open.
    prog = '/start/, /end/ { getline; print "g", NR, $0 }'
    assert awk(prog, DATA)[0].splitlines() == ["g 3 x", "g 5 y", "g 7 z", "g 9 w"]


def test_getline_in_end_condition() -> None:
    assert awk('/2/, (getline > 0) { print "r", $0 }', "1\n2\n3\n4\n5\n6\n")[0] == "r 3\n"


def test_exit_inside_range() -> None:
    assert awk('NR == 2, NR == 4 { print; if (NR == 3) exit } END { print "end" }', "1\n2\n3\n4\n5\n") == (
        "2\n3\nend\n", 0)


def test_comma_binds_loosest_at_run_time() -> None:
    assert awk('$1 == "a" || $1 == "z", /x/', DATA)[0] == lines(1, 2, 3, 7, 8, 9)


def test_newline_after_comma() -> None:
    assert awk("/start/,\n/end/", DATA)[0] == lines(2, 3, 4, 6, 8, 9)


def test_range_state_persists_across_input_files(tmp_path: Path) -> None:
    f1 = tmp_path / "f1"
    f2 = tmp_path / "f2"
    f1.write_text("a\nb\nc\nd\n")
    f2.write_text("e\nf\ng\n")
    out, _ = awk("FNR == 3, /f/ { print FNR, $0 }", "", str(f1), str(f2))
    assert out == "3 c\n4 d\n1 e\n2 f\n3 g\n"
