# Classic helpers: insertion sort of an array parameter, and join.
function isort(A, n,    i, j, t) {
	for (i = 2; i <= n; i++) {
		t = A[i]
		for (j = i; j > 1 && A[j-1] > t; j--) A[j] = A[j-1]
		A[j] = t
	}
}
function join(A, n, sep,    i, s) {
	for (i = 1; i <= n; i++) s = s (i > 1 ? sep : "") A[i]
	return s
}
{ v[NR] = $1 }
END { isort(v, NR); print join(v, NR, ",") }
