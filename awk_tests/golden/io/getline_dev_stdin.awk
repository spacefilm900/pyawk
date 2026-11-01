# "/dev/stdin" in getline is standard input too.
{
	if ((getline a < "/dev/stdin") > 0)
		print $0, "|", a
}
