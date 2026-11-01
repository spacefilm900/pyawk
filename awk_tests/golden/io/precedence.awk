function dump(f,    line) {
	close(f)
	while ((getline line < f) > 0)
		print f ": " line
	close(f)
}
# Redirection and getline precedence quirks:
# print > "a" "b" writes to file "ab" (the target is a concatenation);
# "cmd" | getline > 0 is ("cmd" | getline) > 0;
# getline < "f" "1" is (getline < "f") "1" (mawk and gawk; onetrue-awk
# reads file "f1").
BEGIN {
	print "x" > "a" "b"; dump("ab")
	while ("echo l1; echo l2" | getline > 0) print "got", $0
	x = "echo yo"; x | getline v; print v
	print "q" > "f"; close("f")
	$0 = "unchanged"; r = getline < "f" "1"; print r, $0
	while (getline line < "ab" > 0) print "unparenthesized:", line
}
