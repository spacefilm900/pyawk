NR == 1 { while ((getline l < FILENAME) > 0) n++; print "lines:", n; close(FILENAME) }
NR == 2 { getline first < FILENAME; print "first again:", first }
