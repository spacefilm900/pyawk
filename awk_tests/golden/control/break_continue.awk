BEGIN {
	for (i = 1; i <= 10; i++) {
		if (i % 2) continue
		if (i > 8) break
		printf "%d ", i
	}
	print ""
	i = 0
	while (1) { if (++i >= 5) break }
	print i
}
