# RSTART and RLENGTH are in characters.
{ if (match($0, /[éè]+/)) print RSTART, RLENGTH, substr($0, RSTART, RLENGTH); else print "no" }
