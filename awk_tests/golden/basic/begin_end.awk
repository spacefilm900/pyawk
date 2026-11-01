BEGIN { print "start" }
{ n++ }
END { print n, "lines"; print NR }
