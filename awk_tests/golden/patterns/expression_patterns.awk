NR % 2 == 0 { print "even:", $1 }
NR > 4 { print "late:", $1 }
length($1) > 4 { print "long:", $1 }
$3 { print "nonzero:", $1 }
