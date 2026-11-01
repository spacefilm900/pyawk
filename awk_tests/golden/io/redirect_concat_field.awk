# The target of > may be built from fields: one file per key.
{ print $2 > ($1 ".txt") }
{ print $2 > $1 ".out" }
END {
	close("k1.txt"); close("k2.txt"); close("k1.out")
	while ((getline l < "k1.txt") > 0) print "k1.txt:", l
	while ((getline l < "k2.txt") > 0) print "k2.txt:", l
	while ((getline l < "k1.out") > 0) print "k1.out:", l
}
