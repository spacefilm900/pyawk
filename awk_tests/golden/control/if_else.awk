{
	if ($1 < 0) print $1, "negative"
	else if ($1 == 0) print $1, "zero"
	else {
		print $1, "positive"
	}
}
