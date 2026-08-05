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
    for standalone ones. ``source`` names the file layer that defined the
    system (bundled or custom overlay).
    """

    system: str
    label: str
    kind: str
    core_so: str | None
    command: str
    source: str
    selection: str | None = None


def _launch_entries(system_el: ET.Element, *, system: str, source: str) -> tuple[EmulatorSpec, ...]:
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
                source=source,
            )
        )
    return tuple(entries)


def parse_es_systems(text: str, *, source: str) -> dict[str, tuple[EmulatorSpec, ...]]:
    """Parse one ``es_systems.xml`` layer into ``{system: entries-in-order}``.

    Malformed XML yields ``{}`` — the layer is skipped, never guessed at.
    """
    try:
        root = ET.fromstring(text)
    except ET.ParseError:
        return {}
    result: dict[str, tuple[EmulatorSpec, ...]] = {}
    for system_el in root.findall("system"):
        name = (system_el.findtext("name") or "").strip()
        if not name:
            continue
        result[name] = _launch_entries(system_el, system=name, source=source)
    return result


def merge_layers(
    bundled: dict[str, tuple[EmulatorSpec, ...]],
    custom: dict[str, tuple[EmulatorSpec, ...]],
) -> dict[str, tuple[EmulatorSpec, ...]]:
    """Merge the custom overlay over the bundled catalogue.

    Per ES-DE semantics a custom system of the same name replaces the bundled
    one entirely; other custom systems are added.
    """
    merged = dict(bundled)
    merged.update(custom)
    return merged


@dataclass(frozen=True, slots=True)
class GamelistSelections:
    """The user's emulator choices stored in a system's ``gamelist.xml``.

    ``system_label`` is the per-system choice (``<alternativeEmulator><label>``,
    top-level); ``per_game`` maps each game entry's gamelist-relative path
    (``./`` stripped; a file path, or a folder path for directory entries) to
    its ``<altemulator>`` label. Both tag names are verified against the ES-DE
    binary's strings; the per-game hierarchy is game > system > declared.
    """

    system_label: str | None
    per_game: Mapping[str, str]

    def __post_init__(self) -> None:
        object.__setattr__(self, "per_game", MappingProxyType(dict(self.per_game)))


def parse_gamelist(text: str) -> GamelistSelections:
    """Parse a ``gamelist.xml`` for emulator selections — tolerant of ES-DE's quirk.

    ES-DE writes the file with **two root elements** (``<alternativeEmulator>``
    before ``<gameList>``) — not well-formed XML, observed live on RetroDECK
    0.10.9b. Wrapping in a synthetic root parses both that and well-formed
    variants; anything unparseable yields empty selections, never a guess.
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
    system_label = (root.findtext("alternativeEmulator/label") or "").strip() or None
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
