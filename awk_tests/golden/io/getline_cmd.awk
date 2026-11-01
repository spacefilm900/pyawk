# "cmd" | getline reads the command's output into $0 (and NF);
# "cmd" | getline var sets only var.  NR and FNR are left alone (all
# three reference awks agree, although POSIX says NR is incremented).
{
	cmd = "printf \"p q\\nr s t\\n\""
	while ((cmd | getline) > 0)
		print NR, FNR, NF, $0
	close(cmd)
	while ((cmd | getline line) > 0)
		print NR, FNR, NF, $0, "[" line "]"
	print "at end:", (cmd | getline line)
	close(cmd)
	# The same command string after close() runs the command again.
	cmd | getline; print "again:", $0
	close(cmd)
}
