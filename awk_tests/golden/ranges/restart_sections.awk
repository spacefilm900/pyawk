# Print each [section] of a config-like file, numbering the sections.
/^\[/, /^$/ { if (/^\[/) n++; if (NF) print n ": " $0 }
