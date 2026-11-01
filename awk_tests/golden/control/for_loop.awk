BEGIN {
	for (i = 1; i <= 3; i++) {
		line = ""
		for (j = 1; j <= 3; j++)
			line = line (i * j) " "
		print line
	}
	for (;;) { k++; if (k > 4) break }
	print k
}
