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

# (category, name) -> (program, input, args); a dict keyed by a pair.
cases = {}
def add(cat, name, prog, inp="", args=""):
    cases[(cat, name)] = (prog, inp, args)

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
for (cat, name), (prog, inp, args) in cases.items():
    d = os.path.join(ROOT, cat)
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, name + ".awk"), "w") as f:
        f.write(prog)
    with open(os.path.join(d, name + ".in"), "w") as f:
        f.write(inp)
    if args:
        with open(os.path.join(d, name + ".args"), "w") as f:
            f.write(args + "\n")
print(len(cases), "cases")
