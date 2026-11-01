# A one-character FS is that character; a longer FS is a regex.
BEGIN { FS = "é" }
{ print NF, $2; n = split($0, a, /[éè]/); print n, a[n] }
