function edit(arr, s,    n, w) {
	n = split("a b c", w); arr["n"] = n
	sub(/b/, "B", arr["x"])
	s = "zzz"; gsub(/z/, "y", s)
	if (match(s, /y+/)) arr["m"] = RSTART "," RLENGTH
	return n w[3] s length(arr) (("x") in arr)
}
function lines(file, a,    line, n) {
	while ((getline line < file) > 0) a[++n] = line
	close(file)
	return n
}
BEGIN { a["x"] = "abc"; print edit(a), a["x"], a["n"], a["m"] }
NR == 1 { n = lines(FILENAME, all); print n, all[1], all[n] }
