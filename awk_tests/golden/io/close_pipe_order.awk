# close() waits for the command but does not flush awk's own pending
# output first, so the command's output can come before "mid" (mawk and
# onetrue-awk; gawk flushes first).  Starting a command does flush, so
# "start" always comes first.
BEGIN {
	print "start"
	print "pipe output" | "cat"
	print "mid"
	close("cat")
	print "end"
}
