# pyawk design notes

## Pipeline

```
source --lexer.py--> tokens --parser.py--> ast.py nodes --compiler.py--> closures --runtime.py--> output
                                                          builtins.py, values.py, regex.py
```

The AST is not interpreted directly: `compiler.py` first turns every node
into a small Python function (a *closure*, see "Runtime"), once, before the
program runs; the runtime then only calls those functions.

| Module | Responsibility |
|---|---|
| `errors.py` | `AwkError` → `AwkSyntaxError` (with source line), `AwkRuntimeError`, `AwkUnsupportedError` (deferred features; nothing raises it since `nextfile` was added, but the importers still catch it) |
| `values.py` | value model, string↔number conversion, comparison rules |
| `lexer.py` | tokens, regex-vs-division, newline rules, string escapes |
| `ast.py` | slotted dataclasses for expressions, statements, rules |
| `parser.py` | recursive-descent parser, one method per precedence level |
| `regex.py` | POSIX ERE → Python `re` translation, cached compilation (extra module, keeps runtime small) |
| `compiler.py` | `Compiler`: AST → closures (expressions, conditions, statements, function calls, lvalue references) |
| `runtime.py` | `Interpreter` (main loop, records and fields, variables), record reader, I/O manager |
| `builtins.py` | builtin functions (each compiles its own call), `printf` formatting, `sub/gsub` engine |
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
< <= != == > >=  (non-assoc), "cmd" | getline (left operand)
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
* `cmd | getline [var]` takes a concatenation as the command, but once
  read, the whole `cmd | getline var` is an ordinary operand
  (`Parser.parse_operand_tail`): `cmd | getline x + 1` is
  `(cmd | getline x) + 1`, likewise `*`, `%`, `^` and concatenation. This
  is POSIX's `unary_expr '|' simple_get` and what mawk and gawk do;
  onetrue-awk rejects `* / % ^` there and reads `+ 1` as part of the target.
* `for (k in a)` is recognised by a 4-token lookahead.
* Lvalues: `Var`, `Index`, `Field`. Anything else on the left of `=`,
  `++`/`--` is a syntax error.
* Context checks while parsing: `return` only inside a function body,
  `break`/`continue` only inside a loop (`loop_depth`, reset to 0 for a
  function body, so a loop in the caller does not count), `next` not inside
  a function, `nextfile` not inside a function or a BEGIN/END action
  (`in_special` records which of BEGIN/END is being parsed). All are syntax errors, as in the reference awks.
* Function definitions go into `Program.functions` (name → `Function`).
  Because a function may be used before it is defined, some checks run
  after the whole program is parsed (`Parser.check_functions`), using the
  list of every name used as a variable/array and every call site:
  a function name used as a variable or array (which also catches the
  call-with-a-space mistake `f (x)`), a parameter named like a function,
  and calls with more arguments than parameters (a warning, collected in
  `Program.warnings` and printed on stderr by the CLI).

## Runtime

* **Compiled to closures.** `Compiler.compile_program` walks the AST once
  and returns a `CompiledProgram`: BEGIN blocks, `(pattern, range end,
  action)` rules and END blocks, all as Python closures. A closure is an
  inner function that keeps the values it was built with (its operands,
  the constant, the variable name), so running `x + 1` is one call that
  does exactly that addition, with no look-up of node types or operators.
  The first pyawk walked the tree on every evaluation instead; that
  tree-walker has been removed. There are three kinds of closure:
  *expressions* (`Thunk`) return an AWK value; *conditions* (`CondFn`)
  return a Python bool, for `if`, loops, patterns, `&&`, `||`, `!`;
  *statements* (`StmtFn`) return None or a signal.
* **Control flow as return values.** `break`, `continue`, `next`,
  `nextfile` and `return` make a statement return a signal object (`BREAK`,
  `CONTINUE`, `NEXT`, `NEXTFILE`, `RETURN`; the return value travels in `Interpreter.ret_value`).
  Blocks and loops pass on signals they do not handle. The parser
  guarantees these signals never have to cross an expression. `exit` and
  run-time errors are still exceptions (`ExitEx`, `AwkRuntimeError`)
  because they can happen inside a function called from an expression.
* **Specialisation.** The compiler picks a cheaper closure when it can
  see the case at compile time: `$3` (a constant field number), `x + 1`
  and `x % 7` (a constant operand), comparisons with a number constant,
  `i++` and `x += e` as statements (no result needed), global versus
  local variables, `sub`/`gsub` on `$0`, and *string-only* uses of a
  field (array subscripts, `length`, `~`, concatenation, `index`...),
  which read the field's text without the numeric-string check.
  Builtins are compile-time factories (`BUILTINS[name](compiler, args)`)
  that return the closure for one call site.
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
  script check whether a command succeeded. The gawk test close_status runs
  under this decision (`DECIDED` in scripts/import_gawk.py; its expected
  output is gawk's `.ok`, which mawk matches), and the golden case
  `io/close_status` is listed in `awk_tests/golden/DECISIONS`. At exit,
  redirections are closed first and stdout flushed after (matches mawk's
  observable order). The full I/O behaviour is in "ENVIRON, I/O and
  nextfile" below.
* Arithmetic: division/modulo by zero is a runtime error (like all awks);
  `%` is C `fmod`; `^` overflows to inf.

## User-defined functions

**Call frames.** The interpreter's `frame` attribute holds the local
variables of the call now running: a Python list of `Cell`s, one per
parameter (`None` outside any function). Parameter names are known when a
function is compiled, so each gets a fixed position and a local is read as
`frame[position]`, with no look-up by name; every other name is a global,
in `vars` (scalars) or `arrays`. A call builds a new list, makes it
current, runs the body and puts the caller's list back (in a `finally`,
so `exit` and errors restore it too). The chain of saved lists is the call
stack; Python's own call stack holds it, because calls nest as the
compiled closures call each other. Only the current frame is visible: AWK
has no nested scopes, so a function never sees its caller's locals.
(`Interpreter.get_var`, `set_var`, `get_array` and `var_kind` now see
globals only; compiled code reaches locals through its frame positions.)

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

**return / exit.** `return` stores its value in `Interpreter.ret_value`
and returns the `RETURN` signal, which the call turns back into the value;
falling off the end returns the uninitialized value. `exit` inside a
function is the normal `ExitEx` and unwinds every call (END still runs
after an `exit` in BEGIN or a rule). Function lookup happens at call time,
so calling an undefined function is a runtime error only when executed.

**Recursion.** Each AWK call level costs about four nested Python calls
(call closure → block → statement → expression; the tree-walker needed
about eight). When the program defines functions, `Interpreter.run`
raises Python's recursion limit to 400 000 (`RECURSION_LIMIT`) and runs
the program in a helper thread with a 1 GiB stack (`THREAD_STACK_SIZE`;
thread stacks are allocated lazily, so this costs memory only as it is
used). Measured on 2026-11-01 with
`function r(n) { if (n == 0) return 0; return 1 + r(n - 1) }`: 99 900
levels work and 100 000 fail cleanly, so AWK recursion goes about
**100 000 levels** deep for simple functions (before the compiler: about
66 500), fewer for bodies with deeply nested statements. gawk and mawk
manage 10 000; onetrue-awk crashes there. The limit is Python's recursion
limit, not the stack: runaway recursion ends with "function call nesting
too deep" instead of a crash.

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

## Performance

**How to measure.** `scripts/bench.py` runs nine workloads (below) on a
generated, seeded 200 000-line input with pyawk, mawk, `gawk --posix` and
onetrue-awk, keeps the best of three wall-clock runs and checks every
output against mawk's (or, without mawk, the first reference awk found).
The reference awks are looked up by name on PATH (`mawk`, `gawk`, and
`original-awk`/`onetrue-awk`/`bwk-awk`); only those found are timed, and
`--path NAME=PROGRAM` points at one that is not on PATH. `--save LABEL`
stores a run in `.bench/` (ignored by git) and `--compare LABEL` adds a
speedup column against a saved run.

```sh
.venv/bin/python scripts/bench.py --save baseline          # before a change
.venv/bin/python scripts/bench.py --compare baseline       # after it
.venv/bin/python scripts/bench.py --only fields,funcs --awks pyawk
.venv/bin/python scripts/bench.py --path gawk=/opt/gawk/bin/gawk --path onetrue=./a.out
```

| workload | program (abridged) | exercises |
|---|---|---|
| print1 | `{ print $1 }` | record reading, field split, output |
| sum | `{ s += $3 }` | numeric fields |
| fields | `for (i = 1; i <= NF; i++) t += length($i)`; `$2 = "X"` | field loop, field assignment, `$0` rebuild |
| regex | `/[0-9]+5\.\|qu+x.*thud/ { c++ }`, `$2 ~ /.../`, `$5 !~ /a/` | regex matching |
| wordcount | `for (i = 1; i <= NF; i++) cnt[$i]++` | associative arrays |
| printf | `printf "%-10s\|%8.2f\|%5d\|%x\n", ...` | printf |
| gsub | `gsub(/[aeiou]/, "<&>"); sub(/^[0-9]+/, "#")` | sub/gsub on `$0` |
| funcs | `s += f($4)` per record, plus `fib(22)` | user function calls, recursion |
| begin_loop | a 1 000 000-iteration BEGIN loop with `%`, `==`, concatenation | the evaluator with no input |

**Before and after** (2026-11-01; seconds, best of 3, 200 000 lines;
8-core box, Python 3.13.5; mawk 1.3.4, gawk 5.4.1 `--posix`, onetrue-awk).
"Before" is commit 32d9e5e (the tree-walker), "after" is the current code.

| workload | pyawk before | pyawk after | speedup | mawk | gawk | onetrue | after ÷ mawk | before ÷ mawk |
|---|---|---|---|---|---|---|---|---|
| print1 | 1.213 | 0.353 | 3.44× | 0.041 | 0.028 | 0.125 | 8.7× | 29× |
| sum | 1.228 | 0.379 | 3.24× | 0.047 | 0.040 | 0.118 | 8.1× | 25× |
| fields | 6.353 | 1.867 | 3.40× | 0.122 | 0.212 | 0.280 | 15.3× | 52× |
| regex | 1.608 | 0.560 | 2.87× | 0.145 | 0.507 | 0.322 | 3.9× | 11× |
| wordcount | 5.999 | 1.565 | 3.83× | 0.332 | 0.592 | 0.445 | 4.7× | 13× |
| printf | 2.441 | 0.837 | 2.92× | 0.144 | 0.199 | 0.248 | 5.8× | 16× |
| gsub | 2.921 | 0.812 | 3.60× | 0.148 | 0.585 | 0.353 | 5.5× | 19× |
| funcs | 2.066 | 0.459 | 4.50× | 0.048 | 0.053 | 0.139 | 9.7× | 41× |
| begin_loop | 3.179 | 0.795 | 4.00× | 0.174 | 0.095 | 0.407 | 4.6× | 18× |

(The reference awks' times are from the "after" run; the "before" run
measured them within about 8 % of these, except wordcount, where all
three were 15–35 % slower that time. The "before ÷ mawk" column uses the
"before" run's own mawk time.)

**What made the difference**, in order of the commits:

1. *Lazy numeric strings.* Fields were all classified as strnums (does
   the text look like a number?) when the record was split; now each is
   classified the first time it is read as a value, and many never are.
2. *Closures instead of a tree-walker* (the big one; see "Runtime"): no
   per-evaluation dispatch on node type, signals instead of exceptions for
   `break`/`next`/`return`, local variables by list position instead of
   by dict look-up. This also halved the Python frames per AWK call, so
   recursion goes deeper.
3. *Specialised closures* for common shapes: `x op= e`, `$i`, `i++` as a
   statement, `/` and `%` by a nonzero constant, comparisons with
   constants, assignment statements that never build a result value, and
   string-only uses of a field.
4. *printf*: each format string is parsed once and cached; when every
   conversion behaves like C's, the whole format goes through a single
   Python `%` operation.
5. *sub/gsub*: `re.subn` does the whole substitution in C when the
   pattern cannot match the empty string; `$0` as the target is read and
   written directly.
6. *Records*: with the default FS, `str.split()` splits the record (it
   agrees with AWK's rule when the text has no unusual whitespace, which
   is checked); a one-character RS is read directly; `$0` is rebuilt from
   a list, converting only fields that are not already strings.

Numbers now use ASCII digits only (`"٣"` is no longer the number 3), as
in all three reference awks; Python's `float()` had accepted any Unicode
digit.

**After the enhancements branch** (ENVIRON tests, I/O fixes, `nextfile`;
the main loop now checks for a `NEXTFILE` signal). Run on the same box,
best of 5, alternating v1.0 and the branch (two runs of v1.0, three of the
branch, best of each):

| workload | DESIGN baseline | v1.0 now | enhancements | vs v1.0 now |
|---|---|---|---|---|
| print1 | 0.353 | 0.365 | 0.364 | -0.3 % |
| sum | 0.379 | 0.397 | 0.397 | 0.0 % |
| fields | 1.867 | 1.925 | 1.936 | +0.6 % |
| regex | 0.560 | 0.573 | 0.588 | +2.6 % |
| wordcount | 1.565 | 1.645 | 1.550 | -5.8 % |
| printf | 0.837 | 0.862 | 0.865 | +0.3 % |
| gsub | 0.812 | 0.832 | 0.843 | +1.3 % |
| funcs | 0.459 | 0.470 | 0.474 | +0.9 % |
| begin_loop | 0.795 | 0.822 | 0.804 | -2.2 % |

The box itself ran 2–5 % slower than when the baseline was recorded (the
"v1.0 now" column: same code as the baseline); against v1.0 measured
now, the branch is within noise (at most +2.6 %, within the 5 % budget).
Against the recorded baseline it is at most +5.0 % (wordcount best
run 1.550 is faster; regex 0.588 vs 0.560), all of it machine drift.

**Where the time goes now** (cProfile on the workloads):

* the evaluator's own overhead: one Python call per AST node per
  evaluation, most visible in tight loops (`for (i = 1; i <= NF; i++)`
  re-reads `NF` and compares through generic closures every iteration);
* turning field text into strnums (`make_strnum`) and reading fields
  (`field_var`);
* user function calls: argument binders, a new frame list and a
  `try`/`finally` per call;
* field assignment and the `$0` rebuild that follows;
* record reading and splitting;
* the C cost of the regex engine itself (`re.search`, `re.subn`), which
  pyawk cannot reduce.

**Ideas not yet done:** specialise `i <= NF`-style loop conditions and
whole counting `for` loops; read records in batches when RS is `"\n"`;
cheaper frames for functions whose parameters are all scalars; compile a
printf whose format is a constant string directly at compile time; a fast
path for `sub`/`gsub` with a constant replacement containing no `&`.

## Test status

pytest: 803 passed, 122 skipped. Golden summaries: own corpus 129/129
(nine v1.0 categories with 99 cases, plus environ 5, io 18, nextfile 7),
onetrue-awk 215 pass / 9 skip, gawk 191 pass / 113 skip, no failures and
empty `KNOWN_FAILURES` lists (2026-11-01, `enhancements` branch). v1.0 had
744 / 99 / 215 / 190; the extra gawk case is close_status (see `close()`
above).

Semantics settled by those fixes (all three references agree unless noted;
otherwise the majority wins):

* An assignment may be the right operand of `&&`, `||`, `~`, `!~` and the
  comparisons without parentheses (`a && $0 = $1`); after arithmetic,
  concatenation or unary operators it is a syntax error.
* `for (init; test; step)`: init and step may be `print`, `printf` or
  `delete` (onetrue-awk, gawk; mawk rejects). A bare `printf` prints nothing.
* `cmd | getline x y` is `(cmd | getline x) y` (and `cmd | getline x + 1`
  is `(cmd | getline x) + 1`; see Parser).
* `$` with a unary operator takes a unary expression: `$-i++` is
  `$(-(i++))`; a nested `$` takes its own `++`: `$$a++++` is `$($a++)++`.
* `^+`, `^*`, `^?` in a regex repeat the anchor (onetrue-awk, gawk; mawk
  rejects).
* getline resolves its target before reading, so `getline a[++c] < f`
  bumps c even at end of file (mawk, gawk; onetrue-awk does not).
* printf integer conversions follow C flag rules (`%#o`, `%+u`, `%.0d`...).
* A string assigned to `$0` stays a string, not a strnum.

## ENVIRON, I/O and nextfile

Work on the `enhancements` branch after v1.0, in this order: ENVIRON, an
audit of the I/O features, then `nextfile`. Every behaviour below was run
under pyawk and the three references (mawk, onetrue-awk, `gawk --posix`);
the majority wins, and two-way splits are listed under "Open decisions".

### Test infrastructure

* **Sandboxed golden runs.** Each golden case runs in a throwaway copy of
  its category directory, so a program may create, append to and read back
  files with relative names without touching the repository.
* **Optional per-case files.** `NAME.environ` (`NAME=value` lines added to
  the environment), `NAME.operands` (the operands to use instead of
  `NAME.in`; may name other cases' `.in` files and `-`), `NAME.stdin`
  (standard input). `scripts/make_corpus.py` writes them all.
* **`regen_golden.py --vote CAT/NAME...`** runs the named cases under all
  three references and reports which agree with the `.out` (OK), which
  case has no majority, and which is DECIDED.
* **`awk_tests/golden/DECISIONS`** lists cases whose `.out` follows a
  project decision rather than the majority (currently `io/close_status`).
  OVERRIDES remains for cases where mawk is the outlier.
* **`DECIDED` in scripts/import_gawk.py** does the same for imported gawk
  tests: the case runs with gawk's `.ok` as the expected output, and the
  importer stops if mawk (whose answer was chosen) ever disagrees.
* Golden runs compare stdout only, so exit codes and error messages
  (SIGPIPE, syntax errors) are covered by unit tests in `tests/test_io.py`
  and `tests/test_parser.py`.

### ENVIRON

ENVIRON already existed in v1.0 (`Interpreter.__init__` copies
`os.environ`, every value a strnum candidate); this round added tests: the
golden `environ` category (values printed verbatim with no escape
processing, numeric-looking values comparing as numbers, array operations,
use in main rules, and child processes seeing the original environment
even after ENVIRON is changed, on which all three references agree). The
static ENVIRON skips in both importers were removed; no imported test
was waiting on it (gawk's funstack, which had been skipped for ENVIRON, is
now skipped as an error-message test).

### I/O checklist

Each item: what was checked, and the result. "Fixed" items were wrong
before this round and have a commit and tests.

| # | Item | Result |
|---|---|---|
| 1 | `getline` / `getline var` from the main input: sets `$0`/NF or var, NR, FNR; 1 / 0; crosses files | correct |
| 2 | `getline < file`, `getline var < file`: NR/FNR unchanged; -1 for an unreadable file | correct |
| 3 | `cmd \| getline [var]`: NR left alone (all three references, against POSIX's text) | **fixed** (6d9af6e) |
| 4 | getline in BEGIN reads the first operand; FILENAME set by the main rules | correct |
| 5 | getline from `-` and `/dev/stdin` | correct |
| 6 | `>` truncates once per name, `>>` appends; one name is one stream | correct |
| 7 | `print \| cmd`: output reaches the command; `close()` waits for it | correct |
| 8 | `close()` return values: 0, -1 for a name never opened, exit status of a command, 256 + N for a signal | correct (exit status is the user decision) |
| 9 | `close()` of a command does not flush our stdout first (mawk, onetrue-awk) | **fixed** (b59880b) |
| 10 | At exit, commands finish before our buffered stdout is flushed (mawk, gawk) | **fixed** (b59880b) |
| 11 | `system()` flushes our output first and returns the exit status (256 + N for a signal) | correct |
| 12 | `fflush()` / `fflush("")` flush everything; `fflush(name)` only that output; -1 if name is not open for output | **fixed** (4f1b198) |
| 13 | `/dev/stdout`, `/dev/stderr` as output names | correct |
| 14 | `print > "-"` writes a file named `-` (mawk, onetrue-awk; gawk: stdout) | **fixed** (55f42ce) |
| 15 | Empty file or command name in getline or a redirection: fatal error (onetrue-awk, gawk; mawk accepts some forms) | **fixed** (98dac34) |
| 16 | Reader of our output goes away: die by SIGPIPE (exit 141, no message), also at the final flush | **fixed** (59869ed) |
| 17 | Redirection target is a concatenation: `print > "a" "b"` writes `ab`; `print > $1".x"` | correct |
| 18 | `"cmd" \| getline > 0` is `("cmd" \| getline) > 0`; `getline < "f" "1"` is `(getline < "f") "1"` (mawk, gawk) | correct |
| 19 | `"echo " "hi" \| getline` runs `echo hi` (onetrue-awk, gawk; OVERRIDES) | correct |
| 20 | `cmd \| getline x + 1` is `(cmd \| getline x) + 1`, also `* / % ^` (mawk, gawk) | **fixed** (0137923) |

Golden category `io` (18 cases) covers items 1–14 and 17–19; items 15, 16
and 20 are unit tests.

### nextfile

POSIX 2024 `nextfile` stops reading the current input file; the next
record read is the first of the next file, with FNR back to 1 and FILENAME
updated. Rules after the current one do not run for this record. NR counts
only the records actually read. Implementation: the statement returns the
`NEXTFILE` signal; the main loop calls `Interpreter.skip_file()`, which
closes the current file (for standard input it marks the reader as at end
of input, without draining it) and leaves the next operand to be opened
as usual. Range patterns are not reset (they never are between files);
getline from a separate file is unaffected; operand assignments between
files still happen.

* In BEGIN or END: a syntax error (mawk, gawk; onetrue-awk fails at run
  time, after printing earlier output).
* Inside a function: a syntax error (mawk, onetrue-awk; gawk allows it).
* On standard input: a later `-` operand or `getline < "-"` finds nothing
  (mawk, gawk; onetrue-awk carries on reading). pyawk does not drain stdin.

Golden category `nextfile` (7 cases); unit tests in `tests/test_io.py` and
`tests/test_parser.py`.

### Open decisions

Two-way or three-way splits where pyawk made a choice that may deserve a
second look:

* `close("/dev/stdout")` / `close("/dev/stderr")`: three different answers;
  pyawk returns -1 (not open as a redirection) and keeps writing.
* Several commands still open at exit: the order in which they finish is
  nondeterministic in every awk; tests close them explicitly.
* `getline < "-"` then `getline < "/dev/stdin"` in BEGIN with no operands:
  mawk and gawk lose lines to their own buffering; pyawk shares one reader.
* onetrue-awk flushes stdout before every getline; not adopted (mawk, gawk
  do not).
* ENVIRON values like `0x1A`: `gawk --posix` treats them as numbers, mawk
  and onetrue-awk (and pyawk) do not.
* `close()` of a command returns its exit status: user decision (mawk and
  default gawk; onetrue-awk and `gawk --posix` return 0).
* `nextfile` on stdin (above): mawk, given a large regular file as
  stdin, continues from what it has buffered; pyawk always reads nothing.
* `nextfile` in a function: gawk allows it; pyawk follows mawk and
  onetrue-awk.
* `cmd | getline x + 1` and friends: onetrue-awk differs (above).
* Not changed, out of scope: `next` in BEGIN/END is still a run-time
  error in pyawk (like onetrue-awk, after earlier output is printed);
  mawk and gawk reject it at parse time, as pyawk now does for `nextfile`.

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
| Output ordering of `\| cmd` vs stdout | pipes closed before stdout flush | same (tests `close()` explicitly anyway) |

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
* AWK recursion depth is limited (about 100 000 levels for a simple
  function, fewer for bodies with deeply nested statements); beyond that,
  a clean error.
* A parenthesised list `(a, b)` not followed by `in` is a parse error only
  as a whole pattern or an assigned value; in other places (`if ((a, b))`,
  `x = 1 + (a, b)`) it is a run-time error when reached, whereas the
  reference awks reject the program at parse time.
* Performance: roughly 4–15× slower than mawk and 1–13× slower than
  `gawk --posix` on the benchmark workloads (gawk is itself slow on the
  regex and gsub ones, where pyawk is only 1.1× and 1.4× behind; see
  "Performance").
