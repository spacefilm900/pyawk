"""Allow ``python -m pyawk``.

``python -m <package>`` runs the package's ``__main__.py`` file.  We simply
hand the command line to ``cli.main`` and use its return value as the process
exit status.
"""

import sys

from .cli import main

sys.exit(main())
