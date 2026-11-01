"""Generate the golden corpus inputs (.awk/.in/.args). Run scripts/regen_golden.py afterwards for .out.

Each ``add(category, name, program, input, args)`` call below describes one
test case; the loop at the end writes them all under awk_tests/golden/.
The upper-case strings (EMP, PASSWD, TEXT ...) are shared sample inputs.
Running this overwrites the existing .awk/.in/.args files.
"""
import os
ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "awk_tests", "golden")

EMP = """Beth 4.00 0
Dan 3.75 0
Kathy 4.00 10
Mark 5.00 20
Mary 5.50 22
Susie 4.25 18
"""
PASSWD = """root:x:0:0:root:/root:/bin/bash
daemon:x:1:1:daemon:/usr/sbin:/usr/sbin/nologin
alice:x:1000:1000:Alice Smith:/home/alice:/bin/zsh
bob:x:1001:1001:Bob Jones:/home/bob:/bin/bash
"""
TEXT = """The quick brown fox
jumps over the lazy dog

  leading spaces here
trailing tabs\tand\ttabs\t
one
the end of the text
"""
NUMS = """10
10.0
 1e1
abc
010
-3
+4.5
.5
"""

# (category, name) -> (program, input, args, extras); a dict keyed by a pair.
# ``extras`` maps a file extension to its content, for the optional files
# described in awk_tests/harness.py: ".operands" (replaces the NAME.in
# operand), ".stdin" (standard input) and ".environ" (NAME=value lines
# added to the environment).
cases = {}
def add(cat, name, prog, inp="", args="", operands=None, stdin=None, environ=None):
    extras = {}
    if operands is not None:
        extras[".operands"] = operands + "\n"
    if stdin is not None:
        extras[".stdin"] = stdin
    if environ is not None:
        extras[".environ"] = "".join(f"{k}={v}\n" for k, v in environ.items())
    cases[(cat, name)] = (prog, inp, args, extras)

# ---------------- basic
add("basic", "print_all", "{ print }\n", EMP)
add("basic", "begin_only", 'BEGIN { print "hello, world" }\n', "this input must not be read\n")
add("basic", "begin_end", 'BEGIN { print "start" }\n{ n++ }\nEND { print n, "lines"; print NR }\n', EMP)
add("basic", "pattern_only", "$3 > 0\n", EMP)
add("basic", "arithmetic", "BEGIN {\n\tprint 1+2, 7-10, 3*4, 7/2, 7%3, -7%3, 2^10\n\tprint -2^2, 2^3^2, 2*3+4, 2*(3+4), 10-4-3, 100/10/5\n\tprint 1e3, .5 + .25, 017 + 0, 3 / 4 * 4\n}\n")
add("basic", "concat", 'BEGIN {\n\tx = "foo"; y = "bar"\n\tprint x y, x "-" y\n\tprint 1 2, 1 " " 2\n\tprint 1+2 "3", "a" 1+1 "b"\n\tz = x y x; print z, length(z)\n}\n')
add("basic", "number_output", 'BEGIN {\n\tprint 0.1+0.2, 1/3, 100000*100000, 1e6, 123456789, 3.0, 1e-5\n\tOFMT = "%.2f"; x = 3.14159; print x; y = x ""; print y\n\tCONVFMT = "%.3f"; z = x ""; print z; print 17 ""\n}\n')
add("basic", "comments_continuation", '# leading comment\nBEGIN {   # trailing comment\n\tx = 1 + \\\n\t    2\n\tprint x ; print "a",\n\t      "b"\n\tif (x == 3 &&\n\t    x > 0) print "ok"\n}\n\n\n{ print NR ": " $0 }   # per-record\n', "alpha\nbeta\n")
add("basic", "uninitialized", 'BEGIN {\n\tprint x + 0, "[" x "]", length(x)\n\tif (x == 0 && x == "") print "both zero and empty"\n\tif (!x) print "false"\n\ty[1]; print length(y[1]), (1 in y), (2 in y)\n}\n')
add("basic", "semicolons_newlines", 'BEGIN { a = 1; b = 2;; c = 3 }\nBEGIN {\n\tprint a, b, c\n};\nEND { print "done" }\n', "x\n")

# ---------------- fields
add("fields", "select_fields", "{ print $1, $3 }\n", EMP)
add("fields", "nf_last_field", '{ print NF ": " $NF; print $(NF-1), $(1+1) }\n', EMP)
add("fields", "fs_option", "{ print $1, $NF }\n", PASSWD, "-F:")
add("fields", "fs_begin", 'BEGIN { FS = ":" }\n{ print $5 "|" $6 }\n', PASSWD)
add("fields", "ofs_rebuild", 'BEGIN { OFS = "-" }\n{ print $1, $2; $1 = $1; print }\n', EMP)
add("fields", "assign_beyond_nf", '{ $5 = "X"; print; print NF }\n', "a b c\none two\n")
add("fields", "nf_decrease", "{ NF = 2; print; print NF }\n", EMP)
add("fields", "whitespace_split", '{ printf "%d:", NF; for (i = 1; i <= NF; i++) printf "[%s]", $i; print "" }\n', TEXT)
add("fields", "fs_tab", '{ print $2 "|" NF }\n', "a b\tc d\te\nx\ty\n\n", "-F '\\t'")
add("fields", "dollar0_assign", '{ $0 = "x y z"; print NF, $2 }\nEND { $0 = "p q"; print $1, NF }\n', "1\n2 3\n")
add("fields", "nr_fnr_filename", 'NR == 1 { print FILENAME }\n{ print NR, FNR, $0 }\n', "first\nsecond\nthird\n")
add("fields", "strnum_compare", '{ print ($1 == 10), ($1 == "10"), ($1 < 9), ($1 > 0) }\n', NUMS)
add("fields", "field_increment", '{ $2++; $3 += 10; print }\n', EMP)
add("fields", "past_nf", '{ print NF, ($5 == 0), ($5 == ""), length($5), ($NF == 0) }\n{ $6 = "x"; print ($4 == 0), ($4 == ""), NF }\n', "a b c\n\n0\n")
add("fields", "fs_change_next_record", '{ FS = ":"; print $1 }\n', "a:b c\nd:e f\n")

# ---------------- patterns
add("patterns", "regex_pattern", "/^M/\n/a$/ { print \"ends in a:\", $1 }\n", EMP)
add("patterns", "match_operators", '$1 ~ /^[A-K]/ { print "A-K:", $1 }\n$1 !~ /y$/ { print "no-y:", $1 }\n', EMP)
add("patterns", "compound", '$2 >= 4 && $3 > 0 || $1 == "Beth" { print }\n!($3 > 15) { print "not >15:", $1 }\n', EMP)
add("patterns", "negated_regex", "!/a/\n", EMP)
add("patterns", "dynamic_regex", 'BEGIN { re = "^[DM]" }\n$0 ~ re { print "re:", $1 }\n$1 ~ "a" "r" { print "ar:", $1 }\n', EMP)
add("patterns", "expression_patterns", 'NR % 2 == 0 { print "even:", $1 }\nNR > 4 { print "late:", $1 }\nlength($1) > 4 { print "long:", $1 }\n$3 { print "nonzero:", $1 }\n', EMP)
add("patterns", "bracket_classes", '/^[[:upper:]][[:lower:]]+$/ { print "word:", $0 }\n/[[:digit:]]+/ { print "digits:", $0 }\n/[[:space:]]/ { print "space:", $0 }\n/^[^a-z]*$/ { print "no lower:", $0 }\n', "Hello\nabc123\nwith space\nUPPER 42\n")
add("patterns", "regex_specials", '/a\\.b/ { print "dot:", $0 }\n/^(ab)+$/ { print "group:", $0 }\n/colou?r/ { print "opt:", $0 }\n/x+y*z/ { print "rep:", $0 }\n/cat|dog/ { print "alt:", $0 }\n/[/]/ { print "slash:", $0 }\n', "a.b\naxb\nabab\ncolor\ncolour\nxxz\nhotdog\na/b\n")

# ---------------- control
add("control", "if_else", '{\n\tif ($1 < 0) print $1, "negative"\n\telse if ($1 == 0) print $1, "zero"\n\telse {\n\t\tprint $1, "positive"\n\t}\n}\n', "-5\n0\n7\n")
add("control", "while_loop", 'BEGIN {\n\tn = 10; f = 1; i = 1\n\twhile (i <= n) {\n\t\tf *= i\n\t\ti++\n\t}\n\tprint f\n\twhile (0) print "never"\n}\n')
add("control", "do_while", 'BEGIN {\n\ti = 3\n\tdo {\n\t\tprint i\n\t\ti--\n\t} while (i > 0)\n\tdo print "once"; while (0)\n}\n')
add("control", "for_loop", 'BEGIN {\n\tfor (i = 1; i <= 3; i++) {\n\t\tline = ""\n\t\tfor (j = 1; j <= 3; j++)\n\t\t\tline = line (i * j) " "\n\t\tprint line\n\t}\n\tfor (;;) { k++; if (k > 4) break }\n\tprint k\n}\n')
add("control", "break_continue", 'BEGIN {\n\tfor (i = 1; i <= 10; i++) {\n\t\tif (i % 2) continue\n\t\tif (i > 8) break\n\t\tprintf "%d ", i\n\t}\n\tprint ""\n\ti = 0\n\twhile (1) { if (++i >= 5) break }\n\tprint i\n}\n')
add("control", "next_statement", '/^#/ { next }\nNF == 0 { next }\n{ print NR ": " $0 }\n', "# comment\nline one\n\n# another\nline two\n")
add("control", "exit_runs_end", 'NR == 3 { exit }\n{ print }\nEND { print "end at", NR }\n', EMP)
add("control", "assignment_ops", 'BEGIN {\n\tx = 10; x += 5; print x\n\tx -= 3; print x\n\tx *= 2; print x\n\tx /= 4; print x\n\tx %= 4; print x\n\tx ^= 3; print x\n\ty = z = 7; print y, z\n\ti = 5; print i++, i, ++i, i--, --i, i\n\tprint (1 ? "yes" : "no"), (0 ? "a" : 1 ? "b" : "c")\n}\n')
add("control", "getline_plain", 'NR == 1 { getline; print "after getline:", $0, NR }\n{ print NR ": " $0 }\nEND { while ((getline line) > 0) n++; print n + 0 }\n', "one\ntwo\nthree\nfour\n")
add("control", "getline_var", '{ if ((getline second) > 0) print $0 " + " second; else print $0 " alone" }\n', "a\nb\nc\n")

# ---------------- arrays
add("arrays", "word_count_sorted", '{ for (i = 1; i <= NF; i++) count[tolower($i)]++ }\nEND { for (w in count) print w, count[w] | "sort"; close("sort") }\n', "the cat and the dog\nThe end\n")
add("arrays", "in_and_delete", 'BEGIN {\n\ta["x"] = 1; a["y"] = 2\n\tif ("x" in a) print "x in a"\n\tdelete a["x"]\n\tif (!("x" in a)) print "x gone"\n\tn = 0; for (k in a) n++; print n\n\tdelete a\n\tm = 0; for (k in a) m++; print m\n}\n')
add("arrays", "multi_subscript", 'BEGIN {\n\ta[1, 2] = 3\n\tfor (k in a) { split(k, p, SUBSEP); print p[1], p[2], a[k] }\n\tif ((1, 2) in a) print "yes"\n\tif (!((2, 1) in a)) print "no"\n}\n')
add("arrays", "reverse_lines", "{ line[NR] = $0 }\nEND { for (i = NR; i > 0; i--) print line[i] }\n", EMP)
add("arrays", "numeric_subscripts", 'BEGIN {\n\ta[1] = "one"; print a["1"]\n\ta["01"] = "zero-one"; print a[1], a["01"]\n\tx = 0.1; b[x] = "tenth"; print b["0.1"]\n\tc[2+3] = "five"; print c["5"]\n}\n')
add("arrays", "group_sum", '{ total[$3 > 0 ? "worked" : "idle"] += $2; cnt[$3 > 0 ? "worked" : "idle"]++ }\nEND { print "idle", total["idle"], cnt["idle"]; print "worked", total["worked"], cnt["worked"] }\n', EMP)
add("arrays", "dedupe_count", '!seen[$1]++ { order[++n] = $1 }\nEND { for (i = 1; i <= n; i++) print order[i], seen[order[i]] }\n', "b\na\nb\nc\na\nb\n")

# ---------------- builtins
add("builtins", "length_substr", '{ print length($0), length, substr($0, 2, 3) "|" substr($0, 3) "|" substr($0, 2, 100) "|" substr($0, 10) "|" }\n', "hello\nab\nawk language\n")
add("builtins", "index_fn", '{ print index($0, "o"), index($0, "xyz"), index($0, "lo w") }\n', "hello world\nfoo\nbar\n")
add("builtins", "split_fn", 'BEGIN {\n\tn = split("a:b:c", arr, ":"); print n, arr[1], arr[3]\n\tn = split("  one  two three ", w); print n, w[1], w[3]\n\tn = split("a1b22c333d", r, /[0-9]+/); print n, r[1], r[2], r[4]\n\tn = split("", e); print n\n\tn = split("x,y", q, ","); print (q[1] < q[2])\n}\n')
add("builtins", "sub_gsub", '{ s = $0; n = gsub(/o/, "0", s); print n, s }\n{ t = $0; sub(/[a-z]+/, "<&>", t); print t }\n{ u = $0; gsub(/l/, "\\\\&", u); print u }\n{ n = gsub(/o/, "O"); print n, $0, $1 }\n', "hello world\nfoo boo\nxyz\n")
add("builtins", "printf_formats", 'BEGIN {\n\tprintf "%d|%5d|%-5d|%05d|%+d\\n", 42.9, 42, 42, 42, 42\n\tprintf "%.2f|%8.3f|%-8.1f|%e|%E\\n", 3.14159, 3.14159, 3.14159, 12345.678, 0.000123\n\tprintf "%g|%g|%g|%G\\n", 100000, 1000000, 0.0001, 1e-10\n\tprintf "%x|%X|%o|%c|%c|%c\\n", 255, 255, 8, 65, "hello", 256 - 190\n\tprintf "%s|%10s|%-10s|%.3s|%%\\n", "str", "right", "left", "truncate"\n\tprintf "%i|%*d|%-*d|%.*f\\n", 7, 4, 1, 4, 2, 2, 2.71828\n\tprintf "no newline"; printf "\\n"\n\tx = sprintf("%03d-%s", 7, "x"); print x, length(x)\n}\n')
add("builtins", "case_fns", "{ print toupper($0), tolower($0), toupper(substr($1, 1, 1)) substr($1, 2) }\n", "Hello World\nMiXeD case 123\n")
add("builtins", "math_fns", 'BEGIN {\n\tprint int(3.9), int(-3.9), int("12abc"), sqrt(16), exp(0), log(1)\n\tprintf "%.5f %.5f %.5f %.4f %.4f\\n", sin(0), cos(0), atan2(0, -1), exp(1), log(10)\n\tprint (2 ^ 0.5 > 1.41), 10 % 3, -10 % 3, 5.5 % 2\n}\n')
add("builtins", "match_fn", '{ if (match($0, /o+/)) print RSTART, RLENGTH, substr($0, RSTART, RLENGTH); else print RSTART, RLENGTH }\n', "foobar\nbazooo\nnothing here\nxyz\n")
add("builtins", "getline_file", 'NR == 1 { while ((getline l < FILENAME) > 0) n++; print "lines:", n; close(FILENAME) }\nNR == 2 { getline first < FILENAME; print "first again:", first }\n', "alpha\nbeta\ngamma\n")
add("builtins", "string_to_number", 'BEGIN { print "3x" + 1, " 12 " * 2, "abc" + 0, "1e2" + 0, ".5" + 0, "+7" - 1, "-" + 0 }\n')
# mawk keeps the backslash of an undefined escape; this case's .out is
# hand-corrected to the onetrue-awk/gawk answer (listed in golden/OVERRIDES).
add("builtins", "undefined_escapes", '{ s = $0; sub(/o/, "[\\&]", s); t = $0; gsub(/o/, "\\\\&", t); u = $0; sub(/o/, v, u)\n  print s, t, u, "\\q", length("\\q"), ($0 ~ "f\\.o"), ($0 ~ "f\\\\.o") }\n', "foo\nf.o\n", "-v 'v=<\\&>'")
# substr with a start below 1 or fractional arguments: onetrue-awk and gawk
# clamp the start to 1 and truncate; mawk shortens the length for a negative
# start, so this .out is hand-corrected (listed in golden/OVERRIDES).
add("builtins", "substr_clamp", '''{
	print "[" substr($0, 0) "]", "[" substr($0, 0, 2) "]", "[" substr($0, -1, 3) "]", "[" substr($0, -3, 1) "]"
	print "[" substr($0, 2.7) "]", "[" substr($0, 1, 2.5) "]", "[" substr($0, 1.5, 1.5) "]", "[" substr($0, -0.5, 2) "]"
	print "[" substr($0, 2, -1) "]", "[" substr($0, 4, 10) "]", "[" substr($0, 7) "]", "[" substr($0, "x") "]"
}
''', "hello\nab\n")

# ---------------- functions (user-defined)
add("functions", "call_return", r'''# A function may be used before its definition; a newline may come
# before the body and after commas; ";" may follow a definition.
BEGIN {
	print max(3, 7), max("abc", "abd"), add(1,
	    2) add(3, 4)
	print twice(twice(5)), max(add(1, 1), 1) * 10
}
function max(m, n) { return m > n ? m : n }
function add(a,
             b)
{
	return a + b
};
function twice(x) { return 2 * x }
''')
add("functions", "return_values", r'''function none() { return }
function fall_off() { x = 1 }
function paren() { return (42) }
function newline() { return 7
}
function re() { return /an/ }
function id(v) { return v }
{
	a = none(); b = fall_off()
	print "[" a "]", a + 0, length(b), paren(), newline(), re()
	# $1 is a numeric string: it still compares as a number after a return
	print (id($1) < 9), (id($1 "") < 9)
}
''', "10 banana\n")
add("functions", "scalars_by_value", r'''function change(s, e) { s = "changed"; e = "changed"; return s }
function shadow(x) { x = "local"; g = "global set in function"; return x }
BEGIN {
	s = "orig"; arr[1] = "elem"
	print change(s, arr[1]), s, arr[1]
	change(s, arr[2]); print (2 in arr)   # passing arr[2] creates it
	x = "global x"; print shadow(x), x; print g
}
''')
add("functions", "arrays_by_reference", r'''function fill(a, n,    i) { for (i = 1; i <= n; i++) a[i] = i * i }
function count(a,    k, n) { for (k in a) n++; return n + 0 }
function remove(a, k) { delete a[k] }
function clear(a) { delete a }
BEGIN {
	fill(sq, 5); print length(sq), count(sq), sq[3]
	remove(sq, 3); print length(sq), (3 in sq), (4 in sq)
	clear(sq); print length(sq); sq["new"] = 1; print length(sq)
}
''')
add("functions", "uninit_becomes_array", r'''# An unused variable passed to a function that uses the parameter as an
# array becomes that array in the caller, even through several calls.
function set(a, k, v) { a[k] = v }
function pass(b) { set(b, "deep", "yes") }
function local_demo(    loc) { pass(loc); return loc["deep"] }
function as_scalar(s) { s = 5 }
BEGIN {
	set(x, 1, "one"); print x[1], length(x)
	pass(y); print y["deep"]
	print local_demo()
	as_scalar(z); print "[" z "]"
}
''')
add("functions", "locals", r'''# Extra parameters are local variables; they start out uninitialized on
# every call and may be used as scalars or arrays.
function f(n,    loc, arr) { loc++; arr[n] = n; return loc + length(arr) }
function empty(    u) { return (u == 0) (u == "") length(u) }
function words(s,    w, n, i, out) {
	n = split(s, w)
	for (i = n; i >= 1; i--) out = out (i < n ? " " : "") w[i]
	return out
}
BEGIN { print f(1), f(2), f(3), empty(); print words("one two three") }
{ print words($0) }
''', "a b c\nhello world\n")
add("functions", "recursion", r'''function fact(n) { return n <= 1 ? 1 : n * fact(n - 1) }
function fib(n) { return n < 2 ? n : fib(n - 1) + fib(n - 2) }
function ack(m, n) {
	if (m == 0) return n + 1
	if (n == 0) return ack(m - 1, 1)
	return ack(m - 1, ack(m, n - 1))
}
function depth(n) { return n == 0 ? 0 : 1 + depth(n - 1) }
function fresh(n,    a) { a[n] = 1; if (n > 1) fresh(n - 1); return length(a) }
function hanoi(n, from, to, via) {
	if (n == 0) return
	hanoi(n - 1, from, via, to); moves++; hanoi(n - 1, via, to, from)
}
BEGIN {
	print fact(10), fact(20), fib(20), ack(2, 3), ack(3, 3)
	print depth(3000), fresh(5)
	hanoi(10, "A", "C", "B"); print moves
}
''')
add("functions", "builtins_on_params", r'''function edit(arr, s,    n, w) {
	n = split("a b c", w); arr["n"] = n
	sub(/b/, "B", arr["x"])
	s = "zzz"; gsub(/z/, "y", s)
	if (match(s, /y+/)) arr["m"] = RSTART "," RLENGTH
	return n w[3] s length(arr) (("x") in arr)
}
function lines(file, a,    line, n) {
	while ((getline line < file) > 0) a[++n] = line
	close(file)
	return n
}
BEGIN { a["x"] = "abc"; print edit(a), a["x"], a["n"], a["m"] }
NR == 1 { n = lines(FILENAME, all); print n, all[1], all[n] }
''', "first\nsecond\nthird\n")
add("functions", "per_record", r'''# Functions in patterns and actions, with fields as arguments.
function big(x) { return x > 4 }
function field(n) { return $n }
function rate(pay, hours) { return hours > 0 ? pay * hours : "none" }
big($2) { print "big:", $1 }
{ print field(1), field(NF), rate($2, $3) }
END { print NR, field(1) }
''', EMP)
add("functions", "exit_in_function", r'''function stop(code) { print "stopping"; exit code; print "not reached" }
BEGIN { print "begin" }
NR == 2 { stop(3) }
{ print }
END { print "end", NR; stop(4); print "not reached" }
''', "a\nb\nc\n")
add("functions", "sort_and_join", r'''# Classic helpers: insertion sort of an array parameter, and join.
function isort(A, n,    i, j, t) {
	for (i = 2; i <= n; i++) {
		t = A[i]
		for (j = i; j > 1 && A[j-1] > t; j--) A[j] = A[j-1]
		A[j] = t
	}
}
function join(A, n, sep,    i, s) {
	for (i = 1; i <= n; i++) s = s (i > 1 ? sep : "") A[i]
	return s
}
{ v[NR] = $1 }
END { isort(v, NR); print join(v, NR, ",") }
''', "10\n2\n33\n4\n-5\n2.5\n")
# mawk refuses a call with more arguments than the function has parameters;
# onetrue-awk and gawk warn (on stderr) and run it: see golden/OVERRIDES.
add("functions", "extra_args", r'''function g(a) { return a * 2 }
BEGIN { print g(1, x++), x }
''')

# ---------------- range patterns (start, end)
# "start" opens a range and "end" closes it; record 6 holds both words (a
# range of one record) and the range opened on record 8 never closes.
RANGES = "a\nstart\nx\nend\ny\nstart end\nz\nstart\nw\n"
add("ranges", "regex_endpoints", r"""# Without an action a range prints its records.
/start/, /end/
""", RANGES)
add("ranges", "expression_endpoints", r"""NR == 2, NR == 4 { print "nr", NR }
$1 == "y", $2 == "end" { print "fields", NR, $0 }
NR == 8, 0 { print "to eof", NR }
""", RANGES)
add("ranges", "restart_sections", r"""# Print each [section] of a config-like file, numbering the sections.
/^\[/, /^$/ { if (/^\[/) n++; if (NF) print n ": " $0 }
""", "junk\n[one]\na=1\nb=2\n\nmore junk\n[two]\nc=3\n\n[three]\nd=4\n")
add("ranges", "independent_rules", r"""# Overlapping ranges, and two identical rules, each keep their own state.
/start/, /end/ { print "A" NR }
/x/, /y/       { print "B" NR }
NR == 2, NR == 3 { print "C" NR }
NR == 2, NR == 3 { print "D" NR }
""", RANGES)
add("ranges", "evaluation_order", r"""# s counts tests of the start condition, e tests of the end condition:
# start is tested only while the range is closed, end only while it is open
# (including on the record that opened it).
(s++ >= 0 && $1 % 3 == 1), (e++ >= 0 && $1 % 3 == 0) { print $1 }
END { print "start tested", s, "end tested", e }
""", "1\n2\n3\n4\n5\n6\n7\n")
add("ranges", "next_and_getline", r"""# Record 3 is skipped by next before the range rule sees it.  The end
# condition is tested before the action runs, so next in the action on
# record 4 ("end") does not keep the first range open.  In the second range,
# getline on record 6 swallows record 7 ("z"), which is then never tested
# as an end, so that range stays open to the end of the input.
NR == 3 { print "skip", NR; next }
/start/, /end/ { print "range", NR; if (NR == 4) next }
/end/, /z/ { print "r2", NR; if ($0 == "start end") { getline; print "getline", NR, $0 } }
{ print "rest", NR }
""", RANGES)
add("ranges", "across_files", r"""# Read the input twice (as two files): a range may continue from one input
# file into the next.
BEGIN { ARGV[2] = ARGV[1]; ARGC = 3 }
FNR == 4, FNR == 2 { print (NR > FNR ? "second" : "first"), FNR }
""", "1\n2\n3\n4\n5\n")
add("ranges", "precedence_and_newline", r"""# The comma binds loosest: the whole || is the start condition.  A newline
# may follow the comma, and ; separates pattern-only rules.
$1 == "a" || $1 == "z", /x/ { print "or", NR }
x = /start/, /end/ { print "assign", NR, x }
$1 == "y" ? 1 : 0,
/z/ { print "cond", NR }
/w/, /w/; /a/
""", RANGES)
add("ranges", "exit_in_range", r"""NR == 2, NR == 4 { print; if (NR == 3) exit }
END { print "end at", NR }
""", "1\n2\n3\n4\n5\n")

# ---------------- environ
# Each case gets its own environment variables from NAME.environ, so the
# values printed are known.  Names start with PYAWK_T_ to stay clear of
# whatever the real environment holds.
ENVV = {
    "PYAWK_T_NUM": "12", "PYAWK_T_SPACED": " 3.5 ", "PYAWK_T_EXP": "1e3",
    "PYAWK_T_SIGN": "+5", "PYAWK_T_HEX": "0x1A", "PYAWK_T_STR": "abc",
    "PYAWK_T_EMPTY": "", "PYAWK_T_ESC": "a\\tb", "PYAWK_T_PREFIX": "10x",
}
add("environ", "values", r"""# ENVIRON values are the exact text of each variable: no escape
# processing (\t stays two characters) and no trimming.
BEGIN {
	print "[" ENVIRON["PYAWK_T_NUM"] "]", "[" ENVIRON["PYAWK_T_SPACED"] "]"
	print "[" ENVIRON["PYAWK_T_ESC"] "]", length(ENVIRON["PYAWK_T_ESC"])
	print "[" ENVIRON["PYAWK_T_EMPTY"] "]", ("PYAWK_T_EMPTY" in ENVIRON)
	print ("PYAWK_T_MISSING" in ENVIRON), "[" ENVIRON["PYAWK_T_MISSING"] "]"
	# Referencing a missing element creates it, as for any array.
	print ("PYAWK_T_MISSING" in ENVIRON)
}
""", environ=ENVV)
add("environ", "strnum", r"""# Values that look like numbers are numeric strings (strnums): they
# compare as numbers.  Others compare as strings.
BEGIN {
	print "12 < 9:", (ENVIRON["PYAWK_T_NUM"] < 9)            # numeric: 0
	print "spaced == 3.5:", (ENVIRON["PYAWK_T_SPACED"] == 3.5)
	print "1e3 == 1000:", (ENVIRON["PYAWK_T_EXP"] == 1000)
	print "+5 == 5:", (ENVIRON["PYAWK_T_SIGN"] == 5)
	print "0x1A == 26:", (ENVIRON["PYAWK_T_HEX"] == 26)      # not hex: 0
	print "abc < 1:", (ENVIRON["PYAWK_T_STR"] < 1)           # string compare
	print "empty == 0:", (ENVIRON["PYAWK_T_EMPTY"] == 0)     # "" is not numeric
	print "10x == 10:", (ENVIRON["PYAWK_T_PREFIX"] == 10)    # string compare: 0
	print "10x + 0:", ENVIRON["PYAWK_T_PREFIX"] + 0          # but converts to 10
	print "sum:", ENVIRON["PYAWK_T_NUM"] + ENVIRON["PYAWK_T_SPACED"]
}
""", environ=ENVV)
add("environ", "array_ops", r"""# ENVIRON is an ordinary array: in, for-in, delete, element
# assignment and passing to a function all work.
function show(env, name) { return name "=" env[name] }
BEGIN {
	n = 0
	for (k in ENVIRON)
		if (k ~ /^PYAWK_T_/) n++
	print "test variables:", n
	ENVIRON["PYAWK_T_NUM"]++
	print show(ENVIRON, "PYAWK_T_NUM")
	delete ENVIRON["PYAWK_T_STR"]
	print ("PYAWK_T_STR" in ENVIRON)
	ENVIRON["PYAWK_T_NEW"] = "added"
	print show(ENVIRON, "PYAWK_T_NEW")
	m = split(ENVIRON["PYAWK_T_SPACED"], parts, ".")
	print m, parts[1] "|" parts[2]
}
""", environ=ENVV)
add("environ", "child_processes", r"""# Changing ENVIRON does not change the environment of commands that
# awk runs: system(), "cmd" | getline and print | "cmd" all still see the
# original values (onetrue-awk, gawk --posix and mawk agree).
BEGIN {
	ENVIRON["PYAWK_T_NUM"] = "changed"
	ENVIRON["PYAWK_T_NEW"] = "new"
	delete ENVIRON["PYAWK_T_STR"]
	system("echo system: $PYAWK_T_NUM $PYAWK_T_STR [$PYAWK_T_NEW]")
	"echo getline: $PYAWK_T_NUM $PYAWK_T_STR [$PYAWK_T_NEW]" | getline line
	print line
	print "x" | "echo pipe: $PYAWK_T_NUM $PYAWK_T_STR [$PYAWK_T_NEW]; cat >/dev/null"
	close("echo pipe: $PYAWK_T_NUM $PYAWK_T_STR [$PYAWK_T_NEW]; cat >/dev/null")
	print "awk sees:", ENVIRON["PYAWK_T_NUM"], ENVIRON["PYAWK_T_NEW"]
}
""", environ=ENVV)
add("environ", "main_rules", r"""# ENVIRON is set before BEGIN and usable anywhere, e.g. to pass a
# threshold into a pattern.
$2 > ENVIRON["PYAWK_T_NUM"] { print $1, "over" }
END { print "limit", ENVIRON["PYAWK_T_NUM"] + 0 }
""", "a 5\nb 12\nc 13\nd 100\ne 9\n", environ=ENVV)

# ---------------- io
# Golden cases run in a throwaway copy of the category directory, so these
# programs may create and read back files with relative names.  Output
# written to files is shown by reading it back with getline, after close().
IO_LINES = "one 1\ntwo 2 x\nthree 3\n"
DUMP = r"""function dump(f,    line) {
	close(f)
	while ((getline line < f) > 0)
		print f ": " line
	close(f)
}
"""
add("io", "getline_main", r"""# Plain getline reads the next main-input record into $0 (setting NF,
# NR and FNR); getline var sets only var, NR and FNR.  Both return 1, or
# 0 at the end of the input.  Reading crosses into the next file.
NR == 1 { r = getline; print "getline:", r, NR, FNR, NF, $0 }
NR == 3 { r = getline v; print "getline v:", r, NR, FNR, NF, $0, "v=" v }
FNR == 1 && NR > 1 { print "new file", FILENAME, NR, FNR }
END {
	r = getline; print "END getline:", r, NR, $0
	r = getline v; print "END getline v:", r
}
""", operands="getline_main.in getline_file.in", inp=IO_LINES)
add("io", "getline_file", r"""# getline < file reads the file's next record into $0 (and NF);
# getline var < file sets only var.  NR and FNR do not change.  The
# result is 1, 0 at end of file, -1 if the file cannot be read.
{
	f = "getline_file.in"
	while ((r = (getline < f)) > 0)
		print r, NR, FNR, NF, $0
	print "end:", r, (getline < f)
	close(f)
	while ((getline line < f) > 0)
		print NR, FNR, NF, $0, "[" line "]"
	close(f)
	getline $2 < f              # a field as the target rebuilds $0
	print $0, NF
	print "missing:", (getline < "no such file"), (getline line < "no such file")
	print "directory:", (getline line < "/")
	exit
}
""", "a b c\n")
add("io", "getline_cmd", r"""# "cmd" | getline reads the command's output into $0 (and NF);
# "cmd" | getline var sets only var.  NR and FNR are left alone (all
# three reference awks agree, although POSIX says NR is incremented).
{
	cmd = "printf \"p q\\nr s t\\n\""
	while ((cmd | getline) > 0)
		print NR, FNR, NF, $0
	close(cmd)
	while ((cmd | getline line) > 0)
		print NR, FNR, NF, $0, "[" line "]"
	print "at end:", (cmd | getline line)
	close(cmd)
	# The same command string after close() runs the command again.
	cmd | getline; print "again:", $0
	close(cmd)
}
""", "x y\n")
add("io", "getline_begin", r"""# getline in BEGIN takes the first record of the main input, so the
# main rules start from the second; FILENAME is set by then.
BEGIN {
	r = getline
	print "BEGIN:", r, FILENAME, NR, FNR, $0
	r = getline second
	print "BEGIN var:", r, NR, second
}
{ print "main:", FILENAME, NR, FNR, $0 }
END { print "END:", (getline), NR }
""", IO_LINES)
add("io", "getline_stdin", r"""# With files named on the command line, "-" in getline still means
# standard input.
{
	if ((getline a < "-") > 0)
		print $0, "|", a
}
END { print "after:", (getline a < "-"), a }
""", IO_LINES, stdin="s1\ns2\n")
add("io", "getline_dev_stdin", r"""# "/dev/stdin" in getline is standard input too.
{
	if ((getline a < "/dev/stdin") > 0)
		print $0, "|", a
}
""", IO_LINES, stdin="s1\ns2\n")
add("io", "redirect_files", DUMP + r"""# > truncates a file only when it is first opened; later prints append
# until close().  >> appends.  One name is one stream, whichever of > or
# >> wrote to it first.
BEGIN {
	print "old" > "t1"; close("t1")
	print "a" > "t1"; print "b" > "t1"; printf "%s-%d\n", "c", 3 > "t1"
	dump("t1")
	print "after close" > "t1"; dump("t1")
	print "x" >> "t1"; printf("%s\n", "y") >> "t1"; dump("t1")
	print "first" >> "t2"; print "second" > "t2"; dump("t2")
	print "list", "of", "items" > "t3"; print("paren", "list") > "t3"; dump("t3")
}
""")
add("io", "redirect_pipes", r"""# print | "cmd" starts the command once and feeds it every line until
# close(), which waits for it to finish.
BEGIN {
	print "zebra" | "sort"
	print "apple" | "sort"
	printf "%s\n", "mango" | "sort"
	close("sort")
	print "-- sorted"
	print "b" | "sort -r"; print "c" | "sort -r"; close("sort -r")
	print "a" | "sort"; close("sort")
	print "-- done"
}
""")
add("io", "close_values", r"""# close() returns 0 for a file, -1 for a name that is not open, and for
# a command its exit status.
BEGIN {
	print "x" > "c1"
	print "file:", close("c1"), "again:", close("c1"), "never:", close("never opened")
	getline line < "c1"
	print "input file:", close("c1")
	print "y" | "cat >/dev/null"
	print "output pipe:", close("cat >/dev/null")
	"echo hi" | getline line
	print "input pipe:", close("echo hi")
}
""")
add("io", "close_status", r"""# close() of a command returns its exit status (mawk; the user chose this
# over the 0 that onetrue-awk and gawk --posix return), and 256 + N for a
# command killed by signal N.
BEGIN {
	print "x" | "cat >/dev/null; exit 3"
	print "output pipe:", close("cat >/dev/null; exit 3")
	"echo hi; exit 5" | getline line
	print "input pipe:", line, close("echo hi; exit 5")
	"kill -9 $$" | getline line
	print "killed:", close("kill -9 $$")
}
""")
add("io", "system_calls", r"""# system() returns the command's exit status (256 + N if killed by
# signal N) and flushes awk's output first, so the order is preserved and
# the command sees what was written to files.
BEGIN {
	printf "before "
	system("echo during")
	print "after"
	print "exit 3:", system("exit 3"), "true:", system("true")
	print "killed:", system("kill -9 $$")
	print "in the file" > "s1"
	system("cat s1")
	print "flushed"
	system("")
	print "end"
}
""")
add("io", "fflush_calls", r"""# fflush() and fflush("") flush everything; fflush(name) flushes one
# output file or command.  They return 0, or -1 if name is not open
# for output (input files and commands included).
BEGIN {
	print "fflush():", fflush(), "fflush(\"\"):", fflush("")
	print "stdout:", fflush("/dev/stdout"), "stderr:", fflush("/dev/stderr")
	print "not open:", fflush("not open")
	print "data" > "ff"
	r = fflush("ff")
	print "file:", r
	system("cat ff")
	print "p" | "cat > piped"
	r = fflush("cat > piped")
	print "pipe:", r
	"echo hi" | getline line
	print "input pipe:", fflush("echo hi")
	close("ff")
	getline line < "ff"
	print "input file:", fflush("ff")
}
""")
add("io", "special_files", DUMP + r"""# /dev/stdout is standard output and /dev/stderr standard error.  "-"
# is an ordinary file name for output (mawk and onetrue-awk; only gawk
# treats it as standard output).
BEGIN {
	print "o1"
	print "o2" > "/dev/stdout"
	printf "o3\n" > "/dev/stdout"
	print "to stderr" > "/dev/stderr"
	print "dash" > "-"
	close("-")
	system("sed 's/^/file - holds: /' ./-")
	print "o4"
}
""")
add("io", "close_pipe_order", r"""# close() waits for the command but does not flush awk's own pending
# output first, so the command's output can come before "mid" (mawk and
# onetrue-awk; gawk flushes first).  Starting a command does flush, so
# "start" always comes first.
BEGIN {
	print "start"
	print "pipe output" | "cat"
	print "mid"
	close("cat")
	print "end"
}
""")
add("io", "exit_pipe_then_stdout", r"""# At exit, open commands are closed (and waited for) before awk's own
# buffered output is flushed (mawk and gawk; onetrue-awk flushes first).
BEGIN {
	print "pipe output" | "cat"
	print "awk output"
}
""")
add("io", "precedence", DUMP + r"""# Redirection and getline precedence quirks:
# print > "a" "b" writes to file "ab" (the target is a concatenation);
# "cmd" | getline > 0 is ("cmd" | getline) > 0;
# getline < "f" "1" is (getline < "f") "1" (mawk and gawk; onetrue-awk
# reads file "f1").
BEGIN {
	print "x" > "a" "b"; dump("ab")
	while ("echo l1; echo l2" | getline > 0) print "got", $0
	x = "echo yo"; x | getline v; print v
	print "q" > "f"; close("f")
	$0 = "unchanged"; r = getline < "f" "1"; print r, $0
	while (getline line < "ab" > 0) print "unparenthesized:", line
}
""")
add("io", "pipe_getline_concat", r"""# "echo " "hi" | getline runs the command "echo hi": the concatenation
# is the command (onetrue-awk and gawk; mawk runs "hi" and concatenates).
BEGIN {
	"echo " "hi" | getline
	print
	close("echo hi")
	"echo " "hi" | getline line
	print line
}
""")
add("io", "redirect_concat_field", r"""# The target of > may be built from fields: one file per key.
{ print $2 > ($1 ".txt") }
{ print $2 > $1 ".out" }
END {
	close("k1.txt"); close("k2.txt"); close("k1.out")
	while ((getline l < "k1.txt") > 0) print "k1.txt:", l
	while ((getline l < "k2.txt") > 0) print "k2.txt:", l
	while ((getline l < "k1.out") > 0) print "k1.out:", l
}
""", "k1 a\nk2 b\nk1 c\n")

# ---------------- one-liners (from "The AWK Programming Language")
add("one_liners", "count_lines", "END { print NR }\n", EMP)
add("one_liners", "third_line", "NR == 3\n", EMP)
add("one_liners", "last_field", "{ print $NF }\n", TEXT)
add("one_liners", "more_than_3_fields", "NF > 3\n", TEXT)
add("one_liners", "total_fields", "{ nf += NF }\nEND { print nf }\n", TEXT)
add("one_liners", "count_matches", "/Beth|Mary/ { n++ }\nEND { print n + 0 }\n", EMP)
add("one_liners", "max_field", "$3 > max { max = $3; line = $0 }\nEND { print max, line }\n", EMP)
add("one_liners", "number_lines", "{ print NR, $0 }\n", EMP)
add("one_liners", "replace_first_field", "{ $1 = NR; print }\n", EMP)
add("one_liners", "sum_average", "{ pay += $2 * $3 }\nEND { print NR, \"employees\"; print \"total pay is\", pay; print \"average pay is\", pay / NR }\n", EMP)
add("one_liners", "long_lines", "length($0) > 12\n", EMP)
add("one_liners", "reverse_fields", '{ for (i = NF; i > 0; i--) printf "%s ", $i; print "" }\n', EMP)
add("one_liners", "dedupe_lines", "!seen[$0]++\n", "a\nb\na\nc\nb\n")
add("one_liners", "gsub_vowels", '{ gsub(/[aeiou]/, "#"); print }\n', EMP)
add("one_liners", "swap_fields", '{ temp = $1; $1 = $2; $2 = temp; print }\n', EMP)
add("one_liners", "nonempty_lines", "NF\n", TEXT)

# .items() yields (key, value) pairs; both are tuples, unpacked in one go.
for (cat, name), (prog, inp, args, extras) in cases.items():
    d = os.path.join(ROOT, cat)
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, name + ".awk"), "w") as f:
        f.write(prog)
    with open(os.path.join(d, name + ".in"), "w") as f:
        f.write(inp)
    if args:
        with open(os.path.join(d, name + ".args"), "w") as f:
            f.write(args + "\n")
    for ext, content in extras.items():
        with open(os.path.join(d, name + ext), "w") as f:
            f.write(content)
print(len(cases), "cases")
