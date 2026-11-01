# A function may be used before its definition; a newline may come
# before the body and after commas; ";" may follow a definition.
BEGIN {
	print max(3, 7), max("abc", "abd"), add(1,
	    2) add(3, 4)
	print twice(twice(5)), max(add(1, 1), 1) * 10
}
function max(m, n) { return m > n ? m : n }
function add(a,
             b)
{
	return a + b
};
function twice(x) { return 2 * x }
