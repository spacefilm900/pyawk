# Extra parameters are local variables; they start out uninitialized on
# every call and may be used as scalars or arrays.
function f(n,    loc, arr) { loc++; arr[n] = n; return loc + length(arr) }
function empty(    u) { return (u == 0) (u == "") length(u) }
function words(s,    w, n, i, out) {
	n = split(s, w)
	for (i = n; i >= 1; i--) out = out (i < n ? " " : "") w[i]
	return out
}
BEGIN { print f(1), f(2), f(3), empty(); print words("one two three") }
{ print words($0) }
