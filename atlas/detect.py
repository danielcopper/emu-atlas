"""Detection — what emulator installations are present on a machine.

``detect(home, machine)`` probes the config markers of each known arrangement
and returns a handle for every one it finds. Detection labels markers, it does
not partition: EmuDeck *is* a configured ``org.libretro.RetroArch``, so the
EmuDeck marker is checked before concluding "bare standalone", and the EmuDeck
handle claims the standalone Flatpak (its ``kinds`` carries both descriptions —
no second handle for the same RetroArch). Multiple arrangements coexist
ordinarily, so the result is a list, highest-priority first: RetroDECK, EmuDeck,
the standalone RetroArch Flatpak, a native RetroArch. Ambiguity is a truthful
result — the caller chooses, atlas never silently picks a winner.

Every probe goes through the machine seam, so detection is fully provable from a
fixture machine.
"""

from __future__ import annotations

import os

from atlas.installations import (
    EMUDECK_SETTINGS_SUFFIX,
    NATIVE_CFG_SUFFIX,
    RETRODECK_JSON_SUFFIX,
    STANDALONE_FLATPAK_CFG_SUFFIX,
    EmuDeck,
    Installation,
    NativeRetroArch,
    RetroDeck,
    StandaloneRetroArchFlatpak,
)
from atlas.machine import Machine, RealMachine


def detect(home: str, machine: Machine | None = None) -> list[Installation]:
    """Detect the emulator installations under *home*.

    *machine* defaults to the real machine; tests and conformance vectors pass a
    fixture. Returns the detected installations in probe order (an empty list
    when nothing is installed).
    """
    if machine is None:
        machine = RealMachine()

    found: list[Installation] = []

    retrodeck_json = machine.read_text(os.path.join(home, RETRODECK_JSON_SUFFIX)).text
    if retrodeck_json is not None:
        found.append(RetroDeck(home, machine, retrodeck_json))

    emudeck_settings = machine.read_text(os.path.join(home, EMUDECK_SETTINGS_SUFFIX)).text
    emudeck_present = emudeck_settings is not None
    if emudeck_present:
        found.append(EmuDeck(home, machine, emudeck_settings))

    # EmuDeck claims the standalone Flatpak — same RetroArch, two descriptions,
    # one handle. Only an unclaimed Flatpak appears as its own installation.
    if not emudeck_present and machine.path_kind(os.path.join(home, STANDALONE_FLATPAK_CFG_SUFFIX)) == "file":
        found.append(StandaloneRetroArchFlatpak(home, machine))

    if machine.path_kind(os.path.join(home, NATIVE_CFG_SUFFIX)) == "file":
        found.append(NativeRetroArch(home, machine))

    return found
