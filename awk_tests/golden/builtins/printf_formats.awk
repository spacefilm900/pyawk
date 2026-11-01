BEGIN {
	printf "%d|%5d|%-5d|%05d|%+d\n", 42.9, 42, 42, 42, 42
	printf "%.2f|%8.3f|%-8.1f|%e|%E\n", 3.14159, 3.14159, 3.14159, 12345.678, 0.000123
	printf "%g|%g|%g|%G\n", 100000, 1000000, 0.0001, 1e-10
	printf "%x|%X|%o|%c|%c|%c\n", 255, 255, 8, 65, "hello", 256 - 190
	printf "%s|%10s|%-10s|%.3s|%%\n", "str", "right", "left", "truncate"
	printf "%i|%*d|%-*d|%.*f\n", 7, 4, 1, 4, 2, 2, 2.71828
	printf "no newline"; printf "\n"
	x = sprintf("%03d-%s", 7, "x"); print x, length(x)
}
