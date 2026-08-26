"""Tests for atlas.firmware — emulators, their firmware, and what is on disk.

The model is proven from data along its two axes: ``need`` is what an emulator
asks for, ``present``/``checked`` is what the machine answers, and the four
``checked`` values stay apart. Two classes carry the load. The first is
:class:`TestNoDeclarationIsNeverSatisfied`: having no declaration must never
look like nothing missing. The second is :class:`TestPartialReaderIsNotMisled`
— a caller that renders one field and ignores the rest may end up uninformed,
but never wrong.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Mapping
from xml.etree import ElementTree as ET

import pytest

from atlas.firmware import (
    CAVEAT_SYSTEM_ASSIGNMENT_MAY_HIDE_CORES,
    CAVEAT_EMULATOR_CATALOGUE_UNAVAILABLE,
    CAVEAT_EMULATOR_CATALOGUE_UNREADABLE,
    CAVEAT_FIRMWARE_CONTENT_CONTRADICTORY,
    CAVEAT_FIRMWARE_CONTENT_UNIDENTIFIED,
    CAVEAT_FIRMWARE_CONTENT_UNSTATED,
    CAVEAT_CORE_INFO_UNREADABLE,
    CAVEAT_CORE_NOT_INSTALLED,
    CAVEAT_CORE_WITHOUT_SYSTEMNAME,
    CAVEAT_FIRMWARE_DECLARATION_UNKNOWN,
    CAVEAT_FIRMWARE_DECLARATION_UNREAD,
    CAVEAT_FIRMWARE_PATH_ESCAPES_ROOT,
    CAVEAT_FIRMWARE_PATH_INACCESSIBLE,
    CAVEAT_FIRMWARE_PATH_NAMES_NO_FILE,
    CAVEAT_FIRMWARE_PATH_OBSTRUCTED,
    CAVEAT_FIRMWARE_PATH_UNRESOLVABLE,
    CAVEAT_FIRMWARE_ROOT_UNUSABLE,
    CAVEAT_FIRMWARE_UNREADABLE,
    CAVEAT_NO_FIRMWARE_DECLARATION,
    CAVEAT_NO_FIRMWARE_REQUIREMENT,
    CAVEAT_STANDALONE_UNSUPPORTED,
    CAVEAT_SYSTEM_ASSIGNMENT_DERIVED,
    CAVEAT_SYSTEM_NOT_IN_CATALOGUE,
    CAVEAT_SYSTEM_UNKNOWN,
    CHECKED_MISMATCH,
    CHECKED_UNKNOWN,
    DECLARATION_ABSENT,
    DECLARATION_UNSUPPORTED,
    DECLARATION_READ,
    DECLARATION_UNREADABLE,
    FIRMWARE_SYSTEM_OVERRIDE,
    NEED_REQUIRED,
    SYSTEMNAME_MAP_VERSION,
    SYSTEMNAME_TO_SLUG,
    SYSTEMS_WITHOUT_CATALOGUE_ID,
    Catalogue,
    CatalogueEntry,
    CoreDeclarations,
    CoreFirmware,
    Destination,
    FirmwareAlternatives,
    FirmwareAnswer,
    FirmwareContext,
    FirmwareIdentity,
    FirmwareRequirement,
    destination_under,
    firmware_for_core,
    firmware_for_system,
    firmware_inventory,
    identify_firmware,
    load_hashes,
    read_core_declarations,
    resolve_links,
    save_artifact_paths,
    system_decision,
    system_for,
)
from atlas.machine import (
    AppImageReadResult,
    CoreInfo,
    FixtureFileSpec,
    FixtureMachine,
    GlobResult,
    PathKind,
    ReadResult,
)
from atlas.placement import CAVEAT_SYSTEM_DIRECTORY_CLEARED, Caveat
from atlas.systems import known_systems

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
display_name = "Sony - PlayStation (Beetle PSX)"
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

# A multi-system core: one systemname, a database naming two systems, and one
# declared file with no per-file rule.
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

TABLE = json.dumps(
    {
        "_meta": {"generated_from": "test", "version": "0", "generated_at": "2026-01-01"},
        "files": {
            "scph5501.bin": {"md5": "aa" * 16, "sha1": "bb" * 20, "size": 8},
            "dc/dc_boot.bin": {"md5": "cc" * 16, "sha1": "dd" * 20, "size": 4},
            # One content, two canonical names — the gambatte/SameBoy case.
            "gb_bios.bin": {"md5": "ee" * 16, "sha1": "ff" * 20, "size": 5},
            "dmg_boot.bin": {"md5": "ee" * 16, "sha1": "ff" * 20, "size": 5},
            # No per-file rule covers this one, so a core declaring it is filed
            # by its systemname — the derived case, reachable by content.
            "gba_bios.bin": {"md5": "11" * 16, "sha1": "22" * 20, "size": 6},
        },
    }
)


def _blob(content: bytes) -> dict[str, str | int]:
    return {
        "md5": hashlib.md5(content).hexdigest(),
        "sha1": hashlib.sha1(content).hexdigest(),
        "size": len(content),
    }


def _machine(files: Mapping[str, FixtureFileSpec] | None = None, **kwargs: object) -> FixtureMachine:
    tree: dict[str, FixtureFileSpec] = {
        f"{INFO_DIR}/mednafen_psx_libretro.info": PSX_INFO,
        f"{INFO_DIR}/mednafen_psx_libretro.so": {"status": "invalid-text"},
    }
    if files is not None:
        tree.update(files)
    return FixtureMachine(tree, **kwargs)  # type: ignore[arg-type]


def _context(machine: FixtureMachine, *, root: str | None = BIOS_DIR, core_dir: str | None = INFO_DIR):
    return FirmwareContext(
        root=root,
        cores=read_core_declarations(machine, INFO_DIR, core_dir=core_dir).cores,
        hashes=load_hashes(TABLE),
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
        assert [c.core_so for c in cores] == ["mednafen_psx_libretro.so"]

    def test_without_a_core_dir_nothing_is_filtered(self):
        machine = _machine({f"{INFO_DIR}/flycast_libretro.info": DC_INFO})
        cores = read_core_declarations(machine, INFO_DIR).cores
        assert {c.core_so for c in cores} == {"mednafen_psx_libretro.so", "flycast_libretro.so"}

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
            "declared": "firmware1_path",
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
            "declared": "firmwareA_path",
            "firmware_count": "1",
        }

    def test_a_path_key_with_no_index_is_not_silently_dropped(self):
        core = self._answered('firmware_count = 1\nfirmware_path = "needed.bin"\n')
        assert core.requirements == ()
        assert core.requirements_met is True
        assert [c.code for c in core.caveats] == [CAVEAT_FIRMWARE_DECLARATION_UNREAD]
        assert core.caveats[0].data["declared"] == "firmware_path"

    def test_an_empty_path_inside_the_count_is_not_silently_dropped(self):
        # The one shape RetroArch does look up: it finds the entry, sees an
        # empty value and keeps the NULL (core_info.c:1610). The slot is empty
        # either way; that the file states the key is what gets said.
        core = self._answered('firmware_count = 2\nfirmware0_path = "a.bin"\nfirmware1_path = ""\n')
        assert [r.declared for r in _plain_requirements(core)] == ["a.bin"]
        assert [c.code for c in core.caveats] == [CAVEAT_FIRMWARE_DECLARATION_UNREAD]
        assert core.caveats[0].data["declared"] == "firmware1_path"

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
        assert caveat.data["declared"] == "firmware0_path, firmware1_path, firmware_path"
        assert "firmware_path (no key it composes is spelled that way)" in caveat.message
        assert "firmware1_path (its firmware_count is 1, so it reads firmware0_ up to firmware0_" in caveat.message
        assert "firmware0_path (an empty value, which the read that finds it discards)" in caveat.message


class TestPerCoreAnswer:
    """Criterion 1: does this core need firmware, and where does each file go?"""

    def test_the_destination_is_absolute_whether_or_not_a_file_is_there(self):
        answer = firmware_for_core(_machine(), _context(_machine()), core_so="mednafen_psx_libretro.so")
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

    @pytest.mark.parametrize("given", ["mednafen_psx_libretro.so", "mednafen_psx_libretro", "/x/y/mednafen_psx_libretro.so"])
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
        table = json.dumps({"_meta": {}, "files": {"scph5501.bin": _blob(content)}})
        machine = _machine({f"{BIOS_DIR}/scph5501.bin": _blob(content)})
        context = FirmwareContext(
            root=BIOS_DIR,
            cores=read_core_declarations(machine, INFO_DIR, core_dir=INFO_DIR).cores,
            hashes=load_hashes(table),
        )
        core = firmware_for_core(
            machine, context, core_so="mednafen_psx_libretro.so", verify=True
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
        core = firmware_for_core(machine, _context(machine), core_so="mednafen_psx_libretro.so").cores[0]
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
        answer = firmware_for_core(machine, _context(machine), core_so="mednafen_psx_libretro.so")
        uncovered = next(r for r in answer.requirements if r.file_name == "psxonpsp660.bin")
        assert uncovered.identity is None
        assert uncovered.checked == CHECKED_UNKNOWN
        assert uncovered.satisfied is True

    def test_a_missing_required_file_is_unmet(self):
        machine = _machine()
        core = firmware_for_core(machine, _context(machine), core_so="mednafen_psx_libretro.so").cores[0]
        assert [r.file_name for r in core.unmet] == ["scph5501.bin"]
        assert core.requirements_met is False


class TestCheckedAxis:
    """The four values of ``checked`` — and that none of them collapse."""

    def test_nothing_there_means_nothing_to_check(self):
        machine = _machine()
        requirement = _by_name(firmware_for_core(machine, _context(machine), core_so="mednafen_psx_libretro.so"))
        assert requirement["scph5501.bin"].present is False
        assert requirement["scph5501.bin"].checked is None

    def test_a_known_identity_not_asked_about_is_unchecked(self):
        machine = _machine({f"{BIOS_DIR}/scph5501.bin": _blob(b"12345678")})
        requirement = _by_name(firmware_for_core(machine, _context(machine), core_so="mednafen_psx_libretro.so"))
        assert requirement["scph5501.bin"].checked == "unchecked"

    def test_an_unknown_identity_is_unknown_even_when_asked_about(self):
        # psxonpsp660.bin is not in this table: no amount of verifying can
        # establish what it is, which is a different answer from "not checked".
        machine = _machine({f"{BIOS_DIR}/psxonpsp660.bin": _blob(b"whatever")})
        answer = firmware_for_core(
            machine, _context(machine), core_so="mednafen_psx_libretro.so", verify=True
        )
        assert _by_name(answer)["psxonpsp660.bin"].checked == "unknown"

    def test_matching_bytes_verify(self):
        content = b"12345678"
        table = json.dumps(
            {"_meta": {}, "files": {"scph5501.bin": {**_blob(content), "md5": hashlib.md5(content).hexdigest()}}}
        )
        machine = _machine({f"{BIOS_DIR}/scph5501.bin": _blob(content)})
        context = FirmwareContext(
            root=BIOS_DIR,
            cores=read_core_declarations(machine, INFO_DIR, core_dir=INFO_DIR).cores,
            hashes=load_hashes(table),
        )
        answer = firmware_for_core(machine, context, core_so="mednafen_psx_libretro.so", verify=True)
        assert _by_name(answer)["scph5501.bin"].checked == "verified"

    def test_the_right_size_with_the_wrong_bytes_is_a_mismatch(self):
        # Size passes the free pre-filter, the digest does not.
        machine = _machine({f"{BIOS_DIR}/scph5501.bin": {"md5": "00" * 16, "sha1": "00" * 20, "size": 8}})
        answer = firmware_for_core(
            machine, _context(machine), core_so="mednafen_psx_libretro.so", verify=True
        )
        assert _by_name(answer)["scph5501.bin"].checked == "mismatch"

    def test_a_wrong_size_settles_it_without_reading_the_file(self):
        machine = _machine({f"{BIOS_DIR}/scph5501.bin": {"size": 9}})
        answer = firmware_for_core(
            machine, _context(machine), core_so="mednafen_psx_libretro.so", verify=True
        )
        assert _by_name(answer)["scph5501.bin"].checked == "mismatch"

    def test_a_present_but_unreadable_file_stays_unknown_and_says_so(self):
        machine = _machine({f"{BIOS_DIR}/scph5501.bin": {"size": 8}})
        answer = firmware_for_core(
            machine, _context(machine), core_so="mednafen_psx_libretro.so", verify=True
        )
        assert _by_name(answer)["scph5501.bin"].checked == "unknown"
        assert CAVEAT_FIRMWARE_UNREADABLE in [c.code for c in answer.caveats]

    def test_hash_checked_records_whether_verification_ran(self):
        machine = _machine()
        context = _context(machine)
        assert firmware_for_core(machine, context, core_so="mednafen_psx_libretro.so").hash_checked is False
        assert (
            firmware_for_core(machine, context, core_so="mednafen_psx_libretro.so", verify=True).hash_checked
            is True
        )


class TestRequirementInvariants:
    """The dataclass refuses states that would lie."""

    def test_an_absent_file_cannot_carry_a_verdict(self):
        with pytest.raises(ValueError):
            FirmwareRequirement(
                core_so="x.so", system="psx", system_source="systemname", need="required",
                file_name="a.bin", path="/bios/a.bin", declared="a.bin", description="", identity=None,
                found="missing", checked="verified",
            )

    def test_a_present_file_must_state_one_of_the_four(self):
        with pytest.raises(ValueError):
            FirmwareRequirement(
                core_so="x.so", system="psx", system_source="systemname", need="required",
                file_name="a.bin", path="/bios/a.bin", declared="a.bin", description="", identity=None,
                found="file", checked=None,
            )

    def test_need_is_only_required_or_optional(self):
        with pytest.raises(ValueError):
            FirmwareRequirement(
                core_so="x.so", system="psx", system_source="systemname",
                need="undeclared", file_name="a.bin",  # type: ignore[arg-type]
                path="/bios/a.bin", declared="a.bin", description="", identity=None, found="missing",
                checked=None,
            )


class TestWhatTheMachineWouldNotSay:
    """States atlas must not flatten into a claim it did not establish."""

    def test_an_inaccessible_path_is_not_an_absent_file(self):
        machine = _machine(inaccessible=[f"{BIOS_DIR}/scph5501.bin"])
        answer = firmware_for_core(machine, _context(machine), core_so="mednafen_psx_libretro.so")
        blocked = next(r for r in answer.requirements if r.file_name == "scph5501.bin")
        assert blocked.found == "inaccessible"
        assert blocked.present is None, "could not look is not 'not there'"
        assert blocked.checked is None
        assert blocked.satisfied is None
        assert CAVEAT_FIRMWARE_PATH_INACCESSIBLE in [c.code for c in answer.caveats]
        assert answer.cores[0].requirements_met is None

    def test_a_directory_where_a_file_belongs_says_so(self):
        machine = _machine(dirs=[f"{BIOS_DIR}/scph5501.bin"])
        answer = firmware_for_core(machine, _context(machine), core_so="mednafen_psx_libretro.so")
        blocked = next(r for r in answer.requirements if r.file_name == "scph5501.bin")
        # Something is there and nothing about it was established — LRPS2 even
        # declares a folder on purpose, so this is not a missing file and not
        # an invitation to delete anything.
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

    def test_a_multi_system_core_falling_back_states_it(self):
        machine = _machine({f"{INFO_DIR}/mgba_libretro.info": MGBA_INFO,
                            f"{INFO_DIR}/mgba_libretro.so": {"status": "invalid-text"}})
        core = firmware_for_core(machine, _context(machine), core_so="mgba_libretro.so").cores[0]
        caveat = next(c for c in core.caveats if c.code == CAVEAT_SYSTEM_ASSIGNMENT_DERIVED)
        # gb_bios.bin has a per-file rule; gba_bios.bin does not.
        assert caveat.data["files"] == "gba_bios.bin"
        assert caveat.data["database"] == "Nintendo - Game Boy|Nintendo - Game Boy Advance"

    def test_full_override_coverage_states_nothing(self):
        machine = _machine({f"{INFO_DIR}/covered_libretro.info": FULLY_OVERRIDDEN_INFO,
                            f"{INFO_DIR}/covered_libretro.so": {"status": "invalid-text"}})
        core = firmware_for_core(machine, _context(machine), core_so="covered_libretro.so").cores[0]
        assert core.caveats == ()

    def test_a_single_system_core_states_nothing(self):
        # The fallback is sound when the core covers one system, however many
        # of its files lack a per-file rule.
        machine = _machine()
        core = firmware_for_core(machine, _context(machine), core_so="mednafen_psx_libretro.so").cores[0]
        assert core.caveats == ()

    def test_a_core_without_a_systemname_is_its_own_case(self):
        machine = _machine({f"{INFO_DIR}/skyemu_libretro.info": SKYEMU_INFO,
                            f"{INFO_DIR}/skyemu_libretro.so": {"status": "invalid-text"}})
        answer = firmware_for_core(machine, _context(machine), core_so="skyemu_libretro.so")
        core = answer.cores[0]
        assert [c.code for c in core.caveats] == [CAVEAT_CORE_WITHOUT_SYSTEMNAME]
        assert [r.system for r in _plain_requirements(core) if r.file_name == "gba_bios.bin"] == ["_unknown"]
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
        assert core.caveats == ()

    def test_a_system_query_names_the_cores_a_derived_slug_may_hide(self):
        # Without a catalogue the selection is keyed on the cores' own
        # systemname, so a core filed under the wrong slug is unreachable AND
        # its caveat never gets attached. The answer names the candidates.
        machine = _machine({f"{INFO_DIR}/atari800_libretro.info": ATARI800_INFO,
                            f"{INFO_DIR}/atari800_libretro.so": {"status": "invalid-text"}})
        answer = firmware_for_system(machine, _context(machine), system="atari5200")
        assert answer.cores == ()
        hiding = next(c for c in answer.caveats if c.code == CAVEAT_SYSTEM_ASSIGNMENT_MAY_HIDE_CORES)
        assert hiding.data["cores"] == "atari800_libretro.so"

    def test_it_names_only_cores_whose_own_database_covers_the_question(self):
        # mGBA is derived too, but nothing on the machine says it covers the
        # Atari 5200 — naming it would train the reader to skip the line.
        machine = _machine({f"{INFO_DIR}/mgba_libretro.info": MGBA_INFO,
                            f"{INFO_DIR}/mgba_libretro.so": {"status": "invalid-text"}})
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
        machine = _machine({f"{INFO_DIR}/mgba_libretro.info": MGBA_INFO,
                            f"{INFO_DIR}/mgba_libretro.so": {"status": "invalid-text"}})
        identified = identify_firmware(machine, _context(machine), md5="11" * 16)
        assert [r.file_name for r in identified.requirements] == ["gba_bios.bin"]
        assert CAVEAT_SYSTEM_ASSIGNMENT_DERIVED in [c.code for c in identified.caveats]

    def test_a_caveat_about_other_files_stays_off_the_identification(self):
        # The download flow asks about ONE content. mGBA's caveat names
        # gba_bios.bin; an answer about the Game Boy boot ROM — which has a
        # per-file rule — must not carry a warning about a file it does not
        # contain.
        machine = _machine({f"{INFO_DIR}/mgba_libretro.info": MGBA_INFO,
                            f"{INFO_DIR}/mgba_libretro.so": {"status": "invalid-text"}})
        identified = identify_firmware(machine, _context(machine), md5="ee" * 16)
        assert [r.file_name for r in identified.requirements] == ["gb_bios.bin"]
        assert all(r.system_source == "override" for r in identified.requirements)
        assert [c.code for c in identified.caveats] == []

    def test_the_database_field_is_read_as_a_signal_not_as_a_name(self):
        machine = _machine({f"{INFO_DIR}/mgba_libretro.info": MGBA_INFO,
                            f"{INFO_DIR}/mgba_libretro.so": {"status": "invalid-text"}})
        core = next(c for c in read_core_declarations(machine, INFO_DIR, core_dir=INFO_DIR).cores if c.stem == "mgba_libretro")
        assert core.database == ("Nintendo - Game Boy", "Nintendo - Game Boy Advance")
        # The core's own system still comes from systemname, never from
        # database — the two disagree here, and systemname wins.
        assert core.system == "gba"


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
        table = json.dumps({"_meta": {}, "files": {"gb_bios.bin": _blob(content)}})
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
            (CatalogueEntry(label="Beetle PSX", kind="libretro", core_so="mednafen_psx_libretro.so"),),
            hole=hole,
        )
        answer = firmware_for_system(machine, _context(machine), system="psx", catalogue=catalogue)
        assert [c.label for c in answer.cores] == ["Beetle PSX"]
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
        machine = _machine({f"{INFO_DIR}/mednafen_psx_libretro.info": info})
        context = _context(machine)
        catalogue = Catalogue(
            (CatalogueEntry(label="Beetle PSX", kind="libretro", core_so="mednafen_psx_libretro.so"),)
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
            firmware_for_core(machine, context, core_so="mednafen_psx_libretro.so"),
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
            (installed, firmware_for_core(installed, _context(installed), core_so="mednafen_psx_libretro.so")),
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
                for requirement in _plain_requirements(core):
                    if requirement.checked == CHECKED_MISMATCH:
                        seen_mismatch = True
                if core.requirements_met is not True:
                    continue
                assert core.declaration == DECLARATION_READ
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
        assert seen_mismatch, (
            "this class must exercise a verified-wrong required file, or it proves nothing about the case "
            "that broke"
        )

    def test_a_required_file_with_the_wrong_bytes_is_unmet(self):
        # The live case: ecwolf declares ecwolf.pk3 as required, the file is
        # there, and its bytes are a different release of the same pack.
        machine = _machine({f"{BIOS_DIR}/scph5501.bin": _blob(b"12345678")})
        core = firmware_for_core(
            machine, _context(machine), core_so="mednafen_psx_libretro.so", verify=True
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
            machine, _context(machine), core_so="mednafen_psx_libretro.so", verify=True
        ).cores[0]
        undecided = next(r for r in _plain_requirements(core) if r.file_name == "scph5501.bin")
        assert undecided.found == "file"
        assert undecided.checked == CHECKED_UNKNOWN
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

    def file_size(self, path: str) -> int | None:
        self._count("file_size")
        return self._inner.file_size(path)

    def file_digest(self, path: str, algorithm: str) -> str | None:
        self._count("file_digest")
        return self._inner.file_digest(path, algorithm)


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
    left = FirmwareIdentity(md5="a" * 32, sha1="b" * 40, size=4, known_as=("x",))
    right = FirmwareIdentity(md5="a" * 32, sha1="b" * 40, size=4, known_as=("x",))
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
            "files": "ti83.rom",
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
