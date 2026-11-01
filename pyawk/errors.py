"""Exception types used across the interpreter."""

from __future__ import annotations


class AwkError(Exception):
    """Base class for all pyawk errors (reported as ``pyawk: ...``, exit 2)."""


class AwkSyntaxError(AwkError):
    """Raised by the lexer or parser for malformed programs."""

    def __init__(self, message: str, line: int | None = None) -> None:
        self.message = message
        self.line = line
        where = f" at source line {line}" if line is not None else ""
        super().__init__(f"syntax error{where}: {message}")


class AwkRuntimeError(AwkError):
    """Raised for fatal errors while the program is running."""


class AwkUnsupportedError(AwkSyntaxError):
    """A construct that is valid AWK but deferred in spec v0.1."""

    def __init__(self, feature: str, line: int | None = None) -> None:
        super().__init__(f"{feature} is not supported yet (deferred in spec v0.1)", line)
