{ $0 = "x y z"; print NF, $2 }
END { $0 = "p q"; print $1, NF }
