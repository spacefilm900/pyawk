# leading comment
BEGIN {   # trailing comment
	x = 1 + \
	    2
	print x ; print "a",
	      "b"
	if (x == 3 &&
	    x > 0) print "ok"
}


{ print NR ": " $0 }   # per-record
