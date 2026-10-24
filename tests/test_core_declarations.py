"""Tests for atlas.core_declarations — a packaged declaration in a missing ``.info``'s place.

Three things can go wrong with it, and this file holds them apart: a **table**
that states something it has no source for (the loader, which fails closed), a
**reach** wider than the one case it exists for (only a core whose ``.info`` is
missing is answered from it), and an **answer** that hides where its
declaration came from (``declaration: packaged`` and ``core-info-packaged``).
"""

from __future__ import annotations

import json
from collections.abc import Callable

import pytest

from atlas.core_declarations import (
    load_core_declarations,
    packaged_core_declarations,
    packaged_declaration_of,
)
from atlas.core_firmware import CoreFirmwareFolderRoute, lookup_core_firmware
from atlas.firmware import (
    CAVEAT_CORE_INFO_PACKAGED,
    CAVEAT_CORE_INFO_UNREADABLE,
    DECLARATION_PACKAGED,
    DECLARATION_READ,
    DECLARATION_UNREADABLE,
    DECLARED_DIRECTORY,
    NEED_REQUIRED,
    READER_PS2_BIOS_HEADER,
    SOURCE_SYSTEMNAME,
    DeclaredDirectory,
    FirmwareContext,
    FirmwareRequirement,
    declared_directory_of,
    firmware_for_core,
    load_hashes,
    read_core_declarations,
    system_assignment_caveats,
)
from atlas.machine import READ_MISSING, FixtureMachine
from atlas.system_firmware import load_system_firmware

CORES = "/cores"
INFO = "/info"
BIOS = "/bios"
PCEE2_SO = "pcee2_libretro.so"


def _doc(**overrides: object) -> str:
    entry: dict[str, object] = {
        "systemname": {"name": "Sony PlayStation 2", "citation": "somecore_libretro.info at abc1234"},
        "firmware": [
            {"path": "pcsx2/bios", "description": "the folder", "need": "required", "citation": "Libretro.cpp:1"}
        ],
        "left_out": [{"path": "pcsx2/resources", "citation": "Libretro.cpp:2"}],
        "build": {"revision": "abc1234", "stamp": "abc1234def", "citation": "the binary carries it"},
        "provenance": {"source": "someone/somecore at abc1234"},
    }
    entry.update(overrides)
    return json.dumps({"schema": 1, "cores": {"somecore": entry}})


def _context(machine: FixtureMachine) -> FirmwareContext:
    return FirmwareContext(
        root=BIOS,
        cores=read_core_declarations(machine, INFO, core_dir=CORES).cores,
        hashes=load_hashes(),
        system_firmware=load_system_firmware(),
    )


class TestTheLoaderFailsClosed:
    """A table that cannot cite what it states does not load at all."""

    def test_the_packaged_file_loads_and_looks_up_by_stem(self):
        assert [entry.key for entry in packaged_core_declarations()] == ["pcee2"]
        entry = packaged_declaration_of("pcee2_libretro")
        assert entry is not None
        assert entry.systemname == "Sony PlayStation 2"
        assert "b03bfc4" in entry.systemname_citation
        assert [(slot.path, slot.need) for slot in entry.firmware] == [("pcsx2/bios", "required")]
        assert (entry.build.revision, entry.build.stamp) == (
            "340ce04",
            "340ce04071716c52692d33194e0bbfef4514bb22",
        )
        # The short name and the .so name are not stems.
        assert packaged_declaration_of("pcee2") is None
        assert packaged_declaration_of(PCEE2_SO) is None

    def test_the_resources_folder_is_left_out_with_its_reason(self):
        # libretro-core-info's pcee2_libretro.info declares it required; this
        # build embeds what it was needed for (Libretro.cpp:495-504 at 340ce04).
        entry = packaged_declaration_of("pcee2_libretro")
        assert entry is not None
        assert [path.path for path in entry.left_out] == ["pcsx2/resources"]
        assert "pcsx2/resources" not in {slot.path for slot in entry.firmware}

    def test_a_synthetic_entry_loads(self):
        (entry,) = load_core_declarations(_doc())
        assert entry.stem == "somecore_libretro"
        assert entry.firmware[0].citation == "Libretro.cpp:1"

    def test_another_schema_is_refused(self):
        text = json.dumps({"schema": 2, "cores": {}})
        with pytest.raises(ValueError, match="unsupported schema"):
            load_core_declarations(text)

    def test_a_cores_key_that_is_not_an_object_is_refused(self):
        text = '{"schema": 1, "cores": []}'
        with pytest.raises(ValueError, match="expected a 'cores' object"):
            load_core_declarations(text)

    def test_a_stray_field_is_refused(self):
        text = _doc(note="nobody reads this")
        with pytest.raises(ValueError, match="build/firmware/left_out/provenance/systemname"):
            load_core_declarations(text)

    def test_a_blank_citation_is_refused(self):
        slot = {"path": "pcsx2/bios", "description": "d", "need": "required", "citation": "   "}
        text = _doc(firmware=[slot])
        with pytest.raises(ValueError, match="non-blank"):
            load_core_declarations(text)

    def test_a_systemname_without_its_citation_is_refused(self):
        text = _doc(systemname={"name": "Sony PlayStation 2"})
        with pytest.raises(ValueError, match="systemname: expected exactly citation/name"):
            load_core_declarations(text)

    def test_a_need_outside_the_pair_is_refused(self):
        slot = {"path": "pcsx2/bios", "description": "d", "need": "sometimes", "citation": "c"}
        text = _doc(firmware=[slot])
        with pytest.raises(ValueError, match="need must be one of"):
            load_core_declarations(text)

    def test_an_entry_declaring_nothing_is_refused(self):
        text = _doc(firmware=[])
        with pytest.raises(ValueError, match="non-empty list"):
            load_core_declarations(text)

    @pytest.mark.parametrize("path", ["/abs/bios", "pcsx2/bios/", "pcsx2/../bios"])
    def test_a_path_that_is_not_relative_to_the_system_directory_is_refused(self, path: str):
        slot = {"path": path, "description": "d", "need": "required", "citation": "c"}
        text = _doc(firmware=[slot])
        with pytest.raises(ValueError, match="not a path relative to the system directory"):
            load_core_declarations(text)

    def test_one_path_declared_twice_is_refused(self):
        slot = {"path": "pcsx2/bios", "description": "d", "need": "required", "citation": "c"}
        text = _doc(firmware=[slot, slot])
        with pytest.raises(ValueError, match="one path twice"):
            load_core_declarations(text)

    def test_a_path_declared_and_left_out_is_refused(self):
        text = _doc(left_out=[{"path": "pcsx2/bios", "citation": "c"}])
        with pytest.raises(ValueError, match="declared and left out"):
            load_core_declarations(text)

    def test_a_stamp_that_is_not_the_revision_is_refused(self):
        build = {"revision": "abc1234", "stamp": "deadbeef", "citation": "c"}
        text = _doc(build=build)
        with pytest.raises(ValueError, match="spell one commit"):
            load_core_declarations(text)


class TestTheEntryAgreesWithTheKnowledgeBesideIt:
    """The declaration, the folder row and the locating entry are three readings of one build."""

    def test_the_folder_the_entry_declares_has_its_own_row(self):
        row = declared_directory_of("pcee2_libretro", "pcsx2/bios")
        assert row is not None
        assert row.reader == READER_PS2_BIOS_HEADER
        assert (row.min_size, row.max_size) == (None, None)
        assert (row.option_key, row.option_lists, row.offered_by_listing) == ("pcsx2_bios", "auto", True)

    def test_the_row_accepts_any_size_it_can_stat(self):
        row = declared_directory_of("pcee2_libretro", "pcsx2/bios")
        assert row is not None
        assert row.accepts_size(1) is True
        assert row.accepts_size(2 * 1024 * 1024) is True
        assert row.accepts_size(64 * 1024 * 1024) is True
        assert row.accepts_size(None) is False

    def test_the_locating_entry_points_at_that_folder_and_pins_the_same_build(self):
        card = lookup_core_firmware(PCEE2_SO)
        entry = packaged_declaration_of("pcee2_libretro")
        assert card is not None
        assert entry is not None
        assert isinstance(card.name_route, CoreFirmwareFolderRoute)
        assert card.name_route.declared_directory in {slot.path for slot in entry.firmware}
        assert card.content_route is None
        assert (card.build.revision, card.build.stamp) == (entry.build.revision, entry.build.stamp)


def _row(
    *,
    min_size: int | None = None,
    max_size: int | None = None,
    option_lists: str | None = None,
    own_check: str = "c1",
    listing_test: str = "c2",
    offers: str = "c3",
    lists_when: str = "c4",
) -> DeclaredDirectory:
    return DeclaredDirectory(
        identities="x/",
        min_size=min_size,
        max_size=max_size,
        reader=READER_PS2_BIOS_HEADER,
        own_check=own_check,
        listing_test=listing_test,
        offers=offers,
        lists_when=lists_when,
        option_lists=option_lists,
    )


class TestARowGuardsItsOwnShape:
    def test_half_a_size_filter_is_refused(self):
        with pytest.raises(ValueError, match="both bounds or neither"):
            _row(min_size=1)

    def test_a_listing_value_without_its_option_is_refused(self):
        with pytest.raises(ValueError, match="no option is named"):
            _row(option_lists="auto")

    @pytest.mark.parametrize(
        ("field", "build"),
        [
            ("own_check", lambda: _row(own_check="  ")),
            ("listing_test", lambda: _row(listing_test="  ")),
            ("offers", lambda: _row(offers="  ")),
            ("lists_when", lambda: _row(lists_when="  ")),
        ],
    )
    def test_a_blank_row_citation_is_refused(self, field: str, build: Callable[[], DeclaredDirectory]):
        with pytest.raises(ValueError, match=f"{field} cites this core's own source"):
            build()

    def test_every_row_quotes_its_own_core(self):
        # The citations are per row: PCEE2's caveats never quote LRPS2's main.cpp.
        pcee2 = declared_directory_of("pcee2_libretro", "pcsx2/bios")
        assert pcee2 is not None
        cited = (pcee2.own_check, pcee2.listing_test, pcee2.offers, pcee2.lists_when)
        assert not any("main.cpp" in text or "14d19f8" in text for text in cited)


class TestOnlyAMissingInfoIsAnsweredFromThePackage:
    """The entry stands in the .info's place, never beside it or in a broken one's."""

    def test_a_missing_info_is_declared_by_the_package(self):
        machine = FixtureMachine({f"{CORES}/{PCEE2_SO}": {"status": "invalid-text"}}, dirs=[INFO])
        (core,) = read_core_declarations(machine, INFO, core_dir=CORES).cores
        assert core.packaged is True
        assert core.info_status == READ_MISSING
        assert core.system == "ps2"
        (slot,) = core.firmware
        assert (slot.path, slot.need, slot.system, slot.system_source, slot.declared_kind) == (
            "pcsx2/bios",
            NEED_REQUIRED,
            "ps2",
            SOURCE_SYSTEMNAME,
            DECLARED_DIRECTORY,
        )
        # One systemname and no database: a single-system core, nothing to caveat.
        assert core.systemname == "Sony PlayStation 2"
        assert system_assignment_caveats(core) == ()

    def test_an_info_that_is_there_is_read_as_it_stands(self):
        machine = FixtureMachine(
            {
                f"{CORES}/{PCEE2_SO}": {"status": "invalid-text"},
                f"{INFO}/pcee2_libretro.info": 'systemname = "Sony PlayStation 2"\nfirmware_count = 0\n',
            }
        )
        (core,) = read_core_declarations(machine, INFO, core_dir=CORES).cores
        assert core.packaged is False
        assert core.firmware == ()
        answer = firmware_for_core(machine, _context(machine), core_so=PCEE2_SO)
        assert answer.cores[0].declaration == DECLARATION_READ

    def test_an_info_that_is_there_and_unreadable_stays_unreadable(self):
        machine = FixtureMachine(
            {
                f"{CORES}/{PCEE2_SO}": {"status": "invalid-text"},
                f"{INFO}/pcee2_libretro.info": {"status": "unreadable"},
            }
        )
        answer = firmware_for_core(machine, _context(machine), core_so=PCEE2_SO)
        (core,) = answer.cores
        assert core.declaration == DECLARATION_UNREADABLE
        assert [caveat.code for caveat in core.caveats] == [CAVEAT_CORE_INFO_UNREADABLE]

    def test_a_core_with_no_entry_keeps_its_unreadable_answer(self):
        machine = FixtureMachine({f"{CORES}/mesen_libretro.so": {"status": "invalid-text"}}, dirs=[INFO])
        (core,) = read_core_declarations(machine, INFO, core_dir=CORES).cores
        assert core.packaged is False
        answer = firmware_for_core(machine, _context(machine), core_so="mesen_libretro.so")
        assert answer.cores[0].declaration == DECLARATION_UNREADABLE


class TestThePackagedAnswerSaysSo:
    def test_the_declaration_is_packaged_and_its_provenance_leads(self):
        machine = FixtureMachine(
            {f"{CORES}/{PCEE2_SO}": {"status": "invalid-text"}}, dirs=[INFO, f"{BIOS}/pcsx2/bios"]
        )
        (core,) = firmware_for_core(machine, _context(machine), core_so=PCEE2_SO).cores
        assert core.declaration == DECLARATION_PACKAGED
        assert core.caveats[0].code == CAVEAT_CORE_INFO_PACKAGED
        assert dict(core.caveats[0].data) == {"core_so": PCEE2_SO}
        (requirement,) = core.requirements
        assert isinstance(requirement, FirmwareRequirement)
        assert requirement.declared == "pcsx2/bios"
