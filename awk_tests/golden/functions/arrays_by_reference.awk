function fill(a, n,    i) { for (i = 1; i <= n; i++) a[i] = i * i }
function count(a,    k, n) { for (k in a) n++; return n + 0 }
function remove(a, k) { delete a[k] }
function clear(a) { delete a }
BEGIN {
	fill(sq, 5); print length(sq), count(sq), sq[3]
	remove(sq, 3); print length(sq), (3 in sq), (4 in sq)
	clear(sq); print length(sq); sq["new"] = 1; print length(sq)
}
