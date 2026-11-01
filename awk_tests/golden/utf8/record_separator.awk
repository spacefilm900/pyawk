# A multi-character RS is a regex over characters.
BEGIN { RS = "[éè]+" }
{ print NR ": " $0 }
