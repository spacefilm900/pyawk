function change(s, e) { s = "changed"; e = "changed"; return s }
function shadow(x) { x = "local"; g = "global set in function"; return x }
BEGIN {
	s = "orig"; arr[1] = "elem"
	print change(s, arr[1]), s, arr[1]
	change(s, arr[2]); print (2 in arr)   # passing arr[2] creates it
	x = "global x"; print shadow(x), x; print g
}
