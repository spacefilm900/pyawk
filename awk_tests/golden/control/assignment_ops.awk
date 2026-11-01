BEGIN {
	x = 10; x += 5; print x
	x -= 3; print x
	x *= 2; print x
	x /= 4; print x
	x %= 4; print x
	x ^= 3; print x
	y = z = 7; print y, z
	i = 5; print i++, i, ++i, i--, --i, i
	print (1 ? "yes" : "no"), (0 ? "a" : 1 ? "b" : "c")
}
