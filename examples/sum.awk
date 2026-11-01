# sum.awk -- for each input line, add up the fields that are numbers.
#
# How AWK runs this: there is no explicit "read a line" loop.  AWK reads
# the input one line (a "record") at a time and runs the { ... } block
# below once per line.  Within the block:
#   $1, $2, ... $NF  are the line's whitespace-separated fields,
#   NF               is how many fields the line has,
#   NR               is the line number (1, 2, 3, ...).
#
# For every field that looks like a number (optional sign, digits, optional
# decimal point, as matched by the regular expression after ~), its value is
# added to `total`.  Lines with at least one number print their sum, e.g.
#     apples 3 oranges 5   ->   line 1: 8
# Lines without numbers ("no numbers here") print nothing.  A word with
# digits in it, like "abc123", is not a number and is skipped.
#
# Run:  python -m pyawk -f examples/sum.awk examples/sum.in
{
    total = 0
    found = 0
    for (i = 1; i <= NF; i++) {
        if ($i ~ /^[-+]?([0-9]+\.?[0-9]*|\.[0-9]+)$/) {
            total += $i
            found = 1
        }
    }
    if (found)
        print "line " NR ": " total
}
