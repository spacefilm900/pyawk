#!/usr/bin/env bash
# Exact local recipe matching .github/workflows/ci.yml.
#
# usage: scripts/regress.sh [quick|thorough]
#
#   quick     ruff + pytest (no reference awks required)
#   thorough  quick, then run_golden.py and regex_fuzz --check (both UTF-8
#             and C corpora). Still no reference awks: .out files and the
#             differential JSON are committed.
#
# Exits non-zero on the first failure.  Override REGRESS_RUFF / REGRESS_PYTEST
# only in tests that inject a failing step.
set -euo pipefail

ROOT=$(cd "$(dirname "$0")/.." && pwd)
cd "$ROOT"

MODE=${1:-quick}
case "$MODE" in
  quick|thorough) ;;
  *)
    echo "usage: $0 [quick|thorough]" >&2
    exit 2
    ;;
esac

if [[ -x .venv/bin/python ]]; then
  PYTHON=.venv/bin/python
else
  PYTHON=${PYTHON:-python3}
fi

# Floor so a misconfigured collect (almost no tests) cannot look green.
# Keep in sync with tests/test_ci_gate.py expectations (passed >= 1900).
MIN_PASSED=${MIN_PASSED:-1900}

echo "==> ruff"
# REGRESS_RUFF lets a unit test force a failing step without editing the script.
if [[ -n "${REGRESS_RUFF:-}" ]]; then
  eval "$REGRESS_RUFF"
else
  "$PYTHON" -m ruff check .
fi

echo "==> pytest"
if [[ -n "${REGRESS_PYTEST:-}" ]]; then
  eval "$REGRESS_PYTEST"
else
  # Capture the summary line so we can enforce MIN_PASSED.
  # pytest writes the summary to stdout when not a TTY.
  set +e
  out=$("$PYTHON" -m pytest -q --tb=short 2>&1)
  status=$?
  set -e
  printf '%s\n' "$out"
  if [[ $status -ne 0 ]]; then
    exit "$status"
  fi
  # Summary looks like: "2012 passed, 126 skipped in 45.74s"
  passed=$(printf '%s\n' "$out" | awk '/passed/{for(i=1;i<=NF;i++) if($i=="passed,") print $(i-1); else if($i=="passed") print $(i-1)}' | tail -1)
  if [[ -z "${passed}" ]]; then
    echo "regress: could not parse pytest passed count from summary" >&2
    exit 2
  fi
  if (( passed < MIN_PASSED )); then
    echo "regress: only ${passed} tests passed (floor ${MIN_PASSED}); suite may have been silently reduced" >&2
    exit 2
  fi
  echo "regress: ${passed} passed (floor ${MIN_PASSED})"
fi

if [[ "$MODE" == "thorough" ]]; then
  echo "==> run_golden.py"
  "$PYTHON" scripts/run_golden.py
  echo "==> regex_fuzz.py --check"
  "$PYTHON" scripts/regex_fuzz.py --check
  echo "==> regex_fuzz.py --utf8 --check"
  "$PYTHON" scripts/regex_fuzz.py --utf8 --check
fi

echo "regress (${MODE}): OK"
