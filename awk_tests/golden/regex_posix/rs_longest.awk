# A regex RS matches the longest record separator.
BEGIN { RS = "x|xy" }
{ print NR ": [" $0 "]" }
