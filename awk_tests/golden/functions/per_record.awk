# Functions in patterns and actions, with fields as arguments.
function big(x) { return x > 4 }
function field(n) { return $n }
function rate(pay, hours) { return hours > 0 ? pay * hours : "none" }
big($2) { print "big:", $1 }
{ print field(1), field(NF), rate($2, $3) }
END { print NR, field(1) }
