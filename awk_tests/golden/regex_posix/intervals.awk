# Interval expressions {n}, {n,}, {n,m}; a{2}+ repeats the group "aa";
# "{" not followed by a digit is an ordinary character.
{
    printf "%s:", $0
    printf " %d", $0 ~ /^a{2}$/
    printf " %d", $0 ~ /^a{2,}$/
    printf " %d", $0 ~ /^a{1,3}$/
    printf " %d", $0 ~ /^(ab){2}$/
    printf " %d", $0 ~ /^a{2}+$/
    printf " %d", $0 ~ /^a{0}b$/
    printf " %d", $0 ~ /a{x/
    if (match($0, /a{1,2}/)) printf " %d/%d", RSTART, RLENGTH
    print ""
}
