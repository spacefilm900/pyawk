# nextfile leaves any loops and blocks it is inside, like next.
{
	for (i = 1; i <= 3; i++) {
		while (1) {
			if (FNR == 2) {
				print "leaving", FILENAME, "at", $0
				nextfile
			}
			print FILENAME, $0
			break
		}
		break
	}
}
