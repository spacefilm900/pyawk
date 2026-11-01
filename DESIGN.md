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
* Fields past `NF` (`$5` on a 3-field line, `$1` on an empty line) are the
  plain empty string `""`, not `UNINIT`; so are the gap fields created by
  `$7 = x` or `NF = 9`. Thus `$5 == 0` is a *string* comparison and false,
  just as for an existing empty field. POSIX calls such fields "the
  uninitialized value", which would make `$5 == 0` true, but onetrue-awk,
  gawk (also `--posix`) and mawk all agree on the empty string, and the
  onetrue test `t.null0` checks it; we follow the implementations.
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
* Escapes in strings (`lexer.process_escapes`, also used for `-v`, operand
  assignments and `-F`): `\"` `\\` `\/` `\a` `\b` `\f` `\n` `\r` `\t` `\v` and up to
  three octal digits. See "Undefined escapes in strings" below for the rest.

### Undefined escapes in strings

POSIX leaves a backslash before any other character undefined. pyawk
**drops the backslash and keeps the character** (`"\q"` is `q`, length 1;
`"\8"` is `8`); a lone backslash at the very end of a `-v`/operand value is
kept. This is the majority rule of the three references, checked with
onetrue-awk 20260426, `gawk --posix` 5.4.1 (which warns "escape sequence
treated as plain") and mawk 1.3.4, which keeps the backslash:

| Program | onetrue, gawk, pyawk | mawk |
|---|---|---|
| `length("\q")` | `1` | `2` |
| `s="foo"; sub(/o/, "\&", s)` | `foo` (`&` = match) | `f&o` |
| `sub(/o/, "\\&", s)` | `f&o` (literal `&`) | `f&o` |
| `sub(/o/, "\\\\&", s)` | `f\oo` | `f\oo` |
| `sub(/o/, "[\&]", s)` | `f[o]o` | `f[&]o` |
| `"axb" ~ "a\.b"` (dynamic regex) | `1` (`\.` → `.`) | `0` |
| `"axb" ~ "a\\.b"` | `0` | `0` |
| `"a" ~ "a\$"` | `1` (`\$` → `$` anchor) | `0` |
| `"a(" ~ "a\("` | error: unbalanced `(` | `1` |
| `-v 'v=\&'`, operand `w=\&` | `&` | `\&` |
| `-F 'b\.c'`, `FS="b\.c"` on `abxcd` | NF = 2 | NF = 1 |
| regex literals `/a\.b/`, `/\&/`, `/\q/` | unaffected (see below) | same |

Consequences worth knowing: in a `sub`/`gsub` replacement `"\&"` therefore
means the matched text, and you need `"\\&"` for a literal `&` (as POSIX
programs always should). A string used as a dynamic regex is unescaped
*twice* (once as a string, then as a regex), so `"\."` is regex `.` and a
literal dot needs `"\\."` or a regex literal `/\./`. Regex literals never go
through string escaping: `regex.py` handles their escapes. onetrue's
`t.sub0` exercises the `sub` case.
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
| `"\q"`, `"\&"`, `"a\.b"`: undefined escapes | backslash dropped (gawk, BWK) | backslash kept (see Lexer) |
| `RS=""` with `FS=":"` | newline is also a separator | newline not a separator |
| `CONVFMT="%d"; a[1.5]` subscript | `"1"` | garbage (`"100"`) |
| `printf` with too few arguments | missing args are empty/0 | fatal error |
| `printf "%5%"` | `%` | error |
| `index(s, "")` | 0 | 0 (consistent, but untested: unspecified) |
| `**`, `**=` | accepted | syntax error |
| Output ordering of `| cmd` vs stdout | pipes closed before stdout flush | same (tests `close()` explicitly anyway) |

When a second reference awk is available, `scripts/regen_golden.py --check
--awk gawk` shows where the corpus depends on implementation choices.

**Hand-corrected cases.** When we deliberately follow onetrue-awk and gawk
against mawk and want a golden case for it, its `.out` is written from
onetrue-awk (checked against `gawk --posix`) and the case is listed in
`awk_tests/golden/OVERRIDES` (`CATEGORY/NAME  reason`). `regen_golden.py`
never rewrites a listed `.out`, and `--check` reports it as `KEEP ...
differs (expected)` rather than counting it (or suggests removing the entry
if mawk ever agrees). Currently: `builtins/undefined_escapes`.

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
