# At exit, open commands are closed (and waited for) before awk's own
# buffered output is flushed (mawk and gawk; onetrue-awk flushes first).
BEGIN {
	print "pipe output" | "cat"
	print "awk output"
}
