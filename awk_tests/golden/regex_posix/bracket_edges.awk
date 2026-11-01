# Bracket expressions: ] first is literal, - first or last is literal,
# a backslash escapes inside brackets (awk), negation with ^.
{
    printf "%s:", $0
    printf " %d", $0 ~ /^[]a]+$/
    printf " %d", $0 ~ /^[^]a]+$/
    printf " %d", $0 ~ /^[a-]+$/
    printf " %d", $0 ~ /^[-a]+$/
    printf " %d", $0 ~ /^[\]\\]+$/
    printf " %d", $0 ~ /^[%--]+$/
    printf " %d", $0 ~ /^[[]+$/
    print ""
}
