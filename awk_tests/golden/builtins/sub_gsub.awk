{ s = $0; n = gsub(/o/, "0", s); print n, s }
{ t = $0; sub(/[a-z]+/, "<&>", t); print t }
{ u = $0; gsub(/l/, "\\&", u); print u }
{ n = gsub(/o/, "O"); print n, $0, $1 }
