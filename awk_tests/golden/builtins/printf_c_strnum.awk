# %c of a numeric string (a field, a split element, getline input) uses
# its number, as POSIX says; a plain string gives its first character.
{
    n = split($0, parts, " ")
    printf "[%c][%3c][%-3c][%c]", $1, $1, parts[2], $3
    s = sprintf("%c", parts[n])
    printf "[%s][%c][%c]\n", s, $1 "", u
}
