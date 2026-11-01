function fact(n) { return n <= 1 ? 1 : n * fact(n - 1) }
function fib(n) { return n < 2 ? n : fib(n - 1) + fib(n - 2) }
function ack(m, n) {
	if (m == 0) return n + 1
	if (n == 0) return ack(m - 1, 1)
	return ack(m - 1, ack(m, n - 1))
}
function depth(n) { return n == 0 ? 0 : 1 + depth(n - 1) }
function fresh(n,    a) { a[n] = 1; if (n > 1) fresh(n - 1); return length(a) }
function hanoi(n, from, to, via) {
	if (n == 0) return
	hanoi(n - 1, from, via, to); moves++; hanoi(n - 1, via, to, from)
}
BEGIN {
	print fact(10), fact(20), fib(20), ack(2, 3), ack(3, 3)
	print depth(3000), fresh(5)
	hanoi(10, "A", "C", "B"); print moves
}
