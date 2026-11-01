# With files named on the command line, "-" in getline still means
# standard input.
{
	if ((getline a < "-") > 0)
		print $0, "|", a
}
END { print "after:", (getline a < "-"), a }
