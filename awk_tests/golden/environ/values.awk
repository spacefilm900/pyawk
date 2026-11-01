# ENVIRON values are the exact text of each variable: no escape
# processing (\t stays two characters) and no trimming.
BEGIN {
	print "[" ENVIRON["PYAWK_T_NUM"] "]", "[" ENVIRON["PYAWK_T_SPACED"] "]"
	print "[" ENVIRON["PYAWK_T_ESC"] "]", length(ENVIRON["PYAWK_T_ESC"])
	print "[" ENVIRON["PYAWK_T_EMPTY"] "]", ("PYAWK_T_EMPTY" in ENVIRON)
	print ("PYAWK_T_MISSING" in ENVIRON), "[" ENVIRON["PYAWK_T_MISSING"] "]"
	# Referencing a missing element creates it, as for any array.
	print ("PYAWK_T_MISSING" in ENVIRON)
}
