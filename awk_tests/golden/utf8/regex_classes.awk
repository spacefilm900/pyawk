# Character classes follow Unicode (as gawk does in a UTF-8 locale).
{ a = $0; b = $0; c = $0; gsub(/[[:alpha:]]/, "a", a); gsub(/[[:upper:]]/, "U", b); gsub(/[[:punct:]]/, "p", c)
  print a; print b; print c }
