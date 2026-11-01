# The comma binds loosest: the whole || is the start condition.  A newline
# may follow the comma, and ; separates pattern-only rules.
$1 == "a" || $1 == "z", /x/ { print "or", NR }
x = /start/, /end/ { print "assign", NR, x }
$1 == "y" ? 1 : 0,
/z/ { print "cond", NR }
/w/, /w/; /a/
