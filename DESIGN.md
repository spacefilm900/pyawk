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
* `$0` keeps the type of a value assigned to it: after `$0 = "0"` it is a
  plain string (true in a condition), although `$1` split from it is a
  strnum. A `$0` rebuilt from its fields (field or `NF` assignment) is a
  plain string too. All three references agree, except that mawk makes
  `$0` a strnum after an `NF` assignment (gawk tests `nfldstr`, `zeroe0`).
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
* A NAME immediately followed by `(` is a `FUNC_NAME` (a call). With a
  space, `f (x)` is a NAME followed by a parenthesised group, i.e. the
  concatenation of variable `f` and `(x)`; this is how POSIX tells calls
  from concatenation. `func` is not a keyword (see "User-defined functions").

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
  A Group as a whole pattern (`(/a/, /b/)`) or as the value of an
  assignment is a syntax error; elsewhere (a bare statement, an `if`
  condition, inside a larger expression) it is only caught when evaluated
  ("parenthesised expression list used as a value"); see Known issues.
* Range patterns `start, end`: the comma is *not* an operator. `parse_item`
  parses one full expression as the pattern and then looks for a `,`, so
  the comma binds more loosely than anything (`$1 == "a" || $2, /x/` means
  `($1 == "a" || $2), /x/`; likewise `?:` and `=`), and a range can never
  occur inside an expression or have a second comma. Newlines may follow
  the comma but not precede it (a newline ends the rule), and `BEGIN`/`END`
  cannot be endpoints. All three references agree on every one of these.
  The end condition is stored in `Rule.end` (None for an ordinary rule).
* `for (k in a)` is recognised by a 4-token lookahead.
* Lvalues: `Var`, `Index`, `Field`. Anything else on the left of `=`,
  `++`/`--` is a syntax error.
* Context checks while parsing: `return` only inside a function body,
  `break`/`continue` only inside a loop (`loop_depth`, reset to 0 for a
  function body, so a loop in the caller does not count), `next` not inside
  a function. All are syntax errors, as in the reference awks.
* Function definitions go into `Program.functions` (name → `Function`).
  Because a function may be used before it is defined, some checks run
  after the whole program is parsed (`Parser.check_functions`), using the
  list of every name used as a variable/array and every call site:
  a function name used as a variable or array (which also catches the
  call-with-a-space mistake `f (x)`), a parameter named like a function,
  and calls with more arguments than parameters (a warning, collected in
  `Program.warnings` and printed on stderr by the CLI).

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
  In paragraph mode (`RS=""`) newline is always a field separator too, when
  splitting records only: `split()` uses FS alone (all three references).
* Input: `RecordReader` reads bytes with `read1` and decodes UTF-8
  incrementally (`surrogateescape`, so arbitrary bytes round-trip), and
  supports a single-char RS, paragraph mode and (bonus) regex RS. Plain
  `getline` shares the same reader as the main loop.
* Range patterns: the open/closed state is run-time data, so it lives in
  the interpreter (`range_open`, one flag per main rule, indexed by the
  rule's position), not in the AST. `_in_range` implements the algorithm
  all three references share: while closed, test only the start condition;
  when it matches, open the range and test the end condition on the *same*
  record (so one record can be a whole range); while open, test only the
  end condition, and close after the record that matches it. The end test
  happens before the action runs, so `next`/`exit` in the action cannot
  keep a range open, while a record skipped by `next` in an earlier rule or
  consumed by `getline` is never tested. The flags are never reset, so a
  range continues across input files (POSIX; all three references agree).
* `exit` in BEGIN or a main rule still runs END; `exit` in END stops.
  `exit` without an expression keeps an earlier exit status.
* `IOManager` owns output files/pipes and getline sources keyed by name;
  `close()` returns the command's exit status (mawk and default gawk;
  onetrue-awk and gawk --posix return 0). The references split 2-2 and POSIX
  leaves it loose, so the user chose the exit status (2026-11-01): it lets a
  script check whether a command succeeded. The gawk test close_status stays
  skipped via `REVIEWED_SKIP` in scripts/import_gawk.py. At exit, redirections are
  closed first and stdout flushed after (matches mawk's observable order).
* Arithmetic: division/modulo by zero is a runtime error (like all awks);
  `%` is C `fmod`; `^` overflows to inf.

## User-defined functions

**Call frames.** The interpreter's `frame` attribute is the set of local
variables of the call now running: a dict from parameter name to `Cell`
(`None` outside any function). A call builds a new dict, makes it current,
runs the body and puts the caller's dict back (in a `finally`, so `exit`
and errors restore it too). The chain of saved dicts is the call stack;
Python's own call stack holds it, because calls nest as the interpreter
recurses. Variable access (`get_var`, `set_var`, `get_array`) looks in the
current frame first and falls back to the globals (`vars`, `arrays`). Only
the current frame is visible: AWK has no nested scopes, so a function
never sees its caller's locals.

**Cells and types.** AWK variables are untyped until first used, and a
parameter can become a scalar or an array depending on what the body does
with it. A `Cell` has three slots: `value` (scalar, once known), `array`
(a Python dict, once known) and `link` (see below). Reading or assigning
makes an untyped cell a scalar; subscripting, `in`, `for in`, `delete`
or `split` into it makes it an array. Using it the other way afterwards
is a runtime error ("can't use scalar x as array" / "can't use array x in
scalar context"), as in all three references. `return arr` is the same
error: functions return scalars only.

**Passing arguments** (`_bind_arg`), evaluated left to right in the caller:

| Argument | Callee's cell |
|---|---|
| any expression other than a bare name, including `a[i]` and `$1` | `value` = a copy (scalars are by value) |
| a name holding an array (global, or a local of the caller) | `array` = **the same dict**: arrays are by reference, so changes and deletes are seen by the caller |
| a name holding a scalar | `value` = a copy |
| a name never used yet (untyped) | `link` = the caller's Cell, or the global's name |

The `link` case implements a rule all three references agree on: if the
callee uses an untyped argument as an array, the caller's variable becomes
that array (`function f(a) { a[1] = 7 }  BEGIN { f(x); print x[1] }`
prints 7, also through several levels of calls and from a caller's local).
When the callee first uses the cell as an array, `_cell_array` follows the
link, creates the array in the caller (or reuses one) and shares it. If
the callee uses it as a scalar instead, the link is dropped and the caller's
variable stays untouched (scalars are by value). Missing arguments give fresh
untyped cells: these are the function's local variables, new on every call
(so recursive calls each get their own local arrays). Extra arguments are
evaluated for their side effects and dropped.

Reading an unset *global* now stores it as an (empty) scalar, so
`print x; x[1] = 1` and `print x; f(x)` (f using its parameter as an
array) are errors, as in all three references. `length(x)` is the
exception: it checks the type with `var_kind` without deciding it, so an
unused `x` stays untyped (onetrue-awk and mawk; gawk disagrees).

**return / exit.** `return` raises `_ReturnEx(value)`, caught by the call;
falling off the end returns the uninitialized value. `exit` inside a
function is the normal `ExitEx` and unwinds every call (END still runs
after an `exit` in BEGIN or a rule). Function lookup happens at call time,
so calling an undefined function is a runtime error only when executed.

**Recursion.** Each AWK call level costs about eight nested Python calls
(call → block → statement → expression → ...). When the program defines
functions, `Interpreter.run` raises Python's recursion limit to 400 000
and runs the program in a helper thread with a 1 GiB stack (thread stacks
are allocated lazily, so this costs memory only as it is used); this
gives AWK recursion around 40 000 levels deep for simple functions (gawk
and mawk manage 10 000; onetrue-awk crashes there). Runaway recursion ends
with "function call nesting too deep" instead of a crash.

**Reference disagreements and decisions.** Checked with onetrue-awk
20260426, `gawk --posix` 5.4.1 and mawk 1.3.4:

| Question | onetrue | gawk --posix | mawk | pyawk |
|---|---|---|---|---|
| `func` as a synonym of `function` | yes | no (ordinary name) | no | no; a helpful error if a program starts `func f(` |
| function name used as a variable (even in code never run), `f (x)` | runs | error | error | syntax error |
| parameter named like another function (POSIX forbids) | allowed | error | allowed | syntax error (POSIX + gawk; *not* the majority) |
| special variable as a parameter (POSIX forbids) | only `NF` rejected | all rejected | `NF FS RS CONVFMT OFMT` rejected | all rejected (POSIX + gawk; could be relaxed) |
| special variable as a function name | allowed | error | error | syntax error |
| `next` inside a function | syntax error | allowed | syntax error | syntax error |
| more arguments than parameters | warning, runs | warning, runs | fatal | warning on stderr, runs (extras are evaluated) |
| call of an undefined function | error when called | error when called | error at parse | runtime error when called |
| scalar variable passed, used as an array in the callee | runs | error | error | runtime error |
| `print x; f(x)` (f uses it as an array) | runs | error | error | runtime error |
| `length(x)` then `x[1] = 1` | runs | error | runs | runs |
| recursion depth 10 000 | crash | ok | ok | ok |

All three agree (and so does pyawk) that duplicate functions, duplicate
parameters, a parameter named like its own function, a builtin or keyword
name, `return` outside a function, and `break`/`continue` outside a loop
are syntax errors.

## Regular expressions

POSIX ERE is translated to Python `re`: bracket expressions are rebuilt
(`[:alpha:]` etc. classes, leading `]` literal, `[`, `\`, `&`, `~`, `|`
escaped), `$` → `\Z` (no match before a trailing newline), `.` matches newline
(DOTALL), `\/` → `/`, awk escapes (`\t`, octal `\101`, `\b`=backspace)
honoured, unknown letter escapes become literals. Dynamic regex strings are
compiled the same way and cached with `lru_cache`. Matching is leftmost; the
Python engine is backtracking (leftmost-*first*), not POSIX leftmost-*longest*
(see Known issues).

`sub`/`gsub` follow awk rules: `&` is the match and backslashes matter only
in front of `&`. In a run of backslashes followed by `&`, each pair is one
literal backslash and an odd one left over makes the `&` literal (`\&` → `&`,
`\\&` → `\` + match, `\\\&` → `\&`); a run not followed by `&` is copied
unchanged (`\\x` stays `\\x`). This is mawk's rule and the per-case majority:
onetrue-awk differs for four or more backslashes before `&`, and gawk --posix
halves runs not followed by `&` (gawk tests backgsub, posix2008sub). In `gsub` an empty match is not allowed directly after a
previous match (`gsub(/x*/, "-", "abxd")` → `-a-b-d-`).

## Test status

pytest: 706 passed, 123 skipped. Golden summaries: own corpus 99/99,
onetrue-awk 215 pass / 9 skip, gawk 190 pass / 114 skip, no failures and
empty `KNOWN_FAILURES` lists (2026-11-01). The last 18 gawk failures were
fixed except close_status, now a reviewed skip (see `close()` above).

Semantics settled by those fixes (all three references agree unless noted;
otherwise the majority wins):

* An assignment may be the right operand of `&&`, `||`, `~`, `!~` and the
  comparisons without parentheses (`a && $0 = $1`); after arithmetic,
  concatenation or unary operators it is a syntax error.
* `for (init; test; step)`: init and step may be `print`, `printf` or
  `delete` (onetrue-awk, gawk; mawk rejects). A bare `printf` prints nothing.
* `cmd | getline x y` is `(cmd | getline x) y`.
* `$` with a unary operator takes a unary expression: `$-i++` is
  `$(-(i++))`; a nested `$` takes its own `++`: `$$a++++` is `$($a++)++`.
* `^+`, `^*`, `^?` in a regex repeat the anchor (onetrue-awk, gawk; mawk
  rejects).
* getline resolves its target before reading, so `getline a[++c] < f`
  bumps c even at end of file (mawk, gawk; onetrue-awk does not).
* printf integer conversions follow C flag rules (`%#o`, `%+u`, `%.0d`...).
* A string assigned to `$0` stays a string, not a strnum.

## Golden corpus and mawk

`.out` files were generated with **mawk 1.3.4 20250131** (the only awk on the
build box). Cases were written to avoid areas where mawk departs from POSIX
or where POSIX leaves behaviour unspecified. Known divergences, verified with
differential runs:

| Behaviour | POSIX / pyawk | mawk |
|---|---|---|
| `substr(s, -1, 3)`, negative start with a length | start clamped to 1, length kept: `"hel"` (onetrue, gawk) | `"hell"` |
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
if mawk ever agrees). Currently: `builtins/undefined_escapes`,
`builtins/substr_clamp` (see substr below) and `functions/extra_args` (mawk rejects a call with too many arguments; the
other two warn and run it).

**Imported gawk suite.** `awk_tests/gawk/` trusts gawk's `.ok` files only
where two of the three references agree with them (see README). It
settled one open question: `substr` with a start below 1 (next section).

## substr

Decided by the user: follow the reference awks. onetrue-awk and
`gawk --posix` agree on every case tried (a grid of starts and lengths from
-3 to 7, fractions included); mawk differs only when a negative start is
combined with a length. pyawk follows the two:

* start and length are **truncated** toward zero (`substr("hello", 2.7)` is
  `"ello"`, `substr("hello", 1, 2.5)` is `"he"`);
* a start below 1 becomes 1 **without shortening the length**
  (`substr("hello", 0, 2)` is `"he"` in all three; `substr("hello", -1, 3)`
  is `"hel"`, mawk `"hell"`). A literal reading of POSIX (characters at
  positions m..m+n-1, which pyawk used before) would give `"h"` for both;
  no reference does that;
* a negative length gives `""`; a length past the end stops at the end.

For huge, infinite or NaN arguments onetrue-awk and mawk overflow a C
`int` (`substr("hello", 1, 1e30)` is `""` in both), so pyawk follows gawk:
`substr(s, 1, 1e30)` is all of `s`, a NaN start counts as 1, a NaN length
gives `""`. Tests: `tests/test_runtime.py`, golden `builtins/substr_clamp`
(an OVERRIDES case) and gawk `substr`.

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
* AWK recursion depth is limited (about 40 000 levels for a simple
  function, fewer for bodies with deeply nested statements); beyond that,
  a clean error. Function calls are fairly slow (a new dict and Cells per
  call; `return` is a Python exception).
* A parenthesised list `(a, b)` not followed by `in` is a parse error only
  as a whole pattern or an assigned value; in other places (`if ((a, b))`,
  `x = 1 + (a, b)`) it is a run-time error when reached, whereas the
  reference awks reject the program at parse time.
* Performance: ~5 µs per simple record (≈30× slower than mawk). Fine for the
  test corpus; a closure-compilation pass is the planned fix.
