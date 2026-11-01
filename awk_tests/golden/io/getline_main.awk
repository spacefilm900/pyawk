# Plain getline reads the next main-input record into $0 (setting NF,
# NR and FNR); getline var sets only var, NR and FNR.  Both return 1, or
# 0 at the end of the input.  Reading crosses into the next file.
NR == 1 { r = getline; print "getline:", r, NR, FNR, NF, $0 }
NR == 3 { r = getline v; print "getline v:", r, NR, FNR, NF, $0, "v=" v }
FNR == 1 && NR > 1 { print "new file", FILENAME, NR, FNR }
END {
	r = getline; print "END getline:", r, NR, $0
	r = getline v; print "END getline v:", r
}
