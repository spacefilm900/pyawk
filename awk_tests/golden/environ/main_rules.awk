# ENVIRON is set before BEGIN and usable anywhere, e.g. to pass a
# threshold into a pattern.
$2 > ENVIRON["PYAWK_T_NUM"] { print $1, "over" }
END { print "limit", ENVIRON["PYAWK_T_NUM"] + 0 }
