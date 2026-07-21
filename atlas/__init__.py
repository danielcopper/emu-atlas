"""emu-atlas — the map of where emulators keep things.

A config-aware knowledge library: for any emulator installation on a machine, it
answers which configs govern it and where saves and BIOS actually live. Phase 1
covers RetroArch across the RetroDECK, standalone-Flatpak, and native install
flavors, plus the BIOS registry.

Two entry points, per DESIGN.md:

- :func:`atlas.detect` finds what is installed and returns installation handles;
- every question is asked of a handle — ``installation.save_placement(system, core=...)``.
"""

from __future__ import annotations

from atlas.bios import BiosEntry, BiosRegistry, load_registry
from atlas.core_info import parse_core_info
from atlas.detect import detect
from atlas.installations import (
    Installation,
    NativeRetroArch,
    RetroDeck,
    StandaloneRetroArchFlatpak,
)
from atlas.placement import SavePlacement, build_save_placement
from atlas.reader import FilesystemReader, FixtureReader, Reader
from atlas.retroarch_cfg import RetroArchCfg, interpret_cfg

__all__ = [
    "BiosEntry",
    "BiosRegistry",
    "FilesystemReader",
    "FixtureReader",
    "Installation",
    "NativeRetroArch",
    "Reader",
    "RetroArchCfg",
    "RetroDeck",
    "SavePlacement",
    "StandaloneRetroArchFlatpak",
    "build_save_placement",
    "detect",
    "interpret_cfg",
    "load_registry",
    "parse_core_info",
]
