"""pyawk: a POSIX-compatible AWK interpreter written in Python.

The interpreter is a classic pipeline; each stage lives in its own module:

    cli.py      reads the command line and the program text
    lexer.py    splits program text into tokens (words, numbers, operators)
    parser.py   arranges tokens into a tree of nodes defined in ast.py
    runtime.py  walks that tree, reading input records and producing output

Supporting modules: values.py (AWK's string/number values), regex.py (AWK
regular expressions translated to Python's), builtins.py (length, substr,
printf, ...), errors.py (the exceptions every stage raises).

A file named ``__init__.py`` marks a directory as a Python *package*; its
contents run when the package is first imported.
"""

__version__ = "1.2.0"
