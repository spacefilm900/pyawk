"""Exception types used across the interpreter.

Role in the pipeline (cli -> lexer -> parser -> AST -> runtime): every stage
reports problems by *raising* one of these exceptions.  ``cli.main`` catches
the common base class ``AwkError``, prints ``pyawk: <message>`` and exits
with status 2, so no other module has to know how errors are displayed.

Hierarchy (a subclass "is a" kind of its parent, so catching the parent
catches it too)::

    AwkError
    +-- AwkSyntaxError          bad program text (lexer, parser)
    |   +-- AwkUnsupportedError valid AWK we have not implemented yet
    +-- AwkRuntimeError         problem while the program runs
"""

# Makes Python treat type hints as plain text rather than evaluating them,
# which lets us write modern hints such as ``int | None`` everywhere.
from __future__ import annotations


class AwkError(Exception):
    """Base class for all pyawk errors (reported as ``pyawk: ...``, exit 2)."""


class AwkSyntaxError(AwkError):
    """Raised by the lexer or parser for malformed programs."""

    # ``int | None`` means "an int, or None (no value)": the line number is
    # optional because some errors are not tied to a source line.
    def __init__(self, message: str, line: int | None = None) -> None:
        self.message = message
        self.line = line
        where = f" at source line {line}" if line is not None else ""
        super().__init__(f"syntax error{where}: {message}")


class AwkRuntimeError(AwkError):
    """Raised for fatal errors while the program is running."""


class AwkUnsupportedError(AwkSyntaxError):
    """A construct that is valid AWK but deferred in spec v0.1.

    It subclasses ``AwkSyntaxError`` so the program is rejected before any of
    it runs, but with a message that says "not supported yet" rather than
    suggesting the user's program is wrong.
    """

    def __init__(self, feature: str, line: int | None = None) -> None:
        super().__init__(f"{feature} is not supported yet (deferred in spec v0.1)", line)
