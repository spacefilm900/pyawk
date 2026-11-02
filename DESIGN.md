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
| `regex.py` | POSIX ERE parser; Python `re` for yes/no tests, a leftmost-longest NFA/lazy-DFA engine for match spans; cached compilation |
| `compiler.py` | `Compiler`: AST → closures (expressions, conditions, statements, function calls, lvalue references) |
| `runtime.py` | `Interpreter` (main loop, records and fields, variables), record reader, I/O manager |
| `builtins.py` | built-in functions (each compiles its own call), `printf` formatting, `sub/gsub` engine |
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
  Anywhere else (a whole pattern, an assignment's value, an `if`
  condition, inside a larger expression, or wrapped in extra parentheses
  as in `((a, b))`) it is a syntax error, as in all three reference awks,
  even in code that never runs. Because `in` comes *after* the list, the
  parser records each Group as it builds it, crosses it off when `in` or
  `print` claims it, and reports any left over at the end of the rule or
  function (`check_groups`).
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
  function body, so a loop in the caller does not count), `next` and
  `nextfile` not in a BEGIN/END action (`in_special` records which of
  BEGIN/END is being parsed), and `nextfile` not inside a function. All
  are syntax errors, as in the reference awks (see "Parse-time errors"
  below). `next` *is* allowed in a function: POSIX restricts it only
  when "invoked in a BEGIN or END action", and gawk agrees. The function
  body's NEXT signal becomes a `NextFromFunction` exception (the call may
  be inside an expression), which the main loop catches to skip the
  record; from BEGIN or END it is a run-time error, as in gawk.
* Built-in calls: the argument count is checked against `builtins.ARITY`
  (`sprintf()` with no format included); `split`'s second argument must
  be a plain name; the third argument of `sub`/`gsub` must be a variable,
  element or field and not parenthesised (`(x)` is a value in all three
  references; `parse_call_args` records the first token of each argument
  to tell `x` from `(x)`, which make the same tree).

### Parse-time errors

Mistakes the references reject while parsing, so the program never
starts, even when the faulty code could never run (`if (0) ...`).
Decided by POSIX where it is clear, otherwise by the majority of
onetrue-awk, gawk --posix and mawk:

| Mistake | POSIX | onetrue | gawk --posix | mawk | pyawk |
|---|---|---|---|---|---|
| `next` in BEGIN/END | undefined | run-time error | parse error | parse error | parse error |
| `nextfile` in BEGIN/END | undefined | run-time error | parse error | parse error | parse error |
| `next` in a function | allowed (only "invoked in BEGIN or END" is undefined) | parse error | allowed; fatal if called from BEGIN/END | parse error | allowed (POSIX); run-time error if called from BEGIN/END |
| `nextfile` in a function | undefined | parse error | allowed | parse error | parse error |
| `(a, b)` not before `in`, not a print list, or `((a, b))` | grammar does not allow it | parse error | parse error | parse error | parse error |
| wrong built-in argument count (`substr("a")`, `index(1)`, `close()`, `sprintf()`, `match(s, r, m)`) | grammar / function definitions | parse error | parse error | parse error | parse error |
| wrong count for `length(1, 2)`, `rand(1)`, `int()`, `atan2(1)`, `sin()`, `fflush(1, 2)`, `srand(1, 2)` ... | function definitions | warning only | parse error | parse error | parse error |
| `split(s, x[1])`, `split(s, 3)`, `split(s, (a))` | "array" argument | parse error | run-time error | parse error | parse error |
| `sub(/a/, "b", "c")`, `1 + 2`, `x y`, `(x)`, `x++`, `length` as target | undefined | parse error | parse error for `x y`, `(x)`, `length`; else runs | parse error | parse error |

Left as run-time errors because only mawk rejects them while parsing:
a bare `printf`, a call of an undefined function, and scalar/array
misuse (`x = 1; x[1] = 2`, a local array assigned as a scalar).
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
  Built-ins are compile-time factories (`BUILTINS[name](compiler, args)`)
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
* Input: `RecordReader` reads bytes with `read1` and decodes them
  incrementally with the locale's codec (Latin-1 in the C locale, UTF-8
  with `surrogateescape` in a UTF-8 locale; either way arbitrary bytes
  round-trip, see "Locales and UTF-8"), and
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
| `next` inside a function | syntax error | allowed | syntax error | allowed (POSIX; see "Parse-time errors") |
| more arguments than parameters | warning, runs | warning, runs | fatal | warning on stderr, runs (extras are evaluated) |
| call of an undefined function | error when called | error when called | error at parse | runtime error when called |
| scalar variable passed, used as an array in the callee | runs | error | error | runtime error |
| `print x; f(x)` (f uses it as an array) | runs | error | error | runtime error |
| `length(x)` then `x[1] = 1` | runs | error | runs | runs |
| recursion depth 10 000 | crash | ok | ok | ok |

All three agree (and so does pyawk) that duplicate functions, duplicate
parameters, a parameter named like its own function, a built-in or keyword
name, `return` outside a function, and `break`/`continue` outside a loop
are syntax errors.

## Regular expressions

`regex.py` parses each POSIX ERE into a small syntax tree (character sets,
concatenation, alternation, repetition, the anchors `^`/`$`, and "empty").
Everything else is generated from that tree.

**Two engines.** POSIX asks for the *leftmost-longest* match: take the
first position where a match can start, and the longest match starting
there. Python's `re` is a backtracking matcher. It finds the same start,
but its match is the first one its search order reaches, so it can be
shorter (`match("xabc", /a|ab/)` must give RLENGTH 2, not 1). pyawk
therefore keeps two compiled forms of each pattern (`_compile`, cached with
`lru_cache(512)`):

* `compile_ere` gives the form used for yes/no tests (`~`, `!~`, bare
  `/re/` patterns), where only *whether* there is a match matters. That is
  the Python pattern, unless backtracking could explode (see "No
  catastrophic backtracking" below); then it is a `LongestPattern` with
  no Python pattern at all.
* `compile_span` serves the uses where the extent of the match matters:
  `match()`, `sub`/`gsub`, regex FS and `split()`, and regex RS.
  * It returns the same Python pattern when `_safe` proves that Python's
    match is already the longest. That covers a fixed-length pattern
    (`abc`, `[0-9]`, `(ab|cd)`), or a fixed-length prefix followed only by
    single-character items with any repeat operator and anchors
    (`[0-9]+`, `^ *`, `a.*b`, `x[ab]*y?`). The bench workloads fall in this
    class.
  * Otherwise it returns a `LongestPattern`. The start of the match comes
    from Python if the pattern is backtrack-safe, else from the automata
    below. A Thompson NFA, run as a lazily built DFA, then finds the
    longest end from there in one left-to-right pass. DFA states are
    cached per pattern, and the cache is dropped and rebuilt after 10 000
    states.
  * The safety rule is checked by `scripts/regex_fuzz.py --verify-safety`.
    Every pattern judged safe is compared with the automaton on all strings
    over {a, b, c} up to length 6. The patterns are exhaustive up to three
    single-character items, plus random four-item runs and random EREs:
    24 483 patterns, 0 wrong. A small version runs in
    `tests/test_regex_engine.py`.

**No catastrophic backtracking.** Python's `re` tries the ways of dividing
the text among the repetitions one by one, so a pattern with nested or
overlapping repetition is exponential when there is no match: `(a*)*b` on
30 `a`s took 56 s, `(a+)+b` 32 s, `([a-z]+)*[0-9]` 38 s, `(.*)*x` 53 s.
The reference awks use automata and answer at once. pyawk now does too:

* `_backtrack_safe(tree)` decides whether Python may be trusted. A pattern
  is safe when it is a run of items (or a top-level `|` of such runs),
  each item a character set, an anchor, a single-character set with a
  repeat operator, or a `|` of plain strings (at most 64 combinations in
  all). A variable item is *forced* when its characters cannot begin
  anything that may follow it before the next mandatory item: Python then
  never has a choice to undo. At most one item may be *ambiguous*, and
  every variable item after it must be forced and share no character with
  it. With one ambiguous item the work per start position is linear, so a
  search is at worst quadratic in the line length (`[0-9]+5\.` on a line
  of 5s). `(a*)*b`, `(a|aa)+`, `.*x.*y` and `(a|ab)(c|bcd)*` are risky;
  `[0-9]+`, `^(b|d)[a-z]+o$`, `foo|bar` and `a.*b` are safe. Every
  pattern in the bench and nearly all real-world awk regexes are safe.
* Risky patterns get a `LongestPattern` without a Python pattern. Its
  three automata are all built lazily from the same NFA, each running in
  time proportional to the text:
  * `found()` (yes/no): one left-to-right pass of an *unanchored* DFA (the
    NFA start state is put back after every character), stopping at the
    first position where a match ends.
  * Match starts: the pattern is reversed (`_reverse`: concatenations run
    backwards, `^` and `$` swap) and its unanchored DFA scans the text
    from right to left. Wherever it accepts, a match of the original
    pattern starts. The marks are kept for the last string, so the
    repeated searches of gsub, split and FS share one pass; the leftmost
    mark at or after the search position is the POSIX start.
  * The anchored DFA described above then finds the longest end.
* Two speed-ups keep the Python per-character loop short. While an
  unanchored scan is idle (no match in progress) it jumps to the next
  character that can begin a match with a compiled regex of the pattern's
  first characters, which searches in C. And `search` first tries those
  candidate starts one by one with the anchored DFA; the tries share a
  budget of 2n + 64 characters per string, and when it runs out the
  one-pass marks above take over, so the worst case stays linear.
* Long texts. Even a safe pattern is quadratic in the worst case, so for
  texts over `LONG_TEXT` (2048) characters `for_long` swaps in an
  automata-only twin of any pattern with a variable item: in `sub`/`gsub`,
  `match()`, `split()`, a regex FS and a regex RS buffer. `[0-9]+5\.` on
  100 000 fives took 49 s and now takes 0.05 s.
* The DFA cache limit cannot cause a blow-up: when 10 000 states are
  cached, the cache is cleared and rebuilt as the scan goes on. Each input
  character builds at most one new state, so the cost per character is
  bounded by the NFA size. `(a|b)*a(a|b){15}` (2^16 DFA states) runs
  `gsub` and `~` over 100 000 random a/b characters in 0.6 s. Clearing
  also empties the old states' transition tables (`_DFA._forget`): a scan
  in progress still holds its current state and the idle state, and the
  old tables would otherwise link them to every state built later, so
  nothing would be freed during one long scan (20 000 live states with a
  limit of 64 before the fix; CodeRabbit, PR #1). The initial states are
  kept, with empty tables.
* Size limits (a regex may come from the input, and the NFA writes out
  every copy an interval asks for). Repetition counts above
  `RE_DUP_MAX` = 32767 are bad regexes: POSIX lets an implementation pick
  any limit of at least 255; onetrue-awk stops at 255, gawk at 32767, and
  mawk has no limit, so the majority accepts `a{0,1000}` (gawk, mawk) and
  rejects `a{0,100000}` (onetrue, gawk). And a pattern whose NFA would
  need more than `NFA_LIMIT` = 1 000 000 states is refused at parse time
  ("too big"); the count is computed from the syntax tree, not built.
  Only nested intervals reach it: `((a{0,200}){0,200}){0,200}` (8 million
  states) took 1 GB and 3 s, and with 1000s it exhausted memory. That is a
  deliberate difference: gawk and mawk accept such patterns, given the
  memory (CodeRabbit, PR #1).
* The cache of compiled regexes is limited by size as well as count.
  Each pattern passes the limit above on its own, but a dynamic regex
  from the input can be a different big pattern on every line:
  `(a|b{0,1000}){0,400}` needs 800 000 states (about 48 MB), and the
  cache used to keep 512 patterns whatever their size (1.4 GB after 30
  such patterns, and still growing). Now the oldest entries are dropped
  once the cached automata pass `CACHE_STATE_LIMIT` = `NFA_LIMIT` states
  in all; the newest pattern is always kept. The same 30 patterns stay
  under 200 MB. The cache still holds up to 512 small patterns. It drops
  the oldest entry first, not the least recently used one, so that a
  lookup costs no more than before (each place in the program also keeps
  its own last pattern). Raised by CodeRabbit's security review, PR #1.

After the change every one of those cases (`~`, `match`, `sub`, `gsub`,
`split` and FS) takes at most a few milliseconds on 30 characters and stays
linear at 10 000 and 100 000 (`tests/test_regex_backtracking.py`, each case
in a child process with a timeout). The safety rule was also checked
empirically: 968 corpus and random patterns judged safe were timed at
growing lengths, with no superlinear growth per start position.

**Parsing rules** (the majority of mawk, onetrue-awk and gawk --posix; all
references run with LC_ALL=C):

* Intervals:
  * `{n}`, `{n,}` and `{n,m}` are intervals.
  * A `{` not followed by a digit is a literal, so `a{,2}` and `a{x` are
    literal (mawk reads `{,m}` as `{0,m}`; outvoted).
  * `{n` with no closing `}`, and `{3,2}`, are errors.
* Repeat operators:
  * An operator applies to everything before it: `a{2}+` is `(a{2})+`
    (mawk and gawk; onetrue-awk reads it as `a{2,}`), and `a+?` is `(a+)?`
    (mawk and onetrue-awk; but see the POSIX 2024 note under "Regex
    decisions").
  * An operator with nothing to repeat is an error (`*a`, `(+a)`, `a|?b`),
    except straight after `^`: `^+` is `^`, and `^*`/`^?` match the empty
    string.
* Alternatives and groups:
  * An empty alternative is an error (`a|`, `|a`, `(|b)`, `a(|b)c`; see
    "Regex decisions" below). `()` is allowed and matches the empty string.
  * An unmatched `(` is an error; an unmatched `)` is a literal.
* Anchors: `^` and `$` are anchors anywhere in the pattern (`x^y` and
  `a$b` can never match). `$` means the very end (`\Z`, not Python's `$`,
  which also matches before a trailing newline). `.` matches newline.
* Escapes:
  * Supported: `\n \t \r \f \v \a`, `\b` (backspace), octal `\ddd`, and hex
    `\xhh` (one or two digits; mawk and onetrue-awk).
  * Any other escaped character is itself. This includes `\w`, `\s`, `\d`,
    `\y`, `\B`, `\<` (gawk's GNU operators are off with --posix).
  * `\/` is `/`.
* Bracket expressions:
  * A leading `]` (after an optional `^`) is a member. `-` is literal first
    or last, or right after a range (`[a-c-e]` is a–c, `-` and `e`, as in
    mawk).
  * Backslash escapes work inside brackets (`[\]]`, `[\\]`, `[\t]`).
  * Classes: the twelve POSIX `[:class:]` names. Anything else, including
    gawk's `[:word:]`, is an error, as is a class used as a range end.
  * `[=a=]` and `[.a.]` mean `a`, and `[.-.]` can be a range end (in a
    UTF-8 locale too: see "Locales and UTF-8"). A multi-character name (`[.ab.]`, `[.space.]`) is an error.
  * An unterminated bracket and a reversed range (`[z-a]`) are errors.

**Overrides.**
* *Overrides.* The differential record uses one reference's answer instead
  of the vote for a few patterns, listed with reasons in
  `regex_corpus.OVERRIDES`:
  * Collating symbols and equivalence classes (17 patterns) use gawk's
    answer. mawk ignores `[. .]`/`[= =]` and reads them as plain bracket
    members, and onetrue-awk supports them only as the whole bracket, so
    their agreement is two bugs.
  * Two random patterns that the majority rejects use gawk's answer. Each
    one hits a different bug: mawk rejects `$(` at the start of a branch,
    and onetrue-awk rejects `^` after other items. Each construct alone is
    accepted by the other two awks. One of the two is not tested at all,
    because gawk's `split()` is also wrong on it: `split("ab", A, /x*^/)`
    gives "b".
  * `a(|b)c` uses onetrue-awk's answer (error; see the decisions below).
* `[z-a]`, `[a-c-e]` and the five random `^` patterns are not in the
  differential record (no majority); they are pinned by
  `test_regex_decisions` in `tests/test_regex_engine.py` instead.

**Regex decisions.** Where POSIX leaves a construct undefined or
unspecified, pyawk takes the majority of the three references; where
they split three ways, the most defensible answer, flagged below. Each
row is pinned by `test_regex_decisions` / `test_regex_decisions_errors`
in `tests/test_regex_engine.py` (references run with LC_ALL=C):

| Construct | POSIX (XBD 9) | onetrue | gawk --posix | mawk | pyawk |
|---|---|---|---|---|---|
| empty alternative: `a\|`, `\|a`, `(\|b)`, `(a\|)b`, `a\|\|b` | undefined (9.4.7) | error | accepted | error | error (majority) |
| empty alternative: `a(\|b)c`, `x(a\|)b` | undefined | error | accepted | accepted, wrong answers (`x(a\|)b` matches "abc") | error: one consistent rule (onetrue + mawk's other positions) |
| reversed range `[z-a]`, `[b-a]` | unspecified: matches nothing *or* invalid (9.3.5) | matches the empty string | invalid | matches the end points `z` and `a` | invalid. **Three-way split**: gawk's is the only answer POSIX allows. Also why onetrue t.gsub4/t.split3 and gawk regex3minus stay skipped |
| `[a--@]` (`a` after `-`) | invalid *or* the same as `@` (9.3.5) | `@` | invalid | wrong (`a`, `-`, `5` match) | invalid: allowed, and the same rule as `[z-a]` |
| `[a-c-e]` | undefined (9.3.5) | a–e (`d` matches, `-` does not) | invalid | a–c, `-`, `e` | a–c, `-`, `e`. **Three-way split**: mawk's reading follows the rule that `-` after a range is literal, as at the end of a bracket |
| `a{1}{2}`, `(a){1}{2}` | undefined (adjacent duplication symbols) | "out of space" | `(a{1}){2}` | same as gawk | `(a{1}){2}` (majority) |
| `a{1,2}{2}` | undefined | wrong | `(a{1,2}){2}` | wrong on "aa" | gawk (the only consistent reading; mawk and onetrue disagree with each other) |
| `a{2}+`, `a{2}*` | undefined | `a{2,}` | `(a{2})+` | `(a{2})+` | `(a{2})+` (majority) |
| `a*+`, `a**`, `a?*` | undefined | `a*` | `a*` | `a*` | `a*` (all agree) |
| `^` after other items (`b*^a`, `(a\|^b)c`, five random patterns) | an anchor anywhere (9.4.9: clear) | error | anchor | anchor, wrong lengths on the random five | anchor (POSIX) |
| `a+?`, `a*?` (POSIX 2024 minimal repetition) | **clear**: `?` after a repeat makes it leftmost-*shortest* | `(a+)?` | shortest (`match("aaaaa", /a+?/)` gives 1,1) | `(a+)?` | `(a+)?`. **Open** (see below) |

*Open: POSIX 2024 minimal repetition.* POSIX.1-2024 added `*?`, `+?`,
`??` and `{m,n}?` to EREs. gawk 5.4 implements them even with --posix;
mawk and onetrue-awk (and pyawk) still read `a+?` as `(a+)?`. POSIX is
clear, so by the project's rule pyawk should follow it, but it is a new
matching mode for the engine (shortest instead of longest for that item,
in the automata as well as the Python path), so it is left for the
user to decide.
* *Imported gawk test regrange.* It now runs with gawk's `.ok`
  (`import_gawk.REGEX_POSIX`). The vote was split only on collating symbols
  in ranges.

**Differential testing.** `awk_tests/regex_corpus.py` holds the corpus:
the curated categories alternation, nested, quantifiers, intervals,
brackets, classes, anchors, escapes, empty and pathological (19
backtracking-prone patterns such as `(a*)*b`, `(a|aa)*c` and `(.*)*x` on
30–50 character lines), plus 279 seeded random EREs.
Each pattern runs on a set of inputs through one probe program that prints
`~`, `match()`, `sub`, `gsub` and `split` results.

`scripts/regex_fuzz.py` runs the three references, records the per-line
majority in `awk_tests/regex_differential.json`, and compares pyawk with
it. `tests/test_regex_differential.py` checks that record in pytest. The
golden category `regex_posix` (8 cases) covers the same rules with
ordinary awk programs. `scripts/regex_fuzz.py --utf8` does the same for
the UTF-8 corpus (88 patterns over 28 inputs with multibyte and invalid
UTF-8, in `awk_tests/regex_differential_utf8.json`); see "Locales and
UTF-8".

| | patterns with a mismatch | mismatching lines |
|---|---|---|
| before (Python `re` translation) | 105 / 551 | 1065 / 13 160 |
| after | 0 / 551 | 0 / 13 142 (108 split or untested lines) |
| with the pathological category | 0 / 570 | 0 / 13 503 (108 split or untested lines) |

Before, by category (patterns / lines):

| category | patterns | lines |
|---|---|---|
| alternation | 9 | 54 |
| nested | 3 | 19 |
| quantifiers | 3 | 45 |
| intervals | 7 | 174 |
| brackets | 5 | 86 |
| escapes | 2 | 20 |
| empty | 8 | 192 |
| random | 68 | 475 |
| classes, anchors | 0 | 0 |

All categories are 0 after the change.

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
The reference awks are found as in all the tooling (`awk_tests/references.py`:
a `PYAWK_REF_MAWK` / `PYAWK_REF_ONETRUE` / `PYAWK_REF_GAWK` variable, else
`mawk`, `gawk`, and `original-awk`/`onetrue-awk`/`bwk-awk` on PATH); only
those found are timed, and `--path NAME=PROGRAM` overrides either for one run. `--save LABEL`
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

**POSIX regex engine (enhancements).** Same benchmark, before and after
the leftmost-longest engine (`.bench/regex-before.json`,
`.bench/regex-after.json`). Times are seconds per 200 000 lines, best of 3.
print1 and wordcount were re-run best of 7, because their first gap was
noise.

| workload | before | after | before/after |
|---|---|---|---|
| print1 | 0.363 | 0.351 | 1.03× |
| sum | 0.401 | 0.400 | 1.00× |
| fields | 1.868 | 1.830 | 1.02× |
| regex | 0.600 | 0.579 | 1.04× |
| wordcount | 1.535 | 1.540 | 1.00× |
| printf | 0.868 | 0.862 | 1.01× |
| gsub | 0.828 | 0.831 | 1.00× |
| funcs | 0.474 | 0.456 | 1.04× |
| begin_loop | 0.804 | 0.792 | 1.02× |

All workloads are within noise, because their patterns are "safe" and still
run entirely in Python's `re`. The cost shows only for span uses of unsafe
patterns. For example, `gsub(/(e|er)+[a-z]/, "X")` over 200 000 lines
takes 0.53 s before and 0.96 s after (about 1.8×); that is the price of
the correct result.

**No catastrophic backtracking (enhancements).** Same benchmark, before
and after routing risky patterns to the automata (`.bench/before-btfix.json`,
`.bench/after-btfix.json`), best of 3:

| workload | before | after | before/after |
|---|---|---|---|
| print1 | 0.364 | 0.370 | 0.98× |
| sum | 0.398 | 0.396 | 1.01× |
| fields | 1.892 | 1.900 | 1.00× |
| regex | 0.562 | 0.613 | 0.92× |
| wordcount | 1.558 | 1.548 | 1.01× |
| printf | 0.882 | 0.864 | 1.02× |
| gsub | 0.839 | 0.877 | 0.96× |
| funcs | 0.467 | 0.459 | 1.02× |
| begin_loop | 0.802 | 0.815 | 0.98× |

The bench patterns are all backtrack-safe, so nothing changed for them;
the regex and gsub gaps are noise (interleaved best-of-5 runs of the old
and new code: regex 0.565 / 0.565–0.570 s, gsub 0.829–0.841 /
0.848–0.856 s). Risky patterns pay a little for the Python DFA loop:
`gsub(/(e|er)+[a-z]/, "X")` over 200 000 lines 0.95 → 1.01 s, and
`/(foo|bar)+x/` 0.32 → 0.37 s.

## Test status

pytest: 1900 passed, 122 skipped. Golden summaries: own corpus 154/154
(nine v1.0 categories with 99 cases, plus environ 5, io 18, nextfile 7,
regex_posix 8 and utf8 17), onetrue-awk 215 pass / 9 skip, gawk 192 pass /
112 skip, no failures and empty `KNOWN_FAILURES` lists (2026-11-01,
`enhancements` branch). The regex differential record (570 patterns,
13 503 input lines) and the UTF-8 one (88 patterns, 2 152 lines) have no
mismatches. v1.0 had 744 / 99 / 215 / 190. The extra gawk cases
are close_status (see `close()` above) and regrange (see "Regular
expressions"). Most of the pytest growth is the regex differential test,
which has one case per pattern and category, plus 86 engine tests and 57
backtracking tests.

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
the majority wins; the splits are recorded under "I/O decisions".

### Test infrastructure

* **Sandboxed golden runs.** Each golden case runs in a throwaway copy of
  its category directory, so a program may create, append to and read back
  files with relative names without touching the repository.
* **Optional per-case files.** `NAME.environ` (`NAME=value` lines added to
  the environment), `NAME.operands` (the operands to use instead of
  `NAME.in`; may name other cases' `.in` files and `-`), `NAME.stdin`
  (standard input). `scripts/make_corpus.py` writes them all.
* **Where the reference awks are.** `awk_tests/references.py` is the one
  place that finds them, for every tool and test: a `PYAWK_REF_MAWK`,
  `PYAWK_REF_ONETRUE` or `PYAWK_REF_GAWK` variable, else the usual names on
  PATH (never plain `awk`). There is deliberately no built-in default path:
  a path that exists only on the author's machine would make the tools
  behave differently elsewhere. A variable that names nothing is an error,
  not a fallback. A missing reference stops a tool (exit 2) before it
  writes anything, and the tests that need one are skipped with the
  reason. README.md ("Pointing the tools at the reference awks") has the
  details.
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

### I/O decisions

Each was run under all three references; POSIX's stance comes first.
All are covered by tests (`tests/test_io.py`, golden `environ`, `io` and
`nextfile`).

| Question | POSIX | onetrue | gawk --posix | mawk | pyawk |
|---|---|---|---|---|---|
| `close("/dev/stdout")` / `close("/dev/stderr")` after printing to it | `close` returns 0 on success; the special names are not in POSIX awk | 0; stdout is closed (later output lost) | 0; output continues | 0; stdout is closed | 0; output continues (flushes). **Split** on whether stdout survives: losing it is never useful, so gawk |
| `close()` of a special name never used | -1 for "not open" | 0 (`/dev/stderr`); closes fd 1 for `/dev/stdout` | -1 | 0 / closes fd 1 | -1 (gawk; consistent with any other name not open) |
| `close("-")`, `close("/dev/stdin")` after `getline <` it | silent | 0; stdin used up | 0; stdin used up | 0 (crashes on `/dev/stdin`) | 0; later reads of stdin find nothing (all agree) |
| `getline < "-"`, then `< "/dev/stdin"`, then plain getline | one standard input | consecutive lines | lines lost to separate buffers | lines lost | consecutive lines: the loss depends on buffer sizes, so not copied (onetrue) |
| ENVIRON value `0x1A` (strnum?) | implementation-defined whether hex is recognised | string | number | string | string (majority) |
| `nextfile` on standard input, then `-` or `getline < "-"` | silent | carries on reading | nothing more | nothing more (except from its buffer on a big regular file) | nothing more (majority); stdin is not drained |
| `nextfile` in BEGIN/END, in a function | undefined | see "Parse-time errors" | | | syntax error (majority) |
| `cmd \| getline x + 1` | `(cmd \| getline x) + 1` (grammar) | target `x + 1`; `* / % ^` rejected | POSIX | POSIX | POSIX (see Parser) |
| stdout flushed before every getline | not required | yes | no | no | no (majority; output order with a terminal is unaffected) |
| `close()` of a file, `/dev/stdout` or `/dev/stderr` when the last write or flush fails (e.g. `/dev/full`) | close returns non-zero on failure | "write error on ...", exit 2 | fatal: "flush to ... failed", exit 2 | "close failed on file ...", exit 2 | "write error on NAME", exit 2: the program stops there (all three references; the user chose them over the POSIX reading, which would allow returning -1 and carrying on). A broken pipe still ends the program by SIGPIPE |
| `fflush(file)`, `fflush("/dev/stderr")`, or `fflush()` / `fflush("")` with a file, when the flush fails | fflush returns non-zero on failure (POSIX 2024) | "write error on ...", exit 2 | fatal: "fflush: cannot flush file ...", exit 2 | file: message, returns -1, carries on; `/dev/stderr`: exit 2 | "write error on NAME", exit 2: the program stops there (onetrue and gawk; all three for `/dev/stderr`), as for `close()` |
| `fflush("/dev/stdout")`, `fflush()`, `fflush("")` when standard output cannot be written | fflush returns non-zero on failure | message, returns -1 (0 for `fflush()`), carries on, then exit 2 at the end even after `exit 3` | fatal, exit 2 | message, returns -1 (0 for `fflush()`), carries on, keeps the program's exit status | returns -1 and carries on (onetrue, mawk); exit 2 at the end with "write error on standard output" (onetrue, gawk). This is onetrue's behaviour, and the majority on each point |
| output to a file still unwritten at exit fails (`printf "x" > "/dev/full"`, no `close`) | an error is implied (data is lost) | "write error on ...", exit 2 | fatal: "flush to ... failed", exit 2 | "close failed on file ...", exit 2 | "write error on NAME", exit 2 (all agree; before this the data was lost silently with exit 0) |
| standard error cannot be written (`2>/dev/full`) after output to `"/dev/stderr"` or an error message | (no message possible) | exit 2 | exit 2 | exit 2 | exit 2, whatever `exit` asked for. Python alone would exit 120 when its final flush of stderr fails, so pyawk flushes it at exit and discards what cannot be written |
| several commands still open at exit | order unspecified | | | | nondeterministic in every awk; tests close them explicitly |

Left to the user: `close()` of a command returns its exit status
(mawk and default gawk; onetrue-awk and `gawk --posix` return 0), recorded
in `awk_tests/golden/DECISIONS` as `io/close_status`.

## Golden corpus and mawk

`.out` files were generated with **mawk 1.3.4 20250131** (the only awk on the
build box). Cases were written to avoid areas where mawk departs from POSIX
or where POSIX leaves behaviour unspecified. Known divergences, verified with
differential runs:

| Behaviour | POSIX / pyawk | mawk |
|---|---|---|
| `substr(s, -1, 3)`, negative start with a length | start clamped to 1, length kept: `"hel"` (onetrue, gawk) | `"hell"` |
| `length("héllo")` in a UTF-8 locale | characters: 5 | bytes: 6 (mawk has no multibyte support; the `utf8` golden category is generated with `gawk --posix` instead) |
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

## Locales and UTF-8

pyawk picks one of two modes at start-up from the environment, as POSIX
describes: the first of `LC_ALL`, `LC_CTYPE` and `LANG` that is set and
not empty decides (`pyawk/charset.py`).

* **UTF-8 mode**: the locale name contains `UTF-8` or `utf8` (any letter
  case), e.g. `LANG=en_GB.UTF-8` or `LC_ALL=C.UTF-8`. Strings are made of
  characters: `length`, `substr`, `index`, `match` (`RSTART`/`RLENGTH`),
  `split` (including `split(s, a, "")`), printf `%c` and the widths and
  precisions of `%s`/`%c`, `toupper`/`tolower`, the regex `.`, bracket
  expressions and classes, FS and RS all count and compare characters.
* **C mode**: anything else, including no locale at all. Every byte is
  one character; classes are ASCII; `toupper`/`tolower` change only
  `a-z`/`A-Z`. This is byte-exact, as before.

Python itself changes `LC_CTYPE` to `C.UTF-8` at start-up when the locale
is plain C (PEP 538 "locale coercion"). On Linux pyawk detects that and
undoes it, both for its own mode and for the environment of `system()` and
pipes, so `LANG=C pyawk ...` really is byte mode. The detection reads the
original environment from `/proc/self/environ`; on systems without it the
coercion cannot be seen, so there set `LC_ALL=C` (which Python does not
coerce) or `PYTHONCOERCECLOCALE=0` to get byte mode.

**Bytes in, bytes out.** Input is decoded and output encoded with the
mode's codec: Latin-1 in C mode (byte N is character N, so everything
round-trips), UTF-8 with Python's `surrogateescape` in UTF-8 mode. File
names, command lines, `-v` values, `ENVIRON` and program text use the same
codec, so a name such as `été.txt` reaches the file system unchanged.

**Invalid UTF-8.** A byte that is not part of a valid UTF-8 sequence is
kept as an "escaped byte" (one of U+DC80..U+DCFF inside pyawk) and is
written back as the same byte. It never crashes pyawk, and **each invalid
byte counts as one character** (`length("a\377b")` is 3, as in gawk).
Because Python's decoder handles them, truncated sequences, overlong forms
and encoded surrogates are invalid bytes too.

**Tests.** `tests/test_locale.py` (end-to-end programs run in both
locales, the tables, detection, the regex set forms); golden category
`utf8` (17 cases, run with `LC_ALL=C.UTF-8`; `awk_tests/harness.py`
`LOCALES`), whose `.out` files come from `gawk --posix` because mawk has no
multibyte support (`regen_golden.py` votes them against onetrue-awk); the
UTF-8 regex differential corpus (`scripts/regex_fuzz.py --utf8`, 88
patterns: literals, `.`, brackets, classes, escapes, intervals and inputs
over 2048 characters, which take the automaton route) recorded from
onetrue-awk and `gawk --posix` under `C.UTF-8`, with gawk breaking ties.
The rest of pytest runs in the C locale (an autouse fixture in
`tests/conftest.py`), so results do not depend on the machine's locale.

**Decisions.** The user confirmed the four choices marked *Decided by the user* below (they are no longer open). POSIX says characters, but leaves invalid input, `%c` of
large numbers and multibyte collation to the implementation. mawk is not
a reference here (it is byte-only), so where onetrue-awk and gawk
disagree pyawk follows POSIX where it speaks, otherwise gawk, except
where noted. References run with `LC_ALL=C.UTF-8`.

| Question | onetrue-awk | gawk --posix | pyawk |
|---|---|---|---|
| invalid byte in `length`, `index`, `split`, `match` | one character each, but a lone byte such as `\351` is read as its Latin-1 character (so `/é/` matches it) | one character each | one character each, matching only its own escape `/\351/` (gawk) |
| invalid byte through `substr`, `toupper`, `tolower` | `substr` keeps the byte; `toupper` stops with "illegal byte sequence" | replaced by U+FFFD (`substr("a\377b", 2, 1)` is U+FFFD) | **kept as the same byte**: output round-trips. *Decided by the user* (OVERRIDES `utf8/invalid_bytes_substr`) |
| `.`, `[^a]` on an invalid byte | match | do not match | do not match (gawk); `/\377/` does match it |
| `"\303\251"`, `/\303\251/` (escapes spelling UTF-8 bytes) | one character `é` | one character `é` | one character `é` (both agree). `\x` escapes in a regex combine the same way; gawk --posix has no `\x` (reads `x`), plain gawk agrees with pyawk |
| `"\303" "\251"` (concatenation) | `é` | `é` | `é`: concatenation joins escaped bytes that complete a character |
| `printf "%c", 233` | `é` | `é` | `é` (code point, UTF-8 encoded) |
| `printf "%c", 1114112` (above U+10FFFF) | U+FFFD | `\364\220\200\200` (the 4-byte pattern extended) | gawk's bytes. *Decided by the user* |
| `printf "%3c", 233` | 2 spaces + `é` | 1 space + `é` (pads by bytes) | **2 spaces** (onetrue: widths count characters, as POSIX says and as gawk does for strings). *Decided by the user* (OVERRIDES `utf8/printf_width`) |
| `%5s`, `%.1s` of `"é..."` | characters | characters | characters |
| `[[=é=]]` | matches nothing, not even `é` | glibc equivalence class: `e`, `E`, `É`, ... | **`é` only** (code point, same rule as the C locale). *Decided by the user*; overridden in the UTF-8 differential corpus |
| `[[:alpha:]]`, `[[:upper:]]`, `[[:punct:]]` ... | ASCII only | Unicode (glibc tables) | gawk's tables (`pyawk/unicode_tables.py`, generated by `scripts/gen_unicode_tables.py`); `digit`/`xdigit` stay ASCII as POSIX requires |
| `toupper("straße é ж")` | `STRAßE É Ж` | `STRAßE É Ж` | the same (one-character mappings only, so `ß` stays; table from gawk) |
| default FS and U+3000 / U+00A0 | not separators | not separators | not separators: space, tab, newline only |
| multi-character `RS = "é\|x"` | regex | first character only | regex (onetrue, mawk, and pyawk's C mode; OVERRIDES `utf8/record_separator`) |
| `"é" < "z"` | 0 | 0 | 0: comparison by code point (no locale collation) |
| `é = 1`, `-v é=1` (non-ASCII names) | syntax error / invalid -v | syntax error / fatal | errors (all three agree; mawk too) |
| `printf "%٣d"`, `/a{٣}/` (Arabic-Indic digit) | not a width / not an interval | not a width / not an interval | ASCII digits only (Python's `isdigit()` would accept `٣`) |

Overridden patterns in the UTF-8 differential corpus (`regex_corpus.UTF8_OVERRIDES`):
`[[=é=]]` (above), `\xc3\xa9` (gawk --posix reads `\x` as `x`), and `.*`
(gawk --posix never returns from `gsub(/.*/, ...)` on a line with an
invalid byte; `^.*$` and the other `.` patterns cover the same ground).
gawk breaks the ties: onetrue-awk disagrees with it (and with pyawk) on
at least one input line of 66 of the 88 patterns (325 lines), mostly
because of its ASCII-only classes and its reading of a lone invalid byte
as a Latin-1 character.

**Regex engine.** The engine works on Python strings, so in UTF-8 mode it
simply sees characters; the backtracking-safety rule and the routing of
texts longer than 2048 characters (`LONG_TEXT`, now 2048 *characters*) to
the automaton are unchanged. Sets keep sorted ranges and test membership
by bisection, so a Unicode class with hundreds of ranges costs one
`bisect` per character in the automaton. In the Python patterns, a set
with many ranges above U+FFFF is written as
`(?:[below U+10000]|(?=[\U00010000-\U0010ffff])[the rest])`, because
Python checks the ranges above U+FFFF one by one and every character not
in the set would otherwise walk the whole list. The `_compile` cache is
cleared when the mode changes.

**Performance.** On the benchmark (ASCII input) UTF-8 mode is within
about 2% of C mode and of the code before the change (best of 7,
alternating runs: fields +1.4%, sum +1.8%, funcs +1.3%, others within
noise; C mode no slower). On 200 000 lines of mixed French, Greek and
Chinese words, `gsub(/[[:alpha:]]é/, "&")` takes 0.87 s in UTF-8 mode
against 0.45 s in C mode (2.1 s before the set rewrite); the rest of the
gap is Python's `re` scanning a large set. `scripts/bench.py --locale
C.UTF-8` times UTF-8 mode.

## Known issues / limitations

* Regex: the automaton's DFA cache is limited to 10 000 states per pattern; a
  larger pattern still works, linearly, but rebuilds states. Patterns
  that are not backtrack-safe run in a Python per-character loop, so they
  are slower than Python's `re` on easy inputs (`gsub(/(e|er)+[a-z]/, ...)`
  about 1.9× the old leftmost-first code), but never exponential. A safe
  pattern on a text of up to 2048 characters can still be quadratic in
  Python's `re` (a few milliseconds at that size). For a regex RS, mawk
  itself is very slow on `RS = "(a*)*b"` over 100 000 `a`s; pyawk, gawk
  and onetrue-awk are not.
* `getline` updating `NF/NR/FNR` follows POSIX; `getline < file` where file is
  the current input is treated as an independent stream.
* `srand()` seed is time-based in seconds; `rand()` uses Python's Mersenne
  Twister, so sequences differ from other awks.
* AWK recursion depth is limited (about 100 000 levels for a simple
  function, fewer for bodies with deeply nested statements); beyond that,
  a clean error.
* Performance: roughly 4–15× slower than mawk and 1–13× slower than
  `gawk --posix` on the benchmark workloads (gawk is itself slow on the
  regex and gsub ones, where pyawk is only 1.1× and 1.4× behind; see
  "Performance").
* `%x`, `%o`, `%u` (and `%c`) of a value outside the 64-bit range, such
  as `printf "%x", -2^70`: left alone on purpose. awk converts the number
  to a C integer first, and C leaves an out-of-range conversion
  undefined, so the references disagree completely (mawk `1`, onetrue-awk
  `8000000000000000`, gawk switches to `%g`). pyawk prints the exact
  value in two's complement. Values that fit (`printf "%x", -1` gives
  `ffffffffffffffff`) agree with all three.
