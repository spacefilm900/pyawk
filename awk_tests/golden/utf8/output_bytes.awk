# Output goes out as UTF-8, and printf %c of a big code point too.
BEGIN { printf "%c%c%c\n", 233, 8364, 128512; printf "%s\n", "日本" }
