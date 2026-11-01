{ if (match($0, /o+/)) print RSTART, RLENGTH, substr($0, RSTART, RLENGTH); else print RSTART, RLENGTH }
