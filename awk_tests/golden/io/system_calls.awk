# system() returns the command's exit status (256 + N if killed by
# signal N) and flushes awk's output first, so the order is preserved and
# the command sees what was written to files.
BEGIN {
	printf "before "
	system("echo during")
	print "after"
	print "exit 3:", system("exit 3"), "true:", system("true")
	print "killed:", system("kill -9 $$")
	print "in the file" > "s1"
	system("cat s1")
	print "flushed"
	system("")
	print "end"
}
