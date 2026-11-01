$3 > max { max = $3; line = $0 }
END { print max, line }
