# A range left open by nextfile stays open into the next file (range
# state is never reset between files).
/1/, /2/ { print "range:", FILENAME, $0 }
FNR == 1 { nextfile }
