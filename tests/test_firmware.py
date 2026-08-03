"""Tests for atlas.firmware — live declarations, the packaged table, and the state model.

The states are the contract, so each one is proven from data: a declared file
absent from disk, one present without a known identity, one whose identity
matches, one whose identity does not, and a file nobody declared. The single
most important case is the last class in this file: having *no declaration* is
never allowed to look like *nothing missing*.
"""

from __future__ import annotations

import hashlib
import json
from typing import Mapping

import pytest

from atlas.firmware import (
    CAVEAT_NO_FIRMWARE_DECLARATION,
    FirmwareFile,
    load_hashes,
    platform_for,
    read_declarations,
    resolve_firmware,
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

# The same PSX file, declared as optional by a second core: required is the OR.
PSX_SECOND_INFO = """
systemname = "PlayStation"
firmware_count = 1
firmware0_desc = "scph5501.bin"
firmware0_path = "scph5501.bin"
firmware0_opt = "true"
"""

TEMPLATE_INFO = """
systemname = "Example"
firmware_count = 1
firmware0_desc = "Description of the firmware"
firmware0_path = "filename.ext"
firmware0_opt = "true/false"
"""

HASHES = json.dumps(
    {
        "_meta": {"version": "test"},
        "files": {
            "scph5501.bin": {"md5": "aaa", "sha1": "bbb", "size": 524288},
            "dc_boot.bin": {"md5": hashlib.md5(b"boot-bytes").hexdigest(), "sha1": "x", "size": 10},
        },
    }
)


@pytest.fixture
def hashes():
    return load_hashes(HASHES)


def _machine(
    files: Mapping[str, FixtureFileSpec] | None = None,
    cores: tuple[str, ...] = ("beetle_psx_libretro", "flycast_libretro"),
) -> FixtureMachine:
    """A machine with an .info set, the matching .so files, and a bios tree."""
    tree: dict[str, FixtureFileSpec] = {
        f"{INFO_DIR}/beetle_psx_libretro.info": PSX_INFO,
        f"{INFO_DIR}/flycast_libretro.info": DC_INFO,
        f"{INFO_DIR}/00_example_libretro.info": TEMPLATE_INFO,
    }
    tree.update({f"{INFO_DIR}/{core}.so": {"status": "invalid-text"} for core in cores})
    tree.update(files or {})
    return FixtureMachine(tree, dirs=[BIOS_DIR, f"{BIOS_DIR}/dc"])


def _report(machine, hashes, **kwargs):
    declarations = read_declarations(machine, INFO_DIR, core_dir=INFO_DIR)
    return resolve_firmware(machine, root=BIOS_DIR, declarations=declarations, hashes=hashes, **kwargs)


def _by_path(report) -> dict[str, FirmwareFile]:
    return {f.path: f for f in report.files}


class TestPackagedHashes:
    def test_loads_bundled_table(self):
        table = load_hashes()
        assert table.meta["version"] == "5.0.0"
        assert len(table.names()) == 388
        entry = table.get("scph5501.bin")
        assert entry is not None and entry.size == 524288

    def test_absent_identity_is_a_normal_answer(self):
        assert load_hashes().get("no-such-firmware.bin") is None

    def test_for_path_matches_a_path_keyed_entry(self):
        # Upstream keys the Dreamcast BIOS by its path, not its base name.
        entry = load_hashes().for_path("dc/dc_boot.bin")
        assert entry is not None and entry.name == "dc/dc_boot.bin"

    def test_for_path_falls_back_to_the_base_name(self):
        entry = load_hashes().for_path("some/prefix/scph5501.bin")
        assert entry is not None and entry.name == "scph5501.bin"

    def test_incomplete_entry_is_rejected(self):
        with pytest.raises(ValueError):
            load_hashes(json.dumps({"files": {"x.bin": {"md5": "a", "sha1": "b"}}}))


class TestReadDeclarations:
    def test_reads_paths_descriptions_and_opt(self):
        declarations = read_declarations(_machine(), INFO_DIR, core_dir=INFO_DIR)
        by_path = {d.path: d for d in declarations}
        assert set(by_path) == {"scph5501.bin", "psxonpsp660.bin", "dc/dc_boot.bin"}
        assert by_path["scph5501.bin"].required is True
        assert by_path["psxonpsp660.bin"].required is False
        assert by_path["dc/dc_boot.bin"].file_name == "dc_boot.bin"
        assert by_path["dc/dc_boot.bin"].platform == "dc"

    def test_template_info_files_are_dropped(self):
        declarations = read_declarations(_machine(), INFO_DIR, core_dir=INFO_DIR)
        assert not [d for d in declarations if d.path == "filename.ext"]

    def test_core_without_an_so_does_not_declare(self):
        machine = _machine(cores=("flycast_libretro",))
        declarations = read_declarations(machine, INFO_DIR, core_dir=INFO_DIR)
        assert {d.path for d in declarations} == {"dc/dc_boot.bin"}

    def test_without_a_core_dir_nothing_is_filtered(self):
        machine = _machine(cores=())
        declarations = read_declarations(machine, INFO_DIR, core_dir=None)
        assert {d.path for d in declarations} == {"scph5501.bin", "psxonpsp660.bin", "dc/dc_boot.bin"}

    def test_missing_opt_means_required(self):
        machine = FixtureMachine(
            {
                f"{INFO_DIR}/x_libretro.info": 'systemname = "Sony - PlayStation"\nfirmware0_path = "x.bin"\n',
                f"{INFO_DIR}/x_libretro.so": {"status": "invalid-text"},
            }
        )
        assert read_declarations(machine, INFO_DIR, core_dir=INFO_DIR)[0].required is True


class TestStates:
    def test_declared_and_absent_is_missing(self, hashes):
        report = _report(_machine(), hashes)
        assert _by_path(report)["scph5501.bin"].state == "missing"

    def test_present_without_a_known_hash(self, hashes):
        report = _report(_machine({f"{BIOS_DIR}/psxonpsp660.bin": "whatever"}), hashes)
        entry = _by_path(report)["psxonpsp660.bin"]
        assert (entry.state, entry.hash_known) == ("present", False)

    def test_present_stays_present_when_verification_is_not_asked_for(self, hashes):
        report = _report(_machine({f"{BIOS_DIR}/dc/dc_boot.bin": "boot-bytes"}), hashes)
        entry = _by_path(report)["dc/dc_boot.bin"]
        assert (entry.state, entry.hash_known, report.hash_checked) == ("present", True, False)

    def test_matching_identity_verifies(self, hashes):
        report = _report(_machine({f"{BIOS_DIR}/dc/dc_boot.bin": "boot-bytes"}), hashes, verify=True)
        assert _by_path(report)["dc/dc_boot.bin"].state == "verified"

    def test_wrong_bytes_at_the_right_size_mismatch(self, hashes):
        wrong = {f"{BIOS_DIR}/dc/dc_boot.bin": {"md5": "not-the-boot-md5", "size": 10}}
        report = _report(_machine(wrong), hashes, verify=True)
        assert _by_path(report)["dc/dc_boot.bin"].state == "mismatch"

    def test_wrong_size_mismatches_without_hashing(self, hashes):
        # No digest declared at all: the size pre-filter has to settle it.
        report = _report(_machine({f"{BIOS_DIR}/dc/dc_boot.bin": {"size": 11}}), hashes, verify=True)
        assert _by_path(report)["dc/dc_boot.bin"].state == "mismatch"

    def test_unreadable_present_file_is_never_assumed_verified(self, hashes):
        unreadable = {f"{BIOS_DIR}/dc/dc_boot.bin": {"status": "unreadable"}}
        report = _report(_machine(unreadable), hashes, verify=True)
        assert _by_path(report)["dc/dc_boot.bin"].state == "present"

    def test_required_is_the_or_across_declaring_cores(self, hashes):
        machine = _machine(
            {
                f"{INFO_DIR}/second_libretro.info": PSX_SECOND_INFO,
                f"{INFO_DIR}/second_libretro.so": {"status": "invalid-text"},
            }
        )
        entry = _by_path(_report(machine, hashes))["scph5501.bin"]
        assert entry.required is True
        assert entry.cores == ("beetle_psx_libretro", "second_libretro")


class TestUndeclared:
    def test_files_in_declared_directories_are_reported(self, hashes):
        stray = {f"{BIOS_DIR}/stray.bin": "x", f"{BIOS_DIR}/dc/leftover.bin": "y"}
        entries = _by_path(_report(_machine(stray), hashes))
        assert entries["stray.bin"].state == "undeclared"
        assert entries["dc/leftover.bin"].state == "undeclared"
        assert entries["stray.bin"].required is False

    def test_directories_no_declaration_references_stay_invisible(self, hashes):
        noise = {f"{BIOS_DIR}/mame2003-plus/samples/whatever.zip": "x"}
        assert "mame2003-plus/samples/whatever.zip" not in _by_path(_report(_machine(noise), hashes))

    def test_a_platform_query_does_not_report_undeclared(self, hashes):
        report = _report(_machine({f"{BIOS_DIR}/stray.bin": "x"}), hashes, platform="psx")
        assert all(f.state != "undeclared" for f in report.files)

    def test_an_undeclared_file_cannot_claim_a_core(self):
        with pytest.raises(ValueError):
            FirmwareFile(
                path="x.bin",
                file_name="x.bin",
                description="",
                required=False,
                state="undeclared",
                hash_known=False,
                cores=("some_libretro",),
            )


class TestPlatformSelection:
    def test_platform_filters_to_its_declarations(self, hashes):
        report = _report(_machine(), hashes, platform="psx")
        assert {f.path for f in report.files} == {"scph5501.bin", "psxonpsp660.bin"}

    def test_systemname_variants_map_to_one_slug(self):
        assert platform_for("scph5501.bin", "Sony - PlayStation") == "psx"
        assert platform_for("scph5501.bin", "PlayStation") == "psx"

    def test_per_file_override_beats_the_systemname(self):
        # mGBA covers three systems under one systemname; the boot ROMs do not.
        assert platform_for("gbc_bios.bin", "Game Boy/Game Boy Color/Game Boy Advance") == "gbc"

    def test_unknown_systemname_is_slugified_not_dropped(self):
        assert platform_for("x.bin", "Some New Console") == "some-new-console"
        assert platform_for("x.bin", "") == "_unknown"


class TestNothingKnownIsNotAllClear:
    """The defect this module exists to prevent: no declaration must not read as satisfied."""

    def test_unknown_platform_yields_an_empty_list_and_a_caveat(self, hashes):
        report = _report(_machine(), hashes, platform="nintendo-switch")
        assert report.files == ()
        assert [c.code for c in report.caveats] == [CAVEAT_NO_FIRMWARE_DECLARATION]
        assert report.caveats[0].data == {"platform": "nintendo-switch"}

    def test_no_declarations_at_all_still_caveats(self, hashes):
        empty = FixtureMachine({}, dirs=[INFO_DIR, BIOS_DIR])
        report = resolve_firmware(empty, root=BIOS_DIR, declarations=(), hashes=hashes)
        assert report.files == ()
        assert [c.code for c in report.caveats] == [CAVEAT_NO_FIRMWARE_DECLARATION]

    def test_no_root_means_no_answer_at_all(self, hashes):
        machine = _machine()
        declarations = read_declarations(machine, INFO_DIR, core_dir=INFO_DIR)
        report = resolve_firmware(machine, root=None, declarations=declarations, hashes=hashes)
        assert (report.root, report.files, report.hash_checked) == (None, (), False)
