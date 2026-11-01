# s counts tests of the start condition, e tests of the end condition:
# start is tested only while the range is closed, end only while it is open
# (including on the record that opened it).
(s++ >= 0 && $1 % 3 == 1), (e++ >= 0 && $1 % 3 == 0) { print $1 }
END { print "start tested", s, "end tested", e }
