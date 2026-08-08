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
from types import MappingProxyType
from typing import Any, Mapping

from atlas.placement import GRANULARITIES, ROOT_KINDS, TEMPLATE_ROM_STEM, TEMPLATE_SAVE_ID

# Packaged-data schema versions. The loaders are strict: unknown schema or
# malformed entries raise instead of coercing — a broken build must fail
# loudly, never resolve wrongly (REVIEW M3, M10).
ODDITIES_SCHEMA = 1
AUDIT_SCHEMA = 3

_KNOWN_VERDICTS = {"card", "standard", "standard-dir", "multi-option", "suspect", "unaudited"}
# The mode a card without a governing option selects. Named here because the
# loader validates against the same spelling the resolver looks up — two
# literals would let a card pass the load and select nothing.
MODE_ALWAYS = "always"
# The roots a mode may anchor at and the granularities it may select are the
# placement's own vocabularies — imported, not respelled here, for the same
# reason the file-name templates are: a card is data, and a value that only
# looks right would be stated as fact.
_KNOWN_MODE_ROOTS = set(ROOT_KINDS)
_KNOWN_GRANULARITIES = set(GRANULARITIES)
# A declared file name is a template in the placement's own hole grammar. Only
# these tokens exist: one the resolver fills, one the caller does. A token
# outside the set would travel into a stated filename and be read as literal
# text, so it fails the load instead.
_KNOWN_FILE_TEMPLATES = (TEMPLATE_ROM_STEM, TEMPLATE_SAVE_ID)


# This check exists verbatim three times, one per packaged-data loader
# (:func:`atlas.evidence._expect_str`, :func:`atlas.systems._expect_str`). The
# triplication is the deliberate cost of loader independence: each loader reads
# one file and depends on nothing else in atlas, so a defect in one table can
# never fail the load of another — and a fidelity finding about what counts as a
# string belongs in all three.
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
    """A card's file names — templates only in the vocabulary the resolver knows.

    The check is subtractive, not a token scan: the known templates are removed
    and *any* remaining angle bracket fails the load. A scan for well-formed
    ``<…>`` would pass a name whose bracket never closes (``<rom_stem.A1.bin``)
    or nests (``<<rom_stem>>``), and such a name is stated verbatim — the very
    "a typo cannot become a stated filename" guarantee this exists for. A real
    file name with a literal angle bracket would be refused too; none is known,
    and refusing one is the safe direction.
    """
    names = _expect_str_list(value, where)
    if not names:
        raise ValueError(
            f"{where}: an empty list declares nothing — omit the field (or use null) to state that "
            "the file set is not established, which is a different answer than a set with no files"
        )
    for name in names:
        if not name:
            raise ValueError(f"{where}: an empty string is not a file name")
        remainder = name
        for token in _KNOWN_FILE_TEMPLATES:
            remainder = remainder.replace(token, "")
        if "<" in remainder or ">" in remainder:
            raise ValueError(
                f"{where}: file name {name!r} carries an unknown template — only "
                f"{list(_KNOWN_FILE_TEMPLATES)} are filled or carried as holes, and everything else "
                "is stated verbatim as part of the name"
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

    Two fields state what a single file list cannot:

    - ``files_without_save_id`` is the same set as the emulator names it when
      the content carries no platform-native id — Flycast falls back to the
      ROM's own name for arcade content and for a disc whose header states no
      id (``oslib.cpp:44`` vs ``:62``). The set is genuinely conditional on a
      fact atlas does not read, so the resolver states the id-keyed set and
      hands the alternative to the caller in a caveat instead of picking one.
    - ``files_established_for`` names the class of content the list itself was
      established for, and ``files_citation`` cites that. Not every difference
      between content classes is a spelling: Flycast connects four VMUs on a
      Dreamcast and two on a Naomi board, so for arcade content two of the
      four declared names can never exist. The scope travels into the same
      caveat, machine-readably, so the list is never read as established for
      content it was not.
    - ``also_under`` names a *second* root this mode's save data lives under.
      A card describes one root per mode, so a mode that spans two cannot
      state its file set at all: it declares ``files: None`` plus this field,
      and the resolver says so rather than presenting the half it can see as
      the whole save.
    """

    root: str
    subdir: str | None
    files: tuple[str, ...] | None
    granularity: str
    observe: tuple[str, ...] | None = None
    complete: bool = False
    files_without_save_id: tuple[str, ...] | None = None
    files_established_for: str | None = None
    files_citation: str | None = None
    also_under: str | None = None


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
    granularity = _expect_str(mode.get("granularity"), f"{where}: granularity")
    if granularity not in _KNOWN_GRANULARITIES:
        # It reaches the caller as the contractual Granularity.value, so a
        # misspelling here would be stated as this machine's actual grouping.
        raise ValueError(
            f"{where}: granularity must be one of {sorted(_KNOWN_GRANULARITIES)}, got {granularity!r}"
        )
    files = mode.get("files")
    observe = mode.get("observe")
    complete = mode.get("complete", False)
    if not isinstance(complete, bool):
        # bool("false") is True in Python — never coerce this claim.
        raise ValueError(f"{where}: 'complete' must be a JSON boolean")
    alternative = mode.get("files_without_save_id")
    also_under = _expect_opt_str(mode.get("also_under"), f"{where}: also_under")
    if also_under is not None and also_under not in _KNOWN_MODE_ROOTS:
        raise ValueError(
            f"{where}: also_under must be one of {sorted(_KNOWN_MODE_ROOTS)}, got {also_under!r}"
        )
    if also_under == root:
        raise ValueError(
            f"{where}: also_under names this mode's own root ({root!r}) — it exists to name the "
            "*second* root the save reaches, and a root does not span itself"
        )
    if also_under is not None and files is not None:
        # The field exists because one file list cannot describe a save that
        # lies under two roots — a card that states both contradicts itself.
        raise ValueError(
            f"{where}: a mode with 'also_under' cannot declare 'files' — its save data reaches "
            "beyond this root, so the set is not statable here"
        )
    alternative_names: tuple[str, ...] | None = None
    if alternative is not None:
        if files is None or not any(TEMPLATE_SAVE_ID in name for name in files):
            raise ValueError(
                f"{where}: 'files_without_save_id' is the set for content that carries no id, so "
                f"'files' must declare the {TEMPLATE_SAVE_ID} case it is the alternative to"
            )
        alternative_names = _expect_file_names(alternative, f"{where}: files_without_save_id")
        if any(TEMPLATE_SAVE_ID in name for name in alternative_names):
            raise ValueError(
                f"{where}: 'files_without_save_id' describes content without an id — it cannot "
                f"name one with {TEMPLATE_SAVE_ID}"
            )
    # Both are answer content, not flags: an empty one would reach the caller as
    # an empty scope or an empty citation, which says nothing at all.
    raw_scope = mode.get("files_established_for")
    established_for = (
        _expect_str(raw_scope, f"{where}: files_established_for") if raw_scope is not None else None
    )
    raw_citation = mode.get("files_citation")
    citation = _expect_str(raw_citation, f"{where}: files_citation") if raw_citation is not None else None
    if established_for is not None and files is None:
        raise ValueError(
            f"{where}: 'files_established_for' scopes a declared set — a mode that states no 'files' "
            "has nothing to scope"
        )
    if citation is not None and established_for is None:
        raise ValueError(
            f"{where}: 'files_citation' cites the scope in 'files_established_for', which this mode "
            "does not state"
        )
    return SaveMode(
        root=root,
        subdir=_expect_opt_str(mode.get("subdir"), f"{where}: subdir"),
        files=_expect_file_names(files, f"{where}: files") if files is not None else None,
        granularity=granularity,
        observe=_expect_file_names(observe, f"{where}: observe") if observe is not None else None,
        complete=complete,
        files_without_save_id=alternative_names,
        files_established_for=established_for,
        files_citation=citation,
        also_under=also_under,
    )


def _expect_selectable_modes(where: str, *, option_key: str | None, modes: Mapping[str, SaveMode]) -> None:
    """A card without a governing option states exactly the ``always`` mode.

    Nothing selects between modes when no option governs the card, so the
    resolver takes ``always`` and only ``always``. A card that names its one
    mode anything else, or names several, therefore describes behaviour that
    can never be applied: the answer comes back with no rule card behind it and
    no caveat either, because from the resolver's side nothing went wrong. The
    card is shipped with the code, so that is a build mistake, not a state of
    the machine — it fails the load.
    """
    if option_key is not None or set(modes) == {MODE_ALWAYS}:
        return
    raise ValueError(
        f"{where}: a card with no governing_option.key selects nothing, so it must declare exactly "
        f"the {MODE_ALWAYS!r} mode — got {sorted(modes) or 'no modes at all'}"
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
        option_key = _expect_opt_str(governing.get("key"), f"{where}: governing_option.key")
        _expect_selectable_modes(where, option_key=option_key, modes=modes)
        cards.append(
            CoreCard(
                key=key,
                so_names=_expect_str_list(identifiers.get("so", []), f"{where}: identifiers.so"),
                library_names=_expect_str_list(
                    identifiers.get("library_name", []), f"{where}: identifiers.library_name"
                ),
                option_key=option_key,
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
    """One arrangement's verification record — ``None`` stays *never verified*.

    A record that is present must pin the arrangement ``version``. That is the
    field the drift check hangs on: with it null, no machine can ever disagree
    with the record, so the entry would read as verified everywhere and forever
    while pinning nothing at all — the one shape that is worse than *never
    verified*, because it claims the opposite. The core's version may stay null;
    plenty of cores report none, and the arrangement version still bounds what
    was checked.
    """
    if rec is None:
        return None
    return VerifiedOn(
        version=_expect_str(rec.get("version"), f"{where}.version"),
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
