# Changing ENVIRON does not change the environment of commands that
# awk runs: system(), "cmd" | getline and print | "cmd" all still see the
# original values (onetrue-awk, gawk --posix and mawk agree).
BEGIN {
	ENVIRON["PYAWK_T_NUM"] = "changed"
	ENVIRON["PYAWK_T_NEW"] = "new"
	delete ENVIRON["PYAWK_T_STR"]
	system("echo system: $PYAWK_T_NUM $PYAWK_T_STR [$PYAWK_T_NEW]")
	"echo getline: $PYAWK_T_NUM $PYAWK_T_STR [$PYAWK_T_NEW]" | getline line
	print line
	print "x" | "echo pipe: $PYAWK_T_NUM $PYAWK_T_STR [$PYAWK_T_NEW]; cat >/dev/null"
	close("echo pipe: $PYAWK_T_NUM $PYAWK_T_STR [$PYAWK_T_NEW]; cat >/dev/null")
	print "awk sees:", ENVIRON["PYAWK_T_NUM"], ENVIRON["PYAWK_T_NEW"]
}
