# Read the input twice (as two files): a range may continue from one input
# file into the next.
BEGIN { ARGV[2] = ARGV[1]; ARGC = 3 }
FNR == 4, FNR == 2 { print (NR > FNR ? "second" : "first"), FNR }
