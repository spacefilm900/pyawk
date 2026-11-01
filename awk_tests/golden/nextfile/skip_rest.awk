# nextfile abandons the current record and the rest of its file; the
# next record is the first of the next file, with FNR back to 1.  NR
# counts only the records actually read.
{ print FILENAME, NR, FNR, $0 }
FNR == 2 { nextfile }
{ print "  after", $0 }
END { print "END", NR, FNR, FILENAME, $0 }
