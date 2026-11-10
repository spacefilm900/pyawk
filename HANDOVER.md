# Handover

## Purpose

This file exists so that another agent or developer can take over
maintenance of pyawk without relying on the original assistant (Grok Bot)
or its chat history. It is the entry point: it says what to read, how to
build and verify the tree, and the rules that must be followed.

Start here, then read [README.md](README.md) for usage and the test map,
and [DESIGN.md](DESIGN.md) for architecture and semantic decisions. Do not
treat this chat, or any assistant's memory, as a source of truth once you
have this file.

## Project overview and scope

pyawk is a POSIX-compatible AWK interpreter written in pure Python 3.11+.
It has its own POSIX Extended Regular Expression (ERE) engine
(leftmost-longest matching, an NFA plus a lazily built DFA). Programs are
parsed into an abstract syntax tree and compiled to Python closures before
they run.

The target is POSIX AWK (including POSIX 2024 `nextfile`). Where POSIX is
silent or the reference awks disagree, the tie-break in "Standing rules"
below applies. The README's "Scope" table lists what is supported.

Deferred: nothing in the POSIX set is still outstanding (`nextfile` was
the last deferred feature). Gawk-only extensions such as `gensub()`,
`BEGINFILE`/`ENDFILE`, `IGNORECASE` and the time functions are out of
scope unless the owner asks for them as opt-in additions. `func` as a
synonym for `function` is rejected on purpose.

The current version on the `enhancements` branch is 1.2.0.

## Repository map

| Path | What |
|---|---|
| `pyawk/` | Interpreter package: lexer, parser, compiler, runtime, regex engine, builtins, locale/UTF-8 helpers |
| `pyawk/unicode_tables.py` | Generated Unicode class and case tables (from gawk under C.UTF-8) |
| `tests/` | pytest unit tests and documentation/CI guards |
| `awk_tests/golden/` | Own golden corpus (expected `.out` files are committed) |
| `awk_tests/gawk/` | Imported GNU awk 5.4.1 tests (GPL v3; see the notice in that folder) |
| `awk_tests/onetrue/` | Imported onetrue awk tests (Lucent 1997 notice in that folder) |
| `awk_tests/regex_differential.json` | Recorded C-locale regex majority answers |
| `awk_tests/regex_differential_utf8.json` | Recorded UTF-8 regex answers |
| `scripts/regress.sh` | Local recipe that matches CI (`quick` or `thorough`); stops on the first failure |
| `scripts/check.sh` | Same suites as thorough, with a pass/fail summary for every suite |
| `scripts/run_golden.py` | Runs the golden and imported suites and prints per-category counts |
| `scripts/regex_fuzz.py` | Compares pyawk with the recorded regex tables (`--check` needs no reference awk) |
| `scripts/import_gawk.py`, `scripts/import_onetrue.py` | Re-import upstream tests (needs reference awks; preserves licence notices) |
| `scripts/regen_golden.py` | Regenerates golden `.out` files (needs reference awks; do not run casually) |
| `ci/github-ci.yml` | Canonical GitHub Actions workflow (copied to `.github/workflows/ci.yml`) |
| `DESIGN.md` | Architecture and every recorded semantic decision |
| `LICENSE` | MIT licence, copyright Keith Haviland |

## Setup and build

Python 3.11 or later is required. On this tree a virtual environment
already exists at `.venv/`. Prefer `.venv/bin/python` (plain `python` may
not be on `PATH`).

```sh
python3 -m venv .venv
.venv/bin/pip install -e '.[test]'    # installs pytest, ruff, and the pyawk console script
.venv/bin/python -m pyawk --version
.venv/bin/python -m pyawk 'BEGIN { print "hello" }'
```

## Running the tests

pytest itself needs no other awk: the golden `.out` files and the regex
records are committed.

```sh
make check                         # the single command: every suite, then a summary
scripts/check.sh                   # the same, invoked directly
scripts/regress.sh thorough        # the same suites; stops on the first failure (this is what CI runs)
scripts/regress.sh quick           # ruff + pytest only
.venv/bin/python -m pytest -q      # unit tests, golden cases, imported suites, regex tests
scripts/run_golden.py [--diff]     # pass/fail/skip per category (golden, onetrue, gawk)
scripts/regex_fuzz.py --check      # C-locale regex table
scripts/regex_fuzz.py --utf8 --check
```

`make check` / `scripts/check.sh` must not be given flags that rewrite
test data (`--write-known-failures`, `regen_golden.py`, and the like).

The slow tools that talk to reference awks (`regen_golden.py` without
`--check`, `regex_fuzz.py` without `--check`, the import scripts,
`bench.py`) are described in the README. They are not part of `make check`.

## Expected current results

Measured on the `enhancements` branch on 10 November 2026 at 06:16 UK
time (Europe/London, UTC+0), with `make check` (about 96 seconds on this
machine). No test files were modified.

```
ruff                         PASS  clean
pytest                       PASS  2021 passed, 126 skipped (floor 1900)
golden corpus                PASS  158 passed, 0 failed, 0 skipped
onetrue suite                PASS  215 passed, 0 failed, 9 skipped
gawk suite                   PASS  192 passed, 0 failed, 112 skipped
regex differential (C)       PASS  13503 lines agree, 0 mismatch (570 patterns, 108 split/untested)
regex differential (UTF-8)   PASS  2152 lines agree, 0 mismatch (88 patterns, 84 split/untested)
```

All `KNOWN_FAILURES` lists are empty. The pytest floor in
`scripts/regress.sh` and `scripts/check.sh` is 1900 passed, so a
misconfigured collection cannot look green.

A later run should match these counts, or beat them without editing
tests. If a count drops, that is a regression.

## Release and git workflow

* **`master` is frozen at v1.0** (commit `00b53a3`, tag `v1.0`). Do not
  merge into it, push to it, or change it unless the owner has explicitly
  asked you to.
* **Do all work on `enhancements`.** That is the current development
  branch (v1.2.0 and later fixes).
* **The open pull request into `master` is a review vehicle only.** A
  pull request (PR) is a proposed change put up for review. PR #1 exists
  so that CodeRabbit can comment on the work. It must never be merged.
* **Tags and GitHub releases only with the owner's approval.** A tag is
  a named label for one commit, used to mark a release. Do not create
  one yourself.
* **CI** (GitHub Actions, `.github/workflows/ci.yml`) runs on push to
  `enhancements`, on pull requests into `enhancements` or `master`, and
  weekly. The `quick` job is ruff + pytest on Python 3.11, 3.12 and
  3.13; the `thorough` job then runs `scripts/regress.sh thorough`.
  No secrets and no reference awks are required.

## Standing rules

These come from the owner (Keith Haviland) and apply to every change:

1. **Never alter tests.** If there is a problem with an imported test,
   a golden case, or a POSIX-challenge case, report it back. Do not edit
   the test to make it pass.
2. **Test first.** Add or extend a test that fails, then change the
   interpreter, then confirm the test passes.
3. **Small commits.** One idea per commit, with a clear message.
4. **Full regression after each change.** Run `make check` (or
   `scripts/regress.sh thorough`) after each change, not only at the end.
5. **Freeze results before a fix.** Record what fails today before you
   change anything, so you can tell a fix from a new breakage.
6. **Tie-break.** When behaviour is unclear: POSIX first; then the
   majority of onetrue awk, `gawk --posix` and mawk. Record the choice
   in DESIGN.md and pin it with a test. BusyBox is not part of the
   tie-break.

## Licences

* The interpreter is MIT, copyright (c) 2026 Keith Haviland; see
  [LICENSE](LICENSE).
* `awk_tests/gawk/` is GNU Awk 5.4.1 test data, GPL v3 or later; see
  [awk_tests/gawk/NOTICE.md](awk_tests/gawk/NOTICE.md) and
  [awk_tests/gawk/LICENSE-GPL-3.0.txt](awk_tests/gawk/LICENSE-GPL-3.0.txt).
* `awk_tests/onetrue/` is onetrue awk test data, Lucent Technologies
  1997 notice; see [awk_tests/onetrue/NOTICE.md](awk_tests/onetrue/NOTICE.md).

Those imported files are test data only and are not part of the
interpreter. The import scripts keep the notices when they rebuild the
folders (they already kept `KNOWN_FAILURES`).

## Related resources

These live beside this repository on the development machine, and as
sibling GitHub repositories under `spacefilm900`. They are not part of
the pyawk tree.

* **[spacefilm900/awk-posix-challenge](https://github.com/spacefilm900/awk-posix-challenge)**
  — an independent POSIX AWK challenge (v0.6 is the final tagged
  corpus). Run it with `python3 tools/runner.py --cmd '.venv/bin/python -m pyawk'`.
  Observational cases (including those that cite Austin Group
  interpretation #1198) are not POSIX failures. Do not edit the case
  files. The runner may need `PYTHONPATH` if you invoke pyawk as a
  module.
* **[spacefilm900/awk-volume-tests](https://github.com/spacefilm900/awk-volume-tests)**
  — a large-input check that runs a portable program over the complete
  works of Shakespeare. Output must match the committed expected text
  byte for byte.
* **The sister interpreter:** [hawk](https://github.com/spacefilm900/hawk)
  is the Rust port. It follows the same decisions. A checkout next to
  this one (`../hawk`) is useful but not required for pyawk's own tests.
* **Reference awks** used for votes, imports and benchmarks, on the
  development machine under `/workspace/refs` (and `mawk` from the
  system). pyawk does not hard-code these paths. Set the variables
  below, or put the programs on `PATH` under the usual names:

  | reference | environment variable | path on this machine | testdir variable |
  |---|---|---|---|
  | mawk | `PYAWK_REF_MAWK` | `/usr/bin/mawk` | — |
  | onetrue awk | `PYAWK_REF_ONETRUE` | `/workspace/refs/onetrue-awk/a.out` | `PYAWK_REF_ONETRUE_TESTDIR` (`.../testdir`) |
  | gawk (always run as `gawk --posix`) | `PYAWK_REF_GAWK` | `/workspace/refs/local/bin/gawk` (GNU Awk 5.4.1) | `PYAWK_REF_GAWK_TESTDIR` (`/workspace/refs/src/gawk-5.4.1/test`) |

  Plain `awk` is never used. A variable that names nothing runnable is
  an error, not a fallback. See the README, "Pointing the tools at the
  reference awks". BusyBox (`/workspace/refs/busybox/busybox`) is
  available for comparison only; it is not a tie-break voter.

## Known open items

* Nothing required is outstanding in the POSIX feature set.
* The review pull request (PR #1) into `master` must stay unmerged.
* `DESIGN.md`'s "Known issues / limitations" lists remaining behaviour
  that is left as it is on purpose (for example `%x` of integers wider
  than 64 bits, and `rand()` sequences that differ from other awks).
* Performance is improved by the closure compiler but remains slower
  than mawk (see DESIGN.md, "Performance"). Speeding it up further is
  optional, not a correctness gap.

## Suggested first task for a new maintainer

A good first task, if the owner wants to test a handover, is to add
**`gensub()`** as an *opt-in gawk extension*.

`gensub()` is gawk's general substitution function. It is self-contained,
well documented in the gawk manual, and gawk itself is the oracle (the
program you check correct output against). It exercises the regex engine,
the builtins and the test workflow without changing the POSIX core.

Rules for that work:

* Implement it only if the owner asks, and only on a branch created for
  the experiment (or in a separate copy of the repository). Do not put
  it straight onto `enhancements` unless the owner says to.
* It must be opt-in. A POSIX program that does not call `gensub()` must
  behave exactly as it does today.
* **Every POSIX result must stay unchanged.** Run `make check` before
  and after; the golden, onetrue, gawk-imported, and regex counts must
  not regress. Do not edit existing tests to make a change look green.
* Add new tests for `gensub()` itself, rather than altering the imported
  suites.

A harder second task, if that one goes well, is `BEGINFILE`/`ENDFILE`,
which touches the parser and the main input loop. Avoid `IGNORECASE`
(it changes behaviour almost everywhere) and the time functions (they
are hard to test reliably).
