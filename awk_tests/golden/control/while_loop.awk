BEGIN {
	n = 10; f = 1; i = 1
	while (i <= n) {
		f *= i
		i++
	}
	print f
	while (0) print "never"
}
