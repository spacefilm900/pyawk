# nextfile on standard input ("-") throws its remaining lines away: a
# later "-" operand, or getline < "-", finds nothing (mawk and gawk;
# onetrue-awk carries on reading).
{ print (FILENAME == "-" ? "stdin" : FILENAME), FNR, $0; nextfile }
END { print "NR", NR; print "getline:", (getline line < "-") }
