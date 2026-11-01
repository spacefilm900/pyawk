# pyawk design notes

## Pipeline

```
source --lexer.py--> tokens --parser.py--> ast.py nodes --runtime.py--> output
                                                 builtins.py, values.py, regex.py
```

| Module | Responsibility |
|---|---|
| `errors.py` | `AwkError` → `AwkSyntaxError` (with source line), `AwkRuntimeError`, `AwkUnsupportedError` (deferred features) |
| `values.py` | value model, string↔number conversion, comparison rules |
| `lexer.py` | tokens, regex-vs-division, newline rules, string escapes |
| `ast.py` | slotted dataclasses for expressions, statements, rules |
| `parser.py` | recursive-descent parser, one method per precedence level |
| `regex.py` | POSIX ERE → Python `re` translation, cached compilation (extra module, keeps runtime small) |
| `runtime.py` | `Interpreter`, record reader, field splitting, I/O manager |
| `builtins.py` | builtin functions, `printf` formatting, `sub/gsub` engine |
| `cli.py` | argument parsing, `python -m pyawk` entry point |

The package is named `pyawk`, so `pyawk.ast` never shadows the stdlib `ast`.

## Values

A value is one of: `float` (number), `str` (string), or `StrNum(s, n)`
(a *numeric string*: input that looks like a number). `UNINIT` is
`StrNum("", 0.0)`, so uninitialised variables compare equal to both `0` and `""`.

* StrNums are created for fields, `$0`, `getline` input, `split()` elements,
  `ARGV`, `ENVIRON`, `-v` and operand assignments when the text satisfies the
  POSIX numeric-string grammar (leading/trailing blanks allowed; no hex, no
  inf/nan).
* Comparison: numeric if neither operand is a plain string, otherwise string
  comparison (numbers converted with `CONVFMT`).
* Number → string: integral values with |n| < 2^64 print as integers
  (like mawk/gawk), otherwise via `CONVFMT` (or `OFMT` for `print`).
  inf/nan render as `+inf`, `-inf`, `+nan`, `-nan` (as gawk 4.2+ and mawk do).
* String → number uses the longest numeric prefix (`"3x"` → 3).
* Array subscripts are strings; numeric subscripts go through the integer rule
  then `CONVFMT`.

## Lexer

* `/` starts a regex unless the previous token can end an operand
  (NAME, NUMBER, STRING, ERE, BUILTIN, `)`, `]`, `$`, `++`, `--`), in which
  case it is division. Inside a regex, a `/` within a bracket expression does
  not terminate it, and `\/` becomes `/`.
* Newlines are significant terminators, except after `,` `{` `&&` `||` `do`
  `else` (and runs of newlines collapse). Backslash-newline continues a line.
* `**`/`**=` are accepted as `^`/`^=` (common extension).
* A NAME immediately followed by `(` is a `FUNC_NAME`; calling one raises
  "user-defined functions are not supported yet".

## Parser

Recursive descent; precedence from lowest to highest:

```
= += -= *= /= %= ^=   (right)      ternary ?: (right)
||                                  &&
in                                  ~ !~
< <= != == > >=  (non-assoc), "cmd" | getline
concatenation                       + -
* / %                               unary ! + -
^ (right; RHS may be unary: 2^-1)   ++ -- (pre/post)
$                                   ( grouping )
```

* Concatenation only starts on tokens that cannot be binary operators, so
  `a -1` is subtraction and `a " " -1` is `a (" " - 1)` – exactly what
  POSIX's `non_unary_expr` grammar (and mawk) produce.
* Inside `print`/`printf` argument lists a bare `>` is output redirection
  (`no_gt` flag); parentheses re-enable comparison. The redirection target is
  parsed at concatenation level (like gawk), so `print 1 > "a" "b"` writes
  to `ab`; a `?:` target must be parenthesised.
* `(a, b)` is a `Group` node: legal before `in` and as a `print` list.
* `for (k in a)` is recognised by a 4-token lookahead.
* Lvalues: `Var`, `Index`, `Field`. Anything else on the left of `=`,
  `++`/`--` is a syntax error.

## Runtime

* Tree-walking interpreter with per-node-type dispatch dicts
  (`self._eval[type(node)]`). Control flow uses private exceptions
  (`_BreakEx`, `_ContinueEx`, `_NextEx`, `ExitEx`).
* Assignment targets are resolved once into a reference (`resolve` /
  `get_ref` / `set_ref`) so `a[i++] += 1`, `sub(/x/, "y", $(n++))` evaluate
  their subscripts exactly once. Compound assignments evaluate the right-hand
  side before reading the target (gawk/mawk/BWK agree: `x=1; x += x += 2` → 6).
* Records: fields are split lazily, using the FS in effect when the record
  was read (POSIX; changing FS affects the *next* record). Assigning a field
  rebuilds `$0` with `OFS`; assigning `NF` truncates/extends and rebuilds.
  In paragraph mode (`RS=""`) newline is always a field separator too.
* Input: `RecordReader` reads bytes with `read1` and decodes UTF-8
  incrementally (`surrogateescape`, so arbitrary bytes round-trip), and
  supports a single-char RS, paragraph mode and (bonus) regex RS. Plain
  `getline` shares the same reader as the main loop.
* `exit` in BEGIN or a main rule still runs END; `exit` in END stops.
  `exit` without an expression keeps an earlier exit status.
* `IOManager` owns output files/pipes and getline sources keyed by name;
  `close()` returns the command's exit status. At exit, redirections are
  closed first and stdout flushed after (matches mawk's observable order).
* Arithmetic: division/modulo by zero is a runtime error (like all awks);
  `%` is C `fmod`; `^` overflows to inf.

## Regular expressions

POSIX ERE is translated to Python `re`: bracket expressions are rebuilt
(`[:alpha:]` etc. classes, leading `]` literal, `[`, `\`, `&`, `~`, `|`
escaped), `$` → `\Z` (no match before a trailing newline), `.` matches newline
(DOTALL), `\/` → `/`, awk escapes (`\t`, octal `\101`, `\b`=backspace)
honoured, unknown letter escapes become literals. Dynamic regex strings are
compiled the same way and cached with `lru_cache`. Matching is leftmost; the
Python engine is backtracking (leftmost-*first*), not POSIX leftmost-*longest*
(see Known issues).

`sub`/`gsub` follow awk rules: `&` is the match, `\&` a literal `&`, `\\`
a backslash; in `gsub` an empty match is not allowed directly after a
previous match (`gsub(/x*/, "-", "abxd")` → `-a-b-d-`).

## Golden corpus and mawk

`.out` files were generated with **mawk 1.3.4 20250131** (the only awk on the
build box). Cases were written to avoid areas where mawk departs from POSIX
or where POSIX leaves behaviour unspecified. Known divergences, verified with
differential runs:

| Behaviour | POSIX / pyawk | mawk |
|---|---|---|
| `substr(s, 0, 2)`, negative start | chars at positions 1..1 (`"h"`) | counts from 1 (`"he"`) |
| `substr(s, 2.7)` non-integer start | rounds (gawk) → `"llo"` | truncates → `"ello"` |
| `length("héllo")` | characters: 5 | bytes: 6 |
| `"a\/b"` in a string | `a/b` (gawk, BWK) | keeps backslash `a\/b` |
| `RS=""` with `FS=":"` | newline is also a separator | newline not a separator |
| `CONVFMT="%d"; a[1.5]` subscript | `"1"` | garbage (`"100"`) |
| `printf` with too few arguments | missing args are empty/0 | fatal error |
| `printf "%5%"` | `%` | error |
| `index(s, "")` | 0 | 0 (consistent, but untested: unspecified) |
| `**`, `**=` | accepted | syntax error |
| Output ordering of `| cmd` vs stdout | pipes closed before stdout flush | same (tests `close()` explicitly anyway) |

When a second reference awk is available, `scripts/regen_golden.py --check
--awk gawk` shows where the corpus depends on implementation choices.

## Known issues / limitations

* Regex matching is leftmost-first (Python), not POSIX leftmost-longest:
  `match("ab", /a|ab/)` gives RLENGTH 1 instead of 2. Alternations where an
  earlier branch is a prefix of a later one are affected.
* Bracket equivalence classes `[=a=]` and collating symbols `[.a.]` are not
  supported; locale is effectively C/UTF-8.
* `getline` updating `NF/NR/FNR` follows POSIX; `getline < file` where file is
  the current input is treated as an independent stream.
* `srand()` seed is time-based in seconds; `rand()` uses Python's Mersenne
  Twister, so sequences differ from other awks.
* Performance: ~5 µs per simple record (≈30× slower than mawk). Fine for the
  test corpus; a closure-compilation pass is the planned fix.
