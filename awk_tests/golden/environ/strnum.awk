# Values that look like numbers are numeric strings (strnums): they
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
