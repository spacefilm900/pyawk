# Strings built from escapes join into characters when concatenated.
BEGIN { a = "\303"; b = "\251"; s = a b; print length(s), s, (s == "é") }
