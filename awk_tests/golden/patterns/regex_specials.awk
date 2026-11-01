/a\.b/ { print "dot:", $0 }
/^(ab)+$/ { print "group:", $0 }
/colou?r/ { print "opt:", $0 }
/x+y*z/ { print "rep:", $0 }
/cat|dog/ { print "alt:", $0 }
/[/]/ { print "slash:", $0 }
