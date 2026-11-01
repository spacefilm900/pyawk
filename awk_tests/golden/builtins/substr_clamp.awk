{
	print "[" substr($0, 0) "]", "[" substr($0, 0, 2) "]", "[" substr($0, -1, 3) "]", "[" substr($0, -3, 1) "]"
	print "[" substr($0, 2.7) "]", "[" substr($0, 1, 2.5) "]", "[" substr($0, 1.5, 1.5) "]", "[" substr($0, -0.5, 2) "]"
	print "[" substr($0, 2, -1) "]", "[" substr($0, 4, 10) "]", "[" substr($0, 7) "]", "[" substr($0, "x") "]"
}
