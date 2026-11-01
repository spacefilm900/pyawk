function g(a) { return a * 2 }
BEGIN { print g(1, x++), x }
