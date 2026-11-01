# close() returns 0 for a file, -1 for a name that is not open, and for
# a command its exit status.
BEGIN {
	print "x" > "c1"
	print "file:", close("c1"), "again:", close("c1"), "never:", close("never opened")
	getline line < "c1"
	print "input file:", close("c1")
	print "y" | "cat >/dev/null"
	print "output pipe:", close("cat >/dev/null")
	"echo hi" | getline line
	print "input pipe:", close("echo hi")
}
