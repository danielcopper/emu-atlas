"""Standalone firmware cards — what a standalone emulator expects beside its content.

A standalone emulator ships no ``.info`` for the firmware route to read, so
which file it probes and where is established from its source at the shipped
release and packaged here — the same split the standalone save cards make
(:mod:`atlas.standalone_saves`): the card states what *can* be, with
citations, and the resolver reads what *is* on this machine. A card names the
XDG base and the emulator-relative path the emulator itself probes — never
the staging spot an arrangement's installer uses, because the emulator's
probe is the only door that matters at run time (Cemu reads its keys at
``GetUserDataPath("keys.txt")`` no matter where an installer parked them).

Cards are keyed by the ``%EMULATOR_…%`` token, like the save cards beside
them, and reach the firmware answer through the catalogue entry's resolved
token — which on EmuDeck is the launcher route's word, variant-gated there.
"""

from __future__ import annotations

import importlib.resources
import json
from dataclasses import dataclass
from typing import Any

FIRMWARE_CARDS_SCHEMA = 1

_FILE_BASES = ("config", "data")
_FILE_NEEDS = ("required", "optional")


@dataclass(frozen=True, slots=True)
class StandaloneFirmwareFile:
    """One file a standalone emulator probes: the XDG base, the path below it, the claim."""

    name: str
    base: str
    subdir: str
    need: str
    purpose: str
    citation: str


@dataclass(frozen=True, slots=True)
class StandaloneFirmwareCard:
    """One standalone emulator's firmware expectations, with the systems they answer for."""

    token: str
    systems: tuple[str, ...]
    files: tuple[StandaloneFirmwareFile, ...]
    provenance: str


def _expect_str(value: Any, where: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{where}: expected a non-empty string, got {value!r}")
    return value


def _file(token: str, index: int, entry: Any) -> StandaloneFirmwareFile:
    where = f"standalone firmware card {token!r}: files[{index}]"
    if not isinstance(entry, dict) or set(entry) != {
        "name",
        "base",
        "subdir",
        "need",
        "purpose",
        "citation",
    }:
        raise ValueError(
            f"{where}: expected exactly name/base/subdir/need/purpose/citation, got {entry!r}"
        )
    base = _expect_str(entry["base"], f"{where}.base")
    if base not in _FILE_BASES:
        raise ValueError(f"{where}.base must be one of {_FILE_BASES}, got {base!r}")
    need = _expect_str(entry["need"], f"{where}.need")
    if need not in _FILE_NEEDS:
        raise ValueError(f"{where}.need must be one of {_FILE_NEEDS}, got {need!r}")
    return StandaloneFirmwareFile(
        name=_expect_str(entry["name"], f"{where}.name"),
        base=base,
        subdir=_expect_str(entry["subdir"], f"{where}.subdir"),
        need=need,
        purpose=_expect_str(entry["purpose"], f"{where}.purpose"),
        citation=_expect_str(entry["citation"], f"{where}.citation"),
    )


def _card(token: str, entry: Any) -> StandaloneFirmwareCard:
    where = f"standalone firmware card {token!r}"
    if not isinstance(entry, dict):
        raise ValueError(f"{where}: expected an object, got {entry!r}")
    systems = entry.get("systems")
    if not isinstance(systems, list) or not systems:
        raise ValueError(f"{where}: systems must be a non-empty list, got {systems!r}")
    files = entry.get("files")
    if not isinstance(files, list) or not files:
        raise ValueError(f"{where}: files must be a non-empty list, got {files!r}")
    provenance = entry.get("provenance", {})
    if not isinstance(provenance, dict):
        raise ValueError(f"{where}: expected a 'provenance' object, got {provenance!r}")
    return StandaloneFirmwareCard(
        token=token,
        systems=tuple(_expect_str(s, f"{where}: systems[]") for s in systems),
        files=tuple(_file(token, i, f) for i, f in enumerate(files)),
        provenance=_expect_str(provenance.get("source"), f"{where}: provenance.source"),
    )


def load_standalone_firmware(text: str | None = None) -> tuple[StandaloneFirmwareCard, ...]:
    """Load the packaged standalone firmware cards (or *text* when supplied, for tests)."""
    if text is None:
        text = (
            importlib.resources.files("atlas")
            .joinpath("data", "standalone_firmware.json")
            .read_text(encoding="utf-8")
        )
    raw = json.loads(text)
    if not isinstance(raw, dict) or raw.get("schema") != FIRMWARE_CARDS_SCHEMA:
        raise ValueError(
            f"standalone_firmware: unsupported schema "
            f"{raw.get('schema') if isinstance(raw, dict) else None!r} "
            f"(this atlas reads schema {FIRMWARE_CARDS_SCHEMA})"
        )
    return tuple(_card(token, entry) for token, entry in raw.get("emulators", {}).items())


_PACKAGED: tuple[StandaloneFirmwareCard, ...] | None = None


def lookup_standalone_firmware_card(token: str | None) -> StandaloneFirmwareCard | None:
    """The packaged card for one emulator token, or ``None`` — no fuzzy matching."""
    global _PACKAGED
    if _PACKAGED is None:
        _PACKAGED = load_standalone_firmware()
    if token is None:
        return None
    return next((card for card in _PACKAGED if card.token == token), None)
