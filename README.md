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
| Statements | `if/else`, `while`, `do-while`, `for(;;)`, `for (k in a)`, `break`, `continue`, `next`, `nextfile` (POSIX 2024; not in BEGIN/END or a function), `exit [n]`, `return [expr]`, `print`, `printf`, `delete a[k]`, `delete a`, `{ }` blocks |
| Fields | `$0`, `$n`, `$expr`, field assignment rebuilds `$0` with `OFS`, `NF` assignment truncates/extends |
| Variables | `NR FNR NF FS OFS RS ORS FILENAME ARGC ARGV CONVFMT OFMT RSTART RLENGTH SUBSEP` (+ `ENVIRON`) |
| FS | `" "` (default whitespace), single character, a regex when longer, `""` splits characters |
| RS | single character, `""` (paragraph mode); longer values are used as a regex (gawk extension) |
| Arrays | associative, `(i,j)` subscripts with `SUBSEP`, `in`, `(i,j) in a`, `delete` |
| Builtins | `length substr index split sub gsub match sprintf tolower toupper sin cos atan2 exp log sqrt int rand srand system close fflush` |
| getline | `getline`, `getline var`, `getline < file`, `getline var < file`, `cmd \| getline [var]` |
| Output | `print`/`printf` with `> file`, `>> file`, `\| cmd`; `close()` (a command's exit status), `fflush([name])`, `/dev/stdout`, `/dev/stderr`; a closed pipe ends pyawk by SIGPIPE, as in other awks |
| Values | dual string/number values with POSIX *strnum* semantics for fields, `getline` input, `split()` elements, `ARGV`, `-v` and operand assignments |

Deferred: nothing at present (`nextfile` was the last deferred feature).

Not accepted, on purpose: `func` as a synonym for `function` (an old
onetrue-awk/gawk extension; POSIX, `gawk --posix` and mawk treat `func` as an
ordinary name). Where the reference awks disagree about functions, pyawk's
choices are listed in DESIGN.md ("User-defined functions").

## Tests

```sh
.venv/bin/python -m pytest -q          # unit tests + every golden case
scripts/run_golden.py [--diff] [-v]    # pass/fail summary per category
scripts/regen_golden.py [--check]      # regenerate (or verify) .out with /usr/bin/mawk
scripts/regen_golden.py --awk gawk  # use another reference awk
```

Golden cases live in `awk_tests/golden/<category>/NAME.{awk,in,out[,args]}`
and run as `awk [args] -f NAME.awk NAME.in` in a throwaway copy of the
category directory (a sandbox, so programs may write and read back files).
Optional per-case files: `NAME.environ` (`NAME=value` lines added to the
environment), `NAME.operands` (operands used instead of `NAME.in`) and
`NAME.stdin` (standard input). `scripts/make_corpus.py` is the source of
all of them. Cases listed in `awk_tests/golden/OVERRIDES` have
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
scripts/import_onetrue.py   # re-import (needs /workspace/refs/onetrue-awk/a.out, mawk, gawk)
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
Cases fed on standard input have a `NAME.stdin` file. `KNOWN_FAILURES`
works as for the onetrue suite.

```sh
scripts/import_gawk.py      # re-import (needs /workspace/refs/src/gawk-5.4.1/test, the three awks)
scripts/run_golden.py gawk --diff
```

### Current test status

As of 2026-11-01 (`.venv/bin/python -m pytest -q`, `scripts/run_golden.py`):

| suite | pass | fail | skip |
|---|---|---|---|
| pytest (all) | 803 | 0 | 122 |
| own golden corpus | 129 | 0 | 0 |
| imported onetrue-awk | 215 | 0 | 9 |
| imported gawk | 191 | 0 | 113 |

Both `KNOWN_FAILURES` lists are empty.

### Benchmarks

```sh
.venv/bin/python scripts/bench.py                     # all workloads, all awks found
.venv/bin/python scripts/bench.py --save baseline     # store a run in .bench/
.venv/bin/python scripts/bench.py --compare baseline  # add a speedup column
.venv/bin/python scripts/bench.py --only funcs --awks pyawk,mawk
```

pyawk compiles the program to Python closures before running it; it is
now about 3–4.5× faster than the original tree-walking interpreter, and
roughly 4–15× slower than mawk. Tables and details are in DESIGN.md
("Performance").

See [DESIGN.md](DESIGN.md) for architecture, semantics decisions and the list
of places where mawk (the reference used for `.out`) differs from POSIX.
