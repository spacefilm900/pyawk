# substr keeps an invalid byte as it is (gawk turns it into U+FFFD).
{ print substr($0, 1, 2), toupper($0) }
