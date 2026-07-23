"""Rule cards for cores whose save behaviour deviates from the standard rule.

The cards live in ``data/core_oddities.json`` — world knowledge under the
boundary rule: a card states *which* live config governs a core and what its
values mean; the current value is always read from the machine, never from the
card. Cards are keyed by the core's canonical short name (the ``.so`` basename
without ``_libretro.so``); the ``identifiers`` block carries every matching
name, including the display ``library_name`` the binary reports, so lookup
works from either side.

Facts in data, interpretation in code: this module only loads and indexes; the
resolver in :mod:`atlas.installations` applies the card.
"""

from __future__ import annotations

import importlib.resources
import json
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class SaveMode:
    """One value of the governing option and the behaviour it selects.

    ``files`` is the declared file set for this mode, or ``None`` when the
    card marks it unverified — the resolver then refuses to state filenames.
    """

    root: str
    subdir: str | None
    files: tuple[str, ...] | None
    granularity: str


@dataclass(frozen=True, slots=True)
class CoreCard:
    """A core's save rule card: identifiers, governing option, modes, provenance."""

    key: str
    so_names: tuple[str, ...]
    library_names: tuple[str, ...]
    option_key: str | None
    option_default: str | None
    modes: dict[str, SaveMode]
    provenance: str

    def matches(self, *, so_basename: str | None, library_name: str | None) -> bool:
        if so_basename is not None and so_basename in self.so_names:
            return True
        return library_name is not None and library_name in self.library_names


def load_oddities(text: str | None = None) -> tuple[CoreCard, ...]:
    """Load the packaged rule cards (or *text* when supplied, for tests).

    Reading packaged data is not the machine seam — it is the library reading
    its own bundled world knowledge, which is exactly what the cards are.
    """
    if text is None:
        text = (
            importlib.resources.files("atlas").joinpath("data", "core_oddities.json").read_text(encoding="utf-8")
        )
    raw = json.loads(text)
    cards: list[CoreCard] = []
    for key, entry in raw.get("cores", {}).items():
        identifiers = entry.get("identifiers", {})
        saves = entry.get("saves", {})
        governing = saves.get("governing_option") or {}
        modes: dict[str, SaveMode] = {}
        for value, mode in saves.get("modes", {}).items():
            files = mode.get("files")
            modes[value] = SaveMode(
                root=mode["root"],
                subdir=mode.get("subdir"),
                files=tuple(files) if files is not None else None,
                granularity=mode["granularity"],
            )
        provenance = entry.get("provenance", {})
        cards.append(
            CoreCard(
                key=key,
                so_names=tuple(identifiers.get("so", ())),
                library_names=tuple(identifiers.get("library_name", ())),
                option_key=governing.get("key"),
                option_default=governing.get("default"),
                modes=modes,
                provenance=provenance.get("source", "unstated"),
            )
        )
    return tuple(cards)


_PACKAGED: tuple[CoreCard, ...] | None = None


def lookup_card(*, so_basename: str | None, library_name: str | None) -> CoreCard | None:
    """Find the packaged rule card matching a core, by ``.so`` name or ``library_name``."""
    global _PACKAGED
    if _PACKAGED is None:
        _PACKAGED = load_oddities()
    for card in _PACKAGED:
        if card.matches(so_basename=so_basename, library_name=library_name):
            return card
    return None


@dataclass(frozen=True, slots=True)
class VerifiedOn:
    """What one arrangement's verification pinned: arrangement + core versions."""

    version: str | None
    core_library_version: str | None
    date: str | None


@dataclass(frozen=True, slots=True)
class AuditEntry:
    """One core's audit verdict and per-arrangement verification record."""

    key: str
    verdict: str
    verified: dict[str, VerifiedOn | None]


def load_audit(text: str | None = None) -> dict[str, AuditEntry]:
    """Load the packaged verification matrix (``data/core_audit.json``)."""
    if text is None:
        text = importlib.resources.files("atlas").joinpath("data", "core_audit.json").read_text(encoding="utf-8")
    raw = json.loads(text)
    entries: dict[str, AuditEntry] = {}
    for key, entry in raw.get("cores", {}).items():
        verified: dict[str, VerifiedOn | None] = {}
        for arrangement, rec in entry.get("verified", {}).items():
            verified[arrangement] = (
                VerifiedOn(
                    version=rec.get("version"),
                    core_library_version=rec.get("core_library_version"),
                    date=rec.get("date"),
                )
                if rec is not None
                else None
            )
        entries[key] = AuditEntry(key=key, verdict=entry.get("verdict", "unaudited"), verified=verified)
    return entries


_AUDIT: dict[str, AuditEntry] | None = None


def lookup_audit(key: str) -> AuditEntry | None:
    """Find the packaged audit entry for a card key."""
    global _AUDIT
    if _AUDIT is None:
        _AUDIT = load_audit()
    return _AUDIT.get(key)
