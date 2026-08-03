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
from typing import Mapping

import pytest

from atlas.firmware import (
    CAVEAT_CATALOGUE_UNAVAILABLE,
    CAVEAT_CONTENT_UNIDENTIFIED,
    CAVEAT_CORE_NOT_INSTALLED,
    CAVEAT_FIRMWARE_UNREADABLE,
    CAVEAT_NO_FIRMWARE_DECLARATION,
    CAVEAT_STANDALONE_EMULATOR,
    CatalogueEntry,
    FirmwareContext,
    FirmwareIdentity,
    FirmwareRequirement,
    firmware_for_core,
    firmware_for_system,
    firmware_inventory,
    identify_firmware,
    load_hashes,
    read_core_declarations,
    save_artifact_paths,
    system_for,
)
from atlas.machine import FixtureFileSpec, FixtureMachine

INFO_DIR = "/cores"
BIOS_DIR = "/bios"

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

TABLE = json.dumps(
    {
        "_meta": {"generated_from": "test", "version": "0", "generated_at": "2026-01-01"},
        "files": {
            "scph5501.bin": {"md5": "aa" * 16, "sha1": "bb" * 20, "size": 8},
            "dc/dc_boot.bin": {"md5": "cc" * 16, "sha1": "dd" * 20, "size": 4},
            # One content, two canonical names — the gambatte/SameBoy case.
            "gb_bios.bin": {"md5": "ee" * 16, "sha1": "ff" * 20, "size": 5},
            "dmg_boot.bin": {"md5": "ee" * 16, "sha1": "ff" * 20, "size": 5},
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
        cores=read_core_declarations(machine, INFO_DIR, core_dir=core_dir),
        hashes=load_hashes(TABLE),
    )


class TestHashTable:
    """The packaged table: names in, identities out — and content in, names out."""

    def test_for_path_matches_the_declared_path_first(self):
        hashes = load_hashes(TABLE)
        identity = hashes.for_path("dc/dc_boot.bin")
        assert identity is not None and identity.md5 == "cc" * 16

    def test_for_path_falls_back_to_the_base_name(self):
        # A core declaring "subdir/scph5501.bin" still names the same dump.
        identity = load_hashes(TABLE).for_path("psx/scph5501.bin")
        assert identity is not None and identity.size == 8

    def test_an_uncovered_name_is_a_normal_none(self):
        assert load_hashes(TABLE).for_path("neogeo.zip") is None

    def test_known_as_carries_every_name_for_one_content(self):
        identity = load_hashes(TABLE).for_path("gb_bios.bin")
        assert identity is not None
        assert identity.known_as == ("dmg_boot.bin", "gb_bios.bin")

    def test_for_content_matches_by_bytes_not_by_name(self):
        identity = load_hashes(TABLE).for_content(md5="EE" * 16)
        assert identity is not None and identity.known_as == ("dmg_boot.bin", "gb_bios.bin")

    def test_for_content_requires_every_supplied_field_to_agree(self):
        hashes = load_hashes(TABLE)
        assert hashes.for_content(md5="ee" * 16, size=5) is not None
        assert hashes.for_content(md5="ee" * 16, size=6) is None
        assert hashes.for_content(md5="ee" * 16, sha1="00" * 20) is None

    def test_size_alone_is_not_an_identity(self):
        with pytest.raises(ValueError):
            load_hashes(TABLE).for_content(size=5)

    def test_the_packaged_table_loads_and_is_not_empty(self):
        packaged = load_hashes()
        assert packaged.names()
        assert packaged.meta["generated_from"]


class TestSystemSlug:
    """What a declaration belongs to — override, map, or a mechanical slug."""

    def test_the_per_file_override_wins_over_the_core_systemname(self):
        assert system_for("gb_bios.bin", "Game Boy/Game Boy Color/Game Boy Advance") == "gb"

    def test_a_known_systemname_maps(self):
        assert system_for("dc/dc_boot.bin", "Sega - Dreamcast") == "dc"

    def test_an_unknown_systemname_is_slugified_not_dropped(self):
        assert system_for("weird.bin", "Some New Machine") == "some-new-machine"

    def test_an_empty_systemname_is_the_catch_all(self):
        assert system_for("weird.bin", "") == "_unknown"


class TestReadDeclarations:
    """The live read: every installed core, firmware or not."""

    def test_a_core_without_firmware_is_still_read(self):
        machine = _machine({f"{INFO_DIR}/snes9x_libretro.info": NO_FIRMWARE_INFO,
                            f"{INFO_DIR}/snes9x_libretro.so": {"status": "invalid-text"}})
        cores = {c.core_so: c for c in read_core_declarations(machine, INFO_DIR, core_dir=INFO_DIR)}
        assert cores["snes9x_libretro.so"].firmware == ()
        assert cores["snes9x_libretro.so"].system == "snes"

    def test_a_core_without_its_so_is_not_installed(self):
        machine = _machine({f"{INFO_DIR}/flycast_libretro.info": DC_INFO})
        cores = read_core_declarations(machine, INFO_DIR, core_dir=INFO_DIR)
        assert [c.core_so for c in cores] == ["mednafen_psx_libretro.so"]

    def test_without_a_core_dir_nothing_is_filtered(self):
        machine = _machine({f"{INFO_DIR}/flycast_libretro.info": DC_INFO})
        cores = read_core_declarations(machine, INFO_DIR)
        assert {c.core_so for c in cores} == {"mednafen_psx_libretro.so", "flycast_libretro.so"}

    def test_the_template_info_files_are_dropped(self):
        machine = _machine({f"{INFO_DIR}/00_example_libretro.info": TEMPLATE_INFO,
                            f"{INFO_DIR}/00_example_libretro.so": {"status": "invalid-text"}})
        cores = read_core_declarations(machine, INFO_DIR, core_dir=INFO_DIR)
        assert all("filename.ext" not in d.path for c in cores for d in c.firmware)

    def test_a_missing_opt_flag_means_required(self):
        machine = _machine()
        core = read_core_declarations(machine, INFO_DIR, core_dir=INFO_DIR)[0]
        needs = {d.file_name: d.need for d in core.firmware}
        assert needs == {"scph5501.bin": "required", "psxonpsp660.bin": "optional"}


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
        assert [c.installed for c in answer.cores] == [True]

    def test_an_installed_core_declaring_nothing_needs_nothing(self):
        machine = _machine({f"{INFO_DIR}/snes9x_libretro.info": NO_FIRMWARE_INFO,
                            f"{INFO_DIR}/snes9x_libretro.so": {"status": "invalid-text"}})
        answer = firmware_for_core(machine, _context(machine), core_so="snes9x_libretro.so")
        core = answer.cores[0]
        assert core.installed is True
        assert core.requirements == ()
        assert core.requirements_met is True
        assert [c.code for c in answer.caveats] == []

    def test_requirements_met_counts_only_required_files(self):
        machine = _machine({f"{BIOS_DIR}/scph5501.bin": _blob(b"12345678")})
        answer = firmware_for_core(machine, _context(machine), core_so="mednafen_psx_libretro.so")
        core = answer.cores[0]
        assert [r.file_name for r in core.unmet] == []
        assert core.requirements_met is True

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
            cores=read_core_declarations(machine, INFO_DIR, core_dir=INFO_DIR),
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
                core_so="x.so", system="psx", need="required", file_name="a.bin", path="/bios/a.bin",
                description="", identity=None, present=False, checked="verified",
            )

    def test_a_present_file_must_state_one_of_the_four(self):
        with pytest.raises(ValueError):
            FirmwareRequirement(
                core_so="x.so", system="psx", need="required", file_name="a.bin", path="/bios/a.bin",
                description="", identity=None, present=True, checked=None,
            )

    def test_need_is_only_required_or_optional(self):
        with pytest.raises(ValueError):
            FirmwareRequirement(
                core_so="x.so", system="psx", need="undeclared", file_name="a.bin",  # type: ignore[arg-type]
                path="/bios/a.bin", description="", identity=None, present=False, checked=None,
            )


class TestPerSystemAnswer:
    """Criterion 2: which emulators run this system, and what does each want?"""

    def test_without_a_catalogue_the_cores_own_systemname_enumerates(self):
        machine = _gb_machine()
        answer = firmware_for_system(machine, _context(machine), system="gb")
        assert [c.core_so for c in answer.cores] == ["gambatte_libretro.so", "sameboy_libretro.so"]
        assert CAVEAT_CATALOGUE_UNAVAILABLE in [c.code for c in answer.caveats]

    def test_a_catalogue_lists_emulators_whose_core_is_not_installed(self):
        machine = _gb_machine()
        catalogue = (
            CatalogueEntry(label="Gambatte", kind="libretro", core_so="gambatte_libretro.so"),
            CatalogueEntry(label="TGB Dual", kind="libretro", core_so="tgbdual_libretro.so"),
        )
        answer = firmware_for_system(machine, _context(machine), system="gb", catalogue=catalogue)
        by_label = {c.label: c for c in answer.cores}
        assert by_label["TGB Dual"].installed is False
        assert by_label["TGB Dual"].requirements == ()
        assert by_label["TGB Dual"].requirements_met is None
        assert [c.code for c in by_label["TGB Dual"].caveats] == [CAVEAT_CORE_NOT_INSTALLED]

    def test_a_standalone_emulator_is_stated_not_dropped(self):
        machine = _gb_machine()
        catalogue = (
            CatalogueEntry(label="Gambatte", kind="libretro", core_so="gambatte_libretro.so"),
            CatalogueEntry(label="SameBoy (Standalone)", kind="standalone", core_so=None),
        )
        answer = firmware_for_system(machine, _context(machine), system="gb", catalogue=catalogue)
        standalone = answer.cores[1]
        assert standalone.installed is False
        assert [c.code for c in standalone.caveats] == [CAVEAT_STANDALONE_EMULATOR]

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
            cores=read_core_declarations(machine, INFO_DIR, core_dir=INFO_DIR),
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
        assert CAVEAT_CONTENT_UNIDENTIFIED in [c.code for c in identified.caveats]

    def test_recognised_content_nobody_here_wants_is_not_a_silent_empty(self):
        machine = _machine()  # only the PSX core is installed
        identified = identify_firmware(machine, _context(machine), md5="ee" * 16)
        assert identified.identity is not None
        assert identified.requirements == ()
        assert CAVEAT_NO_FIRMWARE_DECLARATION in [c.code for c in identified.caveats]

    def test_size_must_agree_when_it_is_supplied(self):
        machine = _gb_machine()
        assert identify_firmware(machine, _context(machine), md5="ee" * 16, size=99).identity is None


class TestNoDeclarationIsNeverSatisfied:
    """The defect the whole design exists to prevent."""

    def test_an_unknown_core_answers_unknown_not_nothing(self):
        machine = _machine()
        answer = firmware_for_core(machine, _context(machine), core_so="mgba_libretro.so")
        assert answer.cores[0].installed is False
        assert answer.cores[0].requirements == ()
        assert answer.cores[0].requirements_met is None
        assert [c.code for c in answer.cores[0].caveats] == [CAVEAT_CORE_NOT_INSTALLED]
        assert CAVEAT_NO_FIRMWARE_DECLARATION in [c.code for c in answer.caveats]

    def test_a_system_nobody_declares_is_empty_with_a_caveat(self):
        machine = _machine()
        answer = firmware_for_system(machine, _context(machine), system="n64")
        assert answer.cores == ()
        assert CAVEAT_NO_FIRMWARE_DECLARATION in [c.code for c in answer.caveats]

    def test_an_unresolvable_info_directory_yields_no_requirements(self):
        machine = FixtureMachine({f"{BIOS_DIR}/scph5501.bin": _blob(b"12345678")})
        context = FirmwareContext(root=BIOS_DIR, cores=(), hashes=load_hashes(TABLE))
        answer = firmware_inventory(machine, context)
        assert answer.requirements == ()
        assert CAVEAT_NO_FIRMWARE_DECLARATION in [c.code for c in answer.caveats]

    def test_without_a_root_there_is_nothing_to_resolve_against(self):
        machine = _machine()
        context = FirmwareContext(
            root=None,
            cores=read_core_declarations(machine, INFO_DIR, core_dir=INFO_DIR),
            hashes=load_hashes(TABLE),
        )
        for answer in (
            firmware_for_core(machine, context, core_so="mednafen_psx_libretro.so"),
            firmware_for_system(machine, context, system="psx"),
            firmware_inventory(machine, context),
        ):
            assert answer.root is None
            assert answer.cores == ()
            assert answer.unclaimed == ()


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
                assert core.installed or core.caveats, (
                    "an empty list from a core atlas could not read must say so"
                )

    def test_requirements_never_come_from_a_core_that_was_not_read(self):
        for _, answer in self._answers():
            for core in answer.cores:
                assert core.installed or not core.requirements

    def test_a_verdict_never_appears_without_verification(self):
        for _, answer in self._answers():
            if answer.hash_checked:
                continue
            assert all(r.checked not in ("verified", "mismatch") for r in answer.requirements)

    def test_presence_and_the_check_never_disagree(self):
        for _, answer in self._answers():
            for requirement in answer.requirements:
                assert (requirement.checked is None) is (not requirement.present)

    def test_an_unidentifiable_file_never_reads_as_merely_unchecked(self):
        for _, answer in self._answers():
            for requirement in answer.requirements:
                if requirement.present and requirement.identity is None:
                    assert requirement.checked == "unknown"

    def test_an_unclaimed_file_never_carries_a_name_it_was_not_matched_by(self):
        for _, answer in self._answers():
            for unclaimed in answer.unclaimed:
                assert (unclaimed.known_as == ()) is (unclaimed.identity is None)

    def test_requirements_met_is_never_true_out_of_ignorance(self):
        for _, answer in self._answers():
            for core in answer.cores:
                if core.requirements_met is True:
                    assert core.installed and not core.unmet


def _by_name(answer) -> dict[str, FirmwareRequirement]:
    return {r.file_name: r for r in answer.requirements}


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
