"""pytest loads this file automatically before the tests in this directory.

It makes two directories importable without installing anything: the
repository root (for the ``pyawk`` package) and awk_tests/ (for the shared
golden-test ``harness`` module).
"""

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "awk_tests"))
