# Every POSIX character class, alone and combined in one bracket.
BEGIN {
    n = split("alnum alpha blank cntrl digit graph lower print punct space upper xdigit", cls, " ")
    for (c = 0; c < 128; c++) {
        ch = sprintf("%c", c)
        line = sprintf("%3d", c)
        for (i = 1; i <= n; i++) line = line " " (ch ~ ("[[:" cls[i] ":]]") ? 1 : 0)
        line = line " " (ch ~ /[[:upper:][:digit:]_]/ ? 1 : 0) (ch ~ /[^[:alnum:][:space:]]/ ? 1 : 0)
        print line
    }
}
