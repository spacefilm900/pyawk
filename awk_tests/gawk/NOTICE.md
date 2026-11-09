# Notice — imported GNU Awk (gawk) tests

The awk programs and their input data in this directory come from the
**GNU Awk (gawk) 5.4.1** test suite (`test/` in the gawk 5.4.1 source
distribution), Copyright (C) Free Software Foundation, Inc.

They are licensed under the **GNU General Public License version 3 or
later**. See [LICENSE-GPL-3.0.txt](LICENSE-GPL-3.0.txt) in this directory
for the full licence text.

These files are **test data only**. They are not part of the pyawk
interpreter. The pyawk interpreter itself remains under the MIT licence
(see the repository root [LICENSE](../../LICENSE)).

## What is from gawk, and what is ours

From gawk 5.4.1 `test/` (GPL v3 or later):

- `*.awk` — the test programs (copied from gawk, or for a few skipped
  cases a short placeholder comment written by our importer)
- `*.stdin` / `*.in` — input data copied from gawk's corresponding
  `*.in` files (and, for a few special cases, a short stdin literal
  written by the importer to match gawk's Makefile rule)

Produced by our importer (`scripts/import_gawk.py`) and harness, and
**not** part of the gawk distribution:

- `*.operands`, `*.args` — how our harness invokes each case
- `*.out` — expected stdout, taken from gawk's `.ok` only when a
  majority of onetrue-awk, mawk and `gawk --posix` agree; otherwise
  from that majority (or empty for skipped cases)
- `*.status` — expected process exit status for our harness
- `MANIFEST.tsv` — per-case run/skip classification
- `KNOWN_FAILURES` — local failure list maintained by
  `scripts/run_golden.py`
- this `NOTICE.md` and `LICENSE-GPL-3.0.txt`

Re-running `scripts/import_gawk.py` rebuilds this directory from
scratch (it keeps only `KNOWN_FAILURES`). Restore this notice and
`LICENSE-GPL-3.0.txt` afterwards if you re-import.
