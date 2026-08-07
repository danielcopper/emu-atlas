"""emu-atlas — the map of where emulators keep things.

A resolver, not a lookup: for any emulator installation on a machine, atlas
answers which configs govern it and where saves and BIOS actually live — by
reading the running machine the way the emulator does. Phase 1 covers RetroArch
across the RetroDECK, EmuDeck and bare (Flatpak or native) arrangements,
plus firmware.

Two entry points, per DESIGN.md:

- :func:`atlas.detect` finds what is installed and returns installation handles;
- every question is asked of a handle —
  ``installation.save_location(content_path=..., core_so=...)``,
  ``installation.firmware_for_core("mgba_libretro.so")``,
  ``installation.firmware_for_system("gba")``,
  ``installation.firmware_inventory()``,
  ``installation.identify_firmware(md5=...)``.

The question's subject may be positional — the core, the system — while every
modifier stays keyword-only.

Choosing a handle is optional: :func:`atlas.every_installation` puts the same
questions to every detected installation at once and answers each labelled with
the handle it came from, so a machine carrying two arrangements gives two true
answers instead of a silently chosen winner.

**What this namespace is.** Everything below is the consumer API: the two entry
points, the handles they answer with, every answer type, the vocabularies those
answers speak, and the serializers that turn an answer into plain data. If you
are writing a client, you never need to import from a submodule.

**What it deliberately is not.** The machine seam, the config and catalogue
parsers, the packaged-data loaders and the module-level resolver functions are
port and tooling surface: real, documented, and imported from their own modules
(``from atlas.machine import FixtureMachine``). They are not here because a
consumer choosing between ``atlas.firmware_for_core(machine, context, ...)``
and ``installation.firmware_for_core(...)`` is a consumer who can pick the
wrong one. See DESIGN.md, "The two tiers", and docs/architecture.md for the map.
"""

from __future__ import annotations

# --- The two entry points, and the aggregate over them -----------------------
from atlas.detect import detect
from atlas.every_installation import EveryInstallation, InstallationAnswer, every_installation

# --- The handles every question is asked of ----------------------------------
from atlas.installations import (
    EmuDeck,
    Installation,
    BareRetroArchNative,
    RetroDeck,
    BareRetroArchFlatpak,
)

# --- The answers ------------------------------------------------------------
from atlas.contract import (
    catalogue_contract,
    emulator_contract,
    firmware_contract,
    health_contract,
    identification_contract,
    installation_answers_contract,
    installation_contract,
    placement_contract,
    systems_contract,
    unresolved_contract,
)
from atlas.firmware import (
    CoreFirmware,
    FirmwareAnswer,
    FirmwareIdentification,
    FirmwareIdentity,
    FirmwareRequirement,
    RefusedDeclaration,
    UnclaimedFile,
)
from atlas.installations import CatalogueAnswer, EmulatorEntry, Health, SystemsAnswer
from atlas.placement import Caveat, FileSet, Granularity, SavePlacement, Unresolved

# --- The vocabularies those answers speak ------------------------------------
# Closed sets, as types and as constants: a client branches on the value, and
# annotates with the type. The seam's own two (`PathKind`, `ReadStatus`) are
# here because answers carry them — a requirement's `found` is a path kind, a
# health finding's `status` is a read status.
from atlas.esde import KIND_LIBRETRO, KIND_STANDALONE
from atlas.evidence import CAVEAT_ARRANGEMENT_UNVERIFIED, CAVEAT_ARRANGEMENT_VERSION_DRIFTED
from atlas.firmware import (
    CAVEAT_EMULATOR_CATALOGUE_UNAVAILABLE,
    CAVEAT_EMULATOR_CATALOGUE_UNESTABLISHED,
    CAVEAT_EMULATOR_CATALOGUE_UNREADABLE,
    CAVEAT_CORE_DIR_UNRESOLVED,
    CAVEAT_CORE_ENUMERATION_INCOMPLETE,
    CAVEAT_CORE_INFO_UNREADABLE,
    CAVEAT_CORE_NOT_INSTALLED,
    CAVEAT_CORE_WITHOUT_SYSTEMNAME,
    CAVEAT_FIRMWARE_CONTENT_CONTRADICTORY,
    CAVEAT_FIRMWARE_CONTENT_UNIDENTIFIED,
    CAVEAT_FIRMWARE_CONTENT_UNSTATED,
    CAVEAT_FIRMWARE_DECLARATION_UNKNOWN,
    CAVEAT_FIRMWARE_DECLARATION_UNREAD,
    CAVEAT_FIRMWARE_PATH_ESCAPES_ROOT,
    CAVEAT_FIRMWARE_PATH_INACCESSIBLE,
    CAVEAT_FIRMWARE_PATH_NAMES_NO_FILE,
    CAVEAT_FIRMWARE_PATH_OBSTRUCTED,
    CAVEAT_FIRMWARE_PATH_UNRESOLVABLE,
    CAVEAT_FIRMWARE_ROOT_MISSING,
    CAVEAT_FIRMWARE_ROOT_UNUSABLE,
    CAVEAT_FIRMWARE_SCAN_INCOMPLETE,
    CAVEAT_FIRMWARE_UNREADABLE,
    CAVEAT_INFO_PATH_UNRESOLVED,
    CAVEAT_NO_FIRMWARE_DECLARATION,
    CAVEAT_NO_FIRMWARE_REQUIREMENT,
    CAVEAT_STANDALONE_UNSUPPORTED,
    CAVEAT_SYSTEM_ASSIGNMENT_DERIVED,
    CAVEAT_SYSTEM_ASSIGNMENT_MAY_HIDE_CORES,
    CAVEAT_SYSTEM_UNKNOWN,
    CHECKED_MISMATCH,
    CHECKED_UNCHECKED,
    CHECKED_UNKNOWN,
    CHECKED_VERIFIED,
    DECLARATION_ABSENT,
    DECLARATION_READ,
    DECLARATION_UNREADABLE,
    DECLARATION_UNSUPPORTED,
    NEED_OPTIONAL,
    NEED_REQUIRED,
    SOURCE_NONE,
    SOURCE_OVERRIDE,
    SOURCE_SLUG,
    SOURCE_SYSTEMNAME,
    CoreDeclarationState,
    FirmwareChecked,
    FirmwareNeed,
    SystemSource,
)
from atlas.installations import (
    HEALTH_ISSUE_COMPANION_CONFIG_MISSING,
    HEALTH_ISSUE_CONFIG_UNREADABLE,
    HEALTH_ISSUE_MARKER_INVALID,
    HEALTH_ISSUE_MARKER_MISSING,
    HEALTH_ISSUE_MARKER_UNREADABLE,
    HEALTH_ISSUE_ROOT_MISSING,
    HEALTH_ISSUE_SAVES_ROOT_MISSING,
)
from atlas.machine import (
    KIND_DIRECTORY,
    KIND_FILE,
    KIND_INACCESSIBLE,
    KIND_MISSING,
    READ_INVALID_TEXT,
    READ_MISSING,
    READ_OK,
    READ_UNREADABLE,
    PathKind,
    ReadStatus,
)
from atlas.placement import (
    CAVEAT_APP_RELATIVE_PATH_UNEXPANDED,
    CAVEAT_CARD_GENERATION_MISMATCH,
    CAVEAT_CARD_MODE_UNCONFIRMED,
    CAVEAT_CFG_LINE_DROPPED,
    CAVEAT_CFG_VALUE_REJECTED,
    CAVEAT_CONTENT_DIR_OBSERVATION,
    CAVEAT_CONTENT_PATH_UNNAMED,
    CAVEAT_CORE_MULTI_OPTION,
    CAVEAT_CORE_SUSPECT,
    CAVEAT_CORE_UNAUDITED,
    CAVEAT_CORE_UNQUERYABLE,
    CAVEAT_DEAD_SYMLINK,
    CAVEAT_FILENAMES_CONTENT_CONDITIONAL,
    CAVEAT_FILENAMES_UNVERIFIED,
    CAVEAT_FILE_SET_SPANS_ROOTS,
    CAVEAT_INVALID_SAVE_DIRECTORY,
    CAVEAT_NO_CORE,
    CAVEAT_PER_GAME_OVERRIDE,
    CAVEAT_PER_GAME_OVERRIDES_PRESENT,
    CAVEAT_SANDBOX_PATH_UNTRANSLATED,
    CAVEAT_SAVE_DIR_UNLISTABLE,
    CAVEAT_SORTED_DIR_MISSING,
    CAVEAT_SORTED_DIR_UNCREATABLE,
    CAVEAT_SYMLINK_LOOP,
    CAVEAT_SYSTEM_DIRECTORY_CLEARED,
    CAVEAT_UNKNOWN_OPTION_VALUE,
    CAVEAT_UNVERIFIED_VERSION,
    FILE_SET_DECLARED,
    FILE_SET_OBSERVED,
    FILE_SET_UNKNOWN,
    GRANULARITIES,
    GRANULARITY_PER_GAME_FILE,
    GRANULARITY_PER_GAME_FILES,
    GRANULARITY_SHARED_CARD,
    HOLE_CONTENT_DIR,
    HOLE_LIBRARY_NAME,
    HOLE_SAVE_ID,
    ROOT_CONTENT_DIRECTORY,
    ROOT_KINDS,
    ROOT_SAVEFILE_DIRECTORY,
    ROOT_SYSTEM_DIRECTORY,
    UNRESOLVED_STANDALONE,
    FileSetState,
    RootKind,
)

__all__ = [
    # Entry points
    "detect",
    "every_installation",
    # The aggregate over detect
    "EveryInstallation",
    "InstallationAnswer",
    # Handles: what detect answers with, and what every question is asked of
    "Installation",
    "RetroDeck",
    "EmuDeck",
    "BareRetroArchFlatpak",
    "BareRetroArchNative",
    # Answers
    "SavePlacement",
    "Unresolved",
    "FileSet",
    "Granularity",
    "Health",
    "CatalogueAnswer",
    "SystemsAnswer",
    "EmulatorEntry",
    "FirmwareAnswer",
    "CoreFirmware",
    "FirmwareRequirement",
    "FirmwareIdentification",
    "FirmwareIdentity",
    "UnclaimedFile",
    "RefusedDeclaration",
    "Caveat",
    # Serializers — one per answer type, the same code the vectors assert
    "placement_contract",
    "unresolved_contract",
    "health_contract",
    "installation_contract",
    "installation_answers_contract",
    "catalogue_contract",
    "emulator_contract",
    "systems_contract",
    "firmware_contract",
    "identification_contract",
    # Vocabulary types
    "PathKind",
    "ReadStatus",
    "RootKind",
    "FileSetState",
    "FirmwareNeed",
    "FirmwareChecked",
    "CoreDeclarationState",
    "SystemSource",
    # Vocabulary values — path kinds and read statuses (answers carry both)
    "KIND_FILE",
    "KIND_DIRECTORY",
    "KIND_MISSING",
    "KIND_INACCESSIBLE",
    "READ_OK",
    "READ_MISSING",
    "READ_UNREADABLE",
    "READ_INVALID_TEXT",
    # Vocabulary values — emulator kinds, save roots, firmware axes
    "KIND_LIBRETRO",
    "KIND_STANDALONE",
    "ROOT_SAVEFILE_DIRECTORY",
    "ROOT_CONTENT_DIRECTORY",
    "ROOT_SYSTEM_DIRECTORY",
    "FILE_SET_OBSERVED",
    "FILE_SET_DECLARED",
    "FILE_SET_UNKNOWN",
    "NEED_REQUIRED",
    "NEED_OPTIONAL",
    "CHECKED_VERIFIED",
    "CHECKED_MISMATCH",
    "CHECKED_UNCHECKED",
    "CHECKED_UNKNOWN",
    "DECLARATION_READ",
    "DECLARATION_UNREADABLE",
    "DECLARATION_ABSENT",
    "DECLARATION_UNSUPPORTED",
    "SOURCE_OVERRIDE",
    "SOURCE_SYSTEMNAME",
    "SOURCE_SLUG",
    "SOURCE_NONE",
    # Vocabulary values — the holes a caller fills, and the closed sets the
    # contract serializes them beside. A client branches on `needs` and reads
    # `granularity.value` / `root_kind`, so by the tiering rule (a name a client
    # acts on lives in `atlas`) their vocabularies are consumer surface too;
    # without them the only way to branch was a hardcoded string.
    "HOLE_CONTENT_DIR",
    "HOLE_LIBRARY_NAME",
    "HOLE_SAVE_ID",
    "GRANULARITY_SHARED_CARD",
    "GRANULARITY_PER_GAME_FILE",
    "GRANULARITY_PER_GAME_FILES",
    "GRANULARITIES",
    "ROOT_KINDS",
    # Health finding codes
    "HEALTH_ISSUE_MARKER_MISSING",
    "HEALTH_ISSUE_MARKER_UNREADABLE",
    "HEALTH_ISSUE_MARKER_INVALID",
    "HEALTH_ISSUE_ROOT_MISSING",
    "HEALTH_ISSUE_SAVES_ROOT_MISSING",
    "HEALTH_ISSUE_CONFIG_UNREADABLE",
    "HEALTH_ISSUE_COMPANION_CONFIG_MISSING",
    # Typed outcome codes
    "UNRESOLVED_STANDALONE",
    # Caveat codes
    "CAVEAT_APP_RELATIVE_PATH_UNEXPANDED",
    "CAVEAT_ARRANGEMENT_UNVERIFIED",
    "CAVEAT_ARRANGEMENT_VERSION_DRIFTED",
    "CAVEAT_CARD_GENERATION_MISMATCH",
    "CAVEAT_CARD_MODE_UNCONFIRMED",
    "CAVEAT_CFG_LINE_DROPPED",
    "CAVEAT_CFG_VALUE_REJECTED",
    "CAVEAT_CONTENT_DIR_OBSERVATION",
    "CAVEAT_CONTENT_PATH_UNNAMED",
    "CAVEAT_CORE_DIR_UNRESOLVED",
    "CAVEAT_CORE_ENUMERATION_INCOMPLETE",
    "CAVEAT_CORE_INFO_UNREADABLE",
    "CAVEAT_CORE_MULTI_OPTION",
    "CAVEAT_CORE_NOT_INSTALLED",
    "CAVEAT_CORE_SUSPECT",
    "CAVEAT_CORE_UNAUDITED",
    "CAVEAT_CORE_UNQUERYABLE",
    "CAVEAT_CORE_WITHOUT_SYSTEMNAME",
    "CAVEAT_DEAD_SYMLINK",
    "CAVEAT_EMULATOR_CATALOGUE_UNAVAILABLE",
    "CAVEAT_EMULATOR_CATALOGUE_UNESTABLISHED",
    "CAVEAT_EMULATOR_CATALOGUE_UNREADABLE",
    "CAVEAT_FILENAMES_CONTENT_CONDITIONAL",
    "CAVEAT_FILENAMES_UNVERIFIED",
    "CAVEAT_FILE_SET_SPANS_ROOTS",
    "CAVEAT_FIRMWARE_CONTENT_CONTRADICTORY",
    "CAVEAT_FIRMWARE_CONTENT_UNIDENTIFIED",
    "CAVEAT_FIRMWARE_CONTENT_UNSTATED",
    "CAVEAT_FIRMWARE_DECLARATION_UNKNOWN",
    "CAVEAT_FIRMWARE_DECLARATION_UNREAD",
    "CAVEAT_FIRMWARE_PATH_ESCAPES_ROOT",
    "CAVEAT_FIRMWARE_PATH_INACCESSIBLE",
    "CAVEAT_FIRMWARE_PATH_NAMES_NO_FILE",
    "CAVEAT_FIRMWARE_PATH_OBSTRUCTED",
    "CAVEAT_FIRMWARE_PATH_UNRESOLVABLE",
    "CAVEAT_FIRMWARE_ROOT_MISSING",
    "CAVEAT_FIRMWARE_ROOT_UNUSABLE",
    "CAVEAT_FIRMWARE_SCAN_INCOMPLETE",
    "CAVEAT_FIRMWARE_UNREADABLE",
    "CAVEAT_INFO_PATH_UNRESOLVED",
    "CAVEAT_INVALID_SAVE_DIRECTORY",
    "CAVEAT_NO_CORE",
    "CAVEAT_NO_FIRMWARE_DECLARATION",
    "CAVEAT_NO_FIRMWARE_REQUIREMENT",
    "CAVEAT_PER_GAME_OVERRIDE",
    "CAVEAT_PER_GAME_OVERRIDES_PRESENT",
    "CAVEAT_SANDBOX_PATH_UNTRANSLATED",
    "CAVEAT_SAVE_DIR_UNLISTABLE",
    "CAVEAT_SORTED_DIR_MISSING",
    "CAVEAT_SORTED_DIR_UNCREATABLE",
    "CAVEAT_STANDALONE_UNSUPPORTED",
    "CAVEAT_SYMLINK_LOOP",
    "CAVEAT_SYSTEM_ASSIGNMENT_DERIVED",
    "CAVEAT_SYSTEM_ASSIGNMENT_MAY_HIDE_CORES",
    "CAVEAT_SYSTEM_DIRECTORY_CLEARED",
    "CAVEAT_SYSTEM_UNKNOWN",
    "CAVEAT_UNKNOWN_OPTION_VALUE",
    "CAVEAT_UNVERIFIED_VERSION",
]
