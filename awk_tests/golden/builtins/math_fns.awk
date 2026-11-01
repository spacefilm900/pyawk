BEGIN {
	print int(3.9), int(-3.9), int("12abc"), sqrt(16), exp(0), log(1)
	printf "%.5f %.5f %.5f %.4f %.4f\n", sin(0), cos(0), atan2(0, -1), exp(1), log(10)
	print (2 ^ 0.5 > 1.41), 10 % 3, -10 % 3, 5.5 % 2
}
