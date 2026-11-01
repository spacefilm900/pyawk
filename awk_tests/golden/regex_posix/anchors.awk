# ^ and $ are anchors anywhere in an ERE, also inside groups and
# alternatives; in the middle of a pattern they simply cannot match.
{
    printf "%s:", $0
    printf " %d", $0 ~ /(^a|b)c/
    printf " %d", $0 ~ /a(c$|b)/
    printf " %d", $0 ~ /^a|c$/
    printf " %d", $0 ~ /x^y/
    printf " %d", $0 ~ /x$y/
    n = gsub(/^a|b/, "#")
    print " " n " " $0
}
