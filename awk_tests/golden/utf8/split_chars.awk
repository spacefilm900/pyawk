# split with an empty separator gives one element per character;
# a multibyte separator splits only at whole characters.
{ n = split($0, a, ""); printf "%d:", n; for (i = 1; i <= n; i++) printf " %s", a[i]; print ""
  n = split($0, b, "é"); print n, b[1], b[n] }
