# ENVIRON is an ordinary array: in, for-in, delete, element
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
