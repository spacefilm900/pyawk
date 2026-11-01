# Store every line, then print them last to first,
# with the tokens on each line also reversed.
#
# How AWK runs this: the first block, { line[NR] = $0 }, runs once for
# every input line.  $0 is the whole line and NR its line number, so the
# lines are saved in an array: line[1], line[2], ...  AWK arrays need no
# declaration or size; they grow as elements are assigned.
#
# The END block runs once, after the last line has been read; by then NR
# holds the total number of lines.  It walks r from NR down to 1.  For each
# saved line, split() breaks it into words tok[1] .. tok[n] (and returns n),
# and the inner loop joins them back in reverse order, putting a space
# between words (the ?: picks " " except before the first word).
# Writing two values side by side, as in  out " " tok[i], joins strings.
#   "1 The morning began quietly"  ->  "quietly began morning The 1"
# A blank input line has no words, so it prints as a blank line.
#
# Run:  python -m pyawk -f examples/reverse.awk examples/reverse.in
{ line[NR] = $0 }

END {
    for (r = NR; r >= 1; r--) {
        n = split(line[r], tok)
        out = ""
        for (i = n; i >= 1; i--)
            out = out (i < n ? " " : "") tok[i]
        print out
    }
}
