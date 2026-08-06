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
import re
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Mapping

from atlas.placement import TEMPLATE_ROM_STEM, TEMPLATE_SAVE_ID

# Packaged-data schema versions. The loaders are strict: unknown schema or
# malformed entries raise instead of coercing — a broken build must fail
# loudly, never resolve wrongly (REVIEW M3, M10).
ODDITIES_SCHEMA = 1
AUDIT_SCHEMA = 3

_KNOWN_VERDICTS = {"card", "standard", "standard-dir", "multi-option", "suspect", "unaudited"}
_KNOWN_MODE_ROOTS = {"savefile_directory", "system_directory", "content_directory"}
# A declared file name is a template in the placement's own hole grammar. Only
# these tokens exist: one the resolver fills, one the caller does. A token
# outside the set would travel into a stated filename and be read as literal
# text, so it fails the load instead.
_KNOWN_FILE_TEMPLATES = (TEMPLATE_ROM_STEM, TEMPLATE_SAVE_ID)
_TEMPLATE_TOKEN = re.compile(r"<[^<>]*>")


def _expect_str(value: object, where: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{where}: expected a non-empty string, got {value!r}")
    return value


def _expect_opt_str(value: object, where: str) -> str | None:
    if value is not None and not isinstance(value, str):
        raise ValueError(f"{where}: expected a string or null, got {value!r}")
    return value


def _expect_opt_bool(value: object, where: str) -> bool | None:
    if value is not None and not isinstance(value, bool):
        raise ValueError(f"{where}: expected a boolean or null, got {value!r}")
    return value


def _expect_str_list(value: object, where: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise ValueError(f"{where}: expected a list of strings, got {value!r}")
    return tuple(value)


def _expect_file_names(value: object, where: str) -> tuple[str, ...]:
    """A card's file names — templates only in the vocabulary the resolver knows."""
    names = _expect_str_list(value, where)
    for name in names:
        unknown = [t for t in _TEMPLATE_TOKEN.findall(name) if t not in _KNOWN_FILE_TEMPLATES]
        if unknown:
            raise ValueError(
                f"{where}: file name {name!r} carries unknown template {unknown[0]!r} — "
                f"only {list(_KNOWN_FILE_TEMPLATES)} are filled or carried as holes"
            )
    return names


@dataclass(frozen=True, slots=True)
class SaveMode:
    """One value of the governing option and the behaviour it selects.

    ``files`` is the declared file set for this mode, or ``None`` when the
    card marks it unverified — the resolver then refuses to state filenames.
    A name may be a template: ``<rom_stem>`` the resolver fills from the
    content path, ``<save_id>`` it carries through as a hole for the caller.
    ``observe`` optionally widens the *observation* candidates beyond the
    declared defaults (e.g. Flycast's slot-2 VMUs, which exist only when a
    controller port's slot 2 is configured as a VMU). ``complete`` asserts
    that the mode's candidate universe is closed — no other file can belong
    to the save; a card may claim it only with source-verified provenance.
    """

    root: str
    subdir: str | None
    files: tuple[str, ...] | None
    granularity: str
    observe: tuple[str, ...] | None = None
    complete: bool = False


@dataclass(frozen=True, slots=True)
class CoreCard:
    """A core's save rule card: identifiers, governing option, modes, provenance."""

    key: str
    so_names: tuple[str, ...]
    library_names: tuple[str, ...]
    option_key: str | None
    option_default: str | None
    modes: Mapping[str, SaveMode]
    provenance: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "modes", MappingProxyType(dict(self.modes)))

    def matches(self, *, so_basename: str | None, library_name: str | None) -> bool:
        if so_basename is not None and so_basename in self.so_names:
            return True
        return library_name is not None and library_name in self.library_names


def _save_mode(mode: Any, where: str) -> SaveMode:
    """One entry of a card's ``modes`` block — validated, never coerced."""
    root = _expect_str(mode.get("root"), f"{where}: root")
    if root not in _KNOWN_MODE_ROOTS:
        raise ValueError(f"{where}: root must be one of {sorted(_KNOWN_MODE_ROOTS)}, got {root!r}")
    files = mode.get("files")
    observe = mode.get("observe")
    complete = mode.get("complete", False)
    if not isinstance(complete, bool):
        # bool("false") is True in Python — never coerce this claim.
        raise ValueError(f"{where}: 'complete' must be a JSON boolean")
    return SaveMode(
        root=root,
        subdir=_expect_opt_str(mode.get("subdir"), f"{where}: subdir"),
        files=_expect_file_names(files, f"{where}: files") if files is not None else None,
        granularity=_expect_str(mode.get("granularity"), f"{where}: granularity"),
        observe=_expect_file_names(observe, f"{where}: observe") if observe is not None else None,
        complete=complete,
    )


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
    if not isinstance(raw, dict) or raw.get("schema") != ODDITIES_SCHEMA:
        raise ValueError(
            f"core_oddities: unsupported schema {raw.get('schema') if isinstance(raw, dict) else None!r} "
            f"(this atlas reads schema {ODDITIES_SCHEMA})"
        )
    cards: list[CoreCard] = []
    for key, entry in raw.get("cores", {}).items():
        where = f"card {key!r}"
        identifiers = entry.get("identifiers", {})
        saves = entry.get("saves", {})
        governing = saves.get("governing_option") or {}
        modes: dict[str, SaveMode] = {
            value: _save_mode(mode, f"{where} mode {value!r}")
            for value, mode in saves.get("modes", {}).items()
        }
        provenance = entry.get("provenance", {})
        cards.append(
            CoreCard(
                key=key,
                so_names=_expect_str_list(identifiers.get("so", []), f"{where}: identifiers.so"),
                library_names=_expect_str_list(
                    identifiers.get("library_name", []), f"{where}: identifiers.library_name"
                ),
                option_key=_expect_opt_str(governing.get("key"), f"{where}: governing_option.key"),
                option_default=_expect_opt_str(governing.get("default"), f"{where}: governing_option.default"),
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
    """One core's audit verdict, capability summary, and verification record.

    ``save_options`` names the core options the audit found governing the save
    file set — world knowledge with the same provenance as ``note``, not a live
    read. It belongs to the ``multi-option`` verdict and only to it: that
    verdict *means* "the granularity depends on several interacting options the
    card schema cannot express", so an entry that cannot name them has not
    earned it, and any other verdict naming them would be stating a dependency
    it just denied.
    """

    key: str
    verdict: str
    per_game_capable: bool | None
    note: str
    verified: Mapping[str, VerifiedOn | None]
    save_options: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "verified", MappingProxyType(dict(self.verified)))


def _verified_on(rec: Any, where: str) -> VerifiedOn | None:
    """One arrangement's verification record — ``None`` stays *never verified*."""
    if rec is None:
        return None
    return VerifiedOn(
        version=_expect_opt_str(rec.get("version"), f"{where}.version"),
        core_library_version=_expect_opt_str(rec.get("core_library_version"), f"{where}.core_library_version"),
        date=_expect_opt_str(rec.get("date"), f"{where}.date"),
    )


def _audit_entry(key: str, entry: Any) -> AuditEntry:
    """One core's audit entry — verdict, capability, and what it was verified on."""
    where = f"audit {key!r}"
    verdict = _expect_str(entry.get("verdict"), f"{where}: verdict")
    if verdict not in _KNOWN_VERDICTS:
        raise ValueError(f"{where}: verdict must be one of {sorted(_KNOWN_VERDICTS)}, got {verdict!r}")
    if "per_game_capable" not in entry:
        raise ValueError(f"{where}: missing required field 'per_game_capable'")
    per_game_capable = _expect_opt_bool(entry["per_game_capable"], f"{where}: per_game_capable")
    note = _expect_str(entry.get("note"), f"{where}: note")
    save_options = _expect_str_list(entry.get("save_options", []), f"{where}: save_options")
    if verdict == "multi-option" and not save_options:
        raise ValueError(
            f"{where}: a 'multi-option' verdict must list the governing options in 'save_options' — "
            "the verdict states the granularity depends on them"
        )
    if verdict != "multi-option" and save_options:
        raise ValueError(f"{where}: 'save_options' belongs to a 'multi-option' verdict, got {verdict!r}")
    verified: dict[str, VerifiedOn | None] = {
        arrangement: _verified_on(rec, f"{where}: verified[{arrangement!r}]")
        for arrangement, rec in entry.get("verified", {}).items()
    }
    return AuditEntry(
        key=key,
        verdict=verdict,
        per_game_capable=per_game_capable,
        note=note,
        verified=verified,
        save_options=save_options,
    )


def load_audit(text: str | None = None) -> dict[str, AuditEntry]:
    """Load the packaged verification matrix (``data/core_audit.json``)."""
    if text is None:
        text = importlib.resources.files("atlas").joinpath("data", "core_audit.json").read_text(encoding="utf-8")
    raw = json.loads(text)
    if not isinstance(raw, dict) or raw.get("schema") != AUDIT_SCHEMA:
        raise ValueError(
            f"core_audit: unsupported schema {raw.get('schema') if isinstance(raw, dict) else None!r} "
            f"(this atlas reads schema {AUDIT_SCHEMA})"
        )
    return {key: _audit_entry(key, entry) for key, entry in raw.get("cores", {}).items()}


_AUDIT: dict[str, AuditEntry] | None = None


def lookup_audit(key: str) -> AuditEntry | None:
    """Find the packaged audit entry for a card key."""
    global _AUDIT
    if _AUDIT is None:
        _AUDIT = load_audit()
    return _AUDIT.get(key)
