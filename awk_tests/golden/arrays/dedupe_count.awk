!seen[$1]++ { order[++n] = $1 }
END { for (i = 1; i <= n; i++) print order[i], seen[order[i]] }
