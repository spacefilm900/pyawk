function stop(code) { print "stopping"; exit code; print "not reached" }
BEGIN { print "begin" }
NR == 2 { stop(3) }
{ print }
END { print "end", NR; stop(4); print "not reached" }
