{ s = $0; sub(/o/, "[\&]", s); t = $0; gsub(/o/, "\\&", t); u = $0; sub(/o/, v, u)
  print s, t, u, "\q", length("\q"), ($0 ~ "f\.o"), ($0 ~ "f\\.o") }
