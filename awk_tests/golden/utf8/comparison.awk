# Strings compare by code point.
{ print ($1 < $2) ? $1 " < " $2 : $1 " >= " $2 }
