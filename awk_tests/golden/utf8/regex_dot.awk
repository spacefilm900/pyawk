# "." matches one whole character.
{ print ($0 ~ /^.$/), ($0 ~ /^..$/), gsub(/./, "<&>"), $0 }
