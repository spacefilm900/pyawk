BEGIN {
	a[1] = "one"; print a["1"]
	a["01"] = "zero-one"; print a[1], a["01"]
	x = 0.1; b[x] = "tenth"; print b["0.1"]
	c[2+3] = "five"; print c["5"]
}
