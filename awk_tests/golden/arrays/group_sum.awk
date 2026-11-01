{ total[$3 > 0 ? "worked" : "idle"] += $2; cnt[$3 > 0 ? "worked" : "idle"]++ }
END { print "idle", total["idle"], cnt["idle"]; print "worked", total["worked"], cnt["worked"] }
