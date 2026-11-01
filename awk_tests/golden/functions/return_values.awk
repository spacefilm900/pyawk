function none() { return }
function fall_off() { x = 1 }
function paren() { return (42) }
function newline() { return 7
}
function re() { return /an/ }
function id(v) { return v }
{
	a = none(); b = fall_off()
	print "[" a "]", a + 0, length(b), paren(), newline(), re()
	# $1 is a numeric string: it still compares as a number after a return
	print (id($1) < 9), (id($1 "") < 9)
}
