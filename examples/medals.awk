# medals.awk -- report every word that contains GOLD or SILVER.
#
# How AWK runs this: AWK reads the input one line at a time and runs the
# { ... } block below for each line, with the line split into fields
# $1 .. $NF (NF = number of fields) and NR = the current line number.
#
# The loop visits each field $i.  index(s, t) returns the position of
# text t inside s, or 0 if absent, and 0 counts as "false".  So a field
# containing GOLD is reported as a WINNER, otherwise one containing SILVER
# as a RUNNER-UP, e.g.
#     Alice took GOLD in the 100m   ->   line 1, GOLD: WINNER
# Note it is a case-sensitive *substring* test: "GOLDEN" and "SILVERWARE"
# match, "gold" does not, and "GOLDSILVER" is only a WINNER (else-if).
#
# Run:  python -m pyawk -f examples/medals.awk examples/medals.in
{
    for (i = 1; i <= NF; i++) {
        if (index($i, "GOLD"))
            print "line " NR ", " $i ": WINNER"
        else if (index($i, "SILVER"))
            print "line " NR ", " $i ": RUNNER-UP"
    }
}
