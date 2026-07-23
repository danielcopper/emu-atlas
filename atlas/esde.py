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

_CORE_SO_RE = re.compile(r"([A-Za-z0-9_\-\[\]]+_libretro\.so)")

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
        entries: list[EmulatorSpec] = []
        for command_el in system_el.findall("command"):
            command = (command_el.text or "").strip()
            if not command:
                continue
            match = _CORE_SO_RE.search(command)
            entries.append(
                EmulatorSpec(
                    system=name,
                    label=(command_el.get("label") or "").strip(),
                    kind=KIND_LIBRETRO if match else KIND_STANDALONE,
                    core_so=match.group(1) if match else None,
                    command=command,
                    source=source,
                )
            )
        result[name] = tuple(entries)
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
