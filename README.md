# pyawk

A POSIX-compatible AWK interpreter in pure Python (3.11+), built test-first
against a golden corpus generated with the system awk.

## Usage

```sh
python -m pyawk 'program' [file ...]
python -m pyawk -F: -v limit=10 -f prog.awk data.txt
pip install -e '.[test]'   # installs the `pyawk` console script and pytest
```

Options: `-F fs`, `-v var=value` (repeatable), `-f progfile` (repeatable,
`-` = stdin), `--`, `--version`. Operands of the form `var=value` are
assignments processed when reached, as in POSIX. Exit status is the value of
`exit expr` (0 by default) and 2 for syntax/runtime errors.

## Scope: spec v0.1

| Area | Supported |
|---|---|
| Program | `BEGIN`, `pattern { action }`, `pattern`, `{ action }`, `start, end [{ action }]`, `END` (multiple of each), `function` definitions |
| Functions | `function name(params) { ... }` anywhere in the program (calls may precede the definition), `name(args)` calls (no space before `(`), `return [expr]`, extra parameters as local variables, scalars by value, arrays by reference (an unused variable passed in becomes an array if the function uses it as one), recursion (depth > 10000) |
| Patterns | any expression, `/ere/`, `!`, `&&`, `\|\|`, `~`, `!~`; range patterns `start, end` (each rule keeps its own state, which carries over from one input file to the next; a newline may follow the comma) |
| Statements | `if/else`, `while`, `do-while`, `for(;;)`, `for (k in a)`, `break`, `continue`, `next` (not in BEGIN/END; allowed in a function, as POSIX says), `nextfile` (POSIX 2024; not in BEGIN/END or a function), `exit [n]`, `return [expr]`, `print`, `printf`, `delete a[k]`, `delete a`, `{ }` blocks |
| Fields | `$0`, `$n`, `$expr`, field assignment rebuilds `$0` with `OFS`, `NF` assignment truncates/extends |
| Variables | `NR FNR NF FS OFS RS ORS FILENAME ARGC ARGV CONVFMT OFMT RSTART RLENGTH SUBSEP` (+ `ENVIRON`) |
| FS | `" "` (default whitespace), single character, a regex when longer, `""` splits characters |
| RS | single character, `""` (paragraph mode); longer values are used as a regex (gawk extension) |
| Arrays | associative, `(i,j)` subscripts with `SUBSEP`, `in`, `(i,j) in a`, `delete` |
| Built-in functions | `length substr index split sub gsub match sprintf tolower toupper sin cos atan2 exp log sqrt int rand srand system close fflush` |
| getline | `getline`, `getline var`, `getline < file`, `getline var < file`, `cmd \| getline [var]` |
| Output | `print`/`printf` with `> file`, `>> file`, `\| cmd`; `close()` (a command's exit status), `fflush([name])`, `/dev/stdout`, `/dev/stderr`; a closed pipe ends pyawk by SIGPIPE, as in other awks |
| Locale | characters in a UTF-8 locale (`LC_ALL`/`LC_CTYPE`/`LANG` containing `UTF-8` or `utf8`): `length`, `substr`, `index`, `match`, `split`, printf `%c` and widths, `toupper`/`tolower`, regex `.`/brackets/classes, FS and RS; bytes in any other locale. Input bytes always round-trip; each invalid UTF-8 byte counts as one character. Details and decisions: DESIGN.md ("Locales and UTF-8") |
| Values | dual string/number values with POSIX *strnum* semantics for fields, `getline` input, `split()` elements, `ARGV`, `-v` and operand assignments |

Deferred: nothing at present (`nextfile` was the last deferred feature).

Not accepted, on purpose: `func` as a synonym for `function` (an old
onetrue-awk/gawk extension; POSIX, `gawk --posix` and mawk treat `func` as an
ordinary name). Where the reference awks disagree about functions, pyawk's
choices are listed in DESIGN.md ("User-defined functions").

## Tests

The regression suite is exercised on POSIX (Linux/macOS). A couple of
ASCII-locale harness probes are skipped on Windows, where `LC_ALL=C` does
not yield an ASCII preferred encoding.

The CI recipes live in `scripts/regress.sh` (no reference awks or secrets;
golden `.out` files and the regex differential JSON are committed).
`quick` is ruff + pytest on Python 3.11–3.13; `thorough` adds
`run_golden.py` and `regex_fuzz.py --check` (C and UTF-8). The script
refuses to report success if fewer than 1900 tests passed, so a
misconfigured collect cannot look green. The GitHub Actions workflow
definition is `ci/github-ci.yml`; copy it to `.github/workflows/ci.yml`
to enable Actions (pushing under `.github/workflows/` needs a token with
the `workflow` scope).

```sh
scripts/regress.sh quick               # same as CI quick job
scripts/regress.sh thorough            # same as CI thorough job
.venv/bin/python -m pytest -q          # unit tests + every golden case
scripts/run_golden.py [--diff] [-v]    # pass/fail summary per category
scripts/regen_golden.py [--check]      # regenerate (or verify) .out with mawk (gawk for utf8)
scripts/regen_golden.py --awk 'gawk --posix'  # use another awk instead
```

### Pointing the tools at the reference awks

pytest itself needs no other awk: the golden `.out` files and the regex
differential records are committed. The tools that run the three
reference awks (`regen_golden.py`, `regex_fuzz.py` without `--check`, the
import scripts, `bench.py`, `gen_unicode_tables.py`) and a few smoke tests
in `tests/test_references.py` find them like this:

| reference | environment variable | else on PATH as |
|---|---|---|
| mawk | `PYAWK_REF_MAWK` | `mawk` |
| onetrue-awk (BWK awk) | `PYAWK_REF_ONETRUE` | `original-awk`, `onetrue-awk` or `bwk-awk` |
| gawk (run as `gawk --posix`) | `PYAWK_REF_GAWK` | `gawk` |

A variable holds a program path (`./a.out` is fine) or a name to look up
on PATH. Plain `awk` is never used, since it is a different awk on
different systems. A variable that names nothing runnable is an error, not
a fallback. A tool that needs a missing reference stops with exit status 2
and says which variable to set, before it writes anything. The smoke tests
are skipped with that same message. The one exception is
`scripts/bench.py`, which times whichever awks it has: it warns about a
missing reference or a variable that names nothing and carries on without
that awk. It stops (exit status 2) only when `--awks` names an awk it
cannot find. The import scripts also need the
upstream test suites: `PYAWK_REF_ONETRUE_TESTDIR` (onetrue-awk's
`testdir/`, by default the one beside the onetrue program) and
`PYAWK_REF_GAWK_TESTDIR` (`test/` in the gawk 5.4.1 source tree), or
`--src DIR`.

```sh
export PYAWK_REF_ONETRUE=$HOME/src/onetrue-awk/a.out
export PYAWK_REF_GAWK=$HOME/gawk-5.4.1/bin/gawk
export PYAWK_REF_GAWK_TESTDIR=$HOME/src/gawk-5.4.1/test
.venv/bin/python -m pytest -q -rs tests/test_references.py   # check: no skips
scripts/regen_golden.py --check
```

The variables start with `PYAWK_REF_`, not `PYAWK_T_`: the golden-case
harness removes inherited `PYAWK_T_*` variables (they belong to the
`ENVIRON` cases) before it runs an awk.

Golden cases live in `awk_tests/golden/<category>/NAME.{awk,in,out[,args]}`
and run as `awk [args] -f NAME.awk NAME.in` in a throwaway copy of the
category directory (a sandbox, so programs may write and read back files).
Optional per-case files: `NAME.environ` (`NAME=value` lines added to the
environment), `NAME.operands` (operands used instead of `NAME.in`),
`NAME.stdin` (standard input), `NAME.status` (expected exit status; default
0 when absent) and `NAME.err` (optional stderr check). Without `.err`,
stderr is not checked. With `.err`, the first line may be
`mode: portable`, `mode: exact`, `mode: substr` or `mode: regex` (default
`exact` if omitted). `portable` means POSIX requires a diagnostic
(stderr must be non-empty) without locking the wording — copying gawk or
pyawk text is not treated as conformance. `exact` / `substr` / `regex`
are intentional implementation-specific checks. A missing or wrong exit
status fails the case the same way a wrong stdout does: parse and runtime
errors are ordinary expected process failures (typically exit 2 with a
`.status` / `.err` pair), not harness crashes.
`scripts/make_corpus.py` is the source of most of them; the `errors`
category holds a few hand-written expected-failure fixtures. Cases listed in `awk_tests/golden/OVERRIDES` have
hand-corrected `.out` files (mawk is the outlier there); `regen_golden.py`
leaves them alone. `awk_tests/golden/DECISIONS` lists cases that follow a
project decision instead of the majority, and
`scripts/regen_golden.py --vote CAT/NAME...` shows how the three reference
awks vote on given cases.

### Imported onetrue-awk suite

`awk_tests/onetrue/` holds the `t.*` and `p.*` programs from onetrue-awk's
`testdir/`, imported by `scripts/import_onetrue.py` (expected output from the
onetrue-awk binary; mawk and `gawk --posix` are run too, for comparison).
`MANIFEST.tsv` gives each case a status (`run`, `sorted` for programs that
print in `for (k in a)` order, or `skip`), a reason and how the three
reference awks compared. `KNOWN_FAILURES` lists cases pyawk still gets
wrong; pytest reports them as expected failures (strict, so a fix forces the
list to be updated) and `scripts/run_golden.py --write-known-failures`
regenerates it.

```sh
scripts/import_onetrue.py   # re-import (needs the three awks and onetrue-awk's testdir/)
scripts/run_golden.py onetrue --diff
```

### Imported gawk suite

`awk_tests/gawk/` holds GNU awk 5.4.1's `BASIC_TESTS` plus its `NEED_POSIX`
tests (5.4.1 has no `POSIX_TESTS` list), imported by `scripts/import_gawk.py`
from `test/Makefile.am`. gawk's `.ok` files mix stdout with error messages
and an `EXIT CODE` line, and record gawk's default (extended) mode, so the
importer uses a `.ok` only when at least two of onetrue-awk, mawk and
`gawk --posix` produce the same stdout; otherwise it uses the references'
majority, or skips the case. Skips (gawk extensions, locales, error-message
tests, custom Makefile rules, ...) carry a reason in `MANIFEST.tsv`.
The 48 `error-message test` skips stay skipped for now: their `.ok` is
gawk's wording, not a portable expectation. Cases that already run and
stop with a syntax error (for example `delfunc`) now carry a `.status`
of 2 so the exit code is checked; reclaiming a skip means a `.status`
plus typically `mode: portable` on `.err` (stderr non-empty), or an
intentional `exact`/`substr`/`regex` check — not importing gawk's text
as if it were POSIX. Cases fed on standard input have a
`NAME.stdin` file. `KNOWN_FAILURES` works as for the onetrue suite.

```sh
scripts/import_gawk.py      # re-import (needs the three awks and gawk-5.4.1/test)
scripts/run_golden.py gawk --diff
```

### Current test status

As of 2026-11-06 (`.venv/bin/python -m pytest -q` with the three reference awks found, `scripts/run_golden.py`):

| suite | pass | fail | skip |
|---|---|---|---|
| pytest (all) | 2016 | 0 | 126 |
| own golden corpus (incl. regex_posix 8, utf8 17, errors 2) | 158 | 0 | 0 |
| imported onetrue-awk | 215 | 0 | 9 |
| imported gawk | 192 | 0 | 112 |
| regex differential (570 patterns, 13 503 lines) | 13 503 | 0 | 108 split/untested |
| UTF-8 regex differential (88 patterns, 2 152 lines) | 2 152 | 0 | 84 untested |

All `KNOWN_FAILURES` lists are empty.

### Undefined behaviour and parse-time errors

Where POSIX leaves a behaviour undefined, unspecified or
implementation-defined, pyawk follows POSIX's preference if it has one,
otherwise the majority of onetrue-awk, `gawk --posix` and mawk. Every
such choice is recorded, with what each reference does, in DESIGN.md
("Parse-time errors", "Regex decisions", "I/O decisions") and pinned by
a test. Mistakes the references reject before running (`next` in BEGIN,
`substr("a")`, `split(s, 3)`, `sub(/a/, "b", "c")`, a stray `(a, b)`) are
syntax errors in pyawk too, even in code that never runs.

### Regular expressions

Regexes follow POSIX ERE with leftmost-longest matching for `match()`,
`sub`/`gsub`, `split`, regex FS and regex RS. Bracket expressions,
including `[= =]` and `[. .]` (which match only the character itself),
work in the C locale and in UTF-8 locales, where they see characters and
the classes cover Unicode. The `awk_tests/regex_differential.json` record
holds the majority answer of mawk, onetrue-awk and gawk --posix, run with
LC_ALL=C; `awk_tests/regex_differential_utf8.json` holds onetrue-awk's and
gawk --posix's answers under LC_ALL=C.UTF-8 (gawk breaks ties; mawk is
byte-only), for patterns and inputs with multibyte and invalid UTF-8.

```sh
.venv/bin/python scripts/regex_fuzz.py            # re-run the references, rewrite the record, compare pyawk
.venv/bin/python scripts/regex_fuzz.py --check    # compare pyawk with the stored record only
.venv/bin/python scripts/regex_fuzz.py --utf8 [--check]  # the same for the UTF-8 corpus
.venv/bin/python scripts/regex_fuzz.py --verify-safety 1000  # check the fast-path rule exhaustively
```

Rules, overrides and the decisions on constructs POSIX leaves undefined are in DESIGN.md ("Regular expressions", "Regex decisions").

### Benchmarks

```sh
.venv/bin/python scripts/bench.py                     # all workloads, all awks found
.venv/bin/python scripts/bench.py --save baseline     # store a run in .bench/
.venv/bin/python scripts/bench.py --compare baseline  # add a speedup column
.venv/bin/python scripts/bench.py --only funcs --awks pyawk,mawk
.venv/bin/python scripts/bench.py --path onetrue=/path/to/a.out  # this run only (or PYAWK_REF_ONETRUE)
.venv/bin/python scripts/bench.py --locale C.UTF-8    # time UTF-8 mode (default LC_ALL=C)
```

pyawk compiles the program to Python closures before running it; it is
now about 3–4.5× faster than the original tree-walking interpreter, and
roughly 4–15× slower than mawk. Tables and details are in DESIGN.md
("Performance").

See [DESIGN.md](DESIGN.md) for architecture, semantics decisions and the list
of places where mawk (the reference used for `.out`) differs from POSIX.

## License

MIT, copyright (c) 2026 Keith Haviland; see [LICENSE](LICENSE).

The interpreter code is MIT. The imported test suites under
`awk_tests/` have their own licences:

- `awk_tests/gawk/` — GNU Awk (gawk) 5.4.1 tests, GPL v3 or later;
  see [awk_tests/gawk/NOTICE.md](awk_tests/gawk/NOTICE.md) and
  [awk_tests/gawk/LICENSE-GPL-3.0.txt](awk_tests/gawk/LICENSE-GPL-3.0.txt).
- `awk_tests/onetrue/` — onetrue awk tests, Lucent Technologies 1997
  notice; see [awk_tests/onetrue/NOTICE.md](awk_tests/onetrue/NOTICE.md).

Those files are test data only and are not part of the interpreter.
