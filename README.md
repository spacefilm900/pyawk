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
| Program | `BEGIN`, `pattern { action }`, `pattern`, `{ action }`, `END` (multiple of each) |
| Patterns | any expression, `/ere/`, `!`, `&&`, `\|\|`, `~`, `!~` |
| Statements | `if/else`, `while`, `do-while`, `for(;;)`, `for (k in a)`, `break`, `continue`, `next`, `exit [n]`, `print`, `printf`, `delete a[k]`, `delete a`, `{ }` blocks |
| Fields | `$0`, `$n`, `$expr`, field assignment rebuilds `$0` with `OFS`, `NF` assignment truncates/extends |
| Variables | `NR FNR NF FS OFS RS ORS FILENAME ARGC ARGV CONVFMT OFMT RSTART RLENGTH SUBSEP` (+ `ENVIRON`) |
| FS | `" "` (default whitespace), single character, a regex when longer, `""` splits characters |
| RS | single character, `""` (paragraph mode); longer values are used as a regex (gawk extension) |
| Arrays | associative, `(i,j)` subscripts with `SUBSEP`, `in`, `(i,j) in a`, `delete` |
| Builtins | `length substr index split sub gsub match sprintf tolower toupper sin cos atan2 exp log sqrt int rand srand system close fflush` |
| getline | `getline`, `getline var`, `getline < file`, `getline var < file`, `cmd \| getline [var]` |
| Output | `print`/`printf` with `> file`, `>> file`, `\| cmd`; `close()` |
| Values | dual string/number values with POSIX *strnum* semantics for fields, `getline` input, `split()` elements, `ARGV`, `-v` and operand assignments |

Deferred (they fail with a clear "not supported yet" error): user-defined
functions / `return`, range patterns `p1, p2`, `nextfile`.

## Tests

```sh
.venv/bin/python -m pytest -q          # unit tests + every golden case
scripts/run_golden.py [--diff] [-v]    # pass/fail summary per category
scripts/regen_golden.py [--check]      # regenerate (or verify) .out with /usr/bin/mawk
scripts/regen_golden.py --awk gawk  # use another reference awk
```

Golden cases live in `awk_tests/golden/<category>/NAME.{awk,in,out[,args]}`
and run as `awk [args] -f NAME.awk NAME.in` from the category directory.
`scripts/make_corpus.py` is the source of the `.awk/.in/.args` files.

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

See [DESIGN.md](DESIGN.md) for architecture, semantics decisions and the list
of places where mawk (the reference used for `.out`) differs from POSIX.
