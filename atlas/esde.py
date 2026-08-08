"""ES-DE catalogue parsing — which emulators can launch which system, live.

``es_systems.xml`` is the *choice* source of the emulator catalogue: per system,
the launch entries in declared order, first entry = ES-DE's default. Two layers
are read live through the machine seam and merged:

1. the bundled file (inside the frontend's installation), then
2. the user overlay ``<ES-DE home>/custom_systems/es_systems.xml`` — a system
   defined there **replaces** the bundled system of the same name (ES-DE
   USERGUIDE, "Game System Customizations"); new names are added.

A command containing ``*_libretro.so`` is a libretro entry (the ``.so`` basename
is extracted); anything else is a standalone entry. Classification only — no
path knowledge is derived from the command text.

Honest degradations: a missing layer is skipped; a malformed layer is skipped
the same way (recorded per answer as its absence — structured catalogue error
reporting is on the task list). The user's saved per-system emulator choice
(``es_settings.xml``) is **not** read yet: its key format is unverified — the
declared order is the answer until then (task list).

Pure text in, entries out. No I/O.

Parsing uses stdlib ``xml.etree`` deliberately: the input is local config from
the user's own machine (not attacker-controlled in this threat model), modern
expat (Python ≥ 3.11) rejects entity-expansion attacks — surfacing as
``ParseError``, i.e. an honestly skipped layer — and ``dependencies = []`` is a
design contract (DESIGN.md, consumption), so ``defusedxml`` is not an option.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping

# The core ``.so`` inside a launch command. The name run is bounded by
# NAME_MAX (255): an unbounded quantifier in front of the ``_libretro`` suffix
# rescans a long name from every position it fails at, and no run longer than
# a file name can be one anyway.
_CORE_SO_RE = re.compile(r"([A-Za-z0-9_\-\[\]]{1,255}_libretro\.so)")

KIND_LIBRETRO = "libretro"
KIND_STANDALONE = "standalone"


@dataclass(frozen=True, slots=True)
class EmulatorSpec:
    """One launch entry of one system, as declared in ``es_systems.xml``.

    ``core_so`` is the extracted ``.so`` basename for libretro entries, ``None``
    for standalone ones. ``provenance`` names the file layer that defined the
    system (bundled or custom overlay).
    """

    system: str
    label: str
    kind: str
    core_so: str | None
    command: str
    provenance: str
    selection: str | None = None


def _launch_entries(system_el: ET.Element, *, system: str, provenance: str) -> tuple[EmulatorSpec, ...]:
    """The launch entries one ``<system>`` declares, in declared order.

    A command naming a ``*_libretro.so`` is a libretro entry (the basename is
    extracted); anything else is standalone. Classification only.
    """
    entries: list[EmulatorSpec] = []
    for command_el in system_el.findall("command"):
        command = (command_el.text or "").strip()
        if not command:
            continue
        match = _CORE_SO_RE.search(command)
        entries.append(
            EmulatorSpec(
                system=system,
                label=(command_el.get("label") or "").strip(),
                kind=KIND_LIBRETRO if match else KIND_STANDALONE,
                core_so=match.group(1) if match else None,
                command=command,
                provenance=provenance,
            )
        )
    return tuple(entries)


@dataclass(frozen=True, slots=True)
class SystemDeclaration:
    """One ``<system>`` as declared: what launches it, where it lives, what counts.

    ``rom_path`` is the ``<path>`` text **verbatim**, still carrying whatever
    tokens the file wrote (``%ROMPATH%/n64``) — resolving them needs a setting
    this parser does not read, so substituting here would mean guessing it.
    ``extensions`` is the ``<extension>`` list split on whitespace, each token
    exactly as declared: ES-DE lists both cases separately (``.z64 .Z64``), and
    normalizing would state a vocabulary the file does not.
    """

    entries: tuple[EmulatorSpec, ...] = ()
    rom_path: str | None = None
    extensions: tuple[str, ...] = ()


def parse_es_systems(text: str, *, provenance: str) -> dict[str, SystemDeclaration]:
    """Parse one ``es_systems.xml`` layer into ``{system: declaration}``.

    Malformed XML yields ``{}`` — the layer is skipped, never guessed at.
    """
    try:
        root = ET.fromstring(text)
    except ET.ParseError:
        return {}
    result: dict[str, SystemDeclaration] = {}
    for system_el in root.findall("system"):
        name = (system_el.findtext("name") or "").strip()
        if not name:
            continue
        declared_path = (system_el.findtext("path") or "").strip()
        result[name] = SystemDeclaration(
            entries=_launch_entries(system_el, system=name, provenance=provenance),
            rom_path=declared_path or None,
            extensions=tuple((system_el.findtext("extension") or "").split()),
        )
    return result


def merge_layers(
    bundled: dict[str, SystemDeclaration],
    custom: dict[str, SystemDeclaration],
) -> dict[str, SystemDeclaration]:
    """Merge the custom overlay over the bundled catalogue.

    Per ES-DE semantics a custom system of the same name replaces the bundled
    one entirely; other custom systems are added. Replacement is of the whole
    declaration — an overlay system brings its own path and extensions along
    with its commands, because that is the ``<system>`` element ES-DE ends up
    with.
    """
    merged = dict(bundled)
    merged.update(custom)
    return merged


# ES-DE substitutes exactly this token in a system's <path>, and only there;
# every other %TOKEN% in the file belongs to <command>.
ROMPATH_TOKEN = "%ROMPATH%"


def parse_es_settings(text: str) -> dict[str, str] | None:
    """The ``<string name= value=>`` settings of an ``es_settings.xml``, or ``None``.

    **The file is not well-formed XML**, and that is not a defect to route
    around: ES-DE writes a bare sequence of sibling elements with no root, and
    reads it back with pugixml, which does not enforce the single-root rule.
    Handing the text straight to ``xml.etree`` fails at the second element
    ("junk after document element") — so a reader that did the obvious thing
    would call every real machine's settings unreadable and then report the ROM
    directory unresolvable everywhere. The document is wrapped in a synthetic
    root to read the fragments the way the writer meant them.

    A leading UTF-8 BOM is stripped for the same reason, not as a courtesy:
    pugixml detects the encoding from it and reads such a file normally, so the
    settings in it are the settings *in force*, and refusing them would make
    atlas answer about a configuration the frontend is not using. It has to go
    before the wrap either way — inside one it would push the XML declaration
    off the front of the document.

    ``None`` is *unparseable even then*, and is why this does not simply answer
    ``{}``: a file that could not be read and a file that sets nothing are the
    same empty mapping and opposite facts, and only one of them means the
    frontend's own defaults apply.
    """
    body = text.removeprefix("\ufeff").lstrip()
    if body.startswith("<?"):
        _, _, body = body.partition("?>")
    try:
        root = ET.fromstring(f"<es-settings>{body}</es-settings>")
    except ET.ParseError:
        return None
    settings: dict[str, str] = {}
    for element in root:
        if element.tag != "string":
            continue
        name = element.get("name")
        if name:
            settings[name] = element.get("value") or ""
    return settings


def _collapse_separators(path: str) -> str:
    """ES-DE's ``//`` collapse — the loop, not one pass.

    ``Utils::String::replace`` re-scans until the pattern is gone
    (``es-core/src/utils/StringUtil.cpp``, ES-DE 3.4.1), so ``a///b`` reaches
    ``a/b`` there. Python's ``str.replace`` is one pass over non-overlapping
    matches and would leave ``a//b`` behind, which is a different directory
    string for anything comparing paths textually.
    """
    while "//" in path:
        path = path.replace("//", "/")
    return path


def resolve_rom_path(declared: str, rom_directory: str | None) -> str | None:
    """A system's declared ``<path>`` with ``%ROMPATH%`` substituted, or ``None``.

    Follows ``SystemData::loadConfig()`` (``es-app/src/SystemData.cpp``, ES-DE
    3.4.1, ~L859-861, line numbers read from the tagged source over the web):
    the token is replaced with the ROM directory, then ``//`` is collapsed —
    **unconditionally**, on a path that carried no token just the same, which is
    why the collapse here is not inside the substitution branch.

    The directory is normalized to exactly one trailing separator first, because
    that is the shape ES-DE substitutes: ``FileData::getROMDirectory()``
    (``es-app/src/FileData.cpp``, ES-DE 3.4.1, ~L313-345) appends one where the
    configured value lacks it and returns the empty-setting default with one
    already on. Doing it here rather than appending unconditionally keeps a
    configured ``…/roms/`` from spelling the answer ``…/roms//n64``.

    A declared path carrying no token needs no directory and resolves to
    itself: ES-DE insists on the token only in ``createSystemDirectories()``
    (~L1366, the placeholder-generation path), never when loading the
    catalogue, so a literal ``<path>`` is a real path here too.

    ``None`` when the token is present and *rom_directory* cannot stand in for
    it: unset, or not absolute. A relative or ``~``-prefixed value is refused
    rather than expanded, because what those resolve against is the ES-DE
    process's own environment inside its sandbox, which atlas has not
    established — and a ROM directory guessed wrong is a directory the caller
    would go looking in.
    """
    if ROMPATH_TOKEN not in declared:
        return _collapse_separators(declared) or None
    if not rom_directory or not rom_directory.startswith("/"):
        return None
    return _collapse_separators(declared.replace(ROMPATH_TOKEN, f"{rom_directory.rstrip('/')}/"))


@dataclass(frozen=True, slots=True)
class GamelistSelections:
    """The user's emulator choices stored in a system's ``gamelist.xml``.

    ``system_label`` is the per-system choice (``<alternativeEmulator><label>``,
    beside or inside ``<gameList>`` — see :func:`parse_gamelist`); ``per_game``
    maps each game entry's gamelist-relative path (``./`` stripped; a file
    path, or a folder path for directory entries) to its ``<altemulator>``
    label. Both tag names are verified against the ES-DE binary's strings; the
    per-game hierarchy is game > system > declared.
    """

    system_label: str | None
    per_game: Mapping[str, str]

    def __post_init__(self) -> None:
        object.__setattr__(self, "per_game", MappingProxyType(dict(self.per_game)))


def parse_gamelist(text: str) -> GamelistSelections:
    """Parse a ``gamelist.xml`` for emulator selections — both shapes ES-DE reads.

    The per-system selection lives in one of two places, and atlas reads it
    where ES-DE reads it, in ES-DE's own order — the document-level element
    first, the ``<gameList>`` child as the fallback
    (``es-app/src/GamelistFileParser.cpp:190-192``, ES-DE commit ``9207fc77``):

    1. as a **second root element** before ``<gameList>`` — what ES-DE writes
       today (``doc.prepend_child``, same file line 420 when updating an
       existing gamelist and line 444 when creating one), and not well-formed
       XML.
    2. as a **child of ``<gameList>``** — the standards-compliant location
       ES-DE is moving to ("Added forward compatibility for reading the
       alternativeEmulator element from the gameList root element",
       2026-07-29). This is the shape observed live on RetroDECK 0.10.9b,
       whose launcher matches it too (``libexec/run_game.sh:125-135``, an awk
       range over the file).

    Deeper in the tree — inside a ``<game>`` — is not a place ES-DE looks for
    the *system* selection (a game's own choice is ``<altemulator>``), so the
    lookup stays depth-bounded instead of searching the whole document.

    Wrapping in a synthetic root parses the two-root quirk and well-formed
    variants alike; anything unparseable yields empty selections, never a guess.
    """
    stripped = text.strip()
    if stripped.startswith("<?"):
        end = stripped.find("?>")
        if end != -1:
            stripped = stripped[end + 2 :]
    try:
        root = ET.fromstring(f"<atlas-wrapper>{stripped}</atlas-wrapper>")
    except ET.ParseError:
        return GamelistSelections(system_label=None, per_game={})
    selection_el = root.find("alternativeEmulator")
    if selection_el is None:
        game_list = root.find("gameList")
        if game_list is not None:
            selection_el = game_list.find("alternativeEmulator")
    system_label: str | None = None
    if selection_el is not None:
        system_label = (selection_el.findtext("label") or "").strip() or None
    per_game: dict[str, str] = {}
    for game in root.iter("game"):
        path = (game.findtext("path") or "").strip()
        label = (game.findtext("altemulator") or "").strip()
        if path and label:
            # Keep the full gamelist-relative path — basenames alone collide
            # when subdirectories repeat a name (./USA/Game.iso vs ./Japan/…).
            normalized = path.replace("\\", "/").rstrip("/")
            if normalized.startswith("./"):
                normalized = normalized[2:]
            per_game[normalized] = label
    return GamelistSelections(system_label=system_label, per_game=per_game)


def parse_gamelist_alternative(text: str) -> str | None:
    """The per-system ``alternativeEmulator`` label — see :func:`parse_gamelist`."""
    return parse_gamelist(text).system_label
