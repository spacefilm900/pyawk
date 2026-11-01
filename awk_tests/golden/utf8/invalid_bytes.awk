# An invalid byte counts as one character and is copied unchanged.
{ print length($0), index($0, "b"), ($0 ~ /^.b/) , ($0 ~ /b/) }
{ n = split($0, f, "b"); print n; printf "[%3s]\n", $0 }
