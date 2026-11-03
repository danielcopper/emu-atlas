"""What each emulator's save files hold, as packaged knowledge — the facts the save-portability question compares.

A save answer says where an entry keeps a game's save and which files make it
up. Whether another entry can read those files is a question about their
*contents*, and nothing on the machine states it: it is read out of each
emulator's source at a pinned build, so it is world knowledge, packaged here
(``atlas/data/save_formats.json``) with a citation per fact and a pin per
emulator (#577 D5).

The knowledge is stated per emulator, never per pair. Each record labels the
file groups of that emulator's own save answer, mode by mode: which memory of
the console a file holds (:data:`SAVE_MEMORIES`), which slot, and its
**format** — an identity string two records share exactly where the sources
say the bytes are laid out alike. A pair's verdict is then computed from the
two records and the two save answers (:mod:`atlas.save_portability`), so a
system with *n* entries needs *n* records rather than a table of every pair.
The few ways one format turns into another that a source states are listed
apart, as conversions.

The method and the evidence are in ``docs/research/save-portability.md``.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Literal, Mapping

from ._data import packaged_text

SAVE_FORMATS_SCHEMA = 1

SaveMemory = Literal[
    "memory-card",
    "internal-backup-ram",
    "cartridge-backup-ram",
]
MEMORY_MEMORY_CARD: SaveMemory = "memory-card"
"""A memory card in one of the console's card slots, which ``slot`` names."""
MEMORY_INTERNAL_BACKUP_RAM: SaveMemory = "internal-backup-ram"
"""The battery-backed save memory built into the console, such as the Saturn's 32 KiB."""
MEMORY_CARTRIDGE_BACKUP_RAM: SaveMemory = "cartridge-backup-ram"
"""A backup memory cartridge plugged into the console beside the game, such as a Saturn backup cartridge."""
# What one part of a save holds, the vocabulary of a part's ``memory``. It
# grows with the systems the records cover, and only with words a record
# states.
SAVE_MEMORIES = (
    MEMORY_MEMORY_CARD,
    MEMORY_INTERNAL_BACKUP_RAM,
    MEMORY_CARTRIDGE_BACKUP_RAM,
)

# The rules a conversion can be stated by as data (#577 D4), where a source
# pins one as plain as a length. None does yet: every conversion the records
# carry is one a program performs without a rule a client could apply, so it
# is stated as convertible with its citation and no rule.
CONVERSION_RULES: tuple[str, ...] = ()

# What a pinned build string names, which the answer's sources say beside a
# confirmed build (#577 D12): a commit, a release, or a channel whose name
# follows the newest build.
_PIN_KINDS = ("commit", "release", "channel")
_KINDS = ("libretro", "standalone")

# A file name's holes: ``<rom_stem>``, ``<save_id>`` and the rest stand for
# whatever the save answer filled in, or for the hole itself where it filled
# nothing.
_HOLE = re.compile(r"<[a-z_]+>")


@dataclass(frozen=True, slots=True)
class FormatBuild:
    """The build one emulator's records were read at, as the machine names it.

    ``pins`` are the strings the machine states for that build — a core's
    ``library_version``, a standalone emulator's RetroDECK ``component_version``
    — and a build is confirmed by an exact match of one of them (#577 D5, D12).
    ``names`` says what such a string names: ``commit``, ``release`` or
    ``channel``. ``revision`` is the commit the citations were read at.
    """

    pins: tuple[str, ...]
    names: str
    revision: str
    citation: str


@dataclass(frozen=True, slots=True)
class PartRule:
    """How one part of an emulator's save is told apart in its save answer, and what it holds.

    A file of the answer is this part where its name matches one of ``names`` —
    templates whose holes match whatever the answer filled in — or, where the
    answer's readings state ``reading``, where its name is that reading's file
    name. ``modes`` is a pattern over the answer's live mode: the rule speaks
    only where the mode matches it, and everywhere when it is ``None``. A
    ``folder`` rule matches a group whose names are not stated and whose
    directory carries the name instead — PCSX2's folder card.
    """

    memory: str
    slot: str | None
    format: str
    names: tuple[str, ...]
    citation: str
    modes: re.Pattern[str] | None = None
    reading: str | None = None
    folder: bool = False
    frontend: bool = False
    """The file is RetroArch's: the core hands it over through a memory id and RetroArch writes and loads it, so
    RetroArch's own ``save_file_compression`` applies to it."""

    def applies(self, mode: str | None) -> bool:
        """Whether this rule speaks for a save answer in *mode*."""
        if self.modes is None:
            return True
        return mode is not None and self.modes.search(mode) is not None

    def specificity(self, name: str, readings: Mapping[str, str]) -> int | None:
        """How closely *name* matches this rule — the literal characters matched — or ``None`` where it does not."""
        stated = readings.get(self.reading) if self.reading is not None else None
        if stated is not None:
            wanted = stated.rsplit("/", 1)[-1]
            return len(wanted) if name == wanted else None
        best: int | None = None
        for template in self.names:
            if _template_pattern(template).fullmatch(name) is not None:
                literal = len(_HOLE.sub("", template))
                best = literal if best is None else max(best, literal)
        return best


@dataclass(frozen=True, slots=True)
class IgnoredGroup:
    """What of an emulator's save answer is no part of a game's save on this system, and why.

    Either a directory (``dir_suffix``): Kronos's ST-V files, which a Saturn
    disc never writes. Or files by name (``names``, templates as a part rule
    spells them): Beetle Saturn's ``.smpc``, the console's own clock and
    settings memory rather than a game's progress (#577 D13). The citation
    rides the answer's sources wherever the statement sets something aside.
    """

    citation: str
    dir_suffix: str | None = None
    names: tuple[str, ...] = ()

    def covers(self, directory: str) -> bool:
        """Whether *directory* is the one this statement is about."""
        return self.dir_suffix is not None and directory.rstrip("/").endswith("/" + self.dir_suffix)

    def claims(self, name: str) -> bool:
        """Whether the file *name* is one this statement sets aside."""
        return any(_template_pattern(template).fullmatch(name) is not None for template in self.names)


@dataclass(frozen=True, slots=True)
class SystemFormats:
    """One emulator's part rules for one system, and the directories that hold no part of it."""

    parts: tuple[PartRule, ...]
    ignore: tuple[IgnoredGroup, ...] = ()


@dataclass(frozen=True, slots=True)
class EmulatorFormats:
    """One emulator's save-format record: the build it was read at and its parts per system.

    ``key`` is the core's short name for a libretro core and the catalogue
    token for a standalone emulator — the keys of the save cards it labels.
    """

    key: str
    kind: str
    build: FormatBuild
    systems: Mapping[str, SystemFormats]


@dataclass(frozen=True, slots=True)
class FormatConversion:
    """A way one format becomes another that a source states, with the rule where it is plain (#577 D4)."""

    source: str
    target: str
    rule: str | None
    citation: str


@dataclass(frozen=True, slots=True)
class SaveFormats:
    """The whole packaged table: the formats, the conversions between them, and every emulator's record.

    ``compressed`` is the citation for what RetroArch's ``save_file_compression``
    does to a file it writes for a core: an RZIP stream RetroArch reads back
    and an emulator that does not load the file through RetroArch has to have
    decompressed first.
    """

    formats: Mapping[str, str]
    conversions: tuple[FormatConversion, ...]
    emulators: Mapping[str, EmulatorFormats]
    compressed: str

    def conversion(self, source: str, target: str) -> FormatConversion | None:
        """The stated conversion from *source* to *target*, or ``None``."""
        return next((c for c in self.conversions if c.source == source and c.target == target), None)


def _template_pattern(template: str) -> re.Pattern[str]:
    """*template* as a pattern: each hole matches whatever filled it, the hole's own spelling included."""
    return re.compile(".+".join(re.escape(part) for part in _HOLE.split(template)))


def _expect_str(value: Any, where: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{where}: expected a non-blank string, got {value!r}")
    return value


def _exact(entry: Any, required: set[str], optional: set[str], where: str) -> dict[str, Any]:
    if not isinstance(entry, dict):
        raise ValueError(f"{where}: expected an object, got {entry!r}")
    keys = set(entry)
    if not required <= keys or not keys <= required | optional:
        raise ValueError(
            f"{where}: expected keys {sorted(required)} and optionally {sorted(optional)}, got {sorted(keys)}"
        )
    return entry


def _build(where: str, entry: Any) -> FormatBuild:
    entry = _exact(entry, {"pins", "names", "revision", "citation"}, set(), where)
    pins = entry["pins"]
    if not isinstance(pins, list) or not pins:
        raise ValueError(f"{where}: pins must be a non-empty list, got {pins!r}")
    names = entry["names"]
    if names not in _PIN_KINDS:
        raise ValueError(f"{where}: names must be one of {_PIN_KINDS}, got {names!r}")
    return FormatBuild(
        pins=tuple(_expect_str(pin, f"{where}.pins") for pin in pins),
        names=names,
        revision=_expect_str(entry["revision"], f"{where}.revision"),
        citation=_expect_str(entry["citation"], f"{where}.citation"),
    )


def _part(where: str, entry: Any, *, formats: Mapping[str, str], revision: str) -> PartRule:
    entry = _exact(
        entry,
        {"memory", "slot", "format", "names", "citation"},
        {"modes", "reading", "folder", "frontend"},
        where,
    )
    if entry["memory"] not in SAVE_MEMORIES:
        raise ValueError(f"{where}: memory must be one of {SAVE_MEMORIES}, got {entry['memory']!r}")
    slot = entry["slot"]
    if slot is not None:
        _expect_str(slot, f"{where}.slot")
    if entry["format"] not in formats:
        raise ValueError(f"{where}: format {entry['format']!r} is not one the table states")
    names = entry["names"]
    if not isinstance(names, list) or not names:
        raise ValueError(f"{where}: names must be a non-empty list, got {names!r}")
    citation = _expect_str(entry["citation"], f"{where}.citation")
    if revision not in citation:
        raise ValueError(f"{where}: the citation does not name the revision {revision!r} it was read at")
    folder, frontend = entry.get("folder", False), entry.get("frontend", False)
    if type(folder) is not bool or type(frontend) is not bool:
        raise ValueError(f"{where}: folder and frontend must be true or false, got {folder!r}, {frontend!r}")
    modes = entry.get("modes")
    return PartRule(
        memory=entry["memory"],
        slot=slot,
        format=entry["format"],
        names=tuple(_expect_str(name, f"{where}.names") for name in names),
        citation=citation,
        modes=None if modes is None else re.compile(_expect_str(modes, f"{where}.modes")),
        reading=None if entry.get("reading") is None else _expect_str(entry["reading"], f"{where}.reading"),
        folder=folder,
        frontend=frontend,
    )


def _ignored(where: str, entry: Any) -> IgnoredGroup:
    entry = _exact(entry, {"citation"}, {"dir_suffix", "names"}, where)
    if ("dir_suffix" in entry) == ("names" in entry):
        raise ValueError(f"{where}: an ignore states exactly one of dir_suffix and names, got {sorted(entry)}")
    names = entry.get("names", [])
    if "names" in entry and (not isinstance(names, list) or not names):
        raise ValueError(f"{where}: names must be a non-empty list, got {names!r}")
    return IgnoredGroup(
        citation=_expect_str(entry["citation"], f"{where}.citation"),
        dir_suffix=None if "dir_suffix" not in entry else _expect_str(entry["dir_suffix"], f"{where}.dir_suffix"),
        names=tuple(_expect_str(name, f"{where}.names") for name in names),
    )


def _system(where: str, entry: Any, *, formats: Mapping[str, str], revision: str) -> SystemFormats:
    entry = _exact(entry, {"parts"}, {"ignore"}, where)
    parts = entry["parts"]
    if not isinstance(parts, list) or not parts:
        raise ValueError(f"{where}: parts must be a non-empty list, got {parts!r}")
    return SystemFormats(
        parts=tuple(
            _part(f"{where}.parts[{i}]", part, formats=formats, revision=revision) for i, part in enumerate(parts)
        ),
        ignore=tuple(_ignored(f"{where}.ignore[{i}]", item) for i, item in enumerate(entry.get("ignore", []))),
    )


def _systems(where: str, entry: Any, *, formats: Mapping[str, str], revision: str) -> dict[str, SystemFormats]:
    if not isinstance(entry, dict) or not entry:
        raise ValueError(f"{where}: systems must be a non-empty object, got {entry!r}")
    stated = {
        system: _system(f"{where}.{system}", value, formats=formats, revision=revision)
        for system, value in entry.items()
        if not (isinstance(value, dict) and "same_as" in value)
    }
    # A system whose files are exactly another's — saturnjp beside saturn —
    # says so rather than repeating the rules, so the two cannot drift.
    for system, value in entry.items():
        if isinstance(value, dict) and "same_as" in value:
            _exact(value, {"same_as"}, set(), f"{where}.{system}")
            if value["same_as"] not in stated:
                raise ValueError(f"{where}.{system}: same_as names {value['same_as']!r}, which states no rules")
            stated[system] = stated[value["same_as"]]
    return stated


def _emulator(key: str, entry: Any, *, formats: Mapping[str, str]) -> EmulatorFormats:
    where = f"save_formats: {key}"
    entry = _exact(entry, {"kind", "build", "systems"}, set(), where)
    if entry["kind"] not in _KINDS:
        raise ValueError(f"{where}: kind must be one of {_KINDS}, got {entry['kind']!r}")
    build = _build(f"{where}.build", entry["build"])
    return EmulatorFormats(
        key=key,
        kind=entry["kind"],
        build=build,
        systems=MappingProxyType(
            _systems(f"{where}.systems", entry["systems"], formats=formats, revision=build.revision)
        ),
    )


def _conversion(where: str, entry: Any, *, formats: Mapping[str, str]) -> FormatConversion:
    entry = _exact(entry, {"from", "to", "rule", "citation"}, set(), where)
    for side in ("from", "to"):
        if entry[side] not in formats:
            raise ValueError(f"{where}: {side} names {entry[side]!r}, which is not a format the table states")
    rule = entry["rule"]
    if rule is not None and rule not in CONVERSION_RULES:
        raise ValueError(f"{where}: rule must be null or one of {CONVERSION_RULES}, got {rule!r}")
    return FormatConversion(
        source=entry["from"],
        target=entry["to"],
        rule=rule,
        citation=_expect_str(entry["citation"], f"{where}.citation"),
    )


def load_save_formats(text: str | None = None) -> SaveFormats:
    """Load the packaged save-format table (or *text* when supplied, for tests)."""
    raw = json.loads(packaged_text("save_formats.json") if text is None else text)
    if not isinstance(raw, dict) or raw.get("schema") != SAVE_FORMATS_SCHEMA:
        raise ValueError(
            f"save_formats: unsupported schema {raw.get('schema') if isinstance(raw, dict) else None!r} "
            f"(this atlas reads schema {SAVE_FORMATS_SCHEMA})"
        )
    raw = _exact(raw, {"schema", "spec", "formats", "conversions", "emulators", "retroarch"}, set(), "save_formats")
    retroarch = _exact(raw["retroarch"], {"compressed"}, set(), "save_formats: retroarch")
    formats = raw["formats"]
    if not isinstance(formats, dict) or not formats:
        raise ValueError(f"save_formats: formats must be a non-empty object, got {formats!r}")
    for name, description in formats.items():
        _expect_str(description, f"save_formats: formats.{name}")
    if not isinstance(raw["emulators"], dict) or not isinstance(raw["conversions"], list):
        raise ValueError("save_formats: emulators must be an object and conversions a list")
    return SaveFormats(
        formats=MappingProxyType(dict(formats)),
        conversions=tuple(
            _conversion(f"save_formats: conversions[{i}]", entry, formats=formats)
            for i, entry in enumerate(raw["conversions"])
        ),
        emulators=MappingProxyType(
            {key: _emulator(key, entry, formats=formats) for key, entry in raw["emulators"].items()}
        ),
        compressed=_expect_str(retroarch["compressed"], "save_formats: retroarch.compressed"),
    )


_PACKAGED: SaveFormats | None = None


def save_formats() -> SaveFormats:
    """The packaged table, loaded once."""
    global _PACKAGED
    if _PACKAGED is None:
        _PACKAGED = load_save_formats()
    return _PACKAGED
