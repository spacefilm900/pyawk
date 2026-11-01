{ print toupper($0), tolower($0), toupper(substr($1, 1, 1)) substr($1, 2) }
