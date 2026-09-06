r"""The audit page's verdict column, read back against the audit data.

``atlas/data/core_audit.json`` is the authority for a core's verdict, and one
test already refuses a rule card that has no audit entry. Nothing tied the
prose table in ``docs/research/core-audit.md`` to that data, so the page
drifted: seven rows named a verdict the data had moved past — two
``standard-dir`` entries that are cards, and five ``multi-option`` ones that
were carded in #175 and #176. A wrong verdict on the page is a wrong answer to
whoever reads it, and no suite was looking.

The page is parsed the way it renders: the first table only, its cells split on
**unescaped** pipes (a ``\|`` is text inside a cell, not a column boundary),
and the verdict taken from the bold cell the legend above the table defines.
"""

from __future__ import annotations

import re
from pathlib import Path

from atlas.oddities import load_audit

PAGE = Path(__file__).resolve().parent.parent / "docs" / "research" / "core-audit.md"

# A column boundary is a pipe the author did not escape.
CELL_BOUNDARY = re.compile(r"(?<!\\)\|")
# The verdict cell is bold — an unbolded value would render as a different
# claim than every other row makes.
VERDICT = re.compile(r"^\*\*[a-z-]+\*\*$")
# The one row of the table that names no core: it stands in for every core
# still unaudited, and its count moves with the shipped set, so it is matched
# by its opening word rather than by a number that will go stale.
PLACEHOLDER_PREFIX = "_remaining"


def _first_table(text: str) -> list[str]:
    """The first table's rows, its header and separator dropped."""
    rows: list[str] = []
    for line in text.splitlines():
        if line.startswith("|"):
            rows.append(line)
        elif rows:
            break
    return rows[2:]


def _cells(row: str) -> list[str]:
    """One table row's cells, the empties outside the leading and trailing pipe dropped."""
    return [cell.strip() for cell in CELL_BOUNDARY.split(row)[1:-1]]


def _rows() -> list[tuple[str, str]]:
    """Every (core short name, verdict cell) the table states, placeholder dropped."""
    table = _first_table(PAGE.read_text(encoding="utf-8"))
    return [
        (cells[0], cells[2])
        for cells in (_cells(row) for row in table)
        if not cells[0].startswith(PLACEHOLDER_PREFIX)
    ]


def test_the_page_still_has_a_verdict_table():
    # Without this, a parser that stopped finding the table would let every
    # assertion below pass on an empty list.
    assert len(_rows()) > 10, f"{PAGE.name}: found {len(_rows())} rows where the parser looks for the verdict table"


def test_every_verdict_cell_is_bold():
    unbolded = sorted(core for core, cell in _rows() if not VERDICT.match(cell))
    assert unbolded == []


def test_every_row_names_a_core_the_audit_data_knows():
    audit = load_audit()
    unknown = sorted(core for core, _ in _rows() if core not in audit)
    assert unknown == []


def test_every_row_states_the_verdict_the_audit_data_holds():
    audit = load_audit()
    stale = {
        core: (cell.strip("*"), audit[core].verdict)
        for core, cell in _rows()
        if core in audit and cell.strip("*") != audit[core].verdict
    }
    assert stale == {}


def test_exactly_one_row_is_the_unaudited_placeholder():
    table = _first_table(PAGE.read_text(encoding="utf-8"))
    placeholders = [_cells(row)[0] for row in table if _cells(row)[0].startswith(PLACEHOLDER_PREFIX)]
    assert len(placeholders) == 1, (
        f"exactly one row stands in for the unaudited rest; found {placeholders}"
    )
