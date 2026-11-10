#!/usr/bin/env bash
# Run every local test suite and print a one-line-per-suite pass/fail summary.
#
# The suites are the same as `scripts/regress.sh thorough` (and CI's thorough
# job): ruff, pytest, the golden/imported corpus runner, and the C and UTF-8
# regex differential checks. Unlike regress.sh, this script runs every suite
# even if an earlier one fails, then prints a summary. The exit status is 0
# only if every suite passed.
#
# This script never writes to the test trees. Do not add flags such as
# --write-known-failures, or commands that regenerate golden files.
#
# usage: scripts/check.sh
set -uo pipefail

ROOT=$(cd "$(dirname "$0")/.." && pwd)
cd "$ROOT"

if [[ -x .venv/bin/python ]]; then
  PYTHON=.venv/bin/python
else
  PYTHON=${PYTHON:-python3}
fi

MIN_PASSED=${MIN_PASSED:-1900}

LOGDIR=$(mktemp -d "${TMPDIR:-/tmp}/pyawk-check.XXXXXX")
trap 'rm -rf "$LOGDIR"' EXIT

# Each result is "name|STATUS|detail"
RESULTS=()
FAILED=0

pass() { RESULTS+=("$1|PASS|$2"); }
fail() { RESULTS+=("$1|FAIL|$2"); FAILED=1; }

run_logged() {
  # run_logged LOGFILE command...
  local log=$1
  shift
  "$@" >"$log" 2>&1
  return $?
}

echo "pyawk check: logs in $LOGDIR"
echo

echo "==> ruff"
if run_logged "$LOGDIR/ruff.log" "$PYTHON" -m ruff check .; then
  pass "ruff" "clean"
else
  fail "ruff" "see $LOGDIR/ruff.log"
  sed -n '1,20p' "$LOGDIR/ruff.log"
fi

echo "==> pytest"
if run_logged "$LOGDIR/pytest.log" "$PYTHON" -m pytest -q --tb=short; then
  summary=$(grep -E '[0-9]+ passed' "$LOGDIR/pytest.log" | tail -1)
  passed=$(printf '%s\n' "$summary" | awk '{
    for (i=1;i<=NF;i++) if ($i=="passed," || $i=="passed") print $(i-1)
  }' | tail -1)
  if [[ -z "${passed}" ]]; then
    fail "pytest" "could not parse passed count"
  elif (( passed < MIN_PASSED )); then
    fail "pytest" "only ${passed} passed (floor ${MIN_PASSED})"
  else
    pass "pytest" "${summary} (floor ${MIN_PASSED})"
  fi
else
  summary=$(grep -E '[0-9]+ (passed|failed)' "$LOGDIR/pytest.log" | tail -1)
  fail "pytest" "${summary:-failed} (see $LOGDIR/pytest.log)"
  tail -30 "$LOGDIR/pytest.log"
fi

echo "==> run_golden.py"
if run_logged "$LOGDIR/golden.log" "$PYTHON" scripts/run_golden.py; then
  golden_ok=1
else
  golden_ok=0
  echo "run_golden.py failed; last lines:"
  tail -30 "$LOGDIR/golden.log"
fi
# Parse the per-category table regardless of exit status, so the summary
# can still name golden / onetrue / gawk separately.
eval "$(awk '
  $1=="onetrue" { o_p=$2; o_f=$3; o_s=$4 }
  $1=="gawk"    { g_p=$2; g_f=$3; g_s=$4 }
  $1=="TOTAL"   { t_p=$2; t_f=$3; t_s=$4 }
  END {
    own_p = t_p - o_p - g_p
    own_f = t_f - o_f - g_f
    own_s = t_s - o_s - g_s
    printf "own_p=%d; own_f=%d; own_s=%d; o_p=%d; o_f=%d; o_s=%d; g_p=%d; g_f=%d; g_s=%d; t_p=%d; t_f=%d; t_s=%d\n",
      own_p+0, own_f+0, own_s+0, o_p+0, o_f+0, o_s+0, g_p+0, g_f+0, g_s+0, t_p+0, t_f+0, t_s+0
  }
' "$LOGDIR/golden.log")"
# A suite fails if the runner failed or that group had failures.
report_corpus() {
  local name=$1 p=$2 f=$3 s=$4
  local detail="${p} passed, ${f} failed, ${s} skipped"
  if [[ $golden_ok -eq 1 && $f -eq 0 ]]; then
    pass "$name" "$detail"
  else
    fail "$name" "$detail"
  fi
}
report_corpus "golden corpus" "$own_p" "$own_f" "$own_s"
report_corpus "onetrue suite" "$o_p" "$o_f" "$o_s"
report_corpus "gawk suite" "$g_p" "$g_f" "$g_s"

parse_regex() {
  # Sets r_pats r_badp r_lines r_bad r_split from a regex_fuzz total line.
  local log=$1
  local line
  line=$(awk '$1=="total" { print; exit }' "$log")
  r_pats=$(awk '{print $2}' <<<"$line")
  r_badp=$(awk '{print $3}' <<<"$line")
  r_lines=$(awk '{print $4}' <<<"$line")
  r_bad=$(awk '{print $5}' <<<"$line")
  r_split=$(awk '{print $6}' <<<"$line")
}

echo "==> regex_fuzz.py --check"
if run_logged "$LOGDIR/regex-c.log" "$PYTHON" scripts/regex_fuzz.py --check; then
  regex_c_ok=1
else
  regex_c_ok=0
  tail -20 "$LOGDIR/regex-c.log"
fi
parse_regex "$LOGDIR/regex-c.log"
detail="${r_lines} lines agree, ${r_bad} mismatch (${r_pats} patterns, ${r_split} split/untested)"
if [[ $regex_c_ok -eq 1 && ${r_bad:-1} -eq 0 ]]; then
  pass "regex differential (C)" "$detail"
else
  fail "regex differential (C)" "$detail"
fi

echo "==> regex_fuzz.py --utf8 --check"
if run_logged "$LOGDIR/regex-utf8.log" "$PYTHON" scripts/regex_fuzz.py --utf8 --check; then
  regex_u_ok=1
else
  regex_u_ok=0
  tail -20 "$LOGDIR/regex-utf8.log"
fi
parse_regex "$LOGDIR/regex-utf8.log"
detail="${r_lines} lines agree, ${r_bad} mismatch (${r_pats} patterns, ${r_split} split/untested)"
if [[ $regex_u_ok -eq 1 && ${r_bad:-1} -eq 0 ]]; then
  pass "regex differential (UTF-8)" "$detail"
else
  fail "regex differential (UTF-8)" "$detail"
fi

echo
echo "======== check summary ========"
for row in "${RESULTS[@]}"; do
  IFS='|' read -r name status detail <<<"$row"
  printf '%-28s %-4s  %s\n' "$name" "$status" "$detail"
done
if [[ $FAILED -eq 0 ]]; then
  echo "======== all suites passed ========"
else
  echo "======== some suites failed ======== (logs in $LOGDIR)"
  # Keep the logs when something failed so they can be inspected.
  trap - EXIT
fi
exit "$FAILED"
