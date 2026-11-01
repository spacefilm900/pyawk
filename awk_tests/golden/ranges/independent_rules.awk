# Overlapping ranges, and two identical rules, each keep their own state.
/start/, /end/ { print "A" NR }
/x/, /y/       { print "B" NR }
NR == 2, NR == 3 { print "C" NR }
NR == 2, NR == 3 { print "D" NR }
