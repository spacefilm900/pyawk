# sub and gsub replace the leftmost-longest match each time.
{
    s = $0; sub(/a|ab|abc/, "<&>", s)
    t = $0; n = gsub(/(a|ab)(c|bcd)?/, "<&>", t)
    u = $0; gsub(/x*/, "-", u)
    print s; print n, t; print u
}
