BEGIN {
	a[1, 2] = 3
	for (k in a) { split(k, p, SUBSEP); print p[1], p[2], a[k] }
	if ((1, 2) in a) print "yes"
	if (!((2, 1) in a)) print "no"
}
