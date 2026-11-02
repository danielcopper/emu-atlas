"""Tests for atlas.disc_opens — the parts of each emulator's open a whole-machine vector cannot isolate.

What each entry opens through a cue sheet, a ``.ccd`` or a playlist, and the
verdicts that follow, are held by the machines vectors
(``vectors/machines/disc-opens.json``), which run the reading end to end through
the launchability answer. This module holds the grammar details and the bounds
underneath them, each against a fixture machine.
"""

from __future__ import annotations

import pytest

from atlas import disc_opens, psx_disc
from atlas.disc_opens import (
    BEETLE_PCFX,
    BEETLE_PSX,
    BEETLE_SATURN,
    DUCKSTATION,
    FLYCAST,
    GENESIS_PLUS_GX,
    HOW_NAME_DERIVED,
    HOW_NAMED,
    SWANSTATION,
    Opened,
    PatchSetting,
)
from atlas.machine import FixtureMachine, RealMachine

BIN = {"size": 2352}
ROOT = "/roms/Game"


def at(name: str) -> str:
    return f"{ROOT}/{name}"


def patches_off() -> PatchSetting:
    return PatchSetting(False, "setting")


def reading_of(files, profile, launched="Game.cue", patches=patches_off) -> disc_opens.OpensReading | None:
    machine = FixtureMachine({at(name): value for name, value in files.items()})
    return disc_opens.read_opens(machine, profile, "Entry", at(launched), patches)


def read(files, profile, launched="Game.cue", patches=patches_off) -> disc_opens.OpensReading:
    reading = reading_of(files, profile, launched, patches)
    assert reading is not None
    return reading


def opened(reading: disc_opens.OpensReading) -> tuple[Opened, ...]:
    assert reading.opens is not None
    return reading.opens


def listed(reading: disc_opens.OpensReading) -> list[tuple[str, str, bool | None]]:
    return [(entry.path.removeprefix(ROOT + "/"), entry.how, entry.present) for entry in opened(reading)]


def codes(reading: disc_opens.OpensReading) -> list[str]:
    return [caveat.code for caveat in reading.caveats]


class TestAnOpenedFileStatesHowItCameToBeOpened:
    def test_a_word_outside_the_vocabulary_is_refused(self):
        with pytest.raises(ValueError, match="how must be one of"):
            Opened("/roms/Game.bin", "guessed", "/roms/Game.cue", True)  # type: ignore[arg-type]

    def test_the_vocabulary_is_the_two_ways(self):
        assert disc_opens.OPENED_HOWS == (HOW_NAMED, HOW_NAME_DERIVED)


class TestTheReadingCoversTheFormatsItReads:
    @pytest.mark.parametrize("name", ["Game.chd", "Game.pbp", "Game.exe", "Game.toc"])
    def test_a_launched_format_this_cut_does_not_read_has_no_reading(self, name):
        assert reading_of({name: BIN}, SWANSTATION, name) is None
        assert reading_of({name: BIN}, BEETLE_PSX, name) is None

    def test_a_bare_image_on_beetle_has_no_reading(self):
        # The mednafen loaders read every other name as a cue sheet; atlas
        # reads a sheet's text, not a binary image as one.
        assert reading_of({"Game.bin": BIN}, BEETLE_PSX, "Game.bin") is None

    def test_a_bare_image_on_duckstation_opens_its_companions(self):
        reading = read({"Game.bin": BIN}, DUCKSTATION, "Game.bin")
        assert listed(reading) == [("Game.sbi", HOW_NAME_DERIVED, False), ("Game.lsd", HOW_NAME_DERIVED, False)]

    def test_the_suffix_decides_without_regard_to_case(self):
        files = {"Game.CUE": 'FILE "Game.bin" BINARY\n TRACK 01 MODE2/2352\n INDEX 01 00:00:00\n', "Game.bin": BIN}
        reading = read(files, SWANSTATION, "Game.CUE")
        assert listed(reading)[0] == ("Game.bin", HOW_NAMED, True)

    def test_an_mds_opens_the_mdf_of_its_name(self):
        reading = read({"Game.mds": BIN, "Game.mdf": BIN}, SWANSTATION, "Game.mds")
        assert listed(reading)[0] == ("Game.mdf", HOW_NAME_DERIVED, True)
        assert reading.outcome == disc_opens.UNESTABLISHED

    def test_an_mds_without_its_mdf_is_refused(self):
        reading = read({"Game.mds": BIN}, SWANSTATION, "Game.mds")
        assert reading.outcome == disc_opens.REFUSES
        assert codes(reading) == [disc_opens.CAVEAT_OPENS_FILE_MISSING]


SHEET = 'FILE "A.bin" BINARY\n  TRACK 01 MODE2/2352\n    INDEX 01 00:00:00\n'


class TestDuckStationsCueSheet:
    def test_a_track_starting_before_its_predecessor_in_one_file_is_refused(self):
        sheet = (
            'FILE "A.bin" BINARY\n TRACK 01 MODE2/2352\n  INDEX 01 00:10:00\n'
            " TRACK 02 AUDIO\n  INDEX 01 00:05:00\n"
        )
        reading = read({"Game.cue": sheet, "A.bin": BIN}, DUCKSTATION)
        assert reading.outcome == disc_opens.REFUSES
        assert codes(reading) == [disc_opens.CAVEAT_OPENS_SHEET_REFUSED]

    def test_tracks_are_opened_from_one_until_a_number_is_missing(self):
        sheet = SHEET + 'FILE "C.bin" BINARY\n  TRACK 03 AUDIO\n    INDEX 01 00:00:00\n'
        reading = read({"Game.cue": sheet, "A.bin": BIN}, SWANSTATION)
        assert [path for path, _, _ in listed(reading)] == ["A.bin", "Game.sbi"]

    def test_a_file_two_tracks_name_is_opened_once(self):
        sheet = 'FILE "A.bin" BINARY\n TRACK 01 MODE2/2352\n  INDEX 01 00:00:00\n TRACK 02 AUDIO\n  INDEX 01 00:09:00\n'
        reading = read({"Game.cue": sheet, "A.bin": BIN}, SWANSTATION)
        assert [path for path, _, _ in listed(reading)] == ["A.bin", "Game.sbi"]

    def test_only_the_first_file_is_looked_for_as_the_sheets_own_name(self):
        sheet = SHEET + 'FILE "B.bin" BINARY\n  TRACK 02 AUDIO\n    INDEX 01 00:00:00\n'
        reading = read({"Game.cue": sheet, "A.bin": BIN, "Game.bin": BIN}, SWANSTATION)
        assert reading.outcome == disc_opens.REFUSES
        assert ("Game.bin", HOW_NAME_DERIVED, True) not in listed(reading)

    def test_an_absolute_name_is_opened_as_it_is(self):
        sheet = 'FILE "/elsewhere/A.bin" BINARY\n  TRACK 01 MODE2/2352\n    INDEX 01 00:00:00\n'
        machine = FixtureMachine({at("Game.cue"): sheet, "/elsewhere/A.bin": BIN})
        reading = disc_opens.read_opens(machine, SWANSTATION, "Entry", at("Game.cue"), patches_off)
        assert reading is not None
        assert reading.opens is not None
        assert reading.opens[0].path == "/elsewhere/A.bin"

    def test_a_directory_where_a_file_is_named_is_not_established(self):
        machine = FixtureMachine({at("Game.cue"): SHEET}, dirs=[at("A.bin")])
        reading = disc_opens.read_opens(machine, SWANSTATION, "Entry", at("Game.cue"), patches_off)
        assert reading is not None
        assert reading.outcome == disc_opens.UNESTABLISHED
        assert reading.caveats[0].code == disc_opens.CAVEAT_OPENS_FILE_UNREAD

    def test_the_lsd_is_not_looked_for_beside_an_sbi(self):
        reading = read({"Game.cue": SHEET, "A.bin": BIN, "Game.sbi": {"size": 4}}, DUCKSTATION)
        assert [path for path, _, _ in listed(reading)] == ["A.bin", "Game.sbi"]


class TestDuckStationsPlaylist:
    def test_a_nested_playlist_contributes_its_first_entry_alone(self):
        files = {
            "Game.m3u": "Inner.m3u\n",
            "Inner.m3u": "Game.cue\nOther.cue\n",
            "Game.cue": SHEET,
            "A.bin": BIN,
            "Other.cue": SHEET,
        }
        reading = read(files, SWANSTATION, "Game.m3u")
        assert "Other.cue" not in [path for path, _, _ in listed(reading)]

    def test_a_playlist_reaching_itself_is_refused(self):
        reading = read({"Game.m3u": "Inner.m3u\n", "Inner.m3u": "Game.m3u\n"}, SWANSTATION, "Game.m3u")
        assert reading.outcome == disc_opens.REFUSES
        assert codes(reading) == [disc_opens.CAVEAT_OPENS_SHEET_REFUSED]

    def test_an_empty_playlist_is_refused(self):
        reading = read({"Game.m3u": "# nothing\n"}, SWANSTATION, "Game.m3u")
        assert reading.outcome == disc_opens.REFUSES

    def test_duckstation_patches_each_image_and_the_playlist(self):
        files = {"List.m3u": "Game.cue\n", "Game.cue": SHEET, "A.bin": BIN}
        reading = read(files, DUCKSTATION, "List.m3u", lambda: PatchSetting(True, "setting"))
        patches = [(o.path, o.by) for o in opened(reading) if o.path.endswith(".ppf")]
        assert patches == [(at("Game.ppf"), at("Game.cue")), (at("List.ppf"), at("List.m3u"))]

    def test_swanstation_patches_the_launched_path_alone(self):
        files = {"List.m3u": "Game.cue\n", "Game.cue": SHEET, "A.bin": BIN}
        reading = read(files, SWANSTATION, "List.m3u", lambda: PatchSetting(True, "setting"))
        assert [o.path for o in opened(reading) if o.path.endswith(".ppf")] == [at("List.ppf")]

    def test_the_setting_is_not_read_where_nothing_asks_it(self):
        def unread() -> PatchSetting:
            raise AssertionError("the setting was read for a sheet that refused before any image opened")

        reading = read({"Game.cue": 'FILE "A.wav" WAVE\n TRACK 01 AUDIO\n INDEX 01 00:00:00\n'}, DUCKSTATION,
                       patches=unread)
        assert reading.outcome == disc_opens.REFUSES


def mednafen(sheet: str, profile=BEETLE_PSX, extra=None):
    return read({"Game.cue": sheet, "A.bin": BIN, **(extra or {})}, profile)


class TestTheMednafenCueGrammar:
    def test_a_last_line_no_terminator_ends_is_never_read(self):
        reading = mednafen(SHEET + 'FILE "B.bin" BINARY')
        assert [path for path, _, _ in listed(reading)] == ["A.bin", "Game.sbi"]

    def test_cr_and_nul_end_a_line_too(self):
        reading = mednafen('FILE "A.bin" BINARY\r  TRACK 01 MODE2/2352\x00    INDEX 01 00:00:00\r\n')
        assert reading.outcome == disc_opens.ACCEPTS

    def test_a_byte_order_mark_is_skipped(self):
        assert mednafen("﻿" + SHEET).outcome == disc_opens.ACCEPTS

    def test_an_unquoted_name_ends_at_a_space(self):
        reading = mednafen("FILE A.bin BINARY\n  TRACK 01 MODE2/2352\n    INDEX 01 00:00:00\n")
        assert listed(reading)[0] == ("A.bin", HOW_NAMED, True)

    def test_an_unknown_directive_stops_beetle_psx(self):
        reading = mednafen(SHEET + "WHATEVER 1\n")
        assert reading.outcome == disc_opens.REFUSES

    def test_an_unknown_directive_does_not_stop_a_newer_core(self):
        reading = mednafen(SHEET + "WHATEVER 1\n", BEETLE_SATURN)
        assert reading.outcome == disc_opens.ACCEPTS
        assert codes(reading) == [disc_opens.CAVEAT_OPENS_SHEET_REFUSED]

    def test_an_unknown_flag_ends_the_sheet(self):
        assert mednafen(SHEET.replace("INDEX", "FLAGS DCP NOPE\n    INDEX")).outcome == disc_opens.REFUSES

    def test_flags_are_matched_exactly(self):
        assert mednafen(SHEET.replace("INDEX", "FLAGS dcp\n    INDEX")).outcome == disc_opens.REFUSES

    def test_track_zero_parts_the_two_grammars(self):
        sheet = SHEET.replace("TRACK 01", "TRACK 00")
        assert mednafen(sheet).outcome == disc_opens.ACCEPTS
        assert codes(mednafen(sheet, BEETLE_SATURN)) == [disc_opens.CAVEAT_OPENS_SHEET_REFUSED]

    def test_cd_i_is_a_newer_track_mode(self):
        sheet = SHEET.replace("MODE2/2352", "CDI/2352")
        assert mednafen(sheet).outcome == disc_opens.REFUSES
        assert mednafen(sheet, BEETLE_PCFX).outcome == disc_opens.ACCEPTS

    def test_a_newer_core_bounds_a_time(self):
        sheet = SHEET.replace("00:00:00", "00:60:00")
        assert mednafen(sheet).outcome == disc_opens.ACCEPTS
        assert codes(mednafen(sheet, BEETLE_SATURN)) == [disc_opens.CAVEAT_OPENS_SHEET_REFUSED]

    def test_a_time_outside_a_track_is_not_read(self):
        assert mednafen('PREGAP x\n' + SHEET).outcome == disc_opens.ACCEPTS

    def test_a_missing_file_ends_beetle_psxs_sheet(self):
        reading = mednafen(SHEET + 'FILE "B.bin" BINARY\nFILE "C.bin" BINARY\n')
        assert [path for path, _, _ in listed(reading)] == ["A.bin", "B.bin"]

    def test_a_newer_core_reads_on_past_a_missing_file(self):
        sheet = SHEET + 'FILE "B.bin" BINARY\n  TRACK 02 AUDIO\nFILE "C.bin" BINARY\n'
        reading = mednafen(sheet, BEETLE_SATURN, {"C.bin": BIN})
        assert [path for path, _, _ in listed(reading)] == ["A.bin", "B.bin", "C.bin", "Game.sbi"]
        assert reading.outcome == disc_opens.ACCEPTS

    def test_the_sbi_is_cased_like_the_sheets_extension(self):
        files = {"Game.CuE": SHEET, "A.bin": BIN}
        reading = read(files, BEETLE_PSX, "Game.CuE")
        assert listed(reading)[-1] == ("Game.SbI", HOW_NAME_DERIVED, False)

    def test_a_newer_core_checks_an_sbi_that_is_there(self):
        reading = mednafen(SHEET, BEETLE_SATURN, {"Game.sbi": {"size": 4}})
        assert reading.outcome == disc_opens.UNESTABLISHED
        assert codes(reading) == [disc_opens.CAVEAT_OPENS_CONTENT_UNREAD]


class TestTheMednafenCcd:
    @pytest.mark.parametrize(
        ("extension", "img", "sub"),
        [(".ccd", "img", "sub"), (".CCD", "IMG", "SUB"), (".Ccd", "Img", "Sub"), (".cCD", "iMG", "sUB")],
    )
    def test_the_image_and_subchannel_are_cased_letter_by_letter(self, extension, img, sub):
        reading = read({f"Game{extension}": "[CloneCD]\n"}, BEETLE_SATURN, f"Game{extension}")
        assert [path for path, _, _ in listed(reading)] == [f"Game.{img}"]
        assert disc_opens.ccd_extensions(extension) == (img, sub)

    def test_a_character_that_is_no_letter_takes_the_case_before_it(self):
        assert disc_opens.ccd_extensions(".c1D") == ("imG", "suB")
        assert disc_opens.ccd_extensions(".C1d") == ("IMg", "SUb")

    def test_a_character_before_any_letter_takes_the_last_letters_case(self):
        assert disc_opens.ccd_extensions(".1cD") == ("ImG", "SuB")


class TestTheMednafenPlaylist:
    def test_a_nested_playlist_in_upper_case_is_a_disc(self):
        files = {"Game.m3u": "Inner.M3U\n", "Inner.M3U": "Game.cue\n"}
        reading = read(files, BEETLE_PSX, "Game.m3u")
        assert listed(reading) == [("Inner.M3U", HOW_NAMED, True)]
        assert codes(reading) == [disc_opens.CAVEAT_OPENS_FORMAT_UNREAD]

    def test_a_playlist_naming_itself_ends_its_own_read(self):
        files = {"Game.m3u": "Game.cue\nGame.m3u\nOther.cue\n", "Game.cue": SHEET, "A.bin": BIN}
        reading = read(files, BEETLE_PSX, "Game.m3u")
        assert "Other.cue" not in [path for path, _, _ in listed(reading)]

    def test_a_loop_through_another_playlist_is_refused(self):
        files = {"Game.m3u": "Inner.m3u\n", "Inner.m3u": "Game.m3u\n"}
        assert read(files, BEETLE_SATURN, "Game.m3u").outcome == disc_opens.REFUSES

    def test_a_missing_nested_playlist_contributes_nothing(self):
        files = {"Game.m3u": "Gone.m3u\nGame.cue\n", "Game.cue": SHEET, "A.bin": BIN}
        reading = read(files, BEETLE_PSX, "Game.m3u")
        assert reading.outcome == disc_opens.ACCEPTS
        assert listed(reading)[0] == ("Gone.m3u", HOW_NAMED, False)
        assert codes(reading) == [disc_opens.CAVEAT_OPENS_FILE_MISSING]

    def test_a_playlist_naming_no_disc_is_not_established(self):
        assert read({"Game.m3u": "#\n"}, BEETLE_PSX, "Game.m3u").outcome == disc_opens.UNESTABLISHED


class TestTheWalkIsBounded:
    def test_past_the_listed_bound_the_reading_stops_short(self, monkeypatch):
        monkeypatch.setattr(disc_opens, "MAX_LISTED", 2)
        sheet = "".join(
            f'FILE "{name}.bin" BINARY\n  TRACK 0{n} AUDIO\n    INDEX 01 00:00:00\n'
            for n, name in enumerate("ABC", 1)
        )
        reading = mednafen(sheet, BEETLE_SATURN, {"B.bin": BIN, "C.bin": BIN})
        assert len(opened(reading)) == 2
        assert reading.outcome == disc_opens.UNESTABLISHED
        assert codes(reading) == [disc_opens.CAVEAT_OPENS_BOUND_EXCEEDED]

    def test_a_cue_sheet_past_atlass_bound_is_not_read(self, monkeypatch):
        monkeypatch.setattr(psx_disc, "MAX_CUE_BYTES", 8)
        reading = mednafen(SHEET)
        assert reading.opens is None
        assert codes(reading) == [disc_opens.CAVEAT_OPENS_FILE_UNREAD]


class TestAFileTypeNamesWhatTheParserRefused:
    def test_the_refusal_is_a_not_a_disc_that_names_the_file(self):
        with pytest.raises(psx_disc.NotBinaryFile) as raised:
            psx_disc.parse_cue(b'FILE "A.wav" WAVE\n')
        assert isinstance(raised.value, psx_disc.NotADisc)
        assert (raised.value.name, raised.value.kind) == (b"A.wav", b"WAVE")


def fan_out(width: int, depth: int) -> dict[str, object]:
    """Playlists *depth* deep, each naming the next one *width* times, the last naming one sheet *width* times."""
    files: dict[str, object] = {f"Level {level}.m3u": f"Level {level + 1}.m3u\n" * width for level in range(depth)}
    files[f"Level {depth}.m3u"] = "Game.cue\n" * width
    return {**files, "Game.cue": SHEET, "A.bin": BIN}


class TestARepeatedEntryCountsAgainstOneBudget:
    """The emulators open an entry named twice twice; every visit is a step, so a fan-out cannot multiply the work."""

    def test_a_fanned_out_playlist_reaches_the_step_bound(self, monkeypatch):
        monkeypatch.setattr(disc_opens, "MAX_STEPS", 200)
        reading = read(fan_out(width=4, depth=4), BEETLE_PSX, "Level 0.m3u")
        assert reading.outcome == disc_opens.UNESTABLISHED
        assert codes(reading) == [disc_opens.CAVEAT_OPENS_BOUND_EXCEEDED]

    def test_a_wide_fan_out_stays_within_the_default_budget(self):
        # Eight wide and six deep is 8**7 = 2097152 disc visits; the walk
        # stops at its step bound instead of making them.
        reading = read(fan_out(width=8, depth=6), BEETLE_SATURN, "Level 0.m3u")
        assert codes(reading)[-1] == disc_opens.CAVEAT_OPENS_BOUND_EXCEEDED

    def test_a_disc_named_again_on_a_playlist_counts_again(self, monkeypatch):
        monkeypatch.setattr(disc_opens, "MAX_STEPS", 100)
        reading = read({"Game.m3u": "Game.cue\n" * 200, "Game.cue": SHEET, "A.bin": BIN}, SWANSTATION, "Game.m3u")
        assert codes(reading) == [disc_opens.CAVEAT_OPENS_BOUND_EXCEEDED]

    def test_a_repeated_file_is_listed_once(self):
        reading = read({"Game.m3u": "Game.cue\n" * 3, "Game.cue": SHEET, "A.bin": BIN}, BEETLE_PSX, "Game.m3u")
        assert [path for path, _, _ in listed(reading)] == ["Game.cue", "A.bin", "Game.sbi"]


class TestASheetDeclaringTooManyTracksIsBounded:
    def test_every_line_duckstations_parser_takes_is_a_step(self):
        reading = read({"Game.cue": SHEET + "\n" * disc_opens.MAX_STEPS, "A.bin": BIN}, SWANSTATION)
        assert codes(reading) == [disc_opens.CAVEAT_OPENS_BOUND_EXCEEDED]

    def test_past_the_track_bound_the_reading_stops_short(self):
        sheet = 'FILE "A.bin" BINARY\n' + "  TRACK 01 MODE2/2352\n    INDEX 01 00:00:00\n" * (disc_opens.MAX_TRACKS + 1)
        reading = read({"Game.cue": sheet, "A.bin": BIN}, DUCKSTATION)
        assert reading.outcome == disc_opens.UNESTABLISHED
        assert codes(reading) == [disc_opens.CAVEAT_OPENS_BOUND_EXCEEDED]

    def test_a_sheet_at_the_track_bound_is_read(self):
        sheet = 'FILE "A.bin" BINARY\n' + "  TRACK 01 MODE2/2352\n    INDEX 01 00:00:00\n" * disc_opens.MAX_TRACKS
        reading = read({"Game.cue": sheet, "A.bin": BIN}, DUCKSTATION)
        assert reading.outcome == disc_opens.ACCEPTS


class TestANulEndsAPlaylistEntry:
    """Both emulators hand an entry on as a C string, so nothing after a NUL reaches the open — and no path does."""

    @pytest.mark.parametrize("profile", [DUCKSTATION, BEETLE_PSX])
    def test_an_entry_is_cut_at_its_first_nul(self, tmp_path, profile):
        (tmp_path / "Game.m3u").write_bytes(b"Game.cue\x00 trailing\n")
        (tmp_path / "Game.cue").write_text(SHEET)
        (tmp_path / "A.bin").write_bytes(b"")
        reading = disc_opens.read_opens(RealMachine(), profile, "Entry", str(tmp_path / "Game.m3u"), patches_off)
        assert reading is not None
        assert opened(reading)[0].path == str(tmp_path / "Game.cue")
        assert reading.outcome == disc_opens.ACCEPTS

    def test_a_path_the_system_refuses_to_look_up_has_no_known_presence(self, tmp_path):
        presence = disc_opens._presence  # pyright: ignore[reportPrivateUsage]
        assert presence(RealMachine(), f"{tmp_path}/Game\x00.cue") is None


BIG_PLAYLIST = "# " + "x" * 600 + "\nGame.cue\n"


class TestTheBytesReadAreBounded:
    def test_a_file_read_under_many_spellings_reaches_the_byte_bound(self, monkeypatch):
        monkeypatch.setattr(disc_opens, "MAX_READ_BYTES", 2000)
        spellings = "".join(f"{'.' + '/' * n}Big.m3u\n" for n in range(1, 13))
        files = {"Game.m3u": spellings, "Big.m3u": "# " + "x" * 600 + "\nGame.cue\n", "Game.cue": SHEET, "A.bin": BIN}
        reading = read(files, BEETLE_SATURN, "Game.m3u")
        assert reading.outcome == disc_opens.UNESTABLISHED
        assert codes(reading) == [disc_opens.CAVEAT_OPENS_BOUND_EXCEEDED]

    def test_a_mednafen_core_reads_a_playlist_again_on_every_visit(self, monkeypatch):
        monkeypatch.setattr(disc_opens, "MAX_READ_BYTES", 2000)
        files = {"Game.m3u": "Big.m3u\n" * 12, "Big.m3u": BIG_PLAYLIST, "Game.cue": SHEET, "A.bin": BIN}
        assert codes(read(files, BEETLE_SATURN, "Game.m3u")) == [disc_opens.CAVEAT_OPENS_BOUND_EXCEEDED]

    def test_duckstation_keeps_a_nested_playlists_first_entry_and_reads_it_once(self, monkeypatch):
        monkeypatch.setattr(disc_opens, "MAX_READ_BYTES", 2000)
        files = {"Game.m3u": "Big.m3u\n" * 12, "Big.m3u": BIG_PLAYLIST, "Game.cue": SHEET, "A.bin": BIN}
        assert read(files, SWANSTATION, "Game.m3u").outcome == disc_opens.ACCEPTS

    def test_an_over_limit_sheet_is_charged_what_its_read_consumed(self):
        # A bounded read takes the limit and one byte more to tell a file is
        # over it; twelve spellings of a sheet past the cue bound read 48 MiB.
        spellings = "".join(f"{'.' + '/' * n}Big.cue\n" for n in range(1, 13))
        files = {"Game.m3u": spellings, "Big.cue": {"size": psx_disc.MAX_CUE_BYTES + 1}}
        reading = read(files, BEETLE_SATURN, "Game.m3u")
        assert codes(reading)[-1] == disc_opens.CAVEAT_OPENS_BOUND_EXCEEDED

    def test_a_large_playlist_under_many_spellings_holds_one_entry_per_spelling(self, monkeypatch):
        # DuckStation opens a nested playlist's first entry alone, so a reading
        # keeps that and nothing else of each spelling it reads.
        monkeypatch.setattr(disc_opens, "MAX_READ_BYTES", 1 << 30)
        spellings = "".join(f"{'.' + '/' * n}Big.m3u\n" for n in range(1, 13))
        body = "Game.cue\n" + "Other.cue\n" * 20000
        files = {"Game.m3u": spellings, "Big.m3u": body, "Game.cue": SHEET, "A.bin": BIN}
        machine = FixtureMachine({at(name): value for name, value in files.items()})
        duck_reader = disc_opens._DuckReader  # pyright: ignore[reportPrivateUsage]
        reader = duck_reader(machine, SWANSTATION, "Entry", at("Game.m3u"), patches_off)
        reader.read(at("Game.m3u"))
        kept = reader._firsts  # pyright: ignore[reportPrivateUsage]
        assert len(kept) == 13
        assert all(isinstance(first, str) and len(first) < 64 for first in kept.values())


class TestTheAnswerIsBounded:
    """What a reading holds for its answer is bounded too: long paths, repeats and many discs cannot grow it."""

    def test_a_path_too_long_to_open_is_stated_once_and_never_listed(self):
        long_name = "x" * 5000 + ".cue"
        playlists = {"Game.m3u": "Game.cue\n" + "Big.m3u\n" * 300, "Big.m3u": long_name + "\n"}
        files = {**playlists, "Game.cue": SHEET, "A.bin": BIN}
        reading = read(files, SWANSTATION, "Game.m3u")
        assert codes(reading) == [disc_opens.CAVEAT_OPENS_PATH_TOO_LONG]
        assert len(reading.caveats[0].data["file"]) == disc_opens.MAX_SHOWN + 1
        assert all(long_name not in entry.path for entry in opened(reading))

    def test_a_path_past_the_path_limit_cannot_be_opened(self):
        files = {"Game.m3u": "d/" * 2100 + "Game.cue\n"}
        reading = read(files, SWANSTATION, "Game.m3u")
        assert reading.outcome == disc_opens.REFUSES
        assert codes(reading) == [disc_opens.CAVEAT_OPENS_PATH_TOO_LONG]

    def test_a_too_long_path_a_disc_switch_opens_is_stated_as_such_and_not_listed(self):
        long_path = "d/" * 2100 + "Game.cue"
        files = {"Game.m3u": "Game.cue\n" + long_path + "\n", "Game.cue": SHEET, "A.bin": BIN}
        reading = read(files, SWANSTATION, "Game.m3u")
        assert codes(reading) == [disc_opens.CAVEAT_OPENS_PATH_TOO_LONG]
        assert all(len(entry.path) < 4096 for entry in opened(reading))

    def test_a_long_name_goes_to_the_real_lookup(self):
        # A name's own limit belongs to each filesystem (exFAT takes far
        # longer names than ext4), so only the lookup can tell it is missing.
        files = {"Game.m3u": "n" * 300 + ".cue\n"}
        assert codes(read(files, SWANSTATION, "Game.m3u")) == [disc_opens.CAVEAT_OPENS_FILE_MISSING]

    def test_a_too_long_first_entry_is_kept_cut_short(self):
        playlists = {"Game.m3u": "Game.cue\n" + "Big.m3u\n" * 3, "Big.m3u": "x" * 70000 + ".cue\n"}
        files = {**playlists, "Game.cue": SHEET, "A.bin": BIN}
        machine = FixtureMachine({at(name): value for name, value in files.items()})
        duck_reader = disc_opens._DuckReader  # pyright: ignore[reportPrivateUsage]
        reader = duck_reader(machine, SWANSTATION, "Entry", at("Game.m3u"), patches_off)
        reader.read(at("Game.m3u"))
        kept = reader._firsts[at("Big.m3u")]  # pyright: ignore[reportPrivateUsage]
        walk = reader.walk
        assert not isinstance(kept, str)
        assert kept is not None
        assert len(kept.shown) == disc_opens.MAX_SHOWN + 1
        assert max(len(key) for _, key in walk._said) < 1024  # pyright: ignore[reportPrivateUsage]
        assert codes(walk.reading()) == [disc_opens.CAVEAT_OPENS_PATH_TOO_LONG]

    def test_the_file_a_refused_sheet_names_is_charged_to_the_answer(self, monkeypatch):
        # Every disc a switch opens keeps its sheet's refusal, and with it the
        # long name it refused, so many such sheets reach the output bound.
        monkeypatch.setattr(disc_opens, "MAX_OUTPUT", 10000)
        sheets = {f"Big{n}.cue": 'FILE "' + "n" * 1000 + '" WAVE\n' for n in range(20)}
        files = {"Game.m3u": "Game.cue\n" + "".join(f"{name}\n" for name in sheets), "Game.cue": SHEET, "A.bin": BIN}
        machine = FixtureMachine({at(name): value for name, value in {**files, **sheets}.items()})
        duck_reader = disc_opens._DuckReader  # pyright: ignore[reportPrivateUsage]
        launched = at("Game.m3u")
        reader = duck_reader(machine, SWANSTATION, "Entry", launched, patches_off)
        with pytest.raises(disc_opens._Bounded):  # pyright: ignore[reportPrivateUsage]
            reader.read(launched)
        kept = reader._sheets.values()  # pyright: ignore[reportPrivateUsage]
        names = sum(len(sheet.name) + len(sheet.kind) for sheet in kept if isinstance(sheet, psx_disc.NotBinaryFile))
        assert names <= reader.walk.output

    def test_a_file_missing_on_every_visit_is_stated_once(self):
        files = {"Game.m3u": "Game.cue\n" + "Gone.cue\n" * 50, "Game.cue": SHEET, "A.bin": BIN}
        assert codes(read(files, SWANSTATION, "Game.m3u")) == [disc_opens.CAVEAT_OPENS_FILE_MISSING]

    def test_many_discs_under_a_long_spelling_reach_the_output_bound(self):
        files = {"Game.m3u": "./" * 900 + "Big.m3u\n", "Big.m3u": "a\n" * 3000}
        reading = read(files, BEETLE_SATURN, "Game.m3u")
        assert codes(reading) == [disc_opens.CAVEAT_OPENS_BOUND_EXCEEDED]

    def test_past_the_output_bound_the_reading_stops_short(self, monkeypatch):
        monkeypatch.setattr(disc_opens, "MAX_OUTPUT", 150)
        sheet = "".join(
            f'FILE "{name}.bin" BINARY\n  TRACK 0{n} AUDIO\n    INDEX 01 00:00:00\n' for n, name in enumerate("ABC", 1)
        )
        reading = mednafen(sheet, BEETLE_SATURN, {"B.bin": BIN, "C.bin": BIN})
        assert codes(reading) == [disc_opens.CAVEAT_OPENS_BOUND_EXCEEDED]


class TestARefusalAlwaysCarriesItsReason:
    @pytest.mark.parametrize("budget", range(60, 420, 4))
    def test_at_any_output_bound_a_refusal_states_why(self, monkeypatch, budget):
        # The reason is charged before the refusal stands (D4): where the
        # bound cuts the walk at the reason, the take is short, not refused.
        monkeypatch.setattr(disc_opens, "MAX_OUTPUT", budget)
        reading = mednafen(SHEET + 'FILE "B.bin" BINARY\n')
        if reading.outcome == disc_opens.REFUSES:
            assert disc_opens.CAVEAT_OPENS_FILE_MISSING in codes(reading)


# ── Flycast ────────────────────────────────────────────────────────────────

GDI = "3\n1 0 4 2352 track01.bin 0\n2 600 0 2352 \"track 02.raw\" 0\n3 45000 4 2352 track03.bin 0\n"
GDI_TRACKS = {"track01.bin": BIN, "track 02.raw": BIN, "track03.bin": BIN}


def dc_cue(*names: str, mode: str = "MODE1/2352", head: str = "") -> str:
    """A Flycast cue sheet: one FILE, TRACK and INDEX 01 per name."""
    body = "".join(
        f'FILE "{name}" BINARY\n  TRACK {n:02d} {mode}\n    INDEX 01 00:00:00\n' for n, name in enumerate(names, 1)
    )
    return head + body


THREE = dc_cue("T1.bin", "T2.bin", "T3.bin")
THREE_FILES = {"T1.bin": BIN, "T2.bin": BIN, "T3.bin": BIN}


class TestFlycastsGdi:
    def test_every_track_with_a_sector_size_is_opened(self):
        reading = read({"Game.gdi": GDI, **GDI_TRACKS}, FLYCAST, "Game.gdi")
        assert listed(reading) == [(name, HOW_NAMED, True) for name in GDI_TRACKS]
        assert reading.outcome == disc_opens.ACCEPTS

    def test_a_track_of_sector_size_zero_is_not_opened(self):
        gdi = "3\n1 0 4 2352 a.bin 0\n2 600 0 0 b.raw 0\n3 45000 4 -0 c.bin 0\n"
        reading = read({"Game.gdi": gdi, "a.bin": BIN}, FLYCAST, "Game.gdi")
        assert [path for path, _, _ in listed(reading)] == ["a.bin"]

    def test_a_missing_track_stops_the_open(self):
        reading = read({"Game.gdi": GDI, "track01.bin": BIN}, FLYCAST, "Game.gdi")
        assert listed(reading)[-1] == ("track 02.raw", HOW_NAMED, False)
        assert reading.outcome == disc_opens.REFUSES
        assert codes(reading) == [disc_opens.CAVEAT_OPENS_FILE_MISSING]

    def test_fewer_than_three_tracks_are_refused_after_they_opened(self):
        reading = read({"Game.gdi": "2\n1 0 4 2352 a.bin 0\n2 600 0 2352 b.bin 0\n", "a.bin": BIN, "b.bin": BIN},
                       FLYCAST, "Game.gdi")
        assert [path for path, _, _ in listed(reading)] == ["a.bin", "b.bin"]
        assert codes(reading) == [disc_opens.CAVEAT_OPENS_SHEET_REFUSED]

    def test_a_sheet_of_8193_bytes_is_refused_by_its_size(self):
        padded = GDI + " " * (8193 - len(GDI))
        assert read({"Game.gdi": padded, **GDI_TRACKS}, FLYCAST, "Game.gdi").outcome == disc_opens.REFUSES
        assert read({"Game.gdi": padded[:-1], **GDI_TRACKS}, FLYCAST, "Game.gdi").outcome == disc_opens.ACCEPTS

    def test_an_absolute_name_is_joined_onto_the_sheets_directory(self):
        gdi = "3\n1 0 4 0 aa 0\n2 0 0 0 bb 0\n3 0 4 2352 /abs/c.bin 0\n"
        reading = read({"Game.gdi": gdi}, FLYCAST, "Game.gdi")
        assert opened(reading)[0].path == at("/abs/c.bin")

    def test_a_one_byte_name_takes_the_next_field_with_it(self):
        reading = read({"Game.gdi": "3\n1 0 4 2352 a 0\n"}, FLYCAST, "Game.gdi")
        assert opened(reading)[0].path == at("a0")

    def test_a_count_past_the_tracks_listed_leaves_the_reading_undefined(self):
        reading = read({"Game.gdi": "4\n" + GDI[2:], **GDI_TRACKS}, FLYCAST, "Game.gdi")
        assert reading.outcome == disc_opens.UNESTABLISHED
        assert codes(reading) == [disc_opens.CAVEAT_OPENS_CONTENT_UNREAD]

    def test_a_field_that_is_no_number_leaves_the_reading_undefined(self):
        reading = read({"Game.gdi": "3\n1 0 x 2352 a.bin 0\n"}, FLYCAST, "Game.gdi")
        assert reading.outcome == disc_opens.UNESTABLISHED

    def test_a_quote_no_quote_ends_is_refused(self):
        reading = read({"Game.gdi": '3\n1 0 4 2352 "a.bin 0\n'}, FLYCAST, "Game.gdi")
        assert reading.outcome == disc_opens.REFUSES
        assert codes(reading) == [disc_opens.CAVEAT_OPENS_SHEET_REFUSED]

    def test_no_track_count_is_refused(self):
        reading = read({"Game.gdi": "tracks\n"}, FLYCAST, "Game.gdi")
        assert reading.outcome == disc_opens.REFUSES
        assert "declares no track" in reading.caveats[0].message


class TestFlycastsCue:
    def test_three_tracks_open_each_at_its_index(self):
        reading = read({"Game.cue": THREE, **THREE_FILES}, FLYCAST)
        assert [path for path, _, _ in listed(reading)] == ["T1.bin", "T2.bin", "T3.bin"]
        assert reading.outcome == disc_opens.ACCEPTS

    def test_two_tracks_without_a_session_are_refused(self):
        reading = read({"Game.cue": dc_cue("T1.bin", "T2.bin"), **THREE_FILES}, FLYCAST)
        assert reading.outcome == disc_opens.REFUSES
        assert codes(reading) == [disc_opens.CAVEAT_OPENS_SHEET_REFUSED]

    def test_a_session_line_lifts_the_three_track_floor(self):
        sheet = dc_cue("T1.bin", head="REM SESSION 01\n")
        assert read({"Game.cue": sheet, **THREE_FILES}, FLYCAST).outcome == disc_opens.ACCEPTS

    def test_the_file_type_is_never_checked(self):
        sheet = THREE.replace("BINARY", "WAVE")
        assert read({"Game.cue": sheet, **THREE_FILES}, FLYCAST).outcome == disc_opens.ACCEPTS

    def test_a_track_mode_without_a_sector_size_refuses_after_its_file_opened(self):
        sheet = dc_cue("T1.bin", "T2.bin", "T3.bin").replace("TRACK 02 MODE1/2352", "TRACK 02 MODE9/1")
        reading = read({"Game.cue": sheet, **THREE_FILES}, FLYCAST)
        assert listed(reading)[-1] == ("T2.bin", HOW_NAMED, True)
        assert codes(reading) == [disc_opens.CAVEAT_OPENS_TRACK_REFUSED]
        assert reading.caveats[0].data["type"] == "MODE9/1"

    def test_a_quoted_name_is_added_to_the_one_before_it(self):
        sheet = 'FILE "A" BINARY\n' + THREE
        reading = read({"Game.cue": sheet, "AT1.bin": BIN, "T2.bin": BIN, "T3.bin": BIN}, FLYCAST)
        assert listed(reading)[0] == ("AT1.bin", HOW_NAMED, True)

    def test_an_index_with_no_number_leaves_the_reading_undefined(self):
        reading = read({"Game.cue": 'FILE "T1.bin" BINARY\n TRACK 01 MODE1/2352\n INDEX\n', **THREE_FILES}, FLYCAST)
        assert reading.outcome == disc_opens.UNESTABLISHED

    def test_a_nul_ends_the_sheet(self):
        sheet = THREE + '\x00FILE "Gone.bin" BINARY\n  TRACK 04 MODE1/2352\n    INDEX 01 00:00:00\n'
        assert read({"Game.cue": sheet, **THREE_FILES}, FLYCAST).outcome == disc_opens.ACCEPTS

    def test_a_sheet_of_65536_bytes_is_refused_by_its_size(self):
        sheet = THREE + " " * (65536 - len(THREE))
        assert read({"Game.cue": sheet, **THREE_FILES}, FLYCAST).outcome == disc_opens.REFUSES
        assert read({"Game.cue": sheet[:-1], **THREE_FILES}, FLYCAST).outcome == disc_opens.ACCEPTS


class TestFlycastsPlaylist:
    def test_a_name_past_127_bytes_is_no_playlist(self):
        name = "n" * 124 + ".m3u"
        reading = read({name: "Game.gdi\n"}, FLYCAST, name)
        assert reading.outcome == disc_opens.REFUSES
        assert codes(reading) == [disc_opens.CAVEAT_OPENS_FORMAT_REFUSED]
        assert read({name[1:]: "Game.gdi\n", "Game.gdi": GDI, **GDI_TRACKS}, FLYCAST, name[1:]).outcome == "accepts"

    @pytest.mark.parametrize(("suffix", "outcome"), [(".M3U", disc_opens.ACCEPTS), (".M3u", disc_opens.REFUSES)])
    def test_the_suffix_is_matched_in_two_spellings(self, suffix, outcome):
        reading = read({f"List{suffix}": "Game.gdi\n", "Game.gdi": GDI, **GDI_TRACKS}, FLYCAST, f"List{suffix}")
        assert reading.outcome == outcome

    def test_a_line_is_read_as_read_m3u_reads_it(self):
        text = ' #x.gdi\r\n#Comment\n"/abs/Disc.gdi"\n'
        reading = read({"List.m3u": text, " #x.gdi": GDI, **GDI_TRACKS}, FLYCAST, "List.m3u")
        assert [o.path for o in opened(reading)][::4] == [at(" #x.gdi"), "/abs/Disc.gdi"]

    def test_the_first_disc_stops_the_launch_and_a_later_one_does_not(self):
        reading = read({"List.m3u": "Gone.gdi\n"}, FLYCAST, "List.m3u")
        assert reading.outcome == disc_opens.REFUSES
        files = {"List.m3u": "Game.gdi\nGone.gdi\n", "Game.gdi": GDI, **GDI_TRACKS}
        later = read(files, FLYCAST, "List.m3u")
        assert later.outcome == disc_opens.ACCEPTS
        assert codes(later) == [disc_opens.CAVEAT_OPENS_FILE_MISSING]

    def test_a_nested_playlist_is_no_disc(self):
        files = {"List.m3u": "Inner.m3u\n", "Inner.m3u": "Game.gdi\n"}
        assert codes(read(files, FLYCAST, "List.m3u")) == [disc_opens.CAVEAT_OPENS_FORMAT_REFUSED]
        files = {"List.m3u": "Game.gdi\nInner.m3u\n", "Inner.m3u": "x\n", "Game.gdi": GDI, **GDI_TRACKS}
        reading = read(files, FLYCAST, "List.m3u")
        assert (reading.outcome, codes(reading)) == (disc_opens.ACCEPTS, [disc_opens.CAVEAT_OPENS_FORMAT_REFUSED])

    def test_an_arcade_name_at_launch_is_not_followed(self):
        reading = read({"List.m3u": "game.zip\n", "game.zip": BIN}, FLYCAST, "List.m3u")
        assert codes(reading) == [disc_opens.CAVEAT_OPENS_FORMAT_UNREAD]

    def test_an_empty_playlist_is_refused(self):
        assert read({"List.m3u": "#\n\n\"\"\n"}, FLYCAST, "List.m3u").outcome == disc_opens.REFUSES

    def test_a_relative_line_joins_the_directory_cut_to_1023_bytes(self):
        deep = "/d" * 600
        machine = FixtureMachine({f"{deep}/List.m3u": "Game.gdi\n"})
        reading = disc_opens.read_opens(machine, FLYCAST, "Entry", f"{deep}/List.m3u")
        assert reading is not None
        kept = deep[:1023]
        assert opened(reading)[0].path == kept[: kept.rfind("/")] + "/Game.gdi"

    def test_a_disc_path_is_cut_to_4095_bytes(self):
        reading = read({"List.m3u": "x" * 4090 + "\n"}, FLYCAST, "List.m3u")
        assert opened(reading)[0].path == at("x" * (4095 - len(ROOT) - 1))

    def test_a_launched_chd_is_not_read(self):
        assert reading_of({"Game.chd": BIN}, FLYCAST, "Game.chd") is None


class TestTheCppStream:
    @pytest.mark.parametrize(
        ("text", "value", "failed"),
        [(b"-1", 4294967295, False), (b"+7", 7, False), (b"4294967296", 4294967295, True), (b"x", 0, True),
         (b"9" * 5000, 4294967295, True), (b"007", 7, False)],
    )
    def test_an_unsigned_field_reads_as_libstdcxx_reads_it(self, text, value, failed):
        stream = disc_opens._CppStream(text)  # pyright: ignore[reportPrivateUsage]
        assert (stream.number(32, signed=False), stream.failed) == (value, failed)

    def test_the_c_locale_spaces_part_the_fields(self):
        stream = disc_opens._CppStream(b"\x0b\x0c7\r\x0bxy\x0cz \x00")  # pyright: ignore[reportPrivateUsage]
        fields = (stream.number(32, signed=False), stream.word(), stream.char(), stream.word())
        assert fields == (7, b"xy", b"z", b"\x00")

    def test_a_stream_at_its_end_keeps_the_target(self):
        stream = disc_opens._CppStream(b"  ")  # pyright: ignore[reportPrivateUsage]
        assert stream.number(32, signed=False) is None


# ── Genesis Plus GX ────────────────────────────────────────────────────────

SEGA = (
    'FILE "Game.bin" BINARY\n  TRACK 01 MODE1/2048\n    INDEX 01 00:00:00\n'
    'FILE "Game (Track 2).bin" BINARY\n  TRACK 02 AUDIO\n    INDEX 00 00:00:00\n    INDEX 01 00:02:00\n'
)
SEGA_FILES = {"Game.bin": BIN, "Game (Track 2).bin": BIN}


class TestGenesisPlusGxsCue:
    def test_each_file_opens_and_the_sub_follows_the_last(self):
        reading = read({"Game.cue": SEGA, **SEGA_FILES}, GENESIS_PLUS_GX)
        assert listed(reading) == [
            ("Game.bin", HOW_NAMED, True),
            ("Game (Track 2).bin", HOW_NAMED, True),
            ("Game (Track 2).sub", HOW_NAME_DERIVED, False),
        ]
        assert reading.outcome == disc_opens.ACCEPTS

    def test_a_missing_first_file_loads_the_sheet_as_a_cartridge(self):
        reading = read({"Game.cue": SEGA, "Game (Track 2).bin": BIN}, GENESIS_PLUS_GX)
        assert reading.outcome == disc_opens.REFUSES
        assert "cartridge" in reading.caveats[0].message
        assert listed(reading) == [("Game.bin", HOW_NAMED, False)]

    def test_a_missing_later_file_ends_the_parse_and_the_disc_mounts(self):
        reading = read({"Game.cue": SEGA, "Game.bin": BIN}, GENESIS_PLUS_GX)
        assert reading.outcome == disc_opens.ACCEPTS
        assert codes(reading) == [disc_opens.CAVEAT_OPENS_FILE_MISSING]
        assert listed(reading)[-1] == ("Game (Track 2).sub", HOW_NAME_DERIVED, False)

    def test_an_audio_type_is_decided_by_bytes(self):
        sheet = SEGA.replace('2).bin" BINARY', '2).bin" WAVE')
        reading = read({"Game.cue": sheet, **SEGA_FILES}, GENESIS_PLUS_GX)
        assert (reading.outcome, codes(reading)) == (disc_opens.ACCEPTS, [disc_opens.CAVEAT_OPENS_CONTENT_UNREAD])
        first = read({"Game.cue": SEGA.replace("Game.bin\" BINARY", "Game.bin\" OGG"), **SEGA_FILES}, GENESIS_PLUS_GX)
        assert first.outcome == disc_opens.UNESTABLISHED

    def test_a_name_is_written_after_the_last_separator_of_the_path_before_it(self):
        sheet = SEGA.replace('"Game.bin"', '"sub/Game.bin"')
        reading = read({"Game.cue": sheet, "sub/Game.bin": BIN, "sub/Game (Track 2).bin": BIN}, GENESIS_PLUS_GX)
        assert [path for path, _, _ in listed(reading)][:2] == ["sub/Game.bin", "sub/Game (Track 2).bin"]

    def test_only_spaces_lead_a_line(self):
        reading = read({"Game.cue": SEGA.replace("    INDEX", "\tINDEX"), **SEGA_FILES}, GENESIS_PLUS_GX)
        assert codes(reading) == [disc_opens.CAVEAT_OPENS_SHEET_REFUSED]

    def test_a_built_path_is_cut_at_255_bytes(self):
        deep = "/d" * 80
        machine = FixtureMachine({f"{deep}/Game.cue": SEGA.replace("Game.bin", "n" * 100 + ".bin")})
        reading = disc_opens.read_opens(machine, GENESIS_PLUS_GX, "Entry", f"{deep}/Game.cue")
        assert reading is not None
        assert opened(reading)[0].path == f"{deep}/" + "n" * (255 - len(deep) - 1)

    def test_a_line_is_a_127_byte_piece(self):
        long_name = "n" * 150 + ".bin"
        reading = read({"Game.cue": SEGA.replace("Game.bin", long_name)}, GENESIS_PLUS_GX)
        assert opened(reading)[0].path == at("n" * (127 - len('FILE "')))

    def test_a_name_no_stop_ends_reads_its_type_past_the_line(self):
        sheet = SEGA.replace('FILE "Game.bin" BINARY\n', "FILE Game.bin\n")
        reading = read({"Game.cue": sheet, "Game.bin\n": BIN, **SEGA_FILES}, GENESIS_PLUS_GX)
        assert opened(reading)[0].path == at("Game.bin\n")
        assert reading.outcome == disc_opens.UNESTABLISHED

    def test_a_line_index_00_takes_ends_no_track(self):
        sheet = SEGA.replace("INDEX 01 00:00:00", "INDEX 012:34:56").replace("INDEX 01 00:02:00", "INDEX 012:34:56")
        assert codes(read({"Game.cue": sheet, **SEGA_FILES}, GENESIS_PLUS_GX)) == ["opens-sheet-refused"]

    def test_a_loop_command_on_the_line_takes_it(self):
        sheet = SEGA.replace(":00\n", ":00 REM LOOP\n")
        assert codes(read({"Game.cue": sheet, **SEGA_FILES}, GENESIS_PLUS_GX)) == ["opens-sheet-refused"]

    def test_no_file_is_opened_past_99_tracks(self):
        tracks = "  TRACK 01 MODE1/2048\n  INDEX 01 00:00:00\n" * 99
        sheet = 'FILE "Game.bin" BINARY\n' + tracks + 'FILE "Late.bin" BINARY\n'
        reading = read({"Game.cue": sheet, **SEGA_FILES}, GENESIS_PLUS_GX)
        assert "Late.bin" not in [path for path, _, _ in listed(reading)]

    @pytest.mark.parametrize(
        "sheet",
        [
            "TRACK 01 MODE1/2352\n  INDEX 01 00:00:00\n",
            "TRACK 01 AUDIO\n  INDEX 01 00:00:00\n" + SEGA,
            "REM x\n  TRACK 01 MODE1/2048\n" + SEGA,
        ],
    )
    def test_a_first_track_no_file_opened_crashes_the_core(self, sheet):
        reading = read({"Game.cue": sheet, **SEGA_FILES}, GENESIS_PLUS_GX)
        assert (reading.outcome, codes(reading)) == (disc_opens.REFUSES, [disc_opens.CAVEAT_OPENS_SHEET_REFUSED])
        assert "crashes" in reading.caveats[0].message
        assert listed(reading) == []

    def test_an_audio_track_line_before_the_first_file_seeks_nothing(self):
        sheet = "TRACK 01 AUDIO\n" + SEGA
        assert read({"Game.cue": sheet, **SEGA_FILES}, GENESIS_PLUS_GX).outcome == disc_opens.ACCEPTS

    def test_a_sheet_path_of_255_bytes_is_not_established(self):
        name = "n" * (255 - len(ROOT) - 1 - 4) + ".cue"
        reading = read({name: SEGA, **SEGA_FILES}, GENESIS_PLUS_GX, name)
        assert (reading.outcome, codes(reading)) == (disc_opens.UNESTABLISHED, [disc_opens.CAVEAT_OPENS_PATH_TOO_LONG])

    def test_a_playlist_disc_of_256_bytes_is_not_established(self):
        name = "n" * (256 - len(ROOT) - 1 - 4) + ".cue"
        reading = read({"List.m3u": f"{name}\n", name: SEGA, **SEGA_FILES}, GENESIS_PLUS_GX, "List.m3u")
        assert (reading.outcome, codes(reading)) == (disc_opens.UNESTABLISHED, [disc_opens.CAVEAT_OPENS_PATH_TOO_LONG])

    def test_a_launched_path_of_256_bytes_is_refused(self):
        name = "n" * (256 - len(ROOT) - 1 - 4) + ".cue"
        reading = read({name: SEGA, **SEGA_FILES}, GENESIS_PLUS_GX, name)
        assert (reading.outcome, codes(reading)) == (disc_opens.REFUSES, [disc_opens.CAVEAT_OPENS_PATH_TOO_LONG])


class TestGenesisPlusGxsOtherFiles:
    def test_a_bin_looks_for_its_cue_and_its_bytes_decide_the_rest(self):
        reading = read({"Game.cue": SEGA, **SEGA_FILES}, GENESIS_PLUS_GX, "Game.bin")
        assert listed(reading) == [("Game.cue", HOW_NAME_DERIVED, True)]
        assert (reading.outcome, codes(reading)) == (disc_opens.UNESTABLISHED, [disc_opens.CAVEAT_OPENS_CONTENT_UNREAD])

    def test_without_a_cue_a_file_a_cartridge_may_be_loads_either_way(self):
        reading = read({"Game.bin": {"size": 1 << 20}}, GENESIS_PLUS_GX, "Game.bin")
        assert listed(reading) == [("Game.cue", HOW_NAME_DERIVED, False)]
        assert (reading.outcome, codes(reading)) == (disc_opens.ACCEPTS, [disc_opens.CAVEAT_OPENS_CONTENT_UNREAD])

    @pytest.mark.parametrize("size", [0, (10 << 20) + 1])
    def test_without_a_cue_a_file_no_cartridge_load_takes_is_decided_by_its_bytes(self, size):
        reading = read({"Game.bin": {"size": size}}, GENESIS_PLUS_GX, "Game.bin")
        assert reading.outcome == disc_opens.UNESTABLISHED

    def test_a_mixed_case_sheet_is_loaded_as_a_cartridge(self):
        reading = read({"Game.Cue": SEGA, "Game.cue": SEGA, **SEGA_FILES}, GENESIS_PLUS_GX, "Game.Cue")
        assert listed(reading)[0] == ("Game.cue", HOW_NAME_DERIVED, True)
        assert "Game.bin" in [path for path, _, _ in listed(reading)]
        assert (reading.outcome, codes(reading)) == (disc_opens.REFUSES, [disc_opens.CAVEAT_OPENS_FORMAT_REFUSED])

    def test_the_cue_of_a_cartridges_name_crashes_the_core_too(self):
        crash = "TRACK 01 MODE1/2352\n  INDEX 01 00:00:00\n"
        reading = read({"Game.Cue": SEGA, "Game.cue": crash, **SEGA_FILES}, GENESIS_PLUS_GX, "Game.Cue")
        assert (reading.outcome, codes(reading)) == (disc_opens.REFUSES, [disc_opens.CAVEAT_OPENS_SHEET_REFUSED])
        assert "crashes" in reading.caveats[0].message
        assert listed(reading) == [("Game.cue", HOW_NAME_DERIVED, True)]

    def test_the_cue_of_a_name_is_cut_at_a_dot_at_byte_251_or_before(self):
        stem = disc_opens._gpgx_stem_cue  # pyright: ignore[reportPrivateUsage]
        assert stem("/roms/a.b/Game") == "/roms/a.cue"
        assert stem("/" + "x" * 250 + ".bin") == "/" + "x" * 250 + ".cue"
        assert stem("/" + "x" * 252 + ".bin") == ".cue"

    def test_a_launched_chd_is_not_read(self):
        assert reading_of({"Game.chd": BIN}, GENESIS_PLUS_GX, "Game.chd") is None


class TestGenesisPlusGxsPlaylist:
    def test_past_four_discs_the_lines_are_ignored(self):
        text = "".join(f"D{n}.cue\n" for n in range(1, 6))
        reading = read({"List.m3u": text}, GENESIS_PLUS_GX, "List.m3u")
        assert "D5.cue" not in [path for path, _, _ in listed(reading)]
        assert disc_opens.CAVEAT_OPENS_ENTRY_IGNORED in codes(reading)

    def test_four_discs_leave_nothing_ignored(self):
        text = "".join(f"D{n}.cue\n" for n in range(1, 5))
        assert disc_opens.CAVEAT_OPENS_ENTRY_IGNORED not in codes(read({"List.m3u": text}, GENESIS_PLUS_GX, "List.m3u"))

    def test_a_blank_line_is_a_disc_at_the_directory(self):
        files = {"List.m3u": "Game.cue\n\r\n", "Game.cue": SEGA, **SEGA_FILES}
        reading = read(files, GENESIS_PLUS_GX, "List.m3u")
        assert opened(reading)[-1].path == ROOT + "/"

    def test_every_line_joins_the_directory(self):
        reading = read({"List.m3u": "/abs/Game.cue\n"}, GENESIS_PLUS_GX, "List.m3u")
        assert opened(reading)[0].path == at("/abs/Game.cue")

    def test_an_upper_case_suffix_is_a_playlist(self):
        files = {"List.M3U": "Game.cue\n", "Game.cue": SEGA, **SEGA_FILES}
        assert read(files, GENESIS_PLUS_GX, "List.M3U").outcome == disc_opens.ACCEPTS

    def test_a_missing_first_disc_is_refused_and_a_later_one_is_not(self):
        assert read({"List.m3u": "Gone.cue\n"}, GENESIS_PLUS_GX, "List.m3u").outcome == disc_opens.REFUSES
        files = {"List.m3u": "Game.cue\nGone.cue\n", "Game.cue": SEGA, **SEGA_FILES}
        assert read(files, GENESIS_PLUS_GX, "List.m3u").outcome == disc_opens.ACCEPTS

    def test_a_nested_playlist_is_loaded_as_a_cartridge(self):
        files = {"List.m3u": "Inner.m3u\n", "Inner.m3u": "Game.cue\n"}
        reading = read(files, GENESIS_PLUS_GX, "List.m3u")
        assert codes(reading) == [disc_opens.CAVEAT_OPENS_FORMAT_REFUSED]

    def test_a_disc_switch_loads_no_cartridge(self):
        files = {"List.m3u": "Game.cue\nOther.Cue\nDisc.bin\nGone.cue\n", "Game.cue": SEGA, "Other.Cue": SEGA,
                 "Disc.bin": {"size": 1 << 20}, "Gone.cue": SEGA.replace("Game.bin", "Lost.bin"), **SEGA_FILES}
        reading = read(files, GENESIS_PLUS_GX, "List.m3u")
        assert reading.outcome == disc_opens.ACCEPTS
        switched = [c.message for c in reading.caveats if c.data["file"] != at("Game (Track 2).sub")]
        assert all("as a cartridge" not in message for message in switched)
        assert sum("disc switch" in message for message in switched) == 3

    def test_the_cue_of_a_switched_discs_name_loads_no_cartridge_either(self):
        files = {"List.m3u": "Game.cue\nOther.Cue\nThird.Cue\n", "Game.cue": SEGA, "Other.Cue": SEGA,
                 "Other.cue": SEGA.replace("Game.bin", "Lost.bin"), "Third.Cue": SEGA,
                 "Third.cue": "TRACK 01 AUDIO\n  INDEX 01 00:00:00\n", **SEGA_FILES}
        reading = read(files, GENESIS_PLUS_GX, "List.m3u")
        assert reading.outcome == disc_opens.ACCEPTS
        by_file = {c.data["file"]: c.message for c in reading.caveats}
        assert "as a cartridge" not in by_file[at("Lost.bin")]
        assert "crashes" in by_file[at("Third.cue")]
        assert at("Third.Cue") not in by_file

    def test_a_playlist_of_comments_is_refused(self):
        assert read({"List.m3u": "#a\n#b\n"}, GENESIS_PLUS_GX, "List.m3u").outcome == disc_opens.REFUSES


@pytest.mark.parametrize(
    ("text", "fmt", "count"),
    [
        (b"INDEX 01 00:02:00", b"INDEX 01 %02d:%02d:%02d", 3),
        (b"INDEX 01:02:03", b"INDEX 0 %02d:%02d:%02d", 3),
        (b"INDEX 01 0:2", b"INDEX 01 %02d:%02d:%02d", 2),
        (b"", b"INDEX 01 %02d:%02d:%02d", -1),
        (b"INDEX 01 x", b"INDEX 01 %02d:%02d:%02d", 0),
        (b"INDEX 01 123:00:00", b"INDEX 01 %02d:%02d:%02d", 1),
    ],
)
def test_the_track_patterns_count_as_sscanf_counts(text, fmt, count):
    assert disc_opens._scanf_count(text, fmt) == count  # pyright: ignore[reportPrivateUsage]


class TestTheNewWalksAreBounded:
    def test_a_flycast_sheet_past_the_track_bound_stops_short(self, monkeypatch):
        monkeypatch.setattr(disc_opens, "MAX_TRACKS", 2)
        reading = read({"Game.cue": THREE, **THREE_FILES}, FLYCAST)
        assert codes(reading) == [disc_opens.CAVEAT_OPENS_BOUND_EXCEEDED]

    def test_a_gdi_count_past_the_track_bound_stops_short(self, monkeypatch):
        monkeypatch.setattr(disc_opens, "MAX_TRACKS", 2)
        assert codes(read({"Game.gdi": GDI, **GDI_TRACKS}, FLYCAST, "Game.gdi")) == ["opens-bound-exceeded"]

    def test_every_flycast_playlist_line_is_a_step(self):
        text = "#\n" * disc_opens.MAX_STEPS + "Game.gdi\n"
        assert codes(read({"List.m3u": text}, FLYCAST, "List.m3u")) == [disc_opens.CAVEAT_OPENS_BOUND_EXCEEDED]

    def test_every_genesis_sheet_piece_is_a_step(self):
        sheet = SEGA + "\n" * disc_opens.MAX_STEPS
        assert codes(read({"Game.cue": sheet, **SEGA_FILES}, GENESIS_PLUS_GX)) == ["opens-bound-exceeded"]

    def test_many_switch_discs_reach_the_listed_bound(self, monkeypatch):
        monkeypatch.setattr(disc_opens, "MAX_LISTED", 50)
        text = "Game.gdi\n" + "".join(f"D{n}.gdi\n" for n in range(100))
        files = {"List.m3u": text, "Game.gdi": GDI, **GDI_TRACKS}
        assert codes(read(files, FLYCAST, "List.m3u"))[-1] == disc_opens.CAVEAT_OPENS_BOUND_EXCEEDED

    def test_many_genesis_files_reach_the_output_bound(self, monkeypatch):
        monkeypatch.setattr(disc_opens, "MAX_OUTPUT", 2000)
        sheet = "".join(f'FILE "{"n" * 100}{n}.bin" BINARY\n' for n in range(40))
        files = {"Game.cue": sheet, **{f"{'n' * 100}{n}.bin": BIN for n in range(40)}}
        assert codes(read(files, GENESIS_PLUS_GX))[-1] == disc_opens.CAVEAT_OPENS_BOUND_EXCEEDED
