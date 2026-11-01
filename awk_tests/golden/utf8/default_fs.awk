# Default field splitting is at spaces, tabs and newlines only, not
# at Unicode spaces such as NO-BREAK SPACE or IDEOGRAPHIC SPACE.
{ print NF; for (i = 1; i <= NF; i++) print i ": " $i }
