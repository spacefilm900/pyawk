BEGIN { re = "^[DM]" }
$0 ~ re { print "re:", $1 }
$1 ~ "a" "r" { print "ar:", $1 }
