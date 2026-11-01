function dump(f,    line) {
	close(f)
	while ((getline line < f) > 0)
		print f ": " line
	close(f)
}
# > truncates a file only when it is first opened; later prints append
# until close().  >> appends.  One name is one stream, whichever of > or
# >> wrote to it first.
BEGIN {
	print "old" > "t1"; close("t1")
	print "a" > "t1"; print "b" > "t1"; printf "%s-%d\n", "c", 3 > "t1"
	dump("t1")
	print "after close" > "t1"; dump("t1")
	print "x" >> "t1"; printf("%s\n", "y") >> "t1"; dump("t1")
	print "first" >> "t2"; print "second" > "t2"; dump("t2")
	print "list", "of", "items" > "t3"; print("paren", "list") > "t3"; dump("t3")
}
