# Characters, not bytes: length, substr and index count code points.
{ print length($0), substr($0, 2, 3), index($0, "ö"), length($2) }
