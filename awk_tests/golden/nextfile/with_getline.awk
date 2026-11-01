# getline before nextfile reads on in the current file; getline < FILENAME
# is a separate stream, unaffected by nextfile.
FNR == 1 {
	getline
	print "getline:", FILENAME, NR, FNR, $0
	n = 0
	while ((getline line < FILENAME) > 0)
		n++
	close(FILENAME)
	print "lines in", FILENAME ":", n
	nextfile
}
END { print NR }
