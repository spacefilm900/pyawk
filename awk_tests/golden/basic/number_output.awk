BEGIN {
	print 0.1+0.2, 1/3, 100000*100000, 1e6, 123456789, 3.0, 1e-5
	OFMT = "%.2f"; x = 3.14159; print x; y = x ""; print y
	CONVFMT = "%.3f"; z = x ""; print z; print 17 ""
}
