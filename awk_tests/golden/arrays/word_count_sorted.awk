{ for (i = 1; i <= NF; i++) count[tolower($i)]++ }
END { for (w in count) print w, count[w] | "sort"; close("sort") }
