/^#/ { next }
NF == 0 { next }
{ print NR ": " $0 }
