# Octal escapes that spell one UTF-8 character are that character.
{ print ($0 ~ /^\303\251+$/), gsub(/\342\202\254/, "EUR"), $0 }
