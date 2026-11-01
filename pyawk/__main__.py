"""Allow ``python -m pyawk``."""

import sys

from .cli import main

sys.exit(main())
