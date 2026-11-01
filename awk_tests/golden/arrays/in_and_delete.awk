BEGIN {
	a["x"] = 1; a["y"] = 2
	if ("x" in a) print "x in a"
	delete a["x"]
	if (!("x" in a)) print "x gone"
	n = 0; for (k in a) n++; print n
	delete a
	m = 0; for (k in a) m++; print m
}
