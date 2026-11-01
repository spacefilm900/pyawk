$1 ~ /^[A-K]/ { print "A-K:", $1 }
$1 !~ /y$/ { print "no-y:", $1 }
