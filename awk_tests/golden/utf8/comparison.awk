# Strings compare by their bytes: for valid UTF-8 that is code-point order;
# an invalid byte such as \200 comes before the bytes of \303\251 (é).
{ print ($1 < $2) ? $1 " < " $2 : $1 " >= " $2 }
