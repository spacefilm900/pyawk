# An unused variable passed to a function that uses the parameter as an
# array becomes that array in the caller, even through several calls.
function set(a, k, v) { a[k] = v }
function pass(b) { set(b, "deep", "yes") }
function local_demo(    loc) { pass(loc); return loc["deep"] }
function as_scalar(s) { s = 5 }
BEGIN {
	set(x, 1, "one"); print x[1], length(x)
	pass(y); print y["deep"]
	print local_demo()
	as_scalar(z); print "[" z "]"
}
