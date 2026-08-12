"""Narrowing a save route's answer in a test whose fixture guarantees a placement.

Both save questions answer either with a placement or with the typed refusal a
core this installation does not have earns. A test that deploys the core is
asserting a placement, and it says so here — once, in one place — instead of at
every attribute it then reads. The assert is the fixture's own guarantee made
checkable: when a fixture stops providing the core, the test fails on this line
with the refusal's message rather than on an attribute that is missing for a
reason nobody printed.

A test *about* the refusal does not use these. It asserts the outcome is
``Unresolved`` and reads its code, which is the whole point of that test.
"""

from __future__ import annotations

from atlas.placement import SavefilePlacement, SavestatePlacement, Unresolved


def placed(outcome: SavefilePlacement | Unresolved) -> SavefilePlacement:
    """The savefile placement this fixture guarantees — never the refusal."""
    assert isinstance(outcome, SavefilePlacement), f"expected a savefile placement, got {outcome}"
    return outcome


def state_placed(outcome: SavestatePlacement | Unresolved) -> SavestatePlacement:
    """The savestate placement this fixture guarantees — never the refusal."""
    assert isinstance(outcome, SavestatePlacement), f"expected a savestate placement, got {outcome}"
    return outcome
