NR == 1 { getline; print "after getline:", $0, NR }
{ print NR ": " $0 }
END { while ((getline line) > 0) n++; print n + 0 }
