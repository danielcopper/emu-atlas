"""Narrowing a placement route's answer in a test whose fixture guarantees one.

Each placement question answers either with a placement or with a typed refusal
— the core this installation does not have, and for texture packs also the core
whose wiring atlas has not established. A test that deploys the core is
asserting a placement, and it says so here — once, in one place — instead of at
every attribute it then reads. The assert is the fixture's own guarantee made
checkable: when a fixture stops providing the core, the test fails on this line
with the refusal's message rather than on an attribute that is missing for a
reason nobody printed.

A test *about* the refusal does not use these. It asserts the outcome is
``Unresolved`` and reads its code, which is the whole point of that test.
"""

from __future__ import annotations

from atlas.placement import (
    ModPlacement,
    SavefilePlacement,
    SavestatePlacement,
    ScreenshotPlacement,
    TexturePlacement,
    Unresolved,
)


def placed(outcome: SavefilePlacement | Unresolved) -> SavefilePlacement:
    """The savefile placement this fixture guarantees — never the refusal."""
    assert isinstance(outcome, SavefilePlacement), f"expected a savefile placement, got {outcome}"
    return outcome


def state_placed(outcome: SavestatePlacement | Unresolved) -> SavestatePlacement:
    """The savestate placement this fixture guarantees — never the refusal."""
    assert isinstance(outcome, SavestatePlacement), f"expected a savestate placement, got {outcome}"
    return outcome


def screenshot_placed(outcome: ScreenshotPlacement | Unresolved) -> ScreenshotPlacement:
    """The screenshot placement this fixture guarantees — never the refusal."""
    assert isinstance(outcome, ScreenshotPlacement), f"expected a screenshot placement, got {outcome}"
    return outcome


def texture_placed(outcome: TexturePlacement | Unresolved) -> TexturePlacement:
    """The texture placement this fixture guarantees — never one of the refusals."""
    assert isinstance(outcome, TexturePlacement), f"expected a texture placement, got {outcome}"
    return outcome


def mod_placed(outcome: ModPlacement | Unresolved) -> ModPlacement:
    """The mod placement this fixture guarantees — never one of the refusals."""
    assert isinstance(outcome, ModPlacement), f"expected a mod placement, got {outcome}"
    return outcome
