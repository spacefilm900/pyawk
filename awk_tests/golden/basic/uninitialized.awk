BEGIN {
	print x + 0, "[" x "]", length(x)
	if (x == 0 && x == "") print "both zero and empty"
	if (!x) print "false"
	y[1]; print length(y[1]), (1 in y), (2 in y)
}
