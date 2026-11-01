BEGIN { a = 1; b = 2;; c = 3 }
BEGIN {
	print a, b, c
};
END { print "done" }
