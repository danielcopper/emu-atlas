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
