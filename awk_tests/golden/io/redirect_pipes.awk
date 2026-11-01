# print | "cmd" starts the command once and feeds it every line until
# close(), which waits for it to finish.
BEGIN {
	print "zebra" | "sort"
	print "apple" | "sort"
	printf "%s\n", "mango" | "sort"
	close("sort")
	print "-- sorted"
	print "b" | "sort -r"; print "c" | "sort -r"; close("sort -r")
	print "a" | "sort"; close("sort")
	print "-- done"
}
