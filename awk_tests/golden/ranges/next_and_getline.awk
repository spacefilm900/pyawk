# Record 3 is skipped by next before the range rule sees it.  The end
# condition is tested before the action runs, so next in the action on
# record 4 ("end") does not keep the first range open.  In the second range,
# getline on record 6 swallows record 7 ("z"), which is then never tested
# as an end, so that range stays open to the end of the input.
NR == 3 { print "skip", NR; next }
/start/, /end/ { print "range", NR; if (NR == 4) next }
/end/, /z/ { print "r2", NR; if ($0 == "start end") { getline; print "getline", NR, $0 } }
{ print "rest", NR }
