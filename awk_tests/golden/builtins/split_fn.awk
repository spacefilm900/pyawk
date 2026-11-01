BEGIN {
	n = split("a:b:c", arr, ":"); print n, arr[1], arr[3]
	n = split("  one  two three ", w); print n, w[1], w[3]
	n = split("a1b22c333d", r, /[0-9]+/); print n, r[1], r[2], r[4]
	n = split("", e); print n
	n = split("x,y", q, ","); print (q[1] < q[2])
}
