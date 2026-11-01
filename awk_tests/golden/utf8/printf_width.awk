# Widths and precisions count characters; %c of a number is that code point.
{ printf "[%5s] [%-5s] [%.2s] [%c] [%3c]\n", $1, $1, $1, $1, $2 + 0 }
