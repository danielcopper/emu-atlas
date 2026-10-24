"""Tests for atlas.psx_region — each emulator's own region rule, over identities built by hand.

The three rules are ported one by one, so each is held to the place it parts
from the others: DuckStation's licence-first order, executable check and
starts-with prefixes; SwanStation's exact leading letters; Beetle PSX's
SYSTEM.CNF-first order, four-character grammar and licence words. The playlist
readers are held to their own quirks the same way, and the whole route — which
image, whether the emulator opens it, whether atlas reads it — is asked over a
fixture machine.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import pytest

from atlas import psx_disc, psx_region
from atlas.machine import FixtureMachine, RealMachine
from atlas.placement import (
    REASON_CONTENT_UNOPENABLE,
    REASON_DISC_FORMAT_UNREAD,
    REASON_DISC_UNREAD,
)
from atlas.psx_region import (
    DISC_UNREAD_BOOT_LINE_ABSENT,
    DISC_UNREAD_CODEC_UNAVAILABLE,
    DISC_UNREAD_MISSING,
    DISC_UNREAD_NOT_A_DISC,
    DISC_UNREAD_SECTOR_INVALID,
    DISC_UNREAD_UNREADABLE,
    EMULATOR_BEETLE,
    EMULATOR_DUCKSTATION,
    EMULATOR_SWANSTATION,
    REGION_SOURCE_EMULATOR_DEFAULT,
    REGION_SOURCE_LICENSE,
    REGION_SOURCE_SERIAL,
    DiscRegionReading,
    decided,
)

US = b"          Licensed  by          Sony Computer Entertainment Amer  ica "
JP = b"          Licensed  by          Sony Computer Entertainment Inc."
EU = b"          Licensed  by          Sony Computer Entertainment Euro pe"


def identity(
    license: bytes = b"", boot: str | None = None, *, found: bool = True, edc: bool = True, cnf: bytes | None = None
) -> psx_disc.DiscIdentity:
    text = cnf if cnf is not None else (None if boot is None else f"BOOT = {boot}\r\nTCB = 4\r\n".encode("latin-1"))
    return psx_disc.DiscIdentity(
        license=license.ljust(psx_disc.LICENSE_LENGTH, b"\x00"),
        system_cnf=text,
        system_cnf_sector=None if text is None else text.ljust(psx_disc.USER_DATA_SIZE, b"\x00"),
        boot_found=found,
        edc_valid=edc,
    )


NTSC_U = psx_region.REGION_NTSC_U
NTSC_J = psx_region.REGION_NTSC_J
PAL = psx_region.REGION_PAL


class TestDuckStation:
    @pytest.mark.parametrize(("license", "region"), [(US, NTSC_U), (JP, NTSC_J), (EU, PAL)])
    def test_a_licence_string_decides_first(self, license, region):
        assert psx_region.duckstation_region(identity(license, "cdrom:\\SLES_000.01;1")) == decided(
            region, REGION_SOURCE_LICENSE
        )

    def test_a_licence_string_is_compared_from_the_sector_s_first_byte(self):
        shifted = b" " + US
        assert psx_region.duckstation_region(identity(shifted)).source == REGION_SOURCE_EMULATOR_DEFAULT

    def test_the_serial_needs_its_executable(self):
        reading = psx_region.duckstation_region(identity(b"", "cdrom:\\SLES_000.01;1", found=False))
        assert reading == decided(NTSC_U, REGION_SOURCE_EMULATOR_DEFAULT)

    @pytest.mark.parametrize(
        ("boot", "region"),
        [
            ("cdrom:\\SLES_000.01;1", PAL),
            ("cdrom:\\SLESX_123.45;1", PAL),
            ("cdrom:\\slps_000.01;1", NTSC_J),
            ("cdrom:\\PAPX_900.01;1", NTSC_J),
            ("cdrom:\\DIR\\SCUS_944.53;1", NTSC_U),
        ],
    )
    def test_a_serial_prefix_matches_by_starts_with_without_case(self, boot, region):
        assert psx_region.duckstation_region(identity(b"", boot)) == decided(region, REGION_SOURCE_SERIAL)

    def test_a_serial_no_prefix_starts_boots_the_default(self):
        assert psx_region.duckstation_region(identity(b"", "cdrom:\\HELLO.EXE;1")) == decided(
            NTSC_U, REGION_SOURCE_EMULATOR_DEFAULT
        )


class TestSwanStation:
    def test_a_licence_string_decides_first(self):
        assert psx_region.swanstation_region(identity(EU, "cdrom:\\SLUS_000.01;1")) == decided(
            PAL, REGION_SOURCE_LICENSE
        )

    def test_the_serial_needs_no_executable(self):
        reading = psx_region.swanstation_region(identity(b"", "cdrom:\\SLES_000.01;1", found=False))
        assert reading == decided(PAL, REGION_SOURCE_SERIAL)

    def test_the_leading_letters_must_match_exactly(self):
        reading = psx_region.swanstation_region(identity(b"", "cdrom:\\SLESX_123.45;1"))
        assert reading == decided(NTSC_U, REGION_SOURCE_EMULATOR_DEFAULT)

    def test_a_value_without_a_backslash_is_cut_at_its_colon(self):
        assert psx_region.swanstation_region(identity(b"", "cdrom:SCPS_100.01;1")) == decided(
            NTSC_J, REGION_SOURCE_SERIAL
        )

    def test_no_system_cnf_boots_the_default(self):
        assert psx_region.swanstation_region(identity()) == decided(NTSC_U, REGION_SOURCE_EMULATOR_DEFAULT)


class TestBeetle:
    def test_system_cnf_decides_before_the_licence(self):
        assert psx_region.beetle_region(identity(EU, "cdrom:\\SLUS_000.01;1")) == decided(
            NTSC_U, REGION_SOURCE_SERIAL
        )

    @pytest.mark.parametrize(
        ("boot", "region"),
        [
            ("cdrom:\\SCES_000.01;1", PAL),
            ("cdrom:\\SLUS_000.01;1", NTSC_U),
            ("CDROM:\\SIPS_600.01;1", NTSC_J),
            ("cdrom:\\SCKA_200.01;1", NTSC_J),
            ("cdrom:\\SLBM_000.01;1", NTSC_J),
        ],
    )
    def test_the_four_character_grammar(self, boot, region):
        assert psx_region.beetle_region(identity(b"", boot)) == decided(region, REGION_SOURCE_SERIAL)

    @pytest.mark.parametrize(
        "boot",
        ["cdrom:SCES_000.01;1", "cdrom:\\DIR\\SLUS_000.01;1", "cdrom:\\slus_000.01;1", "cdrom:\\PAPX_900.01;1"],
        ids=["no-backslash", "subdirectory", "lower-case", "not-s-c-l-i"],
    )
    def test_an_id_outside_the_grammar_falls_to_the_licence(self, boot):
        assert psx_region.beetle_region(identity(JP, boot)) == decided(NTSC_J, REGION_SOURCE_LICENSE)

    def test_the_licence_is_searched_for_words(self):
        run_together = b"          Licensed  by          Sony Computer Entertainment Europe"
        assert psx_region.beetle_region(identity(run_together)) == decided(PAL, REGION_SOURCE_LICENSE)
        assert psx_region.duckstation_region(identity(run_together)).source == REGION_SOURCE_EMULATOR_DEFAULT

    def test_a_licence_without_a_region_word_boots_the_default(self):
        assert psx_region.beetle_region(identity(b"Licensed by somebody")) == decided(
            NTSC_U, REGION_SOURCE_EMULATOR_DEFAULT
        )

    def test_a_failing_checksum_decides_nothing(self):
        reading = psx_region.beetle_region(identity(US, edc=False))
        assert (reading.reason, reading.status) == (REASON_DISC_UNREAD, DISC_UNREAD_SECTOR_INVALID)

    def test_a_system_cnf_without_boot_decides_nothing(self):
        reading = psx_region.beetle_region(identity(US, cnf=b"TCB = 4\r\n"))
        assert (reading.reason, reading.status) == (REASON_DISC_UNREAD, DISC_UNREAD_BOOT_LINE_ABSENT)

    def test_a_boot_without_its_equals_sign_falls_to_the_licence(self):
        reading = psx_region.beetle_region(identity(US, cnf=b"BOOT2 = cdrom0:\\SLUS_200.00;1\r\n"))
        assert reading == decided(NTSC_U, REGION_SOURCE_LICENSE)


class TestTheReadingHoldsItsShape:
    def test_a_region_and_a_reason_are_never_both_stated(self):
        with pytest.raises(ValueError, match="exactly one"):
            DiscRegionReading(region=NTSC_U, source=REGION_SOURCE_LICENSE, reason=REASON_DISC_UNREAD)
        with pytest.raises(ValueError, match="exactly one"):
            DiscRegionReading()

    def test_words_outside_the_vocabularies_are_refused(self):
        with pytest.raises(ValueError, match="region must be one of"):
            DiscRegionReading(region="ntsc", source=REGION_SOURCE_LICENSE)  # type: ignore[arg-type]
        with pytest.raises(ValueError, match="status must be one of"):
            DiscRegionReading(reason=REASON_DISC_UNREAD, status="gone")  # type: ignore[arg-type]


class TestPlaylists:
    def test_duckstation_and_swanstation_trim_skip_comments_and_one_character_lines(self):
        text = "  # a comment\n\n x\n  Disc 1.cue  \r\n/abs/Disc 2.cue\n"
        assert psx_region.duckstation_playlist_entries(text, "/roms/Game.m3u") == [
            "/roms/Disc 1.cue",
            "/abs/Disc 2.cue",
        ]

    def test_beetle_keeps_leading_space_and_skips_only_a_first_column_hash(self):
        machine = FixtureMachine({"/roms/Game.m3u": "  Disc 1.cue\n #x.cue\n#skipped\n"})
        assert psx_region.beetle_playlist_entries(machine, "/roms/Game.m3u") == ["/roms/  Disc 1.cue", "/roms/ #x.cue"]

    def test_beetle_reads_at_most_2047_bytes_as_one_line(self):
        long = "a" * 2050 + ".cue\n"
        machine = FixtureMachine({"/roms/Game.m3u": long})
        entries = psx_region.beetle_playlist_entries(machine, "/roms/Game.m3u")
        assert entries == ["/roms/" + "a" * 2047, "/roms/aaa.cue"]

    def test_beetle_follows_a_nested_playlist_in_place(self):
        machine = FixtureMachine({"/roms/Game.m3u": "inner.m3u\nlast.cue\n", "/roms/inner.m3u": "first.cue\n"})
        assert psx_region.beetle_playlist_entries(machine, "/roms/Game.m3u") == ["/roms/first.cue", "/roms/last.cue"]

    def test_beetle_stops_reading_a_playlist_that_names_itself(self):
        machine = FixtureMachine({"/roms/Game.m3u": "a.cue\nGame.m3u\nb.cue\n"})
        assert psx_region.beetle_playlist_entries(machine, "/roms/Game.m3u") == ["/roms/a.cue"]

    def test_beetle_skips_a_nested_playlist_that_will_not_open(self):
        machine = FixtureMachine({"/roms/Game.m3u": "gone.m3u\na.cue\n"})
        assert psx_region.beetle_playlist_entries(machine, "/roms/Game.m3u") == ["/roms/a.cue"]

    def test_a_cycle_through_another_playlist_is_content_beetle_cannot_open(self):
        machine = FixtureMachine({"/roms/A.m3u": "B.m3u\n", "/roms/B.m3u": "A.m3u\n"})
        reading = psx_region.read_disc_region(machine, EMULATOR_BEETLE, "/roms/A.m3u")
        assert reading.reason == REASON_CONTENT_UNOPENABLE


GAME = "/roms/Game.cue"
FACTS = {"license": US.decode("latin-1"), "system_cnf": None, "boot_found": False, "edc_valid": True}


def machine(files: Mapping[str, Any], identities: Mapping[str, Any] | None = None) -> FixtureMachine:
    return FixtureMachine(files, disc_identities=identities or {})


class TestOneContentPath:
    def test_a_cue_sheet_is_read_and_decided(self):
        reading = psx_region.read_disc_region(machine({GAME: "x"}, {GAME: FACTS}), EMULATOR_SWANSTATION, GAME)
        assert reading == decided(NTSC_U, REGION_SOURCE_LICENSE)

    def test_a_playlist_decides_from_its_first_entry(self):
        files = {"/roms/Game.m3u": "Game.cue\nOther.cue\n", GAME: "x", "/roms/Other.cue": "x"}
        identities = {GAME: FACTS, "/roms/Other.cue": {**FACTS, "license": EU.decode("latin-1")}}
        reading = psx_region.read_disc_region(machine(files, identities), EMULATOR_DUCKSTATION, "/roms/Game.m3u")
        assert reading.region == NTSC_U

    def test_a_playlist_s_suffix_is_matched_without_case(self):
        files = {"/roms/Game.M3U": "Game.cue\n", GAME: "x"}
        reading = psx_region.read_disc_region(machine(files, {GAME: FACTS}), EMULATOR_SWANSTATION, "/roms/Game.M3U")
        assert reading.region == NTSC_U

    def test_an_empty_playlist_is_content_no_emulator_opens(self):
        reading = psx_region.read_disc_region(machine({"/roms/Game.m3u": "\n"}), EMULATOR_SWANSTATION, "/roms/Game.m3u")
        assert reading == DiscRegionReading(reason=REASON_CONTENT_UNOPENABLE, disc="/roms/Game.m3u", extension="m3u")

    def test_a_playlist_that_is_not_there_or_not_text(self):
        absent = psx_region.read_disc_region(machine({}), EMULATOR_DUCKSTATION, "/roms/Game.m3u")
        assert (absent.reason, absent.status) == (REASON_DISC_UNREAD, DISC_UNREAD_MISSING)
        locked = machine({"/roms/Game.m3u": {"status": "unreadable"}})
        unread = psx_region.read_disc_region(locked, EMULATOR_DUCKSTATION, "/roms/Game.m3u")
        assert (unread.reason, unread.status) == (REASON_DISC_UNREAD, DISC_UNREAD_UNREADABLE)

    @pytest.mark.parametrize("emulator", [EMULATOR_DUCKSTATION, EMULATOR_BEETLE])
    def test_a_playlist_that_is_not_there_is_missing_for_every_reader(self, emulator):
        reading = psx_region.read_disc_region(machine({}), emulator, "/roms/Game.m3u")
        assert reading == DiscRegionReading(reason=REASON_DISC_UNREAD, disc="/roms/Game.m3u", status=DISC_UNREAD_MISSING)

    def test_a_cycle_of_playlists_is_content_duckstation_cannot_open(self):
        files = {"/roms/A.m3u": "B.m3u\n", "/roms/B.m3u": "A.m3u\n"}
        reading = psx_region.read_disc_region(machine(files), EMULATOR_DUCKSTATION, "/roms/A.m3u")
        assert reading == DiscRegionReading(reason=REASON_CONTENT_UNOPENABLE, disc="/roms/A.m3u", extension="m3u")

    def test_beetle_ends_a_line_at_newline_alone(self):
        machine = FixtureMachine({"/roms/Game.m3u": "a.cue\rb.cue\nc.cue\n"})
        assert psx_region.beetle_playlist_entries(machine, "/roms/Game.m3u") == ["/roms/a.cue\rb.cue", "/roms/c.cue"]

    @pytest.mark.parametrize("emulator", [EMULATOR_DUCKSTATION, EMULATOR_BEETLE])
    def test_a_playlist_on_disk_is_read_without_newline_translation(self, tmp_path, emulator):
        # A lone carriage return ends no line for either reader, so the first
        # entry is the whole first line — a file that is not there.
        (tmp_path / "a.cue").write_text("NONSENSE\n")
        playlist = tmp_path / "Game.m3u"
        playlist.write_bytes(b"a.cue\rb.cue\n")
        reading = psx_region.read_disc_region(RealMachine(), emulator, str(playlist))
        assert reading == DiscRegionReading(
            reason=REASON_DISC_UNREAD, disc=str(tmp_path / "a.cue\rb.cue"), status=DISC_UNREAD_MISSING
        )

    def test_a_nested_playlist_is_followed_by_duckstation(self):
        files = {"/roms/Game.m3u": "Inner.m3u\n", "/roms/Inner.m3u": "Game.cue\n", GAME: "x"}
        reading = psx_region.read_disc_region(machine(files, {GAME: FACTS}), EMULATOR_DUCKSTATION, "/roms/Game.m3u")
        assert reading.region == NTSC_U

    @pytest.mark.parametrize("emulator", [EMULATOR_DUCKSTATION, EMULATOR_SWANSTATION])
    def test_a_boot_file_is_a_format_atlas_does_not_read(self, emulator):
        reading = psx_region.read_disc_region(machine({"/roms/Game.exe": "x"}), emulator, "/roms/Game.exe")
        assert (reading.reason, reading.extension) == (REASON_DISC_FORMAT_UNREAD, "exe")

    @pytest.mark.parametrize("suffix", ["ecm", "mds"])
    def test_an_image_the_emulator_opens_and_atlas_does_not_read(self, suffix):
        path = f"/roms/Game.{suffix}"
        reading = psx_region.read_disc_region(machine({path: "x"}), EMULATOR_SWANSTATION, path)
        assert reading == DiscRegionReading(reason=REASON_DISC_FORMAT_UNREAD, disc=path, extension=suffix)

    @pytest.mark.parametrize("suffix", ["bin", "iso", "img"])
    def test_content_beetle_does_not_open(self, suffix):
        path = f"/roms/Game.{suffix}"
        reading = psx_region.read_disc_region(machine({path: "x"}), EMULATOR_BEETLE, path)
        assert reading == DiscRegionReading(reason=REASON_CONTENT_UNOPENABLE, disc=path, extension=suffix)

    def test_a_suffix_duckstation_opens_none_of(self):
        reading = psx_region.read_disc_region(machine({"/roms/Game.zip": "x"}), EMULATOR_DUCKSTATION, "/roms/Game.zip")
        assert reading.reason == REASON_CONTENT_UNOPENABLE

    def test_a_disc_that_is_not_a_disc(self):
        reading = psx_region.read_disc_region(machine({GAME: "x"}, {GAME: "not-a-disc"}), EMULATOR_SWANSTATION, GAME)
        assert reading == DiscRegionReading(reason=REASON_DISC_UNREAD, disc=GAME, status=DISC_UNREAD_NOT_A_DISC)

    def test_a_rule_that_decides_nothing_names_the_disc(self):
        identities = {GAME: {**FACTS, "edc_valid": False}}
        reading = psx_region.read_disc_region(machine({GAME: "x"}, identities), EMULATOR_BEETLE, GAME)
        assert reading == DiscRegionReading(reason=REASON_DISC_UNREAD, disc=GAME, status=DISC_UNREAD_SECTOR_INVALID)


CHD = "/roms/Game.chd"


def chd(codecs: list[str], *, parent: bool = False, read: Any = None) -> FixtureMachine:
    spec = {"codecs": codecs, "requires_parent": parent, "identity": FACTS if read is None else read}
    return machine({CHD: "x"}, {CHD: spec})


class TestAChd:
    """D3, D4, D17 and D18 of #661: a CHD read, refused by the emulator's own open, or not read by this runtime."""

    @pytest.mark.parametrize("emulator", [EMULATOR_DUCKSTATION, EMULATOR_SWANSTATION, EMULATOR_BEETLE])
    def test_a_chd_every_emulator_opens_is_read_and_decided(self, emulator):
        reading = psx_region.read_disc_region(chd(["cdlz", "cdzl", "cdfl"]), emulator, CHD)
        assert reading == decided(NTSC_U, REGION_SOURCE_LICENSE)

    def test_swanstation_does_not_open_a_cdzs_chd(self):
        unopenable = DiscRegionReading(reason=REASON_CONTENT_UNOPENABLE, disc=CHD, extension="chd")
        assert psx_region.read_disc_region(chd(["cdzs"]), EMULATOR_SWANSTATION, CHD) == unopenable
        # whether or not this runtime could have read it
        assert psx_region.read_disc_region(chd(["cdzs"], read="codec-unavailable"), EMULATOR_SWANSTATION, CHD) == (
            unopenable
        )

    @pytest.mark.parametrize("emulator", [EMULATOR_DUCKSTATION, EMULATOR_BEETLE])
    def test_duckstation_and_beetle_open_a_cdzs_chd(self, emulator):
        assert psx_region.read_disc_region(chd(["cdzs"]), emulator, CHD).region == NTSC_U

    @pytest.mark.parametrize("emulator", [EMULATOR_DUCKSTATION, EMULATOR_BEETLE])
    def test_a_codec_this_runtime_lacks(self, emulator):
        reading = psx_region.read_disc_region(chd(["cdzs"], read="codec-unavailable"), emulator, CHD)
        assert reading == DiscRegionReading(reason=REASON_DISC_UNREAD, disc=CHD, status=DISC_UNREAD_CODEC_UNAVAILABLE)

    @pytest.mark.parametrize("emulator", [EMULATOR_SWANSTATION, EMULATOR_BEETLE])
    @pytest.mark.parametrize("read", [None, "parent-not-found"], ids=["parent-found", "parent-not-found"])
    def test_swanstation_and_beetle_open_no_chd_that_names_a_parent(self, emulator, read):
        reading = psx_region.read_disc_region(chd(["cdlz"], parent=True, read=read), emulator, CHD)
        assert reading == DiscRegionReading(reason=REASON_CONTENT_UNOPENABLE, disc=CHD, extension="chd")

    def test_duckstation_reads_through_a_parent_it_finds(self):
        reading = psx_region.read_disc_region(chd(["cdlz"], parent=True), EMULATOR_DUCKSTATION, CHD)
        assert reading == decided(NTSC_U, REGION_SOURCE_LICENSE)

    def test_duckstation_does_not_open_a_chd_whose_parent_it_does_not_find(self):
        reading = psx_region.read_disc_region(
            chd(["cdlz"], parent=True, read="parent-not-found"), EMULATOR_DUCKSTATION, CHD
        )
        assert reading == DiscRegionReading(reason=REASON_CONTENT_UNOPENABLE, disc=CHD, extension="chd")

    @pytest.mark.parametrize("emulator", [EMULATOR_DUCKSTATION, EMULATOR_SWANSTATION, EMULATOR_BEETLE])
    def test_a_codec_no_emulator_carries(self, emulator):
        reading = psx_region.read_disc_region(chd(["cdxx"]), emulator, CHD)
        assert reading.reason == REASON_CONTENT_UNOPENABLE

    def test_a_hunk_in_a_codec_atlas_does_not_read(self):
        reading = psx_region.read_disc_region(chd(["cdfl"], read="format-not-read"), EMULATOR_BEETLE, CHD)
        assert reading == DiscRegionReading(reason=REASON_DISC_FORMAT_UNREAD, disc=CHD, extension="chd")

    def test_a_chd_named_by_a_playlist(self):
        files = {"/roms/Game.m3u": "Game.chd\n", CHD: "x"}
        spec = {"codecs": ["cdzs"], "requires_parent": False, "identity": FACTS}
        reading = psx_region.read_disc_region(machine(files, {CHD: spec}), EMULATOR_SWANSTATION, "/roms/Game.m3u")
        assert reading == DiscRegionReading(reason=REASON_CONTENT_UNOPENABLE, disc=CHD, extension="chd")


class TestDuckStationSettings:
    @pytest.mark.parametrize(
        ("value", "region"),
        [("PAL", PAL), ("ntsc-j", NTSC_J), (" NTSC-U ", NTSC_U), ("Auto", None), ("Europe", None), (None, None)],
    )
    def test_the_console_region_setting(self, value, region):
        assert psx_region.duckstation_console_region(value) == region

    @pytest.mark.parametrize(
        ("value", "region"),
        [("PAL", PAL), ("NTSC-J", NTSC_J), ("Other", NTSC_U), ("Non-PS1", NTSC_U), ("Auto", None), (None, None)],
    )
    def test_the_custom_property_vocabulary(self, value, region):
        assert psx_region.duckstation_custom_region(value) == region

    def test_a_section_name_ends_at_its_first_bracket(self):
        text = "[/roms/a]b.cue]\nRegion = PAL\n[/roms/x.cue\nRegion = NTSC-J\n"
        assert psx_region.custom_property(text, "/roms/a]b.cue", "Region") is None
        assert psx_region.custom_property(text, "/roms/a", "Region") == "NTSC-J"

    def test_a_property_is_found_without_case_and_the_last_entry_wins(self):
        text = "[/roms/Game%20.cue]\nRegion = PAL\n[/ROMS/game.cue]\nregion = NTSC-J\nRegion = NTSC-U\n"
        assert psx_region.custom_property(text, "/roms/Game.cue", "Region") == "NTSC-U"
        assert psx_region.custom_property(text, "/roms/Game%20.cue", "Region") == "PAL"
        assert psx_region.custom_property(text, "/roms/Other.cue", "Region") is None


PBP = "/roms/Game.pbp"


def pbp(*, refuses: bool = False, encrypted: bool = False, read: Any = None) -> FixtureMachine:
    spec = {"duckstation_refuses": refuses, "encrypted": encrypted, "identity": FACTS if read is None else read}
    return machine({PBP: "x"}, {PBP: spec})


class TestAPbp:
    """D1 of #691: a PBP read by each emulator's reader, refused by DuckStation's and SwanStation's, or encrypted."""

    @pytest.mark.parametrize("emulator", [EMULATOR_DUCKSTATION, EMULATOR_SWANSTATION, EMULATOR_BEETLE])
    def test_a_pbp_every_reader_opens_is_read_and_decided(self, emulator):
        assert psx_region.read_disc_region(pbp(), emulator, PBP) == decided(NTSC_U, REGION_SOURCE_LICENSE)

    @pytest.mark.parametrize("emulator", [EMULATOR_DUCKSTATION, EMULATOR_SWANSTATION])
    def test_duckstation_and_swanstation_do_not_open_one_they_refuse(self, emulator):
        reading = psx_region.read_disc_region(pbp(refuses=True), emulator, PBP)
        assert reading == DiscRegionReading(reason=REASON_CONTENT_UNOPENABLE, disc=PBP, extension="pbp")

    def test_beetle_reads_one_duckstation_refuses(self):
        assert psx_region.read_disc_region(pbp(refuses=True), EMULATOR_BEETLE, PBP).region == NTSC_U

    def test_beetle_decrypts_an_encrypted_one_which_atlas_does_not_read(self):
        # Whatever the other readers found: Beetle decrypts it.
        reading = psx_region.read_disc_region(pbp(encrypted=True), EMULATOR_BEETLE, PBP)
        assert reading == DiscRegionReading(reason=REASON_DISC_FORMAT_UNREAD, disc=PBP, extension="pbp")

    @pytest.mark.parametrize("emulator", [EMULATOR_DUCKSTATION, EMULATOR_SWANSTATION])
    def test_duckstation_and_swanstation_refuse_an_encrypted_one(self, emulator):
        reading = psx_region.read_disc_region(pbp(refuses=True, encrypted=True, read="format-not-read"), emulator, PBP)
        assert reading.reason == REASON_CONTENT_UNOPENABLE

    def test_a_disc_structure_the_readers_refuse(self):
        reading = psx_region.read_disc_region(pbp(read="not-a-disc"), EMULATOR_SWANSTATION, PBP)
        assert reading == DiscRegionReading(reason=REASON_DISC_UNREAD, disc=PBP, status=DISC_UNREAD_NOT_A_DISC)

    def test_a_pbp_named_by_a_playlist(self):
        files = {"/roms/Game.m3u": "Game.pbp\n", PBP: "x"}
        spec = {"duckstation_refuses": True, "encrypted": False, "identity": FACTS}
        reading = psx_region.read_disc_region(machine(files, {PBP: spec}), EMULATOR_SWANSTATION, "/roms/Game.m3u")
        assert reading == DiscRegionReading(reason=REASON_CONTENT_UNOPENABLE, disc=PBP, extension="pbp")


SWAN_CLAIMS = frozenset({"exe", "psexe", "cue", "bin", "img", "iso", "chd", "pbp", "ecm", "mds", "psf", "m3u"})
BEETLE_CLAIMS = frozenset({"exe", "cue", "toc", "ccd", "m3u", "pbp", "chd"})
ZIP = "/roms/Game.zip"


class TestTheEntryRetroArchExtracts:
    """extracted_entry: the claimed entries, sorted by strcasecmp, the first written (D2 of #691)."""

    def test_the_first_claimed_entry_by_name(self):
        assert psx_region.extracted_entry(["readme.txt", "Game.cue", "Game.bin"], SWAN_CLAIMS) == "Game.bin"
        assert psx_region.extracted_entry(["readme.txt", "Game.cue", "Game.bin"], BEETLE_CLAIMS) == "Game.cue"

    def test_the_sort_ignores_case_and_reads_the_whole_name(self):
        # strcasecmp over "Game (Track 01).bin" and "Game.cue": a space sorts before a dot.
        assert psx_region.extracted_entry(["Game.cue", "Game (Track 01).bin"], SWAN_CLAIMS) == "Game (Track 01).bin"
        # A folder is part of the name: "b/a.chd" sorts after "A.CHD" and before "Game.cue".
        assert psx_region.extracted_entry(["Game.cue", "b/a.chd", "A.CHD"], SWAN_CLAIMS) == "A.CHD"
        assert psx_region.extracted_entry(["Game.cue", "b/a.chd"], SWAN_CLAIMS) == "b/a.chd"

    def test_an_extension_is_claimed_without_case(self):
        assert psx_region.extracted_entry(["GAME.PBP"], BEETLE_CLAIMS) == "GAME.PBP"

    def test_directories_and_unclaimed_entries_are_passed_over(self):
        assert psx_region.extracted_entry(["Game.cue/", "Game", "Game.txt"], BEETLE_CLAIMS) is None

    def test_of_two_names_apart_only_by_case_the_archive_s_first_is_written(self):
        assert psx_region.extracted_entry(["game.chd", "GAME.chd"], BEETLE_CLAIMS) == "game.chd"
        assert psx_region.extracted_entry(["GAME.chd", "game.chd"], BEETLE_CLAIMS) == "GAME.chd"


def archive(members: list[str], identities: Mapping[str, Any] | None = None, **extra: Any) -> FixtureMachine:
    return FixtureMachine(
        {ZIP: "x", **extra.pop("files", {})},
        archives={ZIP: members},
        disc_identities=identities or {},
        **extra,
    )


def hands(claims: frozenset[str] | None, **kwargs: Any) -> psx_region.ArchiveHands:
    return psx_region.ArchiveHands(claims=claims, **kwargs)


class TestAnArchive:
    """D2 of #691: an archive under SwanStation or Beetle PSX answers from the entry RetroArch extracts."""

    @pytest.mark.parametrize("emulator", [EMULATOR_SWANSTATION, EMULATOR_BEETLE])
    def test_a_chd_inside_is_read_as_a_chd(self, emulator):
        spec = {"codecs": ["cdlz"], "requires_parent": False, "identity": FACTS}
        machine = archive(["Game.chd"], {f"{ZIP}#Game.chd": spec})
        claims = SWAN_CLAIMS if emulator == EMULATOR_SWANSTATION else BEETLE_CLAIMS
        assert psx_region.read_disc_region(machine, emulator, ZIP, hands(claims)) == decided(
            NTSC_U, REGION_SOURCE_LICENSE
        )

    def test_the_entry_s_own_format_decides_what_the_emulator_opens(self):
        spec = {"codecs": ["cdzs"], "requires_parent": False, "identity": FACTS}
        machine = archive(["Game.chd"], {f"{ZIP}#Game.chd": spec})
        reading = psx_region.read_disc_region(machine, EMULATOR_SWANSTATION, ZIP, hands(SWAN_CLAIMS))
        assert reading == DiscRegionReading(reason=REASON_CONTENT_UNOPENABLE, disc=f"{ZIP}#Game.chd", extension="chd")

    def test_swanstation_opens_the_first_bin_as_a_bare_image(self):
        machine = archive(["Game.cue", "Game.bin"], {f"{ZIP}#Game.bin": FACTS})
        reading = psx_region.read_disc_region(machine, EMULATOR_SWANSTATION, ZIP, hands(SWAN_CLAIMS))
        assert reading == decided(NTSC_U, REGION_SOURCE_LICENSE)

    def test_a_cue_extracted_without_its_bin_is_content_the_core_cannot_open(self):
        machine = archive(["Game.cue", "Game.bin"], {f"{ZIP}#Game.cue": "missing"})
        reading = psx_region.read_disc_region(machine, EMULATOR_BEETLE, ZIP, hands(BEETLE_CLAIMS))
        assert reading == DiscRegionReading(reason=REASON_CONTENT_UNOPENABLE, disc=f"{ZIP}#Game.cue", extension="cue")

    def test_a_cue_whose_bin_lies_where_it_is_extracted_is_read(self):
        machine = archive(["Game.cue"], {f"{ZIP}#Game.cue": FACTS})
        reading = psx_region.read_disc_region(machine, EMULATOR_BEETLE, ZIP, hands(BEETLE_CLAIMS))
        assert reading.region == NTSC_U

    def test_no_claimed_entry_extracts_nothing(self):
        reading = psx_region.read_disc_region(archive(["readme.txt"]), EMULATOR_BEETLE, ZIP, hands(BEETLE_CLAIMS))
        assert reading == DiscRegionReading(reason=REASON_CONTENT_UNOPENABLE, disc=ZIP, extension="zip")

    def test_an_entry_in_a_folder_lands_in_a_directory_that_does_not_exist(self):
        spec = {"codecs": ["cdlz"], "requires_parent": False, "identity": FACTS}
        machine = archive(["sub/Game.chd"], {f"{ZIP}#sub/Game.chd": spec})
        beside = psx_region.read_disc_region(machine, EMULATOR_BEETLE, ZIP, hands(BEETLE_CLAIMS))
        assert beside == DiscRegionReading(reason=REASON_CONTENT_UNOPENABLE, disc=f"{ZIP}#sub/Game.chd", extension="chd")
        cached = psx_region.read_disc_region(machine, EMULATOR_BEETLE, ZIP, hands(BEETLE_CLAIMS, cache_directory="/tmp"))
        assert cached.region == NTSC_U

    def test_a_core_that_takes_archives_raw(self):
        reading = psx_region.read_disc_region(archive(["Game.chd"]), EMULATOR_BEETLE, ZIP, hands(BEETLE_CLAIMS, block_extract=True))
        assert reading == DiscRegionReading(reason=REASON_CONTENT_UNOPENABLE, disc=ZIP, extension="zip")

    @pytest.mark.parametrize("name", ["/roms/Game.7z", "/roms/Game.zst", "/roms/Game.ZST"])
    def test_an_archive_the_standard_library_cannot_read(self, name):
        reading = psx_region.read_disc_region(machine({name: "x"}), EMULATOR_SWANSTATION, name, hands(SWAN_CLAIMS))
        assert reading.reason == REASON_DISC_FORMAT_UNREAD

    def test_a_7z_with_a_capital_z_is_no_archive_to_retroarch(self):
        reading = psx_region.read_disc_region(machine({"/roms/Game.7Z": "x"}), EMULATOR_SWANSTATION, "/roms/Game.7Z")
        assert reading == DiscRegionReading(reason=REASON_CONTENT_UNOPENABLE, disc="/roms/Game.7Z", extension="7z")

    def test_an_apk_is_read_as_a_zip(self):
        spec = {"codecs": ["cdlz"], "requires_parent": False, "identity": FACTS}
        apk = "/roms/Game.apk"
        machine = FixtureMachine({apk: "x"}, archives={apk: ["Game.chd"]}, disc_identities={f"{apk}#Game.chd": spec})
        assert psx_region.read_disc_region(machine, EMULATOR_BEETLE, apk, hands(BEETLE_CLAIMS)).region == NTSC_U

    def test_claims_nobody_read(self):
        reading = psx_region.read_disc_region(archive(["Game.chd"]), EMULATOR_BEETLE, ZIP, hands(None))
        assert (reading.disc, reading.extension) == (ZIP, "zip")
        assert reading.region is None

    def test_duckstation_opens_no_archive(self):
        reading = psx_region.read_disc_region(archive(["Game.chd"]), EMULATOR_DUCKSTATION, ZIP, hands(SWAN_CLAIMS))
        assert reading == DiscRegionReading(reason=REASON_CONTENT_UNOPENABLE, disc=ZIP, extension="zip")

    def test_an_archive_that_does_not_list(self):
        absent = psx_region.read_disc_region(machine({}), EMULATOR_BEETLE, ZIP, hands(BEETLE_CLAIMS))
        assert (absent.reason, absent.status) == (REASON_DISC_UNREAD, DISC_UNREAD_MISSING)
        broken = FixtureMachine({ZIP: "x"}, archives={ZIP: "not-archive"})
        assert psx_region.read_disc_region(broken, EMULATOR_BEETLE, ZIP, hands(BEETLE_CLAIMS)).status == (
            DISC_UNREAD_NOT_A_DISC
        )
        undeclared = psx_region.read_disc_region(machine({ZIP: "x"}), EMULATOR_BEETLE, ZIP, hands(BEETLE_CLAIMS))
        assert undeclared.status == DISC_UNREAD_NOT_A_DISC

    def test_a_cue_landing_in_a_cache_directory_atlas_cannot_place(self):
        machine = archive(["Game.cue"], {f"{ZIP}#Game.cue": FACTS})
        reading = psx_region.read_disc_region(
            machine, EMULATOR_BEETLE, ZIP, hands(BEETLE_CLAIMS, cache_directory_read=False)
        )
        assert reading == DiscRegionReading(reason=REASON_DISC_UNREAD, disc=f"{ZIP}#Game.cue", status=DISC_UNREAD_UNREADABLE)

    def test_a_boot_file_inside_is_a_format_atlas_does_not_read(self):
        reading = psx_region.read_disc_region(archive(["Game.exe"]), EMULATOR_BEETLE, ZIP, hands(BEETLE_CLAIMS))
        assert reading == DiscRegionReading(reason=REASON_DISC_FORMAT_UNREAD, disc=f"{ZIP}#Game.exe", extension="exe")


class TestAPlaylistInsideAnArchive:
    @pytest.mark.parametrize("emulator", [EMULATOR_SWANSTATION, EMULATOR_BEETLE])
    def test_its_discs_are_looked_for_beside_where_it_is_written(self, emulator):
        machine = archive(
            ["Game.m3u", "Game.cue"],
            {GAME: FACTS},
            files={GAME: "x"},
            archive_texts={f"{ZIP}#Game.m3u": "Game.cue\n"},
        )
        claims = frozenset({"m3u"})
        assert psx_region.read_disc_region(machine, emulator, ZIP, hands(claims)).region == NTSC_U

    @pytest.mark.parametrize("emulator", [EMULATOR_SWANSTATION, EMULATOR_BEETLE])
    def test_a_disc_left_inside_the_archive_is_content_the_core_cannot_open(self, emulator):
        machine = archive(["Game.m3u", "Game.cue"], archive_texts={f"{ZIP}#Game.m3u": "Game.cue\n"})
        reading = psx_region.read_disc_region(machine, emulator, ZIP, hands(frozenset({"m3u"})))
        assert reading == DiscRegionReading(reason=REASON_CONTENT_UNOPENABLE, disc=f"{ZIP}#Game.m3u", extension="m3u")

    def test_a_playlist_with_no_entry(self):
        machine = archive(["Game.m3u"], archive_texts={f"{ZIP}#Game.m3u": "\n"})
        reading = psx_region.read_disc_region(machine, EMULATOR_SWANSTATION, ZIP, hands(frozenset({"m3u"})))
        assert reading.reason == REASON_CONTENT_UNOPENABLE

    def test_a_playlist_text_not_declared_is_missing(self):
        reading = psx_region.read_disc_region(archive(["Game.m3u"]), EMULATOR_BEETLE, ZIP, hands(frozenset({"m3u"})))
        assert (reading.reason, reading.status) == (REASON_DISC_UNREAD, DISC_UNREAD_MISSING)
