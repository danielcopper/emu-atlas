"""Tests for atlas.firmware — emulators, their firmware, and what is on disk.

The model is proven from data along its two axes: ``need`` is what an emulator
asks for, ``present``/``checked`` is what the machine answers, and the six
``checked`` values stay apart. Two classes carry the load. The first is
:class:`TestNoDeclarationIsNeverSatisfied`: having no declaration must never
look like nothing missing. The second is :class:`TestPartialReaderIsNotMisled`
— a caller that renders one field and ignores the rest may end up uninformed,
but never wrong.
"""

from __future__ import annotations

import ast
import functools
import hashlib
import json
import re
from dataclasses import replace
from pathlib import Path
from typing import Mapping
from xml.etree import ElementTree as ET

import pytest

from atlas.firmware import (
    CAVEAT_SYSTEM_ASSIGNMENT_MAY_HIDE_CORES,
    CAVEAT_SYSTEM_FIRMWARE_WORLD_KNOWLEDGE,
    CORE_DECLARATION_STATES,
    CORE_SYSTEM_FIRMWARE_STATES,
    DECLARATIONS_JUDGED,
    DECLARATIONS_UNJUDGED,
    SYSTEM_FIRMWARE_CANNOT_RUN_WITHOUT,
    SYSTEM_FIRMWARE_CORE_ALTERNATIVE,
    SYSTEM_FIRMWARE_OPEN,
    SYSTEM_FIRMWARE_RUNS_WITHOUT,
    system_firmware_system,
    CAVEAT_EMULATOR_CATALOGUE_SEALED,
    CAVEAT_EMULATOR_CATALOGUE_UNAVAILABLE,
    CAVEAT_EMULATOR_CATALOGUE_UNREADABLE,
    CAVEAT_FIRMWARE_CONFIGURED_IMAGE_MISSING,
    CAVEAT_FIRMWARE_CONTENT_CONTRADICTORY,
    CAVEAT_FIRMWARE_CONTENT_UNIDENTIFIED,
    CAVEAT_FIRMWARE_CONTENT_UNSTATED,
    CAVEAT_CORE_INFO_UNREADABLE,
    CAVEAT_CORE_NOT_INSTALLED,
    CAVEAT_CORE_WITHOUT_SYSTEMNAME,
    CAVEAT_FIRMWARE_DECLARATION_UNKNOWN,
    CAVEAT_FIRMWARE_DECLARATION_UNREAD,
    CAVEAT_FIRMWARE_DIRECTORY_HOLDS_NO_CANDIDATE,
    CAVEAT_FIRMWARE_DIRECTORY_HOLDS_NO_IMAGE,
    CAVEAT_FIRMWARE_IDENTITY_NOT_COMPARABLE,
    CAVEAT_FIRMWARE_IMAGE_CONFIGURED,
    CAVEAT_FIRMWARE_IMAGE_CONTRADICTED,
    CAVEAT_FIRMWARE_IMAGE_AMBIGUOUS,
    CAVEAT_FIRMWARE_IMAGE_IDENTIFIED,
    CAVEAT_FIRMWARE_IMAGE_REFUSED,
    CAVEAT_FIRMWARE_IMAGE_UNLISTED,
    CAVEAT_FIRMWARE_INSTALLER_DOWNLOAD,
    CAVEAT_FIRMWARE_NAME_SPELLINGS,
    CAVEAT_FIRMWARE_PATH_ESCAPES_ROOT,
    CAVEAT_FIRMWARE_PATH_INACCESSIBLE,
    CAVEAT_FIRMWARE_PATH_NAMES_NO_FILE,
    CAVEAT_FIRMWARE_PATH_NOT_A_DIRECTORY,
    CAVEAT_FIRMWARE_PATH_OBSTRUCTED,
    CAVEAT_FIRMWARE_PATH_UNRESOLVABLE,
    CAVEAT_FIRMWARE_ROOT_UNUSABLE,
    CAVEAT_FIRMWARE_SCAN_INCOMPLETE,
    CAVEAT_FIRMWARE_SEARCH_UNVERIFIED,
    CAVEAT_FIRMWARE_PACKAGED_DECLARATION,
    CAVEAT_FIRMWARE_UNREADABLE,
    CAVEAT_NO_FIRMWARE_DECLARATION,
    CAVEAT_NO_FIRMWARE_REQUIREMENT,
    CAVEAT_STANDALONE_UNSUPPORTED,
    CAVEAT_SYSTEM_ASSIGNMENT_DERIVED,
    CAVEAT_SYSTEM_NOT_IN_CATALOGUE,
    CAVEAT_SYSTEM_UNKNOWN,
    CHECKED_MISMATCH,
    CHECKED_NOT_COMPARABLE,
    CHECKED_REFUSED,
    CHECKED_UNCHECKED,
    CHECKED_UNREAD,
    CHECKED_UNKNOWN,
    CHECKED_UNRECOGNISED,
    CHECKED_VERIFIED,
    DECLARATION_ABSENT,
    DECLARATION_PACKAGED,
    DECLARATION_UNSUPPORTED,
    DECLARATION_READ,
    DECLARATION_UNREADABLE,
    DECLARED_DIRECTORY,
    DECLARED_FILE,
    FIRMWARE_DECLARED_DIRECTORY,
    FIRMWARE_DECLARED_DIRECTORY_VERSION,
    FIRMWARE_SYSTEM_OVERRIDE,
    NEED_OPTIONAL,
    NEED_REQUIRED,
    READER_PS2_BIOS_HEADER,
    SYSTEMNAME_MAP_VERSION,
    SYSTEMNAME_TO_SLUG,
    SYSTEMS_WITHOUT_CATALOGUE_ID,
    Catalogue,
    CatalogueEntry,
    Concern,
    CoreDeclarations,
    CoreFirmware,
    DeclaredDirectory,
    Destination,
    FirmwareAlternatives,
    FirmwareAnswer,
    FirmwareContext,
    FirmwareIdentity,
    FirmwareRequirement,
    InventoryCatalogue,
    RefusedDeclaration,
    RELATION_DECLARES,
    RELATION_RECOGNISES,
    SOURCE_OVERRIDE,
    SOURCE_SYSTEMNAME,
    SuppliedBy,
    UnclaimedFile,
    declared_directory_of,
    declared_kind_of,
    destination_under,
    firmware_for_core,
    firmware_for_system,
    firmware_inventory,
    identify_firmware,
    load_hashes,
    read_core_declarations,
    resolve_links,
    save_artifact_paths,
    stated_once,
    system_assignment_caveats,
    system_decision,
    system_for,
)
from atlas.core_firmware import lookup_core_firmware
from atlas.distribution_supplied import lookup_distribution_supplied
from atlas.core_options import CoreOptionsChain
from atlas.machine import (
    KIND_DIRECTORY,
    KIND_INACCESSIBLE,
    KIND_MISSING,
    AppImageReadResult,
    CoreInfo,
    CoreReading,
    FixtureFileSpec,
    FixtureMachine,
    GlobResult,
    PathKind,
    ArchiveListResult,
    Ps2BiosHeaderResult,
    ReadResult,
    WhdloadSlaveResult,
)
from atlas.placement import (
    CAVEAT_CFG_LINE_DROPPED,
    CAVEAT_CORE_MODE_UNESTABLISHED,
    CAVEAT_CORE_UNQUERYABLE,
    CAVEAT_UNKNOWN_OPTION_VALUE,
    REASON_REGION_DECIDED_BY_DISC,
    DataValue,
    CAVEAT_FIRMWARE_SEARCH_CANDIDATES,
    CAVEAT_PER_GAME_ALTERNATIVE_EMULATOR,
    CAVEAT_SYSTEM_DIRECTORY_CLEARED,
    READING_IDENTIFIED,
    READING_UNREADABLE,
    READING_UNRECOGNISED,
    Caveat,
)
from atlas.system_firmware import (
    EVIDENCE_DERIVED,
    EVIDENCE_LEVELS,
    EVIDENCE_OPEN,
    EVIDENCE_VERIFIED,
    EVIDENCE_WORD_DERIVED,
    EVIDENCE_WORD_OPEN,
    EVIDENCE_WORD_VERIFIED,
    EVIDENCE_WORDS,
    STATED_EVIDENCE_WORDS,
    SYSTEM_FIRMWARE_VERDICTS,
    VERDICT_CANNOT_RUN_WITHOUT,
    VERDICT_OPEN,
    VERDICT_RUNS_WITHOUT,
    SystemFirmware,
    load_system_firmware,
)
from atlas.systems import known_systems

import atlas.firmware
from atlas import duckstation

INFO_DIR = "/cores"
BIOS_DIR = "/bios"


def _plain_requirements(core: CoreFirmware) -> tuple[FirmwareRequirement, ...]:
    """A conjunctive core's entries, narrowed for attribute access.

    Every core these tests build through the .info route is a conjunction, so
    the narrowing drops nothing — and the assert holds it to that: a group
    appearing where a test expects plain entries must FAIL, not be silently
    skipped. Group-building tests read options explicitly.
    """
    assert all(isinstance(r, FirmwareRequirement) for r in core.requirements), (
        f"{core.core_so or core.label}: an alternatives group appeared in a core this test "
        "reads as a plain conjunction — read its options explicitly instead of filtering it away"
    )
    return tuple(r for r in core.requirements if isinstance(r, FirmwareRequirement))

PSX_INFO = """
display_name = "Sony - PlayStation (Demo)"
systemname = "Sony - PlayStation"
firmware_count = 2
firmware0_desc = "scph5501.bin (PS1 US BIOS)"
firmware0_path = "scph5501.bin"
firmware0_opt = "false"
firmware1_desc = "psxonpsp660.bin (PSP PS1 BIOS)"
firmware1_path = "psxonpsp660.bin"
firmware1_opt = "true"
"""

DC_INFO = """
systemname = "Sega - Dreamcast"
firmware_count = 1
firmware0_desc = "dc_boot.bin (Dreamcast BIOS)"
firmware0_path = "dc/dc_boot.bin"
firmware0_opt = "false"
"""

# LRPS2 declares a FOLDER rather than a file, and RetroDECK links that folder
# back to the firmware root — so this declaration lands on the root itself.
LRPS2_FOLDER_INFO = """
systemname = "Sony PlayStation 2"
firmware_count = 1
firmware0_desc = "pcsx2/bios (PS2 BIOS directory)"
firmware0_path = "pcsx2/bios"
firmware0_opt = "false"
"""

# A second core declaring one of the images LRPS2 accepts by NAME, so one
# content is wanted at a file destination and a folder destination at once. The
# table covers the name, so this half is matched identity to identity.
PS2_IMAGE_BY_NAME_INFO = """
systemname = "Sony PlayStation 2"
firmware_count = 1
firmware0_desc = "ps2-0200e-20040614.bin (PS2 BIOS)"
firmware0_path = "pcsx2/bios/ps2-0200e-20040614.bin"
firmware0_opt = "false"
"""

# The same PSX file, declared as optional by a second core.
PSX_SECOND_INFO = """
systemname = "PlayStation"
firmware_count = 1
firmware0_desc = "scph5501.bin"
firmware0_path = "scph5501.bin"
firmware0_opt = "true"
"""

TEMPLATE_INFO = """
display_name = "Example"
systemname = "Example"
firmware_count = 1
firmware0_desc = "filename.ext (description)"
firmware0_path = "filename.ext"
firmware0_opt = "true/false"
"""

NO_FIRMWARE_INFO = """
display_name = "Nintendo - SNES (Snes9x)"
systemname = "Super Nintendo Entertainment System"
"""

GAMBATTE_INFO = """
systemname = "Nintendo - Game Boy"
firmware_count = 1
firmware0_desc = "gb_bios.bin"
firmware0_path = "gb_bios.bin"
firmware0_opt = "true"
"""

SAMEBOY_INFO = """
systemname = "Nintendo - Game Boy"
firmware_count = 1
firmware0_desc = "dmg_boot.bin"
firmware0_path = "dmg_boot.bin"
firmware0_opt = "true"
"""

# A multi-system core: one systemname, a database naming two systems, and two
# declared files that a per-file rule sends to two different systems.
MGBA_INFO = """
systemname = "Game Boy/Game Boy Color/Game Boy Advance"
database = "Nintendo - Game Boy|Nintendo - Game Boy Advance"
firmware_count = 2
firmware0_desc = "gb_bios.bin"
firmware0_path = "gb_bios.bin"
firmware0_opt = "true"
firmware1_desc = "gba_bios.bin"
firmware1_path = "gba_bios.bin"
firmware1_opt = "true"
"""

# NooDS, the core the derived assignment is still about: RetroDECK offers it as
# a GBA emulator while its .info can only say "Nintendo DS" for the whole core.
# One declaration of each kind — gba_bios.bin carries a per-file rule and is
# filed by it, against that systemname; firmware.bin carries none and falls
# back to it.
NOODS_INFO = """
systemname = "Nintendo DS"
database = "Nintendo - Nintendo DS|Nintendo - Nintendo DS (Download Play)"
firmware_count = 2
firmware0_desc = "gba_bios.bin"
firmware0_path = "gba_bios.bin"
firmware0_opt = "true"
firmware1_desc = "firmware.bin"
firmware1_path = "firmware.bin"
firmware1_opt = "true"
"""

# A multi-system core whose every declaration carries a per-file rule.
FULLY_OVERRIDDEN_INFO = """
systemname = "Game Boy/Game Boy Color"
database = "Nintendo - Game Boy|Nintendo - Game Boy Color"
firmware_count = 2
firmware0_desc = "gb_bios.bin"
firmware0_path = "gb_bios.bin"
firmware0_opt = "true"
firmware1_desc = "gbc_bios.bin"
firmware1_path = "gbc_bios.bin"
firmware1_opt = "true"
"""

# atari800: its database names the 5200 while its systemname does not, which is
# exactly how a core ends up unreachable under the right slug.
ATARI800_INFO = """
systemname = "Atari 8-bit Family"
database = "Atari - 5200|Atari - 8-bit Family"
firmware_count = 1
firmware0_desc = "ATARIXL.ROM"
firmware0_path = "ATARIXL.ROM"
firmware0_opt = "true"
"""

# SkyEmu ships no systemname at all — only a database naming three systems.
SKYEMU_INFO = """
display_name = "Multi (SkyEmu)"
database = "Nintendo - Nintendo DS|Nintendo - Game Boy|Nintendo - Game Boy Advance"
firmware_count = 3
firmware0_desc = "cgb_boot.bin"
firmware0_path = "cgb_boot.bin"
firmware0_opt = "true"
firmware1_desc = "gba_bios.bin"
firmware1_path = "gba_bios.bin"
firmware1_opt = "true"
firmware2_desc = "nds7.bin"
firmware2_path = "nds7.bin"
firmware2_opt = "true"
"""

# ECWolf declares one required file and it is a data pack shipped with the core
# — the arrangement that first showed a `mismatch` meaning nothing.
ECWOLF_INFO = """
display_name = "Wolfenstein 3D (ECWolf)"
systemname = "Wolfenstein 3D Game Engine"
firmware_count = 1
firmware0_desc = "ecwolf.pk3 (ECWolf System File)"
firmware0_path = "ecwolf.pk3"
"""

TABLE = json.dumps(
    {
        "_meta": {"generated_from": "test", "version": "0", "generated_at": "2026-01-01"},
        "files": {
            "scph5501.bin": {"md5": "aa" * 16, "sha1": "bb" * 20, "size": 8, "kind": "file"},
            "dc/dc_boot.bin": {"md5": "cc" * 16, "sha1": "dd" * 20, "size": 4, "kind": "file"},
            # One content, two canonical names — the gambatte/SameBoy case.
            "gb_bios.bin": {"md5": "ee" * 16, "sha1": "ff" * 20, "size": 5, "kind": "file"},
            "dmg_boot.bin": {"md5": "ee" * 16, "sha1": "ff" * 20, "size": 5, "kind": "file"},
            # A per-file rule covers this one, so it is filed as gba whatever
            # the declaring core is called — the override case, reachable by
            # content.
            "gba_bios.bin": {"md5": "11" * 16, "sha1": "22" * 20, "size": 6, "kind": "file"},
            # No per-file rule covers this one and none is going to: the name is
            # too generic to claim for one system, so a core declaring it is
            # filed by its systemname — the derived case, reachable by content.
            "firmware.bin": {"md5": "a1" * 16, "sha1": "b2" * 20, "size": 9, "kind": "file"},
            # Two of the images LRPS2 accepts inside the folder it lists, filed
            # under the prefix its curated row names — the sizes are the real
            # ones, because the row's size filter is what admits them.
            "pcsx2/bios/ps2-0200e-20040614.bin": {"md5": "77" * 16, "sha1": "88" * 20, "size": 4194304, "kind": "file"},
            "pcsx2/bios/ps2-0160a-20010427.bin": {"md5": "5a" * 16, "sha1": "6b" * 20, "size": 4194304, "kind": "file"},
        },
    }
)

# The two PS2 images above, as fixture files: identity is by content, so the
# file name a test gives them is deliberately not the table's.
PS2_IMAGE_EUR: dict[str, str | int] = {"md5": "77" * 16, "sha1": "88" * 20, "size": 4194304}
PS2_IMAGE_USA: dict[str, str | int] = {"md5": "5a" * 16, "sha1": "6b" * 20, "size": 4194304}
PS2_IMAGE_SIZE = 4194304
LRPS2_SO = "pcsx2_libretro.so"
LRPS2_FOLDER = f"{BIOS_DIR}/pcsx2/bios"
# A file of a size LRPS2 keeps whose bytes the test table does not list.
PS2_UNLISTED: dict[str, str | int] = {"md5": "00" * 16, "sha1": "00" * 20, "size": PS2_IMAGE_SIZE}
# The two strings the ROMDIR walk yields for the two packaged images the test
# table names — version strings spelling what the table's own file names spell
# (ps2-0200e-20040614.bin: v02.00, Europe, 2004-06-14) — and the fields the
# core's format builds from the first.
PS2_HEADER_EUR = {"romver": "0200EC20040614", "serial": "fixture-eu"}
PS2_HEADER_USA = {"romver": "0160AC20010427", "serial": "fixture-us"}
PS2_FIELDS_EUR = {
    "description": "Europe  v02.00(14/06/2004)  Console fixture-eu",
    "zone": "Europe",
    "version": "02.00",
    "date": "2004-06-14",
    "serial": "fixture-eu",
}
NOT_A_BIOS = "not-a-bios"


def _blob(content: bytes) -> dict[str, str | int]:
    return {
        "md5": hashlib.md5(content).hexdigest(),
        "sha1": hashlib.sha1(content).hexdigest(),
        "size": len(content),
    }


def _entry(content: bytes, **stamp: str) -> dict[str, str | int]:
    """*content* as one packaged table entry — the blob plus what kind it is."""
    return {**_blob(content), "kind": "file", **stamp}


DEMO_PSX_SO = "demo_psx_libretro.so"
"""The PlayStation core the generic tests declare, of no particular make.

The stem is invented, and that is what it is for: it is the key of no packaged
table — not ``core_firmware.json``, ``core_audit.json``, ``core_oddities.json``
or ``save_memory.json`` — so a test about a destination, a ``checked`` value or
an unclaimed file reads the ``.info`` route and no route a core's NAME reaches.
What it does not escape is knowledge keyed by SYSTEM: this declaration states a
PlayStation, so ``system_firmware.json`` still answers
``cannot-run-without-firmware`` for it and the world-knowledge caveat still
rides. A core whose own firmware route atlas has read answers more than its
declaration, and a test that wants that names the core it wants
(:data:`BEETLE_PSX_SO`).
"""


def _machine(files: Mapping[str, FixtureFileSpec] | None = None, **kwargs: object) -> FixtureMachine:
    # `core` names the .so this fixture declares, and it is read out of the
    # keywords rather than taken as one of its own: every caller here forwards
    # an untyped **kwargs into this function, and a typed keyword beside that
    # is a type error at each of those call sites.
    core = kwargs.pop("core", DEMO_PSX_SO)
    assert isinstance(core, str)
    tree: dict[str, FixtureFileSpec] = {
        f"{INFO_DIR}/{core[: -len('.so')]}.info": PSX_INFO,
        f"{INFO_DIR}/{core}": {"status": "invalid-text"},
    }
    if files is not None:
        tree.update(files)
    return FixtureMachine(tree, **kwargs)  # type: ignore[arg-type]


@functools.cache
def _shipped_system_firmware() -> Mapping[str, SystemFirmware]:
    """The packaged table, parsed once for the whole suite."""
    return load_system_firmware()


def _context(
    machine: FixtureMachine,
    *,
    root: str | None = BIOS_DIR,
    core_dir: str | None = INFO_DIR,
    system_firmware: Mapping[str, SystemFirmware] | None = None,
    core_options: CoreOptionsChain | None = None,
):
    # `system_firmware` defaults to the shipped table, the way `hashes` would
    # if it had a default: a test names one only to ask what an answer does
    # with a verdict the shipped table does not carry.
    return FirmwareContext(
        root=root,
        cores=read_core_declarations(machine, INFO_DIR, core_dir=core_dir).cores,
        hashes=load_hashes(TABLE),
        system_firmware=_shipped_system_firmware() if system_firmware is None else system_firmware,
        core_options=core_options,
    )


class TestHashTable:
    """The packaged table: names in, identities out — and content in, names out."""

    def test_for_path_matches_the_declared_path_first(self):
        hashes = load_hashes(TABLE)
        identity = hashes.for_path("dc/dc_boot.bin")
        assert identity is not None
        assert identity.md5 == "cc" * 16

    def test_for_path_falls_back_to_the_base_name(self):
        # A core declaring "subdir/scph5501.bin" still names the same dump.
        identity = load_hashes(TABLE).for_path("psx/scph5501.bin")
        assert identity is not None
        assert identity.size == 8

    def test_an_uncovered_name_is_a_normal_none(self):
        assert load_hashes(TABLE).for_path("neogeo.zip") is None

    def test_known_as_carries_every_name_for_one_content(self):
        identity = load_hashes(TABLE).for_path("gb_bios.bin")
        assert identity is not None
        assert identity.known_as == ("dmg_boot.bin", "gb_bios.bin")

    def test_for_content_matches_by_bytes_not_by_name(self):
        identity = load_hashes(TABLE).for_content(md5="EE" * 16)
        assert identity is not None
        assert identity.known_as == ("dmg_boot.bin", "gb_bios.bin")

    def test_for_content_requires_every_supplied_field_to_agree(self):
        hashes = load_hashes(TABLE)
        assert hashes.for_content(md5="ee" * 16, size=5) is not None
        assert hashes.for_content(md5="ee" * 16, size=6) is None
        assert hashes.for_content(md5="ee" * 16, sha1="00" * 20) is None

    def test_size_alone_is_not_an_identity(self):
        # The table is a lookup, not an answer: asked for content it was never
        # told, it refuses rather than returning the "no match" that would
        # collapse "you named nothing" into "nothing matches". The public
        # question route answers instead — see TestIdentification.
        hashes = load_hashes(TABLE)
        with pytest.raises(ValueError):
            hashes.for_content(size=5)

    def test_a_known_digest_the_rest_disagrees_with_is_a_contradiction(self):
        # The M22 case: the table knows this md5 perfectly and the caller's own
        # size is what disagrees, so the request contradicts itself — reporting
        # the content as unknown would send them to the table.
        hashes = load_hashes(TABLE)
        assert hashes.for_content(md5="ee" * 16, size=999) is None
        assert hashes.contradicts_itself(md5="ee" * 16, size=999) is True
        assert hashes.contradicts_itself(md5="ee" * 16, sha1="bb" * 20) is True
        assert hashes.contradicts_itself(sha1="ff" * 20, size=999) is True

    def test_unknown_digests_are_unknown_content_not_a_contradiction(self):
        hashes = load_hashes(TABLE)
        assert hashes.contradicts_itself(md5="99" * 16, sha1="99" * 20) is False
        # A size the table happens to carry says nothing about an md5 it does
        # not know: that is content it has never seen, not a bad request.
        assert hashes.contradicts_itself(md5="99" * 16, size=5) is False

    def test_one_field_alone_can_never_contradict_itself(self):
        hashes = load_hashes(TABLE)
        assert hashes.contradicts_itself(md5="ee" * 16) is False
        assert hashes.contradicts_itself(size=5) is False

    def test_the_packaged_table_loads_and_is_not_empty(self):
        packaged = load_hashes()
        assert packaged.names()
        assert packaged.meta["generated_from"]


class TestSystemSlug:
    """What a declaration belongs to — override, map, or a mechanical slug."""

    def test_the_per_file_override_wins_over_the_core_systemname(self):
        assert system_for("gb_bios.bin", "Game Boy/Game Boy Color/Game Boy Advance") == "gb"

    def test_a_known_systemname_maps(self):
        assert system_for("dc/dc_boot.bin", "Sega - Dreamcast") == "dreamcast"

    def test_an_unknown_systemname_is_slugified_not_dropped(self):
        assert system_for("weird.bin", "Some New Machine") == "some-new-machine"

    def test_an_empty_systemname_is_the_catch_all(self):
        assert system_for("weird.bin", "") == "_unknown"


class TestReadDeclarations:
    """The live read: every installed core, firmware or not."""

    def test_a_core_without_firmware_is_still_read(self):
        machine = _machine({f"{INFO_DIR}/snes9x_libretro.info": NO_FIRMWARE_INFO,
                            f"{INFO_DIR}/snes9x_libretro.so": {"status": "invalid-text"}})
        cores = {c.core_so: c for c in read_core_declarations(machine, INFO_DIR, core_dir=INFO_DIR).cores}
        assert cores["snes9x_libretro.so"].firmware == ()
        assert cores["snes9x_libretro.so"].system == "snes"

    def test_a_core_without_its_so_is_not_installed(self):
        machine = _machine({f"{INFO_DIR}/flycast_libretro.info": DC_INFO})
        cores = read_core_declarations(machine, INFO_DIR, core_dir=INFO_DIR).cores
        assert [c.core_so for c in cores] == ["demo_psx_libretro.so"]

    def test_without_a_core_dir_nothing_is_filtered(self):
        machine = _machine({f"{INFO_DIR}/flycast_libretro.info": DC_INFO})
        cores = read_core_declarations(machine, INFO_DIR).cores
        assert {c.core_so for c in cores} == {"demo_psx_libretro.so", "flycast_libretro.so"}

    def test_the_template_info_files_are_dropped(self):
        machine = _machine({f"{INFO_DIR}/00_example_libretro.info": TEMPLATE_INFO,
                            f"{INFO_DIR}/00_example_libretro.so": {"status": "invalid-text"}})
        cores = read_core_declarations(machine, INFO_DIR, core_dir=INFO_DIR).cores
        assert all("filename.ext" not in d.path for c in cores for d in c.firmware)

    def test_a_missing_opt_flag_means_required(self):
        machine = _machine()
        core = read_core_declarations(machine, INFO_DIR, core_dir=INFO_DIR).cores[0]
        needs = {d.file_name: d.need for d in core.firmware}
        assert needs == {"scph5501.bin": "required", "psxonpsp660.bin": "optional"}


class TestTheCountIsTheEnumeration:
    """``firmware_count`` bounds what a core asks for — it is not a cross-check.

    ``core_info_resolve_firmware`` returns before reading anything when the
    count is missing and otherwise fills slots ``0 .. count-1`` only
    (core_info.c:1572-1629), so a path outside that is a file RetroArch never
    asks for. What atlas adds is that it says so.
    """

    STEM = "counted_libretro"
    HEAD = 'display_name = "Counted"\nsystemname = "Nintendo - Game Boy"\n'

    def _machine_for(self, info: str) -> FixtureMachine:
        return _machine(
            {
                f"{INFO_DIR}/{self.STEM}.info": self.HEAD + info,
                f"{INFO_DIR}/{self.STEM}.so": {"status": "invalid-text"},
            }
        )

    def _core(self, info: str) -> CoreDeclarations:
        cores = read_core_declarations(self._machine_for(info), INFO_DIR, core_dir=INFO_DIR).cores
        return next(c for c in cores if c.stem == self.STEM)

    def _answered(self, info: str) -> CoreFirmware:
        machine = self._machine_for(info)
        answer = firmware_for_core(machine, _context(machine), core_so=f"{self.STEM}.so")
        return answer.cores[0]

    def test_without_a_count_the_core_asks_for_nothing(self):
        core = self._core('firmware0_path = "a.bin"\nfirmware1_path = "b.bin"\n')
        assert core.firmware == ()
        assert core.unread == ("firmware0_path", "firmware1_path")

    def test_a_declaration_past_the_count_is_not_a_requirement(self):
        core = self._core("firmware_count = 1\n" + 'firmware0_path = "a.bin"\nfirmware1_path = "b.bin"\n')
        assert [d.path for d in core.firmware] == ["a.bin"]
        assert core.unread == ("firmware1_path",)

    def test_a_slot_the_count_covers_but_the_file_skips_declares_nothing(self):
        core = self._core('firmware_count = 2\nfirmware0_path = "a.bin"\n')
        assert [d.path for d in core.firmware] == ["a.bin"]
        assert core.unread == ()

    def test_a_repeated_declaration_is_read_as_the_first_one(self):
        core = self._core('firmware_count = 2\nfirmware0_path = "exec.bin"\nfirmware0_path = "grom.bin"\n')
        assert [d.path for d in core.firmware] == ["exec.bin"]

    def test_an_opt_outside_the_boolean_vocabulary_is_required(self):
        core = self._core('firmware_count = 1\nfirmware0_path = "a.bin"\nfirmware0_opt = "TRUE"\n')
        assert [d.need for d in core.firmware] == ["required"]

    def test_the_answer_states_what_the_enumeration_left_out(self):
        core = self._answered("firmware_count = 1\n" + 'firmware0_path = "a.bin"\nfirmware1_path = "b.bin"\n')
        assert [c.code for c in core.caveats] == [CAVEAT_FIRMWARE_DECLARATION_UNREAD]
        assert core.caveats[0].data == {
            "core_so": f"{self.STEM}.so",
            "declared": ("firmware1_path",),
            "firmware_count": "1",
        }

    def test_a_core_whose_whole_list_is_unread_is_not_silently_satisfied(self):
        core = self._answered('firmware0_path = "a.bin"\n')
        assert core.requirements == ()
        assert [c.code for c in core.caveats] == [CAVEAT_FIRMWARE_DECLARATION_UNREAD]
        assert core.caveats[0].data["firmware_count"] == ""

    def test_a_core_that_declares_nothing_states_nothing(self):
        core = self._answered("firmware_count = 0\n")
        assert core.requirements == ()
        assert core.caveats == ()

    def test_a_letter_where_the_index_belongs_is_not_silently_dropped(self):
        # snprintf("%u_") cannot write 'A_' (core_info.c:1599), so nothing ever
        # looks this key up — and an answer that just left it out would read as
        # a core whose .info asks for nothing.
        core = self._answered('firmware_count = 1\nfirmwareA_path = "needed.bin"\n')
        assert core.requirements == ()
        assert core.requirements_met is True
        assert [c.code for c in core.caveats] == [CAVEAT_FIRMWARE_DECLARATION_UNREAD]
        assert core.caveats[0].data == {
            "core_so": f"{self.STEM}.so",
            "declared": ("firmwareA_path",),
            "firmware_count": "1",
        }

    def test_a_path_key_with_no_index_is_not_silently_dropped(self):
        core = self._answered('firmware_count = 1\nfirmware_path = "needed.bin"\n')
        assert core.requirements == ()
        assert core.requirements_met is True
        assert [c.code for c in core.caveats] == [CAVEAT_FIRMWARE_DECLARATION_UNREAD]
        assert core.caveats[0].data["declared"] == ("firmware_path",)

    def test_an_empty_path_inside_the_count_is_not_silently_dropped(self):
        # The one shape RetroArch does look up: it finds the entry, sees an
        # empty value and keeps the NULL (core_info.c:1610). The slot is empty
        # either way; that the file states the key is what gets said.
        core = self._answered('firmware_count = 2\nfirmware0_path = "a.bin"\nfirmware1_path = ""\n')
        assert [r.declared for r in _plain_requirements(core)] == ["a.bin"]
        assert [c.code for c in core.caveats] == [CAVEAT_FIRMWARE_DECLARATION_UNREAD]
        assert core.caveats[0].data["declared"] == ("firmware1_path",)

    def test_a_checksum_key_is_not_a_declaration_nobody_reads(self):
        # holani_libretro.info ships firmware0_md5 — a key RetroArch reads
        # nowhere, but it names no file, so it is not a hidden declaration and
        # a well-formed core must stay silent.
        core = self._answered('firmware_count = 1\nfirmware0_path = "a.bin"\nfirmware0_md5 = "d41d8c"\n')
        assert [r.declared for r in _plain_requirements(core)] == ["a.bin"]
        assert core.caveats == ()

    def test_a_file_wrong_in_several_ways_states_a_reason_for_each(self):
        # One reason standing for three would name a cause the reader cannot
        # find in their file, so each group is named with the keys it explains.
        core = self._answered(
            'firmware_count = 1\nfirmware_path = "un.bin"\nfirmware1_path = "past.bin"\nfirmware0_path = ""\n'
        )
        (caveat,) = core.caveats
        assert caveat.data["declared"] == ("firmware0_path", "firmware1_path", "firmware_path")
        assert "firmware_path (no key it composes is spelled that way)" in caveat.message
        assert "firmware1_path (its firmware_count is 1, so it reads firmware0_ up to firmware0_" in caveat.message
        assert "firmware0_path (an empty value, which the read that finds it discards)" in caveat.message


class TestPerCoreAnswer:
    """Criterion 1: does this core need firmware, and where does each file go?"""

    def test_the_destination_is_absolute_whether_or_not_a_file_is_there(self):
        answer = firmware_for_core(_machine(), _context(_machine()), core_so="demo_psx_libretro.so")
        paths = {r.file_name: r.path for r in answer.requirements}
        assert paths == {
            "scph5501.bin": f"{BIOS_DIR}/scph5501.bin",
            "psxonpsp660.bin": f"{BIOS_DIR}/psxonpsp660.bin",
        }
        assert all(not r.present for r in answer.requirements)

    def test_a_subdirectory_is_part_of_the_destination(self):
        machine = _machine({f"{INFO_DIR}/flycast_libretro.info": DC_INFO,
                            f"{INFO_DIR}/flycast_libretro.so": {"status": "invalid-text"}})
        answer = firmware_for_core(machine, _context(machine), core_so="flycast_libretro.so")
        assert [r.path for r in answer.requirements] == [f"{BIOS_DIR}/dc/dc_boot.bin"]

    @pytest.mark.parametrize("given", ["demo_psx_libretro.so", "demo_psx_libretro", "/x/y/demo_psx_libretro.so"])
    def test_a_core_is_named_by_so_name_stem_or_path(self, given: str):
        machine = _machine()
        answer = firmware_for_core(machine, _context(machine), core_so=given)
        assert [c.declaration for c in answer.cores] == ["read"]

    def test_an_installed_core_declaring_nothing_needs_nothing(self):
        machine = _machine({f"{INFO_DIR}/snes9x_libretro.info": NO_FIRMWARE_INFO,
                            f"{INFO_DIR}/snes9x_libretro.so": {"status": "invalid-text"}})
        answer = firmware_for_core(machine, _context(machine), core_so="snes9x_libretro.so")
        core = answer.cores[0]
        assert core.declaration == DECLARATION_READ
        assert core.requirements == ()
        assert core.requirements_met is True
        assert [c.code for c in answer.caveats] == []

    def test_requirements_met_counts_only_required_files(self):
        # Verified, so the answer can be earned: the optional file is absent and
        # that alone must not make the core fail.
        content = b"12345678"
        table = json.dumps({"_meta": {}, "files": {"scph5501.bin": _entry(content)}})
        machine = _machine({f"{BIOS_DIR}/scph5501.bin": _blob(content)})
        context = FirmwareContext(
            root=BIOS_DIR,
            cores=read_core_declarations(machine, INFO_DIR, core_dir=INFO_DIR).cores,
            hashes=load_hashes(table),
        )
        core = firmware_for_core(
            machine, context, core_so="demo_psx_libretro.so", verify=True
        ).cores[0]
        assert [r.file_name for r in core.unmet] == []
        assert core.requirements_met is True

    def test_an_unverified_known_identity_is_not_an_all_clear(self):
        """verify=False must not hand out a green light it did not earn.

        The file is there under the right name and the table knows what it
        should be — but nobody looked, so the honest answer is "undetermined",
        not "met". A caller who wants the green light asks for it; on the
        reference machine that costs 0.03 s for one core.
        """
        machine = _machine({f"{BIOS_DIR}/scph5501.bin": _blob(b"12345678")})
        core = firmware_for_core(machine, _context(machine), core_so="demo_psx_libretro.so").cores[0]
        required = next(r for r in _plain_requirements(core) if r.need == NEED_REQUIRED)
        assert required.found == "file"
        assert required.checked == "unchecked"
        assert required.satisfied is None
        assert core.unmet == ()
        assert [r.file_name for r in core.undetermined] == ["scph5501.bin"]
        assert core.requirements_met is None

    def test_a_file_no_table_covers_stays_settled(self):
        # Nothing further can EVER be established about it, so withholding the
        # answer would withhold it forever.
        machine = _machine({f"{BIOS_DIR}/psxonpsp660.bin": _blob(b"unknown to the table")})
        answer = firmware_for_core(machine, _context(machine), core_so="demo_psx_libretro.so")
        uncovered = next(r for r in answer.requirements if r.file_name == "psxonpsp660.bin")
        assert uncovered.identity is None
        assert uncovered.checked == CHECKED_UNKNOWN
        assert uncovered.satisfied is True

    def test_a_missing_required_file_is_unmet(self):
        machine = _machine()
        core = firmware_for_core(machine, _context(machine), core_so="demo_psx_libretro.so").cores[0]
        assert [r.file_name for r in core.unmet] == ["scph5501.bin"]
        assert core.requirements_met is False


class TestCheckedAxis:
    """The values of ``checked`` — and that none of them collapse.

    ``not-comparable`` has its own class below: it is the only one that
    depends on what kind of identity the table carries.
    """

    def test_nothing_there_means_nothing_to_check(self):
        machine = _machine()
        requirement = _by_name(firmware_for_core(machine, _context(machine), core_so="demo_psx_libretro.so"))
        assert requirement["scph5501.bin"].present is False
        assert requirement["scph5501.bin"].checked is None

    def test_a_known_identity_not_asked_about_is_unchecked(self):
        machine = _machine({f"{BIOS_DIR}/scph5501.bin": _blob(b"12345678")})
        requirement = _by_name(firmware_for_core(machine, _context(machine), core_so="demo_psx_libretro.so"))
        assert requirement["scph5501.bin"].checked == "unchecked"

    def test_an_unknown_identity_is_unknown_even_when_asked_about(self):
        # psxonpsp660.bin is not in this table: no amount of verifying can
        # establish what it is, which is a different answer from "not checked".
        machine = _machine({f"{BIOS_DIR}/psxonpsp660.bin": _blob(b"whatever")})
        answer = firmware_for_core(
            machine, _context(machine), core_so="demo_psx_libretro.so", verify=True
        )
        assert _by_name(answer)["psxonpsp660.bin"].checked == "unknown"

    def test_matching_bytes_verify(self):
        content = b"12345678"
        table = json.dumps(
            {"_meta": {}, "files": {"scph5501.bin": {**_entry(content), "md5": hashlib.md5(content).hexdigest()}}}
        )
        machine = _machine({f"{BIOS_DIR}/scph5501.bin": _blob(content)})
        context = FirmwareContext(
            root=BIOS_DIR,
            cores=read_core_declarations(machine, INFO_DIR, core_dir=INFO_DIR).cores,
            hashes=load_hashes(table),
        )
        answer = firmware_for_core(machine, context, core_so="demo_psx_libretro.so", verify=True)
        assert _by_name(answer)["scph5501.bin"].checked == "verified"

    def test_the_right_size_with_the_wrong_bytes_is_a_mismatch(self):
        # Size passes the free pre-filter, the digest does not.
        machine = _machine({f"{BIOS_DIR}/scph5501.bin": {"md5": "00" * 16, "sha1": "00" * 20, "size": 8}})
        answer = firmware_for_core(
            machine, _context(machine), core_so="demo_psx_libretro.so", verify=True
        )
        assert _by_name(answer)["scph5501.bin"].checked == "mismatch"

    def test_a_wrong_size_settles_it_without_reading_the_file(self):
        machine = _machine({f"{BIOS_DIR}/scph5501.bin": {"size": 9}})
        answer = firmware_for_core(
            machine, _context(machine), core_so="demo_psx_libretro.so", verify=True
        )
        assert _by_name(answer)["scph5501.bin"].checked == "mismatch"

    def test_a_present_but_unreadable_file_is_unread_and_says_so(self):
        # The bytes were asked for and did not come back, which is a statement
        # about atlas's read. It kept the identity its table pins for the
        # declared name: that was known before any byte was read.
        machine = _machine({f"{BIOS_DIR}/scph5501.bin": {"size": 8}})
        answer = firmware_for_core(
            machine, _context(machine), core_so="demo_psx_libretro.so", verify=True
        )
        requirement = _by_name(answer)["scph5501.bin"]
        assert requirement.checked == CHECKED_UNREAD
        assert requirement.satisfied is None
        assert requirement.identity is not None
        assert CAVEAT_FIRMWARE_UNREADABLE in [c.code for c in answer.caveats]

    def test_unread_stands_with_an_identity_and_without_one(self):
        # The one value on both sides of the identity question, and the pair
        # says which kind of table covers the entry: keyed by the declared
        # name, or keyed by content.
        for identity in (FirmwareIdentity(md5="0" * 32, sha1="1" * 40, size=8, kind="file"), None):
            requirement = FirmwareRequirement(
                core_so="x.so", system="psx", system_source="systemname", need="required",
                file_name="a.bin", path="/bios/a.bin", declared="a.bin", description="",
                identity=identity, found="file", checked=CHECKED_UNREAD,
            )
            assert requirement.satisfied is None

    def test_hash_checked_records_whether_verification_ran(self):
        machine = _machine()
        context = _context(machine)
        assert firmware_for_core(machine, context, core_so="demo_psx_libretro.so").hash_checked is False
        assert (
            firmware_for_core(machine, context, core_so="demo_psx_libretro.so", verify=True).hash_checked
            is True
        )


ARCHIVE_MD5 = "ab" * 16
ARCHIVE_SHA1 = "cd" * 20
ARCHIVE_SIZE = 64


ARCHIVE_LIST_VERSION = "7"


def _archive_table(reason: str = "core-bundled", *, list_version: str | None = ARCHIVE_LIST_VERSION) -> str:
    meta = {} if list_version is None else {"archive_identities_version": list_version}
    return json.dumps(
        {
            "_meta": meta,
            "files": {
                "ecwolf.pk3": {
                    "md5": ARCHIVE_MD5,
                    "sha1": ARCHIVE_SHA1,
                    "size": ARCHIVE_SIZE,
                    "kind": "archive",
                    "archive_reason": reason,
                }
            },
        }
    )


def _archive_answer(
    pk3: FixtureFileSpec,
    *,
    verify: bool = True,
    reason: str = "core-bundled",
    list_version: str | None = ARCHIVE_LIST_VERSION,
):
    """The ECWolf machine with *pk3* at the destination, asked about its one file."""
    machine = FixtureMachine(
        {
            f"{INFO_DIR}/ecwolf_libretro.info": ECWOLF_INFO,
            f"{INFO_DIR}/ecwolf_libretro.so": {"status": "invalid-text"},
            f"{BIOS_DIR}/ecwolf.pk3": pk3,
        }  # type: ignore[arg-type]
    )
    context = FirmwareContext(
        root=BIOS_DIR,
        cores=read_core_declarations(machine, INFO_DIR, core_dir=INFO_DIR).cores,
        hashes=load_hashes(_archive_table(reason, list_version=list_version)),
    )
    return firmware_for_core(machine, context, core_so="ecwolf_libretro.so", verify=verify)


class TestArchiveIdentitiesWithhold:
    """The fifth value: an archive's bytes differ, and that is not a verdict.

    A whole-file hash over a MAME romset or a core's data pack pins one
    packaging of one version, so a difference from it is the ordinary state of
    a correct file — the ``ecwolf.pk3`` sighting that gave a present, right
    file the word ``mismatch``. The answer withholds instead, and says why.
    """

    def test_bytes_that_differ_answer_not_comparable(self):
        answer = _archive_answer({"md5": "00" * 16, "sha1": "00" * 20, "size": ARCHIVE_SIZE})
        assert _by_name(answer)["ecwolf.pk3"].checked == CHECKED_NOT_COMPARABLE

    def test_a_wrong_size_reaches_the_same_answer_without_a_digest(self):
        # The free pre-filter is the other way into the branch, and it must not
        # answer 'mismatch' on the way.
        answer = _archive_answer({"md5": ARCHIVE_MD5, "sha1": ARCHIVE_SHA1, "size": ARCHIVE_SIZE + 1})
        assert _by_name(answer)["ecwolf.pk3"].checked == CHECKED_NOT_COMPARABLE

    def test_nothing_is_established_so_the_file_is_not_satisfied_either_way(self):
        answer = _archive_answer({"md5": "00" * 16, "sha1": "00" * 20, "size": ARCHIVE_SIZE})
        assert _by_name(answer)["ecwolf.pk3"].satisfied is None

    def test_a_required_file_nobody_judged_leaves_the_core_undecided(self):
        answer = _archive_answer({"md5": "00" * 16, "sha1": "00" * 20, "size": ARCHIVE_SIZE})
        assert answer.cores[0].requirements_met is None

    def test_the_answer_says_why_it_withheld(self):
        answer = _archive_answer({"md5": "00" * 16, "sha1": "00" * 20, "size": ARCHIVE_SIZE})
        assert [c.code for c in answer.caveats] == [CAVEAT_FIRMWARE_IDENTITY_NOT_COMPARABLE]

    def test_the_caveat_names_the_file_and_the_drift(self):
        answer = _archive_answer({"md5": "00" * 16, "sha1": "00" * 20, "size": ARCHIVE_SIZE})
        data = answer.caveats[0].data
        assert data["file_name"] == "ecwolf.pk3"
        assert data["path"] == f"{BIOS_DIR}/ecwolf.pk3"
        assert data["archive_reason"] == "core-bundled"

    def test_a_romset_says_romset(self):
        answer = _archive_answer(
            {"md5": "00" * 16, "sha1": "00" * 20, "size": ARCHIVE_SIZE}, reason="romset"
        )
        assert answer.caveats[0].data["archive_reason"] == "romset"

    def test_the_caveat_names_the_list_version_that_stated_the_kind(self):
        # Which reading called this an archive is part of the answer, the way
        # the system-assignment caveats carry their own table's version.
        answer = _archive_answer({"md5": "00" * 16, "sha1": "00" * 20, "size": ARCHIVE_SIZE})
        assert answer.caveats[0].data["table_version"] == ARCHIVE_LIST_VERSION

    def test_a_table_that_states_no_list_version_carries_none(self):
        # An older vendored table is a fact about that table, not a reason to
        # report the version this atlas happens to know.
        answer = _archive_answer(
            {"md5": "00" * 16, "sha1": "00" * 20, "size": ARCHIVE_SIZE}, list_version=None
        )
        assert answer.caveats[0].data["table_version"] == ""

    def test_an_exact_hit_is_still_a_verdict(self):
        # The asymmetry: a positive establishes that this IS the pinned
        # packaging of the pinned version, and throwing that away would cost a
        # real answer.
        answer = _archive_answer({"md5": ARCHIVE_MD5, "sha1": ARCHIVE_SHA1, "size": ARCHIVE_SIZE})
        assert _by_name(answer)["ecwolf.pk3"].checked == CHECKED_VERIFIED

    def test_an_exact_hit_carries_no_caveat(self):
        answer = _archive_answer({"md5": ARCHIVE_MD5, "sha1": ARCHIVE_SHA1, "size": ARCHIVE_SIZE})
        assert answer.caveats == ()

    def test_without_verification_it_is_unchecked_not_not_comparable(self):
        # 'not-comparable' is a result of looking. Not looking has its own word.
        answer = _archive_answer(
            {"md5": "00" * 16, "sha1": "00" * 20, "size": ARCHIVE_SIZE}, verify=False
        )
        assert _by_name(answer)["ecwolf.pk3"].checked == CHECKED_UNCHECKED

    def test_unreadable_bytes_are_unread_not_not_comparable(self):
        # A read failure is not a comparison that came out inconclusive: the
        # unreadable answer wins, and its own caveat says so.
        answer = _archive_answer({"size": ARCHIVE_SIZE})
        assert _by_name(answer)["ecwolf.pk3"].checked == CHECKED_UNREAD

    def test_unreadable_bytes_carry_the_read_failure_caveat(self):
        answer = _archive_answer({"size": ARCHIVE_SIZE})
        assert [c.code for c in answer.caveats] == [CAVEAT_FIRMWARE_UNREADABLE]

    def test_the_kind_is_stated_even_where_no_check_ran(self):
        answer = _archive_answer(
            {"md5": ARCHIVE_MD5, "sha1": ARCHIVE_SHA1, "size": ARCHIVE_SIZE}, verify=False
        )
        identity = _by_name(answer)["ecwolf.pk3"].identity
        assert identity is not None
        assert identity.kind == "archive"

    def test_a_whole_file_dump_still_answers_mismatch(self):
        # The regression direction: nothing about the new value may soften the
        # verdict a real dump earns.
        machine = _machine({f"{BIOS_DIR}/scph5501.bin": {"md5": "00" * 16, "sha1": "00" * 20, "size": 8}})
        answer = firmware_for_core(
            machine, _context(machine), core_so="demo_psx_libretro.so", verify=True
        )
        assert _by_name(answer)["scph5501.bin"].checked == CHECKED_MISMATCH


class TestTheTableStatesEveryKind:
    """``kind`` is read strictly: a table that never learned it is refused.

    Defaulting a missing ``kind`` to ``file`` would answer ``mismatch`` over an
    archive again, silently, which is the whole defect. So the loader refuses
    the table instead — the same shape of refusal a missing digest gets.
    """

    def _table(self, entry: dict[str, object]) -> str:
        return json.dumps({"_meta": {}, "files": {"neogeo.zip": entry}})

    def test_an_entry_without_a_kind_is_refused(self):
        text = self._table({"md5": "aa" * 16, "sha1": "bb" * 20, "size": 8})
        with pytest.raises(ValueError, match="kind must be one of"):
            load_hashes(text)

    def test_a_kind_outside_the_vocabulary_is_refused(self):
        text = self._table({"md5": "aa" * 16, "sha1": "bb" * 20, "size": 8, "kind": "zip"})
        with pytest.raises(ValueError, match="kind must be one of"):
            load_hashes(text)

    def test_an_archive_that_states_no_reason_is_refused(self):
        text = self._table({"md5": "aa" * 16, "sha1": "bb" * 20, "size": 8, "kind": "archive"})
        with pytest.raises(ValueError, match="archive_reason must be one of"):
            load_hashes(text)

    def test_an_archive_reason_outside_the_vocabulary_is_refused(self):
        text = self._table(
            {"md5": "aa" * 16, "sha1": "bb" * 20, "size": 8, "kind": "archive", "archive_reason": "big"}
        )
        with pytest.raises(ValueError, match="archive_reason must be one of"):
            load_hashes(text)

    def test_a_dump_that_claims_a_reason_is_refused(self):
        text = self._table(
            {"md5": "aa" * 16, "sha1": "bb" * 20, "size": 8, "kind": "file", "archive_reason": "romset"}
        )
        with pytest.raises(ValueError, match="only an archive carries an archive_reason"):
            load_hashes(text)

    def test_the_error_names_the_entry(self):
        text = self._table({"md5": "aa" * 16, "sha1": "bb" * 20, "size": 8})
        with pytest.raises(ValueError, match="^neogeo.zip: "):
            load_hashes(text)

    def test_an_identity_carries_the_version_of_the_table_it_came_from(self):
        text = json.dumps(
            {
                "_meta": {"archive_identities_version": "9"},
                "files": {"neogeo.zip": {"md5": "aa" * 16, "sha1": "bb" * 20, "size": 8, "kind": "file"}},
            }
        )
        identity = load_hashes(text).for_path("neogeo.zip")
        assert identity is not None
        assert identity.table_version == "9"

    def test_the_packaged_table_states_its_list_version(self):
        assert load_hashes().archive_identities_version

    def test_an_archive_entry_reaches_the_identity_with_its_reason(self):
        identity = load_hashes(self._table(
            {"md5": "aa" * 16, "sha1": "bb" * 20, "size": 8, "kind": "archive", "archive_reason": "romset"}
        )).for_path("neogeo.zip")
        assert identity is not None
        assert identity.archive_reason == "romset"

    def test_the_packaged_table_states_both_kinds_and_nothing_else(self):
        packaged = load_hashes()
        kinds = {entry.kind for name in packaged.names() if (entry := packaged.get(name)) is not None}
        assert kinds == {"file", "archive"}


class TestIdentityRefusesAStateThatWouldLie:
    """The dataclass holds the same rule the table does, at every construction."""

    def test_an_unknown_kind_is_refused(self):
        with pytest.raises(ValueError, match="kind must be one of"):
            FirmwareIdentity(md5="a" * 32, sha1="b" * 40, size=4, kind="zip")  # type: ignore[arg-type]

    def test_an_archive_must_state_why_its_bytes_move(self):
        with pytest.raises(ValueError, match="archive_reason must be one of"):
            FirmwareIdentity(md5="a" * 32, sha1="b" * 40, size=4, kind="archive")

    def test_a_dump_may_not_claim_a_reason(self):
        with pytest.raises(ValueError, match="only an archive carries an archive_reason"):
            FirmwareIdentity(md5="a" * 32, sha1="b" * 40, size=4, kind="file", archive_reason="romset")


class TestRequirementInvariants:
    """The dataclass refuses states that would lie."""

    def test_an_absent_file_cannot_carry_a_verdict(self):
        with pytest.raises(ValueError):
            FirmwareRequirement(
                core_so="x.so", system="psx", system_source="systemname", need="required",
                file_name="a.bin", path="/bios/a.bin", declared="a.bin", description="", identity=None,
                found="missing", checked="verified",
            )

    def test_a_present_file_must_state_a_value_of_the_vocabulary(self):
        with pytest.raises(ValueError):
            FirmwareRequirement(
                core_so="x.so", system="psx", system_source="systemname", need="required",
                file_name="a.bin", path="/bios/a.bin", declared="a.bin", description="", identity=None,
                found="file", checked=None,
            )

    def test_a_refused_file_cannot_carry_an_identity(self):
        # The emulator turned it down before any table was consulted, so
        # there is nothing an identity could have been read from.
        identity = FirmwareIdentity(md5="0" * 32, sha1=None, size=8, kind="file")
        with pytest.raises(ValueError, match="carries no identity"):
            FirmwareRequirement(
                core_so=None, system="psx", system_source="card", need="required",
                file_name="a.bin", path="/bios/a.bin", declared="a.bin", description="",
                identity=identity, found="file", checked=CHECKED_REFUSED,
            )

    def test_need_is_only_required_or_optional(self):
        with pytest.raises(ValueError):
            FirmwareRequirement(
                core_so="x.so", system="psx", system_source="systemname",
                need="undeclared", file_name="a.bin",  # type: ignore[arg-type]
                path="/bios/a.bin", declared="a.bin", description="", identity=None, found="missing",
                checked=None,
            )

    def test_found_is_only_a_path_kind(self):
        with pytest.raises(ValueError, match="found must be one of"):
            FirmwareRequirement(
                core_so="x.so", system="psx", system_source="systemname", need="required",
                file_name="a.bin", path="/bios/a.bin", declared="a.bin", description="", identity=None,
                found="gone", checked=None,  # type: ignore[arg-type]
            )

    def test_system_source_is_only_a_way_the_system_was_arrived_at(self):
        with pytest.raises(ValueError, match="system_source must be one of"):
            FirmwareRequirement(
                core_so="x.so", system="psx", need="required",
                system_source="guessed", file_name="a.bin",  # type: ignore[arg-type]
                path="/bios/a.bin", declared="a.bin", description="", identity=None, found="missing",
                checked=None,
            )

    def test_the_distribution_states_the_name_it_writes_for_itself(self):
        # Derived from the identifier rather than stored beside it, so the two
        # cannot disagree — 'retrodeck' is the key, 'RetroDECK' the word to show.
        supplied = SuppliedBy(distribution="retrodeck", source="/ship/a.bin", card_version="1")
        assert supplied.label == "RetroDECK"

    def test_a_distribution_nothing_spells_is_an_error_and_not_a_guess(self):
        supplied = SuppliedBy(distribution="retrodek", source="/ship/a.bin", card_version="1")
        with pytest.raises(ValueError, match="no packaged spelling"):
            _ = supplied.label

    def test_only_a_file_can_be_the_distributions_own_copy(self):
        # A provenance statement over a destination with nothing at it would be
        # about a file that does not exist.
        supplied = SuppliedBy(distribution="retrodeck", source="/ship/a.bin", card_version="1")
        with pytest.raises(ValueError, match="supplied_by"):
            FirmwareRequirement(
                core_so="x.so", system="psx", system_source="systemname", need="required",
                file_name="a.bin", path="/bios/a.bin", declared="a.bin", description="", identity=None,
                found="missing", checked=None, supplied_by=supplied,
            )


class TestRefusedDeclarationInvariants:
    """A refused declaration states the same need a followed one does."""

    def test_need_is_only_required_or_optional(self):
        with pytest.raises(ValueError, match="need must be one of"):
            RefusedDeclaration(
                declared="../a.bin",
                need="undeclared",  # type: ignore[arg-type]
                reason=CAVEAT_FIRMWARE_PATH_ESCAPES_ROOT,
            )

    def test_a_declared_need_is_kept(self):
        refused = RefusedDeclaration(
            declared="../a.bin", need="optional", reason=CAVEAT_FIRMWARE_PATH_ESCAPES_ROOT
        )
        assert refused.need == "optional"


class TestWhatTheMachineWouldNotSay:
    """States atlas must not flatten into a claim it did not establish."""

    def test_an_inaccessible_path_is_not_an_absent_file(self):
        machine = _machine(inaccessible=[f"{BIOS_DIR}/scph5501.bin"])
        answer = firmware_for_core(machine, _context(machine), core_so="demo_psx_libretro.so")
        blocked = next(r for r in answer.requirements if r.file_name == "scph5501.bin")
        assert blocked.found == "inaccessible"
        assert blocked.present is None, "could not look is not 'not there'"
        assert blocked.checked is None
        assert blocked.satisfied is None
        assert CAVEAT_FIRMWARE_PATH_INACCESSIBLE in [c.code for c in answer.caveats]
        assert answer.cores[0].requirements_met is None

    def test_a_directory_where_a_file_belongs_says_so(self):
        machine = _machine(dirs=[f"{BIOS_DIR}/scph5501.bin"])
        answer = firmware_for_core(machine, _context(machine), core_so="demo_psx_libretro.so")
        blocked = next(r for r in answer.requirements if r.file_name == "scph5501.bin")
        # Something is there and nothing about it was established. No row of the
        # curated table names this core and this path, so the declaration is a
        # file — but the directory is still not a missing file and still not an
        # invitation to delete anything.
        assert blocked.declared_kind == DECLARED_FILE
        assert blocked.found == "directory"
        assert blocked.present is True
        assert blocked.checked == CHECKED_UNKNOWN
        assert blocked.satisfied is None
        assert answer.cores[0].requirements_met is None
        assert CAVEAT_FIRMWARE_PATH_OBSTRUCTED in [c.code for c in answer.caveats]

    def test_an_unreadable_info_leaves_the_core_in_the_answer(self):
        machine = _machine(
            {
                f"{INFO_DIR}/flycast_libretro.info": {"status": "unreadable"},
                f"{INFO_DIR}/flycast_libretro.so": {"status": "invalid-text"},
            }
        )
        cores = {c.core_so: c for c in firmware_inventory(machine, _context(machine)).cores}
        core = cores["flycast_libretro.so"]
        assert core.declaration == DECLARATION_UNREADABLE
        assert core.requirements == ()
        assert core.requirements_met is None
        assert [c.code for c in core.caveats] == [CAVEAT_CORE_INFO_UNREADABLE]

    def test_a_core_whose_info_is_missing_entirely_is_still_installed(self):
        machine = _machine({f"{INFO_DIR}/flycast_libretro.so": {"status": "invalid-text"}})
        cores = {c.core_so: c for c in firmware_inventory(machine, _context(machine)).cores}
        assert cores["flycast_libretro.so"].declaration == DECLARATION_UNREADABLE

    @pytest.mark.parametrize("declared", ["../../etc/shadow", "dc/../../etc/shadow"])
    def test_a_declaration_never_reaches_outside_the_firmware_root(self, declared: str):
        info = f'systemname = "Escape"\nfirmware_count = 1\nfirmware0_path = "{declared}"\n'
        machine = _machine(
            {
                f"{INFO_DIR}/escape_libretro.info": info,
                f"{INFO_DIR}/escape_libretro.so": {"status": "invalid-text"},
                "/etc/shadow": "root:!:0:0:::",
            }
        )
        answer = firmware_for_core(machine, _context(machine), core_so="escape_libretro.so", verify=True)
        core = answer.cores[0]
        assert core.requirements == ()
        # The refusal is a fact about THIS core, so it lives on the core — and
        # a required file atlas would not look at is never an all-clear.
        assert [r.declared for r in core.refused] == [declared]
        assert [c.code for c in core.caveats] == [CAVEAT_FIRMWARE_PATH_ESCAPES_ROOT]
        assert core.requirements_met is None

    def test_an_escaping_declaration_does_not_widen_the_unclaimed_scan(self):
        info = 'systemname = "Escape"\nfirmware_count = 1\nfirmware0_path = "../etc/passwd"\n'
        machine = _machine(
            {
                f"{INFO_DIR}/escape_libretro.info": info,
                f"{INFO_DIR}/escape_libretro.so": {"status": "invalid-text"},
                "/etc/passwd": "root:x:0:0:::",
                f"{BIOS_DIR}/stray.bin": _blob(b"stray"),
            }
        )
        paths = [f.path for f in firmware_inventory(machine, _context(machine), verify=True).unclaimed]
        assert paths == [f"{BIOS_DIR}/stray.bin"]

    def test_destination_under_refuses_what_leaves_the_root(self):
        m = _machine()
        assert destination_under(m, "/bios", "dc/dc_boot.bin").path == "/bios/dc/dc_boot.bin"
        assert destination_under(m, "/bios", "./scph5501.bin").path == "/bios/scph5501.bin"
        for declared in ("../etc/shadow", "dc/../../etc/shadow", "../bios-backup/x.bin"):
            outcome = destination_under(m, "/bios", declared)
            assert outcome.path is None, declared
            assert outcome.refusal == CAVEAT_FIRMWARE_PATH_ESCAPES_ROOT, declared

    @pytest.mark.parametrize(
        "declared",
        [
            "/etc/shadow",
            "//etc/shadow",
            "///etc/shadow",
            "/./etc/shadow",
            "/etc//shadow",
            "/etc/./shadow",
            "/../etc/shadow",
            "//../etc/shadow",
            "/etc/../../etc/shadow",
            "/bios/../etc/shadow",
            "/bios/../../etc/shadow",
            "/../../../../../../etc/shadow",
            "/./../etc/shadow",
            "/proc/self/environ",
            "/",
            "//",
            "/etc/",
            "/..",
            "/etc/..",
            "C:\\Windows\\x.bin",
            "C:/Windows/x.bin",
            "\\etc\\shadow",
            "/ /etc/shadow",
            "/etc/shadow ",
        ],
    )
    def test_an_absolute_declaration_never_reaches_outside_the_root(self, declared: str):
        """An absolute declaration is no longer refused — it is *composed* under the root.

        This is the property the old refusal was there to guard, and it has to
        survive the change under its own name: whatever the spelling, either
        the destination lies inside the firmware root or there is none at all.
        Every read atlas does afterwards — presence, size, digest, the scan —
        is derived from that path, so nothing here may resolve outside it.
        """
        outcome = destination_under(_machine(), "/bios", declared)
        if outcome.path is None:
            assert outcome.refusal in (
                CAVEAT_FIRMWARE_PATH_ESCAPES_ROOT,
                CAVEAT_FIRMWARE_PATH_NAMES_NO_FILE,
            ), declared
        else:
            assert outcome.path.startswith("/bios/"), declared

    def test_destination_under_composes_an_absolute_declaration_inside_the_root(self):
        # fill_pathname_join concatenates with one separator and has no case for
        # an absolute path (file_path.c:983-993), so RetroArch looks for this
        # file under the system directory and atlas answers where it looks.
        m = _machine()
        assert destination_under(m, "/bios", "/etc/shadow").path == "/bios/etc/shadow"
        assert destination_under(m, "/bios", "//etc/shadow").path == "/bios/etc/shadow"
        assert destination_under(m, "/bios/", "/dc/dc_boot.bin").path == "/bios/dc/dc_boot.bin"
        # Composed first, so a climb that reads like it leaves the root only
        # cancels the component the composition put in front of it.
        assert destination_under(m, "/bios", "/bios/../etc/shadow").path == "/bios/etc/shadow"

    def test_an_absolute_declaration_is_answered_where_retroarch_looks(self):
        info = 'systemname = "Escape"\nfirmware_count = 1\nfirmware0_path = "/etc/shadow"\n'
        machine = _machine(
            {
                f"{INFO_DIR}/escape_libretro.info": info,
                f"{INFO_DIR}/escape_libretro.so": {"status": "invalid-text"},
                "/etc/shadow": "root:!:0:0:::",
            }
        )
        answer = firmware_for_core(machine, _context(machine), core_so="escape_libretro.so", verify=True)
        core = answer.cores[0]
        # The answer never reaches outside the root: the destination is under
        # it, so the real /etc/shadow — which exists on this fixture machine —
        # is neither read nor reported, and the file the core will not find is
        # stated as missing where RetroArch will look for it.
        assert [r.path for r in _plain_requirements(core)] == [f"{BIOS_DIR}/etc/shadow"]
        assert _plain_requirements(core)[0].path.startswith(f"{BIOS_DIR}/")
        assert _plain_requirements(core)[0].found == "missing"
        assert core.refused == ()
        assert core.caveats == ()

    @pytest.mark.parametrize("declared", ["dc/dc_boot.bin", "/etc/shadow"])
    @pytest.mark.parametrize("root", ["", "bios", "./bios", "~/bios"])
    def test_destination_under_refuses_a_root_that_is_not_an_absolute_path(
        self, root: str, declared: str
    ):
        """A root that is not one cannot bound anything, so it yields no destination.

        The resolver builds every path from ``/``, so an empty root resolves to
        ``/`` and the containment check then passes on ``/etc/shadow`` — a
        guard that accepts everything. A cfg reaches this with a relative
        ``system_directory`` (vector: firmware-a-relative-system-directory-…),
        which survives ``expand_home`` and the sandbox translation as written;
        the empty spelling reads as unset instead, and only this public entry
        point can be handed it directly.
        """
        outcome = destination_under(_machine(), root, declared)
        assert outcome.path is None, (root, declared)
        assert outcome.refusal == CAVEAT_FIRMWARE_ROOT_UNUSABLE, (root, declared)

    def test_destination_under_refuses_a_declaration_that_names_no_file(self):
        # Each of these resolves to a perfectly legal directory — "dc/.." to the
        # firmware root itself, "dc/" to the subdirectory — so nothing here is
        # caught by the root bound.
        m = _machine()
        for declared in (".", "dc/..", "dc/.", "..", "dc/", "/"):
            outcome = destination_under(m, "/bios", declared)
            assert outcome.path is None, declared
            assert outcome.refusal == CAVEAT_FIRMWARE_PATH_NAMES_NO_FILE, declared

    @pytest.mark.parametrize("declared", [".", "dc/..", "dc/"])
    def test_a_declaration_that_names_no_file_is_refused_not_answered(self, declared: str):
        """A directory step is not a firmware file, however well it resolves.

        Answered as a requirement it points at a directory (the root itself for
        ``dc/..``), which reads as "a directory sits where a file belongs" —
        obstruction, a fact about the *machine*. The fact here is about the
        declaration, and the core's own refusal list is where it belongs.
        """
        info = (
            'systemname = "Odd"\n'
            "firmware_count = 1\n"
            f'firmware0_path = "{declared}"\n'
            'firmware0_opt = "false"\n'
        )
        machine = _machine(
            {
                f"{INFO_DIR}/odd_libretro.info": info,
                f"{INFO_DIR}/odd_libretro.so": {"status": "invalid-text"},
                f"{BIOS_DIR}/dc/dc_boot.bin": _blob(b"boot"),
            }
        )
        core = firmware_for_core(machine, _context(machine), core_so="odd_libretro.so").cores[0]
        assert core.requirements == ()
        assert [r.declared for r in core.refused] == [declared]
        assert [r.reason for r in core.refused] == [CAVEAT_FIRMWARE_PATH_NAMES_NO_FILE]
        assert [c.code for c in core.caveats] == [CAVEAT_FIRMWARE_PATH_NAMES_NO_FILE]
        # A required file atlas would not follow is never an all-clear.
        assert core.requirements_met is None


class TestADeclarationStatesItsOwnShape:
    """``declared_kind``: what the CORE opens the path at, table-stated, not guessed.

    The declaration is the subject throughout — the field holds whether or not
    anything is at the destination, which is the whole reason it is not derived
    from what was found there.
    """

    def _lrps2(
        self, files: Mapping[str, FixtureFileSpec] | None = None, *, verify: bool = True, **kwargs: object
    ):
        tree: dict[str, FixtureFileSpec] = {
            f"{INFO_DIR}/pcsx2_libretro.info": LRPS2_FOLDER_INFO,
            f"{INFO_DIR}/pcsx2_libretro.so": {"status": "invalid-text"},
        }
        tree.update(files or {})
        machine = _machine(tree, **kwargs)
        answer = firmware_for_core(machine, _context(machine), core_so=LRPS2_SO, verify=verify)
        return answer, next(r for r in answer.requirements if r.declared == "pcsx2/bios")

    def test_the_table_is_the_only_source(self):
        # Keyed on the .so SHORT name, the spelling the rule cards use — and on
        # the declared string, so one core's two declarations differ.
        assert declared_kind_of("pcsx2_libretro", "pcsx2/bios") == DECLARED_DIRECTORY
        assert declared_kind_of("pcsx2", "pcsx2/bios") == DECLARED_DIRECTORY
        assert declared_kind_of("pcsx2_libretro", "pcsx2/resources/GameIndex.yaml") == DECLARED_FILE
        # Another core declaring the same string is another question entirely.
        assert declared_kind_of("play_libretro", "pcsx2/bios") == DECLARED_FILE
        # The kind and the row come off the same table, so they agree by construction.
        assert declared_directory_of("pcsx2_libretro", "pcsx2/bios") is not None
        assert declared_directory_of("play_libretro", "pcsx2/bios") is None

    def test_every_row_keys_on_the_short_name(self):
        """The mistake a future row would make: keying on the ``.so`` stem."""
        for core, declared in FIRMWARE_DECLARED_DIRECTORY:
            assert core, (core, declared)
            assert not core.endswith("_libretro"), core
            assert declared, (core, declared)
            assert not declared.endswith("/"), declared

    def test_the_row_states_the_size_filter_inclusively(self):
        """LRPS2 keeps ``MIN_BIOS_SIZE <= size <= MAX_BIOS_SIZE`` (main.cpp:1815 at 14d19f8)."""
        row = declared_directory_of("pcsx2", "pcsx2/bios")
        assert row is not None
        assert row == DeclaredDirectory(
            identities="pcsx2/bios/",
            min_size=4 * 1024 * 1024,
            max_size=8 * 1024 * 1024,
            reader=READER_PS2_BIOS_HEADER,
            # The option whose value names the one file inside the folder a
            # launch opens (main.cpp:360-365 at 14d19f8) — the row states it,
            # so the resolver reads no core name of its own.
            option_key="pcsx2_bios",
        )
        assert row.accepts_size(4 * 1024 * 1024 - 1) is False
        assert row.accepts_size(4 * 1024 * 1024) is True
        assert row.accepts_size(8 * 1024 * 1024) is True
        assert row.accepts_size(8 * 1024 * 1024 + 1) is False
        # An unknown size is not a yes: the core's filter reads a stat that came back.
        assert row.accepts_size(None) is False

    def test_a_row_names_only_a_content_read_the_resolver_makes(self):
        """A row promising a read nobody implemented fails at construction, not at the first folder."""
        with pytest.raises(ValueError, match="reader"):
            DeclaredDirectory(identities="pcsx2/bios/", min_size=1, max_size=2, reader="crc-table")

    def test_the_packaged_images_under_the_prefix_are_of_a_size_the_row_accepts(self):
        """The 73 packaged identities are a subset of what the core accepts, and reachable by content."""
        packaged = load_hashes()
        row = declared_directory_of("pcsx2", "pcsx2/bios")
        assert row is not None
        filed = [name for name in packaged.names() if name.startswith(row.identities)]
        assert len(filed) == 73
        for name in filed:
            entry = packaged.get(name)
            assert entry is not None, name
            assert row.accepts_size(entry.size), name
            # Case-insensitive on the digest, the way every other content lookup is.
            assert packaged.for_content_under(row.identities, entry.md5.upper()) is entry, name

    def test_for_content_under_sees_only_the_prefix(self):
        hashes = load_hashes(TABLE)
        # scph5501.bin's bytes are in the table — and not under this prefix.
        assert hashes.for_content_under("pcsx2/bios/", "aa" * 16) is None
        under = hashes.for_content_under("pcsx2/bios/", "77" * 16)
        assert under is not None
        assert under.name == "pcsx2/bios/ps2-0200e-20040614.bin"

    def test_a_recognised_image_inside_the_folder_satisfies_the_declaration(self):
        answer, folder = self._lrps2(
            {f"{LRPS2_FOLDER}/scph70004.bin": PS2_IMAGE_EUR},
            ps2_bios_headers={f"{LRPS2_FOLDER}/scph70004.bin": PS2_HEADER_EUR},
        )
        assert folder.declared_kind == DECLARED_DIRECTORY
        assert folder.found == "directory"
        assert folder.present is True
        # No packaged identity belongs to a folder, so the byte axis stays where it was.
        assert folder.checked == CHECKED_UNKNOWN
        assert folder.identity is None
        # The verdict is over the contents: by the core's own header read, and
        # named under the table's own name where the table lists the bytes.
        assert folder.satisfied is True
        assert answer.cores[0].requirements_met is True
        assert [c.code for c in answer.caveats] == [CAVEAT_FIRMWARE_IMAGE_IDENTIFIED]
        assert answer.caveats[0].data == {
            "path": f"{LRPS2_FOLDER}/scph70004.bin",
            "image": "ps2-0200e-20040614.bin",
            "md5": "77" * 16,
            "table": "0",
            "core_so": LRPS2_SO,
            **PS2_FIELDS_EUR,
        }
        assert "reads as 'Europe  v02.00(14/06/2004)  Console fixture-eu'" in answer.caveats[0].message

    def test_every_recognised_image_is_stated_and_none_is_picked(self):
        """Two is more choice, not a conflict.

        The core offers them all as option values, up to 127 — the fill loop
        stops one short of the 128-slot RETRO_NUM_CORE_OPTION_VALUES_MAX to
        leave room for the terminator (main.cpp:1828, :1833) — and needs one.
        """
        answer, folder = self._lrps2(
            {
                f"{LRPS2_FOLDER}/scph70004.bin": PS2_IMAGE_EUR,
                f"{LRPS2_FOLDER}/scph39001.bin": PS2_IMAGE_USA,
            },
            ps2_bios_headers={
                f"{LRPS2_FOLDER}/scph70004.bin": PS2_HEADER_EUR,
                f"{LRPS2_FOLDER}/scph39001.bin": PS2_HEADER_USA,
            },
        )
        assert folder.satisfied is True
        identified = [c for c in answer.caveats if c.code == CAVEAT_FIRMWARE_IMAGE_IDENTIFIED]
        assert [c.data["path"] for c in identified] == [
            f"{LRPS2_FOLDER}/scph39001.bin",
            f"{LRPS2_FOLDER}/scph70004.bin",
        ]
        assert [c.data["image"] for c in identified] == [
            "ps2-0160a-20010427.bin",
            "ps2-0200e-20040614.bin",
        ]

    def test_a_folder_with_nothing_of_an_accepted_size_is_unmet_with_and_without_a_content_check(self):
        """The size test is the core's own first filter and a stat, so it settles without hashing."""
        wrong_sized = {
            f"{LRPS2_FOLDER}/scph5501.bin": _blob(b"a PlayStation image is 512 KiB, not 4 MiB"),
            f"{LRPS2_FOLDER}/readme.txt": "put your PS2 BIOS images here\n",
        }
        for verify in (False, True):
            answer, folder = self._lrps2(wrong_sized, verify=verify)
            assert folder.found == "directory"
            assert folder.satisfied is False
            assert answer.cores[0].requirements_met is False
            assert [c.code for c in answer.caveats] == [CAVEAT_FIRMWARE_DIRECTORY_HOLDS_NO_CANDIDATE]
            assert answer.caveats[0].data == {
                "dir": LRPS2_FOLDER,
                "core_so": LRPS2_SO,
                "need": NEED_REQUIRED,
                "table_version": FIRMWARE_DECLARED_DIRECTORY_VERSION,
            }

    def test_the_listing_is_not_recursive(self):
        """``FindFiles(..., "*", FILESYSTEM_FIND_FILES, ...)`` lists one level (main.cpp:1807)."""
        answer, folder = self._lrps2({f"{LRPS2_FOLDER}/eur/scph70004.bin": PS2_IMAGE_EUR})
        assert folder.satisfied is False
        assert [c.code for c in answer.caveats] == [CAVEAT_FIRMWARE_DIRECTORY_HOLDS_NO_CANDIDATE]

    def test_a_dotted_name_is_not_listed(self):
        """No hidden-files flag on the core's listing, and the glob's wildcard matches no leading dot."""
        answer, folder = self._lrps2({f"{LRPS2_FOLDER}/.scph70004.bin": PS2_IMAGE_EUR})
        assert folder.satisfied is False
        assert [c.code for c in answer.caveats] == [CAVEAT_FIRMWARE_DIRECTORY_HOLDS_NO_CANDIDATE]

    def test_a_right_sized_file_that_fails_the_cores_own_test_is_no_image(self):
        """The verdict the core makes: read the way it reads, and no RESET/ROMVER table found."""
        answer, folder = self._lrps2(
            {f"{LRPS2_FOLDER}/scph39001.bin": PS2_UNLISTED},
            ps2_bios_headers={f"{LRPS2_FOLDER}/scph39001.bin": NOT_A_BIOS},
        )
        assert folder.satisfied is False
        assert answer.cores[0].requirements_met is False
        assert [c.code for c in answer.caveats] == [CAVEAT_FIRMWARE_DIRECTORY_HOLDS_NO_IMAGE]
        assert answer.caveats[0].data == {
            "dir": LRPS2_FOLDER,
            "candidates": "1",
            "paths": (f"{LRPS2_FOLDER}/scph39001.bin",),
            "core_so": LRPS2_SO,
            "table_version": FIRMWARE_DECLARED_DIRECTORY_VERSION,
        }
        assert answer.caveats[0].message.startswith(f"one file in {LRPS2_FOLDER} is of a size")
        # Read, found to be no BIOS, and nobody's: the unclaimed scan may list it.
        assert answer.cores[0].claims == ()

    def test_a_right_sized_file_the_table_does_not_list_is_an_image_by_its_header(self):
        """The table is a subset of what the core accepts; the header is the core's own test."""
        answer, folder = self._lrps2(
            {f"{LRPS2_FOLDER}/scph39001.bin": PS2_UNLISTED},
            ps2_bios_headers={f"{LRPS2_FOLDER}/scph39001.bin": PS2_HEADER_EUR},
        )
        assert folder.satisfied is True
        assert answer.cores[0].requirements_met is True
        assert [c.code for c in answer.caveats] == [CAVEAT_FIRMWARE_IMAGE_UNLISTED]
        assert answer.caveats[0].data == {
            "path": f"{LRPS2_FOLDER}/scph39001.bin",
            **PS2_FIELDS_EUR,
            "md5": "00" * 16,
            "table": "0",
            "core_so": LRPS2_SO,
        }
        assert answer.cores[0].claims == (f"{LRPS2_FOLDER}/scph39001.bin",)

    def test_the_description_reproduces_the_cores_own_format_string_byte_for_byte(self):
        """``description`` is prose the CORE composes, and its columns are load-bearing.

        Not a slug and never to become one: it reproduces LRPS2's own
        ``"%-7s v%s.%s(%c%c/%c%c/%c%c%c%c)  %s %s"``
        (``BiosTools.cpp:147-155`` at 14d19f8, built in
        :meth:`atlas.ps2_bios.Ps2BiosHeader.from_strings`), and a consumer
        renders it to a person the way the emulator's own BIOS list does. The
        zone is left-justified in seven columns and two spaces stand before the
        console word — so ``Europe`` carries one trailing space here, and a
        test that trimmed it would let the padding rot unnoticed. The fields
        beside it (``zone``, ``version``, ``date``, ``serial``) are the
        machine-readable halves; this one is the rendering.
        """
        identified, _ = self._lrps2(
            {f"{LRPS2_FOLDER}/scph70004.bin": PS2_IMAGE_EUR},
            ps2_bios_headers={f"{LRPS2_FOLDER}/scph70004.bin": PS2_HEADER_EUR},
        )
        unlisted, _ = self._lrps2(
            {f"{LRPS2_FOLDER}/scph39001.bin": PS2_UNLISTED},
            ps2_bios_headers={f"{LRPS2_FOLDER}/scph39001.bin": PS2_HEADER_EUR},
        )
        for answer, code in (
            (identified, CAVEAT_FIRMWARE_IMAGE_IDENTIFIED),
            (unlisted, CAVEAT_FIRMWARE_IMAGE_UNLISTED),
        ):
            stated = next(c for c in answer.caveats if c.code == code)
            assert stated.data["description"] == "Europe  v02.00(14/06/2004)  Console fixture-eu"

    def test_an_image_whose_digest_could_not_be_taken_is_an_image_and_a_stated_read_failure(self):
        """The header is the verdict and stands; the digest that was never taken is a read failure, not a miss."""
        answer, folder = self._lrps2(
            {f"{LRPS2_FOLDER}/scph39001.bin": {"size": PS2_IMAGE_SIZE}},
            ps2_bios_headers={f"{LRPS2_FOLDER}/scph39001.bin": PS2_HEADER_EUR},
        )
        assert folder.satisfied is True
        assert answer.cores[0].requirements_met is True
        assert [c.code for c in answer.caveats] == [CAVEAT_FIRMWARE_IMAGE_UNLISTED, CAVEAT_FIRMWARE_UNREADABLE]
        # An empty md5 is the data's own word for "the table was not consulted".
        assert answer.caveats[0].data == {
            "path": f"{LRPS2_FOLDER}/scph39001.bin",
            **PS2_FIELDS_EUR,
            "md5": "",
            "table": "0",
            "core_so": LRPS2_SO,
        }
        # Worded without a table lookup nobody made.
        assert "files no identity" not in answer.caveats[0].message
        assert "could not be hashed" in answer.caveats[0].message
        assert answer.caveats[1].data == {"path": f"{LRPS2_FOLDER}/scph39001.bin"}
        assert "beside a header that came back" in answer.caveats[1].message
        assert answer.cores[0].claims == (f"{LRPS2_FOLDER}/scph39001.bin",)

    def test_a_rejected_candidate_whose_digest_could_not_be_taken_is_still_no_image(self):
        """The header's verdict stands; the digest that was never taken is stated, and reopens nothing."""
        answer, folder = self._lrps2(
            {f"{LRPS2_FOLDER}/scph39001.bin": {"size": PS2_IMAGE_SIZE}},
            ps2_bios_headers={f"{LRPS2_FOLDER}/scph39001.bin": NOT_A_BIOS},
        )
        assert folder.satisfied is False
        assert answer.cores[0].requirements_met is False
        assert [c.code for c in answer.caveats] == [
            CAVEAT_FIRMWARE_UNREADABLE,
            CAVEAT_FIRMWARE_DIRECTORY_HOLDS_NO_IMAGE,
        ]
        assert answer.caveats[0].data == {"path": f"{LRPS2_FOLDER}/scph39001.bin"}
        assert "does not read as a PS2 BIOS, and its bytes could not be hashed" in answer.caveats[0].message
        assert answer.caveats[1].data["paths"] == (f"{LRPS2_FOLDER}/scph39001.bin",)
        # Stated here, so the unclaimed scan — which hashes what it lists and
        # would state the same failure — must not state it again.
        assert answer.cores[0].claims == (f"{LRPS2_FOLDER}/scph39001.bin",)

    def test_the_inventory_does_not_restate_a_rejected_candidates_untaken_digest(self):
        machine = _machine(
            {
                f"{INFO_DIR}/pcsx2_libretro.info": LRPS2_FOLDER_INFO,
                f"{INFO_DIR}/pcsx2_libretro.so": {"status": "invalid-text"},
                f"{BIOS_DIR}/other.bin": {"size": PS2_IMAGE_SIZE},
            },
            symlinks={f"{BIOS_DIR}/pcsx2/bios": BIOS_DIR},
            ps2_bios_headers={f"{BIOS_DIR}/other.bin": NOT_A_BIOS},
        )
        answer = firmware_inventory(machine, _context(machine), verify=True)
        folder = next(r for r in answer.requirements if r.declared == "pcsx2/bios")
        assert folder.satisfied is False
        assert [c.code for c in answer.caveats] == [
            CAVEAT_FIRMWARE_UNREADABLE,
            CAVEAT_FIRMWARE_DIRECTORY_HOLDS_NO_IMAGE,
        ]
        assert answer.unclaimed == ()

    def test_a_table_hit_the_header_denies_is_a_contradiction_not_a_green(self):
        """Two reads over one file disagree; neither is taken over the other."""
        answer, folder = self._lrps2(
            {f"{LRPS2_FOLDER}/scph70004.bin": PS2_IMAGE_EUR},
            ps2_bios_headers={f"{LRPS2_FOLDER}/scph70004.bin": NOT_A_BIOS},
        )
        assert folder.satisfied is None
        assert answer.cores[0].requirements_met is None
        assert [c.code for c in answer.caveats] == [CAVEAT_FIRMWARE_IMAGE_CONTRADICTED]
        assert answer.caveats[0].data == {
            "path": f"{LRPS2_FOLDER}/scph70004.bin",
            "image": "ps2-0200e-20040614.bin",
            "md5": "77" * 16,
            "core_so": LRPS2_SO,
            "table": "0",
            "table_version": FIRMWARE_DECLARED_DIRECTORY_VERSION,
        }
        # Stated here, so the unclaimed scan must not state it again as an identified file.
        assert answer.cores[0].claims == (f"{LRPS2_FOLDER}/scph70004.bin",)

    def test_an_image_beside_a_contradiction_still_satisfies(self):
        answer, folder = self._lrps2(
            {
                f"{LRPS2_FOLDER}/scph70004.bin": PS2_IMAGE_EUR,
                f"{LRPS2_FOLDER}/scph39001.bin": PS2_IMAGE_USA,
            },
            ps2_bios_headers={
                f"{LRPS2_FOLDER}/scph70004.bin": PS2_HEADER_EUR,
                f"{LRPS2_FOLDER}/scph39001.bin": NOT_A_BIOS,
            },
        )
        assert folder.satisfied is True
        assert [c.code for c in answer.caveats] == [
            CAVEAT_FIRMWARE_IMAGE_IDENTIFIED,
            CAVEAT_FIRMWARE_IMAGE_CONTRADICTED,
        ]

    def test_a_header_that_cannot_be_read_is_a_read_failure_whatever_the_table_says(self):
        """The header is the read that decides; a table hit beside a failed one names nothing."""
        answer, folder = self._lrps2(
            {f"{LRPS2_FOLDER}/scph70004.bin": PS2_IMAGE_EUR},
            ps2_bios_headers={f"{LRPS2_FOLDER}/scph70004.bin": "unreadable"},
        )
        assert folder.satisfied is None
        assert [c.code for c in answer.caveats] == [CAVEAT_FIRMWARE_UNREADABLE]
        assert answer.caveats[0].data == {"path": f"{LRPS2_FOLDER}/scph70004.bin"}
        assert answer.cores[0].claims == (f"{LRPS2_FOLDER}/scph70004.bin",)

    def test_a_listed_file_with_no_header_answer_is_a_read_that_did_not_happen(self):
        """A fixture that forgot the key proves nothing about the file, and says so as unread."""
        answer, folder = self._lrps2({f"{LRPS2_FOLDER}/scph70004.bin": PS2_IMAGE_EUR})
        assert folder.satisfied is None
        assert [c.code for c in answer.caveats] == [CAVEAT_FIRMWARE_UNREADABLE]

    def test_the_header_is_not_read_without_a_content_check(self):
        """A declared header that would fail the read leaves no trace: nothing read it."""
        answer, folder = self._lrps2(
            {f"{LRPS2_FOLDER}/scph70004.bin": PS2_IMAGE_EUR},
            ps2_bios_headers={f"{LRPS2_FOLDER}/scph70004.bin": "unreadable"},
            verify=False,
        )
        assert folder.satisfied is None
        assert [c.code for c in answer.caveats] == [CAVEAT_FIRMWARE_SEARCH_UNVERIFIED]

    def test_a_file_that_fails_the_test_beside_a_recognised_image_casts_no_doubt(self):
        answer, folder = self._lrps2(
            {
                f"{LRPS2_FOLDER}/scph70004.bin": PS2_IMAGE_EUR,
                f"{LRPS2_FOLDER}/other.bin": PS2_UNLISTED,
            },
            ps2_bios_headers={
                f"{LRPS2_FOLDER}/scph70004.bin": PS2_HEADER_EUR,
                f"{LRPS2_FOLDER}/other.bin": NOT_A_BIOS,
            },
        )
        assert folder.satisfied is True
        assert [c.code for c in answer.caveats] == [CAVEAT_FIRMWARE_IMAGE_IDENTIFIED]

    def test_right_sized_files_without_a_content_check_answer_nothing(self):
        answer, folder = self._lrps2(
            {
                f"{LRPS2_FOLDER}/scph70004.bin": PS2_IMAGE_EUR,
                f"{LRPS2_FOLDER}/scph39001.bin": PS2_IMAGE_USA,
            },
            verify=False,
        )
        assert answer.hash_checked is False
        assert folder.satisfied is None
        assert answer.cores[0].requirements_met is None
        assert [c.code for c in answer.caveats] == [CAVEAT_FIRMWARE_SEARCH_UNVERIFIED]
        assert answer.caveats[0].data == {
            "dir": LRPS2_FOLDER,
            "candidates": "2",
            "need": NEED_REQUIRED,
            "core_so": LRPS2_SO,
        }
        assert "holds 2 files of a size this core accepts; which of them is a BIOS" in answer.caveats[0].message

    def test_a_folder_that_cannot_be_listed_establishes_nothing(self):
        answer, folder = self._lrps2(dirs=[LRPS2_FOLDER], unlistable=[LRPS2_FOLDER])
        assert folder.found == "directory"
        assert folder.satisfied is None
        assert [c.code for c in answer.caveats] == [CAVEAT_FIRMWARE_SCAN_INCOMPLETE]
        assert answer.caveats[0].data == {"dir": LRPS2_FOLDER, "unreadable": (LRPS2_FOLDER,)}

    def test_a_right_sized_file_whose_bytes_cannot_be_read_is_carried_as_unread(self):
        """A read failure is not 'bytes the table does not know'."""
        answer, folder = self._lrps2(
            {f"{LRPS2_FOLDER}/scph39001.bin": {"status": "unreadable", "size": PS2_IMAGE_SIZE}}
        )
        assert folder.satisfied is None
        assert [c.code for c in answer.caveats] == [CAVEAT_FIRMWARE_UNREADABLE]
        assert answer.caveats[0].data == {"path": f"{LRPS2_FOLDER}/scph39001.bin"}

    def test_an_unread_candidate_beside_a_recognised_image_is_still_stated(self):
        answer, folder = self._lrps2(
            {
                f"{LRPS2_FOLDER}/scph70004.bin": PS2_IMAGE_EUR,
                f"{LRPS2_FOLDER}/locked.bin": {"status": "unreadable", "size": PS2_IMAGE_SIZE},
            },
            ps2_bios_headers={f"{LRPS2_FOLDER}/scph70004.bin": PS2_HEADER_EUR},
        )
        assert folder.satisfied is True
        assert [c.code for c in answer.caveats] == [CAVEAT_FIRMWARE_IMAGE_IDENTIFIED, CAVEAT_FIRMWARE_UNREADABLE]

    def test_a_missing_folder_keeps_the_shape_that_says_what_to_create(self):
        answer, folder = self._lrps2(dirs=[BIOS_DIR])
        assert folder.found == "missing"
        # The point of the field: 'missing' alone reads as an absent dump.
        assert folder.declared_kind == DECLARED_DIRECTORY
        assert folder.satisfied is False
        assert answer.cores[0].requirements_met is False

    def test_a_file_where_the_core_opens_a_folder_reaches_nothing(self):
        answer, folder = self._lrps2({f"{BIOS_DIR}/pcsx2/bios": _blob(b"a dump in the folder's place")})
        assert folder.found == "file"
        assert folder.present is True
        # Established, not withheld: the core lists this path and a file has no
        # inside. And it is not a byte verdict — nothing was hashed.
        assert folder.satisfied is False
        assert folder.checked == CHECKED_UNKNOWN
        assert folder.identity is None
        wrong_shape = next(c for c in answer.caveats if c.code == CAVEAT_FIRMWARE_PATH_NOT_A_DIRECTORY)
        assert wrong_shape.data == {
            "path": f"{BIOS_DIR}/pcsx2/bios",
            "table_version": FIRMWARE_DECLARED_DIRECTORY_VERSION,
        }

    def test_the_retrodeck_link_lists_the_root_and_the_size_filter_drops_the_siblings(self):
        """RetroDECK links ``pcsx2/bios`` onto the firmware root — the stock case.

        The folder IS the root, so every other system's dumps are listed
        beside the PS2 image; the size test, first as it is in the core,
        drops the 512 KiB PlayStation image before a byte of it is read.
        """
        answer, folder = self._lrps2(
            {
                f"{BIOS_DIR}/scph1001.bin": _blob(b"a PlayStation image is 512 KiB, not 4 MiB"),
                f"{BIOS_DIR}/scph70004.bin": PS2_IMAGE_EUR,
            },
            symlinks={f"{BIOS_DIR}/pcsx2/bios": BIOS_DIR},
            ps2_bios_headers={f"{BIOS_DIR}/scph70004.bin": PS2_HEADER_EUR},
        )
        assert folder.path == BIOS_DIR
        assert folder.found == "directory"
        assert folder.satisfied is True
        assert [c.code for c in answer.caveats] == [CAVEAT_FIRMWARE_IMAGE_IDENTIFIED]
        assert answer.caveats[0].data["path"] == f"{BIOS_DIR}/scph70004.bin"

    def test_the_inventory_scan_does_not_restate_what_the_folder_read_accounted_for(self):
        """On a linked root the folder IS the scanned root, so ownership decides who states a file.

        A recognised image and a candidate whose bytes could not be read are
        the declaration's — stated once, by the read that saw them — and stay
        out of ``unclaimed``; a candidate read and failing the core's test,
        and a sibling the size filter dropped, are nobody's and are listed.
        """
        machine = _machine(
            {
                f"{INFO_DIR}/pcsx2_libretro.info": LRPS2_FOLDER_INFO,
                f"{INFO_DIR}/pcsx2_libretro.so": {"status": "invalid-text"},
                f"{BIOS_DIR}/scph70004.bin": PS2_IMAGE_EUR,
                f"{BIOS_DIR}/locked.bin": {"status": "unreadable", "size": PS2_IMAGE_SIZE},
                f"{BIOS_DIR}/other.bin": PS2_UNLISTED,
                f"{BIOS_DIR}/scph1001.bin": _blob(b"a PlayStation image is 512 KiB, not 4 MiB"),
            },
            symlinks={f"{BIOS_DIR}/pcsx2/bios": BIOS_DIR},
            ps2_bios_headers={f"{BIOS_DIR}/scph70004.bin": PS2_HEADER_EUR, f"{BIOS_DIR}/other.bin": NOT_A_BIOS},
        )
        answer = firmware_inventory(machine, _context(machine), verify=True)
        folder = next(r for r in answer.requirements if r.declared == "pcsx2/bios")
        assert folder.satisfied is True
        assert [c.code for c in answer.caveats] == [CAVEAT_FIRMWARE_IMAGE_IDENTIFIED, CAVEAT_FIRMWARE_UNREADABLE]
        assert [f.path for f in answer.unclaimed] == [f"{BIOS_DIR}/other.bin", f"{BIOS_DIR}/scph1001.bin"]

    def test_a_linked_candidate_is_claimed_by_its_target(self):
        """The scan compares resolved paths, so a claim must be made in that spelling.

        Two listed entries are links into ``store/``: the folder read states
        them under the names it listed — what the core lists and opens — and
        claims their targets, so the scan meets neither twice.
        """
        machine = _machine(
            {
                f"{INFO_DIR}/pcsx2_libretro.info": LRPS2_FOLDER_INFO,
                f"{INFO_DIR}/pcsx2_libretro.so": {"status": "invalid-text"},
                f"{BIOS_DIR}/store/scph70004.bin": PS2_IMAGE_EUR,
                f"{BIOS_DIR}/store/locked.bin": {"status": "unreadable", "size": PS2_IMAGE_SIZE},
                f"{BIOS_DIR}/other.bin": PS2_UNLISTED,
            },
            symlinks={
                f"{BIOS_DIR}/pcsx2/bios": BIOS_DIR,
                f"{BIOS_DIR}/ps2.bin": f"{BIOS_DIR}/store/scph70004.bin",
                f"{BIOS_DIR}/locked.bin": f"{BIOS_DIR}/store/locked.bin",
            },
            # Headers are keyed by the file the bytes belong to — the link's target.
            ps2_bios_headers={
                f"{BIOS_DIR}/store/scph70004.bin": PS2_HEADER_EUR,
                f"{BIOS_DIR}/other.bin": NOT_A_BIOS,
            },
        )
        answer = firmware_inventory(machine, _context(machine), verify=True)
        folder = next(r for r in answer.requirements if r.declared == "pcsx2/bios")
        assert folder.satisfied is True
        assert [(c.code, c.data["path"]) for c in answer.caveats] == [
            (CAVEAT_FIRMWARE_IMAGE_IDENTIFIED, f"{BIOS_DIR}/ps2.bin"),
            (CAVEAT_FIRMWARE_UNREADABLE, f"{BIOS_DIR}/locked.bin"),
        ]
        lrps2 = next(c for c in answer.cores if c.core_so == LRPS2_SO)
        assert lrps2.claims == (f"{BIOS_DIR}/store/locked.bin", f"{BIOS_DIR}/store/scph70004.bin")
        assert [f.path for f in answer.unclaimed] == [f"{BIOS_DIR}/other.bin"]

    def test_a_catalogue_that_lists_the_core_twice_reads_the_folder_once(self):
        """RetroDECK 0.10.9b's ES-DE catalogue lists ps2 twice on pcsx2_libretro.so (LRPS2, PCSX2).

        Both entries answer their own row with the same verdict; the folder is
        read once and what it holds is stated once.
        """
        catalogue = Catalogue(
            (
                CatalogueEntry(label="LRPS2", kind="libretro", core_so=LRPS2_SO),
                CatalogueEntry(label="PCSX2", kind="libretro", core_so=LRPS2_SO),
            )
        )
        tree: dict[str, FixtureFileSpec] = {
            f"{INFO_DIR}/pcsx2_libretro.info": LRPS2_FOLDER_INFO,
            f"{INFO_DIR}/pcsx2_libretro.so": {"status": "invalid-text"},
            f"{LRPS2_FOLDER}/scph70004.bin": PS2_IMAGE_EUR,
        }
        machine = _machine(tree, ps2_bios_headers={f"{LRPS2_FOLDER}/scph70004.bin": PS2_HEADER_EUR})
        for verify, code, verdict in (
            (False, CAVEAT_FIRMWARE_SEARCH_UNVERIFIED, None),
            (True, CAVEAT_FIRMWARE_IMAGE_IDENTIFIED, True),
        ):
            answer = firmware_for_system(
                machine, _context(machine), system="ps2", catalogue=catalogue, verify=verify
            )
            assert [c.label for c in answer.cores] == ["LRPS2", "PCSX2"]
            verdicts = [r.satisfied for core in answer.cores for r in _plain_requirements(core)]
            assert verdicts == [verdict, verdict]
            assert [c.code for c in answer.caveats] == [code]

    def test_the_count_sentences_read_for_one_file_and_for_many(self):
        """The two counted messages are written around the count, never with a '(s)'."""
        three: dict[str, FixtureFileSpec] = {
            f"{LRPS2_FOLDER}/a.bin": PS2_UNLISTED,
            f"{LRPS2_FOLDER}/b.bin": PS2_UNLISTED,
            f"{LRPS2_FOLDER}/c.bin": PS2_UNLISTED,
        }
        no_bios = {path: NOT_A_BIOS for path in three}
        answer, _ = self._lrps2({f"{LRPS2_FOLDER}/scph70004.bin": PS2_IMAGE_EUR}, verify=False)
        assert answer.caveats[0].message.startswith(
            f"{LRPS2_FOLDER} holds one file of a size this core accepts, and whether it is a BIOS is a question "
            "about its bytes"
        )
        answer, _ = self._lrps2(three, verify=False)
        assert answer.caveats[0].message.startswith(
            f"{LRPS2_FOLDER} holds 3 files of a size this core accepts; which of them is a BIOS is a question "
            "about their bytes"
        )
        answer, _ = self._lrps2(
            {f"{LRPS2_FOLDER}/a.bin": PS2_UNLISTED}, ps2_bios_headers={f"{LRPS2_FOLDER}/a.bin": NOT_A_BIOS}
        )
        assert answer.caveats[0].code == CAVEAT_FIRMWARE_DIRECTORY_HOLDS_NO_IMAGE
        assert answer.caveats[0].message.startswith(
            f"one file in {LRPS2_FOLDER} is of a size this core accepts and it does not read as a PS2 BIOS"
        )
        assert "fails it — so the listing" in answer.caveats[0].message
        answer, _ = self._lrps2(three, ps2_bios_headers=no_bios)
        assert answer.caveats[0].message.startswith(
            f"3 files in {LRPS2_FOLDER} are of a size this core accepts and none of them reads as a PS2 BIOS"
        )
        assert "fails every one — so the listing" in answer.caveats[0].message
        assert answer.caveats[0].data["candidates"] == "3"
        assert answer.caveats[0].data["paths"] == tuple(sorted(three))

    def test_a_contents_verdict_is_refused_off_a_listed_folder(self):
        """The internal field can only state a folder that was listed — anywhere else it would lie."""
        build = functools.partial(
            FirmwareRequirement,
            core_so=LRPS2_SO,
            system="ps2",
            system_source="systemname",
            need=NEED_REQUIRED,
            file_name="bios",
            path=LRPS2_FOLDER,
            declared="pcsx2/bios",
            description="folder",
            identity=None,
            checked=CHECKED_UNKNOWN,
        )
        with pytest.raises(ValueError, match="contents_satisfied"):
            build(found="file", declared_kind=DECLARED_DIRECTORY, contents_satisfied=True)
        with pytest.raises(ValueError, match="contents_satisfied"):
            build(found="directory", declared_kind=DECLARED_FILE, contents_satisfied=False)
        # And over a listed folder it is exactly what ``satisfied`` answers.
        listed = build(found="directory", declared_kind=DECLARED_DIRECTORY, contents_satisfied=False)
        assert listed.satisfied is False

    def test_a_declaration_the_table_does_not_name_is_a_file(self):
        """The default, and nothing was read to reach it.

        RetroArch draws no such distinction of its own — its presence check is
        one ``path_is_valid`` stat over the composed name
        (core_info.c:2381-2383), which answers alike for both shapes — so a
        missing row costs exactly what atlas answered before the table.
        """
        machine = _machine({f"{BIOS_DIR}/scph5501.bin": _blob(b"12345678")})
        answer = firmware_for_core(machine, _context(machine), core_so="demo_psx_libretro.so")
        assert {r.declared_kind for r in answer.requirements} == {DECLARED_FILE}

    def test_a_card_declared_requirement_is_a_file(self):
        """A requirement built without the field is a file, not an error.

        The card route never passes it (:func:`_packaged_standalone_core`), so
        the default is what its requirements carry — pinned here on a directly
        constructed requirement, which is the seam the default lives at.
        """
        assert (
            FirmwareRequirement(
                core_so=None,
                system="wiiu",
                system_source="card",
                need=NEED_REQUIRED,
                file_name="keys.txt",
                path="/keys.txt",
                declared="keys.txt",
                description="title keys",
                identity=None,
                found="missing",
                checked=None,
            ).declared_kind
            == DECLARED_FILE
        )

    def test_a_word_outside_the_vocabulary_is_refused(self):
        with pytest.raises(ValueError, match="declared_kind"):
            FirmwareRequirement(
                core_so="x_libretro.so",
                system="ps2",
                system_source="systemname",
                need=NEED_REQUIRED,
                file_name="bios",
                path="/bios/pcsx2/bios",
                declared="pcsx2/bios",
                description="",
                identity=None,
                found="missing",
                checked=None,
                declared_kind="folder",  # type: ignore[arg-type]
            )


class TestAConfiguredNameNamesWhatTheLaunchOpens:
    """``pcsx2_bios``: once a name is stored, the launch opens one file and the folder is not judged.

    ``retro_init`` lists the folder whenever the core has not read a value yet
    (libretro/main.cpp:1801 at 14d19f8) — so the listing keeps happening, to
    fill the option's own values — while ``check_variables`` writes the stored
    value to ``Filenames/BIOS`` (:360-365), ``FullpathToBios`` joins it onto
    the folder (pcsx2/Pcsx2Config.cpp:994-1000) and ``LoadBIOS`` opens that one
    path (pcsx2/ps2/BiosTools.cpp:281). It falls back to the listing where its
    own stat says no (:270-278), and the state atlas can state as that search
    is the one below: nothing at the composed path.
    """

    OPTIONS = "/cfg/retroarch-core-options.cfg"
    OPT_DIR = "/cfg/config"
    IMAGE = f"{LRPS2_FOLDER}/scph70004.bin"

    def _answer(
        self,
        options: str | None = None,
        *,
        files: Mapping[str, FixtureFileSpec] | None = None,
        per_core: str | None = None,
        per_core_options: bool = False,
        chain: CoreOptionsChain | None = None,
        no_chain: bool = False,
        verify: bool = True,
        **kwargs: object,
    ):
        tree: dict[str, FixtureFileSpec] = {
            f"{INFO_DIR}/pcsx2_libretro.info": LRPS2_FOLDER_INFO,
            f"{INFO_DIR}/pcsx2_libretro.so": {"status": "invalid-text"},
            self.IMAGE: PS2_IMAGE_EUR,
        }
        if options is not None:
            tree[self.OPTIONS] = options
        if per_core is not None:
            tree[f"{self.OPT_DIR}/PCSX2/PCSX2.opt"] = per_core
        tree.update(files or {})
        machine = _machine(tree, ps2_bios_headers={self.IMAGE: PS2_HEADER_EUR}, **kwargs)
        if chain is None and not no_chain:
            chain = CoreOptionsChain(
                global_file=self.OPTIONS,
                override_config_dir=self.OPT_DIR,
                per_core_options=per_core_options,
                core_dir=INFO_DIR,
            )
        return firmware_for_core(
            machine, _context(machine, core_options=chain), core_so=LRPS2_SO, verify=verify
        )

    def _entry(self, answer) -> FirmwareRequirement:
        (entry,) = _plain_requirements(answer.cores[0])
        return entry

    def test_the_named_file_is_the_requirement_and_the_folder_is_not_judged(self):
        answer = self._answer('pcsx2_bios = "scph70004.bin"\n')
        entry = self._entry(answer)
        # The declaration is still the folder — the name came from a setting,
        # which is what the caveat beside it says — and the destination is the
        # one file the launch opens.
        assert entry.declared == "pcsx2/bios"
        assert entry.declared_kind == DECLARED_FILE
        assert entry.file_name == "scph70004.bin"
        assert entry.path == self.IMAGE
        assert entry.found == "file"
        assert entry.checked == CHECKED_VERIFIED
        assert entry.satisfied is True
        # By CONTENT under the row's prefix: the core takes any name inside the
        # folder, so what the file is called says nothing about its bytes.
        assert entry.identity is not None
        assert entry.identity.known_as == ("pcsx2/bios/ps2-0200e-20040614.bin",)
        # No folder verdict was reached, so none of the folder's codes appear.
        assert [c.code for c in answer.caveats] == []
        (stated,) = answer.cores[0].caveats
        assert stated.code == CAVEAT_FIRMWARE_IMAGE_CONFIGURED
        assert dict(stated.data) == {
            "core": "pcsx2",
            "key": "pcsx2_bios",
            "name": "scph70004.bin",
            "options_file": self.OPTIONS,
        }

    def test_the_named_file_is_claimed_so_the_scan_does_not_restate_it(self):
        answer = self._answer('pcsx2_bios = "scph70004.bin"\n')
        assert answer.cores[0].claims == (self.IMAGE,)

    def test_bytes_the_packaged_prefix_does_not_know_stay_open(self):
        # The core opens the configured file whatever its header reads as
        # (BiosTools.cpp:281, the ROMDIR walk at :294 discarding its verdict),
        # so an unlisted image is neither a failure nor a green light.
        answer = self._answer(
            'pcsx2_bios = "mystery.bin"\n', files={f"{LRPS2_FOLDER}/mystery.bin": PS2_UNLISTED}
        )
        entry = self._entry(answer)
        assert entry.checked == CHECKED_UNRECOGNISED
        assert entry.identity is None
        assert entry.satisfied is None

    def test_without_a_content_check_the_named_file_is_unchecked(self):
        answer = self._answer('pcsx2_bios = "scph70004.bin"\n', verify=False)
        entry = self._entry(answer)
        assert entry.checked == CHECKED_UNCHECKED
        assert entry.satisfied is None

    def test_bytes_that_do_not_come_back_are_unread_and_stated(self):
        answer = self._answer(
            'pcsx2_bios = "locked.bin"\n',
            files={f"{LRPS2_FOLDER}/locked.bin": {"status": "unreadable", "size": PS2_IMAGE_SIZE}},
        )
        entry = self._entry(answer)
        assert entry.checked == CHECKED_UNREAD
        assert entry.satisfied is None
        assert [c.code for c in answer.caveats] == [CAVEAT_FIRMWARE_UNREADABLE]

    def test_a_name_at_no_file_is_stale_and_the_folder_answers(self):
        answer = self._answer('pcsx2_bios = "gone.bin"\n')
        entry = self._entry(answer)
        # The state atlas can state as a search: nothing at the composed path,
        # so the folder verdict is what the launch rests on and it is unchanged.
        assert entry.declared_kind == DECLARED_DIRECTORY
        assert entry.found == "directory"
        assert entry.satisfied is True
        assert [c.code for c in answer.caveats] == [CAVEAT_FIRMWARE_IMAGE_IDENTIFIED]
        (stale,) = answer.cores[0].caveats
        assert stale.code == CAVEAT_FIRMWARE_CONFIGURED_IMAGE_MISSING
        assert dict(stale.data) == {
            "core": "pcsx2",
            "key": "pcsx2_bios",
            "name": "gone.bin",
            "dir": LRPS2_FOLDER,
        }

    def test_a_directory_at_the_configured_name_is_not_searched_past(self):
        """The core's test is a stat that succeeded, and a directory passes it.

        ``LoadBIOS`` searches the folder where the path is empty or where
        ``path_is_valid`` says no (BiosTools.cpp:270), and that call returns 0
        for an empty path or where ``stat`` itself fails (file_path_io.c:87-90
        with vfs_implementation.c:848-849, :941-952). A directory there passes
        the stat, so the core does not search past it: the configured file
        stays the requirement, the shape is stated, and no folder verdict and
        no green light stands over it.
        """
        answer = self._answer(
            'pcsx2_bios = "a-folder"\n', dirs=[f"{LRPS2_FOLDER}/a-folder"]
        )
        entry = self._entry(answer)
        assert entry.declared_kind == DECLARED_FILE
        assert entry.path == f"{LRPS2_FOLDER}/a-folder"
        assert entry.found == "directory"
        assert entry.checked == CHECKED_UNKNOWN
        assert entry.satisfied is None
        assert [c.code for c in answer.caveats] == [CAVEAT_FIRMWARE_PATH_OBSTRUCTED]
        assert [c.code for c in answer.cores[0].caveats] == [CAVEAT_FIRMWARE_IMAGE_CONFIGURED]

    def test_a_configured_name_atlas_cannot_stat_establishes_neither_branch(self):
        """The core decides by that same stat, so which branch it takes is the open question.

        Answering it with the folder would claim the core searched; answering
        it with a missing file would claim it did not. The configured file
        stays the requirement and the look that did not happen is stated.
        """
        answer = self._answer(
            'pcsx2_bios = "locked.bin"\n', inaccessible=[f"{LRPS2_FOLDER}/locked.bin"]
        )
        entry = self._entry(answer)
        assert entry.declared_kind == DECLARED_FILE
        assert entry.found == "inaccessible"
        assert entry.checked is None
        assert entry.satisfied is None
        assert [c.code for c in answer.caveats] == [CAVEAT_FIRMWARE_PATH_INACCESSIBLE]
        assert [c.code for c in answer.cores[0].caveats] == [CAVEAT_FIRMWARE_IMAGE_CONFIGURED]

    def test_a_value_with_a_separator_is_followed_the_way_the_core_follows_it(self):
        """Nothing normalises, so a climb that stays under the root is followed, not refused.

        ``Path::Combine`` resolves no component of its own, so the value
        composes literally and where it lands is the kernel's answer — which
        is the file the core opens. The requirement's ``path`` then names a
        file OUTSIDE the declared folder while ``declared`` stays the folder,
        and the claim follows the path so the unclaimed scan does not meet it
        again.
        """
        outside = f"{BIOS_DIR}/pcsx2/elsewhere.bin"
        answer = self._answer(
            'pcsx2_bios = "../elsewhere.bin"\n', files={outside: PS2_IMAGE_EUR}
        )
        entry = self._entry(answer)
        assert entry.declared == "pcsx2/bios"
        assert entry.path == outside
        assert entry.file_name == "elsewhere.bin"
        assert entry.checked == CHECKED_VERIFIED
        assert answer.cores[0].claims == (outside,)
        assert [c.code for c in answer.cores[0].caveats] == [CAVEAT_FIRMWARE_IMAGE_CONFIGURED]

    def test_an_options_file_stating_no_value_leaves_the_folder_verdict_alone(self):
        answer = self._answer('pcsx2_fastboot = "enabled"\n')
        assert self._entry(answer).declared_kind == DECLARED_DIRECTORY
        assert answer.cores[0].caveats == ()

    def test_no_options_file_at_all_leaves_the_folder_verdict_alone(self):
        # There is no default to fall back on and there could not be: the core
        # fills this option's default from the folder it has just listed
        # (main.cpp:1832-1834), which is not a stored value.
        answer = self._answer()
        assert self._entry(answer).declared_kind == DECLARED_DIRECTORY
        assert answer.cores[0].caveats == ()

    def test_an_empty_value_is_unset(self):
        answer = self._answer('pcsx2_bios = ""\n')
        assert self._entry(answer).declared_kind == DECLARED_DIRECTORY
        assert answer.cores[0].caveats == ()

    def test_a_context_carrying_no_chain_answers_as_it_did_before(self):
        answer = self._answer(no_chain=True)
        assert self._entry(answer).declared_kind == DECLARED_DIRECTORY
        assert answer.cores[0].caveats == ()

    def test_the_per_core_file_outranks_the_global_one(self):
        # RetroArch reads the per-core .opt first unless global_core_options
        # switched it on, and the file is named for the core's library_name —
        # which lives in the binary, so reading it costs the same probe
        # RetroArch makes.
        answer = self._answer(
            'pcsx2_bios = "scph70004.bin"\n',
            per_core='pcsx2_bios = "mystery.bin"\n',
            per_core_options=True,
            files={f"{LRPS2_FOLDER}/mystery.bin": PS2_UNLISTED},
            cores={f"{INFO_DIR}/pcsx2_libretro.so": {"library_name": "PCSX2"}},
        )
        entry = self._entry(answer)
        assert entry.file_name == "mystery.bin"
        assert entry.checked == CHECKED_UNRECOGNISED

    def test_a_core_that_cannot_be_queried_says_the_per_core_file_went_unread(self):
        answer = self._answer(
            'pcsx2_bios = "scph70004.bin"\n',
            per_core='pcsx2_bios = "mystery.bin"\n',
            per_core_options=True,
            files={f"{LRPS2_FOLDER}/mystery.bin": PS2_UNLISTED},
        )
        # The global file answered, and what could have outranked it was not read.
        assert self._entry(answer).file_name == "scph70004.bin"
        assert [c.code for c in answer.cores[0].caveats] == [
            CAVEAT_CORE_UNQUERYABLE,
            CAVEAT_FIRMWARE_IMAGE_CONFIGURED,
        ]

    def test_no_per_core_directory_means_no_probe_and_no_statement(self):
        """A degradation that cannot bite is not stated: with nothing at the
        override directory there is no per-core file to miss, whatever the core
        calls itself."""
        answer = self._answer(
            'pcsx2_bios = "scph70004.bin"\n',
            per_core_options=True,
        )
        assert [c.code for c in answer.cores[0].caveats] == [CAVEAT_FIRMWARE_IMAGE_CONFIGURED]

    def test_an_absolute_value_lands_below_the_folder(self):
        # Path::Combine swallows the leading separator of an absolute name
        # (common/FileSystem.cpp:442-457 at 14d19f8, ported as
        # qt_ini.path_combine), where os.path.join would let it replace the
        # folder entirely.
        answer = self._answer('pcsx2_bios = "/scph70004.bin"\n')
        assert self._entry(answer).path == self.IMAGE

    def test_a_value_climbing_out_of_the_root_is_refused_rather_than_followed(self):
        answer = self._answer('pcsx2_bios = "../../../elsewhere/scph70004.bin"\n')
        entry = self._entry(answer)
        # No destination is stated for it, so the folder's own listing is what
        # this answer carries.
        assert entry.declared_kind == DECLARED_DIRECTORY
        (refusal,) = answer.cores[0].caveats
        assert refusal.code == CAVEAT_FIRMWARE_PATH_ESCAPES_ROOT
        assert dict(refusal.data) == {
            "core_so": LRPS2_SO,
            "declared": "../../../elsewhere/scph70004.bin",
            "key": "pcsx2_bios",
        }

    def test_identification_still_names_the_declared_folder(self):
        """A setting says which image the next launch opens, not where a file belongs.

        The download flow asks about content: the core takes any name inside
        the folder and the option is one edit away from naming another, so the
        destination stays the declared folder whatever it currently says.
        """
        tree: dict[str, FixtureFileSpec] = {
            f"{INFO_DIR}/pcsx2_libretro.info": LRPS2_FOLDER_INFO,
            f"{INFO_DIR}/pcsx2_libretro.so": {"status": "invalid-text"},
            self.IMAGE: PS2_IMAGE_EUR,
            self.OPTIONS: 'pcsx2_bios = "scph70004.bin"\n',
        }
        machine = _machine(tree, ps2_bios_headers={self.IMAGE: PS2_HEADER_EUR})
        context = _context(
            machine,
            core_options=CoreOptionsChain(
                global_file=self.OPTIONS,
                override_config_dir=self.OPT_DIR,
                per_core_options=False,
                core_dir=INFO_DIR,
            ),
        )
        identified = identify_firmware(machine, context, md5="77" * 16)
        (entry,) = identified.requirements
        assert entry.declared == "pcsx2/bios"
        assert entry.declared_kind == DECLARED_DIRECTORY
        assert entry.path == LRPS2_FOLDER

    def test_what_resolving_the_options_chain_cost_reaches_the_answer(self):
        # A line RetroArch's parser refused degrades every value read through
        # the chain, and this answer reads one — stated whatever the read came
        # back with, because a refused line is one reason a value is missing.
        dropped = Caveat(CAVEAT_CFG_LINE_DROPPED, "a line the parser refused", {"key": "x"})
        answer = self._answer(
            chain=CoreOptionsChain(
                global_file=self.OPTIONS,
                override_config_dir=self.OPT_DIR,
                per_core_options=False,
                caveats=(dropped,),
            )
        )
        assert CAVEAT_CFG_LINE_DROPPED in [c.code for c in answer.caveats]


class TestResolutionIsTheKernelsOrder:
    """Symlink cases — the ones a lexical check cannot reach, which is the point."""

    def _escaping(self, declared: str, **kwargs: object) -> FixtureMachine:
        info = (
            'systemname = "Sony - PlayStation"\n'
            "firmware_count = 1\n"
            f'firmware0_path = "{declared}"\n'
            'firmware0_opt = "false"\n'
        )
        tree: dict[str, FixtureFileSpec] = {
            f"{INFO_DIR}/escape_libretro.info": info,
            f"{INFO_DIR}/escape_libretro.so": {"status": "invalid-text"},
            "/etc/shadow": "root:!:0:0:::",
            f"{BIOS_DIR}/pcsx2/scph5501.bin": _blob(b"12345678"),
            "/elsewhere/scph5501.bin": _blob(b"12345678"),
        }
        return FixtureMachine(tree, **kwargs)  # type: ignore[arg-type]

    def test_a_symlinked_component_is_followed_before_the_bound_is_checked(self):
        machine = self._escaping("etclink/shadow", symlinks={f"{BIOS_DIR}/etclink": "/etc"})
        answer = firmware_for_core(machine, _context(machine), core_so="escape_libretro.so")
        assert answer.requirements == ()
        assert [r.reason for r in answer.cores[0].refused] == [CAVEAT_FIRMWARE_PATH_ESCAPES_ROOT]

    def test_dotdot_applies_to_where_the_link_landed_not_to_the_spelling(self):
        """The kernel resolves, then walks up. Collapsing '..' first is a different path.

        With ``pcsx2/bios`` linked to the firmware root, ``pcsx2/bios/../x`` is
        ``<root>/../x`` — outside. A lexical reading answers ``<root>/pcsx2/x``,
        which exists here and would verify: a green light for a file the core
        never opens.
        """
        machine = self._escaping(
            "pcsx2/bios/../scph5501.bin", symlinks={f"{BIOS_DIR}/pcsx2/bios": BIOS_DIR}
        )
        answer = firmware_for_core(machine, _context(machine), core_so="escape_libretro.so", verify=True)
        assert answer.requirements == (), "the lexical reading would have verified /bios/pcsx2/scph5501.bin"
        assert [r.reason for r in answer.cores[0].refused] == [CAVEAT_FIRMWARE_PATH_ESCAPES_ROOT]
        assert answer.cores[0].requirements_met is None

    def test_the_root_itself_is_inside_the_root(self):
        # RetroDECK links bios/pcsx2/bios back to the firmware root; an earlier
        # revision refused exactly this and broke a stock installation.
        machine = FixtureMachine(
            {
                f"{INFO_DIR}/pcsx2_libretro.info": (
                    'systemname = "Sony - PlayStation"\n'
                    "firmware_count = 1\n"
                    'firmware0_path = "pcsx2/bios/scph5501.bin"\n'
                    'firmware0_opt = "false"\n'
                ),
                f"{INFO_DIR}/pcsx2_libretro.so": {"status": "invalid-text"},
                f"{BIOS_DIR}/scph5501.bin": _blob(b"12345678"),
            },
            symlinks={f"{BIOS_DIR}/pcsx2/bios": BIOS_DIR},
        )
        answer = firmware_for_core(machine, _context(machine), core_so="pcsx2_libretro.so")
        requirement = answer.requirements[0]
        assert requirement.declared == "pcsx2/bios/scph5501.bin"
        # Resolved, so this and a direct declaration are ONE destination — a
        # placing client cannot end up writing two copies.
        assert requirement.path == f"{BIOS_DIR}/scph5501.bin"
        assert requirement.found == "file"

    def test_an_unresolvable_path_is_refused_for_its_own_reason(self):
        machine = self._escaping(
            "loop/scph5501.bin",
            symlinks={f"{BIOS_DIR}/loop": f"{BIOS_DIR}/loop2", f"{BIOS_DIR}/loop2": f"{BIOS_DIR}/loop"},
        )
        answer = firmware_for_core(machine, _context(machine), core_so="escape_libretro.so")
        refused = answer.cores[0].refused[0]
        assert refused.reason == CAVEAT_FIRMWARE_PATH_UNRESOLVABLE
        assert [c.code for c in answer.cores[0].caveats] == [CAVEAT_FIRMWARE_PATH_UNRESOLVABLE]
        assert answer.cores[0].requirements_met is None

    def test_an_escaping_declaration_never_widens_the_unclaimed_scan(self):
        machine = self._escaping("etclink/shadow", symlinks={f"{BIOS_DIR}/etclink": "/etc"})
        paths = [f.path for f in firmware_inventory(machine, _context(machine), verify=True).unclaimed]
        assert paths == [f"{BIOS_DIR}/pcsx2/scph5501.bin"] or paths == []
        assert not any(p.startswith("/etc") for p in paths)

    def test_a_destination_cannot_be_both_or_neither(self):
        # The docstring's invariant, enforced where the object is built.
        for kwargs in ({}, {"path": "/bios/x.bin", "refusal": CAVEAT_FIRMWARE_PATH_ESCAPES_ROOT}):
            with pytest.raises(ValueError):
                Destination(**kwargs)  # type: ignore[arg-type]

    def test_resolve_links_follows_the_seam(self):
        machine = FixtureMachine({"/real/f.bin": "x"}, symlinks={"/via": "/real", "/real/inner": "/real"})
        assert resolve_links(machine, "/via/f.bin") == "/real/f.bin"
        assert resolve_links(machine, "/via/inner/f.bin") == "/real/f.bin"
        assert resolve_links(machine, "/plain/path") == "/plain/path"
        assert resolve_links(machine, "/via/../real/f.bin") == "/real/f.bin"


class TestSystemAssignmentIsVisible:
    """A file filed by what its *core* is called must say so.

    Only the per-file override knows which machine a dump belongs to; every
    other route files a file by its core's ``systemname``, which holds exactly
    while the core covers one system. The ``.info`` states when it does not.
    """

    # What this class is about, named so the tests can assert over it. These
    # are the codes the two assignment readers emit —
    # `system_assignment_caveats` (derived, and no systemname at all) and
    # `catalogue_vocabulary_caveats` (atlas's own spelling for a system no
    # ES-DE build declares) — plus the may-hide statement a system query adds.
    # Derived from those functions rather than guessed at.
    ASSIGNMENT_CODES = (
        CAVEAT_SYSTEM_ASSIGNMENT_DERIVED,
        CAVEAT_CORE_WITHOUT_SYSTEMNAME,
        CAVEAT_SYSTEM_NOT_IN_CATALOGUE,
        CAVEAT_SYSTEM_ASSIGNMENT_MAY_HIDE_CORES,
    )

    def _assignment_codes(self, core: CoreFirmware) -> list[str]:
        return [c.code for c in core.caveats if c.code in self.ASSIGNMENT_CODES]

    def test_a_multi_system_core_falling_back_states_it(self):
        machine = _machine({f"{INFO_DIR}/noods_libretro.info": NOODS_INFO,
                            f"{INFO_DIR}/noods_libretro.so": {"status": "invalid-text"}})
        core = firmware_for_core(machine, _context(machine), core_so="noods_libretro.so").cores[0]
        caveat = next(c for c in core.caveats if c.code == CAVEAT_SYSTEM_ASSIGNMENT_DERIVED)
        # gba_bios.bin has a per-file rule; firmware.bin does not.
        assert caveat.data["files"] == ("firmware.bin",)
        # The systems atlas parsed out of the .info, not the .info's own
        # pipe-joined spelling re-made — a client wants what was read.
        assert caveat.data["database"] == (
            "Nintendo - Nintendo DS",
            "Nintendo - Nintendo DS (Download Play)",
        )

    def test_the_rule_beats_the_core_name_it_disagrees_with(self):
        # The defect the two names were admitted for: NooDS is offered as a GBA
        # emulator and its .info can only say "Nintendo DS", so without a rule
        # its GBA BIOS is filed under nds. The rule files it under gba and says
        # that is where the assignment came from.
        machine = _machine({f"{INFO_DIR}/noods_libretro.info": NOODS_INFO,
                            f"{INFO_DIR}/noods_libretro.so": {"status": "invalid-text"}})
        core = firmware_for_core(machine, _context(machine), core_so="noods_libretro.so").cores[0]
        filed = {r.file_name: (r.system, r.system_source) for r in _plain_requirements(core)}
        assert filed["gba_bios.bin"] == ("gba", SOURCE_OVERRIDE)
        assert filed["firmware.bin"] == ("nds", SOURCE_SYSTEMNAME)

    def test_full_override_coverage_states_nothing(self):
        machine = _machine({f"{INFO_DIR}/covered_libretro.info": FULLY_OVERRIDDEN_INFO,
                            f"{INFO_DIR}/covered_libretro.so": {"status": "invalid-text"}})
        core = firmware_for_core(machine, _context(machine), core_so="covered_libretro.so").cores[0]
        assert core.caveats == ()

    def test_a_single_system_core_states_nothing(self):
        # The fallback is sound when the core covers one system, however many
        # of its files lack a per-file rule.
        machine = _machine()
        core = firmware_for_core(machine, _context(machine), core_so="demo_psx_libretro.so").cores[0]
        assert self._assignment_codes(core) == []
        # The whole list is still accounted for, so nothing else can creep in
        # unnoticed: what this PlayStation core does carry is the statement
        # that its system's firmware requirement is world knowledge, which is
        # a different subject from how its files were filed.
        assert [c.code for c in core.caveats] == [CAVEAT_SYSTEM_FIRMWARE_WORLD_KNOWLEDGE]

    def test_a_core_without_a_systemname_is_its_own_case(self):
        machine = _machine({f"{INFO_DIR}/skyemu_libretro.info": SKYEMU_INFO,
                            f"{INFO_DIR}/skyemu_libretro.so": {"status": "invalid-text"}})
        answer = firmware_for_core(machine, _context(machine), core_so="skyemu_libretro.so")
        core = answer.cores[0]
        assert [c.code for c in core.caveats] == [CAVEAT_CORE_WITHOUT_SYSTEMNAME]
        assert [r.system for r in _plain_requirements(core) if r.file_name == "nds7.bin"] == ["_unknown"]
        # The override still applies where it has a rule, so this is not a
        # blanket "we know nothing about this core".
        assert [r.system for r in _plain_requirements(core) if r.file_name == "cgb_boot.bin"] == ["gbc"]

    def test_a_core_declaring_nothing_has_nothing_to_be_unsure_about(self):
        machine = _machine({f"{INFO_DIR}/snes9x_libretro.info": NO_FIRMWARE_INFO,
                            f"{INFO_DIR}/snes9x_libretro.so": {"status": "invalid-text"}})
        core = firmware_for_core(machine, _context(machine), core_so="snes9x_libretro.so").cores[0]
        assert core.caveats == ()

    def test_a_systemname_naming_several_machines_is_evidence_too(self):
        # jollycv and mGBA carry the same shape of systemname; honouring the
        # evidence for one and ignoring it for the other is the asymmetry this
        # closes. No database field here at all.
        info = (
            'systemname = "ColecoVision/CreatiVision/My Vision"\n'
            "firmware_count = 1\n"
            'firmware0_path = "bioscv.rom"\n'
        )
        machine = _machine({f"{INFO_DIR}/jollycv_libretro.info": info,
                            f"{INFO_DIR}/jollycv_libretro.so": {"status": "invalid-text"}})
        core = firmware_for_core(machine, _context(machine), core_so="jollycv_libretro.so").cores[0]
        assert [c.code for c in core.caveats] == [CAVEAT_SYSTEM_ASSIGNMENT_DERIVED]

    def test_two_disagreeing_sources_are_evidence_too(self):
        # One entry each, and they name different machines — a reason to trust
        # neither blindly. No shipped core trips this since map version 2
        # turned the vice_x128 pair into agreement, so the pair here is
        # synthetic; the reading is kept because the next info set can
        # disagree again.
        info = (
            'systemname = "Sega Master System"\n'
            'database = "Sega - Game Gear"\n'
            "firmware_count = 1\n"
            'firmware0_path = "bios.sms"\n'
        )
        machine = _machine({f"{INFO_DIR}/disagree_libretro.info": info,
                            f"{INFO_DIR}/disagree_libretro.so": {"status": "invalid-text"}})
        core = firmware_for_core(machine, _context(machine), core_so="disagree_libretro.so").cores[0]
        assert [c.code for c in core.caveats] == [CAVEAT_SYSTEM_ASSIGNMENT_DERIVED]

    def test_the_c128_pair_agrees_since_the_catalogue_files_it_under_c64(self):
        # The historical disagreement example: systemname "C128" against a
        # database naming the C64. The deployed catalogue's own c64 entry
        # launches vice_x128, so map version 2 files "C128" under c64 — and
        # the two sources now agree, which is the correct silence.
        info = (
            'systemname = "C128"\n'
            'database = "C64"\n'
            "firmware_count = 1\n"
            'firmware0_path = "kernal"\n'
        )
        machine = _machine({f"{INFO_DIR}/vice_x128_libretro.info": info,
                            f"{INFO_DIR}/vice_x128_libretro.so": {"status": "invalid-text"}})
        answer = firmware_for_core(machine, _context(machine), core_so="vice_x128_libretro.so")
        core = answer.cores[0]
        assert core.caveats == ()
        assert _plain_requirements(core)[0].system == "c64"

    def test_the_disagreement_check_needs_both_names_to_be_mappable(self):
        """The known limit of that reading, pinned rather than glossed over.

        On the real machine vice_x128's database says ``Commodore - 64``, which
        the systemname map does not know — so the two cannot be compared and
        this core stays silent. Mapping the database vocabulary would mean
        maintaining the second table this design refuses to grow, so the check
        covers the names already known and no more.
        """
        info = (
            'systemname = "C128"\n'
            'database = "Commodore - 64"\n'
            "firmware_count = 1\n"
            'firmware0_path = "kernal"\n'
        )
        machine = _machine({f"{INFO_DIR}/vice_x128_libretro.info": info,
                            f"{INFO_DIR}/vice_x128_libretro.so": {"status": "invalid-text"}})
        core = firmware_for_core(machine, _context(machine), core_so="vice_x128_libretro.so").cores[0]
        assert core.caveats == ()

    def test_one_agreeing_source_pair_states_nothing(self):
        info = (
            'systemname = "Sony - PlayStation"\n'
            'database = "Sony - PlayStation"\n'
            "firmware_count = 1\n"
            'firmware0_path = "scph5501.bin"\n'
        )
        machine = _machine({f"{INFO_DIR}/agree_libretro.info": info,
                            f"{INFO_DIR}/agree_libretro.so": {"status": "invalid-text"}})
        core = firmware_for_core(machine, _context(machine), core_so="agree_libretro.so").cores[0]
        assert self._assignment_codes(core) == []
        assert [c.code for c in core.caveats] == [CAVEAT_SYSTEM_FIRMWARE_WORLD_KNOWLEDGE]

    def test_a_system_query_names_the_cores_a_derived_slug_may_hide(self):
        # Without a catalogue the selection is keyed on the cores' own
        # systemname, so a core filed under the wrong slug is unreachable AND
        # its caveat never gets attached. The answer names the candidates.
        machine = _machine({f"{INFO_DIR}/atari800_libretro.info": ATARI800_INFO,
                            f"{INFO_DIR}/atari800_libretro.so": {"status": "invalid-text"}})
        answer = firmware_for_system(machine, _context(machine), system="atari5200")
        assert answer.cores == ()
        hiding = next(c for c in answer.caveats if c.code == CAVEAT_SYSTEM_ASSIGNMENT_MAY_HIDE_CORES)
        assert hiding.data["cores"] == ("atari800_libretro.so",)

    def test_it_names_only_cores_whose_own_database_covers_the_question(self):
        # NooDS is derived too — firmware.bin carries no per-file rule — and it
        # is not selected for this question either, so only the database term
        # keeps it out of the list: nothing on the machine says it covers the
        # Atari 5200, and naming it would train the reader to skip the line.
        # The core has to stay derived for that term to be the one under test,
        # which is what the first assertion holds.
        machine = _machine({f"{INFO_DIR}/noods_libretro.info": NOODS_INFO,
                            f"{INFO_DIR}/noods_libretro.so": {"status": "invalid-text"}})
        cores = read_core_declarations(machine, INFO_DIR, core_dir=INFO_DIR).cores
        noods = next(c for c in cores if c.stem == "noods_libretro")
        assert system_assignment_caveats(noods) != ()
        answer = firmware_for_system(machine, _context(machine), system="atari5200")
        assert CAVEAT_SYSTEM_ASSIGNMENT_MAY_HIDE_CORES not in [c.code for c in answer.caveats]

    def test_a_system_query_that_reaches_every_core_hides_nothing(self):
        machine = _gb_machine()
        answer = firmware_for_system(machine, _context(machine), system="gb")
        assert CAVEAT_SYSTEM_ASSIGNMENT_MAY_HIDE_CORES not in [c.code for c in answer.caveats]

    def test_the_source_of_every_assignment_is_recorded(self):
        assert system_decision("gb_bios.bin", "Game Boy/Game Boy Color") == ("gb", "override")
        assert system_decision("x.bin", "Sega - Dreamcast") == ("dreamcast", "systemname")
        assert system_decision("x.bin", "Some New Machine") == ("some-new-machine", "slug")
        assert system_decision("x.bin", "") == ("_unknown", "none")

    def test_the_caveat_travels_with_an_identification_it_is_about(self):
        # identify_firmware hands back requirements without their emulator, so
        # the caveat has to come along or it is lost.
        machine = _machine({f"{INFO_DIR}/noods_libretro.info": NOODS_INFO,
                            f"{INFO_DIR}/noods_libretro.so": {"status": "invalid-text"}})
        identified = identify_firmware(machine, _context(machine), md5="a1" * 16)
        assert [r.file_name for r in identified.requirements] == ["firmware.bin"]
        assert CAVEAT_SYSTEM_ASSIGNMENT_DERIVED in [c.code for c in identified.caveats]

    def test_a_caveat_about_other_files_stays_off_the_identification(self):
        # The download flow asks about ONE content. NooDS's caveat names
        # firmware.bin; an answer about the Game Boy Advance BIOS — which has a
        # per-file rule — must not carry a warning about a file it does not
        # contain.
        machine = _machine({f"{INFO_DIR}/noods_libretro.info": NOODS_INFO,
                            f"{INFO_DIR}/noods_libretro.so": {"status": "invalid-text"}})
        identified = identify_firmware(machine, _context(machine), md5="11" * 16)
        assert [r.file_name for r in identified.requirements] == ["gba_bios.bin"]
        assert all(r.system_source == SOURCE_OVERRIDE for r in identified.requirements)
        assert [c.code for c in identified.caveats] == []

    def test_the_database_field_is_read_as_a_signal_not_as_a_name(self):
        machine = _machine({f"{INFO_DIR}/mgba_libretro.info": MGBA_INFO,
                            f"{INFO_DIR}/mgba_libretro.so": {"status": "invalid-text"}})
        core = next(c for c in read_core_declarations(machine, INFO_DIR, core_dir=INFO_DIR).cores if c.stem == "mgba_libretro")
        assert core.database == ("Nintendo - Game Boy", "Nintendo - Game Boy Advance")
        # The core's own system still comes from systemname, never from
        # database — the two disagree here, and systemname wins.
        assert core.system == "gba"


# Three PlayStation cores over one machine with no BIOS in place. Their
# declarations differ in exactly one way that matters — Beetle PSX marks its
# region image required, the other two mark everything optional — and only one
# of the three all-optional readings is correct.
BEETLE_PSX_SO = "mednafen_psx_libretro.so"
SWANSTATION_SO = "swanstation_libretro.so"
REARMED_SO = "pcsx_rearmed_libretro.so"

# SwanStation's shape: every image optional, said about a machine observed
# refusing to start.
SWANSTATION_INFO = """
systemname = "PlayStation"
firmware_count = 2
firmware0_desc = "scph5501.bin (PS1 US BIOS)"
firmware0_path = "scph5501.bin"
firmware0_opt = "true"
firmware1_desc = "psxonpsp660.bin (PSP PS1 BIOS)"
firmware1_path = "psxonpsp660.bin"
firmware1_opt = "true"
"""

# The identical declaration from the one core the table excuses: PCSX ReARMed
# carries its own HLE BIOS, so all-optional is what it should say.
REARMED_INFO = SWANSTATION_INFO

# The same declaration again under a stem no packaged knowledge entry names, so
# the core it stands for answers ``locating: unestablished`` and nothing but
# that declaration is read for it. It is the vehicle for every test about what
# a declaration leaves unsaid, because the deployed core this shape was read
# off now answers a route of its own that reads sizes and bytes (#466) — and a
# second reader in a test about the first one makes its fixtures say something
# they were never about. Not ``pcsx_rearmed``: the packaged system table
# excuses that core by name, which is a different case with its own test.
UNDERSTATER_SO = "understater_libretro.so"

# A PlayStation BIOS image the size SwanStation accepts, and the md5 its own
# packaged table pins for the PSP image — a row of region ``any``, so the
# search picks it whichever region a launch is. Both are needed together: the
# size is what the core's first filter reads off the stat, and the md5 is what
# its search recognises the bytes by. Only the tests about that route use
# these: a test about a declaration takes the vehicle above, whose answer
# reads no bytes at all.
PSX_IMAGE_SIZE = 524288
PSX_ANY_REGION_IMAGE: dict[str, str | int] = {
    "md5": "c53ca5908936d412331790f4426c6c33",
    "sha1": "96880d1ca92a016ff054be5159bb06fe03cb4e14",
    "size": PSX_IMAGE_SIZE,
}
# An all-optional core of a system the table records as open — nobody has
# established whether a Saturn starts with no firmware present.
SATURN_INFO = """
systemname = "Saturn"
firmware_count = 1
firmware0_desc = "sega_101.bin"
firmware0_path = "sega_101.bin"
firmware0_opt = "true"
"""

# An all-optional core of a system the table records nothing about at all.
UNRECORDED_SYSTEM_INFO = """
systemname = "Sharp X68000"
firmware_count = 1
firmware0_desc = "iplrom.dat"
firmware0_path = "iplrom.dat"
firmware0_opt = "true"
"""


# Real rows of SwanStation's own packaged table (atlas/data/swanstation_bios.json),
# which is what its search recognises an image by — an invented md5 would be a
# file it refuses, which is a different case and has its own test below.
SWAN_US_IMAGE: dict[str, str | int] = {
    "md5": "490f666e1afb15b7362b406ed1cea246",
    "sha1": "0555c6fae8906f3f09baf5988f00e55f88e9f30b",
    "size": PSX_IMAGE_SIZE,
}
SWAN_JP_IMAGE: dict[str, str | int] = {
    "md5": "8dd7d5296a650fac7319bce665a6a53c",
    "sha1": "13" * 20,
    "size": PSX_IMAGE_SIZE,
}
SWAN_PAL_IMAGE: dict[str, str | int] = {
    "md5": "32736f17079d0b2b7024407c39bd3050",
    "sha1": "12" * 20,
    "size": PSX_IMAGE_SIZE,
}
# A file of an accepted size no row of that table holds.
SWAN_UNKNOWN_IMAGE: dict[str, str | int] = {"md5": "00" * 16, "sha1": "01" * 20, "size": PSX_IMAGE_SIZE}
# The PS3-sized image: 0x3E66F0 bytes, recognised over its FIRST 512 KiB, which
# is why the blob states a scoped digest and the whole-file one is a different
# number. It is one of the three rows this table has and DuckStation's has not.
SWAN_PS3_SIZE = 4089584
SWAN_PS3_IMAGE: dict[str, str | int] = {
    "md5": "ff" * 16,
    "sha1": "fe" * 20,
    "md5:524288": "c02a6fbb1b27359f84e92fae8bc21316",
    "size": SWAN_PS3_SIZE,
}


class TestACoreThatOpensANameAndThenSearches:
    """SwanStation's route: the configured name first, the directory by content after.

    Its ``.info`` lists two optional images and its code opens neither unless
    an option names it. What a launch really opens is one of three per-region
    settings, and where that file cannot be loaded the core hashes every file
    of an accepted size in the system directory against its own table. Both
    doors are packaged knowledge (``atlas/data/core_firmware.json``), so these
    tests are about the resolver reading them, never about a name.
    """

    OPTIONS = "/config/retroarch-core-options.cfg"
    OPT_DIR = "/config/config"

    def _machine(
        self,
        bios: Mapping[str, FixtureFileSpec] | None = None,
        *,
        options: str | None = None,
        per_core: str | None = None,
        core: Mapping[str, object] | None = None,
        unlistable: bool = False,
    ) -> FixtureMachine:
        files: dict[str, FixtureFileSpec] = {
            f"{INFO_DIR}/swanstation_libretro.info": SWANSTATION_INFO,
            f"{INFO_DIR}/swanstation_libretro.so": {"status": "invalid-text"},
            **(bios or {}),
        }
        if options is not None:
            files[self.OPTIONS] = options
        if per_core is not None:
            files[f"{self.OPT_DIR}/SwanStation/SwanStation.opt"] = per_core
        return FixtureMachine(
            files,  # type: ignore[arg-type]
            dirs=[BIOS_DIR],
            # A directory that IS one and whose contents cannot be read, which
            # is the state a search reaches after passing its own "is this a
            # directory" check — a file inside it that atlas already knows
            # still stats, exactly as a mode-111 directory behaves.
            unlistable=[BIOS_DIR] if unlistable else None,
            cores={f"{INFO_DIR}/swanstation_libretro.so": core} if core is not None else None,
        )

    def _core(
        self, machine: FixtureMachine, *, verify: bool = False, per_core_options: bool = False
    ) -> CoreFirmware:
        context = _context(
            machine,
            core_options=CoreOptionsChain(
                global_file=self.OPTIONS,
                override_config_dir=self.OPT_DIR,
                per_core_options=per_core_options,
                core_dir=INFO_DIR,
            ),
        )
        return firmware_for_core(
            machine, context, core_so=SWANSTATION_SO, verify=verify
        ).cores[0]

    def _group(self, core: CoreFirmware) -> FirmwareAlternatives:
        (group,) = [r for r in core.requirements if isinstance(r, FirmwareAlternatives)]
        return group

    def _option(self, core: CoreFirmware, region: str) -> FirmwareRequirement:
        """The group's option a launch of *region* needs.

        By region rather than by position, because two regions whose launches
        open the same file under the same reading are ONE option carrying both
        — which is what a search find usually is.
        """
        (option,) = [o for o in self._group(core).options if region in (o.regions or ())]
        return option

    def _route(self, core: CoreFirmware) -> FirmwareRequirement:
        """The one unconditional requirement this core's own route added."""
        (requirement,) = [
            r
            for r in core.requirements
            if isinstance(r, FirmwareRequirement) and r.need == NEED_REQUIRED
        ]
        return requirement

    def _codes(self, core: CoreFirmware) -> list[str]:
        return [caveat.code for caveat in core.caveats]

    def _data(self, core: CoreFirmware, code: str) -> Mapping[str, "DataValue"]:
        (caveat,) = [c for c in core.caveats if c.code == code]
        return caveat.data

    def _regions(self, core: CoreFirmware, code: str) -> list[str]:
        stated = self._data(core, code)["regions"]
        assert not isinstance(stated, str)
        return list(stated)

    # --- the named door ------------------------------------------------------

    def test_the_named_default_that_is_there_is_what_that_region_opens(self):
        # No options file at all, so every key answers with the core's own
        # declared default — and the US default is the file that is there.
        core = self._core(
            self._machine({f"{BIOS_DIR}/scph5501.bin": SWAN_US_IMAGE}), verify=True
        )
        option = self._option(core, "ntsc-u")
        assert (option.path, option.declared) == (f"{BIOS_DIR}/scph5501.bin", "scph5501.bin")
        assert (option.checked, option.satisfied) == (CHECKED_VERIFIED, True)
        assert option.identity is not None
        assert option.identity.md5 == SWAN_US_IMAGE["md5"]

    def test_a_named_file_of_a_size_the_core_refuses_hands_the_region_to_the_search(self):
        # The size gate is the core's own first test and a stat settles it, so
        # the refusal needs no content check to be stated — and the region
        # falls through to the directory, which holds the image under another
        # name entirely.
        machine = self._machine(
            {
                f"{BIOS_DIR}/scph5501.bin": {"md5": "cd" * 16, "sha1": "ce" * 20, "size": 1024},
                f"{BIOS_DIR}/anything.bin": SWAN_US_IMAGE,
            }
        )
        core = self._core(machine, verify=True)
        refused = self._data(core, CAVEAT_FIRMWARE_IMAGE_REFUSED)
        assert refused["path"] == f"{BIOS_DIR}/scph5501.bin"
        assert refused["size"] == "1024"
        option = self._option(core, "ntsc-u")
        assert (option.path, option.checked, option.satisfied) == (
            f"{BIOS_DIR}/anything.bin",
            CHECKED_VERIFIED,
            True,
        )

    def test_a_name_that_climbs_out_of_the_firmware_root_is_refused_and_searched(self):
        machine = self._machine(
            {f"{BIOS_DIR}/anything.bin": SWAN_US_IMAGE},
            options='swanstation_BIOS_PathNTSCU = "../escape.bin"\n',
        )
        core = self._core(machine, verify=True)
        assert CAVEAT_FIRMWARE_PATH_ESCAPES_ROOT in self._codes(core)
        assert self._option(core, "ntsc-u").path == f"{BIOS_DIR}/anything.bin"

    def test_a_name_that_climbs_out_over_an_empty_root_leaves_its_region_without_an_option(self):
        # Both halves of one region's route yield nothing: the refused name gives
        # no destination to keep, and the settled search finds nothing to pick.
        # The refusal is the one reason that names no region — it names the key
        # and the value — so the disc-decides statement names the region in its
        # sentence and its ``regions`` carry only what the group holds.
        machine = self._machine({}, options='swanstation_BIOS_PathNTSCU = "../escape.bin"\n')
        core = self._core(machine, verify=True)
        assert CAVEAT_FIRMWARE_PATH_ESCAPES_ROOT in self._codes(core)
        assert "regions" not in self._data(core, CAVEAT_FIRMWARE_PATH_ESCAPES_ROOT)
        assert [o.regions for o in self._group(core).options] == [("ntsc-j",), ("pal",)]
        assert self._regions(core, CAVEAT_CORE_MODE_UNESTABLISHED) == ["ntsc-j", "pal"]
        (undecided,) = [c for c in core.caveats if c.code == CAVEAT_CORE_MODE_UNESTABLISHED]
        assert "ntsc-u" in undecided.message

    # --- the search door -----------------------------------------------------

    def test_the_search_finds_the_image_under_a_name_no_option_knows(self):
        # The whole point of the second door: the file is named nothing the
        # core would ever open, and it boots because its bytes are a row.
        machine = self._machine({f"{BIOS_DIR}/a-us-bios": SWAN_US_IMAGE})
        core = self._core(machine, verify=True)
        option = self._option(core, "ntsc-u")
        assert (option.path, option.checked, option.satisfied) == (
            f"{BIOS_DIR}/a-us-bios",
            CHECKED_VERIFIED,
            True,
        )
        listing = self._data(core, CAVEAT_FIRMWARE_SEARCH_CANDIDATES)
        assert listing["readings"] == {f"{BIOS_DIR}/a-us-bios": READING_IDENTIFIED}
        assert listing["image_regions"] == {f"{BIOS_DIR}/a-us-bios": "ntsc-u"}

    def test_a_directory_of_unrecognised_images_boots_nothing(self):
        # The core's search refuses a file its table does not know, so a
        # directory of them is a directory with no image in it — and the
        # listing says of each one that its bytes were read and not known.
        machine = self._machine({f"{BIOS_DIR}/mystery.bin": SWAN_UNKNOWN_IMAGE})
        core = self._core(machine, verify=True)
        assert {option.satisfied for option in self._group(core).options} == {False}
        assert {option.found for option in self._group(core).options} == {KIND_MISSING}
        listing = self._data(core, CAVEAT_FIRMWARE_SEARCH_CANDIDATES)
        assert listing["readings"] == {f"{BIOS_DIR}/mystery.bin": READING_UNRECOGNISED}
        assert self._data(core, CAVEAT_FIRMWARE_PATH_NAMES_NO_FILE)["candidates"] == "1"

    def test_an_image_of_another_region_is_the_pick_and_says_which_region(self):
        # Upstream boots it with a "possibly-incompatible" warning, so it is a
        # pick rather than a failure — and the row's own region rides the
        # statement, because that is the mismatch a client has to see.
        machine = self._machine(
            {f"{BIOS_DIR}/europe.bin": SWAN_PAL_IMAGE},
            options='swanstation_Console_Region = "NTSC-U"\n',
        )
        core = self._core(machine, verify=True)
        requirement = self._route(core)
        assert requirement.path == f"{BIOS_DIR}/europe.bin"
        assert requirement.checked == CHECKED_VERIFIED
        identified = self._data(core, CAVEAT_FIRMWARE_IMAGE_IDENTIFIED)
        assert identified["region"] == "pal"
        assert self._regions(core, CAVEAT_FIRMWARE_IMAGE_IDENTIFIED) == ["ntsc-u"]

    def test_two_region_valid_images_are_a_tie_the_directory_would_decide(self):
        machine = self._machine(
            {
                f"{BIOS_DIR}/one.bin": SWAN_US_IMAGE,
                f"{BIOS_DIR}/two.bin": PSX_ANY_REGION_IMAGE,
            },
            options='swanstation_Console_Region = "NTSC-U"\n',
        )
        core = self._core(machine, verify=True)
        assert self._data(core, CAVEAT_FIRMWARE_IMAGE_AMBIGUOUS)["tied"] == "2"

    def test_a_four_megabyte_image_is_recognised_over_its_first_512_kib(self):
        # The scope is the whole reason this table exists beside DuckStation's:
        # the core reads BIOS_SIZE bytes out of every candidate whatever its
        # length, so the md5 that decides is the prefix's and not the file's.
        machine = self._machine({f"{BIOS_DIR}/ps3.bin": SWAN_PS3_IMAGE})
        core = self._core(machine, verify=True)
        option = self._option(core, "ntsc-u")
        assert (option.path, option.checked) == (f"{BIOS_DIR}/ps3.bin", CHECKED_VERIFIED)
        assert option.identity is not None
        assert option.identity.md5 == "c02a6fbb1b27359f84e92fae8bc21316"
        assert option.identity.size == SWAN_PS3_SIZE

    # --- the region the option pins, and the one nothing pins ----------------

    def test_a_pinned_region_is_one_unconditional_requirement(self):
        machine = self._machine(
            {f"{BIOS_DIR}/scph5502.bin": SWAN_PAL_IMAGE},
            options='swanstation_Console_Region = "PAL"\n',
        )
        core = self._core(machine, verify=True)
        assert not [r for r in core.requirements if isinstance(r, FirmwareAlternatives)]
        assert CAVEAT_CORE_MODE_UNESTABLISHED not in self._codes(core)
        pinned = self._route(core)
        assert (pinned.declared, pinned.regions, pinned.checked) == (
            "scph5502.bin",
            None,
            CHECKED_VERIFIED,
        )

    def test_a_region_value_the_core_does_not_declare_pins_nothing(self):
        machine = self._machine(options='swanstation_Console_Region = "NTSC-KR"\n')
        core = self._core(machine)
        assert self._data(core, CAVEAT_UNKNOWN_OPTION_VALUE)["value"] == "NTSC-KR"
        # Three options, one per region, because each region's key names its
        # own default and an empty directory leaves each of them the answer.
        assert {option.regions for option in self._group(core).options} == {
            ("ntsc-j",),
            ("ntsc-u",),
            ("pal",),
        }

    def test_an_undecided_region_states_the_disc_decides(self):
        core = self._core(self._machine())
        assert self._data(core, CAVEAT_CORE_MODE_UNESTABLISHED)["reason"] == (
            REASON_REGION_DECIDED_BY_DISC
        )

    # --- what a query without a content check may say ------------------------

    def test_without_a_content_check_the_named_file_is_unchecked(self):
        core = self._core(self._machine({f"{BIOS_DIR}/scph5501.bin": SWAN_US_IMAGE}))
        option = self._option(core, "ntsc-u")
        assert (option.checked, option.satisfied) == (CHECKED_UNCHECKED, None)

    def test_without_a_content_check_the_search_states_itself_and_picks_nothing(self):
        # Files of an accepted size and nobody asked to hash them: the regions
        # the search speaks for get no option at all, because "no image for
        # this region" would be a claim about bytes nobody read.
        core = self._core(self._machine({f"{BIOS_DIR}/a-us-bios": SWAN_US_IMAGE}))
        assert not [r for r in core.requirements if isinstance(r, FirmwareAlternatives)]
        unverified = self._data(core, CAVEAT_FIRMWARE_SEARCH_UNVERIFIED)
        assert unverified["candidates"] == "1"
        assert self._regions(core, CAVEAT_FIRMWARE_SEARCH_UNVERIFIED) == ["ntsc-j", "ntsc-u", "pal"]

    # --- which options file governs -----------------------------------------

    def test_a_per_core_options_file_outranks_the_global_one(self):
        # RetroArch's own priority: with global_core_options off, the per-core
        # .opt is the governing file and the global one is never consulted for
        # this core. The directory it names is the core's library_name, which
        # lives in the binary and costs a probe.
        machine = self._machine(
            {f"{BIOS_DIR}/psxonpsp660.bin": PSX_ANY_REGION_IMAGE},
            options='swanstation_BIOS_PathNTSCU = "scph5501.bin"\n',
            per_core='swanstation_BIOS_PathNTSCU = "psxonpsp660.bin"\n',
            core={"library_name": "SwanStation"},
        )
        core = self._core(machine, verify=True, per_core_options=True)
        option = self._option(core, "ntsc-u")
        assert option.declared == "psxonpsp660.bin"
        assert (option.checked, option.satisfied) == (CHECKED_VERIFIED, True)

    def test_a_core_that_cannot_be_queried_reads_the_global_file_and_says_so(self):
        machine = self._machine(
            {f"{BIOS_DIR}/psxonpsp660.bin": PSX_ANY_REGION_IMAGE},
            options='swanstation_BIOS_PathNTSCU = "psxonpsp660.bin"\n',
            per_core='swanstation_BIOS_PathNTSCU = "scph5501.bin"\n',
        )
        core = self._core(machine, verify=True, per_core_options=True)
        assert self._data(core, CAVEAT_CORE_UNQUERYABLE)["core_so"] == SWANSTATION_SO
        assert self._option(core, "ntsc-u").declared == "psxonpsp660.bin"

    # --- what a read that did not come back leaves open ----------------------

    def test_a_named_image_whose_bytes_will_not_read_is_unread_and_the_search_runs(self):
        # Upstream falls through on a failed read like any other (bios.cpp:98-102
        # returns nothing, host_interface.cpp:178-179 searches), so the
        # directory is read and stated. The region keeps the named file all the
        # same: atlas's read failing is no evidence the launch's does, so
        # naming the searched image would state an open nobody watched as
        # having failed.
        machine = self._machine(
            {
                f"{BIOS_DIR}/scph5501.bin": {"status": "unreadable", "size": PSX_IMAGE_SIZE},
                f"{BIOS_DIR}/a-us-bios": SWAN_US_IMAGE,
            }
        )
        core = self._core(machine, verify=True)
        option = self._option(core, "ntsc-u")
        assert (option.path, option.checked, option.satisfied) == (
            f"{BIOS_DIR}/scph5501.bin",
            CHECKED_UNREAD,
            None,
        )
        assert CAVEAT_FIRMWARE_UNREADABLE in self._codes(core)
        # The search ran and said what the directory holds, which is the half
        # that used to be skipped entirely. The named file is in that listing
        # too, because it is a file of an accepted size sitting in the
        # directory the core would search — read there as unreadable for the
        # same reason it is unread above.
        listing = self._data(core, CAVEAT_FIRMWARE_SEARCH_CANDIDATES)
        assert listing["readings"] == {
            f"{BIOS_DIR}/a-us-bios": READING_IDENTIFIED,
            f"{BIOS_DIR}/scph5501.bin": READING_UNREADABLE,
        }

    def test_a_candidate_whose_bytes_will_not_read_can_still_be_the_pick(self):
        # A read failure is atlas's and not the table's refusal, so the file
        # stays in the ranking and the option it becomes carries `unread`
        # rather than a verdict about content nobody saw.
        machine = self._machine(
            {f"{BIOS_DIR}/maybe.bin": {"status": "unreadable", "size": PSX_IMAGE_SIZE}}
        )
        core = self._core(machine, verify=True)
        option = self._option(core, "ntsc-u")
        assert (option.path, option.checked, option.satisfied) == (
            f"{BIOS_DIR}/maybe.bin",
            CHECKED_UNREAD,
            None,
        )
        assert self._data(core, CAVEAT_FIRMWARE_UNREADABLE)["path"] == f"{BIOS_DIR}/maybe.bin"
        assert self._data(core, CAVEAT_FIRMWARE_SEARCH_CANDIDATES)["readings"] == {
            f"{BIOS_DIR}/maybe.bin": READING_UNREADABLE
        }

    def test_a_directory_that_will_not_list_leaves_the_regions_it_answers_for_unstated(self):
        # The one way to a PARTIAL group under a content check: one region's
        # named file is there and boots, and what the other two would find is a
        # question the failed listing did not answer — so they get no option,
        # the statement about which option a disc selects names only the
        # region the group carries, and the incomplete-scan statement names
        # the two regions whose answer rested on the listing, not the one a
        # named file settled without it.
        machine = self._machine(
            {f"{BIOS_DIR}/scph5501.bin": SWAN_US_IMAGE},
            unlistable=True,
        )
        core = self._core(machine, verify=True)
        assert [o.regions for o in self._group(core).options] == [("ntsc-u",)]
        assert self._regions(core, CAVEAT_CORE_MODE_UNESTABLISHED) == ["ntsc-u"]
        incomplete = self._data(core, CAVEAT_FIRMWARE_SCAN_INCOMPLETE)
        assert incomplete["dir"] == BIOS_DIR
        assert self._regions(core, CAVEAT_FIRMWARE_SCAN_INCOMPLETE) == ["ntsc-j", "pal"]

    def test_the_disc_decides_statement_rides_no_answer_without_a_group(self):
        # It explains which of a group's options a launch needs, and with no
        # option anywhere there is no group for it to explain.
        core = self._core(self._machine({f"{BIOS_DIR}/a-us-bios": SWAN_US_IMAGE}))
        assert not [r for r in core.requirements if isinstance(r, FirmwareAlternatives)]
        assert CAVEAT_CORE_MODE_UNESTABLISHED not in self._codes(core)

    # --- where this route meets the verdict about the system -----------------

    def test_an_all_optional_declaration_is_answered_by_what_the_search_finds(self):
        # The two halves meeting. This core's .info marks every image
        # optional, so the declaration alone leaves nothing unmet over a
        # machine that will not boot; the packaged system table says a
        # PlayStation needs an image; and the image its own search found is
        # what answers that need — for every region at once, because the row
        # it matched carries none of its own.
        core = self._core(self._machine({f"{BIOS_DIR}/a-us-bios": SWAN_US_IMAGE}), verify=True)
        assert [r.need for r in core.requirements if isinstance(r, FirmwareRequirement)] == [
            "optional",
            "optional",
        ]
        assert core.system_firmware == SYSTEM_FIRMWARE_CANNOT_RUN_WITHOUT
        assert core.system_firmware_needs == ("psx",)
        option = self._option(core, "ntsc-u")
        assert (option.need, option.system, option.satisfied) == (NEED_REQUIRED, "psx", True)
        assert core.requirements_met is True

    def test_the_same_declaration_over_an_empty_directory_is_not_green(self):
        # The other side of the same seam, and the one the declaration could
        # never reach on its own: nothing is unmet in what the file states,
        # the directory holds no image, and the answer is false because the
        # route demonstrated that no region has one.
        core = self._core(self._machine(), verify=True)
        assert [r for r in core.unmet if r.regions is None] == []
        assert {o.satisfied for o in self._group(core).options} == {False}
        assert core.requirements_met is False

    # --- what the core claims, and what the knowledge must agree on ----------

    def test_a_named_image_the_core_refused_is_still_this_cores_claim(self):
        # The route composed that path, stat'ed it and states a caveat naming
        # it — so it is a file this entry accounted for, even though the search
        # then answered the region and the answer names another file. A path a
        # caveat names must not come back as one nobody asks for.
        machine = self._machine(
            {
                f"{BIOS_DIR}/scph5501.bin": {"md5": "cd" * 16, "sha1": "ce" * 20, "size": 1024},
                f"{BIOS_DIR}/anything.bin": SWAN_US_IMAGE,
            }
        )
        answer = firmware_inventory(machine, _context(machine), verify=True)
        (core,) = answer.cores
        assert self._data(core, CAVEAT_FIRMWARE_IMAGE_REFUSED)["path"] == f"{BIOS_DIR}/scph5501.bin"
        assert {f"{BIOS_DIR}/scph5501.bin", f"{BIOS_DIR}/anything.bin"} <= set(core.claims)
        assert answer.unclaimed == ()

    def test_every_file_the_search_ran_over_is_this_cores_claim(self):
        machine = self._machine(
            {f"{BIOS_DIR}/a-us-bios": SWAN_US_IMAGE, f"{BIOS_DIR}/mystery.bin": SWAN_UNKNOWN_IMAGE}
        )
        core = self._core(machine, verify=True)
        assert set(core.claims) >= {f"{BIOS_DIR}/a-us-bios", f"{BIOS_DIR}/mystery.bin"}

    def test_what_resolving_the_options_chain_cost_reaches_the_answer(self):
        # A line RetroArch's parser refused while resolving the options file
        # is a degradation of every value read through it, and this is the
        # answer that reads one — so it is stated here rather than swallowed
        # where the chain was assembled.
        machine = self._machine()
        chain = CoreOptionsChain(
            global_file=self.OPTIONS,
            override_config_dir=self.OPT_DIR,
            per_core_options=False,
            caveats=(Caveat(CAVEAT_CFG_LINE_DROPPED, "a line the parser refused", {"key": "x"}),),
        )
        answer = firmware_for_core(
            machine, _context(machine, core_options=chain), core_so=SWANSTATION_SO
        )
        assert CAVEAT_CFG_LINE_DROPPED in [caveat.code for caveat in answer.caveats]

    def test_an_unclaimed_image_this_cores_own_table_knows_names_the_core(self):
        # Every region's named default is in place, so the core reads no
        # directory and claims nothing in one — and the file beside them is
        # one nobody declared whose bytes its table holds. That is a concern
        # about this core, and it is stated because the core is installed.
        machine = self._machine(
            {
                f"{BIOS_DIR}/scph5500.bin": SWAN_JP_IMAGE,
                f"{BIOS_DIR}/scph5501.bin": SWAN_US_IMAGE,
                f"{BIOS_DIR}/scph5502.bin": SWAN_PAL_IMAGE,
                f"{BIOS_DIR}/spare.bin": SWAN_PS3_IMAGE,
            }
        )
        answer = firmware_inventory(machine, _context(machine), verify=True)
        (spare,) = [f for f in answer.unclaimed if f.path == f"{BIOS_DIR}/spare.bin"]
        assert spare.concerns == (
            Concern(emulator=SWANSTATION_SO, relation=RELATION_RECOGNISES),
        )
        # Read over the table's own scope, which is why the identity is the
        # prefix's md5 and not the file's.
        assert spare.identity is not None
        assert spare.identity.md5 == "c02a6fbb1b27359f84e92fae8bc21316"
        assert spare.console == "ps3"

    def test_a_table_that_moved_away_from_the_entry_stops_the_answer(self):
        # Two sources for the hash scope and the unknown policy — the entry
        # cites the core's source, the table carries what the generator read —
        # and a disagreement is a table regenerated from another build.
        card = lookup_core_firmware(SWANSTATION_SO)
        assert card is not None
        assert card.content_route is not None
        moved = replace(card, content_route=replace(card.content_route, hash_scope=1024))
        content_table = getattr(atlas.firmware, "_content_table")
        with pytest.raises(ValueError, match="shipped out of step"):
            content_table(moved)


BEETLE_PSX_HW_SO = "mednafen_psx_hw_libretro.so"
# The deployed Beetle PSX declaration: one image per console region marked
# required, and the two region-free ones marked optional beside them.
BEETLE_INFO = """
display_name = "Sony - PlayStation (Beetle PSX)"
systemname = "PlayStation"
firmware_count = 5
firmware0_desc = "scph5500.bin (PS1 JP BIOS)"
firmware0_path = "scph5500.bin"
firmware0_opt = "false"
firmware1_desc = "scph5501.bin (PS1 US BIOS)"
firmware1_path = "scph5501.bin"
firmware1_opt = "false"
firmware2_desc = "scph5502.bin (PS1 EU BIOS)"
firmware2_path = "scph5502.bin"
firmware2_opt = "false"
firmware3_desc = "psxonpsp660.bin (PSP PS1 BIOS)"
firmware3_path = "psxonpsp660.bin"
firmware3_opt = "true"
firmware4_desc = "ps1_rom.bin (PS3 PS1 BIOS)"
firmware4_path = "ps1_rom.bin"
firmware4_opt = "true"
"""
# The US image the packaged hash table pins for scph5501.bin, and bytes that
# are not it. Neither decides anything on this route — the core opens what it
# finds — which is exactly what the tests below hold.
BEETLE_US_IMAGE: dict[str, str | int] = {
    "md5": "490f666e1afb15b7362b406ed1cea246",
    "sha1": "0555c6fae8906f3f09baf5988f00e55f88e9f30b",
    "size": PSX_IMAGE_SIZE,
}
BEETLE_OTHER_BYTES: dict[str, str | int] = {
    "md5": "ab" * 16,
    "sha1": "cd" * 20,
    "size": PSX_IMAGE_SIZE,
}


class TestACoreThatTriesSeveralSpellingsOfOneImage:
    """Beetle PSX's route: one image per console region, under any of its names.

    Its ``.info`` lists five images and marks three of them required, and no
    launch needs three: ``firmware_is_present`` is called once with one region
    and walks that region's own list of spellings, opening the first that
    exists. An override option can select a region-free image ahead of them.
    Both facts are packaged knowledge (``atlas/data/core_firmware.json``), and
    these tests are about the resolver reading it.
    """

    OPTIONS = "/config/retroarch-core-options.cfg"
    OPT_DIR = "/config/config"
    KEY = "beetle_psx_override_bios"

    def _machine(
        self,
        bios: Mapping[str, FixtureFileSpec] | None = None,
        *,
        options: str | None = None,
        per_core: str | None = None,
        core_so: str = BEETLE_PSX_SO,
        dirs: list[str] | None = None,
        **kwargs: object,
    ) -> FixtureMachine:
        files: dict[str, FixtureFileSpec] = {
            f"{INFO_DIR}/{core_so[: -len('.so')]}.info": BEETLE_INFO,
            f"{INFO_DIR}/{core_so}": {"status": "invalid-text"},
            **(bios or {}),
        }
        if options is not None:
            files[self.OPTIONS] = options
        if per_core is not None:
            files[f"{self.OPT_DIR}/Beetle PSX/Beetle PSX.opt"] = per_core
        return FixtureMachine(
            files,  # type: ignore[arg-type]
            dirs=dirs or [BIOS_DIR],
            **kwargs,  # type: ignore[arg-type]
        )

    def _core(
        self,
        machine: FixtureMachine,
        *,
        verify: bool = False,
        core_so: str = BEETLE_PSX_SO,
        chain: CoreOptionsChain | None = None,
        options_file: bool = True,
    ) -> CoreFirmware:
        if chain is None and options_file:
            chain = CoreOptionsChain(
                global_file=self.OPTIONS,
                override_config_dir=self.OPT_DIR,
                per_core_options=False,
                core_dir=INFO_DIR,
            )
        context = _context(machine, core_options=chain)
        return firmware_for_core(machine, context, core_so=core_so, verify=verify).cores[0]

    def _group(self, core: CoreFirmware) -> FirmwareAlternatives:
        (group,) = [r for r in core.requirements if isinstance(r, FirmwareAlternatives)]
        return group

    def _option(self, core: CoreFirmware, region: str) -> FirmwareRequirement:
        (option,) = [o for o in self._group(core).options if region in (o.regions or ())]
        return option

    def _codes(self, core: CoreFirmware) -> list[str]:
        return [caveat.code for caveat in core.caveats]

    def _spellings(self, core: CoreFirmware) -> dict[str, list[str]]:
        """Every list this launch consulted, keyed by the region or the option it belongs to."""
        stated: dict[str, list[str]] = {}
        for caveat in core.caveats:
            if caveat.code != CAVEAT_FIRMWARE_NAME_SPELLINGS:
                continue
            names = caveat.data.get("region") or caveat.data["option"]
            spellings = caveat.data["spellings"]
            assert isinstance(names, str)
            assert not isinstance(spellings, str)
            stated[names] = list(spellings)
        return stated

    def _rows(self, core: CoreFirmware) -> dict[str, FirmwareRequirement]:
        """The declared rows beside the group, by the name the .info spelled.

        Not :func:`_plain_requirements`, which refuses a core carrying a group
        — here the group is the point, and these are the entries standing
        beside it.
        """
        return {
            r.declared: r for r in core.requirements if isinstance(r, FirmwareRequirement)
        }

    def _declared(self, core: CoreFirmware) -> dict[str, str]:
        return {name: row.need for name, row in self._rows(core).items()}

    # --- the region lists ----------------------------------------------------

    def test_the_first_spelling_that_is_there_is_what_that_region_opens(self):
        core = self._core(self._machine({f"{BIOS_DIR}/scph5501.bin": BEETLE_US_IMAGE}))
        option = self._option(core, "ntsc-u")
        assert (option.file_name, option.path) == ("scph5501.bin", f"{BIOS_DIR}/scph5501.bin")
        assert option.satisfied is True

    def test_an_image_under_a_later_spelling_is_what_that_region_opens(self):
        # The defect #474 names: the declaration spells scph5501.bin and the
        # core tries nine names, so a file under the third of them boots it.
        core = self._core(self._machine({f"{BIOS_DIR}/SCPH-5501.bin": BEETLE_US_IMAGE}))
        option = self._option(core, "ntsc-u")
        assert option.file_name == "SCPH-5501.bin"
        assert option.declared == "scph5501.bin"
        assert option.satisfied is True

    def test_a_verified_answer_states_no_identity_for_the_name_it_opens(self):
        # The core pins no image to a spelling: it compares one SHA1 after
        # opening the file and boots a mismatch with a warning, so nothing
        # covers the destination and presence is the whole verdict.
        core = self._core(
            self._machine({f"{BIOS_DIR}/SCPH-5503.bin": BEETLE_OTHER_BYTES}), verify=True
        )
        option = self._option(core, "ntsc-u")
        assert option.identity is None
        assert option.checked == CHECKED_UNKNOWN
        assert option.satisfied is True

    def test_the_declared_rows_keep_answering_from_their_own_identity(self):
        # The route states no identity; the DECLARATION still does, and the
        # rows beside the group are reproduced with their own reading.
        machine = self._machine({f"{BIOS_DIR}/scph5501.bin": BEETLE_OTHER_BYTES})
        rows = self._rows(self._core(machine, verify=True))
        assert rows["scph5501.bin"].checked == CHECKED_MISMATCH
        assert rows["scph5501.bin"].identity is not None
        assert rows["scph5500.bin"].checked is None

    def test_a_region_with_nothing_at_any_spelling_names_the_first_one(self):
        # The name the core reports as missing and shows on screen.
        core = self._core(self._machine())
        assert [(o.file_name, o.regions) for o in self._group(core).options] == [
            ("scph5500.bin", ("ntsc-j",)),
            ("scph5501.bin", ("ntsc-u",)),
            ("scph5502.bin", ("pal",)),
        ]
        assert core.requirements_met is False

    def test_one_image_of_one_region_leaves_the_launch_undecided(self):
        # A group with one option satisfied and two not has no single verdict:
        # which region the disc is decides it, and that is not on disk.
        core = self._core(self._machine({f"{BIOS_DIR}/scph5502.bin": BEETLE_US_IMAGE}))
        assert self._group(core).satisfied is None
        assert core.requirements_met is None

    def test_the_spellings_of_every_list_consulted_reach_the_answer(self):
        core = self._core(self._machine())
        stated = self._spellings(core)
        assert sorted(stated) == ["ntsc-j", "ntsc-u", "pal"]
        assert [len(names) for names in stated.values()] == [3, 9, 6]
        assert stated["ntsc-j"][:2] == ["scph5500.bin", "SCPH5500.bin"]

    def test_the_expected_image_rides_the_names_it_belongs_to(self):
        (caveat,) = [
            c
            for c in self._core(self._machine()).caveats
            if c.code == CAVEAT_FIRMWARE_NAME_SPELLINGS and c.data.get("region") == "pal"
        ]
        assert caveat.data["sha1"] == "f6bc2d1f5eb6593de7d089c425ac681d6fffd3f0"
        assert "option" not in caveat.data

    def test_a_region_is_stated_as_undecided_because_the_disc_decides_it(self):
        core = self._core(self._machine())
        assert CAVEAT_CORE_MODE_UNESTABLISHED in self._codes(core)

    # --- the declaration beside it -------------------------------------------

    def test_a_declared_row_the_lists_name_asks_for_nothing_by_itself(self):
        # Three required rows for a launch that needs one of them: the group is
        # the requirement, so the rows it speaks for are optional.
        assert self._declared(self._core(self._machine())) == {
            "ps1_rom.bin": NEED_OPTIONAL,
            "psxonpsp660.bin": NEED_OPTIONAL,
            "scph5500.bin": NEED_OPTIONAL,
            "scph5501.bin": NEED_OPTIONAL,
            "scph5502.bin": NEED_OPTIONAL,
        }

    def test_a_refused_declaration_the_lists_name_asks_for_nothing_either(self):
        # A declaration atlas will not follow still carries a need, and a
        # required one withholds the verdict. The group answers for these three
        # names as much as for the rows: each is one spelling out of the list,
        # and the images are in place under the others.
        machine = self._machine(
            {
                f"{BIOS_DIR}/SCPH5500.bin": BEETLE_US_IMAGE,
                f"{BIOS_DIR}/SCPH5501.bin": BEETLE_US_IMAGE,
                f"{BIOS_DIR}/SCPH5502.bin": BEETLE_US_IMAGE,
            },
            symlinks={
                f"{BIOS_DIR}/scph5500.bin": "/elsewhere/scph5500.bin",
                f"{BIOS_DIR}/scph5501.bin": "/elsewhere/scph5501.bin",
                f"{BIOS_DIR}/scph5502.bin": "/elsewhere/scph5502.bin",
            },
        )
        core = self._core(machine)
        assert [row.need for row in core.refused] == [NEED_OPTIONAL] * 3
        assert self._group(core).satisfied is True
        assert core.requirements_met is True

    def test_a_declared_row_no_list_names_keeps_its_need(self):
        info = BEETLE_INFO.replace(
            'firmware4_path = "ps1_rom.bin"', 'firmware4_path = "something_else.bin"'
        ).replace('firmware4_opt = "true"', 'firmware4_opt = "false"')
        machine = self._machine({f"{INFO_DIR}/{BEETLE_PSX_SO[: -len('.so')]}.info": info})
        assert self._declared(self._core(machine))["something_else.bin"] == NEED_REQUIRED

    def test_a_core_whose_route_states_no_names_moves_no_declaration(self):
        # The rule is the route's, not the module's: SwanStation's entry states
        # no spellings, so every row of its declaration stands as it read.
        machine = FixtureMachine(
            {
                f"{INFO_DIR}/swanstation_libretro.info": SWANSTATION_INFO,
                f"{INFO_DIR}/swanstation_libretro.so": {"status": "invalid-text"},
            },
            dirs=[BIOS_DIR],
        )
        core = firmware_for_core(machine, _context(machine), core_so=SWANSTATION_SO).cores[0]
        assert [row.need for row in self._rows(core).values()] == [NEED_OPTIONAL, NEED_OPTIONAL]

    def test_the_two_door_route_speaks_for_no_declared_name(self):
        # The seam the test above rests on, held directly: SwanStation's rows
        # are optional already, so its answer cannot show a rule that widened.
        # What it CAN show is the route handing back nothing to move.
        machine = FixtureMachine(
            {
                f"{INFO_DIR}/swanstation_libretro.info": SWANSTATION_INFO,
                f"{INFO_DIR}/swanstation_libretro.so": {"status": "invalid-text"},
            },
            dirs=[BIOS_DIR],
        )
        context = _context(machine)
        declarations = next(
            core for core in context.cores if core.core_so == SWANSTATION_SO
        )
        located = getattr(atlas.firmware, "_located_firmware")(
            machine, context, declarations, verify=False
        )
        assert located.entry is not None
        assert located.spoken_for == ()

    # --- the override option -------------------------------------------------

    def test_a_selected_override_image_that_is_there_serves_every_region(self):
        core = self._core(
            self._machine(
                {f"{BIOS_DIR}/psxonpsp660.bin": BEETLE_US_IMAGE},
                options=f'{self.KEY} = "psxonpsp"\n',
            )
        )
        (option,) = self._group(core).options
        assert option.regions == ("ntsc-j", "ntsc-u", "pal")
        assert option.file_name == "psxonpsp660.bin"
        assert core.requirements_met is True

    def test_a_selected_override_ends_the_search_before_the_region_lists(self):
        core = self._core(
            self._machine(
                {f"{BIOS_DIR}/PSXONPSP660.bin": BEETLE_US_IMAGE},
                options=f'{self.KEY} = "psxonpsp"\n',
            )
        )
        assert list(self._spellings(core)) == [self.KEY]
        assert self._option(core, "pal").file_name == "PSXONPSP660.bin"

    def test_a_selected_override_at_no_name_falls_back_to_the_region_lists(self):
        core = self._core(self._machine(options=f'{self.KEY} = "ps1_rom"\n'))
        assert CAVEAT_FIRMWARE_CONFIGURED_IMAGE_MISSING in self._codes(core)
        assert sorted(self._spellings(core)) == [self.KEY, "ntsc-j", "ntsc-u", "pal"]
        assert self._option(core, "ntsc-u").file_name == "scph5501.bin"

    def test_the_shipped_value_looks_at_no_override_name_at_all(self):
        core = self._core(
            self._machine(
                {f"{BIOS_DIR}/ps1_rom.bin": BEETLE_US_IMAGE}, options=f'{self.KEY} = "disabled"\n'
            )
        )
        assert sorted(self._spellings(core)) == ["ntsc-j", "ntsc-u", "pal"]
        assert core.requirements_met is False

    def test_an_option_value_the_core_does_not_declare_selects_nothing_and_says_so(self):
        core = self._core(
            self._machine(
                {f"{BIOS_DIR}/ps1_rom.bin": BEETLE_US_IMAGE}, options=f'{self.KEY} = "psx_on_ps4"\n'
            )
        )
        assert CAVEAT_UNKNOWN_OPTION_VALUE in self._codes(core)
        assert sorted(self._spellings(core)) == ["ntsc-j", "ntsc-u", "pal"]

    def test_each_build_reads_its_own_option_key(self):
        # One source tree, two cores, and the renderer is in the key's name.
        machine = self._machine(
            {f"{BIOS_DIR}/ps1_rom.bin": BEETLE_US_IMAGE},
            options=f'{self.KEY} = "ps1_rom"\n',
            core_so=BEETLE_PSX_HW_SO,
        )
        core = self._core(machine, core_so=BEETLE_PSX_HW_SO)
        assert sorted(self._spellings(core)) == ["ntsc-j", "ntsc-u", "pal"]
        hardware = self._core(
            self._machine(
                {f"{BIOS_DIR}/ps1_rom.bin": BEETLE_US_IMAGE},
                options=f'beetle_psx_hw_override_bios = "ps1_rom"\n',
                core_so=BEETLE_PSX_HW_SO,
            ),
            core_so=BEETLE_PSX_HW_SO,
        )
        assert list(self._spellings(hardware)) == ["beetle_psx_hw_override_bios"]

    def test_the_per_core_options_file_governs_where_it_is_the_one_read(self):
        machine = self._machine(
            {f"{BIOS_DIR}/psxonpsp660.bin": BEETLE_US_IMAGE},
            options=f'{self.KEY} = "disabled"\n',
            per_core=f'{self.KEY} = "psxonpsp"\n',
            cores={f"{INFO_DIR}/{BEETLE_PSX_SO}": {"library_name": "Beetle PSX"}},
        )
        chain = CoreOptionsChain(
            global_file=self.OPTIONS,
            override_config_dir=self.OPT_DIR,
            per_core_options=True,
            core_dir=INFO_DIR,
        )
        assert list(self._spellings(self._core(machine, chain=chain))) == [self.KEY]

    def test_a_context_that_names_no_options_file_reads_the_core_s_own_default(self):
        core = self._core(
            self._machine({f"{BIOS_DIR}/ps1_rom.bin": BEETLE_US_IMAGE}), options_file=False
        )
        assert sorted(self._spellings(core)) == ["ntsc-j", "ntsc-u", "pal"]

    def test_an_options_file_that_will_not_read_leaves_the_region_lists_standing(self):
        machine = self._machine(
            {self.OPTIONS: {"status": "unreadable"}, f"{BIOS_DIR}/scph5500.bin": BEETLE_US_IMAGE}
        )
        core = self._core(machine)
        assert sorted(self._spellings(core)) == ["ntsc-j", "ntsc-u", "pal"]
        assert self._option(core, "ntsc-j").satisfied is True

    # --- what the machine puts in the way ------------------------------------

    def test_a_directory_at_one_of_the_names_ends_the_walk_there(self):
        # The core's test is an open for reading, which succeeds on a
        # directory; it then reads nothing out of it. So the walk stops, the
        # later spelling is not the answer, and nothing is confirmed.
        machine = self._machine(
            {f"{BIOS_DIR}/SCPH-5500.bin": BEETLE_US_IMAGE},
            dirs=[BIOS_DIR, f"{BIOS_DIR}/SCPH5500.bin"],
        )
        option = self._option(self._core(machine), "ntsc-j")
        assert (option.file_name, option.found) == ("SCPH5500.bin", KIND_DIRECTORY)
        assert option.satisfied is None

    def test_a_dead_symlink_is_not_one_of_these_names_being_there(self):
        # The core's open fails on it, so the walk goes on — and the stat says
        # the same thing, which is why nothing special is needed to mirror it.
        machine = self._machine(
            {f"{BIOS_DIR}/SCPH5500.bin": BEETLE_US_IMAGE},
            symlinks={f"{BIOS_DIR}/scph5500.bin": f"{BIOS_DIR}/gone.bin"},
        )
        assert self._option(self._core(machine), "ntsc-j").file_name == "SCPH5500.bin"

    def test_a_name_that_climbs_out_of_the_firmware_root_is_refused_and_the_walk_goes_on(self):
        machine = self._machine(
            {f"{BIOS_DIR}/SCPH5500.bin": BEETLE_US_IMAGE},
            symlinks={f"{BIOS_DIR}/scph5500.bin": "/elsewhere/scph5500.bin"},
        )
        core = self._core(machine)
        assert CAVEAT_FIRMWARE_PATH_ESCAPES_ROOT in self._codes(core)
        assert self._option(core, "ntsc-j").file_name == "SCPH5500.bin"

    def test_a_name_that_cannot_be_looked_at_does_not_end_the_walk(self):
        # The core's open would fail on a path a stat cannot reach either, so
        # its walk goes on — and the look that did not happen is stated.
        machine = self._machine(
            {f"{BIOS_DIR}/SCPH5500.bin": BEETLE_US_IMAGE},
            inaccessible=[f"{BIOS_DIR}/scph5500.bin"],
        )
        answer = firmware_for_core(machine, _context(machine), core_so=BEETLE_PSX_SO)
        assert CAVEAT_FIRMWARE_PATH_INACCESSIBLE in [c.code for c in answer.caveats]
        assert self._option(answer.cores[0], "ntsc-j").file_name == "SCPH5500.bin"

    def test_a_list_whose_names_cannot_be_looked_at_states_the_first_of_them(self):
        machine = self._machine(inaccessible=[f"{BIOS_DIR}/scph5500.bin"])
        option = self._option(self._core(machine), "ntsc-j")
        assert (option.file_name, option.found) == ("scph5500.bin", KIND_INACCESSIBLE)
        assert option.satisfied is None

    def test_every_name_this_core_would_try_is_claimed(self):
        # An unclaimed file is one no emulator asks for, and a file under any
        # of these names is one this core opens. Including a name the WALK
        # never reached: the first spelling is there, so the core stops at it,
        # and the file under a later one is still a file this core asks for.
        machine = self._machine(
            {
                f"{BIOS_DIR}/scph5501.bin": BEETLE_US_IMAGE,
                f"{BIOS_DIR}/SCPH-5503.bin": BEETLE_OTHER_BYTES,
            }
        )
        answer = firmware_inventory(machine, _context(machine))
        assert [file.path for file in answer.unclaimed] == []


class TestTheSystemBehindTheCoreReachesTheAnswer:
    """A ``.info`` cannot say "this machine does not start without one of these".

    So a core that knows its system needs a BIOS has two lossy moves, and the
    deployed catalogue takes both: Beetle PSX marks its region image required,
    SwanStation marks every image optional. Read on its own, the all-optional
    declaration says nothing is missing over a PlayStation that will not boot.

    The missing half is packaged world knowledge about the SYSTEM
    (``atlas/data/system_firmware.json``), and these tests hold the two things
    it must do and the one thing it must not: ``system_firmware`` states what
    is recorded, ``requirements_met`` stops being green where the system
    cannot run — and every ``need`` stays exactly what the core declared.

    The all-optional core here is :data:`UNDERSTATER_SO`, which carries
    SwanStation's declaration under a stem no packaged entry names — so it
    answers ``locating: unestablished`` and its whole answer is that
    declaration. The deployed core the shape was read off finds its firmware
    by a route of its own that reads the size and the bytes of what it finds
    (#466), and a test about what a DECLARATION leaves unsaid must not have a
    second reader in it: with SwanStation as the vehicle, a fixture would have
    to be a plausible BIOS for these assertions to mean what they say.
    ``TestACoreThatOpensANameAndThenSearches`` is where that route meets this
    verdict.
    """

    def _psx_machine(self, *, bios: Mapping[str, FixtureFileSpec] | None = None) -> FixtureMachine:
        # These tests ask about BEETLE_PSX_SO by name, so the fixture has to
        # declare that .so: under the generic stem the rest of the file uses,
        # the core they name would not be on this machine at all. The
        # declaration it carries is this file's own two-row one, not the
        # deployed five-row declaration.
        return _machine(
            {
                f"{INFO_DIR}/{UNDERSTATER_SO[: -len('.so')]}.info": SWANSTATION_INFO,
                f"{INFO_DIR}/{UNDERSTATER_SO}": {"status": "invalid-text"},
                f"{INFO_DIR}/{REARMED_SO[: -len('.so')]}.info": REARMED_INFO,
                f"{INFO_DIR}/{REARMED_SO}": {"status": "invalid-text"},
                **(bios or {}),
            },
            core=BEETLE_PSX_SO,
        )

    def _core(self, machine: FixtureMachine, core_so: str, *, verify: bool = False) -> CoreFirmware:
        return firmware_for_core(machine, _context(machine), core_so=core_so, verify=verify).cores[0]

    def _marks(self, core: CoreFirmware) -> tuple[Caveat, ...]:
        return tuple(c for c in core.caveats if c.code == CAVEAT_SYSTEM_FIRMWARE_WORLD_KNOWLEDGE)

    # --- the three PlayStation cores, one machine, no BIOS in place ---------

    def test_the_core_that_understates_its_system_is_no_longer_green(self):
        # The defect this whole thing exists for: five (here two) optional
        # images, nothing unmet, and a machine that will not boot.
        core = self._core(self._psx_machine(), UNDERSTATER_SO)
        assert core.system_firmware == SYSTEM_FIRMWARE_CANNOT_RUN_WITHOUT
        assert core.requirements_met is False
        assert core.unmet == ()

    def test_the_core_supplying_its_own_alternative_stays_green(self):
        # The identical declaration, and the table records why it is right.
        core = self._core(self._psx_machine(), REARMED_SO)
        assert core.system_firmware == SYSTEM_FIRMWARE_CORE_ALTERNATIVE
        assert core.requirements_met is True

    def test_the_core_that_was_already_right_answers_as_it_did(self):
        # Beetle PSX declares its region image required, so it was already
        # false — the point is that the reading did not move it.
        core = self._core(self._psx_machine(), BEETLE_PSX_SO)
        assert core.system_firmware == SYSTEM_FIRMWARE_CANNOT_RUN_WITHOUT
        assert core.requirements_met is False
        # What it names as unmet is this core's own route rather than its
        # declaration (#474): every region's list reached nothing, so the whole
        # group fails and each region's first spelling is named. The declared
        # row those spellings speak for is optional beside them.
        assert [r.file_name for r in core.unmet] == [
            "scph5500.bin",
            "scph5501.bin",
            "scph5502.bin",
        ]

    def test_nothing_read_off_the_machine_is_overwritten(self):
        # The declaration is the emulator's statement, reproduced. SwanStation
        # says optional and keeps saying optional; the verdict beside it is
        # atlas's own and is a different field.
        core = self._core(self._psx_machine(), UNDERSTATER_SO)
        assert [r.need for r in _plain_requirements(core)] == ["optional", "optional"]

    # --- an open system, and a system nobody recorded ------------------------

    def test_an_open_system_is_stated_and_moves_nothing(self):
        machine = _machine(
            {
                f"{INFO_DIR}/kronos_libretro.info": SATURN_INFO,
                f"{INFO_DIR}/kronos_libretro.so": {"status": "invalid-text"},
            }
        )
        core = self._core(machine, "kronos_libretro.so")
        assert core.system_firmware == SYSTEM_FIRMWARE_OPEN
        # All-optional, nothing required, nothing unmet — exactly the answer
        # the declaration alone gives, because nobody has established more.
        assert core.requirements_met is True

    def test_a_system_nobody_recorded_states_nothing_at_all(self):
        machine = _machine(
            {
                f"{INFO_DIR}/px68k_libretro.info": UNRECORDED_SYSTEM_INFO,
                f"{INFO_DIR}/px68k_libretro.so": {"status": "invalid-text"},
            }
        )
        core = self._core(machine, "px68k_libretro.so")
        assert core.system_firmware is None
        assert self._marks(core) == ()

    def test_the_unrecorded_system_is_not_the_answer_nothing_is_needed(self):
        # The misreading this field exists to prevent, held as a comparison:
        # `None` and `runs-without-firmware` are different words, and only the
        # second is a claim that the system starts with no image present.
        machine = _machine(
            {
                f"{INFO_DIR}/px68k_libretro.info": UNRECORDED_SYSTEM_INFO,
                f"{INFO_DIR}/px68k_libretro.so": {"status": "invalid-text"},
            }
        )
        core = self._core(machine, "px68k_libretro.so")
        assert core.system_firmware is None
        assert core.system_firmware != SYSTEM_FIRMWARE_RUNS_WITHOUT
        assert SYSTEM_FIRMWARE_RUNS_WITHOUT in CORE_SYSTEM_FIRMWARE_STATES

    # --- the interactions with the tri-state that was already there ----------

    def test_one_image_in_place_is_what_the_system_asked_for(self):
        # The requirement is a disjunction: the system needs an image, not all
        # of them. One verified image is the whole demand met.
        machine = self._psx_machine(
            bios={f"{BIOS_DIR}/scph5501.bin": {"md5": "aa" * 16, "sha1": "bb" * 20, "size": 8}}
        )
        core = self._core(machine, UNDERSTATER_SO, verify=True)
        assert core.system_firmware == SYSTEM_FIRMWARE_CANNOT_RUN_WITHOUT
        assert core.requirements_met is True

    def test_an_image_nobody_judged_leaves_the_answer_unsaid(self):
        # Present but unverified: the file might be the one that would serve,
        # so `false` would claim atlas knows the core will not run. It does
        # not, and `None` is the honest word.
        machine = self._psx_machine(bios={f"{BIOS_DIR}/scph5501.bin": "whatever"})
        core = self._core(machine, UNDERSTATER_SO)
        assert core.system_firmware == SYSTEM_FIRMWARE_CANNOT_RUN_WITHOUT
        assert core.requirements_met is None

    def test_a_refused_declaration_leaves_the_answer_unsaid(self):
        # A declaration atlas would not follow is an image it never looked
        # for, so the absence of every OTHER image establishes nothing about
        # whether this core has one.
        info = (
            'systemname = "PlayStation"\n'
            "firmware_count = 2\n"
            'firmware0_path = "scph5501.bin"\n'
            'firmware0_opt = "true"\n'
            'firmware1_path = "../outside.bin"\n'
            'firmware1_opt = "true"\n'
        )
        machine = _machine(
            {
                f"{INFO_DIR}/refuser_libretro.info": info,
                f"{INFO_DIR}/refuser_libretro.so": {"status": "invalid-text"},
            }
        )
        core = self._core(machine, "refuser_libretro.so")
        assert [r.declared for r in core.refused] == ["../outside.bin"]
        assert core.system_firmware == SYSTEM_FIRMWARE_CANNOT_RUN_WITHOUT
        assert core.requirements_met is None

    def test_a_declaration_that_was_not_read_states_neither(self):
        # No declaration, no requirements, no system on this answer — so the
        # state is `None` for the reason it always means: nothing is recorded
        # about a system nobody established.
        machine = _machine(
            {
                f"{INFO_DIR}/{UNDERSTATER_SO[: -len('.so')]}.info": {"status": "unreadable"},
                f"{INFO_DIR}/{UNDERSTATER_SO}": {"status": "invalid-text"},
            }
        )
        core = self._core(machine, UNDERSTATER_SO)
        assert core.declaration == DECLARATION_UNREADABLE
        assert core.system_firmware is None
        assert core.requirements_met is None

    def test_the_reading_never_turns_an_answer_green(self):
        # The one-way rule: over every core this machine has, an answer that
        # is true with the system-level reading was true without it. Held
        # mechanically, because "it only narrows" is the kind of claim a
        # future branch quietly breaks.
        #
        # The machine is chosen so the rule has something to break: the
        # optional image is in place and the required one is not, so Beetle
        # PSX has the image its SYSTEM needs while a file it declares required
        # is missing. Anything that let the first fact answer for the core
        # would turn a false into a true right here.
        machine = self._psx_machine(bios={f"{BIOS_DIR}/psxonpsp660.bin": "whatever"})
        answer = firmware_inventory(machine, _context(machine))
        assert [core.core_so for core in answer.cores] == [BEETLE_PSX_SO, REARMED_SO, UNDERSTATER_SO]
        for core in answer.cores:
            unaided = replace(core, system_firmware=None)
            assert core.requirements_met is not True or unaided.requirements_met is True
        # And the state the machine is actually in, so the guard above cannot
        # go vacuous by every core answering the same thing.
        met = {core.core_so: core.requirements_met for core in answer.cores}
        assert met == {BEETLE_PSX_SO: False, REARMED_SO: True, UNDERSTATER_SO: True}

    def test_one_image_of_the_set_is_what_the_system_asked_for(self):
        # The disjunction over a whole machine rather than one core: the
        # PlayStation needs an image, psxonpsp660.bin is one, and SwanStation
        # is green over it even though the region image beside it is missing.
        machine = self._psx_machine(bios={f"{BIOS_DIR}/psxonpsp660.bin": "whatever"})
        core = self._core(machine, UNDERSTATER_SO)
        assert core.system_firmware == SYSTEM_FIRMWARE_CANNOT_RUN_WITHOUT
        assert [(r.file_name, r.satisfied) for r in _plain_requirements(core)] == [
            ("psxonpsp660.bin", True),
            ("scph5501.bin", False),
        ]
        assert core.requirements_met is True

    # --- the mark that says where the second source came from ---------------

    def test_the_mark_carries_the_system_and_the_evidence_level(self):
        core = self._core(self._psx_machine(), UNDERSTATER_SO)
        (mark,) = self._marks(core)
        assert mark.data == {"system": "psx", "evidence": EVIDENCE_WORD_VERIFIED}

    def test_the_mark_publishes_a_word_and_not_the_bracket_notation(self):
        # `[V]` is how this repository's research pages write an evidence
        # level; the contract is the public surface and spells it. The two
        # spellings are one scale, joined by a map that is total over the
        # markers, so nothing can reach a client unspelled.
        core = self._core(self._psx_machine(), UNDERSTATER_SO)
        (mark,) = self._marks(core)
        assert mark.data["evidence"] == "verified"
        assert mark.data["evidence"] not in EVIDENCE_LEVELS
        assert dict(EVIDENCE_WORDS).keys() == set(EVIDENCE_LEVELS)

    def test_a_derived_verdict_publishes_the_derived_word(self):
        # The word no shipped entry produces — every stated verdict in the
        # packaged table is verified today — so this is what reaches it, and
        # what the corpus exemption list points at.
        machine = self._demo_machine()
        context = _context(
            machine,
            system_firmware=self._recorded(VERDICT_CANNOT_RUN_WITHOUT, EVIDENCE_DERIVED),
        )
        core = firmware_for_core(machine, context, core_so="demo_libretro.so").cores[0]
        (mark,) = self._marks(core)
        assert mark.data["evidence"] == EVIDENCE_WORD_DERIVED

    def test_the_mark_stays_off_an_open_verdict(self):
        # The mark is a degradation with a code a client acts on, not a
        # general provenance note. An open entry's whole content is that
        # nobody established the answer, which is exactly what the field value
        # `open` on this same core already says — so a mark here would restate
        # the field, and a note that adds nothing devalues the ones that do.
        machine = _machine(
            {
                f"{INFO_DIR}/kronos_libretro.info": SATURN_INFO,
                f"{INFO_DIR}/kronos_libretro.so": {"status": "invalid-text"},
            }
        )
        core = self._core(machine, "kronos_libretro.so")
        assert core.system_firmware == SYSTEM_FIRMWARE_OPEN
        assert self._marks(core) == ()

    def test_the_open_evidence_level_can_never_reach_the_mark(self):
        # The two halves that make that a guarantee rather than a habit: the
        # loader ties an open verdict to the open evidence level and refuses
        # every other pairing, and the mark rides no open verdict — so the
        # published vocabulary is the two words a stated verdict can rest on.
        with pytest.raises(ValueError, match="disagree"):
            self._recorded(VERDICT_CANNOT_RUN_WITHOUT, EVIDENCE_OPEN, checked=True)
        assert EVIDENCE_WORD_OPEN not in STATED_EVIDENCE_WORDS
        assert set(STATED_EVIDENCE_WORDS) == {EVIDENCE_WORD_VERIFIED, EVIDENCE_WORD_DERIVED}

    def test_a_core_reaching_two_open_systems_is_open_and_unmarked(self):
        # mGBA declares Game Boy boot ROMs beside its GBA BIOS, so two
        # recorded systems answer for it. Both are open in the shipped table,
        # so there is no strongest to pick here — the state is open whatever
        # order they come in, and neither contributes a mark. The precedence
        # between differing verdicts is held next door, over a table written
        # in the test.
        machine = _machine(
            {
                f"{INFO_DIR}/mgba_libretro.info": MGBA_INFO,
                f"{INFO_DIR}/mgba_libretro.so": {"status": "invalid-text"},
            }
        )
        core = self._core(machine, "mgba_libretro.so")
        assert {r.system for r in _plain_requirements(core)} >= {"gb", "gba"}
        assert core.system_firmware == SYSTEM_FIRMWARE_OPEN
        assert self._marks(core) == ()

    def test_a_core_reaching_two_stated_systems_marks_each(self):
        # Accumulation, which one mark per core would pass: mGBA's two
        # recorded systems, both stated by a table written here, each carrying
        # its own system and its own evidence level.
        machine = _machine(
            {
                f"{INFO_DIR}/mgba_libretro.info": MGBA_INFO,
                f"{INFO_DIR}/mgba_libretro.so": {"status": "invalid-text"},
            }
        )
        recorded = {
            "Game Boy/Game Boy Color": SystemFirmware(
                system="Game Boy/Game Boy Color",
                verdict=VERDICT_RUNS_WITHOUT,
                evidence=EVIDENCE_DERIVED,
                source="a fixture",
                alternatives=(),
            ),
            "Game Boy Advance": SystemFirmware(
                system="Game Boy Advance",
                verdict=VERDICT_CANNOT_RUN_WITHOUT,
                evidence=EVIDENCE_VERIFIED,
                source="a fixture",
                alternatives=(),
            ),
        }
        context = _context(machine, system_firmware=recorded)
        core = firmware_for_core(machine, context, core_so="mgba_libretro.so").cores[0]
        assert [(m.data["system"], m.data["evidence"]) for m in self._marks(core)] == [
            ("gb", EVIDENCE_WORD_DERIVED),
            ("gba", EVIDENCE_WORD_VERIFIED),
        ]
        # And the state is the strongest of the two, not the first seen.
        assert core.system_firmware == SYSTEM_FIRMWARE_CANNOT_RUN_WITHOUT

    # --- whose image answers whose need, on a core declaring for two systems -

    def _mgba_machine(self, *, bios: Mapping[str, FixtureFileSpec] | None = None) -> FixtureMachine:
        # The fixture declares two of mGBA's images — a Game Boy boot ROM
        # beside its GBA BIOS — which the per-file rules send to two different
        # systems, gb and gba. The deployed .info declares four images and the
        # rules file all four of them (gb, gbc, gba, snes); two systems is what
        # this rule needs, and two is what the shipped system-firmware table
        # has entries for.
        return _machine(
            {
                f"{INFO_DIR}/mgba_libretro.info": MGBA_INFO,
                f"{INFO_DIR}/mgba_libretro.so": {"status": "invalid-text"},
                **(bios or {}),
            }
        )

    def _only_the_advance_needs_an_image(self) -> dict[str, SystemFirmware]:
        # A table written here, routed through the loader so it is one the
        # loader accepts: the Game Boy Advance needs an image and the Game Boy
        # question is open. Both systems the shipped table records for mGBA
        # are open, and recording the Advance is its own question with its own
        # evidence — so this shape exists nowhere but in a test, and no machine
        # vector can carry it either, the vector runner having no key for a
        # table.
        return load_system_firmware(
            json.dumps(
                {
                    "schema": 1,
                    "spec": "a spec",
                    "systems": {
                        "Game Boy/Game Boy Color": {
                            "verdict": VERDICT_OPEN,
                            "evidence": EVIDENCE_OPEN,
                            "source": "a fixture",
                        },
                        "Game Boy Advance": {
                            "verdict": VERDICT_CANNOT_RUN_WITHOUT,
                            "evidence": EVIDENCE_VERIFIED,
                            "source": "a fixture",
                        },
                    },
                }
            )
        )

    def test_another_systems_image_does_not_answer_this_systems_need(self):
        # The defect #431 names: the Game Boy boot ROM is established usable
        # and the GBA BIOS is not there, so a disjunction over every declared
        # image would answer `True` over a machine that will not boot a GBA
        # game. The need belongs to the Game Boy Advance and is asked over the
        # images filed under it.
        machine = self._mgba_machine(
            bios={f"{BIOS_DIR}/gb_bios.bin": {"md5": "ee" * 16, "sha1": "ff" * 20, "size": 5}}
        )
        context = _context(machine, system_firmware=self._only_the_advance_needs_an_image())
        core = firmware_for_core(machine, context, core_so="mgba_libretro.so", verify=True).cores[0]
        assert [(r.file_name, r.system, r.satisfied) for r in _plain_requirements(core)] == [
            ("gb_bios.bin", "gb", True),
            ("gba_bios.bin", "gba", False),
        ]
        assert core.system_firmware == SYSTEM_FIRMWARE_CANNOT_RUN_WITHOUT
        assert core.system_firmware_needs == ("gba",)
        assert core.requirements_met is False
        # One mark, and it names the Advance — not because that system is the
        # needing one, but because the Game Boy entry is open and a mark rides
        # only an entry that states something.
        assert [(m.data["system"], m.data["evidence"]) for m in self._marks(core)] == [
            ("gba", EVIDENCE_WORD_VERIFIED)
        ]

    def test_the_needing_systems_own_image_answers_its_need(self):
        # The other half of the same fixture: the GBA BIOS is the image the
        # Game Boy Advance asks for, and the Game Boy boot ROM missing beside
        # it is a question about another machine.
        machine = self._mgba_machine(
            bios={f"{BIOS_DIR}/gba_bios.bin": {"md5": "11" * 16, "sha1": "22" * 20, "size": 6}}
        )
        context = _context(machine, system_firmware=self._only_the_advance_needs_an_image())
        core = firmware_for_core(machine, context, core_so="mgba_libretro.so", verify=True).cores[0]
        assert [(r.file_name, r.satisfied) for r in _plain_requirements(core)] == [
            ("gb_bios.bin", False),
            ("gba_bios.bin", True),
        ]
        assert core.system_firmware == SYSTEM_FIRMWARE_CANNOT_RUN_WITHOUT
        assert core.requirements_met is True

    def test_an_unjudged_image_of_the_needing_system_leaves_the_answer_unsaid(self):
        # Scoping narrows which images are asked about; it does not turn the
        # tri-state into a two-state. The GBA BIOS is present and nobody
        # judged it, so it might be the one that would serve — `None`, with
        # the Game Boy image beside it established usable and irrelevant.
        machine = self._mgba_machine(
            bios={
                f"{BIOS_DIR}/gb_bios.bin": {"md5": "ee" * 16, "sha1": "ff" * 20, "size": 5},
                f"{BIOS_DIR}/gba_bios.bin": {"status": "unreadable"},
            }
        )
        context = _context(machine, system_firmware=self._only_the_advance_needs_an_image())
        core = firmware_for_core(machine, context, core_so="mgba_libretro.so", verify=True).cores[0]
        assert [(r.file_name, r.satisfied) for r in _plain_requirements(core)] == [
            ("gb_bios.bin", True),
            ("gba_bios.bin", None),
        ]
        assert core.requirements_met is None

    def _both_systems_need_an_image(self) -> dict[str, SystemFirmware]:
        # The same fixture with both entries recording a need: two machines,
        # each of which does not start without one of its own images.
        return load_system_firmware(
            json.dumps(
                {
                    "schema": 1,
                    "spec": "a spec",
                    "systems": {
                        name: {
                            "verdict": VERDICT_CANNOT_RUN_WITHOUT,
                            "evidence": EVIDENCE_VERIFIED,
                            "source": "a fixture",
                        }
                        for name in ("Game Boy/Game Boy Color", "Game Boy Advance")
                    },
                }
            )
        )

    def _two_needs(self, bios: Mapping[str, FixtureFileSpec]) -> CoreFirmware:
        machine = self._mgba_machine(bios=bios)
        context = _context(machine, system_firmware=self._both_systems_need_an_image())
        return firmware_for_core(machine, context, core_so="mgba_libretro.so", verify=True).cores[0]

    def test_two_needing_systems_each_need_an_image_of_their_own(self):
        # The conjunction met: each machine has one of its own images, so the
        # one field a client renders is green.
        core = self._two_needs(
            {
                f"{BIOS_DIR}/gb_bios.bin": {"md5": "ee" * 16, "sha1": "ff" * 20, "size": 5},
                f"{BIOS_DIR}/gba_bios.bin": {"md5": "11" * 16, "sha1": "22" * 20, "size": 6},
            }
        )
        assert core.system_firmware_needs == ("gb", "gba")
        assert core.requirements_met is True

    def test_one_image_does_not_answer_for_two_needing_systems(self):
        # One image between them is not one image apiece: the Game Boy has its
        # boot ROM, the Game Boy Advance has nothing, and a single disjunction
        # over the union of both systems' images would call that green.
        core = self._two_needs(
            {f"{BIOS_DIR}/gb_bios.bin": {"md5": "ee" * 16, "sha1": "ff" * 20, "size": 5}}
        )
        assert core.system_firmware_needs == ("gb", "gba")
        assert core.requirements_met is False

    def test_a_second_needing_system_nobody_judged_leaves_the_answer_unsaid(self):
        # The Game Boy is served and the Advance's image is present and
        # unjudged: it might be the one that would serve, so neither `True`
        # nor `False` is honest about the pair.
        core = self._two_needs(
            {
                f"{BIOS_DIR}/gb_bios.bin": {"md5": "ee" * 16, "sha1": "ff" * 20, "size": 5},
                f"{BIOS_DIR}/gba_bios.bin": {"status": "unreadable"},
            }
        )
        assert core.requirements_met is None

    def test_a_demonstrated_absence_outranks_an_unjudged_image_across_systems(self):
        # The Game Boy has no image at all and the Advance's is unjudged. One
        # machine that demonstrably will not boot is the answer, whatever is
        # still unsettled about the other.
        core = self._two_needs({f"{BIOS_DIR}/gba_bios.bin": {"status": "unreadable"}})
        assert core.requirements_met is False

    def test_one_declared_system_makes_the_scope_the_whole_declaration(self):
        # The reduction: SwanStation declares both its images under the one
        # system the table speaks about, so the scoped set is every image it
        # declares and the answer is the one this reading always gave.
        machine = self._psx_machine(bios={f"{BIOS_DIR}/psxonpsp660.bin": "whatever"})
        core = self._core(machine, UNDERSTATER_SO)
        assert core.system_firmware_needs == ("psx",)
        assert {r.system for r in _plain_requirements(core)} == {"psx"}
        assert core.requirements_met is True

    def test_a_core_the_table_excuses_names_no_needing_system(self):
        # The exempted core has no need to scope: its all-optional
        # declaration is right, and the field says so by being empty.
        core = self._core(self._psx_machine(), REARMED_SO)
        assert core.system_firmware == SYSTEM_FIRMWARE_CORE_ALTERNATIVE
        assert core.system_firmware_needs == ()

    def test_a_need_with_no_system_named_is_refused(self):
        # The state and the systems it is about travel together, because a
        # need with no machine behind it cannot tell the images that would
        # answer it from the ones filed under something else.
        with pytest.raises(ValueError, match="names the systems it is about"):
            CoreFirmware(
                core_so="x_libretro.so",
                label=None,
                declaration=DECLARATION_READ,
                requirements=(),
                caveats=(),
                system_firmware=SYSTEM_FIRMWARE_CANNOT_RUN_WITHOUT,
            )

    def test_an_exemption_names_one_core_and_not_its_system(self):
        # The exemption is per core, so the other cores of an excused
        # system are untouched by it.
        machine = self._psx_machine()
        states = {
            core.core_so: core.system_firmware
            for core in firmware_inventory(machine, _context(machine)).cores
        }
        assert states == {
            BEETLE_PSX_SO: SYSTEM_FIRMWARE_CANNOT_RUN_WITHOUT,
            REARMED_SO: SYSTEM_FIRMWARE_CORE_ALTERNATIVE,
            UNDERSTATER_SO: SYSTEM_FIRMWARE_CANNOT_RUN_WITHOUT,
        }

    def _demo_machine(self) -> FixtureMachine:
        return _machine(
            {
                f"{INFO_DIR}/demo_libretro.info": (
                    'systemname = "Demo System"\n'
                    "firmware_count = 1\n"
                    'firmware0_path = "demo.bin"\n'
                    'firmware0_opt = "true"\n'
                ),
                f"{INFO_DIR}/demo_libretro.so": {"status": "invalid-text"},
            }
        )

    def _recorded(
        self, verdict: str, evidence: str, *, checked: bool = False
    ) -> dict[str, SystemFirmware]:
        """One system's entry. *checked* routes it through the loader's refusals."""
        entry = {"verdict": verdict, "evidence": evidence, "source": "a fixture"}
        if checked:
            return load_system_firmware(
                json.dumps({"schema": 1, "spec": "a spec", "systems": {"Demo System": entry}})
            )
        return {
            "Demo System": SystemFirmware(
                system="Demo System",
                verdict=verdict,
                evidence=evidence,
                source="a fixture",
                alternatives=(),
            )
        }

    def test_every_recorded_verdict_has_a_word_in_the_answer(self):
        # Totality, mechanized rather than asserted in prose: a verdict the
        # table can carry and this answer has no state for would otherwise
        # become `None`, which reads as "nothing is recorded about this
        # system" — the one misreading the field exists to prevent. The
        # derivation refuses instead, and this is what keeps the two
        # vocabularies in step.
        machine = self._demo_machine()
        for verdict in SYSTEM_FIRMWARE_VERDICTS:
            evidence = EVIDENCE_OPEN if verdict == VERDICT_OPEN else EVIDENCE_VERIFIED
            context = _context(machine, system_firmware=self._recorded(verdict, evidence))
            core = firmware_for_core(machine, context, core_so="demo_libretro.so").cores[0]
            assert core.system_firmware in CORE_SYSTEM_FIRMWARE_STATES, verdict

    def test_a_verdict_the_answer_has_no_word_for_is_refused(self):
        # The other half: the refusal is real, so the totality above is a
        # guarantee rather than a coincidence of today's vocabulary.
        machine = self._demo_machine()
        context = _context(
            machine,
            system_firmware=self._recorded("something-nobody-taught-the-answer", EVIDENCE_VERIFIED),
        )
        with pytest.raises(ValueError, match="no answer state for the recorded verdict"):
            firmware_for_core(machine, context, core_so="demo_libretro.so")

    def test_every_firmware_answer_goes_through_the_one_seam(self):
        # The claim `_stating_system_firmware` makes about itself — that it is
        # the ONE place world knowledge enters a firmware answer — held
        # against the module's own source rather than against a reading of it.
        # A new answer site that forgot the seam would answer `null` for every
        # core, which is indistinguishable from "nothing is recorded".
        tree = ast.parse(Path(atlas.firmware.__file__).read_text(encoding="utf-8"))
        stated = []
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call) and getattr(node.func, "id", None) == "FirmwareAnswer"):
                continue
            cores = next((kw.value for kw in node.keywords if kw.arg == "cores"), None)
            through_seam = (
                isinstance(cores, ast.Call)
                and getattr(cores.func, "id", None) == "_stating_system_firmware"
            )
            no_cores = isinstance(cores, ast.Tuple) and not cores.elts
            stated.append((node.lineno, through_seam or no_cores))
        assert stated, "no FirmwareAnswer construction site was found — this scan reads nothing"
        assert [line for line, ok in stated if not ok] == []

    def test_the_cores_own_caveat_list_is_not_touched(self):
        # The mark is appended; nothing already on the core is rewritten.
        # `stated_once` is the answer-level rule and says of itself that a
        # core's own list belongs to that entry — deduplicating it here made
        # a core's caveats depend on whether the table happened to know its
        # system, which is a fact about a table and not about that core.
        info = (
            'systemname = "PlayStation"\n'
            "firmware_count = 3\n"
            'firmware0_path = "scph5501.bin"\n'
            'firmware0_opt = "true"\n'
            'firmware1_path = "../outside.bin"\n'
            'firmware1_opt = "true"\n'
            'firmware2_path = "../outside.bin"\n'
            'firmware2_opt = "true"\n'
        )
        machine = _machine(
            {
                f"{INFO_DIR}/dup_libretro.info": info,
                f"{INFO_DIR}/dup_libretro.so": {"status": "invalid-text"},
            }
        )
        core = self._core(machine, "dup_libretro.so")
        # Two identical refusals, because the .info really does declare the
        # same escaping path twice, and the mark after them.
        assert [c.code for c in core.caveats] == [
            CAVEAT_FIRMWARE_PATH_ESCAPES_ROOT,
            CAVEAT_FIRMWARE_PATH_ESCAPES_ROOT,
            CAVEAT_SYSTEM_FIRMWARE_WORLD_KNOWLEDGE,
        ]

    def test_a_cores_caveats_do_not_depend_on_what_the_table_knows(self):
        # The same declaration under a system the table records nothing about
        # must answer the same list, minus only the mark. That equality is
        # what the deduplication broke.
        def _codes(systemname: str) -> list[str]:
            # A real declaration beside the two refusals, so the core has a
            # system on the answer and the table has something to say about
            # it — without that the seam never reaches this core at all and
            # the comparison would prove nothing.
            info = (
                f'systemname = "{systemname}"\n'
                "firmware_count = 3\n"
                'firmware0_path = "scph5501.bin"\n'
                'firmware0_opt = "true"\n'
                'firmware1_path = "../outside.bin"\n'
                'firmware1_opt = "true"\n'
                'firmware2_path = "../outside.bin"\n'
                'firmware2_opt = "true"\n'
            )
            machine = _machine(
                {
                    f"{INFO_DIR}/dup_libretro.info": info,
                    f"{INFO_DIR}/dup_libretro.so": {"status": "invalid-text"},
                }
            )
            core = self._core(machine, "dup_libretro.so")
            return [c.code for c in core.caveats if c.code != CAVEAT_SYSTEM_FIRMWARE_WORLD_KNOWLEDGE]

        assert _codes("PlayStation") == _codes("Sharp X68000")
        assert _codes("PlayStation") == [CAVEAT_FIRMWARE_PATH_ESCAPES_ROOT] * 2

    def test_two_table_keys_on_one_system_state_one_mark_per_reading(self):
        # The case the deduplication was really for, kept and scoped to the
        # marks: two keys landing on one system id. Same level, one mark;
        # different levels, one each, because they are different readings.
        machine = self._psx_machine()
        same = {
            "Sony - PlayStation": SystemFirmware(
                system="Sony - PlayStation",
                verdict=VERDICT_CANNOT_RUN_WITHOUT,
                evidence=EVIDENCE_VERIFIED,
                source="a fixture",
                alternatives=(),
            ),
            "PlayStation": SystemFirmware(
                system="PlayStation",
                verdict=VERDICT_CANNOT_RUN_WITHOUT,
                evidence=EVIDENCE_VERIFIED,
                source="a fixture",
                alternatives=(),
            ),
        }
        core = firmware_for_core(
            machine, _context(machine, system_firmware=same), core_so=UNDERSTATER_SO
        ).cores[0]
        assert [m.data["evidence"] for m in self._marks(core)] == [EVIDENCE_WORD_VERIFIED]

        differing = dict(same)
        differing["PlayStation"] = SystemFirmware(
            system="PlayStation",
            verdict=VERDICT_CANNOT_RUN_WITHOUT,
            evidence=EVIDENCE_DERIVED,
            source="a fixture",
            alternatives=(),
        )
        core = firmware_for_core(
            machine, _context(machine, system_firmware=differing), core_so=UNDERSTATER_SO
        ).cores[0]
        assert [m.data["evidence"] for m in self._marks(core)] == [
            EVIDENCE_WORD_VERIFIED,
            EVIDENCE_WORD_DERIVED,
        ]

    def test_the_table_key_is_joined_by_the_system_an_answer_speaks(self):
        # The table is keyed by libretro `systemname` and an answer speaks
        # atlas's own ids, so the join is the systemname map. Both PlayStation
        # spellings the deployed catalogue uses land on the same id, and the
        # entry therefore reaches a core declaring either.
        assert system_firmware_system("PlayStation") == "psx"
        assert system_firmware_system("Sony - PlayStation") == "psx"
        # A systemname nothing maps still answers a word, and nothing is
        # recorded under it.
        assert system_firmware_system("Some New Machine") == "some-new-machine"


class TestPerSystemAnswer:
    """Criterion 2: which emulators run this system, and what does each want?"""

    def test_without_a_catalogue_the_cores_own_systemname_enumerates(self):
        machine = _gb_machine()
        answer = firmware_for_system(machine, _context(machine), system="gb")
        assert [c.core_so for c in answer.cores] == ["gambatte_libretro.so", "sameboy_libretro.so"]
        assert CAVEAT_EMULATOR_CATALOGUE_UNAVAILABLE in [c.code for c in answer.caveats]

    def test_a_catalogue_lists_emulators_whose_core_is_not_installed(self):
        machine = _gb_machine()
        catalogue = Catalogue(
            (
                CatalogueEntry(label="Gambatte", kind="libretro", core_so="gambatte_libretro.so"),
                CatalogueEntry(label="TGB Dual", kind="libretro", core_so="tgbdual_libretro.so"),
            )
        )
        answer = firmware_for_system(machine, _context(machine), system="gb", catalogue=catalogue)
        by_label = {c.label: c for c in answer.cores}
        assert by_label["TGB Dual"].declaration == DECLARATION_ABSENT
        assert by_label["TGB Dual"].requirements == ()
        assert by_label["TGB Dual"].requirements_met is None
        assert [c.code for c in by_label["TGB Dual"].caveats] == [CAVEAT_CORE_NOT_INSTALLED]

    def test_a_standalone_emulator_is_stated_not_dropped(self):
        machine = _gb_machine()
        catalogue = Catalogue(
            (
                CatalogueEntry(label="Gambatte", kind="libretro", core_so="gambatte_libretro.so"),
                CatalogueEntry(label="SameBoy (Standalone)", kind="standalone", core_so=None),
            )
        )
        answer = firmware_for_system(machine, _context(machine), system="gb", catalogue=catalogue)
        standalone = answer.cores[1]
        assert standalone.declaration == DECLARATION_UNSUPPORTED
        assert [c.code for c in standalone.caveats] == [CAVEAT_STANDALONE_UNSUPPORTED]

    def test_one_identity_under_two_names_leaves_both_requirements_standing(self):
        # gb_bios.bin is on disk; SameBoy's dmg_boot.bin is byte-identical and
        # still missing, because SameBoy opens dmg_boot.bin and nothing else.
        machine = _gb_machine({f"{BIOS_DIR}/gb_bios.bin": _blob(b"boot!")})
        answer = firmware_for_system(machine, _context(machine), system="gb")
        by_core = {r.core_so: r for r in answer.requirements}
        assert by_core["gambatte_libretro.so"].present is True
        assert by_core["sameboy_libretro.so"].present is False
        assert (
            by_core["gambatte_libretro.so"].identity == by_core["sameboy_libretro.so"].identity
        ), "the same bytes are expected at both destinations"


class TestInventory:
    """Criteria 5 and 6: the aggregate, and what nobody asked for."""

    def test_an_unclaimed_file_is_recognised_by_content(self):
        content = b"boot!"
        table = json.dumps({"_meta": {}, "files": {"gb_bios.bin": _entry(content)}})
        machine = _machine({f"{BIOS_DIR}/mystery-name.bin": _blob(content)})
        context = FirmwareContext(
            root=BIOS_DIR,
            cores=read_core_declarations(machine, INFO_DIR, core_dir=INFO_DIR).cores,
            hashes=load_hashes(table),
        )
        unclaimed = firmware_inventory(machine, context, verify=True).unclaimed
        assert [f.path for f in unclaimed] == [f"{BIOS_DIR}/mystery-name.bin"]
        assert unclaimed[0].known_as == ("gb_bios.bin",)

    def test_without_verification_no_claim_is_made_about_an_unclaimed_file(self):
        machine = _machine({f"{BIOS_DIR}/scph1001.bin": _blob(b"whatever")})
        unclaimed = firmware_inventory(machine, _context(machine)).unclaimed
        assert unclaimed[0].identity is None
        assert unclaimed[0].known_as == ()

    def test_save_data_the_rule_cards_claim_is_not_firmware(self):
        machine = _machine(
            {
                f"{INFO_DIR}/flycast_libretro.info": DC_INFO,
                f"{INFO_DIR}/flycast_libretro.so": {"status": "invalid-text"},
                f"{BIOS_DIR}/dc/vmu_save_A1.bin": _blob(b"vmu"),
                f"{BIOS_DIR}/dc/spare.bin": _blob(b"spare"),
            }
        )
        paths = [f.path for f in firmware_inventory(machine, _context(machine)).unclaimed]
        assert paths == [f"{BIOS_DIR}/dc/spare.bin"]

    def test_the_scan_stays_in_the_directories_declarations_reference(self):
        machine = _machine(
            {
                f"{INFO_DIR}/flycast_libretro.info": DC_INFO,
                f"{INFO_DIR}/flycast_libretro.so": {"status": "invalid-text"},
                f"{BIOS_DIR}/dc/stray.bin": _blob(b"a"),
                f"{BIOS_DIR}/mame2003-plus/samples/wboy.zip": _blob(b"b"),
            }
        )
        paths = [f.path for f in firmware_inventory(machine, _context(machine)).unclaimed]
        assert paths == [f"{BIOS_DIR}/dc/stray.bin"]

    def test_a_declared_file_is_never_unclaimed(self):
        machine = _machine({f"{BIOS_DIR}/scph5501.bin": _blob(b"12345678")})
        answer = firmware_inventory(machine, _context(machine))
        assert answer.unclaimed == ()

    def test_the_scan_never_climbs_above_the_firmware_root(self):
        """A folder declaration may land on the root — its parent is not the tree.

        LRPS2 declares ``pcsx2/bios`` and means the folder, and RetroDECK links
        that back to the firmware root, so the claimed path *is* the root. The
        directory holding it is one level above the firmware tree, and
        scanning it reports whatever lies there as unclaimed firmware — hashed,
        under ``verify``.
        """
        machine = _machine(
            {
                f"{INFO_DIR}/pcsx2_libretro.info": LRPS2_FOLDER_INFO,
                f"{INFO_DIR}/pcsx2_libretro.so": {"status": "invalid-text"},
                f"{BIOS_DIR}/scph1001.bin": _blob(b"in the tree"),
                "/private-notes.txt": _blob(b"one level above the firmware root"),
            },
            symlinks={f"{BIOS_DIR}/pcsx2/bios": BIOS_DIR},
        )
        answer = firmware_inventory(machine, _context(machine), verify=True)
        assert [f.path for f in answer.unclaimed] == [f"{BIOS_DIR}/scph1001.bin"]

    def test_a_save_artifact_behind_a_symlinked_directory_is_still_a_save(self):
        # dir_prep links whole firmware subdirectories elsewhere, so the card's
        # "dc/vmu_save_A1.bin" and the file the scan finds are the same file
        # under two spellings — and a memory card is not firmware either way.
        machine = _machine(
            {
                f"{INFO_DIR}/flycast_libretro.info": DC_INFO,
                f"{INFO_DIR}/flycast_libretro.so": {"status": "invalid-text"},
                f"{BIOS_DIR}/dreamcast/vmu_save_A1.bin": _blob(b"vmu"),
                f"{BIOS_DIR}/dreamcast/spare.bin": _blob(b"spare"),
            },
            symlinks={f"{BIOS_DIR}/dc": f"{BIOS_DIR}/dreamcast"},
        )
        paths = [f.path for f in firmware_inventory(machine, _context(machine)).unclaimed]
        assert paths == [f"{BIOS_DIR}/dreamcast/spare.bin"]

    def test_an_entry_that_cannot_be_looked_at_is_stated_not_dropped(self):
        # Skipping it silently would be the collapse the status model exists to
        # prevent; listing it would invent a file atlas never saw.
        machine = _machine(
            {f"{BIOS_DIR}/scph1001.bin": _blob(b"whatever")},
            inaccessible=[f"{BIOS_DIR}/locked.bin"],
        )
        answer = firmware_inventory(machine, _context(machine))
        assert [f.path for f in answer.unclaimed] == [f"{BIOS_DIR}/scph1001.bin"]
        blocked = next(c for c in answer.caveats if c.code == CAVEAT_FIRMWARE_PATH_INACCESSIBLE)
        assert blocked.data["path"] == f"{BIOS_DIR}/locked.bin"

    def test_a_declared_destination_that_cannot_be_looked_at_is_stated_once(self):
        # The requirement side already states it, so the scan must not state it
        # again: one fact, twice in one answer, from two routes.
        machine = _machine(inaccessible=[f"{BIOS_DIR}/scph5501.bin"])
        answer = firmware_inventory(machine, _context(machine))
        blocked = [c for c in answer.caveats if c.code == CAVEAT_FIRMWARE_PATH_INACCESSIBLE]
        assert [c.data["path"] for c in blocked] == [f"{BIOS_DIR}/scph5501.bin"]

    def test_an_unreadable_save_artifact_is_never_called_a_firmware_file(self):
        # Readable or not, a memory card the rule cards claim is not this scan's
        # subject — a caveat wondering whether it is undeclared firmware is the
        # same category error the exclusion exists to prevent.
        machine = _machine(
            {
                f"{INFO_DIR}/flycast_libretro.info": DC_INFO,
                f"{INFO_DIR}/flycast_libretro.so": {"status": "invalid-text"},
            },
            inaccessible=[f"{BIOS_DIR}/dc/vmu_save_A1.bin"],
        )
        answer = firmware_inventory(machine, _context(machine))
        assert answer.unclaimed == ()
        assert [c.code for c in answer.caveats] == []

    def test_the_rule_cards_name_the_save_artifacts(self):
        artifacts = save_artifact_paths()
        assert "dc/vmu_save_A1.bin" in artifacts
        assert "pcsx2/memcards/Mcd001.ps2" in artifacts


class TestIdentification:
    """Criterion 4: content in, every destination that wants it out."""

    def test_one_content_answers_every_destination_that_wants_it(self):
        machine = _gb_machine()
        identified = identify_firmware(machine, _context(machine), md5="ee" * 16)
        assert identified.known_as == ("dmg_boot.bin", "gb_bios.bin")
        assert [(r.core_so, r.path) for r in identified.requirements] == [
            ("sameboy_libretro.so", f"{BIOS_DIR}/dmg_boot.bin"),
            ("gambatte_libretro.so", f"{BIOS_DIR}/gb_bios.bin"),
        ]

    def test_unrecognised_content_says_so_instead_of_answering_nothing(self):
        machine = _gb_machine()
        identified = identify_firmware(machine, _context(machine), md5="99" * 16)
        assert identified.identity is None
        assert identified.requirements == ()
        assert CAVEAT_FIRMWARE_CONTENT_UNIDENTIFIED in [c.code for c in identified.caveats]

    def test_recognised_content_nobody_here_wants_is_not_a_silent_empty(self):
        machine = _machine()  # only the PSX core is installed
        identified = identify_firmware(machine, _context(machine), md5="ee" * 16)
        assert identified.identity is not None
        assert identified.requirements == ()
        assert CAVEAT_NO_FIRMWARE_DECLARATION in [c.code for c in identified.caveats]

    def test_size_must_agree_when_it_is_supplied(self):
        machine = _gb_machine()
        assert identify_firmware(machine, _context(machine), md5="ee" * 16, size=99).identity is None

    def test_a_request_that_contradicts_itself_says_so_instead_of_blaming_the_table(self):
        # An md5 from one file and a sha1 from another: the table knows both,
        # just not together. Reporting "unknown content" would send the caller
        # looking in the wrong place.
        machine = _gb_machine()
        identified = identify_firmware(machine, _context(machine), md5="ee" * 16, sha1="bb" * 20)
        codes = [c.code for c in identified.caveats]
        assert CAVEAT_FIRMWARE_CONTENT_CONTRADICTORY in codes
        assert CAVEAT_FIRMWARE_CONTENT_UNIDENTIFIED not in codes

    def test_a_known_digest_with_a_size_no_entry_carries_is_contradictory_too(self):
        # The table recognises this md5 perfectly; the size is the caller's own
        # and matches no entry. "Unidentified" would blame the table for the
        # one field it got right.
        machine = _gb_machine()
        identified = identify_firmware(machine, _context(machine), md5="ee" * 16, size=999)
        caveat = next(c for c in identified.caveats if c.code == CAVEAT_FIRMWARE_CONTENT_CONTRADICTORY)
        assert CAVEAT_FIRMWARE_CONTENT_UNIDENTIFIED not in [c.code for c in identified.caveats]
        # And the rejected value is in the answer: told only the md5, a caller
        # cannot see which of its fields the table disagreed with.
        assert caveat.data == {"md5": "ee" * 16, "size": "999"}

    def test_an_unidentified_request_carries_every_field_it_stated(self):
        machine = _gb_machine()
        identified = identify_firmware(machine, _context(machine), md5="99" * 16, size=7)
        caveat = next(c for c in identified.caveats if c.code == CAVEAT_FIRMWARE_CONTENT_UNIDENTIFIED)
        assert caveat.data == {"md5": "99" * 16, "size": "7"}

    def test_a_request_naming_no_content_is_answered_not_raised(self):
        # A size is not an identity — but a public question is answered in the
        # grammar of this module, not by an exception out of the table below it.
        machine = _gb_machine()
        identified = identify_firmware(machine, _context(machine), size=5)
        assert identified.identity is None
        assert identified.requirements == ()
        caveat = next(c for c in identified.caveats if c.code == CAVEAT_FIRMWARE_CONTENT_UNSTATED)
        assert caveat.data == {"size": "5"}

    def test_a_request_naming_nothing_at_all_is_answered_too(self):
        machine = _gb_machine()
        identified = identify_firmware(machine, _context(machine))
        assert [c.code for c in identified.caveats] == [CAVEAT_FIRMWARE_CONTENT_UNSTATED]

    def test_genuinely_unknown_content_still_blames_nobody(self):
        machine = _gb_machine()
        identified = identify_firmware(machine, _context(machine), md5="99" * 16, sha1="99" * 20)
        assert CAVEAT_FIRMWARE_CONTENT_UNIDENTIFIED in [c.code for c in identified.caveats]

    def test_identification_answers_exactly_what_the_inventory_holds(self):
        # The two routes resolve the same declarations, so an identification is
        # the inventory's requirement list filtered by content — never a
        # different set, and never a different order.
        machine = _gb_machine({f"{BIOS_DIR}/gb_bios.bin": _blob(b"boot!")})
        context = _context(machine)
        identified = identify_firmware(machine, context, md5="ee" * 16)
        from_inventory = tuple(
            r
            for r in firmware_inventory(machine, context).requirements
            if r.identity is not None and r.identity.md5 == "ee" * 16
        )
        assert identified.requirements == from_inventory

    def test_identifying_content_does_not_walk_the_firmware_tree(self):
        """A lookup by bytes must not pay for the scan that answers another question.

        Which requirements want this content comes from the declarations plus a
        look at each destination. The unclaimed scan globs and stats every
        directory a declaration references to find files *nobody* declared —
        none of which reaches this answer.
        """
        inner = _gb_machine({f"{BIOS_DIR}/stray.bin": _blob(b"stray")})
        context = _context(inner)
        counted = _CountingMachine(inner)
        identified = identify_firmware(counted, context, md5="ee" * 16)
        assert [r.path for r in identified.requirements] == [
            f"{BIOS_DIR}/dmg_boot.bin",
            f"{BIOS_DIR}/gb_bios.bin",
        ]
        assert counted.calls.get("glob", 0) == 0
        assert counted.calls.get("file_digest", 0) == 0


class TestIdentificationNamesTheFolderADeclarationOpens:
    """A folder declaration is a destination too — the folder, and never a name inside it.

    LRPS2 declares ``pcsx2/bios`` and no file in it, so the requirement carries
    no identity to compare and a content-matched answer used to name nothing at
    all: the bytes were recognised and the download flow had no ``req.path`` to
    copy to. What ties the two halves together is the curated row's identity
    prefix — the same prefix the folder verdict judges the files already inside
    against, read the other way round.
    """

    def _lrps2(
        self, files: Mapping[str, FixtureFileSpec] | None = None, **kwargs: object
    ) -> FixtureMachine:
        tree: dict[str, FixtureFileSpec] = {
            f"{INFO_DIR}/pcsx2_libretro.info": LRPS2_FOLDER_INFO,
            f"{INFO_DIR}/pcsx2_libretro.so": {"status": "invalid-text"},
        }
        tree.update(files or {})
        return _machine(tree, **kwargs)

    def test_content_filed_under_the_prefix_answers_the_folder_that_is_there(self):
        machine = self._lrps2(dirs=[LRPS2_FOLDER])
        identified = identify_firmware(machine, _context(machine), md5="77" * 16)
        assert [(r.core_so, r.declared_kind, r.path, r.found) for r in identified.requirements] == [
            (LRPS2_SO, DECLARED_DIRECTORY, LRPS2_FOLDER, KIND_DIRECTORY)
        ]

    def test_the_folder_is_named_where_it_is_not_there_yet(self):
        # The install flow's own case: nothing is at the destination, and the
        # answer is the folder to create rather than an empty list plus a
        # caveat saying nobody wants these bytes.
        machine = self._lrps2()
        identified = identify_firmware(machine, _context(machine), md5="77" * 16)
        assert [(r.path, r.found) for r in identified.requirements] == [(LRPS2_FOLDER, KIND_MISSING)]
        assert CAVEAT_NO_FIRMWARE_DECLARATION not in [c.code for c in identified.caveats]

    def test_the_destination_is_the_folder_and_never_a_name_inside_it(self):
        # The core lists the folder and validates by header, so the name is the
        # caller's: atlas states the declaration's own basename and leaves the
        # table's name where a caller that wants a conventional one reads it.
        machine = self._lrps2(dirs=[LRPS2_FOLDER])
        identified = identify_firmware(machine, _context(machine), md5="77" * 16)
        assert [(r.file_name, r.path) for r in identified.requirements] == [("bios", LRPS2_FOLDER)]
        assert identified.known_as == ("pcsx2/bios/ps2-0200e-20040614.bin",)

    def test_a_folder_declaration_answers_only_the_content_its_own_prefix_files(self):
        # scph5501.bin's bytes are in the table and not under this prefix, so
        # the folder is no destination for them — the match is the row's
        # prefix, never "some directory declaration is installed".
        machine = self._lrps2(dirs=[LRPS2_FOLDER])
        identified = identify_firmware(machine, _context(machine), md5="aa" * 16)
        assert [r.core_so for r in identified.requirements] == ["demo_psx_libretro.so"]

    def test_content_under_the_prefix_is_still_empty_where_that_core_is_not_installed(self):
        # Nothing about the prefix is a claim about the machine: with no core
        # declaring the folder, the established absence stands exactly as it did.
        machine = _machine()
        identified = identify_firmware(machine, _context(machine), md5="77" * 16)
        assert identified.identity is not None
        assert identified.requirements == ()
        assert CAVEAT_NO_FIRMWARE_DECLARATION in [c.code for c in identified.caveats]

    def test_one_content_answers_a_folder_and_a_file_destination_at_once(self):
        # Two cores want the same image, one by listing the folder it may sit
        # in and one by opening it under the table's own name. Each declaring
        # core gets its own requirement, in its own shape.
        machine = self._lrps2(
            {
                f"{INFO_DIR}/ps2byname_libretro.info": PS2_IMAGE_BY_NAME_INFO,
                f"{INFO_DIR}/ps2byname_libretro.so": {"status": "invalid-text"},
            },
            dirs=[LRPS2_FOLDER],
        )
        identified = identify_firmware(machine, _context(machine), md5="77" * 16)
        assert [(r.core_so, r.declared_kind, r.path) for r in identified.requirements] == [
            (LRPS2_SO, DECLARED_DIRECTORY, LRPS2_FOLDER),
            ("ps2byname_libretro.so", DECLARED_FILE, f"{LRPS2_FOLDER}/ps2-0200e-20040614.bin"),
        ]

    def test_a_file_declaration_is_still_matched_by_identity_alone(self):
        # The file half is untouched: a core that declares a name the table
        # covers answers for the bytes that name pins and for nothing else.
        machine = self._lrps2(
            {
                f"{INFO_DIR}/ps2byname_libretro.info": PS2_IMAGE_BY_NAME_INFO,
                f"{INFO_DIR}/ps2byname_libretro.so": {"status": "invalid-text"},
            }
        )
        identified = identify_firmware(machine, _context(machine), md5="5a" * 16)
        assert [r.core_so for r in identified.requirements] == [LRPS2_SO]


class TestNoDeclarationIsNeverSatisfied:
    """The defect the whole design exists to prevent."""

    def test_an_unknown_core_answers_unknown_not_nothing(self):
        machine = _machine()
        answer = firmware_for_core(machine, _context(machine), core_so="mgba_libretro.so")
        assert answer.cores[0].declaration == DECLARATION_ABSENT
        assert answer.cores[0].requirements == ()
        assert answer.cores[0].requirements_met is None
        assert [c.code for c in answer.cores[0].caveats] == [CAVEAT_CORE_NOT_INSTALLED]
        # Not "nothing declares firmware": this core may declare plenty, it is
        # simply not here.
        assert [c.code for c in answer.caveats] == [CAVEAT_CORE_NOT_INSTALLED]

    def test_an_identifier_nothing_covers_says_unknown_not_nothing_needed(self):
        machine = _machine()
        answer = firmware_for_system(machine, _context(machine), system="n64")
        assert answer.cores == ()
        codes = [c.code for c in answer.caveats]
        assert CAVEAT_SYSTEM_UNKNOWN in codes
        assert CAVEAT_NO_FIRMWARE_DECLARATION not in codes

    def test_a_known_system_whose_emulators_cannot_be_read_is_a_different_code(self):
        # The catalogue knows the system, so the identifier is right; what is
        # missing is the cores. Nothing was read here, so the answer may not
        # say the system declares nothing — only that it could not be
        # established, which is never "this system needs nothing".
        machine = _machine()
        catalogue = Catalogue((CatalogueEntry(label="TGB Dual", kind="libretro", core_so="tgbdual_libretro.so"),))
        answer = firmware_for_system(machine, _context(machine), system="gb", catalogue=catalogue)
        codes = [c.code for c in answer.caveats]
        assert CAVEAT_FIRMWARE_DECLARATION_UNKNOWN in codes
        assert CAVEAT_NO_FIRMWARE_DECLARATION not in codes
        assert CAVEAT_SYSTEM_UNKNOWN not in codes
        assert [c.declaration for c in answer.cores] == [DECLARATION_ABSENT]

    def test_an_unresolvable_info_directory_yields_no_requirements(self):
        # What production hands over when libretro_info_path does not resolve:
        # no cores, and cores_read false to say the enumeration never ran.
        machine = FixtureMachine({f"{BIOS_DIR}/scph5501.bin": _blob(b"12345678")})
        context = FirmwareContext(root=BIOS_DIR, cores=(), hashes=load_hashes(TABLE), cores_read=False)
        answer = firmware_inventory(machine, context)
        assert answer.requirements == ()
        assert CAVEAT_FIRMWARE_DECLARATION_UNKNOWN in [c.code for c in answer.caveats]

    def test_a_system_query_that_could_not_enumerate_claims_nothing(self):
        # The cores were never read, so "no emulator covers gba" would be a
        # statement about the machine derived from a read failure — and so
        # would "nothing declares firmware for it".
        machine = FixtureMachine({})
        context = FirmwareContext(root=BIOS_DIR, cores=(), hashes=load_hashes(TABLE), cores_read=False)
        answer = firmware_for_system(machine, context, system="gba")
        codes = [c.code for c in answer.caveats]
        assert CAVEAT_SYSTEM_UNKNOWN not in codes
        assert CAVEAT_NO_FIRMWARE_DECLARATION not in codes
        assert CAVEAT_FIRMWARE_DECLARATION_UNKNOWN in codes

    def test_an_unreadable_catalogue_claims_nothing_either(self):
        machine = _machine()
        answer = firmware_for_system(
            machine, _context(machine), system="gba", catalogue=Catalogue((), read=False)
        )
        codes = [c.code for c in answer.caveats]
        assert CAVEAT_EMULATOR_CATALOGUE_UNREADABLE in codes
        assert CAVEAT_SYSTEM_UNKNOWN not in codes
        assert CAVEAT_FIRMWARE_DECLARATION_UNKNOWN in codes

    def test_a_holed_catalogue_with_no_entries_is_a_failed_look(self):
        # The third state between read and unread: part of the catalogue could
        # not be consulted, so an empty enumeration says nothing about the
        # machine — the hole is stated, the empty is declaration-unknown, and
        # system-unknown (a machine claim) may not fire.
        machine = _machine()
        hole = Caveat("emulator-catalogue-sealed", "part of the catalogue is sealed away")
        answer = firmware_for_system(
            machine, _context(machine), system="gba", catalogue=Catalogue((), hole=hole)
        )
        codes = [c.code for c in answer.caveats]
        assert "emulator-catalogue-sealed" in codes
        assert CAVEAT_SYSTEM_UNKNOWN not in codes
        assert CAVEAT_FIRMWARE_DECLARATION_UNKNOWN in codes

    def test_a_holed_catalogue_with_entries_enumerates(self):
        # The readable part is authoritative for what it declares: entries
        # resolve exactly as a read catalogue's do, with the hole stated
        # beside them rather than degrading them.
        machine = _machine()
        hole = Caveat("emulator-catalogue-sealed", "part of the catalogue is sealed away")
        catalogue = Catalogue(
            (CatalogueEntry(label="Demo PSX", kind="libretro", core_so="demo_psx_libretro.so"),),
            hole=hole,
        )
        answer = firmware_for_system(machine, _context(machine), system="psx", catalogue=catalogue)
        assert [c.label for c in answer.cores] == ["Demo PSX"]
        assert answer.cores[0].requirements
        codes = [c.code for c in answer.caveats]
        assert "emulator-catalogue-sealed" in codes
        assert CAVEAT_SYSTEM_UNKNOWN not in codes

    def test_a_holed_catalogue_that_named_entries_did_enumerate(self):
        # The other half of the hole semantics, and the mainstream sealed
        # case: an entry whose core was read and declares no firmware is the
        # per-entry answer — declaration="read", empty list — and no
        # answer-level declaration-unknown may ride it, or every sealed
        # answer with a firmware-less emulator would read as a failed look.
        machine = _machine(
            {
                f"{INFO_DIR}/virtualjaguar_libretro.info": (
                    'display_name = "Atari - Jaguar (Virtual Jaguar)"\nsystemname = "Jaguar"\n'
                ),
                f"{INFO_DIR}/virtualjaguar_libretro.so": {"status": "invalid-text"},
            }
        )
        hole = Caveat("emulator-catalogue-sealed", "part of the catalogue is sealed away")
        catalogue = Catalogue(
            (
                CatalogueEntry(
                    label="Virtual Jaguar", kind="libretro", core_so="virtualjaguar_libretro.so"
                ),
            ),
            hole=hole,
        )
        answer = firmware_for_system(
            machine, _context(machine), system="atarijaguar", catalogue=catalogue
        )
        assert [c.declaration for c in answer.cores] == [DECLARATION_READ]
        codes = [c.code for c in answer.caveats]
        assert "emulator-catalogue-sealed" in codes
        assert CAVEAT_FIRMWARE_DECLARATION_UNKNOWN not in codes

    def test_a_core_query_that_could_not_enumerate_claims_no_absence(self):
        machine = FixtureMachine({})
        context = FirmwareContext(root=BIOS_DIR, cores=(), hashes=load_hashes(TABLE), cores_read=False)
        answer = firmware_for_core(machine, context, core_so="mgba_libretro.so")
        codes = [c.code for c in answer.caveats]
        assert CAVEAT_CORE_NOT_INSTALLED not in codes
        assert CAVEAT_FIRMWARE_DECLARATION_UNKNOWN in codes

    def test_an_answer_whose_declarations_were_all_refused_does_not_claim_none(self):
        # M14's first route: the core declares a required file and every
        # declaration was refused. Saying "nothing declares firmware" here
        # contradicts the very same answer's refused list.
        machine = FixtureMachine(
            {
                f"{INFO_DIR}/escape_libretro.info": (
                    'systemname = "Sony - PlayStation"\n'
                    "firmware_count = 1\n"
                    'firmware0_path = "etclink/shadow"\n'
                    'firmware0_opt = "false"\n'
                ),
                f"{INFO_DIR}/escape_libretro.so": {"status": "invalid-text"},
                "/etc/shadow": "root:!:0:0:::",
                f"{BIOS_DIR}/keep.bin": _blob(b"12345678"),
            },
            symlinks={f"{BIOS_DIR}/etclink": "/etc"},
        )
        answer = firmware_inventory(machine, _context(machine))
        assert [r.declared for r in answer.cores[0].refused] == ["etclink/shadow"]
        codes = [c.code for c in answer.caveats]
        assert CAVEAT_NO_FIRMWARE_REQUIREMENT in codes
        assert CAVEAT_NO_FIRMWARE_DECLARATION not in codes

    def test_an_answer_whose_declarations_are_all_unread_does_not_claim_none(self):
        # M14's third route: the .info plainly declares two paths and its own
        # firmware_count enumerates neither, so nothing is required — but
        # "nothing is declared" is not what the file says.
        machine = FixtureMachine(
            {
                f"{INFO_DIR}/nocount_libretro.info": (
                    'systemname = "Sony - PlayStation"\n'
                    'firmware0_path = "scph5501.bin"\n'
                    'firmware0_opt = "false"\n'
                ),
                f"{INFO_DIR}/nocount_libretro.so": {"status": "invalid-text"},
                f"{BIOS_DIR}/scph5501.bin": _blob(b"12345678"),
            }
        )
        answer = firmware_inventory(machine, _context(machine))
        core = answer.cores[0]
        assert core.requirements == ()
        assert core.requirements_met is True, "RetroArch asks for nothing here, and that is honest"
        assert CAVEAT_FIRMWARE_DECLARATION_UNREAD in [c.code for c in core.caveats]
        codes = [c.code for c in answer.caveats]
        assert CAVEAT_NO_FIRMWARE_REQUIREMENT in codes
        assert CAVEAT_NO_FIRMWARE_DECLARATION not in codes

    def test_an_identity_whose_declaration_was_refused_is_not_wanted_nowhere(self):
        # The same distinction on the identification route: a core asked for
        # exactly these bytes and the declaration was refused, so "no installed
        # core declares a file with this identity" would contradict the machine.
        machine = FixtureMachine(
            {
                f"{INFO_DIR}/escape_libretro.info": (
                    'systemname = "Sony - PlayStation"\n'
                    "firmware_count = 1\n"
                    'firmware0_path = "etclink/scph5501.bin"\n'
                    'firmware0_opt = "false"\n'
                ),
                f"{INFO_DIR}/escape_libretro.so": {"status": "invalid-text"},
                "/etc/scph5501.bin": _blob(b"12345678"),
            },
            symlinks={f"{BIOS_DIR}/etclink": "/etc"},
        )
        identified = identify_firmware(machine, _context(machine), md5="aa" * 16)
        assert identified.requirements == ()
        codes = [c.code for c in identified.caveats]
        assert CAVEAT_NO_FIRMWARE_REQUIREMENT in codes
        assert CAVEAT_NO_FIRMWARE_DECLARATION not in codes

    def test_an_identity_nobody_declares_is_still_an_established_absence(self):
        # The neighbour of the case above: everything was read, and no core
        # asks for these bytes. That is an answer, not a hole.
        machine = _machine()  # only the PSX core, which declares other files
        identified = identify_firmware(machine, _context(machine), md5="ee" * 16)
        codes = [c.code for c in identified.caveats]
        assert CAVEAT_NO_FIRMWARE_DECLARATION in codes
        assert CAVEAT_NO_FIRMWARE_REQUIREMENT not in codes

    def test_an_identity_is_not_absent_while_a_core_declares_what_nobody_reads(self):
        # An unread declaration is known by the key it was declared under, not
        # by the path it named, so it can never be tied to an identity — which
        # is exactly why it may not be answered as an established absence. The
        # inventory calls this machine "declared, nothing required"; the
        # identification must not call it "nothing declares these bytes".
        machine = _machine(
            {
                f"{INFO_DIR}/nocount_libretro.info": (
                    'systemname = "Nintendo - Game Boy"\n'
                    'firmware0_path = "gb_bios.bin"\n'
                    'firmware0_opt = "true"\n'
                ),
                f"{INFO_DIR}/nocount_libretro.so": {"status": "invalid-text"},
            }
        )
        identified = identify_firmware(machine, _context(machine), md5="ee" * 16)
        assert identified.requirements == ()
        codes = [c.code for c in identified.caveats]
        assert CAVEAT_NO_FIRMWARE_REQUIREMENT in codes
        assert CAVEAT_NO_FIRMWARE_DECLARATION not in codes

    def test_an_unread_key_with_no_path_behind_it_leaves_the_absence_standing(self):
        # The reach of the case above is its own premise: the unread key counts
        # because it MAY have named these bytes. An empty value names no file,
        # so it cannot have — neither inside the count, where RetroArch looks
        # the key up and discards what it finds, nor outside it, where the
        # lookup never happens. Both stay stated on the core; neither may turn
        # an established absence into an unresolved one.
        for info in ('firmware_count = 1\nfirmware0_path = ""\n', 'firmware_count = 1\nfirmware5_path = ""\n'):
            machine = _machine(
                {
                    f"{INFO_DIR}/empty_libretro.info": 'systemname = "Nintendo - Game Boy"\n' + info,
                    f"{INFO_DIR}/empty_libretro.so": {"status": "invalid-text"},
                }
            )
            context = _context(machine)
            core = next(c for c in firmware_inventory(machine, context).cores if c.core_so == "empty_libretro.so")
            assert CAVEAT_FIRMWARE_DECLARATION_UNREAD in [c.code for c in core.caveats], info
            codes = [c.code for c in identify_firmware(machine, context, md5="ee" * 16).caveats]
            assert CAVEAT_NO_FIRMWARE_DECLARATION in codes, info
            assert CAVEAT_NO_FIRMWARE_REQUIREMENT not in codes, info

    def test_one_core_atlas_could_not_read_withdraws_the_whole_absence(self):
        # An absence is a claim about EVERY emulator in the answer. One core
        # whose .info could not be read leaves what it wants unknown, so the
        # answer may not say "nothing declares this" over it — however many
        # of its neighbours were read.
        machine = FixtureMachine(
            {
                f"{INFO_DIR}/snes9x_libretro.info": NO_FIRMWARE_INFO,
                f"{INFO_DIR}/snes9x_libretro.so": {"status": "invalid-text"},
                f"{INFO_DIR}/flycast_libretro.info": {"status": "unreadable"},
                f"{INFO_DIR}/flycast_libretro.so": {"status": "invalid-text"},
            }
        )
        context = _context(machine)
        for codes in (
            [c.code for c in firmware_inventory(machine, context).caveats],
            [c.code for c in identify_firmware(machine, context, md5="ee" * 16).caveats],
        ):
            assert CAVEAT_FIRMWARE_DECLARATION_UNKNOWN in codes
            assert CAVEAT_NO_FIRMWARE_DECLARATION not in codes

    def test_a_system_whose_emulators_declare_nothing_states_it_per_emulator(self):
        # The other side of the split, and the one that needs no answer-level
        # line: every emulator listed was read and declares no firmware, which
        # each entry says itself — the per-core route answers the same fact the
        # same way. An answer-level caveat here would read as a degradation
        # where there is none.
        machine = _machine({f"{INFO_DIR}/snes9x_libretro.info": NO_FIRMWARE_INFO})
        answer = firmware_for_system(machine, _context(machine, core_dir=None), system="snes")
        assert [(c.declaration, c.requirements) for c in answer.cores] == [(DECLARATION_READ, ())]
        assert [c.code for c in answer.caveats] == [CAVEAT_EMULATOR_CATALOGUE_UNAVAILABLE]

    def test_a_standalone_beside_a_read_core_is_not_silence(self):
        # The mixed shape, and the reason silence needs EVERY emulator read:
        # the catalogue lists a standalone whose firmware rules are not
        # resolvable at all, so "nothing here declares firmware for dc" would
        # be an absence claimed over an emulator atlas cannot read. On the
        # reference machine 28 systems have exactly this shape.
        machine = _machine(
            {
                f"{INFO_DIR}/snes9x_libretro.info": NO_FIRMWARE_INFO,
                f"{INFO_DIR}/snes9x_libretro.so": {"status": "invalid-text"},
            }
        )
        catalogue = Catalogue(
            (
                CatalogueEntry(label="Snes9x", kind="libretro", core_so="snes9x_libretro.so"),
                CatalogueEntry(label="Flycast (Standalone)", kind="standalone", core_so=None),
            )
        )
        answer = firmware_for_system(machine, _context(machine), system="snes", catalogue=catalogue)
        # The standalone is 'unsupported', not 'absent': it is installed, and
        # what atlas is missing is a source for its rules, not the emulator.
        assert [c.declaration for c in answer.cores] == [DECLARATION_READ, DECLARATION_UNSUPPORTED]
        assert [c.code for c in answer.caveats] == [CAVEAT_FIRMWARE_DECLARATION_UNKNOWN]

    def test_the_catalogue_route_states_the_declarations_nobody_reads(self):
        # Both routes resolve the same core, so both must state the same facts
        # about it: a declaration outside its own enumeration is stated whether
        # the core was reached through the catalogue or through its systemname.
        info = (
            'systemname = "Sony - PlayStation"\n'
            "firmware_count = 1\n"
            'firmware0_path = "scph5501.bin"\n'
            'firmware0_opt = "false"\n'
            'firmware1_path = "psxonpsp660.bin"\n'
            'firmware1_opt = "false"\n'
        )
        machine = _machine({f"{INFO_DIR}/demo_psx_libretro.info": info})
        context = _context(machine)
        catalogue = Catalogue(
            (CatalogueEntry(label="Demo PSX", kind="libretro", core_so="demo_psx_libretro.so"),)
        )
        through_catalogue = firmware_for_system(machine, context, system="psx", catalogue=catalogue)
        through_systemname = firmware_for_system(machine, context, system="psx")
        for answer in (through_catalogue, through_systemname):
            core = answer.cores[0]
            assert [r.declared for r in _plain_requirements(core)] == ["scph5501.bin"]
            assert CAVEAT_FIRMWARE_DECLARATION_UNREAD in [c.code for c in core.caveats]

    def test_without_a_root_there_is_nothing_to_resolve_against(self):
        machine = _machine()
        context = FirmwareContext(
            root=None,
            cores=read_core_declarations(machine, INFO_DIR, core_dir=INFO_DIR).cores,
            hashes=load_hashes(TABLE),
            caveats=(Caveat(CAVEAT_SYSTEM_DIRECTORY_CLEARED, "system_directory is cleared in the configs"),),
        )
        for answer in (
            firmware_for_core(machine, context, core_so="demo_psx_libretro.so"),
            firmware_for_system(machine, context, system="psx"),
            firmware_inventory(machine, context),
        ):
            assert answer.root is None
            assert answer.cores == ()
            assert answer.unclaimed == ()
            # The reason there is no root IS the answer here: an empty answer
            # that states nothing can only be read as "nothing needed".
            assert [c.code for c in answer.caveats] == [CAVEAT_SYSTEM_DIRECTORY_CLEARED]

    def test_an_empty_answer_that_states_nothing_cannot_be_built(self):
        # The invariant behind the loop above: production seeds the reason into
        # the context, and an answer without one is refused rather than shipped.
        machine = _machine()
        context = FirmwareContext(root=None, cores=(), hashes=load_hashes(TABLE))
        with pytest.raises(ValueError, match="must state why"):
            firmware_inventory(machine, context)
        with pytest.raises(ValueError, match="must state why"):
            FirmwareAnswer(root=None, cores=(), unclaimed=(), hash_checked=False, sources=(), caveats=())


def _states_a_verified_wrong_file(core: CoreFirmware) -> bool:
    """Whether this core reports any file, required or optional, whose bytes are known to be wrong."""
    return any(r.checked == CHECKED_MISMATCH for r in _plain_requirements(core))


def _assert_every_required_file_is_really_there(core: CoreFirmware) -> None:
    """What an all-clear core promises about its required files, file by file."""
    for requirement in _plain_requirements(core):
        if requirement.need != NEED_REQUIRED:
            continue
        assert requirement.found == "file", "all-clear over something that is not a file"
        assert requirement.checked != CHECKED_MISMATCH, (
            "all-clear over a file whose bytes are known to be wrong"
        )
        assert not (
            requirement.checked == CHECKED_UNKNOWN and requirement.identity is not None
        ), "all-clear over a file whose identity could not be established"


class TestPartialReaderIsNotMisled:
    """A caller that renders one field must never be shown something false.

    The review's question, answered as a test: across every answer shape this
    module can produce, no single field can be read as "all good" when it is
    not. Uninformed is acceptable; wrong is not.
    """

    @staticmethod
    def _answers():
        installed = _machine({f"{BIOS_DIR}/scph5501.bin": _blob(b"12345678")})
        gb = _gb_machine({f"{BIOS_DIR}/gb_bios.bin": _blob(b"boot!")})
        empty = _machine()
        unreadable = _machine({f"{BIOS_DIR}/scph5501.bin": {"size": 8}})
        for machine, answer in (
            (installed, firmware_for_core(installed, _context(installed), core_so="demo_psx_libretro.so")),
            (installed, firmware_inventory(installed, _context(installed), verify=True)),
            (gb, firmware_for_system(gb, _context(gb), system="gb", verify=True)),
            (empty, firmware_for_core(empty, _context(empty), core_so="mgba_libretro.so")),
            (empty, firmware_for_system(empty, _context(empty), system="n64")),
            (unreadable, firmware_inventory(unreadable, _context(unreadable), verify=True)),
        ):
            yield machine, answer

    def test_an_empty_requirement_list_is_either_explained_or_genuinely_empty(self):
        for _, answer in self._answers():
            for core in answer.cores:
                if core.requirements:
                    continue
                assert core.declaration == DECLARATION_READ or core.caveats, (
                    "an empty list from a core atlas could not read must say so"
                )

    def test_requirements_never_come_from_a_core_that_was_not_read(self):
        for _, answer in self._answers():
            for core in answer.cores:
                assert core.declaration == DECLARATION_READ or not core.requirements

    def test_a_verdict_never_appears_without_verification(self):
        for _, answer in self._answers():
            if answer.hash_checked:
                continue
            assert all(r.checked not in ("verified", "mismatch") for r in answer.requirements)

    def test_presence_and_the_check_never_disagree(self):
        for _, answer in self._answers():
            for requirement in answer.requirements:
                # Identity, not truthiness: present=False and present=None are
                # different answers and only one of them is falsy by accident.
                assert (requirement.checked is None) is (requirement.present is not True)

    def test_an_unidentifiable_file_never_reads_as_merely_unchecked(self):
        for _, answer in self._answers():
            for requirement in answer.requirements:
                if requirement.present and requirement.identity is None:
                    assert requirement.checked == "unknown"

    def test_an_unclaimed_file_never_carries_a_name_it_was_not_matched_by(self):
        for _, answer in self._answers():
            for unclaimed in answer.unclaimed:
                assert (unclaimed.known_as == ()) is (unclaimed.identity is None)

    def test_requirements_met_is_never_true_over_a_file_that_is_not_right(self):
        """The invariant stated against the requirements, not against the property.

        Asserting ``not core.unmet`` here would be a tautology — ``unmet`` is
        what ``requirements_met`` is defined from, so the test could not fail
        for any implementation. What must hold is a statement about the *files*:
        if this core reports all-clear, then every required file is really
        there, its bytes are not known to be wrong, and none of them was left
        undecided.
        """
        seen_mismatch = False
        for _, answer in self._answers():
            for core in answer.cores:
                if _states_a_verified_wrong_file(core):
                    seen_mismatch = True
                if core.requirements_met is not True:
                    continue
                assert core.declaration == DECLARATION_READ
                _assert_every_required_file_is_really_there(core)
        assert seen_mismatch, (
            "this class must exercise a verified-wrong file, required or optional, or it proves nothing "
            "about the case that broke"
        )

    def test_a_required_file_with_the_wrong_bytes_is_unmet(self):
        # The live case: ecwolf declares ecwolf.pk3 as required, the file is
        # there, and its bytes are a different release of the same pack.
        machine = _machine({f"{BIOS_DIR}/scph5501.bin": _blob(b"12345678")})
        core = firmware_for_core(
            machine, _context(machine), core_so="demo_psx_libretro.so", verify=True
        ).cores[0]
        wrong = next(r for r in _plain_requirements(core) if r.file_name == "scph5501.bin")
        assert wrong.found == "file"
        assert wrong.checked == CHECKED_MISMATCH
        assert wrong.satisfied is False
        assert [r.file_name for r in core.unmet] == ["scph5501.bin"]
        assert core.requirements_met is False

    def test_a_required_file_that_could_not_be_judged_leaves_it_undecided(self):
        machine = _machine({f"{BIOS_DIR}/scph5501.bin": {"size": 8}})
        core = firmware_for_core(
            machine, _context(machine), core_so="demo_psx_libretro.so", verify=True
        ).cores[0]
        undecided = next(r for r in _plain_requirements(core) if r.file_name == "scph5501.bin")
        assert undecided.found == "file"
        assert undecided.checked == CHECKED_UNREAD
        assert undecided.satisfied is None
        assert core.unmet == ()
        assert [r.file_name for r in core.undetermined] == ["scph5501.bin"]
        assert core.requirements_met is None


def _by_name(answer) -> dict[str, FirmwareRequirement]:
    return {r.file_name: r for r in answer.requirements}


class _CountingMachine:
    """A machine that answers like the one it wraps and counts every seam call.

    What a route reads is part of what it promises: a table lookup that walks
    the firmware tree costs a caller a glob and a stat per declared directory,
    and no assertion about the answer can see that.
    """

    def __init__(self, inner: FixtureMachine) -> None:
        self._inner = inner
        self.calls: dict[str, int] = {}

    def _count(self, operation: str) -> None:
        self.calls[operation] = self.calls.get(operation, 0) + 1

    def read_text(self, path: str) -> ReadResult:
        self._count("read_text")
        return self._inner.read_text(path)

    def read_appimage_text(self, path: str, inner_path: str) -> AppImageReadResult:
        self._count("read_appimage_text")
        return self._inner.read_appimage_text(path, inner_path)

    def read_ps2_bios_header(self, path: str) -> Ps2BiosHeaderResult:
        self._count("read_ps2_bios_header")
        return self._inner.read_ps2_bios_header(path)

    def list_archive(self, path: str) -> ArchiveListResult:
        self._count("list_archive")
        return self._inner.list_archive(path)

    def read_whdload_slave(self, path: str) -> WhdloadSlaveResult:
        self._count("read_whdload_slave")
        return self._inner.read_whdload_slave(path)

    def glob(self, pattern: str) -> GlobResult:
        self._count("glob")
        return self._inner.glob(pattern)

    def path_kind(self, path: str) -> PathKind:
        self._count("path_kind")
        return self._inner.path_kind(path)

    def readlink(self, path: str) -> str | None:
        self._count("readlink")
        return self._inner.readlink(path)

    def query_core(self, so_path: str) -> CoreInfo | None:
        self._count("query_core")
        return self._inner.query_core(so_path)

    def read_core(self, so_path: str) -> CoreReading:
        self._count("read_core")
        return self._inner.read_core(so_path)

    def file_size(self, path: str) -> int | None:
        self._count("file_size")
        return self._inner.file_size(path)

    def file_digest(self, path: str, algorithm: str, *, first_bytes: int | None = None) -> str | None:
        self._count("file_digest")
        return self._inner.file_digest(path, algorithm, first_bytes=first_bytes)


def _gb_machine(files: Mapping[str, FixtureFileSpec] | None = None) -> FixtureMachine:
    tree: dict[str, FixtureFileSpec] = {
        f"{INFO_DIR}/gambatte_libretro.info": GAMBATTE_INFO,
        f"{INFO_DIR}/gambatte_libretro.so": {"status": "invalid-text"},
        f"{INFO_DIR}/sameboy_libretro.info": SAMEBOY_INFO,
        f"{INFO_DIR}/sameboy_libretro.so": {"status": "invalid-text"},
    }
    if files is not None:
        tree.update(files)
    return FixtureMachine(tree)  # type: ignore[arg-type]


def test_identity_equality_is_content_equality():
    left = FirmwareIdentity(md5="a" * 32, sha1="b" * 40, size=4, kind="file", known_as=("x",))
    right = FirmwareIdentity(md5="a" * 32, sha1="b" * 40, size=4, kind="file", known_as=("x",))
    assert left == right


class TestUnsupportedIsNotAbsent:
    """Two claims the old vocabulary spelled the same way.

    ``absent`` is about the machine — no such core here. ``unsupported`` is
    about atlas — the emulator is here and there is no source for what it
    wants. A client deciding whether to offer "install this core" needs them
    apart, and the placement route already kept them apart.
    """

    CATALOGUE = Catalogue(
        (
            CatalogueEntry(label="RPCS3 (Standalone)", kind="standalone", core_so=None),
            CatalogueEntry(label="Mupen64Plus-Next", kind="libretro", core_so="mupen64plus_next_libretro.so"),
        )
    )

    def _cores(self):
        machine = _machine({f"{INFO_DIR}/snes9x_libretro.info": NO_FIRMWARE_INFO})
        answer = firmware_for_system(machine, _context(machine), system="ps3", catalogue=self.CATALOGUE)
        return {core.label: core for core in answer.cores}

    def test_a_standalone_is_unsupported(self):
        assert self._cores()["RPCS3 (Standalone)"].declaration == DECLARATION_UNSUPPORTED

    def test_a_core_the_catalogue_names_but_nobody_installed_stays_absent(self):
        assert self._cores()["Mupen64Plus-Next"].declaration == DECLARATION_ABSENT

    def test_the_standalone_caveat_is_the_placement_routes_word(self):
        from atlas.placement import UNRESOLVED_STANDALONE

        codes = [c.code for c in self._cores()["RPCS3 (Standalone)"].caveats]
        assert codes == [UNRESOLVED_STANDALONE]

    def test_an_unsupported_declaration_still_means_unknown(self):
        # The tri-state rule holds for the new state: nothing was read, so
        # nothing may render as a green light.
        assert self._cores()["RPCS3 (Standalone)"].requirements_met is None

    def test_the_two_states_do_not_share_a_caveat_code(self):
        cores = self._cores()
        standalone = {c.code for c in cores["RPCS3 (Standalone)"].caveats}
        not_installed = {c.code for c in cores["Mupen64Plus-Next"].caveats}
        assert standalone & not_installed == set()


class TestBothRoutesCarryWhatWasDeclaredButNotRequired:
    """The catalogue route answers from the same fields as the per-core route.

    An empty answer must always say which kind of empty it is (item 13), and
    one of the three kinds — something is declared that never became a
    requirement — is read off ``CoreFirmware.unread`` and ``.refused``. Those
    are fields the answer carries, so a route that builds a ``CoreFirmware``
    without them turns that caveat off silently: the core still states the
    declaration in its own caveat, the shape still type-checks, and the answer
    quietly loses the sentence a client branches on.
    """

    # firmware0_path with no firmware_count: RetroArch's enumeration reads
    # nothing, so the file is declared and never asked for.
    INFO = 'display_name = "Nestopia"\nsystemname = "Nintendo - NES"\nfirmware0_path = "disksys.rom"\n'
    CATALOGUE = Catalogue(
        (CatalogueEntry(label="Nestopia", kind="libretro", core_so="nestopia_libretro.so"),)
    )

    def _answer(self, *, through_catalogue: bool):
        # The `.so` too: a declaration is only read for an installed core, and
        # without it both routes answer 'core-not-installed' instead.
        machine = _machine(
            {
                f"{INFO_DIR}/nestopia_libretro.info": self.INFO,
                f"{INFO_DIR}/nestopia_libretro.so": {"status": "invalid-text"},
            }
        )
        context = _context(machine)
        if through_catalogue:
            return firmware_for_system(machine, context, system="nes", catalogue=self.CATALOGUE)
        return firmware_for_core(machine, context, core_so="nestopia_libretro.so")

    def test_the_core_route_states_it_on_the_core(self):
        # A one-core question needs no answer-level restatement: the core it is
        # about says so itself, and that is where a client reads it.
        answer = self._answer(through_catalogue=False)
        assert CAVEAT_FIRMWARE_DECLARATION_UNREAD in [c.code for c in answer.cores[0].caveats]

    def test_the_catalogue_route_says_it_at_answer_level(self):
        # A per-system answer aggregates cores, so the answer level is the only
        # place the fact appears there — and it must not go missing because one
        # route built its CoreFirmware without the field it is read from.
        answer = self._answer(through_catalogue=True)
        assert [c.code for c in answer.caveats] == [CAVEAT_NO_FIRMWARE_REQUIREMENT]

    def test_the_catalogue_route_carries_the_unread_declaration(self):
        answer = self._answer(through_catalogue=True)
        assert answer.cores[0].unread == ("firmware0_path",)

    def test_neither_route_invents_a_requirement(self):
        assert [c.requirements for c in self._answer(through_catalogue=True).cores] == [()]


class TestASystemTheCatalogueDoesNotNameIsMarked:
    """The no-catalogue-word family: atlas's own spelling, and never unmarked."""

    def _core(self, info: str, stem: str):
        machine = _machine(
            {
                f"{INFO_DIR}/{stem}.info": info,
                f"{INFO_DIR}/{stem}.so": {"status": "invalid-text"},
            }
        )
        return firmware_for_core(machine, _context(machine), core_so=f"{stem}.so").cores[0]

    def test_the_own_spelling_carries_the_caveat(self):
        info = 'systemname = "TI83"\nfirmware_count = 1\nfirmware0_path = "ti83.rom"\n'
        core = self._core(info, "numero_libretro")
        assert _plain_requirements(core)[0].system == "ti83"
        caveat = next(c for c in core.caveats if c.code == CAVEAT_SYSTEM_NOT_IN_CATALOGUE)
        assert caveat.data == {
            "core_so": "numero_libretro.so",
            "system": "ti83",
            "files": ("ti83.rom",),
            "map_version": SYSTEMNAME_MAP_VERSION,
        }

    def test_the_enterprise_128_is_split_off_the_commodore(self):
        # Map version 1 misfiled ep128emu's "128" under the Commodore c128;
        # the split gives the Enterprise its own spelling, marked.
        info = (
            'systemname = "128"\n'
            "firmware_count = 1\n"
            'firmware0_path = "ep128emu/roms/exos21.rom"\n'
        )
        core = self._core(info, "ep128emu_core_libretro")
        assert _plain_requirements(core)[0].system == "ep128"
        assert CAVEAT_SYSTEM_NOT_IN_CATALOGUE in [c.code for c in core.caveats]

    def test_a_catalogue_id_is_never_marked(self):
        info = 'systemname = "Sega - Dreamcast"\nfirmware_count = 1\nfirmware0_path = "dc/dc_boot.bin"\n'
        core = self._core(info, "flycast_libretro")
        assert _plain_requirements(core)[0].system == "dreamcast"
        assert CAVEAT_SYSTEM_NOT_IN_CATALOGUE not in [c.code for c in core.caveats]

    def _ti83_machine(self):
        return _machine(
            {
                f"{INFO_DIR}/numero_libretro.info": (
                    'systemname = "TI83"\nfirmware_count = 1\nfirmware0_path = "ti83.rom"\n'
                ),
                f"{INFO_DIR}/numero_libretro.so": {"status": "invalid-text"},
            }
        )

    def test_the_own_spelling_is_a_question_word_on_the_catalogue_route_too(self):
        # A catalogue can neither enumerate nor deny a word no build declares,
        # so the same question answers the same rows with the same mark on a
        # catalogued arrangement — not system-unknown, and not a claim that
        # this machine lacks a catalogue.
        machine = self._ti83_machine()
        answer = firmware_for_system(
            machine,
            _context(machine),
            system="ti83",
            catalogue=Catalogue(entries=(), read=True),
        )
        assert [c.core_so for c in answer.cores] == ["numero_libretro.so"]
        codes = [c.code for c in answer.caveats]
        assert CAVEAT_SYSTEM_NOT_IN_CATALOGUE in codes
        assert CAVEAT_SYSTEM_UNKNOWN not in codes
        assert CAVEAT_EMULATOR_CATALOGUE_UNAVAILABLE not in codes

    def test_the_catalogue_less_route_marks_the_word_the_same_way(self):
        # Same rows, same answer-level mark — and not the no-catalogue caveat:
        # the reason this list is derived is the word, not the machine.
        machine = self._ti83_machine()
        answer = firmware_for_system(machine, _context(machine), system="ti83")
        assert [c.core_so for c in answer.cores] == ["numero_libretro.so"]
        codes = [c.code for c in answer.caveats]
        assert CAVEAT_SYSTEM_NOT_IN_CATALOGUE in codes
        assert CAVEAT_EMULATOR_CATALOGUE_UNAVAILABLE not in codes

    def test_a_word_outside_the_set_is_still_unknown_on_the_catalogue_route(self):
        # The discriminating direction: the retired slug "dc" is neither an id
        # nor a published own spelling, so a catalogued arrangement whose
        # catalogue declares no such system answers system-unknown.
        machine = self._ti83_machine()
        answer = firmware_for_system(
            machine,
            _context(machine),
            system="dc",
            catalogue=Catalogue(entries=(), read=True),
        )
        assert answer.cores == ()
        assert CAVEAT_SYSTEM_UNKNOWN in [c.code for c in answer.caveats]

    def test_an_own_spelling_with_nothing_filed_is_empty_not_unknown(self):
        # The word is published vocabulary, so nothing filing under it is a
        # machine fact: the answer keeps the own-spelling mark and states the
        # established absence (no-firmware-declaration) at answer level —
        # with zero cores there is no entry to say it per emulator, unlike
        # the id-with-declaration-less-emulators shape this mirrors.
        # system-unknown would blame the identifier for the machine; it
        # fires only for words in neither vocabulary.
        machine = _machine(
            {
                f"{INFO_DIR}/snes9x_libretro.info": NO_FIRMWARE_INFO,
                f"{INFO_DIR}/snes9x_libretro.so": {"status": "invalid-text"},
            }
        )
        for catalogue in (None, Catalogue(entries=(), read=True)):
            answer = firmware_for_system(
                machine, _context(machine), system="ti83", catalogue=catalogue
            )
            assert answer.cores == ()
            assert [c.code for c in answer.caveats] == [
                CAVEAT_SYSTEM_NOT_IN_CATALOGUE,
                CAVEAT_NO_FIRMWARE_DECLARATION,
            ]


class TestTheMapSpeaksTheCatalogueVocabulary:
    """The packaged map's target set is the packaged id set — guarded, not assumed.

    The map is world knowledge, so what holds it to its own account is tests:
    every value must be an id a question can take or a declared own spelling,
    and the day one stops being either, the suite says so instead of a query
    for a system that is right there coming back ``system-unknown``.
    """

    def test_every_systemname_maps_into_the_vocabulary_or_the_declared_markers(self):
        ids = set(known_systems())
        strays = {
            name: value
            for name, value in SYSTEMNAME_TO_SLUG.items()
            if value not in ids and value not in SYSTEMS_WITHOUT_CATALOGUE_ID
        }
        assert strays == {}

    def test_every_override_files_under_a_catalogue_id(self):
        # The override table names concrete dumps, and every one of them has a
        # catalogue system: a file worth a per-file rule is a file whose
        # machine is not in question.
        ids = set(known_systems())
        strays = {
            name: value for name, value in FIRMWARE_SYSTEM_OVERRIDE.items() if value not in ids
        }
        assert strays == {}

    def test_the_own_spellings_are_not_catalogue_ids(self):
        # The family exists because the catalogue lacks these words. One of
        # them turning up as an id is the family entry's retirement notice,
        # and this is the test that serves it.
        assert set(SYSTEMS_WITHOUT_CATALOGUE_ID) & set(known_systems()) == set()

    def test_the_ruled_values_hold(self):
        # The map-version-2 re-pointings, pinned one by one: the map is world
        # knowledge, and this is its reviewable shape — a value drifting under
        # a later edit fails here by name.
        ruled = {
            "Sega - Dreamcast": "dreamcast",
            "Sega Dreamcast": "dreamcast",
            "Sega - Game Gear": "gamegear",
            "Atari - Lynx": "atarilynx",
            "Lynx": "atarilynx",
            "CD-i": "cdimono1",
            "CDi": "cdimono1",
            "Palm OS": "palm",
            "Mac68k": "macintosh",
            "Sega - Master System - Mark III": "mastersystem",
            "Sega Master System": "mastersystem",
            "Sega 8-bit": "mastersystem",
            "Sega 8-bit (MS/GG/SG-1000)": "mastersystem",
            "NEC - PC Engine - TurboGrafx 16": "pcenginecd",
            "PC Engine/PCE-CD": "pcenginecd",
            "PC Engine SuperGrafx": "pcenginecd",
            "PC Engine/SuperGrafx": "pcenginecd",
            "PC Engine/SuperGrafx/CD": "pcenginecd",
            "C128": "c64",
            "128": "ep128",
            "BK-0010/BK-0011(M)": "bk",
            "TI83": "ti83",
            "Arcade (various)": "arcade",
            "Game engine": "scummvm",
            "Wolfenstein 3D Game Engine": "ports",
        }
        assert {name: SYSTEMNAME_TO_SLUG.get(name) for name in ruled} == ruled

    def test_the_rpg_maker_xp_entry_is_gone(self):
        # Deliberately unmapped: no shipped .info carries it, so it files
        # mechanically like any other systemname the map does not know.
        assert "RPG Maker XP/VX/VX Ace Game Engine" not in SYSTEMNAME_TO_SLUG

    def test_the_ruled_override_rows_hold(self):
        # The user-ruled per-file rows, pinned like the map values above.
        # Necessary even where vectors touch a row: a value here can flip to
        # another *real* id (bios_J.sms to mark3) with the membership guard
        # and most vectors still green, so belt and braces.
        ruled = {
            "bios_E.sms": "mastersystem",
            "bios_U.sms": "mastersystem",
            "bios_J.sms": "mastersystem",
            "bios.gg": "gamegear",
            "BIOS.col": "colecovision",
            "naomi.zip": "naomi",
            "naomi2.zip": "naomi2",
            "hod2bios.zip": "naomi",
            "f355dlx.zip": "naomi",
            "f355bios.zip": "naomi",
            "airlbios.zip": "naomi",
            "awbios.zip": "atomiswave",
            "gba_bios.bin": "gba",
            "nds_sd_card.bin": "nds",
        }
        assert {name: FIRMWARE_SYSTEM_OVERRIDE.get(name) for name in ruled} == ruled


# The build the map's evidence joins are cited to, where RetroDECK deploys it.
# The same constant lives in tests/test_systems.py for the id-set guard —
# duplicated deliberately, like the packaged-data loaders: each guard reads
# its one file and shares no machinery, so a defect in one can never mute the
# other.
DEPLOYED_ES_SYSTEMS = Path(
    "/var/lib/flatpak/app/net.retrodeck.retrodeck/current/active/files/retrodeck/components"
    "/es-de/share/es-de/resources/systems/linux/es_systems.xml"
)

# The evidence joins behind the map's version-2 values: which deployed
# catalogue system witnesses each id (by fullname), and which of the cores
# carrying the mapped systemnames its <command> lines launch. An empty core
# tuple is a derived join — the id's meaning matched and no launching-core
# witness exists (macintosh runs standalone MAME; colecovision's witness is
# BIOS.col's own description). Re-run live below, so drift in the deployed
# build fails the suite loudly instead of silently outdating the citations
# in atlas/firmware.py.
RECORDED_JOINS = (
    ("dreamcast", "Sega Dreamcast", ("flycast_libretro",)),
    ("mastersystem", "Sega Master System", ("gearsystem_libretro", "smsplus_libretro")),
    ("gamegear", "Sega Game Gear", ("genesis_plus_gx_libretro",)),
    ("atarilynx", "Atari Lynx", ("handy_libretro", "holani_libretro", "mednafen_lynx_libretro")),
    ("pcenginecd", "NEC PC Engine CD", ("mednafen_pce_libretro", "mednafen_pce_fast_libretro")),
    ("cdimono1", "Philips CD-i", ("same_cdi_libretro", "cdi2015_libretro")),
    ("palm", "Palm OS", ("mu_libretro",)),
    ("macintosh", "Apple Macintosh", ()),
    ("c64", "Commodore 64", ("vice_x128_libretro",)),
    ("arcade", "Arcade", ("fbneo_libretro", "mame_libretro")),
    ("scummvm", "ScummVM Game Engine", ("scummvm_libretro",)),
    ("ports", "Ports", ("ecwolf_libretro",)),
    ("naomi", "Sega NAOMI", ("flycast_libretro",)),
    ("naomi2", "Sega NAOMI 2", ("flycast_libretro",)),
    ("atomiswave", "Sammy Corporation Atomiswave", ("flycast_libretro",)),
    ("colecovision", "Coleco ColecoVision", ()),
)


class TestTheRecordedJoinsStillHoldOnTheDeployedBuild:
    """The map's citations, re-run live — the 16d pattern for world knowledge.

    A citation proves what a file said the day it was read; the deployed file
    moves with RetroDECK updates. Re-running the joins — fullname equality and
    catalogue-launches-core — turns that drift into a loud failure naming the
    entry to re-establish, instead of a table quietly citing a build that no
    longer says what it did. Skipped where the deployment is absent: the
    Flatpak is not a build dependency, and the packaged map ships either way.
    """

    def _systems(self) -> dict[str, tuple[str, set[str]]]:
        if not DEPLOYED_ES_SYSTEMS.exists():
            pytest.skip(f"RetroDECK's ES-DE is not deployed at {DEPLOYED_ES_SYSTEMS}")
        # ElementTree drops comments, and that is the point of parsing rather
        # than grepping: a commented-out <system> or <command> is not a
        # declaration and must not witness anything.
        root = ET.fromstring(DEPLOYED_ES_SYSTEMS.read_text(encoding="utf-8"))
        systems: dict[str, tuple[str, set[str]]] = {}
        for system in root.findall("system"):
            name = (system.findtext("name") or "").strip()
            fullname = (system.findtext("fullname") or "").strip()
            cores = {
                match.group(1)
                for command in system.findall("command")
                for match in re.finditer(r"([A-Za-z0-9_.-]+_libretro)\.so", command.text or "")
            }
            systems[name] = (fullname, cores)
        return systems

    def test_every_recorded_join_still_holds(self):
        systems = self._systems()
        failures: list[str] = []
        for name, fullname, cores in RECORDED_JOINS:
            if name not in systems:
                failures.append(f"{name}: no longer declared at all")
                continue
            declared_fullname, declared_cores = systems[name]
            if declared_fullname != fullname:
                failures.append(f"{name}: fullname {declared_fullname!r}, recorded {fullname!r}")
            missing = set(cores) - declared_cores
            if missing:
                failures.append(f"{name}: commands no longer launch {sorted(missing)}")
        assert failures == []

    def test_the_own_spellings_are_still_absent_from_the_build(self):
        # The other half of the family's evidence: bk, ti83 and ep128 are own
        # spellings because the build declares no such system — and neither a
        # c128 nor a cdimono2, which is what files the C128 under c64 and
        # same_cdi's cdimono2.zip under cdimono1.
        declared = set(self._systems())
        assert declared & (set(SYSTEMS_WITHOUT_CATALOGUE_ID) | {"c128", "cdimono2"}) == set()


def _region_option(file_name: str, regions: tuple[str, ...], *, found: PathKind = "missing") -> FirmwareRequirement:
    return FirmwareRequirement(
        core_so=None, system="psx", system_source="card", need="required",
        file_name=file_name, path=f"/bios/{file_name}", declared=file_name, description="",
        identity=None, found=found, checked=CHECKED_UNKNOWN if found == "file" else None,
        regions=regions,
    )


class TestAlternativesAreOneOfSeveralNotSeveralNeeds:
    """The group states what one launch needs: one option, the console region decides."""

    def test_regions_must_name_at_least_one_region(self):
        with pytest.raises(ValueError):
            _region_option("scph5501.bin", ())

    def test_an_empty_group_states_nothing(self):
        with pytest.raises(ValueError):
            FirmwareAlternatives(options=())

    def test_every_option_must_state_its_regions(self):
        unscoped = FirmwareRequirement(
            core_so=None, system="psx", system_source="card", need="required",
            file_name="scph5501.bin", path="/bios/scph5501.bin", declared="scph5501.bin",
            description="", identity=None, found="missing", checked=None,
        )
        with pytest.raises(ValueError):
            FirmwareAlternatives(options=(unscoped,))

    def test_two_options_one_region_could_pick_between_are_refused(self):
        # Overlapping scopes would leave the pick unstated — the exact defect
        # the shape exists to remove.
        first = _region_option("scph5501.bin", ("ntsc-u",))
        overlapping = _region_option("scph5500.bin", ("ntsc-u", "ntsc-j"))
        with pytest.raises(ValueError):
            FirmwareAlternatives(options=(first, overlapping))

    def test_a_region_repeated_within_one_option_is_refused(self):
        # Disjointness across options never sees a duplicate inside one, so
        # the option's own tuple is checked too.
        doubled = _region_option("scph5501.bin", ("ntsc-u", "ntsc-u"))
        with pytest.raises(ValueError, match="repeats a region within its own tuple"):
            FirmwareAlternatives(options=(doubled,))

    def test_a_group_met_for_every_region_is_satisfied(self):
        group = FirmwareAlternatives(
            options=(
                _region_option("scph5501.bin", ("ntsc-u",), found="file"),
                _region_option("scph5502.bin", ("ntsc-j", "pal"), found="file"),
            )
        )
        assert group.satisfied is True

    def test_a_group_failed_for_every_region_is_unmet(self):
        group = FirmwareAlternatives(
            options=(
                _region_option("scph5501.bin", ("ntsc-u",)),
                _region_option("scph5502.bin", ("ntsc-j", "pal")),
            )
        )
        assert group.satisfied is False

    def test_a_mixed_group_has_no_single_verdict(self):
        # Whether THIS launch is served depends on the disc's region, which
        # atlas cannot read — None, never a coin flip.
        group = FirmwareAlternatives(
            options=(
                _region_option("scph5501.bin", ("ntsc-u",)),
                _region_option("scph5502.bin", ("ntsc-j", "pal"), found="file"),
            )
        )
        assert group.satisfied is None

    def test_a_region_scoped_requirement_outside_a_group_is_refused(self):
        scoped = _region_option("scph5501.bin", ("ntsc-u",))
        provenance = Caveat("firmware-packaged-declaration", "", {})
        with pytest.raises(ValueError):
            CoreFirmware(
                core_so=None, label="DuckStation", declaration="packaged",
                requirements=(scoped,), caveats=(provenance,),
            )


class TestAPartialReaderOfAGroupIsNotMisled:
    """unmet/undetermined/requirements_met fold groups in through the lift."""

    def _core(self, *options: FirmwareRequirement) -> CoreFirmware:
        # declaration "read" on purpose: only there does requirements_met ever
        # leave None, so only there can the lift be proven to gate it.
        return CoreFirmware(
            core_so="x_libretro.so", label=None, declaration=DECLARATION_READ,
            requirements=(FirmwareAlternatives(options=options),), caveats=(),
        )

    def test_a_group_failed_everywhere_blocks_and_names_its_options(self):
        core = self._core(
            _region_option("scph5501.bin", ("ntsc-u",)),
            _region_option("scph5502.bin", ("ntsc-j", "pal")),
        )
        assert [r.file_name for r in core.unmet] == ["scph5501.bin", "scph5502.bin"]
        assert core.requirements_met is False

    def test_a_mixed_group_is_undetermined_not_failed(self):
        core = self._core(
            _region_option("scph5501.bin", ("ntsc-u",)),
            _region_option("scph5502.bin", ("ntsc-j", "pal"), found="file"),
        )
        assert core.unmet == ()
        # The option not established usable is listed; the satisfied one is not.
        assert [r.file_name for r in core.undetermined] == ["scph5501.bin"]
        assert core.requirements_met is None

    def test_a_group_met_everywhere_lets_true_through(self):
        core = self._core(
            _region_option("scph5501.bin", ("ntsc-u",), found="file"),
            _region_option("scph5502.bin", ("ntsc-j", "pal"), found="file"),
        )
        assert core.unmet == ()
        assert core.undetermined == ()
        assert core.requirements_met is True

    def test_the_flat_view_keeps_every_options_scope(self):
        core = self._core(
            _region_option("scph5502.bin", ("ntsc-j", "pal"), found="file"),
            _region_option("scph5501.bin", ("ntsc-u",)),
        )
        answer = FirmwareAnswer(
            root="/bios", cores=(core,), unclaimed=(), hash_checked=False, sources=(), caveats=(),
        )
        flattened = answer.requirements
        assert [r.file_name for r in flattened] == ["scph5501.bin", "scph5502.bin"]
        assert [r.regions for r in flattened] == [("ntsc-u",), ("ntsc-j", "pal")]


def _card_requirement(file_name: str, **overrides: object) -> FirmwareRequirement:
    """One requirement of a packaged card — missing at its destination by default."""
    fields: dict[str, object] = {
        "core_so": None, "system": "psx", "system_source": "card", "need": NEED_REQUIRED,
        "file_name": file_name, "path": f"/bios/{file_name}", "declared": file_name,
        "description": "", "identity": None, "found": "missing", "checked": None,
    }
    fields.update(overrides)
    return FirmwareRequirement(**fields)  # type: ignore[arg-type]


def _packaged_core(*requirements: FirmwareRequirement) -> CoreFirmware:
    provenance = Caveat(CAVEAT_FIRMWARE_PACKAGED_DECLARATION, "", {})
    return CoreFirmware(
        core_so=None, label="DuckStation (Standalone)", declaration=DECLARATION_PACKAGED,
        requirements=requirements, caveats=(provenance,),
    )


class TestEveryDeclarationIsJudgedOrExcusedByName:
    """A declaration is weighed or excused because it is named, never by default.

    ``requirements_met`` gated on ``!= read`` from the day it was written, when
    the vocabulary was read/unreadable/absent. ``packaged`` arrived later with
    real requirements behind it and fell into the unjudged side of that
    inequality, so every card answered ``None`` and nobody had decided it. The
    two halves are tuples now and the property branches on one of them, so a
    value added tomorrow is judged or excused by name.
    """

    def test_the_two_halves_cover_the_vocabulary_exactly(self):
        assert set(DECLARATIONS_JUDGED) | set(DECLARATIONS_UNJUDGED) == set(CORE_DECLARATION_STATES)

    def test_a_declaration_is_never_in_both_halves(self):
        assert set(DECLARATIONS_JUDGED) & set(DECLARATIONS_UNJUDGED) == set()

    def test_the_property_reads_the_tuple_this_test_reads(self):
        # The halves agreeing with the vocabulary says nothing about the code
        # if the property no longer asks them, which is the state the defect
        # was in: a name and an inequality, both defensible, disagreeing.
        source = ast.parse(Path(atlas.firmware.__file__).read_text(encoding="utf-8"))
        bodies = [
            node
            for node in ast.walk(source)
            if isinstance(node, ast.FunctionDef) and node.name == "requirements_met"
        ]
        assert len(bodies) == 1, "requirements_met is defined once"
        names = {node.id for node in ast.walk(bodies[0]) if isinstance(node, ast.Name)}
        assert "DECLARATIONS_UNJUDGED" in names

    def test_every_unjudged_declaration_answers_none(self):
        # Behaviour beside the name: each of the three really does stop the
        # verdict. None of them may carry requirements, so the empty list is
        # the whole of what such a core states.
        for declaration in DECLARATIONS_UNJUDGED:
            core = CoreFirmware(
                core_so="x_libretro.so", label=None, declaration=declaration,
                requirements=(), caveats=(Caveat(CAVEAT_NO_FIRMWARE_DECLARATION, "", {}),),
            )
            assert core.requirements_met is None, declaration


class TestAPackagedDeclarationIsJudged:
    """A card is a declaration, so the field that judges declarations judges it.

    What atlas established about a packaged card is the same kind of thing it
    establishes about a ``.info``: which files an emulator opens, and what is
    at those destinations. The one place the two part company is the empty
    list — a ``.info`` declaring nothing needs nothing, a card with nothing
    established states nothing.
    """

    def test_a_required_file_in_place_is_true(self):
        core = _packaged_core(_card_requirement("scph5501.bin", found="file", checked=CHECKED_UNKNOWN))
        assert core.requirements_met is True

    def test_a_required_file_missing_is_false(self):
        core = _packaged_core(_card_requirement("scph5501.bin"))
        assert [r.file_name for r in core.unmet] == ["scph5501.bin"]
        assert core.requirements_met is False

    def test_one_of_three_missing_is_false(self):
        # melonDS's shape: a probe set where two paths are configured and the
        # third is not. The list is a conjunction, so one absence decides it.
        core = _packaged_core(
            _card_requirement("bios9.bin", found="file", checked=CHECKED_UNKNOWN),
            _card_requirement("bios7.bin", found="file", checked=CHECKED_UNKNOWN),
            _card_requirement("firmware.bin"),
        )
        assert core.requirements_met is False

    def test_an_optional_file_missing_is_still_true(self):
        # Cemu's keys.txt: the card marks it optional because decrypted content
        # runs without it, and this field has always weighed the required ones.
        core = _packaged_core(_card_requirement("keys.txt", need="optional"))
        assert core.requirements_met is True
        assert core.unmet == ()

    def test_a_file_nobody_judged_leaves_it_unsaid(self):
        core = _packaged_core(
            _card_requirement("scph5501.bin", found="file", checked=CHECKED_UNRECOGNISED)
        )
        assert core.requirements_met is None

    def test_an_empty_card_states_nothing_rather_than_nothing_needed(self):
        # The one asymmetry with a read declaration, and the reason it exists:
        # an empty card is a probe whose switch is off or a search whose bytes
        # were never read, never an emulator that needs no file.
        assert _packaged_core().requirements_met is None


class TestAnIdentifiedImageIsVerified:
    """DuckStation hashes the file it is about to load, so the requirement carries that.

    The emulator names no BIOS of its own: it keeps every file of an accepted
    size and recognises what is left against a table compiled into it. atlas
    carries that table and the caveat beside the requirement already said
    which image the picked bytes are — while the requirement itself said
    ``unknown`` with no identity, which reads as "no table covers this file".
    The answer now carries the reading in the field the verdict is derived
    from.

    Both routes into that table are held here, because upstream reads them
    through one function: the search calls ``LoadImageFromFile`` per candidate
    it kept and a named region key hands it the composed path (bios.cpp:385
    and :350), and the size gate, the hash and the lookup all sit inside it.
    """

    BIOS = "/mnt/sd/retrodeck/bios"
    # Rows of DuckStation's own table, so a test that stops agreeing with the
    # shipped data fails rather than passing against an invention.
    SCPH5501 = "490f666e1afb15b7362b406ed1cea246"
    SCPH5502 = "32736f17079d0b2b7024407c39bd3050"
    # A Saturn dump: an accepted size, and no row of the table holds its bytes.
    SATURN = "85ec9ca47d8f6807718151cbcca8b964"
    PS1_SIZE = 524288

    def _machine(
        self, bios: Mapping[str, FixtureFileSpec], *, named: str = "", **kwargs: object
    ) -> FixtureMachine:
        """A RetroDECK arrangement whose psx row is the DuckStation standalone entry.

        *named* is the value of ``PathNTSCU``; empty leaves every region key
        empty, which is the state both arrangements ship and the one the
        search speaks for.
        """
        config = "/home/deck/.var/app/net.retrodeck.retrodeck/config"
        deployed = (
            "/var/lib/flatpak/app/net.retrodeck.retrodeck/current/active/files/retrodeck"
            "/components/es-de/share/es-de/resources/systems/linux/es_systems.xml"
        )
        tree: dict[str, FixtureFileSpec] = {
            f"{config}/retrodeck/retrodeck.json": json.dumps(
                {"paths": {"rd_home_path": "/mnt/sd/retrodeck", "saves_path": "/mnt/sd/retrodeck/saves",
                           "bios_path": self.BIOS}}
            ),
            f"{config}/retroarch/retroarch.cfg": (
                f'savefile_directory = "/mnt/sd/retrodeck/saves"\nlibretro_directory = "/app/cores"\n'
                f'system_directory = "{self.BIOS}"\n'
            ),
            deployed: (
                '<?xml version="1.0"?>\n<systemList>\n  <system>\n    <name>psx</name>\n'
                "    <fullname>Sony PlayStation</fullname>\n    <path>%ROMPATH%/psx</path>\n"
                "    <extension>.chd</extension>\n"
                '    <command label="DuckStation (Legacy) (Standalone)">'
                "%EMULATOR_DUCKSTATION% -batch %ROM%</command>\n"
                "    <platform>psx</platform>\n  </system>\n</systemList>\n"
            ),
            f"{config}/duckstation/settings.ini": (
                f"[BIOS]\nSearchDirectory = {self.BIOS}\nPathNTSCU = {named}\n"
                "PathNTSCJ = \nPathPAL = \n"
            ),
            **bios,
        }
        return FixtureMachine(
            tree, dirs=["/mnt/sd/retrodeck/saves", self.BIOS], **kwargs  # type: ignore[arg-type]
        )

    def _image(self, md5: str) -> dict[str, str | int]:
        return {"md5": md5, "size": self.PS1_SIZE}

    def _core(self, machine: FixtureMachine, *, verify: bool = True) -> CoreFirmware:
        installs = atlas.detect("/home/deck", machine)
        answer = installs[0].firmware_for_system(system="psx", verify=verify)
        return next(core for core in answer.cores if core.declaration == DECLARATION_PACKAGED)

    def _codes(self, core: CoreFirmware) -> list[str]:
        return [caveat.code for caveat in core.caveats]

    def test_the_picked_image_carries_the_row_the_table_named(self):
        core = self._core(self._machine({f"{self.BIOS}/scph5501.bin": self._image(self.SCPH5501)}))
        requirement = core.requirements[0]
        assert isinstance(requirement, FirmwareRequirement)
        assert requirement.checked == CHECKED_VERIFIED
        assert requirement.identity is not None
        assert requirement.identity.md5 == self.SCPH5501
        assert requirement.identity.size == self.PS1_SIZE
        # The table pins no sha1 and names no file, so both stay empty rather
        # than being filled from somewhere else.
        assert requirement.identity.sha1 is None
        assert requirement.identity.known_as == ()
        assert requirement.identity.table_version
        assert CAVEAT_FIRMWARE_IMAGE_IDENTIFIED in self._codes(core)
        assert core.requirements_met is True

    def test_a_tie_between_identified_images_is_still_a_verdict(self):
        # Which of them boots is the directory's order and atlas cannot read
        # it; that both are images this emulator knows is established, so the
        # ambiguity is about WHICH file, never about whether one is there.
        core = self._core(
            self._machine(
                {
                    f"{self.BIOS}/scph5501.bin": self._image(self.SCPH5501),
                    f"{self.BIOS}/scph5502.bin": self._image(self.SCPH5502),
                }
            )
        )
        requirement = core.requirements[0]
        assert isinstance(requirement, FirmwareRequirement)
        assert requirement.checked == CHECKED_VERIFIED
        assert CAVEAT_FIRMWARE_IMAGE_AMBIGUOUS in self._codes(core)
        assert core.requirements_met is True

    def test_bytes_no_row_holds_are_unrecognised_rather_than_unknown(self):
        core = self._core(self._machine({f"{self.BIOS}/mpr-17933.bin": self._image(self.SATURN)}))
        requirement = core.requirements[0]
        assert isinstance(requirement, FirmwareRequirement)
        assert requirement.checked == CHECKED_UNRECOGNISED
        assert requirement.identity is None
        # The emulator boots such an image with a warning, so this is neither
        # a failure nor an all-clear — and the verdict says exactly that.
        assert requirement.satisfied is None
        assert CAVEAT_FIRMWARE_CONTENT_UNIDENTIFIED in self._codes(core)
        assert core.requirements_met is None

    def test_an_unreadable_pick_reads_the_same_on_both_routes(self):
        # One file, two ways to reach it: the directory search and a region
        # key that names it. Neither read its bytes, so neither may say what
        # it is, and the two answers are the same answer.
        bios = {f"{self.BIOS}/scph5501.bin": {"status": "unreadable", "size": self.PS1_SIZE}}
        searched = self._core(self._machine(bios)).requirements[0]
        assert isinstance(searched, FirmwareRequirement)
        named = self._named_option(self._core(self._machine(bios, named="scph5501.bin")))
        assert searched.checked == named.checked == CHECKED_UNREAD
        assert searched.satisfied is named.satisfied is None
        assert searched.identity is named.identity is None

    def test_no_state_of_the_search_alone_reaches_a_false_verdict(self):
        """With every region key empty, the search never demonstrates an absence.

        Each state either names the file the ranking reached — with what its
        bytes turned out to be, up to and including that they would not come
        back — or states no requirement at all, and a packaged card with no
        requirement answers ``None``. So a red light over this emulator always
        comes from a named region key, and never from a directory atlas could
        not read to the end.
        """
        image = self._image(self.SCPH5501)
        states = {
            "an empty directory": self._machine({}),
            "a directory that cannot be listed": self._machine({}, unlistable=[self.BIOS]),
            "a file of an accepted size whose bytes will not come back": self._machine(
                {f"{self.BIOS}/scph5501.bin": {"status": "unreadable", "size": self.PS1_SIZE}}
            ),
            "bytes no row of the table holds": self._machine(
                {f"{self.BIOS}/mpr-17933.bin": self._image(self.SATURN)}
            ),
            "an image the table knows": self._machine({f"{self.BIOS}/scph5501.bin": image}),
        }
        verdicts = {name: self._core(machine).requirements_met for name, machine in states.items()}
        assert [name for name, met in verdicts.items() if met is False] == []
        assert verdicts["an image the table knows"] is True
        # The unreadable state names its file now rather than staying silent,
        # and the verdict it leaves is still undetermined rather than green.
        unreadable = self._core(states["a file of an accepted size whose bytes will not come back"])
        picked = unreadable.requirements[0]
        assert isinstance(picked, FirmwareRequirement)
        assert picked.checked == CHECKED_UNREAD
        assert verdicts["a file of an accepted size whose bytes will not come back"] is None
        # The same machine asked without a content check: nothing was hashed,
        # so nothing is claimed and nothing is denied either.
        assert self._core(states["an image the table knows"], verify=False).requirements_met is None

    # --- every file the search kept, not only the one the ranking reached ---

    def _listing(self, core: CoreFirmware) -> Caveat:
        """The one caveat that names every file the search kept."""
        codes = self._codes(core)
        assert codes.count(CAVEAT_FIRMWARE_SEARCH_CANDIDATES) == 1, codes
        return next(c for c in core.caveats if c.code == CAVEAT_FIRMWARE_SEARCH_CANDIDATES)

    def test_every_kept_file_is_listed_with_what_the_table_makes_of_it(self):
        # One dump under two names beside a file no row holds. Three keys in
        # the reading, and the two copies map to one image name — which is
        # what the emulator sees: one image the directory holds twice.
        core = self._core(
            self._machine(
                {
                    f"{self.BIOS}/scph5501.bin": self._image(self.SCPH5501),
                    f"{self.BIOS}/psx-ntscu.bin": self._image(self.SCPH5501),
                    f"{self.BIOS}/mpr-17933.bin": self._image(self.SATURN),
                }
            )
        )
        data = self._listing(core).data
        assert data["dir"] == self.BIOS
        assert data["token"] == "DUCKSTATION"
        assert data["readings"] == {
            f"{self.BIOS}/mpr-17933.bin": READING_UNRECOGNISED,
            f"{self.BIOS}/psx-ntscu.bin": READING_IDENTIFIED,
            f"{self.BIOS}/scph5501.bin": READING_IDENTIFIED,
        }
        # The name is the one the pick's own caveat states, so the listing and
        # the identification cannot drift into two spellings of one row — and
        # the file no row holds is absent rather than carrying a placeholder.
        named = next(c for c in core.caveats if c.code == CAVEAT_FIRMWARE_IMAGE_IDENTIFIED)
        assert data["images"] == {
            f"{self.BIOS}/psx-ntscu.bin": named.data["image"],
            f"{self.BIOS}/scph5501.bin": named.data["image"],
        }
        assert data["image_regions"] == {
            f"{self.BIOS}/psx-ntscu.bin": named.data["region"],
            f"{self.BIOS}/scph5501.bin": named.data["region"],
        }

    def test_a_candidate_whose_bytes_did_not_come_back_is_listed_as_unreadable(self):
        # A read failure is not the verdict "the table does not know these
        # bytes": the listing says so in its own word, and the file is absent
        # from the two mappings that say what a file is.
        core = self._core(
            self._machine(
                {
                    f"{self.BIOS}/scph5501.bin": self._image(self.SCPH5501),
                    f"{self.BIOS}/broken.bin": {"status": "unreadable", "size": self.PS1_SIZE},
                }
            )
        )
        data = self._listing(core).data
        assert data["readings"] == {
            f"{self.BIOS}/broken.bin": READING_UNREADABLE,
            f"{self.BIOS}/scph5501.bin": READING_IDENTIFIED,
        }
        assert f"{self.BIOS}/broken.bin" not in data["images"]
        assert f"{self.BIOS}/broken.bin" not in data["image_regions"]
        named = next(c for c in core.caveats if c.code == CAVEAT_FIRMWARE_IMAGE_IDENTIFIED)
        assert data["images"] == {f"{self.BIOS}/scph5501.bin": named.data["image"]}
        assert data["image_regions"] == {f"{self.BIOS}/scph5501.bin": named.data["region"]}

    def test_without_a_content_check_there_is_nothing_to_list(self):
        # The listing is the hashing written down, and no hashing happened —
        # the count of files of an accepted size rides
        # firmware-search-unverified, which is what that state does say.
        core = self._core(
            self._machine({f"{self.BIOS}/scph5501.bin": self._image(self.SCPH5501)}),
            verify=False,
        )
        assert CAVEAT_FIRMWARE_SEARCH_CANDIDATES not in self._codes(core)

    def test_a_directory_holding_no_file_of_an_accepted_size_lists_none(self):
        core = self._core(self._machine({}))
        assert CAVEAT_FIRMWARE_SEARCH_CANDIDATES not in self._codes(core)

    # --- the same table, reached by a region key that names a file ----------

    def _named_option(self, core: CoreFirmware) -> FirmwareRequirement:
        """The option of the alternatives group a named NTSC-U key contributes."""
        group = core.requirements[0]
        assert isinstance(group, FirmwareAlternatives)
        return next(option for option in group.options if option.regions == ("ntsc-u",))

    def test_a_named_image_the_table_knows_is_verified(self):
        core = self._core(
            self._machine(
                {f"{self.BIOS}/scph5501.bin": self._image(self.SCPH5501)}, named="scph5501.bin"
            )
        )
        option = self._named_option(core)
        assert option.checked == CHECKED_VERIFIED
        assert option.identity is not None
        assert option.identity.md5 == self.SCPH5501
        assert option.satisfied is True

    def test_a_named_image_no_row_holds_is_unrecognised(self):
        core = self._core(
            self._machine(
                {f"{self.BIOS}/mpr-17933.bin": self._image(self.SATURN)}, named="mpr-17933.bin"
            )
        )
        option = self._named_option(core)
        assert option.checked == CHECKED_UNRECOGNISED
        assert option.identity is None
        assert option.satisfied is None

    def test_the_named_and_the_searched_reading_of_one_file_agree(self):
        # The defect this closes: one file, two routes, two answers. Upstream
        # reads both through LoadImageFromFile, so the two options the group
        # carries for one path may not disagree about its bytes.
        core = self._core(
            self._machine(
                {f"{self.BIOS}/scph5501.bin": self._image(self.SCPH5501)}, named="scph5501.bin"
            )
        )
        group = core.requirements[0]
        assert isinstance(group, FirmwareAlternatives)
        at_path = [o for o in group.options if o.path == f"{self.BIOS}/scph5501.bin"]
        assert len(at_path) == 2, "the named key and the search both speak for this file"
        assert {o.checked for o in at_path} == {CHECKED_VERIFIED}
        assert len({(o.identity.md5, o.identity.size) for o in at_path if o.identity}) == 1

    def test_a_named_file_of_another_size_is_refused(self):
        # LoadImageFromFile refuses a file whose size is none of the three
        # before reading a byte (bios.cpp:183-190), so this launch boots
        # nothing from it — a demonstrated failure, and one a stat settles.
        core = self._core(
            self._machine({f"{self.BIOS}/scph5501.bin": "far too short to be a BIOS"},
                          named="scph5501.bin")
        )
        option = self._named_option(core)
        assert option.checked == CHECKED_REFUSED
        assert option.satisfied is False
        assert option.identity is None

    def test_the_refusal_says_which_size_was_read_and_which_are_accepted(self):
        # "Wrong size" a user cannot act on is no sentence at all: the data
        # carries the number read and the three the emulator loads.
        content = "far too short to be a BIOS"
        core = self._core(
            self._machine({f"{self.BIOS}/scph5501.bin": content}, named="scph5501.bin")
        )
        refusal = next(c for c in core.caveats if c.code == CAVEAT_FIRMWARE_IMAGE_REFUSED)
        assert refusal.data["path"] == f"{self.BIOS}/scph5501.bin"
        assert refusal.data["size"] == str(len(content))
        # The constructor freezes a sequence value, so the tuple is what a client reads.
        assert refusal.data["accepted"] == tuple(str(size) for size in duckstation.bios_table().sizes)
        assert refusal.data["token"] == "DUCKSTATION"

    def test_a_refused_file_is_answered_without_a_content_check(self):
        # The gate is a stat, so the verdict needs no verify — the one
        # checked value that fails a present file without one.
        machine = self._machine(
            {f"{self.BIOS}/scph5501.bin": "far too short to be a BIOS"}, named="scph5501.bin"
        )
        option = self._named_option(self._core(machine, verify=False))
        assert option.checked == CHECKED_REFUSED
        assert option.satisfied is False

    def test_a_named_image_whose_bytes_will_not_come_back_is_unread(self):
        # The read failed for atlas, which is no evidence the launch cannot
        # read it — so the answer withholds the verdict and states the failure
        # under the one code every other route uses for it. The identity stays
        # null because this table is keyed by content: without the bytes there
        # is no row to pin.
        core = self._core(
            self._machine(
                {f"{self.BIOS}/scph5501.bin": {"status": "unreadable", "size": self.PS1_SIZE}},
                named="scph5501.bin",
            )
        )
        option = self._named_option(core)
        assert option.checked == CHECKED_UNREAD
        assert option.satisfied is None
        assert option.identity is None
        assert CAVEAT_FIRMWARE_UNREADABLE in self._codes(core)
        assert core.requirements_met is None

    def test_the_search_never_refuses_what_it_picked(self):
        # The search keeps only files of an accepted size (bios.cpp:378), so
        # no candidate it picks can be one the loader turns down for its size.
        # Held over every state the search reaches, including a directory
        # whose only file is of a size it skips.
        states = [
            self._machine({f"{self.BIOS}/scph5501.bin": self._image(self.SCPH5501)}),
            self._machine({f"{self.BIOS}/mpr-17933.bin": self._image(self.SATURN)}),
            self._machine({f"{self.BIOS}/notes.txt": "not a BIOS at all"}),
            self._machine({}),
        ]
        for machine in states:
            for verify in (True, False):
                core = self._core(machine, verify=verify)
                options = [
                    option
                    for entry in core.requirements
                    for option in (
                        entry.options if isinstance(entry, FirmwareAlternatives) else (entry,)
                    )
                ]
                assert [o for o in options if o.checked == CHECKED_REFUSED] == []
                assert CAVEAT_FIRMWARE_IMAGE_REFUSED not in self._codes(core)


class TestTheInventoryCarriesTheCardsAndSaysWhatAFileConcerns:
    """The two defects of an inventory computed from the libretro cores alone.

    It ran no standalone card, so a BIOS image a card's own search collects was
    that card's satisfied requirement in ``firmware_for_system`` and a file
    nobody asks for in ``firmware_inventory`` at the same time. And it looked a
    file nobody declared up in the libretro table only, so a PlayStation dump
    filed by hand under a name no ``.info`` declares came back unidentified
    while the packaged DuckStation table knew those bytes exactly.

    Both halves are held here over one arrangement, because they are one
    reading: the carded emulators are entries, what their own reads looked at
    is theirs, and what is left is identified against every packaged table
    atlas carries.
    """

    # Rows of DuckStation's shipped table, so a test that stops agreeing with
    # the packaged data fails rather than passing against an invention.
    SCPH5501 = "490f666e1afb15b7362b406ed1cea246"
    # A row no libretro name holds: the table's development-kit image, which is
    # what makes "identified by the emulator's own table alone" testable.
    DEVKIT = "ca5cfc321f916756e3f0effbfaeba13b"
    DEVKIT_NAME = "DTL-H1100 (v2.2 03-06-96 D)"
    PS1_SIZE = 524288
    SEARCH = f"{BIOS_DIR}/duckstation"
    DATA_HOME = "/home/deck/.local/share"
    CONFIG_HOME = "/home/deck/.config"
    KEYS = f"{DATA_HOME}/Cemu/keys.txt"
    # A libretro table naming the PlayStation image under two names and the GBA
    # one under a single name whose size no emulator table accepts. The first
    # md5 is DuckStation's own, which is the point of it: one content, two
    # tables.
    TABLE = json.dumps(
        {
            "_meta": {"generated_from": "test", "version": "0", "generated_at": "2026-01-01"},
            "files": {
                "scph5501.bin": {"md5": SCPH5501, "sha1": "bb" * 20, "size": PS1_SIZE, "kind": "file"},
                "scph7003.bin": {"md5": SCPH5501, "sha1": "bb" * 20, "size": PS1_SIZE, "kind": "file"},
                "gba_bios.bin": {"md5": "11" * 16, "sha1": "22" * 20, "size": 6, "kind": "file"},
                # Two names no per-file rule covers: the Neo Geo system ROM,
                # which an arcade board and a console both boot, and one of
                # SkyEmu's DS BIOS names.
                "neogeo.zip": {
                    "md5": "55" * 16, "sha1": "66" * 20, "size": 9,
                    "kind": "archive", "archive_reason": "romset",
                },
                "nds7.bin": {"md5": "a3" * 16, "sha1": "b4" * 20, "size": 10, "kind": "file"},
                # Keyed by a RELATIVE PATH, which 91 of the packaged table's
                # own entries are: the name a declaration carries is not the
                # key, and the reading that relates the two is for_path.
                "dc/dc_boot.bin": {"md5": "33" * 16, "sha1": "44" * 20, "size": 7, "kind": "file"},
            },
        }
    )
    PS1_DUMP: dict[str, str | int] = {"md5": SCPH5501, "sha1": "bb" * 20, "size": PS1_SIZE}
    DC_DUMP: dict[str, str | int] = {"md5": "33" * 16, "sha1": "44" * 20, "size": 7}
    DEVKIT_DUMP: dict[str, str | int] = {"md5": DEVKIT, "sha1": "cc" * 20, "size": PS1_SIZE}
    GBA_DUMP: dict[str, str | int] = {"md5": "11" * 16, "sha1": "22" * 20, "size": 6}
    NEOGEO_DUMP: dict[str, str | int] = {"md5": "55" * 16, "sha1": "66" * 20, "size": 9}
    NDS7_DUMP: dict[str, str | int] = {"md5": "a3" * 16, "sha1": "b4" * 20, "size": 10}
    # One core declaring one file the packaged table knows, under a systemname
    # that agrees with the per-file rule covering it.
    GBA_ONLY_INFO = """
systemname = "Nintendo - Game Boy Advance"
firmware_count = 1
firmware0_desc = "gba_bios.bin"
firmware0_path = "gba_bios.bin"
firmware0_opt = "true"
"""
    # Two installed cores declaring the same Neo Geo system ROM under two
    # systemnames. The deployed info set has three cores declaring it under
    # those same two — the FinalBurn Neo arcade build files it under arcade,
    # its Neo Geo build and Geolith under neogeo — and no per-file rule picks
    # between them, because that is exactly the machine being in question. The
    # arcade build declares it under a relative path the table does not hold,
    # so the pinning that relates the two falls back to the bare name.
    FBNEO_INFO = """
systemname = "Arcade (various)"
firmware_count = 1
firmware0_desc = "fbneo/neogeo.zip (Neo Geo BIOS)"
firmware0_path = "fbneo/neogeo.zip"
firmware0_opt = "false"
"""
    GEOLITH_INFO = """
systemname = "Neo Geo"
firmware_count = 1
firmware0_desc = "neogeo.zip (Neo Geo MVS System ROM)"
firmware0_path = "neogeo.zip"
firmware0_opt = "false"
"""
    DC_INFO = """
systemname = "Sega - Dreamcast"
firmware_count = 1
firmware0_desc = "dc/dc_boot.bin (Dreamcast BIOS)"
firmware0_path = "dc/dc_boot.bin"
firmware0_opt = "false"
"""
    # A core whose only declaration names a place outside the firmware root:
    # atlas refuses it, so the core declares something and requires nothing.
    REFUSED_INFO = """
systemname = "Sony - PlayStation"
firmware_count = 1
firmware0_desc = "outside"
firmware0_path = "../outside.bin"
firmware0_opt = "false"
"""
    PSX_CORE = "demo_psx_libretro.so"
    DUCKSTATION = CatalogueEntry(
        label="DuckStation (Legacy) (Standalone)",
        kind="standalone",
        core_so=None,
        emulator="DUCKSTATION",
        declared_index=0,
        standalone_token="DUCKSTATION",
    )
    CEMU = CatalogueEntry(
        label="Cemu (Standalone)",
        kind="standalone",
        core_so=None,
        emulator="CEMU",
        declared_index=0,
        standalone_token="CEMU",
    )
    CATALOGUE = InventoryCatalogue(
        by_system={"psx": Catalogue((DUCKSTATION,)), "wiiu": Catalogue((CEMU,))}
    )

    def _machine(
        self,
        files: Mapping[str, FixtureFileSpec],
        *,
        search: str = BIOS_DIR,
        infos: Mapping[str, str] | None = None,
        symlinks: Mapping[str, str] | None = None,
        dirs: tuple[str, ...] = (),
    ) -> FixtureMachine:
        """A machine whose psx row is DuckStation and whose wiiu row is Cemu.

        *search* is ``[BIOS] SearchDirectory``: the firmware root by default,
        which is the value RetroDECK ships — the emulator searches the very
        tree this answer is otherwise about — while a directory of its own is
        what tells a claim by the search apart from a claim by the table.
        *infos* replaces the installed cores, which decides what declares a
        name the packaged table knows.
        """
        declared = {self.PSX_CORE: PSX_INFO} if infos is None else dict(infos)
        tree: dict[str, FixtureFileSpec] = {
            f"{self.CONFIG_HOME}/duckstation/settings.ini": (
                f"[BIOS]\nSearchDirectory = {search}\nPathNTSCU = \nPathNTSCJ = \nPathPAL = \n"
            )
        }
        for core_so, info in declared.items():
            stem = core_so.removesuffix(".so")
            tree[f"{INFO_DIR}/{stem}.info"] = info
            tree[f"{INFO_DIR}/{stem}.so"] = {"status": "invalid-text"}
        tree.update(files)
        return FixtureMachine(
            tree,  # type: ignore[arg-type]
            symlinks=symlinks,
            dirs=[BIOS_DIR, INFO_DIR, self.SEARCH, *dirs],
        )

    def _context(self, machine: FixtureMachine) -> FirmwareContext:
        return FirmwareContext(
            root=BIOS_DIR,
            cores=read_core_declarations(machine, INFO_DIR, core_dir=INFO_DIR).cores,
            hashes=load_hashes(self.TABLE),
            standalone_data_home=self.DATA_HOME,
            standalone_config_home=self.CONFIG_HOME,
        )

    def _answer(
        self,
        files: Mapping[str, FixtureFileSpec],
        *,
        verify: bool = True,
        catalogue: InventoryCatalogue | None = None,
        search: str = BIOS_DIR,
        infos: Mapping[str, str] | None = None,
        symlinks: Mapping[str, str] | None = None,
    ) -> FirmwareAnswer:
        machine = self._machine(files, search=search, infos=infos, symlinks=symlinks)
        return firmware_inventory(
            machine,
            self._context(machine),
            catalogue=self.CATALOGUE if catalogue is None else catalogue,
            verify=verify,
        )

    def _found(self, answer: FirmwareAnswer, name: str) -> UnclaimedFile:
        return next(f for f in answer.unclaimed if f.path.endswith(name))

    def test_the_carded_emulators_are_entries_after_the_cores_in_catalogue_order(self):
        answer = self._answer({})
        assert [core.emulator for core in answer.cores] == [self.PSX_CORE, "DUCKSTATION", "CEMU"]
        assert [core.declaration for core in answer.cores[1:]] == [
            DECLARATION_PACKAGED,
            DECLARATION_PACKAGED,
        ]

    def test_with_no_catalogue_at_all_the_answer_is_the_installed_cores_alone(self):
        # A bare RetroArch, and the behaviour this answer had before cards
        # reached it: nothing enumerates a standalone emulator there.
        machine = self._machine({})
        answer = firmware_inventory(machine, self._context(machine), verify=True)
        assert [core.emulator for core in answer.cores] == [self.PSX_CORE]

    def test_an_uncarded_standalone_emulator_is_not_an_entry(self):
        # Its unsupported statement is firmware_for_system's to make, once per
        # system it launches; repeating it here would add rows that say nothing
        # about the tree this answer is about.
        rpcs3 = CatalogueEntry(label="RPCS3 (Standalone)", kind="standalone", core_so=None)
        answer = self._answer(
            {}, catalogue=InventoryCatalogue(by_system={"ps3": Catalogue((rpcs3,))})
        )
        assert [core.emulator for core in answer.cores] == [self.PSX_CORE]

    def test_a_file_the_search_kept_is_that_entrys_answer_and_not_unclaimed(self):
        # The first defect itself: DuckStation's search directory is the
        # firmware root on RetroDECK, so this dump was the card's satisfied
        # requirement and a file nobody asks for in one and the same reading.
        answer = self._answer({f"{BIOS_DIR}/playstation-us.bin": self.PS1_DUMP})
        assert [f.path for f in answer.unclaimed] == []
        duckstation = next(c for c in answer.cores if c.emulator == "DUCKSTATION")
        assert duckstation.requirements_met is True

    def test_a_file_of_a_size_the_search_skips_is_still_unclaimed(self):
        # The claim is the set the emulator's own recognition runs over, not
        # the directory: a file its size gate drops was never looked at.
        answer = self._answer({f"{BIOS_DIR}/handheld.rom": self.GBA_DUMP})
        assert [f.path for f in answer.unclaimed] == [f"{BIOS_DIR}/handheld.rom"]

    def test_a_card_destination_reached_through_a_link_is_claimed_by_its_target(self):
        # RetroDECK stages Cemu's keys file in the firmware tree and links it to
        # the path the emulator probes, so the claim has to be by resolved path.
        answer = self._answer(
            {f"{BIOS_DIR}/keys.txt": "# add keys below\n"},
            symlinks={self.KEYS: f"{BIOS_DIR}/keys.txt"},
        )
        assert [f.path for f in answer.unclaimed] == []
        cemu = next(c for c in answer.cores if c.emulator == "CEMU")
        assert cemu.requirements_met is True

    def test_an_unclaimed_file_the_emulators_own_table_knows_names_that_emulator(self):
        # Identified by the packaged DuckStation table alone: no libretro name
        # holds these bytes and the search is looking elsewhere, so nothing
        # about a declaration or a destination could have reached this answer.
        answer = self._answer({f"{BIOS_DIR}/devkit.bin": self.DEVKIT_DUMP}, search=self.SEARCH)
        found = self._found(answer, "devkit.bin")
        assert found.identity is not None
        assert found.identity.md5 == self.DEVKIT
        # The table pins no sha1 and names no file, so both stay empty.
        assert found.identity.sha1 is None
        assert found.known_as == ()
        assert found.description == self.DEVKIT_NAME
        assert found.console == "psx"
        assert found.concerns == (Concern(emulator="DUCKSTATION", relation=RELATION_RECOGNISES),)

    def test_bytes_both_tables_know_carry_the_libretro_identity_and_both_concerns(self):
        answer = self._answer(
            {f"{BIOS_DIR}/playstation-us.bin": self.PS1_DUMP}, search=self.SEARCH
        )
        found = self._found(answer, "playstation-us.bin")
        assert found.identity is not None
        # The libretro identity, because it pins a sha1 and the names the
        # content goes by and a content-keyed emulator table pins neither.
        assert found.identity.sha1 == "bb" * 20
        assert found.known_as == ("scph5501.bin", "scph7003.bin")
        assert found.description == "scph5501.bin, scph7003.bin"
        assert found.console == "psx"
        assert found.concerns == (
            Concern(emulator="DUCKSTATION", relation=RELATION_RECOGNISES),
            Concern(emulator=self.PSX_CORE, relation=RELATION_DECLARES),
        )

    def test_bytes_only_the_libretro_table_knows_state_the_declaring_core_alone(self):
        answer = self._answer(
            {f"{BIOS_DIR}/handheld.rom": self.GBA_DUMP},
            search=self.SEARCH,
            infos={"mgba_libretro.so": self.GBA_ONLY_INFO},
        )
        found = self._found(answer, "handheld.rom")
        assert found.description == "gba_bios.bin"
        assert found.console == "gba"
        assert found.concerns == (
            Concern(emulator="mgba_libretro.so", relation=RELATION_DECLARES),
        )

    def test_a_declaration_the_table_keys_by_path_still_states_its_concern(self):
        # The match is what the table pins for the declared path, not the
        # declaration's file name against the table's keys: Flycast declares
        # dc/dc_boot.bin, which is how the table holds it, while the file name
        # alone (dc_boot.bin) matches no key at all.
        answer = self._answer(
            {f"{BIOS_DIR}/boot.bin": self.DC_DUMP},
            search=self.SEARCH,
            infos={"flycast_libretro.so": self.DC_INFO},
        )
        found = self._found(answer, "/boot.bin")
        assert found.known_as == ("dc/dc_boot.bin",)
        assert found.console == "dreamcast"
        assert found.concerns == (
            Concern(emulator="flycast_libretro.so", relation=RELATION_DECLARES),
        )

    def test_two_systems_declaring_one_content_state_no_console(self):
        answer = self._answer(
            {f"{BIOS_DIR}/boardrom.bin": self.NEOGEO_DUMP},
            search=self.SEARCH,
            infos={"fbneo_libretro.so": self.FBNEO_INFO, "geolith_libretro.so": self.GEOLITH_INFO},
        )
        found = self._found(answer, "boardrom.bin")
        # A file two machines claim is not a file one of them owns, and both
        # cores still state their concern.
        assert found.console is None
        assert [concern.emulator for concern in found.concerns] == [
            "fbneo_libretro.so",
            "geolith_libretro.so",
        ]

    def test_a_core_that_states_no_system_of_its_own_states_no_console(self):
        # _unknown marks a core whose .info names no systemname at all, which
        # is a fact about that core rather than a machine these bytes belong to.
        # SkyEmu's DS BIOS carries no per-file rule, so nothing else assigns it
        # either — unlike the GBA BIOS it declares beside it.
        answer = self._answer(
            {f"{BIOS_DIR}/handheld.rom": self.NDS7_DUMP},
            search=self.SEARCH,
            infos={"skyemu_libretro.so": SKYEMU_INFO},
        )
        found = self._found(answer, "handheld.rom")
        assert found.console is None
        assert found.concerns == (
            Concern(emulator="skyemu_libretro.so", relation=RELATION_DECLARES),
        )

    def test_without_verification_the_three_fields_say_nothing(self):
        answer = self._answer(
            {f"{BIOS_DIR}/playstation-us.bin": self.PS1_DUMP}, search=self.SEARCH, verify=False
        )
        found = self._found(answer, "playstation-us.bin")
        assert found.identity is None
        assert found.description is None
        assert found.console is None
        assert found.concerns == ()

    def test_an_unclaimed_entry_satisfies_nothing(self):
        # The dump sits under a name nobody declared, so the core that wants
        # scph5501.bin is still missing its file — the concern is about the
        # bytes and moves no verdict.
        answer = self._answer(
            {f"{BIOS_DIR}/playstation-us.bin": self.PS1_DUMP}, search=self.SEARCH
        )
        core = next(c for c in answer.cores if c.emulator == self.PSX_CORE)
        assert core.requirements_met is False
        assert [r.present for r in _plain_requirements(core)] == [False, False]

    def test_a_carded_entry_is_a_declaration_so_no_absence_is_established(self):
        # The card names a search and found no image, so it states no
        # requirement — and "no installed core declares any firmware" would
        # still be the wrong sentence: a packaged declaration was read.
        answer = self._answer({}, search=self.SEARCH, infos={})
        assert [core.declaration for core in answer.cores] == [
            DECLARATION_PACKAGED,
            DECLARATION_PACKAGED,
        ]
        assert CAVEAT_NO_FIRMWARE_DECLARATION not in [c.code for c in answer.caveats]

    def test_with_no_carded_entry_the_established_absence_still_stands(self):
        answer = self._answer({}, infos={}, catalogue=InventoryCatalogue())
        assert answer.cores == ()
        assert CAVEAT_NO_FIRMWARE_DECLARATION in [c.code for c in answer.caveats]

    def test_a_catalogue_that_could_not_be_read_says_so_and_establishes_nothing(self):
        # The other empty: no card entry, and not because the frontend
        # declares none. Answering as a bare RetroArch would state an absence
        # over a catalogue nobody could consult.
        answer = self._answer({}, infos={}, catalogue=InventoryCatalogue(read=False))
        assert answer.cores == ()
        codes = [c.code for c in answer.caveats]
        assert codes == [CAVEAT_EMULATOR_CATALOGUE_UNREADABLE, CAVEAT_FIRMWARE_DECLARATION_UNKNOWN]

    def test_a_hole_that_reached_no_card_is_stated_and_establishes_nothing(self):
        # EmuDeck's sealed layer: the readable part declares no carded
        # emulator, and the declaration may sit in the part nobody could open.
        hole = Caveat(CAVEAT_EMULATOR_CATALOGUE_SEALED, "the bundled layer is sealed", {})
        answer = self._answer({}, infos={}, catalogue=InventoryCatalogue(hole=hole))
        assert answer.cores == ()
        codes = [c.code for c in answer.caveats]
        assert codes == [CAVEAT_EMULATOR_CATALOGUE_SEALED, CAVEAT_FIRMWARE_DECLARATION_UNKNOWN]

    def test_a_hole_whose_readable_part_named_a_card_still_enumerated(self):
        # The readable part is authoritative for what it declares, so a card
        # it named is an enumeration that happened — the hole is still stated.
        hole = Caveat(CAVEAT_EMULATOR_CATALOGUE_SEALED, "the bundled layer is sealed", {})
        answer = self._answer(
            {},
            infos={},
            search=self.SEARCH,
            catalogue=InventoryCatalogue(
                by_system={"psx": Catalogue((self.DUCKSTATION,))}, hole=hole
            ),
        )
        assert [core.emulator for core in answer.cores] == ["DUCKSTATION"]
        codes = [c.code for c in answer.caveats]
        assert codes[0] == CAVEAT_EMULATOR_CATALOGUE_SEALED
        assert CAVEAT_FIRMWARE_DECLARATION_UNKNOWN not in codes
        assert CAVEAT_NO_FIRMWARE_DECLARATION not in codes

    def test_a_read_that_did_not_happen_is_still_stated_beside_a_card(self):
        # Only ONE of the three empties is a sentence a card contradicts. A
        # core whose .info could not be read leaves what it declares unknown,
        # and a packaged declaration beside it does not answer that question —
        # so the statement stands, over an answer where nothing named a file.
        answer = self._answer(
            {
                f"{INFO_DIR}/broken_libretro.info": {"status": "invalid-text"},
                f"{INFO_DIR}/broken_libretro.so": {"status": "invalid-text"},
            },
            search=self.SEARCH,
            infos={},
            catalogue=InventoryCatalogue(by_system={"psx": Catalogue((self.DUCKSTATION,))}),
        )
        assert [core.declaration for core in answer.cores] == [
            DECLARATION_UNREADABLE,
            DECLARATION_PACKAGED,
        ]
        assert [core.requirements for core in answer.cores] == [(), ()]
        assert CAVEAT_FIRMWARE_DECLARATION_UNKNOWN in [c.code for c in answer.caveats]

    def test_one_requirement_anywhere_silences_every_empty_answer_code(self):
        # The refused declaration is the only one this core makes, so nothing
        # it declares became a requirement — while both carded entries produced
        # one: DuckStation found its dump, and Cemu names a keys file that is
        # missing. "Nothing that declares any firmware produced a requirement"
        # is then false, and no code in that family may be stated.
        answer = self._answer(
            {f"{BIOS_DIR}/playstation-us.bin": self.PS1_DUMP},
            infos={"refuser_libretro.so": self.REFUSED_INFO},
        )
        assert [len(core.requirements) for core in answer.cores] == [0, 1, 1]
        assert answer.cores[0].refused
        assert [c.code for c in answer.caveats] == []

    def test_the_caveats_walk_the_entries_in_the_order_the_entries_are_in(self):
        # Three groups, and a client reading cores and caveats side by side
        # walks them together: the catalogue's own health, then what the
        # installed cores observed, then what the carded entries observed —
        # the order ``cores`` itself puts them in. Held over a fixture where
        # each half observes something of its own: Flycast's declared BIOS
        # cannot be read, and a directory sits where Cemu opens its keys file.
        hole = Caveat(CAVEAT_EMULATOR_CATALOGUE_SEALED, "the bundled layer is sealed", {})
        machine = self._machine(
            {f"{BIOS_DIR}/dc/dc_boot.bin": {"status": "unreadable", "size": 7}},
            infos={"flycast_libretro.so": self.DC_INFO},
            dirs=(self.KEYS,),
        )
        answer = firmware_inventory(
            machine,
            self._context(machine),
            catalogue=InventoryCatalogue(
                by_system={"wiiu": Catalogue((self.CEMU,))}, hole=hole
            ),
            verify=True,
        )
        assert [core.emulator for core in answer.cores] == ["flycast_libretro.so", "CEMU"]
        assert [(c.code, c.data.get("path", "")) for c in answer.caveats] == [
            (CAVEAT_EMULATOR_CATALOGUE_SEALED, ""),
            (CAVEAT_FIRMWARE_UNREADABLE, f"{BIOS_DIR}/dc/dc_boot.bin"),
            (CAVEAT_FIRMWARE_PATH_OBSTRUCTED, self.KEYS),
        ]

    def test_a_concern_outside_the_vocabulary_is_refused(self):
        with pytest.raises(ValueError, match="relation must be one of"):
            Concern(emulator="DUCKSTATION", relation="knows")  # type: ignore[arg-type]

class TestAnAnswerStatesAnObservationOnce:
    """Two caveats with one code and one data are one statement, and the first stays.

    A catalogue naming one core under two entries observes each destination
    once per entry with identical data. What the answer keeps is decided by
    ``code`` and ``data`` alone: a restatement in other words is dropped, the
    same code over other data is another fact, another code over the same
    data is one too, and a mapping-valued entry is keyed without being
    hashed.
    """

    LOCKED = f"{BIOS_DIR}/locked.bin"
    OTHER = f"{BIOS_DIR}/other.bin"

    def test_a_restatement_in_other_words_is_dropped_and_the_first_kept(self):
        first = Caveat(CAVEAT_FIRMWARE_UNREADABLE, "the sentence of the reader that saw it", {"path": self.LOCKED})
        again = Caveat(CAVEAT_FIRMWARE_UNREADABLE, "the same fact, worded again", {"path": self.LOCKED})
        assert stated_once((first, again)) == (first,)

    def test_the_same_code_over_other_data_is_another_fact(self):
        locked = Caveat(CAVEAT_FIRMWARE_UNREADABLE, "one file", {"path": self.LOCKED})
        other = Caveat(CAVEAT_FIRMWARE_UNREADABLE, "another file", {"path": self.OTHER})
        assert stated_once((locked, other, locked)) == (locked, other)

    def test_another_code_over_the_same_data_is_another_fact(self):
        unread = Caveat(CAVEAT_FIRMWARE_UNREADABLE, "its bytes failed", {"path": self.LOCKED})
        obstructed = Caveat(CAVEAT_FIRMWARE_PATH_OBSTRUCTED, "a directory is there", {"path": self.LOCKED})
        assert stated_once((unread, obstructed, unread)) == (unread, obstructed)

    def test_a_mapping_valued_entry_is_keyed_by_its_items(self):
        tally = Caveat(
            CAVEAT_PER_GAME_ALTERNATIVE_EMULATOR,
            "the tally",
            {"count": "3", "emulators": {"DuckStation": "2", "SwanStation": "1"}},
        )
        reordered = Caveat(
            CAVEAT_PER_GAME_ALTERNATIVE_EMULATOR,
            "the tally, keys in another order",
            {"emulators": {"SwanStation": "1", "DuckStation": "2"}, "count": "3"},
        )
        other = Caveat(
            CAVEAT_PER_GAME_ALTERNATIVE_EMULATOR,
            "another tally",
            {"count": "1", "emulators": {"DuckStation": "1"}},
        )
        assert stated_once((tally, reordered, other)) == (tally, other)

    def test_a_sequence_valued_entry_is_keyed_by_its_own_order(self):
        # A list-valued key is keyed without being hashed, like the mapping
        # above — but ORDER decides here, because the order is the emitter's
        # stated one and part of the contract. Two answers naming the same
        # files in different orders are two statements, not a restatement.
        one = Caveat(
            CAVEAT_FIRMWARE_DECLARATION_UNREAD,
            "the declarations RetroArch does not take",
            {"core_so": LRPS2_SO, "declared": ("firmware0_path", "firmware1_path")},
        )
        same = Caveat(
            CAVEAT_FIRMWARE_DECLARATION_UNREAD,
            "the same fact, worded again",
            {"core_so": LRPS2_SO, "declared": ("firmware0_path", "firmware1_path")},
        )
        reordered = Caveat(
            CAVEAT_FIRMWARE_DECLARATION_UNREAD,
            "the same names, the other way round",
            {"core_so": LRPS2_SO, "declared": ("firmware1_path", "firmware0_path")},
        )
        assert stated_once((one, same)) == (one,)
        assert stated_once((one, reordered)) == (one, reordered)

    def test_a_sequence_is_keyed_apart_from_the_string_it_used_to_be(self):
        # The contract change itself: ("a",) and "a" are different statements,
        # so a port that still joins its lists is not silently deduplicated
        # against one that does not.
        listed = Caveat(CAVEAT_FIRMWARE_DECLARATION_UNREAD, "as a list", {"declared": ("a",)})
        joined = Caveat(CAVEAT_FIRMWARE_DECLARATION_UNREAD, "as a string", {"declared": "a"})
        assert stated_once((listed, joined)) == (listed, joined)


class TestARowsForeignCoreFileIsHeldToItsKind:
    """`CatalogueEntry` refuses the two pairings that would make the caveat lie (#446).

    The firmware seam carries no launch command, so the file a
    ``retroarch-foreign-core`` row names travels as a field — and a field that
    may disagree with the kind beside it is a caveat waiting to state the wrong
    thing, or nothing at all.
    """

    def test_the_word_without_the_file_is_refused(self):
        with pytest.raises(ValueError, match="without that name the caveat"):
            CatalogueEntry(label="Citra", kind=atlas.KIND_RETROARCH_FOREIGN_CORE, core_so=None)

    def test_the_file_without_the_word_is_refused(self):
        with pytest.raises(ValueError, match="nothing here was refused over"):
            CatalogueEntry(
                label="Citra",
                kind=atlas.KIND_STANDALONE,
                core_so=None,
                foreign_core_file="citra_libretro.dll",
            )

    def test_the_pairing_builds(self):
        entry = CatalogueEntry(
            label="Citra",
            kind=atlas.KIND_RETROARCH_FOREIGN_CORE,
            core_so=None,
            foreign_core_file="citra_libretro.dll",
        )
        assert entry.foreign_core_file == "citra_libretro.dll"


class TestALaunchsOwnXdgPinningReachesTheAnswer:
    """Issue #492: the fifth launch fact comes off the entry, like the other four.

    Since #350 a catalogue entry carries its launch's own bases, app id and
    sandbox, all read off one ``_XdgHomes``; whether those bases are a
    flatpak's pinned XDG variables was still the arrangement's answer, taken
    from a different reading. It decides which of two DataRoots DuckStation
    opens — inside a sandbox ``XDG_CONFIG_HOME`` is force-set, so the config
    side is the only candidate — and an entry whose launch runs the installed
    flatpak of an arrangement that is itself unsandboxed disagrees with the
    context about exactly that.

    The pair is hand-built because no catalogue shape composes it today: the
    disagreement arises on an EmuDeck launch of an emulator's own flatpak, and
    of the two carded emulators the settings table names an app id for
    (melonDS, xemu) neither reads the flag, while the one that reads it
    (DuckStation) has no id. That is the latency the issue names, and it is
    why this is a unit test and not a vector — inventing a table row to reach
    it through a fixture would test the invention.

    Every field but the pinning is the same on both sides here, so what the
    answer opens can only be the pinning's doing.
    """

    HOME = "/home/deck"
    DATA_HOME = f"{HOME}/.local/share"
    CONFIG_HOME = f"{HOME}/.config"
    # Two directories that both hold a BIOS image, reachable only one way
    # each: the settings file on the data side names the first, and the second
    # is the emulator's compiled default below the config-side DataRoot.
    STATED_DIR = "/mnt/sd/bios-the-settings-file-names"
    DEFAULT_DIR = f"{CONFIG_HOME}/duckstation/bios"
    STATED_IMAGE = f"{STATED_DIR}/stated.bin"
    DEFAULT_IMAGE = f"{DEFAULT_DIR}/default.bin"
    SETTINGS = f"{DATA_HOME}/duckstation/settings.ini"
    # A row of DuckStation's own table. The tests assert the verdict as well
    # as the path, so a fixture that stops agreeing with the shipped data
    # fails rather than passing against an invention.
    SCPH5501 = "490f666e1afb15b7362b406ed1cea246"
    PS1_SIZE = 524288
    LABEL = "DuckStation (Standalone)"

    def _machine(self, *, settings: bool) -> FixtureMachine:
        """Both directories present; *settings* says whether the data side speaks."""
        image: FixtureFileSpec = {"md5": self.SCPH5501, "size": self.PS1_SIZE}
        files: dict[str, FixtureFileSpec] = {
            self.STATED_IMAGE: image,
            self.DEFAULT_IMAGE: image,
        }
        if settings:
            files[self.SETTINGS] = (
                f"[BIOS]\nSearchDirectory = {self.STATED_DIR}\n"
                "PathNTSCU = \nPathNTSCJ = \nPathPAL = \n"
            )
        return FixtureMachine(files, dirs=[BIOS_DIR, self.STATED_DIR, self.DEFAULT_DIR])

    def _core(
        self, *, entry_pinned: bool | None, context_pinned: bool, settings: bool = True
    ) -> CoreFirmware:
        machine = self._machine(settings=settings)
        context = FirmwareContext(
            root=BIOS_DIR,
            cores=(),
            hashes=load_hashes(TABLE),
            standalone_data_home=self.DATA_HOME,
            standalone_config_home=self.CONFIG_HOME,
            standalone_xdg_pinned=context_pinned,
        )
        entry = CatalogueEntry(
            label=self.LABEL,
            kind=atlas.KIND_STANDALONE,
            core_so=None,
            emulator="DUCKSTATION",
            declared_index=0,
            standalone_token="DUCKSTATION",
            standalone_data_home=self.DATA_HOME,
            standalone_config_home=self.CONFIG_HOME,
            standalone_xdg_pinned=entry_pinned,
        )
        # Verified, because what this emulator's search calls a BIOS is a
        # content question: without the hash there is a directory and a count,
        # and the file a launch opens is what these tests are about.
        answer = firmware_for_system(
            machine, context, system="psx", catalogue=Catalogue((entry,)), verify=True
        )
        return answer.cores[0]

    @staticmethod
    def _opened(core: CoreFirmware) -> list[tuple[str | None, str | None]]:
        """What the search found, with the verdict that makes it a BIOS.

        The verdict travels beside the path so the images in both directories
        have to be rows of the emulator's own table: a file of an accepted
        size whose bytes nothing recognises is found and stays unidentified,
        and a test that read only the path could not tell the two apart.
        """
        return [
            (r.path, r.checked)
            for r in core.requirements
            if isinstance(r, FirmwareRequirement)
        ]

    def test_a_pinned_entry_opens_the_config_side_the_context_would_read_past(self):
        # The defect the issue names, with the two answers made to differ: the
        # entry's launch is inside a sandbox, so only the config side exists
        # for it and the settings file the unpinned arrangement would have
        # read is not this launch's to read.
        core = self._core(entry_pinned=True, context_pinned=False)
        assert self._opened(core) == [(self.DEFAULT_IMAGE, CHECKED_VERIFIED)]

    def test_an_unpinned_entry_reads_past_the_config_side_the_context_pinned(self):
        # And the other way round, so the read cannot be passing by agreeing
        # with the entry only where the entry says what the context says.
        core = self._core(entry_pinned=False, context_pinned=True)
        assert self._opened(core) == [(self.STATED_IMAGE, CHECKED_VERIFIED)]

    @pytest.mark.parametrize(
        ("context_pinned", "expected"), [(False, STATED_IMAGE), (True, DEFAULT_IMAGE)]
    )
    def test_an_entry_that_states_no_pinning_is_governed_by_the_arrangement(
        self, context_pinned: bool, expected: str
    ):
        # The fallback the other four take, spelled for both values, because
        # False is a statement here and only None is the absence of one.
        core = self._core(entry_pinned=None, context_pinned=context_pinned)
        assert self._opened(core) == [(expected, CHECKED_VERIFIED)]

    def test_a_pinned_entry_has_no_dataroot_question_left_to_state(self):
        # With no settings file anywhere the caveat says which root the answer
        # hangs off is the launch environment's to decide — but a pinned launch
        # has one candidate, so there is nothing undecided about it.
        core = self._core(entry_pinned=True, context_pinned=False, settings=False)
        assert CAVEAT_CORE_MODE_UNESTABLISHED not in [c.code for c in core.caveats]

    def test_an_unpinned_entry_still_states_the_dataroot_question(self):
        core = self._core(entry_pinned=False, context_pinned=True, settings=False)
        assert CAVEAT_CORE_MODE_UNESTABLISHED in [c.code for c in core.caveats]


class TestADirectoryTheInstallerFillsByDownload:
    """Issue #354: a declared file the distribution fetches is not one the user is missing.

    EmuDeck places nothing into the firmware root from a tree of its own. What
    it does is download an archive and unpack it there, which leaves the bytes
    at the destination verifiable against nothing on the machine — so
    ``supplied_by``, whose whole content is a measured equality, stays ``None``
    and the statement that can be made is about the DIRECTORY instead. Every
    test here turns on that distinction: the subject is the directory, so the
    statement stands over an empty one, is made once however many declarations
    resolve into it, and is never made about a directory this core declares
    nothing in.
    """

    CORE = "demo_psp_libretro.so"
    TREE = f"{BIOS_DIR}/PPSSPP"
    URL = "https://buildbot.libretro.com/assets/system/PPSSPP.zip"

    def _info(self, *declared: str) -> str:
        rows = "".join(
            f'firmware{index}_desc = "{path.rpartition("/")[2]}"\n'
            f'firmware{index}_path = "{path}"\n'
            f'firmware{index}_opt = "false"\n'
            for index, path in enumerate(declared)
        )
        return (
            'display_name = "A handheld core of no particular make"\n'
            'systemname = "Sony - PlayStation Portable"\n'
            f"firmware_count = {len(declared)}\n{rows}"
        )

    def _core(
        self,
        *declared: str,
        files: Mapping[str, FixtureFileSpec] | None = None,
        dirs: list[str] | None = None,
        distribution: str | None = "emudeck",
    ) -> CoreFirmware:
        stem = self.CORE[: -len(".so")]
        tree: dict[str, FixtureFileSpec] = {
            f"{INFO_DIR}/{stem}.info": self._info(*declared),
            f"{INFO_DIR}/{self.CORE}": {"status": "invalid-text"},
        }
        tree.update(files or {})
        machine = FixtureMachine(tree, dirs=dirs)
        context = replace(_context(machine), distribution=distribution)
        return firmware_for_core(machine, context, core_so=self.CORE).cores[0]

    def _stated(self, core: CoreFirmware) -> list[Caveat]:
        return [c for c in core.caveats if c.code == CAVEAT_FIRMWARE_INSTALLER_DOWNLOAD]

    def test_a_declared_file_inside_the_tree_is_told_who_fills_the_tree(self):
        # The whole data mapping, because a key a client cannot find is the
        # failure this shape has — and both versions are in it: the table's
        # own revision, and the distribution release its citations were read
        # at, which is the only thing this statement rests on.
        core = self._core(
            "PPSSPP/ppge_atlas.zim", files={f"{self.TREE}/ppge_atlas.zim": "whatever landed"}
        )
        (stated,) = self._stated(core)
        assert stated.data == {
            "dir": self.TREE,
            "distribution": "emudeck",
            "url": self.URL,
            "card_version": "1",
            "revision": "acc45fc",
        }

    def test_the_statement_stands_over_an_empty_directory(self):
        # The subject is the directory, so the file being absent changes
        # nothing about it — and that is the case the statement is worth most
        # in: "not in your library" over a file the installer fetches.
        core = self._core("PPSSPP/ppge_atlas.zim", dirs=[self.TREE])
        (requirement,) = _plain_requirements(core)
        assert requirement.found == "missing"
        assert [c.data["dir"] for c in self._stated(core)] == [self.TREE]

    def test_two_declarations_in_one_tree_are_one_statement(self):
        core = self._core("PPSSPP/ppge_atlas.zim", "PPSSPP/flash0/font/jpn0.pgf", dirs=[self.TREE])
        assert len(_plain_requirements(core)) == 2
        assert len(self._stated(core)) == 1

    def test_a_core_that_declares_nothing_in_the_tree_is_told_nothing(self):
        # The tree is there and the card covers it; this core simply does not
        # read anything out of it, so its answer carries no statement about it.
        core = self._core("scph5501.bin", dirs=[self.TREE])
        assert self._stated(core) == []

    def test_a_declaration_of_the_directory_itself_is_not_one_of_its_contents(self):
        # The destination IS the directory the step fills. A core that lists
        # the directory has declared the thing the installer creates, not
        # something the download put inside it.
        core = self._core("PPSSPP", dirs=[self.TREE])
        assert self._stated(core) == []

    def test_a_step_no_caller_reaches_states_nothing(self):
        # The RPG Maker runtime packages: recorded in the card with the caller
        # measurement, invoked by nothing the reading found, and therefore
        # answering no path. A statement here would be about a step atlas has
        # not established runs at all.
        core = self._core("rtp/2000/harmony.dll", dirs=[f"{BIOS_DIR}/rtp/2000"])
        assert self._stated(core) == []

    def test_an_arrangement_with_no_download_card_is_told_nothing(self):
        # RetroDECK copies its trees out of its own deploy and is answered by
        # the copy list; it has no entry in the download table, and a card
        # that is absent states nothing rather than defaulting to something.
        core = self._core(
            "PPSSPP/ppge_atlas.zim",
            files={f"{self.TREE}/ppge_atlas.zim": "whatever landed"},
            distribution="retrodeck",
        )
        assert self._stated(core) == []

    def test_an_arrangement_that_names_no_distribution_is_told_nothing(self):
        core = self._core(
            "PPSSPP/ppge_atlas.zim",
            files={f"{self.TREE}/ppge_atlas.zim": "whatever landed"},
            distribution=None,
        )
        assert self._stated(core) == []

    def test_the_file_in_the_downloaded_tree_is_claimed_by_nobody(self):
        # The mutation this whole shape exists to refuse: a SuppliedBy here
        # would say the bytes equal a copy EmuDeck ships, and EmuDeck ships
        # none — no copy list is keyed under its word, so the provenance route
        # answers None for the reason the caveat gives rather than in silence.
        core = self._core(
            "PPSSPP/ppge_atlas.zim", files={f"{self.TREE}/ppge_atlas.zim": "whatever landed"}
        )
        (requirement,) = _plain_requirements(core)
        assert requirement.found == "file"
        assert requirement.supplied_by is None
        assert lookup_distribution_supplied("emudeck") is None
