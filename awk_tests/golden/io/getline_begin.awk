# getline in BEGIN takes the first record of the main input, so the
# main rules start from the second; FILENAME is set by then.
BEGIN {
	r = getline
	print "BEGIN:", r, FILENAME, NR, FNR, $0
	r = getline second
	print "BEGIN var:", r, NR, second
}
{ print "main:", FILENAME, NR, FNR, $0 }
END { print "END:", (getline), NR }
