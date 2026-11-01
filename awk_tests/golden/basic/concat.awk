BEGIN {
	x = "foo"; y = "bar"
	print x y, x "-" y
	print 1 2, 1 " " 2
	print 1+2 "3", "a" 1+1 "b"
	z = x y x; print z, length(z)
}
