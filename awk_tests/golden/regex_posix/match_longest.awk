# match() reports the leftmost-longest match (POSIX), not the first
# alternative that matches: /a|ab/ on "xabc" is "ab" (RSTART 2, RLENGTH 2).
{
    n = split($0, f, " ")
    if (match(f[2], f[1])) print f[1], f[2], RSTART, RLENGTH, substr(f[2], RSTART, RLENGTH)
    else print f[1], f[2], RSTART, RLENGTH
}
