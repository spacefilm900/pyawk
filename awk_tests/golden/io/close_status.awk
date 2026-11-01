# close() of a command returns its exit status (mawk; the user chose this
# over the 0 that onetrue-awk and gawk --posix return), and 256 + N for a
# command killed by signal N.
BEGIN {
	print "x" | "cat >/dev/null; exit 3"
	print "output pipe:", close("cat >/dev/null; exit 3")
	"echo hi; exit 5" | getline line
	print "input pipe:", line, close("echo hi; exit 5")
	"kill -9 $$" | getline line
	print "killed:", close("kill -9 $$")
}
