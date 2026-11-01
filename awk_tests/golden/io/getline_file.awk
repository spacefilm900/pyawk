# getline < file reads the file's next record into $0 (and NF);
# getline var < file sets only var.  NR and FNR do not change.  The
# result is 1, 0 at end of file, -1 if the file cannot be read.
{
	f = "getline_file.in"
	while ((r = (getline < f)) > 0)
		print r, NR, FNR, NF, $0
	print "end:", r, (getline < f)
	close(f)
	while ((getline line < f) > 0)
		print NR, FNR, NF, $0, "[" line "]"
	close(f)
	getline $2 < f              # a field as the target rebuilds $0
	print $0, NF
	print "missing:", (getline < "no such file"), (getline line < "no such file")
	print "directory:", (getline line < "/")
	exit
}
