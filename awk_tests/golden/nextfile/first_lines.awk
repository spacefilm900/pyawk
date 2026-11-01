# The classic use: the first line of each file, without reading the rest.
FNR == 1 { print FILENAME ": " $0; n++; nextfile }
{ print "never", $0 }
END { print n, "files,", NR, "records read" }
