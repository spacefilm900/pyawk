function dump(f,    line) {
	close(f)
	while ((getline line < f) > 0)
		print f ": " line
	close(f)
}
# /dev/stdout is standard output and /dev/stderr standard error.  "-"
# is an ordinary file name for output (mawk and onetrue-awk; only gawk
# treats it as standard output).
BEGIN {
	print "o1"
	print "o2" > "/dev/stdout"
	printf "o3\n" > "/dev/stdout"
	print "to stderr" > "/dev/stderr"
	print "dash" > "-"
	close("-")
	system("sed 's/^/file - holds: /' ./-")
	print "o4"
}
