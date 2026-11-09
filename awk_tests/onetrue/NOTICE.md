# Notice — imported onetrue awk tests

The awk programs and their input data in this directory come from the
**onetrue awk** ("The One True Awk") test directories
(https://github.com/onetrue-awk/awk), specifically the `testdir/`
programs (`t.*` and `p.*`) and the shared inputs `test.data` /
`test.countries`.

They are used under the following Lucent Technologies licence, which
must appear with every copy:

```
/****************************************************************
Copyright (C) Lucent Technologies 1997
All Rights Reserved

Permission to use, copy, modify, and distribute this software and
its documentation for any purpose and without fee is hereby
granted, provided that the above copyright notice appear in all
copies and that both that the copyright notice and this
permission notice and warranty disclaimer appear in supporting
documentation, and that the name Lucent Technologies or any of
its entities not be used in advertising or publicity pertaining
to distribution of the software without specific, written prior
permission.

LUCENT DISCLAIMS ALL WARRANTIES WITH REGARD TO THIS SOFTWARE,
INCLUDING ALL IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS.
IN NO EVENT SHALL LUCENT OR ANY OF ITS ENTITIES BE LIABLE FOR ANY
SPECIAL, INDIRECT OR CONSEQUENTIAL DAMAGES OR ANY DAMAGES
WHATSOEVER RESULTING FROM LOSS OF USE, DATA OR PROFITS, WHETHER
IN AN ACTION OF CONTRACT, NEGLIGENCE OR OTHER TORTIOUS ACTION,
ARISING OUT OF OR IN CONNECTION WITH THE USE OR PERFORMANCE OF
THIS SOFTWARE.
****************************************************************/
```

These files are **test data only**. They are not part of the pyawk
interpreter. The pyawk interpreter itself remains under the MIT licence
(see the repository root [LICENSE](../../LICENSE)).

## What is from onetrue awk, and what is ours

From onetrue awk `testdir/` (Lucent licence above):

- `*.awk` — the test programs (`t.*` / `p.*`)
- `*.in` — copies of `test.data` or `test.countries`

Produced by our importer (`scripts/import_onetrue.py`) and harness,
and **not** part of the onetrue distribution:

- `*.operands` — how our harness invokes `p.*` cases (input twice)
- `*.out` — expected stdout, produced by running the onetrue-awk
  binary on each program
- `*.status` — expected process exit status for our harness
- `MANIFEST.tsv` — per-case run/skip classification
- `KNOWN_FAILURES` — local failure list maintained by
  `scripts/run_golden.py`
- this `NOTICE.md`

Re-running `scripts/import_onetrue.py` rebuilds this directory from
scratch (it keeps only `KNOWN_FAILURES`). Restore this notice
afterwards if you re-import.
