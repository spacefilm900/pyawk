# A regex field separator, and split() with a regex, take the longest
# separator at each point: "a|ab" splits "1ab2a3" into 1, 2, 3.
BEGIN { FS = "a|ab" }
{
    print NF ":" $1 "," $2 "," $3
    n = split($0, p, /b|ba+/)
    out = n ":"
    for (i = 1; i <= n; i++) out = out p[i] (i < n ? "," : "")
    print out
}
