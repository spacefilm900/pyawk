# Convenience wrappers. The real recipes live in scripts/.
# `make check` runs every local suite and prints a pass/fail summary.
# It does not modify any test files.
.PHONY: check

check:
	scripts/check.sh
