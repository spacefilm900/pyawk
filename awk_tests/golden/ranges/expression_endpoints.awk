NR == 2, NR == 4 { print "nr", NR }
$1 == "y", $2 == "end" { print "fields", NR, $0 }
NR == 8, 0 { print "to eof", NR }
