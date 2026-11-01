# "echo " "hi" | getline runs the command "echo hi": the concatenation
# is the command (onetrue-awk and gawk; mawk runs "hi" and concatenates).
BEGIN {
	"echo " "hi" | getline
	print
	close("echo hi")
	"echo " "hi" | getline line
	print line
}
