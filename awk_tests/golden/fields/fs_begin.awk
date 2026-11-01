BEGIN { FS = ":" }
{ print $5 "|" $6 }
