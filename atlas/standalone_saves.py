"""Standalone save cards — which emulators atlas can answer the save question for.

A standalone emulator is handed nothing by a frontend: its save tree is its
own, shaped by its own configuration and its own compiled-in defaults. The
card here is the thin, versioned half of that knowledge — which emulator,
which configuration file governs it, which catalogue systems it answers for,
and the citations behind both — while the reading itself is code beside it in
:mod:`atlas.installations`, exactly the split the libretro rule cards make
(:mod:`atlas.mode_rules`): a card states what *can* be, the code reads what
*is* on this machine, and neither guesses.

The cards are keyed by the ``%EMULATOR_…%`` token the ES-DE catalogue names in
a launch command, because for a standalone entry that token is the only
identifier there is — the same key the standalone texture cards use
(:mod:`atlas.textures`). A card whose token has no resolver function
registered is a marker selecting nothing, and fails the load the way a rule
card without a rule does.
"""

from __future__ import annotations

import importlib.resources
import json
from dataclasses import dataclass
from typing import Any

SAVES_SCHEMA = 1

# The XDG bases a card's configuration file may hang off — the same two words
# the texture cards use, because they are the same fact: one flatpak pins both.
_CONFIG_BASES = ("config", "data")


@dataclass(frozen=True, slots=True)
class StandaloneSaveCard:
    """One standalone emulator's save knowledge: config file, systems, citations.

    ``config_base`` and ``config_path`` name the configuration file the
    resolver reads the way the emulator does — below the XDG base the
    arrangement pins. They are ``None`` for an emulator whose save tree is
    fixed by the build rather than by any file (PPSSPP's Linux memstick is a
    compiled-in XDG join): naming a file the resolver never reads would state
    a governing config that does not govern. ``systems`` is the closed list of
    catalogue systems this card answers for: an emulator can serve several
    with different trees (Dolphin keeps GameCube cards and a Wii NAND), and a
    system outside the list is a question the card does not answer, stated
    rather than stretched.
    """

    token: str
    config_base: str | None
    config_path: str | None
    systems: tuple[str, ...]
    provenance: str


def _expect_str(value: Any, where: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{where}: expected a non-empty string, got {value!r}")
    return value


def _card(token: str, entry: Any) -> StandaloneSaveCard:
    """One emulator's card — validated, never coerced."""
    where = f"standalone save card {token!r}"
    if not isinstance(entry, dict):
        raise ValueError(f"{where}: expected an object, got {entry!r}")
    saves = entry.get("saves")
    if not isinstance(saves, dict):
        raise ValueError(f"{where}: expected a 'saves' object, got {saves!r}")
    config = saves.get("config")
    if config is not None:
        if not isinstance(config, dict) or set(config) != {"base", "path"}:
            raise ValueError(
                f"{where}: saves.config must name exactly 'base' and 'path', got {config!r}"
            )
        base = _expect_str(config["base"], f"{where}: saves.config.base")
        if base not in _CONFIG_BASES:
            raise ValueError(
                f"{where}: saves.config.base must be one of {_CONFIG_BASES}, got {base!r}"
            )
    systems = saves.get("systems")
    if not isinstance(systems, list) or not systems:
        raise ValueError(f"{where}: saves.systems must be a non-empty list, got {systems!r}")
    provenance = entry.get("provenance", {})
    if not isinstance(provenance, dict):
        raise ValueError(f"{where}: expected a 'provenance' object, got {provenance!r}")
    return StandaloneSaveCard(
        token=token,
        config_base=config["base"] if config is not None else None,
        config_path=(
            _expect_str(config["path"], f"{where}: saves.config.path")
            if config is not None
            else None
        ),
        systems=tuple(_expect_str(s, f"{where}: saves.systems[]") for s in systems),
        provenance=_expect_str(provenance.get("source"), f"{where}: provenance.source"),
    )


def load_standalone_saves(text: str | None = None) -> tuple[StandaloneSaveCard, ...]:
    """Load the packaged standalone save cards (or *text* when supplied, for tests)."""
    if text is None:
        text = (
            importlib.resources.files("atlas")
            .joinpath("data", "standalone_saves.json")
            .read_text(encoding="utf-8")
        )
    raw = json.loads(text)
    if not isinstance(raw, dict) or raw.get("schema") != SAVES_SCHEMA:
        raise ValueError(
            f"standalone_saves: unsupported schema "
            f"{raw.get('schema') if isinstance(raw, dict) else None!r} "
            f"(this atlas reads schema {SAVES_SCHEMA})"
        )
    return tuple(_card(token, entry) for token, entry in raw.get("emulators", {}).items())


_PACKAGED: tuple[StandaloneSaveCard, ...] | None = None


def lookup_standalone_save_card(token: str | None) -> StandaloneSaveCard | None:
    """The packaged card for one emulator token, or ``None`` — no fuzzy matching."""
    global _PACKAGED
    if _PACKAGED is None:
        _PACKAGED = load_standalone_saves()
    if token is None:
        return None
    return next((card for card in _PACKAGED if card.token == token), None)
