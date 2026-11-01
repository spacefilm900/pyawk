"""Checks on the Markdown documentation (CodeRabbit v1.0, finding 8)."""

import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent


def _cells(row: str) -> int:
    """Number of cells in a Markdown table row.  A ``|`` written as ``\\|``
    (as in ``\\| cmd``) is part of a cell's text, not a column border."""
    borders = re.findall(r"(?<!\\)\|", row.strip())
    return len(borders) - 1


@pytest.mark.parametrize("doc", ["DESIGN.md", "README.md"])
def test_table_rows_have_as_many_cells_as_their_header(doc: str) -> None:
    lines = (REPO / doc).read_text().splitlines()
    in_code = False
    header_cells = None
    bad = []
    for number, line in enumerate(lines, 1):
        if line.startswith("```"):
            in_code = not in_code  # skip code blocks
            continue
        if in_code or not line.startswith("|"):
            header_cells = None  # a non-table line ends the table
            continue
        if header_cells is None:
            header_cells = _cells(line)  # the first row is the header
        elif _cells(line) != header_cells:
            bad.append(f"{doc}:{number}: {line}")
    assert bad == []
