# A var=value operand between files takes effect when the next file is
# reached, nextfile or not.
{ print "v=" v, FILENAME, $0; nextfile }
