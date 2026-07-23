"""emu-atlas — the map of where emulators keep things.

A resolver, not a lookup: for any emulator installation on a machine, atlas
answers which configs govern it and where saves and BIOS actually live — by
reading the running machine the way the emulator does. Phase 1 covers RetroArch
across the RetroDECK, EmuDeck, standalone-Flatpak, and native install flavors,
plus the BIOS registry.

Two entry points, per DESIGN.md:

- :func:`atlas.detect` finds what is installed and returns installation handles;
- every question is asked of a handle —
  ``installation.save_location(content_path=..., core_so=...)``.
"""

from __future__ import annotations

from atlas.bios import BiosEntry, BiosRegistry, load_registry
from atlas.core_info import parse_core_info
from atlas.detect import detect
from atlas.installations import (
    HEALTH_CONFIG_UNREADABLE,
    HEALTH_OK,
    HEALTH_ROOT_MISSING,
    EmuDeck,
    Installation,
    NativeRetroArch,
    RetroDeck,
    StandaloneRetroArchFlatpak,
)
from atlas.machine import CoreInfo, FixtureMachine, Machine, RealMachine
from atlas.oddities import CoreCard, SaveMode, load_oddities, lookup_card
from atlas.placement import (
    CAVEAT_CORE_UNQUERYABLE,
    CAVEAT_FILENAMES_UNVERIFIED,
    CAVEAT_HEALTH,
    CAVEAT_NO_CORE,
    CAVEAT_SORTED_DIR_MISSING,
    CAVEAT_SYSTEM_DIR_UNSET,
    CAVEAT_UNKNOWN_OPTION_VALUE,
    ROOT_CONTENT_DIRECTORY,
    ROOT_SAVEFILE_DIRECTORY,
    ROOT_SYSTEM_DIRECTORY,
    UNKNOWN_FILE_SET,
    Caveat,
    FileSet,
    Granularity,
    SavePlacement,
    build_save_placement,
)
from atlas.retroarch_cfg import (
    EMUDECK_DEFAULTS,
    RETRODECK_DEFAULTS,
    UPSTREAM_DEFAULTS,
    LayoutDefaults,
    RetroArchCfg,
    interpret_cfg,
    parse_cfg_text,
    resolve_save_layout,
)

__all__ = [
    "BiosEntry",
    "CAVEAT_CORE_UNQUERYABLE",
    "CAVEAT_FILENAMES_UNVERIFIED",
    "CAVEAT_HEALTH",
    "CAVEAT_NO_CORE",
    "CAVEAT_SORTED_DIR_MISSING",
    "CAVEAT_SYSTEM_DIR_UNSET",
    "CAVEAT_UNKNOWN_OPTION_VALUE",
    "Caveat",
    "BiosRegistry",
    "CoreInfo",
    "EMUDECK_DEFAULTS",
    "EmuDeck",
    "FileSet",
    "FixtureMachine",
    "HEALTH_CONFIG_UNREADABLE",
    "HEALTH_OK",
    "HEALTH_ROOT_MISSING",
    "Installation",
    "LayoutDefaults",
    "Machine",
    "NativeRetroArch",
    "RETRODECK_DEFAULTS",
    "ROOT_CONTENT_DIRECTORY",
    "ROOT_SAVEFILE_DIRECTORY",
    "ROOT_SYSTEM_DIRECTORY",
    "RealMachine",
    "RetroArchCfg",
    "RetroDeck",
    "SavePlacement",
    "StandaloneRetroArchFlatpak",
    "UNKNOWN_FILE_SET",
    "UPSTREAM_DEFAULTS",
    "build_save_placement",
    "detect",
    "interpret_cfg",
    "load_registry",
    "parse_cfg_text",
    "resolve_save_layout",
]
