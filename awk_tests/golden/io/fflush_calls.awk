# fflush() and fflush("") flush everything; fflush(name) flushes one
# output file or command.  They return 0, or -1 if name is not open
# for output (input files and commands included).
BEGIN {
	print "fflush():", fflush(), "fflush(\"\"):", fflush("")
	print "stdout:", fflush("/dev/stdout"), "stderr:", fflush("/dev/stderr")
	print "not open:", fflush("not open")
	print "data" > "ff"
	r = fflush("ff")
	print "file:", r
	system("cat ff")
	print "p" | "cat > piped"
	r = fflush("cat > piped")
	print "pipe:", r
	"echo hi" | getline line
	print "input pipe:", fflush("echo hi")
	close("ff")
	getline line < "ff"
	print "input file:", fflush("ff")
}
