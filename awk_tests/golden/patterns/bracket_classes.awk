/^[[:upper:]][[:lower:]]+$/ { print "word:", $0 }
/[[:digit:]]+/ { print "digits:", $0 }
/[[:space:]]/ { print "space:", $0 }
/^[^a-z]*$/ { print "no lower:", $0 }
