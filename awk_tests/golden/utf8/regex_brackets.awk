# Bracket expressions, ranges and negation hold characters.
{ x = $0; y = $0; z = $0; gsub(/[éè]/, "E", x); gsub(/[à-ÿ]/, "L", y); gsub(/[^a-z ]/, "_", z); print x; print y; print z }
