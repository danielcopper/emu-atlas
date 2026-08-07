"""Tests for atlas.installations — handles, health, and the live resolver."""

from __future__ import annotations

import json
from collections import Counter

import pytest

import atlas
from atlas.firmware import resolve_links
from atlas.machine import FixtureMachine
from atlas.machine import SYMLINK_HOPS

HOME = "/home/deck"
RETRODECK_JSON = f"{HOME}/.var/app/net.retrodeck.retrodeck/config/retrodeck/retrodeck.json"
RETRODECK_CFG = f"{HOME}/.var/app/net.retrodeck.retrodeck/config/retroarch/retroarch.cfg"
RETRODECK_OVERRIDES = f"{HOME}/.var/app/net.retrodeck.retrodeck/config/retroarch/config"
EMUDECK_SETTINGS = f"{HOME}/.config/EmuDeck/settings.sh"
STANDALONE_CFG = f"{HOME}/.var/app/org.libretro.RetroArch/config/retroarch/retroarch.cfg"

RD_JSON = '{"paths": {"rd_home_path": "/mnt/sd/retrodeck", "saves_path": "/mnt/sd/retrodeck/saves"}}'
RD_DEPLOY_CORES = "/var/lib/flatpak/app/net.retrodeck.retrodeck/current/active/files/cores"


def _retrodeck(files, **kwargs):
    machine = FixtureMachine(files, **kwargs)
    return atlas.RetroDeck(HOME, machine)


class TestRetroDeckPaths:
    def test_roots_from_json(self):
        rd = _retrodeck({RETRODECK_JSON: RD_JSON})
        assert rd.root() == "/mnt/sd/retrodeck"
        assert rd.saves_root() == "/mnt/sd/retrodeck/saves"

    def test_fallback_roots_when_json_lacks_paths(self):
        rd = _retrodeck({RETRODECK_JSON: "{}"})
        assert rd.root() == f"{HOME}/retrodeck"
        assert rd.bios_dir() == f"{HOME}/retrodeck/bios"


class TestMarkerPathValuesMustBeStrings:
    """A marker with a non-string path is present and broken, not usable.

    ``retrodeck.json`` is editable and drives every read that follows. Handing
    a non-string on unchecked made ``health()`` raise ``AttributeError`` on the
    fixture seam and ``TypeError`` from ``os.stat`` on the real one — and
    ``os.stat(123)`` reads an *int* as a file descriptor, so it can even answer
    for something that is not a path at all.
    """

    FALLBACK_DIRS = [f"{HOME}/retrodeck", f"{HOME}/retrodeck/saves"]

    def test_non_string_path_value_makes_the_marker_invalid(self):
        rd = _retrodeck({RETRODECK_JSON: '{"paths": {"rd_home_path": 123}}'}, dirs=self.FALLBACK_DIRS)
        assert rd.health().codes == (atlas.HEALTH_ISSUE_MARKER_INVALID,)

    def test_the_offending_key_is_named(self):
        rd = _retrodeck({RETRODECK_JSON: '{"paths": {"rd_home_path": "/mnt/sd/rd", "saves_path": {"a": 1}}}'})
        issue = rd.health().issues[0]
        assert issue.code == atlas.HEALTH_ISSUE_MARKER_INVALID
        assert issue.data["key"] == "paths.saves_path"

    def test_health_does_not_raise_and_roots_stay_strings(self):
        rd = _retrodeck({RETRODECK_JSON: '{"paths": {"rd_home_path": 123, "saves_path": {"a": 1}}}'})
        assert rd.health().codes[0] == atlas.HEALTH_ISSUE_MARKER_INVALID
        for value in (rd.root(), rd.saves_root(), rd.bios_dir(), rd.roms_dir()):
            assert isinstance(value, str)
        assert rd.root() == f"{HOME}/retrodeck"

    def test_a_paths_section_of_the_wrong_type_is_invalid_too(self):
        rd = _retrodeck({RETRODECK_JSON: '{"paths": ["/mnt/sd/retrodeck"]}'})
        issue = rd.health().issues[0]
        assert issue.code == atlas.HEALTH_ISSUE_MARKER_INVALID
        assert issue.data["key"] == "paths"

    def test_a_null_path_value_is_invalid(self):
        # JSON null is not "unset" here: RetroDECK writes the key or omits it.
        rd = _retrodeck({RETRODECK_JSON: '{"paths": {"saves_path": null}}'}, dirs=self.FALLBACK_DIRS)
        assert rd.health().codes == (atlas.HEALTH_ISSUE_MARKER_INVALID,)

    def test_a_marker_without_a_paths_section_is_not_invalid(self):
        rd = _retrodeck({RETRODECK_JSON: '{"version": "0.10.9b"}'}, dirs=[f"{HOME}/retrodeck"])
        assert rd.health().codes == (atlas.HEALTH_ISSUE_SAVES_ROOT_MISSING,)

    def test_a_null_paths_section_is_invalid_not_absent(self):
        # Omitting the section and writing null in it are different states: the
        # second is a section no key can be read from, and null is not an object.
        rd = _retrodeck({RETRODECK_JSON: '{"paths": null}'}, dirs=self.FALLBACK_DIRS)
        issue = rd.health().issues[0]
        assert issue.code == atlas.HEALTH_ISSUE_MARKER_INVALID
        assert issue.data["key"] == "paths"

    def test_every_path_key_atlas_reads_is_checked(self):
        # Keeps the checked set honest against the read set: root(), saves_root(),
        # bios_dir() and roms_dir() are the four reads, so all four are validated.
        for key in ("rd_home_path", "saves_path", "bios_path", "roms_path"):
            rd = _retrodeck({RETRODECK_JSON: json.dumps({"paths": {key: 5}})}, dirs=self.FALLBACK_DIRS)
            assert rd.health().codes == (atlas.HEALTH_ISSUE_MARKER_INVALID,), key

    def test_a_key_atlas_does_not_read_is_none_of_its_business(self):
        # atlas reports on what it reads. A value under a key no read touches
        # says nothing about this installation, and calling the marker broken
        # over it would be a claim about who wrote the file — the day RetroDECK
        # nests something new under paths, a healthy install must stay healthy.
        rd = _retrodeck(
            {
                RETRODECK_JSON: (
                    '{"paths": {"rd_home_path": "/mnt/sd/retrodeck", '
                    '"saves_path": "/mnt/sd/retrodeck/saves", "videos_path": {"nested": 1}}}'
                )
            },
            dirs=["/mnt/sd/retrodeck/saves"],
        )
        assert rd.health() == atlas.Health()
        assert rd.root() == "/mnt/sd/retrodeck"

    def test_placement_carries_the_marker_issue_instead_of_crashing(self):
        rd = _retrodeck(
            {
                RETRODECK_JSON: '{"paths": {"saves_path": 7}}',
                RETRODECK_CFG: (
                    'savefile_directory = "/mnt/sd/retrodeck/saves"\n'
                    'sort_savefiles_by_content_enable = "false"\nsort_savefiles_enable = "false"\n'
                ),
                "/mnt/sd/retrodeck/saves/.keep": "",
            },
            dirs=self.FALLBACK_DIRS,
        )
        p = rd.save_location(content_path="/mnt/sd/retrodeck/roms/gba/Game.zip")
        assert p.dir == "/mnt/sd/retrodeck/saves"
        # The finding rides in the placement under its own code, carrying the
        # key it is about — not wrapped in a category with the condition nested.
        issue = next(c for c in p.caveats if c.code == atlas.HEALTH_ISSUE_MARKER_INVALID)
        assert issue.data["key"] == "paths.saves_path"


class TestRetroDeckSaveLocation:
    def test_cfg_is_the_truth_not_json(self):
        # The cfg is what RetroArch reads; a user-edited cfg wins over retrodeck.json.
        rd = _retrodeck(
            {
                RETRODECK_JSON: RD_JSON,
                RETRODECK_CFG: (
                    'savefile_directory = "/elsewhere/saves"\n'
                    'sort_savefiles_by_content_enable = "false"\nsort_savefiles_enable = "false"\n'
                ),
                "/elsewhere/saves/.keep": "",
                "/mnt/sd/retrodeck/roms/gba/Game.zip": "",
            }
        )
        p = rd.save_location(content_path="/mnt/sd/retrodeck/roms/gba/Game.zip")
        assert p.dir == "/elsewhere/saves"

    def test_missing_cfg_uses_platform_default(self):
        # Platform defaults are initialized before config load
        # (platform_unix.c:2133-2134); upstream compile defaults sort by core.
        rd = _retrodeck(
            {RETRODECK_JSON: RD_JSON, "/mnt/sd/retrodeck/roms/gba/Game.zip": ""}
        )
        p = rd.save_location(content_path="/mnt/sd/retrodeck/roms/gba/Game.zip")
        assert p.dir == f"{HOME}/.var/app/net.retrodeck.retrodeck/config/retroarch/saves/<library_name>"
        assert p.root_kind == atlas.ROOT_SAVEFILE_DIRECTORY
        assert p.needs == ("library_name",)

    def test_core_override_applied(self):
        rd = _retrodeck(
            {
                RETRODECK_JSON: RD_JSON,
                RETRODECK_CFG: (
                    'savefile_directory = "/mnt/sd/retrodeck/saves"\n'
                    'sort_savefiles_by_content_enable = "true"\nsort_savefiles_enable = "false"\n'
                    'libretro_directory = "/app/cores"\n'
                ),
                f"{RETRODECK_OVERRIDES}/PPSSPP/PPSSPP.cfg": 'sort_savefiles_by_content_enable = "false"',
                "/mnt/sd/retrodeck/saves/.keep": "",
                "/mnt/sd/retrodeck/roms/psp/Game.iso": "",
            },
            cores={f"{RD_DEPLOY_CORES}/ppsspp_libretro.so": {"library_name": "PPSSPP"}},
        )
        p = rd.save_location(
            content_path="/mnt/sd/retrodeck/roms/psp/Game.iso", core_so="ppsspp_libretro.so"
        )
        assert p.dir == "/mnt/sd/retrodeck/saves"
        assert any("override wins" in s for s in p.sources)

    def test_library_name_from_binary_not_filename(self):
        rd = _retrodeck(
            {
                RETRODECK_JSON: RD_JSON,
                RETRODECK_CFG: (
                    'savefile_directory = "/mnt/sd/retrodeck/saves"\n'
                    'sort_savefiles_by_content_enable = "false"\n'
                    'sort_savefiles_enable = "true"\n'
                    'libretro_directory = "/app/cores"\n'
                ),
                "/mnt/sd/retrodeck/saves/.keep": "",
                "/mnt/sd/retrodeck/roms/gba/Game.zip": "",
            },
            cores={f"{RD_DEPLOY_CORES}/mgba_libretro.so": {"library_name": "mGBA"}},
        )
        p = rd.save_location(
            content_path="/mnt/sd/retrodeck/roms/gba/Game.zip", core_so="mgba_libretro.so"
        )
        assert p.dir == "/mnt/sd/retrodeck/saves/mGBA"

    def test_unqueryable_core_leaves_hole_and_caveat(self):
        rd = _retrodeck(
            {
                RETRODECK_JSON: RD_JSON,
                RETRODECK_CFG: (
                    'savefile_directory = "/mnt/sd/retrodeck/saves"\n'
                    'sort_savefiles_by_content_enable = "false"\n'
                    'sort_savefiles_enable = "true"\n'
                    'libretro_directory = "/app/cores"\n'
                ),
                "/mnt/sd/retrodeck/saves/.keep": "",
                "/mnt/sd/retrodeck/roms/apple2/game.dsk": "",
            },
            cores={f"{RD_DEPLOY_CORES}/applewin_libretro.so": None},
        )
        p = rd.save_location(
            content_path="/mnt/sd/retrodeck/roms/apple2/game.dsk", core_so="applewin_libretro.so"
        )
        assert p.dir == "/mnt/sd/retrodeck/saves/<library_name>"
        assert p.needs == ("library_name",)
        assert any(c.code == atlas.CAVEAT_CORE_UNQUERYABLE for c in p.caveats)

    def test_observed_file_set(self):
        rd = _retrodeck(
            {
                RETRODECK_JSON: RD_JSON,
                RETRODECK_CFG: (
                    'savefile_directory = "/mnt/sd/retrodeck/saves"\n'
                    'sort_savefiles_by_content_enable = "true"\nsort_savefiles_enable = "false"\n'
                ),
                "/mnt/sd/retrodeck/roms/n64/Paper Mario (USA).zip": "",
                "/mnt/sd/retrodeck/saves/n64/Paper Mario (USA).srm": "sram",
            }
        )
        p = rd.save_location(content_path="/mnt/sd/retrodeck/roms/n64/Paper Mario (USA).zip")
        assert p.file_set.state == "observed"
        assert p.file_set.files == ("Paper Mario (USA).srm",)

    def test_no_files_is_unknown_not_guessed(self):
        rd = _retrodeck(
            {
                RETRODECK_JSON: RD_JSON,
                RETRODECK_CFG: (
                    'savefile_directory = "/mnt/sd/retrodeck/saves"\n'
                    'sort_savefiles_by_content_enable = "true"\nsort_savefiles_enable = "false"\n'
                ),
                "/mnt/sd/retrodeck/saves/.keep": "",
                "/mnt/sd/retrodeck/roms/gba/Game.zip": "",
            }
        )
        p = rd.save_location(content_path="/mnt/sd/retrodeck/roms/gba/Game.zip")
        assert p.file_set.state == "unknown"
        assert p.file_set.files == ()

    def test_health_caveat_on_missing_root(self):
        rd = _retrodeck(
            {
                RETRODECK_JSON: '{"paths": {"rd_home_path": "/run/media/gone/retrodeck"}}',
            }
        )
        p = rd.save_location()
        issue = next(c for c in p.caveats if c.code == atlas.HEALTH_ISSUE_ROOT_MISSING)
        assert issue.data["path"] == "/run/media/gone/retrodeck"

    def test_the_placement_carries_the_health_findings_unchanged(self):
        # Equality, deliberately not identity: handles are live, so health()
        # and save_location() each read their own snapshot and build their own
        # Caveat objects — `is` would be false by design, not by defect. What
        # equality pins is everything a re-wrap would disturb, message
        # included: the retired envelope carried a different code, a nested
        # data["issue"], and an "installation health: " prefix, so any of the
        # three coming back fails this.
        rd = _retrodeck({RETRODECK_JSON: '{"paths": {"rd_home_path": "/run/media/gone/retrodeck"}}'})
        findings = rd.health().issues
        assert [c for c in rd.save_location().caveats if c in findings] == list(findings)

    def test_a_finding_reaches_the_placement_with_its_own_message(self):
        # The prefix the envelope added is the cheapest tell that something
        # rebuilt the finding on the way.
        rd = _retrodeck({RETRODECK_JSON: '{"paths": {"rd_home_path": "/run/media/gone/retrodeck"}}'})
        finding = rd.health().issues[0]
        carried = next(c for c in rd.save_location().caveats if c.code == finding.code)
        assert carried.message == finding.message

    def test_observation_is_literal_for_glob_metacharacters(self):
        # '[USA]' in a ROM name must match itself, never act as a class (M2).
        rd = _retrodeck(
            {
                RETRODECK_JSON: RD_JSON,
                RETRODECK_CFG: (
                    'savefile_directory = "/mnt/sd/retrodeck/saves"\n'
                    'sort_savefiles_by_content_enable = "false"\nsort_savefiles_enable = "false"\n'
                ),
                "/mnt/sd/retrodeck/roms/gba/Game [USA].zip": "",
                "/mnt/sd/retrodeck/saves/Game [USA].srm": "s",
                "/mnt/sd/retrodeck/saves/Game U.srm": "decoy",
            }
        )
        p = rd.save_location(content_path="/mnt/sd/retrodeck/roms/gba/Game [USA].zip")
        assert p.file_set.files == ("Game [USA].srm",)

    def test_disk_index_companion_is_filtered(self):
        # <stem>.ldci is RetroArch's disk-control index, not save data
        # (disk_index_file.c:201-249, file_path_special.h:83).
        rd = _retrodeck(
            {
                RETRODECK_JSON: RD_JSON,
                RETRODECK_CFG: (
                    'savefile_directory = "/mnt/sd/retrodeck/saves"\n'
                    'sort_savefiles_by_content_enable = "false"\nsort_savefiles_enable = "false"\n'
                ),
                "/mnt/sd/retrodeck/roms/psx/Game.chd": "",
                "/mnt/sd/retrodeck/saves/Game.srm": "s",
                "/mnt/sd/retrodeck/saves/Game.ldci": "{}",
            }
        )
        p = rd.save_location(content_path="/mnt/sd/retrodeck/roms/psx/Game.chd")
        assert p.file_set.files == ("Game.srm",)

    def test_observed_never_claims_completeness(self):
        rd = _retrodeck(
            {
                RETRODECK_JSON: RD_JSON,
                RETRODECK_CFG: (
                    'savefile_directory = "/mnt/sd/retrodeck/saves"\n'
                    'sort_savefiles_by_content_enable = "false"\nsort_savefiles_enable = "false"\n'
                ),
                "/mnt/sd/retrodeck/roms/gba/Game.zip": "",
                "/mnt/sd/retrodeck/saves/Game.srm": "s",
            }
        )
        p = rd.save_location(content_path="/mnt/sd/retrodeck/roms/gba/Game.zip")
        assert p.file_set.state == "observed"
        assert p.file_set.complete is False

    def test_rom_stem_truncates_at_last_dot(self):
        # runloop.c:8710 — truncate at the last dot, but not a leading one.
        rd = _retrodeck(
            {
                RETRODECK_JSON: RD_JSON,
                RETRODECK_CFG: (
                    'savefile_directory = "/mnt/sd/retrodeck/saves"\n'
                    'sort_savefiles_by_content_enable = "false"\nsort_savefiles_enable = "false"\n'
                ),
                "/mnt/sd/retrodeck/roms/gb/Tetris (World) (Rev 1).zip": "",
                "/mnt/sd/retrodeck/saves/Tetris (World) (Rev 1).srm": "s",
                "/mnt/sd/retrodeck/saves/Tetris (World) (Rev 1).rtc": "r",
            }
        )
        p = rd.save_location(content_path="/mnt/sd/retrodeck/roms/gb/Tetris (World) (Rev 1).zip")
        assert p.file_set.files == ("Tetris (World) (Rev 1).rtc", "Tetris (World) (Rev 1).srm")


class TestArchiveContentResolves:
    """The consequences of the stem: the observed set and the per-game override."""

    ARCHIVE = "/mnt/sd/retrodeck/roms/gba/Pack.zip"
    ENTRY = f"{ARCHIVE}#Golden Sun (USA).gba"
    CFG = (
        'savefile_directory = "/mnt/sd/retrodeck/saves"\n'
        'sort_savefiles_by_content_enable = "true"\nsort_savefiles_enable = "false"\n'
        'libretro_directory = "/app/cores"\n'
    )

    def _machine(self, extra=None):
        files = {
            RETRODECK_JSON: RD_JSON,
            RETRODECK_CFG: self.CFG,
            self.ARCHIVE: "",
            "/mnt/sd/retrodeck/saves/gba/Golden Sun (USA).srm": "sram",
            **(extra or {}),
        }
        return _retrodeck(files, cores={f"{RD_DEPLOY_CORES}/mgba_libretro.so": {"library_name": "mGBA"}})

    def test_the_entry_names_the_save(self):
        rd = self._machine()
        p = rd.save_location(content_path=self.ENTRY, core_so="mgba_libretro.so")
        assert p.dir == "/mnt/sd/retrodeck/saves/gba"
        assert p.file_set.files == ("Golden Sun (USA).srm",)

    def test_the_game_override_is_found_under_the_entry_name(self):
        rd = self._machine(
            {
                f"{RETRODECK_OVERRIDES}/mGBA/Golden Sun (USA).cfg": (
                    'sort_savefiles_by_content_enable = "false"\n'
                ),
                "/mnt/sd/retrodeck/saves/Golden Sun (USA).srm": "sram",
            }
        )
        p = rd.save_location(content_path=self.ENTRY, core_so="mgba_libretro.so")
        assert p.dir == "/mnt/sd/retrodeck/saves"

    def test_the_archive_itself_is_not_a_save(self):
        # In content-dir mode the archive lies where the save does and carries
        # the same stem when it is named after its entry.
        rd = _retrodeck(
            {
                RETRODECK_JSON: RD_JSON,
                RETRODECK_CFG: (
                    'savefiles_in_content_dir = "true"\n'
                    'sort_savefiles_by_content_enable = "false"\nsort_savefiles_enable = "false"\n'
                ),
                "/mnt/sd/retrodeck/roms/gba/Golden Sun (USA).zip": "",
                "/mnt/sd/retrodeck/roms/gba/Golden Sun (USA).srm": "sram",
            }
        )
        p = rd.save_location(
            content_path="/mnt/sd/retrodeck/roms/gba/Golden Sun (USA).zip#Golden Sun (USA).gba"
        )
        assert p.file_set.files == ("Golden Sun (USA).srm",)


class TestContentDirObservation:
    """M10: an observation in the ROM's own directory says what it is."""

    CFG = (
        'savefiles_in_content_dir = "true"\n'
        'sort_savefiles_by_content_enable = "false"\nsort_savefiles_enable = "false"\n'
    )
    CUE = "/mnt/sd/retrodeck/roms/psx/Game.cue"

    def _machine(self):
        return _retrodeck(
            {
                RETRODECK_JSON: RD_JSON,
                RETRODECK_CFG: self.CFG,
                self.CUE: "FILE \"Game.bin\" BINARY\n",
                "/mnt/sd/retrodeck/roms/psx/Game.bin": "track",
                "/mnt/sd/retrodeck/roms/psx/Game.png": "cover",
                "/mnt/sd/retrodeck/roms/psx/Game.srm": "sram",
            }
        )

    def test_the_content_siblings_are_stated_not_hidden(self):
        p = self._machine().save_location(content_path=self.CUE)
        assert p.file_set.state == "observed"
        assert p.file_set.files == ("Game.bin", "Game.png", "Game.srm")
        assert p.file_set.complete is False

    def test_the_observation_carries_the_caveat_that_says_so(self):
        p = self._machine().save_location(content_path=self.CUE)
        caveat = next(c for c in p.caveats if c.code == atlas.CAVEAT_CONTENT_DIR_OBSERVATION)
        assert caveat.data == {"dir": "/mnt/sd/retrodeck/roms/psx"}

    def test_a_trailing_slash_still_filters_the_content_file(self):
        # The ROM is filtered by the name it has on disk, and that name has to
        # survive the trailing slash the rest of the math already normalizes
        # away — otherwise the content file reads as save data.
        with_slash = self._machine().save_location(content_path=f"{self.CUE}/")
        without = self._machine().save_location(content_path=self.CUE)
        assert with_slash.file_set.files == without.file_set.files
        assert "Game.cue" not in with_slash.file_set.files

    def test_a_save_directory_of_its_own_carries_no_such_caveat(self):
        rd = _retrodeck(
            {
                RETRODECK_JSON: RD_JSON,
                RETRODECK_CFG: (
                    'savefile_directory = "/mnt/sd/retrodeck/saves"\n'
                    'sort_savefiles_by_content_enable = "false"\nsort_savefiles_enable = "false"\n'
                ),
                self.CUE: "",
                "/mnt/sd/retrodeck/saves/Game.srm": "sram",
            }
        )
        p = rd.save_location(content_path=self.CUE)
        assert p.file_set.files == ("Game.srm",)
        assert not any(c.code == atlas.CAVEAT_CONTENT_DIR_OBSERVATION for c in p.caveats)


class TestUnnamedContentPath:
    """L11: a content path RetroArch derives no name from is refused, loudly."""

    CFG = (
        'savefile_directory = "/mnt/sd/retrodeck/saves"\n'
        'sort_savefiles_by_content_enable = "true"\nsort_savefiles_enable = "false"\n'
    )

    def _machine(self):
        return _retrodeck(
            {
                RETRODECK_JSON: RD_JSON,
                RETRODECK_CFG: self.CFG,
                "/mnt/sd/retrodeck/roms/psx/Game/disc.bin": "",
                "/mnt/sd/retrodeck/saves/psx/.hidden": "not a save",
            }
        )

    def test_no_dotfile_is_reported_as_a_save(self):
        p = self._machine().save_location(content_path="/mnt/sd/retrodeck/roms/psx/Game/")
        assert p.file_set.state == "unknown"
        assert p.file_set.files == ()

    def test_the_refusal_is_stated(self):
        p = self._machine().save_location(content_path="/mnt/sd/retrodeck/roms/psx/Game/")
        caveat = next(c for c in p.caveats if c.code == atlas.CAVEAT_CONTENT_PATH_UNNAMED)
        assert caveat.data == {"content_path": "/mnt/sd/retrodeck/roms/psx/Game/"}

    def test_the_directory_is_still_answered(self):
        # The name is missing, not the layout: the sort component is the
        # directory of the last component (file_path.c:493-534).
        p = self._machine().save_location(content_path="/mnt/sd/retrodeck/roms/psx/Game/")
        assert p.dir == "/mnt/sd/retrodeck/saves/psx"

    def test_an_empty_content_path_is_answered_not_raised(self):
        # An empty string fills no coordinate at all, so every hole stays a
        # hole — and the answer says why it was not filled. Domain states never
        # raise, and a content-directory root would otherwise build an empty
        # dir (which SavePlacement refuses).
        rd = _retrodeck(
            {
                RETRODECK_JSON: RD_JSON,
                RETRODECK_CFG: (
                    'savefiles_in_content_dir = "true"\n'
                    'sort_savefiles_by_content_enable = "false"\nsort_savefiles_enable = "false"\n'
                ),
                "/mnt/sd/retrodeck/saves/.keep": "",
            }
        )
        p = rd.save_location(content_path="")
        assert p.dir == "<content_dir>"
        assert p.needs == ("content_dir",)
        assert p.file_set.state == "unknown"
        assert any(c.code == atlas.CAVEAT_CONTENT_PATH_UNNAMED for c in p.caveats)


class TestConditionalPlacement:
    """H5: an absent sorted directory is a conditional result, not a fact."""

    CFG_SORT_CONTENT = (
        'savefile_directory = "/mnt/sd/retrodeck/saves"\n'
        'sort_savefiles_by_content_enable = "true"\nsort_savefiles_enable = "false"\n'
    )

    def test_existing_sorted_dir_is_unconditional(self):
        rd = _retrodeck(
            {
                RETRODECK_JSON: RD_JSON,
                RETRODECK_CFG: self.CFG_SORT_CONTENT,
                "/mnt/sd/retrodeck/roms/gba/Game.zip": "",
                "/mnt/sd/retrodeck/saves/gba/Game.srm": "s",
            }
        )
        p = rd.save_location(content_path="/mnt/sd/retrodeck/roms/gba/Game.zip")
        assert p.fallback_dir is None
        assert not any(c.code == atlas.CAVEAT_SORTED_DIR_MISSING for c in p.caveats)

    def test_missing_sorted_dir_carries_structural_fallback(self):
        rd = _retrodeck(
            {
                RETRODECK_JSON: RD_JSON,
                RETRODECK_CFG: self.CFG_SORT_CONTENT,
                "/mnt/sd/retrodeck/roms/gba/Game.zip": "",
                "/mnt/sd/retrodeck/saves/.keep": "",
            }
        )
        p = rd.save_location(content_path="/mnt/sd/retrodeck/roms/gba/Game.zip")
        assert p.dir == "/mnt/sd/retrodeck/saves/gba"
        assert p.fallback_dir == "/mnt/sd/retrodeck/saves"
        assert any(c.code == atlas.CAVEAT_SORTED_DIR_MISSING for c in p.caveats)

    def test_file_blocking_sorted_dir_makes_fallback_the_answer(self):
        # A file where the sorted dir should be: mkdir MUST fail, so the
        # fallback is not conditional — it is the known outcome.
        rd = _retrodeck(
            {
                RETRODECK_JSON: RD_JSON,
                RETRODECK_CFG: self.CFG_SORT_CONTENT,
                "/mnt/sd/retrodeck/roms/gba/Game.zip": "",
                "/mnt/sd/retrodeck/saves/gba": "i am a file, not a directory",
            }
        )
        p = rd.save_location(content_path="/mnt/sd/retrodeck/roms/gba/Game.zip")
        assert p.dir == "/mnt/sd/retrodeck/saves"
        assert p.fallback_dir is None
        assert any(c.code == atlas.CAVEAT_SORTED_DIR_UNCREATABLE for c in p.caveats)

    def test_content_dir_root_with_sorting_gets_fallback_too(self):
        # H6 established that sorting applies after content-root selection;
        # the conditional-creation rule applies there just the same.
        rd = _retrodeck(
            {
                RETRODECK_JSON: RD_JSON,
                RETRODECK_CFG: (
                    'savefiles_in_content_dir = "true"\n'
                    'sort_savefiles_by_content_enable = "true"\nsort_savefiles_enable = "false"\n'
                ),
                "/mnt/sd/retrodeck/roms/gba/Game.zip": "",
            },
            dirs=["/mnt/sd/retrodeck/saves"],
        )
        p = rd.save_location(content_path="/mnt/sd/retrodeck/roms/gba/Game.zip")
        assert p.dir == "/mnt/sd/retrodeck/roms/gba/gba"
        assert p.fallback_dir == "/mnt/sd/retrodeck/roms/gba"
        assert any(c.code == atlas.CAVEAT_SORTED_DIR_MISSING for c in p.caveats)


class TestLinkView:
    """M7: the emulator-side path and the physical path are both answers."""

    FLAT_CFG = (
        'savefile_directory = "/home/deck/links/saves"\n'
        'sort_savefiles_by_content_enable = "false"\nsort_savefiles_enable = "false"\n'
    )

    def test_symlinked_save_dir_reports_physical_dir(self):
        machine = FixtureMachine(
            {
                f"{HOME}/.config/retroarch/retroarch.cfg": self.FLAT_CFG,
                "/data/real-saves/Game.srm": "s",
                f"{HOME}/roms/gba/Game.zip": "",
            },
            symlinks={f"{HOME}/links/saves": "/data/real-saves"},
        )
        p = atlas.NativeRetroArch(HOME, machine).save_location(content_path=f"{HOME}/roms/gba/Game.zip")
        assert p.dir == f"{HOME}/links/saves"
        assert p.physical_dir == "/data/real-saves"

    def test_dead_link_in_card_directory_is_stated(self):
        # The LRPS2 dir_prep case: the memcards link points into an unmounted
        # volume — the emulator-side path is dead and the answer says so.
        machine = FixtureMachine(
            {
                RETRODECK_JSON: RD_JSON,
                RETRODECK_CFG: (
                    'savefile_directory = "/mnt/sd/retrodeck/saves"\n'
                    'system_directory = "/mnt/sd/retrodeck/bios"\n'
                    'libretro_directory = "/app/cores"\nsort_savefiles_enable = "false"\n'
                ),
                "/mnt/sd/retrodeck/roms/ps2/Game.iso": "",
                "/mnt/sd/retrodeck/saves/.keep": "",
            },
            symlinks={"/mnt/sd/retrodeck/bios/pcsx2/memcards": "/run/media/gone/saves/ps2/memcards"},
            cores={f"{RD_DEPLOY_CORES}/pcsx2_libretro.so": {"library_name": "LRPS2"}},
        )
        p = atlas.RetroDeck(HOME, machine).save_location(
            content_path="/mnt/sd/retrodeck/roms/ps2/Game.iso", core_so="pcsx2_libretro.so"
        )
        assert p.dir == "/mnt/sd/retrodeck/bios/pcsx2/memcards"
        assert p.physical_dir is None
        dead = [c for c in p.caveats if c.code == atlas.CAVEAT_DEAD_SYMLINK]
        assert dead
        assert dead[0].data["link"] == "/mnt/sd/retrodeck/bios/pcsx2/memcards"

    def test_rejected_save_root_through_dead_link_says_why(self):
        machine = FixtureMachine(
            {
                f"{HOME}/.config/retroarch/retroarch.cfg": self.FLAT_CFG,
                f"{HOME}/roms/gba/Game.zip": "",
            },
            symlinks={f"{HOME}/links/saves": "/run/media/gone/saves"},
        )
        p = atlas.NativeRetroArch(HOME, machine).save_location(content_path=f"{HOME}/roms/gba/Game.zip")
        assert p.dir == f"{HOME}/.config/retroarch/saves"
        codes = [c.code for c in p.caveats]
        assert atlas.CAVEAT_INVALID_SAVE_DIRECTORY in codes
        assert atlas.CAVEAT_DEAD_SYMLINK in codes

    def test_rejected_save_root_through_a_symlink_loop_says_why(self):
        """A chain that never settles is stated, not half-followed.

        The kernel answers ELOOP for the whole resolution — there is no path it
        got partway to. A resolver that handed back where it stopped would name
        a directory nothing can open as the physical one, and say nothing about
        it: the caller would read an ordinary answer. It is its own code because
        a dead link points at a name somebody can create, while a loop is a
        cycle somebody has to break.
        """
        machine = FixtureMachine(
            {
                f"{HOME}/.config/retroarch/retroarch.cfg": self.FLAT_CFG,
                f"{HOME}/roms/gba/Game.zip": "",
            },
            symlinks={f"{HOME}/links/saves": "/data/a", "/data/a": "/data/b", "/data/b": "/data/a"},
        )
        p = atlas.NativeRetroArch(HOME, machine).save_location(content_path=f"{HOME}/roms/gba/Game.zip")
        assert p.dir == f"{HOME}/.config/retroarch/saves"
        looping = [c for c in p.caveats if c.code == atlas.CAVEAT_SYMLINK_LOOP]
        assert looping
        assert looping[0].data["link"] == f"{HOME}/links/saves"
        assert atlas.CAVEAT_DEAD_SYMLINK not in [c.code for c in p.caveats]

    @pytest.mark.parametrize("length, settles", [(SYMLINK_HOPS, True), (SYMLINK_HOPS + 1, False)])
    def test_the_link_view_gives_up_on_the_hop_the_kernel_does(self, length, settles):
        """The resolver behind ``physical_dir`` stops where the seam's does.

        A chain of exactly ``SYMLINK_HOPS`` links resolves on a real filesystem
        and one link more answers ELOOP, so this reads the same constant the
        seam does rather than a second copy of the number — a chain that
        resolved in the seam and not here would report *no physical directory*
        for a place the emulator writes to perfectly well.
        """
        chain = {f"{HOME}/links/saves": "/c/l1", f"/c/l{length - 1}": "/data/real-saves"}
        chain.update({f"/c/l{i}": f"/c/l{i + 1}" for i in range(1, length - 1)})
        machine = FixtureMachine(
            {
                f"{HOME}/.config/retroarch/retroarch.cfg": self.FLAT_CFG,
                "/data/real-saves/Game.srm": "s",
                f"{HOME}/roms/gba/Game.zip": "",
            },
            symlinks=chain,
        )
        p = atlas.NativeRetroArch(HOME, machine).save_location(content_path=f"{HOME}/roms/gba/Game.zip")
        assert (p.physical_dir == "/data/real-saves") is settles
        assert (atlas.CAVEAT_SYMLINK_LOOP in [c.code for c in p.caveats]) is not settles


class TestSystemDirectoryRoot:
    """M6: a card rooted in the system directory answers the root the core is handed.

    ``RETRO_ENVIRONMENT_GET_SYSTEM_DIRECTORY`` (runloop.c:1958-1999) is not a
    read of ``system_directory``: the flag, and an emptied key, send the core to
    the content's own directory, and an absent key resolves to RetroArch's
    platform default. None of those is a hole — ``needs`` only ever carries what
    the caller fills from the content.
    """

    CONTENT = "/mnt/sd/retrodeck/roms/dreamcast/Game (Europe).gdi"
    SYSTEM_DIR_CFG = 'system_directory = "/mnt/sd/retrodeck/bios"\n'
    BASE_CFG = (
        'savefile_directory = "/mnt/sd/retrodeck/saves"\n'
        'sort_savefiles_by_content_enable = "false"\nsort_savefiles_enable = "false"\n'
        'global_core_options = "true"\nlibretro_directory = "/app/cores"\n'
    )
    VMU = "vmu_save_A1.bin"

    def _flycast(self, cfg, files=None, **kwargs):
        return _retrodeck(
            {
                RETRODECK_JSON: RD_JSON,
                RETRODECK_CFG: self.BASE_CFG + cfg,
                f"{HOME}/.var/app/net.retrodeck.retrodeck/config/retroarch/retroarch-core-options.cfg": (
                    'reicast_per_content_vmus = "disabled"\n'
                ),
                self.CONTENT: "",
                "/mnt/sd/retrodeck/saves/.keep": "",
                **(files or {}),
            },
            cores={f"{RD_DEPLOY_CORES}/flycast_libretro.so": {"library_name": "Flycast"}},
            **kwargs,
        )

    def _placement(self, cfg, files=None, content: str | None = CONTENT, **kwargs):
        return self._flycast(cfg, files, **kwargs).save_location(
            content_path=content, core_so="flycast_libretro.so"
        )

    def test_the_configured_directory_is_the_root_when_nothing_moves_it(self):
        p = self._placement(self.SYSTEM_DIR_CFG, {f"/mnt/sd/retrodeck/bios/dc/{self.VMU}": "v"})
        assert p.dir == "/mnt/sd/retrodeck/bios/dc"
        assert p.root_kind == atlas.ROOT_SYSTEM_DIRECTORY
        assert p.file_set.files == (self.VMU,)

    def test_system_files_in_the_content_dir_move_the_root_to_the_content(self):
        p = self._placement(
            self.SYSTEM_DIR_CFG + 'systemfiles_in_content_dir = "true"\n',
            {f"/mnt/sd/retrodeck/roms/dreamcast/dc/{self.VMU}": "v"},
        )
        assert p.dir == "/mnt/sd/retrodeck/roms/dreamcast/dc"
        assert p.root_kind == atlas.ROOT_CONTENT_DIRECTORY
        assert p.file_set.state == "observed"

    def test_without_content_the_content_root_is_a_hole_the_caller_can_fill(self):
        p = self._placement(
            self.SYSTEM_DIR_CFG + 'systemfiles_in_content_dir = "true"\n', content=None
        )
        assert p.dir == "<content_dir>/dc"
        assert p.needs == ("content_dir",)
        assert p.file_set.state == "declared"

    def test_an_override_can_set_the_flag_too(self):
        # The flag is read through the merged config like every other key —
        # not from the global cfg alone.
        p = self._placement(
            self.SYSTEM_DIR_CFG,
            {
                f"{RETRODECK_OVERRIDES}/Flycast/Flycast.cfg": 'systemfiles_in_content_dir = "true"\n',
                f"/mnt/sd/retrodeck/roms/dreamcast/dc/{self.VMU}": "v",
            },
        )
        assert p.dir == "/mnt/sd/retrodeck/roms/dreamcast/dc"

    def test_a_blank_system_directory_hands_the_core_the_content_dir(self):
        # config_get_path copies an empty value through (config_file.c:1202-1216)
        # because system_directory passes handle_setting=true — the opposite of
        # savefile_directory, where blank keeps the standing root.
        p = self._placement(
            'system_directory = ""\n', {f"/mnt/sd/retrodeck/roms/dreamcast/dc/{self.VMU}": "v"}
        )
        assert p.dir == "/mnt/sd/retrodeck/roms/dreamcast/dc"
        assert p.root_kind == atlas.ROOT_CONTENT_DIRECTORY

    def test_a_cleared_key_without_content_leaves_the_content_hole_too(self):
        # Upstream's own no-content fallback (runloop.c:1986-1987) hands back the
        # empty value — nothing a save can be placed in. The template is.
        p = self._placement('system_directory = ""\n', content=None)
        assert p.dir == "<content_dir>/dc"
        assert p.needs == ("content_dir",)

    def test_the_literal_default_clears_it_the_same_way(self):
        p = self._placement(
            'system_directory = "default"\n', {f"/mnt/sd/retrodeck/roms/dreamcast/dc/{self.VMU}": "v"}
        )
        assert p.dir == "/mnt/sd/retrodeck/roms/dreamcast/dc"

    def test_an_unset_key_resolves_to_the_platform_default(self):
        # config_set_defaults put 'system' under the config tree there before
        # any cfg was read (configuration.c:5746-5749, platform_unix.c:2142-2143).
        config_tree = f"{HOME}/.var/app/net.retrodeck.retrodeck/config/retroarch"
        p = self._placement("", {f"{config_tree}/system/dc/{self.VMU}": "v"})
        assert p.dir == f"{config_tree}/system/dc"
        assert p.root_kind == atlas.ROOT_SYSTEM_DIRECTORY
        assert p.file_set.state == "observed"
        assert p.needs == ()
        # Nothing to refuse: an absent key resolves, so neither route states a
        # refusal for it — the cleared key is the one that still does.
        assert atlas.CAVEAT_SYSTEM_DIRECTORY_CLEARED not in [c.code for c in p.caveats]

    def test_a_dropped_system_directory_line_is_stated(self):
        # The line sets nothing, so the platform default stands — silently,
        # unless the answer says the file tried to say otherwise.
        p = self._placement('system_directory "/mnt/sd/retrodeck/bios"\n')
        dropped = next(c for c in p.caveats if c.code == atlas.CAVEAT_CFG_LINE_DROPPED)
        assert dropped.data["key"] == "system_directory"
        assert p.dir.endswith("/config/retroarch/system/dc")

    def test_a_rejected_flag_value_is_stated_and_changes_nothing(self):
        p = self._placement(
            self.SYSTEM_DIR_CFG + 'systemfiles_in_content_dir = "yes"\n',
            {f"/mnt/sd/retrodeck/bios/dc/{self.VMU}": "v"},
        )
        assert p.dir == "/mnt/sd/retrodeck/bios/dc"
        rejected = next(c for c in p.caveats if c.code == atlas.CAVEAT_CFG_VALUE_REJECTED)
        assert rejected.data["key"] == "systemfiles_in_content_dir"

    def test_a_sandbox_only_directory_is_stated_as_configured_and_not_observed(self):
        # The emulator writes there; atlas cannot look. Naming the spelling the
        # emulator uses is the answer — a hole would ask the caller for a value
        # no caller has.
        p = self._placement('system_directory = "/var/db/bios"\n')
        assert p.dir == "/var/db/bios/dc"
        assert p.needs == ()
        assert p.file_set.state == "declared"
        caveat = next(c for c in p.caveats if c.code == atlas.CAVEAT_SANDBOX_PATH_UNTRANSLATED)
        assert caveat.data == {"key": "system_directory", "path": "/var/db/bios"}

    def test_an_application_relative_directory_is_stated_unexpanded(self):
        p = self._placement('system_directory = ":/system"\n')
        assert p.dir == ":/system/dc"
        assert p.needs == ()
        assert any(c.code == atlas.CAVEAT_APP_RELATIVE_PATH_UNEXPANDED for c in p.caveats)

    def test_the_second_root_of_a_spanning_mode_is_resolved_too(self):
        # 'VMU A1' moves port A1 to the save directory and leaves the rest on
        # the shared card — which the flag has just moved to the content's own
        # directory. A consumer following also_under back to the cfg key would
        # look where this core no longer writes.
        def spanning(flag):
            rd = self._flycast(
                self.SYSTEM_DIR_CFG + flag,
                {
                    f"{HOME}/.var/app/net.retrodeck.retrodeck/config/retroarch/"
                    "retroarch-core-options.cfg": 'reicast_per_content_vmus = "VMU A1"\n'
                },
            )
            p = rd.save_location(content_path=self.CONTENT, core_so="flycast_libretro.so")
            return next(c for c in p.caveats if c.code == atlas.CAVEAT_FILE_SET_SPANS_ROOTS)

        assert spanning("").data["also_under"] == atlas.ROOT_SYSTEM_DIRECTORY
        assert (
            spanning('systemfiles_in_content_dir = "true"\n').data["also_under"]
            == atlas.ROOT_CONTENT_DIRECTORY
        )

    def test_no_configured_directory_is_ever_a_hole(self):
        for cfg in ("", 'system_directory = ""\n', 'system_directory = "/var/db/bios"\n'):
            p = self._placement(cfg)
            assert "system_directory" not in p.needs, cfg


class TestEmuDeck:
    def test_settings_parse_and_roots(self):
        machine = FixtureMachine(
            {
                EMUDECK_SETTINGS: 'romsPath="$HOME/Emulation/roms"\nsavesPath="$HOME/Emulation/saves"\n',
                f"{HOME}/Emulation/saves/.keep": "",
            }
        )
        ed = atlas.EmuDeck(HOME, machine)
        assert ed.root() == f"{HOME}/Emulation"
        assert ed.saves_root() == f"{HOME}/Emulation/saves"
        # No standalone RetroArch cfg in this fixture: the companion issue is
        # the only one — roots are fine.
        assert ed.health().codes == (atlas.HEALTH_ISSUE_COMPANION_CONFIG_MISSING,)

    def test_save_location_reads_standalone_cfg(self):
        machine = FixtureMachine(
            {
                EMUDECK_SETTINGS: 'savesPath="$HOME/Emulation/saves"\nromsPath="$HOME/Emulation/roms"\n',
                STANDALONE_CFG: (
                    'savefile_directory = "/home/deck/Emulation/saves/retroarch/saves"\n'
                    'sort_savefiles_by_content_enable = "false"\n'
                    'sort_savefiles_enable = "false"\n'
                ),
                f"{HOME}/Emulation/roms/gba/Game.zip": "",
                f"{HOME}/Emulation/saves/retroarch/saves/.keep": "",
            }
        )
        ed = atlas.EmuDeck(HOME, machine)
        p = ed.save_location(content_path=f"{HOME}/Emulation/roms/gba/Game.zip")
        assert p.dir == "/home/deck/Emulation/saves/retroarch/saves"
        assert p.root_kind == atlas.ROOT_SAVEFILE_DIRECTORY


class TestFlatpakSandboxPaths:
    """A Flatpak app writes its config from inside its sandbox, so the paths in
    it are sandbox paths: the live RetroDECK cfg spells its override directory
    ``/var/config/retroarch/config``, which Flatpak binds to
    ``~/.var/app/<app id>/config/retroarch/config`` on the host."""

    SANDBOX_CFG_DIR = "/var/config/retroarch"
    SORTED_CFG = (
        'savefile_directory = "/mnt/sd/retrodeck/saves"\n'
        'sort_savefiles_by_content_enable = "true"\nsort_savefiles_enable = "false"\n'
        'libretro_directory = "/app/cores"\n'
    )

    def _psp_query(self, cfg_lines, files=None):
        rd = _retrodeck(
            {
                RETRODECK_JSON: RD_JSON,
                RETRODECK_CFG: cfg_lines,
                "/mnt/sd/retrodeck/saves/.keep": "",
                "/mnt/sd/retrodeck/roms/psp/Game.iso": "",
                **(files or {}),
            },
            cores={f"{RD_DEPLOY_CORES}/ppsspp_libretro.so": {"library_name": "PPSSPP"}},
        )
        return rd.save_location(
            content_path="/mnt/sd/retrodeck/roms/psp/Game.iso", core_so="ppsspp_libretro.so"
        )

    def test_override_dir_in_sandbox_spelling_reaches_the_host_override(self):
        # The stock RetroDECK override config/PPSSPP/PPSSPP.cfg flips the layout
        # flat; reached only if the sandbox spelling is translated.
        p = self._psp_query(
            self.SORTED_CFG + f'rgui_config_directory = "{self.SANDBOX_CFG_DIR}/config"\n',
            files={f"{RETRODECK_OVERRIDES}/PPSSPP/PPSSPP.cfg": 'sort_savefiles_by_content_enable = "false"'},
        )
        assert p.dir == "/mnt/sd/retrodeck/saves"

    def test_translation_names_both_spellings_in_the_sources(self):
        p = self._psp_query(self.SORTED_CFG + f'rgui_config_directory = "{self.SANDBOX_CFG_DIR}/config"\n')
        assert any(
            f'rgui_config_directory = "{self.SANDBOX_CFG_DIR}/config"' in s and RETRODECK_OVERRIDES in s
            for s in p.sources
        )

    def test_untranslatable_override_dir_is_stated_not_silent(self):
        # /var/db is the runtime's own filesystem inside the sandbox — no host
        # location exists, so the overrides there cannot be read from here.
        p = self._psp_query(self.SORTED_CFG + 'rgui_config_directory = "/var/db/retroarch/config"\n')
        assert [c.data for c in p.caveats if c.code == atlas.CAVEAT_SANDBOX_PATH_UNTRANSLATED] == [
            {"key": "rgui_config_directory", "path": "/var/db/retroarch/config"}
        ]

    def test_save_directory_in_sandbox_spelling_is_translated(self):
        p = self._psp_query(
            'savefile_directory = "/var/data/saves"\n'
            'sort_savefiles_by_content_enable = "false"\nsort_savefiles_enable = "false"\n',
            files={f"{HOME}/.var/app/net.retrodeck.retrodeck/data/saves/.keep": ""},
        )
        assert p.dir == f"{HOME}/.var/app/net.retrodeck.retrodeck/data/saves"

    def test_untranslatable_save_directory_keeps_the_emulators_own_path(self):
        # RetroArch's "not an existing directory" test happens inside the
        # sandbox; atlas cannot reproduce it, so it must not report its outcome.
        p = self._psp_query(
            'savefile_directory = "/run/user/1000/saves"\n'
            'sort_savefiles_by_content_enable = "false"\nsort_savefiles_enable = "false"\n'
            'libretro_directory = "/app/cores"\n'
        )
        assert p.dir == "/run/user/1000/saves"
        codes = [c.code for c in p.caveats]
        assert atlas.CAVEAT_SANDBOX_PATH_UNTRANSLATED in codes
        assert atlas.CAVEAT_INVALID_SAVE_DIRECTORY not in codes

    def test_rejected_sandbox_save_directory_names_where_atlas_looked(self):
        # The cfg line to edit is the sandbox spelling, but the directory atlas
        # found missing is the host one — the message states both, so "missing"
        # can be checked where the check was made (carried over from item 9).
        p = self._psp_query(
            'savefile_directory = "/var/data/gone"\n'
            'sort_savefiles_by_content_enable = "false"\nsort_savefiles_enable = "false"\n'
        )
        rejected = [c for c in p.caveats if c.code == atlas.CAVEAT_INVALID_SAVE_DIRECTORY]
        assert rejected
        assert rejected[0].data["configured"] == "/var/data/gone"
        assert f"{HOME}/.var/app/net.retrodeck.retrodeck/data/gone" in rejected[0].message

    def test_a_host_side_rejection_does_not_repeat_the_same_path(self):
        p = self._psp_query(
            'savefile_directory = "/mnt/sd/gone"\n'
            'sort_savefiles_by_content_enable = "false"\nsort_savefiles_enable = "false"\n'
        )
        rejected = [c for c in p.caveats if c.code == atlas.CAVEAT_INVALID_SAVE_DIRECTORY]
        assert rejected
        assert "host spelling" not in rejected[0].message

    def test_host_shared_paths_pass_through_untranslated(self):
        # /run/media is the same directory inside and outside the sandbox.
        p = self._psp_query(
            'savefile_directory = "/run/media/deck/card/saves"\n'
            'sort_savefiles_by_content_enable = "false"\nsort_savefiles_enable = "false"\n',
            files={"/run/media/deck/card/saves/.keep": ""},
        )
        assert p.dir == "/run/media/deck/card/saves"

    def test_options_path_in_sandbox_spelling_governs_the_option(self):
        # The card's option is read from the file core_options_path names —
        # here in the sandbox's spelling of the app's own config directory.
        rd = _retrodeck(
            {
                RETRODECK_JSON: RD_JSON,
                RETRODECK_CFG: (
                    'savefile_directory = "/mnt/sd/retrodeck/saves"\n'
                    'sort_savefiles_by_content_enable = "false"\nsort_savefiles_enable = "false"\n'
                    'libretro_directory = "/app/cores"\nglobal_core_options = "true"\n'
                    f'core_options_path = "{self.SANDBOX_CFG_DIR}/opts.cfg"\n'
                ),
                f"{HOME}/.var/app/net.retrodeck.retrodeck/config/retroarch/opts.cfg": (
                    'opera_nvram_storage = "shared"'
                ),
                "/mnt/sd/retrodeck/saves/.keep": "",
                "/mnt/sd/retrodeck/roms/3do/Game.chd": "",
            },
            cores={f"{RD_DEPLOY_CORES}/opera_libretro.so": {"library_name": "Opera"}},
        )
        p = rd.save_location(
            content_path="/mnt/sd/retrodeck/roms/3do/Game.chd", core_so="opera_libretro.so"
        )
        assert p.granularity is not None
        assert p.granularity.option_value == "shared"

    def test_unreachable_root_is_not_observed_at_all(self):
        # The sorted directory, its fallback and the link view all come from
        # reads that cannot apply to a path atlas just declared unreadable.
        p = self._psp_query(
            'savefile_directory = "/run/user/1000/saves"\n'
            'sort_savefiles_by_content_enable = "true"\nsort_savefiles_enable = "false"\n'
            'libretro_directory = "/app/cores"\n'
        )
        assert p.dir == "/run/user/1000/saves/psp"  # RetroArch's path math still applies
        assert (p.fallback_dir, p.physical_dir, p.file_set.state) == (None, None, "unknown")
        assert atlas.CAVEAT_SORTED_DIR_MISSING not in [c.code for c in p.caveats]

    def test_untranslatable_options_path_is_silent_for_a_core_that_reads_none(self):
        # A core without a rule card consults no options file, so an
        # unreachable core_options_path is not a degradation of its answer.
        p = self._psp_query(
            'savefile_directory = "/mnt/sd/retrodeck/saves"\n'
            'sort_savefiles_by_content_enable = "false"\nsort_savefiles_enable = "false"\n'
            'libretro_directory = "/app/cores"\ncore_options_path = "/var/db/opts.cfg"\n'
        )
        assert atlas.CAVEAT_SANDBOX_PATH_UNTRANSLATED not in [c.code for c in p.caveats]

    def test_native_install_cfg_is_not_translated(self):
        # NativeRetroArch writes its cfg outside any sandbox: /var/config there
        # is a real host path, and substituting one would answer with a
        # directory this RetroArch never touches.
        machine = FixtureMachine(
            {
                f"{HOME}/.config/retroarch/retroarch.cfg": (
                    'savefile_directory = "/var/config/saves"\n'
                    'sort_savefiles_by_content_enable = "false"\nsort_savefiles_enable = "false"\n'
                ),
                "/var/config/saves/.keep": "",
                f"{HOME}/roms/gba/Game.zip": "",
            }
        )
        p = atlas.NativeRetroArch(HOME, machine).save_location(content_path=f"{HOME}/roms/gba/Game.zip")
        assert p.dir == "/var/config/saves"


class TestCfgFidelity:
    """A config atlas reads more permissively than RetroArch would attest paths
    the emulator never uses — so the resolver drops what the parser drops, keeps
    the previous value where the getter refuses one, and says so."""

    SORTED_CFG = (
        'savefile_directory = "/mnt/sd/retrodeck/saves"\n'
        'sort_savefiles_by_content_enable = "true"\nsort_savefiles_enable = "false"\n'
        'libretro_directory = "/app/cores"\n'
    )

    def _psp_query(self, cfg_lines, files=None):
        rd = _retrodeck(
            {
                RETRODECK_JSON: RD_JSON,
                RETRODECK_CFG: cfg_lines,
                "/mnt/sd/retrodeck/saves/.keep": "",
                "/mnt/sd/retrodeck/roms/psp/Game.iso": "",
                **(files or {}),
            },
            cores={f"{RD_DEPLOY_CORES}/ppsspp_libretro.so": {"library_name": "PPSSPP"}},
        )
        return rd.save_location(
            content_path="/mnt/sd/retrodeck/roms/psp/Game.iso", core_so="ppsspp_libretro.so"
        )

    def test_dropped_save_dir_line_falls_back_and_is_stated(self):
        # RetroArch drops 'savefile_directory="…"' (no space before the '='), so
        # the platform default governs — and the answer says which line was lost.
        p = self._psp_query(
            'savefile_directory="/mnt/sd/retrodeck/saves"\n'
            'sort_savefiles_by_content_enable = "false"\nsort_savefiles_enable = "false"\n'
            'libretro_directory = "/app/cores"\n'
        )
        assert p.dir == f"{HOME}/.var/app/net.retrodeck.retrodeck/config/retroarch/saves"
        assert [c.data for c in p.caveats if c.code == atlas.CAVEAT_CFG_LINE_DROPPED] == [
            {"key": "savefile_directory", "line": 'savefile_directory="/mnt/sd/retrodeck/saves"'}
        ]

    def test_rejected_override_value_keeps_the_global_layout_and_is_stated(self):
        p = self._psp_query(
            self.SORTED_CFG,
            files={
                f"{RETRODECK_OVERRIDES}/PPSSPP/PPSSPP.cfg": 'sort_savefiles_by_content_enable = "yes"'
            },
        )
        assert p.dir == "/mnt/sd/retrodeck/saves/psp"  # the global "true" still governs
        assert [c.data for c in p.caveats if c.code == atlas.CAVEAT_CFG_VALUE_REJECTED] == [
            {"key": "sort_savefiles_by_content_enable", "value": "yes"}
        ]

    def test_rejected_gate_value_keeps_the_gates_default(self):
        # auto_overrides_enable defaults true; "no" is not a boolean, so the
        # override below still applies — it is not silently switched off.
        p = self._psp_query(
            self.SORTED_CFG + 'auto_overrides_enable = "no"\n',
            files={
                f"{RETRODECK_OVERRIDES}/PPSSPP/PPSSPP.cfg": 'sort_savefiles_by_content_enable = "false"'
            },
        )
        assert p.dir == "/mnt/sd/retrodeck/saves"
        assert [c.data for c in p.caveats if c.code == atlas.CAVEAT_CFG_VALUE_REJECTED] == [
            {"key": "auto_overrides_enable", "value": "no"}
        ]

    def test_cleared_rgui_dir_puts_overrides_beside_retroarch_cfg(self):
        # rgui_config_directory = "default" clears the setting
        # (configuration.c:6825), and the override directory then falls back to
        # the directory of retroarch.cfg itself (file_path_special.c:196-207) —
        # one level above the platform-default 'config' subdirectory.
        beside = f"{HOME}/.var/app/net.retrodeck.retrodeck/config/retroarch"
        p = self._psp_query(
            self.SORTED_CFG + 'rgui_config_directory = "default"\n',
            files={
                f"{beside}/PPSSPP/PPSSPP.cfg": 'sort_savefiles_by_content_enable = "false"',
                f"{RETRODECK_OVERRIDES}/PPSSPP/PPSSPP.cfg": 'savefile_directory = "/mnt/sd/wrong"',
            },
        )
        assert p.dir == "/mnt/sd/retrodeck/saves"

    def test_absent_rgui_dir_keeps_the_config_subdirectory(self):
        p = self._psp_query(
            self.SORTED_CFG,
            files={
                f"{RETRODECK_OVERRIDES}/PPSSPP/PPSSPP.cfg": 'sort_savefiles_by_content_enable = "false"'
            },
        )
        assert p.dir == "/mnt/sd/retrodeck/saves"

    def test_app_relative_save_dir_is_stated_unexpanded(self):
        # ':' resolves against the running RetroArch executable's own directory
        # (file_path.c:1066-1101) — unknowable from disk, so atlas states the
        # value as configured instead of testing a host path it invented.
        p = self._psp_query(
            'savefile_directory = ":/saves"\n'
            'sort_savefiles_by_content_enable = "false"\nsort_savefiles_enable = "false"\n'
            'libretro_directory = "/app/cores"\n'
        )
        assert p.dir == ":/saves"
        codes = [c.code for c in p.caveats]
        assert atlas.CAVEAT_APP_RELATIVE_PATH_UNEXPANDED in codes
        assert atlas.CAVEAT_INVALID_SAVE_DIRECTORY not in codes
        assert p.file_set.state == "unknown"

    def test_app_relative_override_dir_reads_no_overrides(self):
        p = self._psp_query(
            self.SORTED_CFG + 'rgui_config_directory = ":/config"\n',
            files={
                f"{RETRODECK_OVERRIDES}/PPSSPP/PPSSPP.cfg": 'sort_savefiles_by_content_enable = "false"'
            },
        )
        assert p.dir == "/mnt/sd/retrodeck/saves/psp"  # the unreachable override did not apply
        assert [c.data for c in p.caveats if c.code == atlas.CAVEAT_APP_RELATIVE_PATH_UNEXPANDED] == [
            {"key": "rgui_config_directory", "path": ":/config"}
        ]


class TestOverrideChainSemantics:
    """What an override can and cannot change, read the way RetroArch reads it.

    The overrides are merged into the global cfg and re-read in one pass
    (configuration.c:7161-7243), so a value the merged config refuses leaves the
    global cfg's own value standing — not the platform default, and not a layer
    in between.
    """

    PLATFORM_DEFAULT = f"{HOME}/.var/app/net.retrodeck.retrodeck/config/retroarch/saves"
    GLOBAL_CFG = (
        'savefile_directory = "/mnt/sd/retrodeck/saves"\n'
        'sort_savefiles_by_content_enable = "false"\nsort_savefiles_enable = "false"\n'
        'libretro_directory = "/app/cores"\n'
    )

    def _psp_query(self, cfg_lines, files=None):
        rd = _retrodeck(
            {
                RETRODECK_JSON: RD_JSON,
                RETRODECK_CFG: cfg_lines,
                "/mnt/sd/retrodeck/saves/.keep": "",
                "/mnt/sd/retrodeck/roms/psp/Game.iso": "",
                **(files or {}),
            },
            cores={f"{RD_DEPLOY_CORES}/ppsspp_libretro.so": {"library_name": "PPSSPP"}},
        )
        return rd.save_location(
            content_path="/mnt/sd/retrodeck/roms/psp/Game.iso", core_so="ppsspp_libretro.so"
        )

    def test_unusable_override_root_keeps_the_global_root(self):
        # The per-core override points at an unmounted card. path_is_directory
        # fails, so that read sets nothing and the global cfg's root — which the
        # boot load already accepted — is where RetroArch writes.
        p = self._psp_query(
            self.GLOBAL_CFG,
            files={
                f"{RETRODECK_OVERRIDES}/PPSSPP/PPSSPP.cfg": 'savefile_directory = "/run/media/gone/saves"'
            },
        )
        assert p.dir == "/mnt/sd/retrodeck/saves"
        assert [c.data for c in p.caveats if c.code == atlas.CAVEAT_INVALID_SAVE_DIRECTORY] == [
            {
                "layer": "core override config/PPSSPP/PPSSPP.cfg",
                "configured": "/run/media/gone/saves",
                "effective": "/mnt/sd/retrodeck/saves",
            }
        ]

    def test_unusable_global_root_still_falls_to_the_platform_default(self):
        # The single-layer case is unchanged: nothing stood before the boot load
        # but the platform default under the config tree.
        p = self._psp_query(
            'savefile_directory = "/run/media/gone/saves"\n'
            'sort_savefiles_by_content_enable = "false"\nsort_savefiles_enable = "false"\n'
            'libretro_directory = "/app/cores"\n'
        )
        assert p.dir == self.PLATFORM_DEFAULT
        assert [c.data for c in p.caveats if c.code == atlas.CAVEAT_INVALID_SAVE_DIRECTORY] == [
            {
                "layer": "retroarch.cfg",
                "configured": "/run/media/gone/saves",
                "effective": self.PLATFORM_DEFAULT,
            }
        ]

    def test_a_usable_override_root_still_wins(self):
        p = self._psp_query(
            self.GLOBAL_CFG,
            files={
                f"{RETRODECK_OVERRIDES}/PPSSPP/PPSSPP.cfg": 'savefile_directory = "/mnt/sd/elsewhere"',
                "/mnt/sd/elsewhere/.keep": "",
            },
        )
        assert p.dir == "/mnt/sd/elsewhere"
        assert not [c for c in p.caveats if c.code == atlas.CAVEAT_INVALID_SAVE_DIRECTORY]

    def test_an_override_cannot_switch_overrides_off(self):
        # auto_overrides_enable is copied into a local BEFORE config_load_override
        # runs (runloop.c:4941 vs :5002-5003), so a file that only exists because
        # overrides are on cannot turn them off — the sort flip below still lands.
        p = self._psp_query(
            self.GLOBAL_CFG,
            files={
                f"{RETRODECK_OVERRIDES}/PPSSPP/PPSSPP.cfg": (
                    'auto_overrides_enable = "false"\nsort_savefiles_by_content_enable = "true"\n'
                ),
                "/mnt/sd/retrodeck/saves/psp/.keep": "",
            },
        )
        assert p.dir == "/mnt/sd/retrodeck/saves/psp"

    def test_dropped_rgui_line_moves_the_override_tree_and_is_stated(self):
        # The line sets nothing, so the override tree stays at the platform
        # default and the override below is never read — invisible in the answer
        # without the caveat.
        p = self._psp_query(
            self.GLOBAL_CFG + 'rgui_config_directory="/mnt/sd/retrodeck/rgui"\n',
            files={
                "/mnt/sd/retrodeck/rgui/PPSSPP/PPSSPP.cfg": 'sort_savefiles_by_content_enable = "true"',
                "/mnt/sd/retrodeck/saves/psp/.keep": "",
            },
        )
        assert p.dir == "/mnt/sd/retrodeck/saves"
        assert [c.data for c in p.caveats if c.code == atlas.CAVEAT_CFG_LINE_DROPPED] == [
            {
                "key": "rgui_config_directory",
                "line": 'rgui_config_directory="/mnt/sd/retrodeck/rgui"',
            }
        ]

    def test_dropped_auto_overrides_line_leaves_overrides_on_and_is_stated(self):
        p = self._psp_query(
            self.GLOBAL_CFG + 'auto_overrides_enable="false"\n',
            files={
                f"{RETRODECK_OVERRIDES}/PPSSPP/PPSSPP.cfg": 'sort_savefiles_by_content_enable = "true"',
                "/mnt/sd/retrodeck/saves/psp/.keep": "",
            },
        )
        assert p.dir == "/mnt/sd/retrodeck/saves/psp"  # the overrides did apply
        assert [c.data for c in p.caveats if c.code == atlas.CAVEAT_CFG_LINE_DROPPED] == [
            {"key": "auto_overrides_enable", "line": 'auto_overrides_enable="false"'}
        ]

    def test_a_dropped_line_for_an_ungoverning_key_is_still_silent(self):
        p = self._psp_query(self.GLOBAL_CFG + 'video_driver="gl"\n')
        assert not [c for c in p.caveats if c.code == atlas.CAVEAT_CFG_LINE_DROPPED]

    # The two directory keys read a blank value in opposite directions, and the
    # pair below is the reason: rgui_config_directory is a handled path setting
    # (configuration.c:1736) that the generic loop writes untested (:6536-6537),
    # savefile_directory is not (:1709, skipped at :6534-6535) and reaches only
    # the block that demands path_is_directory (:6914-6933). Neither is a bug —
    # asserting them side by side is what keeps a later reader from ruling that
    # one of them is.

    def test_blank_rgui_config_directory_clears_it_and_moves_the_tree(self):
        beside = f"{HOME}/.var/app/net.retrodeck.retrodeck/config/retroarch"
        p = self._psp_query(
            self.GLOBAL_CFG + 'rgui_config_directory = ""\n',
            files={
                f"{beside}/PPSSPP/PPSSPP.cfg": 'sort_savefiles_by_content_enable = "true"',
                f"{RETRODECK_OVERRIDES}/PPSSPP/PPSSPP.cfg": 'savefile_directory = "/mnt/sd/wrong"',
                "/mnt/sd/retrodeck/saves/psp/.keep": "",
            },
        )
        # The tree moved: the override beside retroarch.cfg is the one read.
        assert p.dir == "/mnt/sd/retrodeck/saves/psp"

    def test_blank_savefile_directory_in_an_override_changes_nothing(self):
        p = self._psp_query(
            self.GLOBAL_CFG,
            files={f"{RETRODECK_OVERRIDES}/PPSSPP/PPSSPP.cfg": 'savefile_directory = ""'},
        )
        assert p.dir == "/mnt/sd/retrodeck/saves"  # not the platform default
        assert [c.data for c in p.caveats if c.code == atlas.CAVEAT_INVALID_SAVE_DIRECTORY] == [
            {
                "layer": "core override config/PPSSPP/PPSSPP.cfg",
                "configured": "",
                "effective": "/mnt/sd/retrodeck/saves",
            }
        ]

    def test_a_refused_global_root_can_be_rescued_by_an_override(self):
        # The one direction where the standing root is set AFTER the refusal:
        # the global cfg is refused, the override then supplies a usable root.
        # The message must not claim that root predates the rejected file.
        p = self._psp_query(
            'savefile_directory = "/run/media/gone/saves"\n'
            'sort_savefiles_by_content_enable = "false"\nsort_savefiles_enable = "false"\n'
            'libretro_directory = "/app/cores"\n',
            files={
                f"{RETRODECK_OVERRIDES}/PPSSPP/PPSSPP.cfg": 'savefile_directory = "/mnt/sd/other"',
                "/mnt/sd/other/.keep": "",
            },
        )
        assert p.dir == "/mnt/sd/other"
        invalid = [c for c in p.caveats if c.code == atlas.CAVEAT_INVALID_SAVE_DIRECTORY]
        assert [c.data for c in invalid] == [
            {
                "layer": "retroarch.cfg",
                "configured": "/run/media/gone/saves",
                "effective": "/mnt/sd/other",
            }
        ]
        assert "a later file in the chain set" in invalid[0].message
        assert "stood before this file" not in invalid[0].message

    def test_a_refusal_the_chain_never_got_past_names_the_earlier_root(self):
        p = self._psp_query(
            self.GLOBAL_CFG,
            files={
                f"{RETRODECK_OVERRIDES}/PPSSPP/PPSSPP.cfg": 'savefile_directory = "/run/media/gone/saves"'
            },
        )
        invalid = [c for c in p.caveats if c.code == atlas.CAVEAT_INVALID_SAVE_DIRECTORY]
        assert "stood before this file" in invalid[0].message
        assert "a later file in the chain set" not in invalid[0].message

    def test_a_shadowed_override_is_not_the_fallback_for_an_unusable_one(self):
        # The merged config holds the GAME override's root; the core override's
        # was overwritten before any getter saw it, so the refusal falls back to
        # the global cfg's root, not to /mnt/sd/other.
        p = self._psp_query(
            self.GLOBAL_CFG,
            files={
                f"{RETRODECK_OVERRIDES}/PPSSPP/PPSSPP.cfg": 'savefile_directory = "/mnt/sd/other"',
                f"{RETRODECK_OVERRIDES}/PPSSPP/Game.cfg": 'savefile_directory = "/run/media/gone/saves"',
                "/mnt/sd/other/.keep": "",
            },
        )
        assert p.dir == "/mnt/sd/retrodeck/saves"


class TestOstreeHomeIsHostSide:
    """Fedora Silverblue and Bazzite — both ship RetroDECK — make ``/home`` a
    symlink to ``/var/home``, so real home directories live under ``/var``.
    Nothing scopes atlas to SteamOS: ``home`` is whatever the caller passes."""

    HOME = "/var/home/deck"
    CFG = f"{HOME}/.var/app/net.retrodeck.retrodeck/config/retroarch/retroarch.cfg"
    JSON = f"{HOME}/.var/app/net.retrodeck.retrodeck/config/retrodeck/retrodeck.json"
    MARKER = '{"paths": {"rd_home_path": "/var/home/deck/retrodeck", "saves_path": "/var/home/deck/retrodeck/saves"}}'

    def _retrodeck_at(self, home, cfg_body, files=None, cores=None):
        machine = FixtureMachine(
            {
                self.JSON: self.MARKER,
                self.CFG: cfg_body,
                f"{self.HOME}/retrodeck/saves/.keep": "",
                f"{self.HOME}/retrodeck/roms/gba/Game.zip": "",
                **(files or {}),
            },
            cores=cores,
        )
        return atlas.RetroDeck(home, machine)

    def test_configured_directories_under_var_home_are_not_sandbox_paths(self):
        rd = self._retrodeck_at(
            self.HOME,
            f'savefile_directory = "{self.HOME}/retrodeck/saves"\n'
            'sort_savefiles_by_content_enable = "false"\nsort_savefiles_enable = "false"\n',
        )
        p = rd.save_location(content_path=f"{self.HOME}/retrodeck/roms/gba/Game.zip")
        assert p.dir == f"{self.HOME}/retrodeck/saves"
        assert atlas.CAVEAT_SANDBOX_PATH_UNTRANSLATED not in [c.code for c in p.caveats]

    def test_firmware_root_under_var_home_still_resolves(self):
        rd = self._retrodeck_at(
            self.HOME,
            f'system_directory = "{self.HOME}/retrodeck/bios"\n',
            files={f"{self.HOME}/retrodeck/bios/panafz1.bin": "rom"},
        )
        assert rd.firmware_inventory().root == f"{self.HOME}/retrodeck/bios"

    def test_var_home_is_host_side_even_when_home_is_spelled_the_other_way(self):
        # RetroArch resolves /home -> /var/home when it writes the cfg, while
        # the caller may still pass the symlink spelling as `home` — so the
        # configured path matches neither `home` nor a sandbox bind.
        machine = FixtureMachine(
            {
                RETRODECK_JSON: self.MARKER,
                RETRODECK_CFG: f'system_directory = "{self.HOME}/retrodeck/bios"\n',
                f"{self.HOME}/retrodeck/bios/panafz1.bin": "rom",
                f"{self.HOME}/retrodeck/saves/.keep": "",
            }
        )
        rd = atlas.RetroDeck(HOME, machine)
        assert rd.firmware_inventory().root == f"{self.HOME}/retrodeck/bios"

    def test_the_apps_own_config_dir_still_translates_into_that_home(self):
        # /var/config is still a bind of the app's config directory — which on
        # this host lives under /var/home. Both halves have to hold at once.
        overrides = f"{self.HOME}/.var/app/net.retrodeck.retrodeck/config/retroarch/config"
        rd = self._retrodeck_at(
            self.HOME,
            f'savefile_directory = "{self.HOME}/retrodeck/saves"\n'
            'sort_savefiles_by_content_enable = "true"\nsort_savefiles_enable = "false"\n'
            'libretro_directory = "/app/cores"\n'
            'rgui_config_directory = "/var/config/retroarch/config"\n',
            files={f"{overrides}/mGBA/mGBA.cfg": 'sort_savefiles_by_content_enable = "false"'},
            cores={f"{RD_DEPLOY_CORES}/mgba_libretro.so": {"library_name": "mGBA"}},
        )
        p = rd.save_location(
            content_path=f"{self.HOME}/retrodeck/roms/gba/Game.zip", core_so="mgba_libretro.so"
        )
        assert p.dir == f"{self.HOME}/retrodeck/saves"


class TestSandboxPathsInFirmware:
    CORE_INFO = 'display_name = "Opera"\nfirmware_count = "1"\nfirmware0_path = "panafz1.bin"\n'

    def test_info_path_in_sandbox_spelling_resolves_on_the_host(self):
        rd = _retrodeck(
            {
                RETRODECK_JSON: RD_JSON,
                RETRODECK_CFG: (
                    'system_directory = "/mnt/sd/retrodeck/bios"\n'
                    'libretro_info_path = "/var/config/retroarch/cores"\n'
                ),
                f"{HOME}/.var/app/net.retrodeck.retrodeck/config/retroarch/cores/opera_libretro.info": (
                    self.CORE_INFO
                ),
                "/mnt/sd/retrodeck/bios/panafz1.bin": "rom",
            }
        )
        assert [c.core_so for c in rd.firmware_inventory().cores] == ["opera_libretro.so"]

    def test_untranslatable_system_directory_is_stated_not_reported_missing(self):
        rd = _retrodeck(
            {
                RETRODECK_JSON: RD_JSON,
                RETRODECK_CFG: 'system_directory = "/var/db/bios"\n',
            }
        )
        answer = rd.firmware_inventory()
        assert answer.root is None
        assert [c.data for c in answer.caveats if c.code == atlas.CAVEAT_SANDBOX_PATH_UNTRANSLATED] == [
            {"key": "system_directory", "path": "/var/db/bios"}
        ]


class _CountingMachine:
    """A machine that records every ``read_text``, so a query's reads can be counted.

    Only ``read_text`` is counted: it is the operation that turns a file's
    *content* into an answer, so it is what "every governing source is read
    exactly once" (DESIGN, consistency model) is about. ``path_kind`` and
    ``glob`` are repeatable probes that carry no revision of a file with them.

    Reads are keyed on the **file**, not on the spelling that reached it:
    ``resolve_links`` follows the seam's own ``readlink`` the way the
    resolver does. Two spellings of one file are one source — the deployed
    ``es_systems.xml`` really is reached through ``current/active``, a symlink
    — so a counter keyed on the spelling would guard "one spelling per source"
    and let a second read through a second spelling walk past it.
    """

    def __init__(self, files, **kwargs):
        self._inner = FixtureMachine(files, **kwargs)
        self.reads: Counter[str] = Counter()

    def read_text(self, path):
        # An unresolvable chain (ELOOP) has no file to key on; the spelling is
        # then the most specific thing there is.
        self.reads[resolve_links(self._inner, path) or path] += 1
        return self._inner.read_text(path)

    def glob(self, pattern):
        return self._inner.glob(pattern)

    def path_kind(self, path):
        return self._inner.path_kind(path)

    def readlink(self, path):
        return self._inner.readlink(path)

    def query_core(self, so_path):
        return self._inner.query_core(so_path)

    def file_size(self, path):
        return self._inner.file_size(path)

    def file_digest(self, path, algorithm):
        return self._inner.file_digest(path, algorithm)

    def repeats(self) -> dict[str, int]:
        return {path: count for path, count in self.reads.items() if count > 1}


class TestOneReadPerSourcePerQuery:
    """DESIGN's consistency model, enforced: one read per source per query.

    A source read twice inside one answer can be edited between the two reads,
    and the answer then mixes two revisions of the machine — the whole reason
    the handles hold no state. ``firmware_for_system`` did exactly that: it
    read ``retrodeck.json`` and both ``es_systems.xml`` layers, then called
    ``emulators_for``, which read all three again.
    """

    DEPLOY_ESDE = (
        "/var/lib/flatpak/app/net.retrodeck.retrodeck/current/active/files/retrodeck/components"
        "/es-de/share/es-de/resources/systems/linux/es_systems.xml"
    )
    ES_SYSTEMS = (
        '<?xml version="1.0"?>\n<systemList>\n  <system><name>n64</name>\n'
        '    <command label="Mupen64Plus-Next">retroarch -L '
        "/app/cores/mupen64plus_next_libretro.so %ROM%</command>\n"
        '    <command label="ParaLLEl N64">retroarch -L '
        "/app/cores/parallel_n64_libretro.so %ROM%</command>\n"
        "  </system>\n</systemList>\n"
    )
    GAMELIST = (
        '<?xml version="1.0"?>\n<gameList>\n\t<game>\n\t\t<path>./Game.z64</path>\n'
        "\t\t<altemulator>ParaLLEl N64</altemulator>\n\t</game>\n</gameList>\n"
    )
    FILES = {
        RETRODECK_JSON: RD_JSON,
        RETRODECK_CFG: (
            'savefile_directory = "/mnt/sd/retrodeck/saves"\n'
            'system_directory = "/mnt/sd/retrodeck/bios"\n'
            'libretro_info_path = "/app/cores"\nlibretro_directory = "/app/cores"\n'
            'sort_savefiles_by_content_enable = "false"\nsort_savefiles_enable = "false"\n'
        ),
        DEPLOY_ESDE: ES_SYSTEMS,
        "/mnt/sd/retrodeck/ES-DE/custom_systems/es_systems.xml": '<?xml version="1.0"?>\n<systemList />\n',
        "/mnt/sd/retrodeck/ES-DE/gamelists/n64/gamelist.xml": GAMELIST,
        f"{RD_DEPLOY_CORES}/mupen64plus_next_libretro.info": (
            'display_name = "Mupen64Plus-Next"\nsystemname = "Nintendo 64"\n'
        ),
        f"{RD_DEPLOY_CORES}/parallel_n64_libretro.info": (
            'display_name = "ParaLLEl N64"\nsystemname = "Nintendo 64"\n'
            'firmware_count = "1"\nfirmware0_path = "pifdata.bin"\n'
        ),
        "/mnt/sd/retrodeck/bios/pifdata.bin": "rom",
        "/mnt/sd/retrodeck/saves/.keep": "",
        "/mnt/sd/retrodeck/roms/n64/Game.z64": "",
    }
    CORES = {
        f"{RD_DEPLOY_CORES}/mupen64plus_next_libretro.so": {"library_name": "Mupen64Plus-Next"},
        f"{RD_DEPLOY_CORES}/parallel_n64_libretro.so": {"library_name": "ParaLLEl N64"},
    }
    CONTENT = "/mnt/sd/retrodeck/roms/n64/Game.z64"

    INFO = f"{RD_DEPLOY_CORES}/parallel_n64_libretro.info"

    def _query(self, ask):
        machine = _CountingMachine(self.FILES, cores=self.CORES)
        ask(atlas.RetroDeck(HOME, machine))
        # An empty read log would make every assertion below vacuously true.
        assert machine.reads
        return machine

    def test_save_location_reads_each_source_once(self):
        machine = self._query(
            lambda rd: rd.save_location(content_path=self.CONTENT, core_so="parallel_n64_libretro.so")
        )
        assert machine.repeats() == {}

    def test_emulators_for_reads_each_source_once(self):
        machine = self._query(lambda rd: rd.emulators_for("n64", content_path=self.CONTENT))
        assert machine.repeats() == {}

    def test_entry_save_location_reads_each_source_once(self):
        # The catalogue ask that produced the entry is its own query; the
        # invariant is per query, so only the entry's own reads are counted.
        machine = _CountingMachine(self.FILES, cores=self.CORES)
        entry = atlas.RetroDeck(HOME, machine).emulators_for("n64").entries[0]
        machine.reads.clear()
        entry.save_location(content_path=self.CONTENT)
        assert machine.repeats() == {}

    def test_firmware_for_core_reads_each_source_once(self):
        machine = self._query(lambda rd: rd.firmware_for_core(core_so="parallel_n64_libretro.so"))
        assert machine.repeats() == {}
        assert self.INFO in machine.reads  # the declarations were really read

    def test_firmware_for_system_reads_each_source_once(self):
        machine = self._query(lambda rd: rd.firmware_for_system(system="n64"))
        assert machine.repeats() == {}
        # The sources the two halves of this query share, actually read.
        assert RETRODECK_JSON in machine.reads
        assert self.DEPLOY_ESDE in machine.reads

    def test_firmware_inventory_reads_each_source_once(self):
        machine = self._query(lambda rd: rd.firmware_inventory())
        assert machine.repeats() == {}
        assert self.INFO in machine.reads

    def test_identify_firmware_reads_each_source_once(self):
        machine = self._query(lambda rd: rd.identify_firmware(md5="0" * 32))
        assert machine.repeats() == {}
        assert self.INFO in machine.reads

    def test_systems_reads_each_source_once(self):
        # The listing reads the marker for its root and again, in effect, for
        # the health findings it carries — one read has to serve both.
        machine = self._query(lambda rd: rd.systems())
        assert machine.repeats() == {}
        assert RETRODECK_JSON in machine.reads

    def test_an_emudeck_query_reads_each_source_once(self):
        # The other handle family, where the companion cfg is what two things
        # want: the context reads its text, the health finding its status.
        machine = _CountingMachine(
            {
                EMUDECK_SETTINGS: 'romsPath="$HOME/Emulation/roms"\nsavesPath="$HOME/Emulation/saves"\n',
                STANDALONE_CFG: 'system_directory = "/home/deck/Emulation/bios"\n',
            }
        )
        atlas.EmuDeck(HOME, machine).firmware_inventory()
        assert machine.reads
        assert machine.repeats() == {}
        assert STANDALONE_CFG in machine.reads

    def test_an_emudeck_catalogue_query_reads_each_source_once(self):
        machine = _CountingMachine(
            {EMUDECK_SETTINGS: 'savesPath="$HOME/Emulation/saves"\n', STANDALONE_CFG: ""}
        )
        atlas.EmuDeck(HOME, machine).systems()
        assert machine.reads
        assert machine.repeats() == {}

    def test_the_counter_would_see_a_repeat(self):
        # The guard above proves nothing unless a second read is visible.
        machine = self._query(lambda rd: (rd.root(), rd.saves_root()))
        assert machine.repeats() == {RETRODECK_JSON: 2}

    def test_the_counter_sees_one_file_under_two_spellings(self):
        # …and nothing unless it counts the file rather than the spelling: the
        # deployed catalogue is reached through `current/active`, a symlink, so
        # a second read spelled the other way must not read as a first read.
        deployed = "/var/lib/flatpak/app/x/1.0/files/es_systems.xml"
        machine = _CountingMachine(
            {deployed: "<systemList />"},
            symlinks={"/var/lib/flatpak/app/x/current": "/var/lib/flatpak/app/x/1.0"},
        )
        machine.read_text(deployed)
        machine.read_text("/var/lib/flatpak/app/x/current/files/es_systems.xml")
        assert machine.repeats() == {deployed: 2}

    def test_firmware_for_system_answers_what_the_catalogue_answers(self):
        # Threading the snapshot must not change the enumeration it produces.
        machine = FixtureMachine(self.FILES, cores=self.CORES)
        rd = atlas.RetroDeck(HOME, machine)
        answer = rd.firmware_for_system(system="n64")
        assert [c.core_so for c in answer.cores] == [
            e.core_so for e in rd.emulators_for("n64").entries
        ]


class TestCfgDirectorySpelling:
    """A cfg names its directories however whoever wrote the line spelled them.

    ``some/dir`` and ``some/dir/`` are one directory to the machine, and the
    keys travel straight from the cfg into ``path_kind`` (``_cfg_directory``),
    so an answer that depended on the trailing slash would report an unresolved
    core catalogue for an installation that is entirely fine.
    """

    INFO = f"{RD_DEPLOY_CORES}/pcsx2_libretro.info"

    def _machine(self, slash):
        return FixtureMachine(
            {
                RETRODECK_JSON: RD_JSON,
                RETRODECK_CFG: (
                    f'system_directory = "/mnt/sd/retrodeck/bios{slash}"\n'
                    f'libretro_info_path = "/app/cores{slash}"\n'
                    f'libretro_directory = "/app/cores{slash}"\n'
                    f'savefile_directory = "/mnt/sd/retrodeck/saves{slash}"\n'
                ),
                self.INFO: 'display_name = "LRPS2"\nfirmware_count = "1"\nfirmware0_path = "ps2/bios/rom1.bin"\n',
                "/mnt/sd/retrodeck/bios/ps2/bios/rom1.bin": "rom",
                "/mnt/sd/retrodeck/saves/.keep": "",
            },
            cores={f"{RD_DEPLOY_CORES}/pcsx2_libretro.so": {"library_name": "LRPS2"}},
        )

    def test_a_trailing_slash_on_a_cfg_directory_changes_no_answer(self):
        plain = atlas.RetroDeck(HOME, self._machine("")).firmware_inventory()
        slashed = atlas.RetroDeck(HOME, self._machine("/")).firmware_inventory()
        assert [c.core_so for c in slashed.cores] == [c.core_so for c in plain.cores]
        assert [c.core_so for c in plain.cores] == ["pcsx2_libretro.so"]
        assert [c.code for c in slashed.caveats] == [c.code for c in plain.caveats]
        assert [r.found for c in slashed.cores for r in c.requirements] == ["file"]


class TestBareRetroArch:
    def test_native_upstream_default_sorts_by_core(self):
        # config.def.h:982 — upstream defaults to sort-by-core.
        machine = FixtureMachine(
            {
                f"{HOME}/.config/retroarch/retroarch.cfg": 'savefile_directory = "~/saves"\n',
                f"{HOME}/saves/.keep": "",
                f"{HOME}/roms/gba/Game.zip": "",
            }
        )
        inst = atlas.NativeRetroArch(HOME, machine)
        p = inst.save_location(content_path=f"{HOME}/roms/gba/Game.zip")
        assert p.dir == f"{HOME}/saves/<library_name>"
        assert p.needs == ("library_name",)


class TestEveryHandleAnswersTheCatalogueQuestion:
    """The question is about an arrangement, so no handle may decline to answer it.

    Before, only RetroDECK had the method and a consumer had to narrow with
    ``isinstance`` — which meant deciding, without help, what the other
    arrangements would have said. They say it themselves now, and the two
    reasons for having nothing to say are different claims that must not
    collapse: one is a fact about the machine, the other is about atlas.
    """

    RA_FILES = {
        f"{HOME}/.config/retroarch/retroarch.cfg": 'savefile_directory = "~/saves"\n',
        f"{HOME}/saves/.keep": "",
    }
    EMUDECK_FILES = {
        EMUDECK_SETTINGS: 'romsPath="$HOME/Emulation/roms"\nsavesPath="$HOME/Emulation/saves"\n',
        STANDALONE_CFG: 'savefile_directory = "~/Emulation/saves"\n',
    }
    DEPLOY_ESDE = (
        "/var/lib/flatpak/app/net.retrodeck.retrodeck/current/active/files/retrodeck/components"
        "/es-de/share/es-de/resources/systems/linux/es_systems.xml"
    )

    def _only(self, files, **kwargs):
        return atlas.detect(HOME, FixtureMachine(files, **kwargs))[0]

    def test_a_bare_retroarch_states_the_arrangement_has_none(self):
        # A settled fact: RetroArch ships no frontend catalogue, and no
        # evidence work would change that. The evidence caveat beside it is a
        # different claim — no bare install has been observed live — and every
        # answer from an unverified arrangement carries it (atlas.evidence).
        answer = self._only(self.RA_FILES).emulators_for("n64")
        assert answer.entries == ()
        assert [c.code for c in answer.caveats] == [
            atlas.CAVEAT_EMULATOR_CATALOGUE_UNAVAILABLE,
            atlas.CAVEAT_ARRANGEMENT_UNVERIFIED,
        ]

    def test_an_emudeck_arrangement_states_that_atlas_has_not_established_it(self):
        # EmuDeck installs a frontend; which one, and where it keeps its
        # catalogue, is what nobody has established. Saying "none" here would
        # be atlas reporting its own gap as a property of the machine.
        answer = self._only(self.EMUDECK_FILES, dirs=[f"{HOME}/Emulation/saves"]).emulators_for("n64")
        assert answer.entries == ()
        assert [c.code for c in answer.caveats] == [
            atlas.CAVEAT_EMULATOR_CATALOGUE_UNESTABLISHED,
            atlas.CAVEAT_ARRANGEMENT_UNVERIFIED,
        ]

    def test_the_two_refusals_are_different_codes(self):
        # The whole point of two: a client rendering "no emulators" may act on
        # the first and must not act on the second.
        bare = self._only(self.RA_FILES).emulators_for("n64")
        emudeck = self._only(self.EMUDECK_FILES, dirs=[f"{HOME}/Emulation/saves"]).emulators_for("n64")
        assert bare.caveats[0].code != emudeck.caveats[0].code

    def test_the_systems_question_answers_the_same_way(self):
        answer = self._only(self.RA_FILES).systems()
        assert answer.systems == ()
        assert [c.code for c in answer.caveats] == [
            atlas.CAVEAT_EMULATOR_CATALOGUE_UNAVAILABLE,
            atlas.CAVEAT_ARRANGEMENT_UNVERIFIED,
        ]

    def test_an_unreadable_retrodeck_catalogue_is_not_an_empty_one(self):
        """The defect this answer object exists for, on the handle that has a catalogue.

        The read flag was computed and thrown away, so an ``es_systems.xml``
        that could not be read produced the same empty tuple as a catalogue
        that was read and declares nothing — and the caller could not tell that
        atlas had never looked.
        """
        machine = FixtureMachine(
            {RETRODECK_JSON: RD_JSON, self.DEPLOY_ESDE: {"status": "unreadable"}},
            dirs=["/mnt/sd/retrodeck/saves"],
        )
        rd = atlas.RetroDeck(HOME, machine)
        for answer in (rd.emulators_for("n64"), rd.systems()):
            assert [c.code for c in answer.caveats] == [atlas.CAVEAT_EMULATOR_CATALOGUE_UNREADABLE]
        assert rd.emulators_for("n64").entries == ()
        assert rd.systems().systems == ()

    def test_a_read_catalogue_that_knows_no_emulator_is_silent_on_a_healthy_installation(self):
        # The one empty answer a client may act on: read, and the frontend
        # genuinely knows nothing for this system. The empty caveat list is
        # this fixture's health talking too — on a broken installation the
        # findings sit here, which is why the client's test is the three
        # emulator-catalogue-* codes rather than `not answer.caveats`.
        machine = FixtureMachine(
            {
                RETRODECK_JSON: RD_JSON,
                self.DEPLOY_ESDE: '<?xml version="1.0"?>\n<systemList>\n  <system><name>n64</name>'
                '<path>%ROMPATH%/n64</path>\n    <command label="Mupen64Plus-Next">'
                "%EMULATOR_RETROARCH% -L %CORE_RETROARCH%/mupen64plus_next_libretro.so %ROM%"
                "</command>\n  </system>\n</systemList>\n",
            },
            dirs=["/mnt/sd/retrodeck/saves"],
        )
        answer = atlas.RetroDeck(HOME, machine).emulators_for("dreamcast")
        assert answer.entries == ()
        assert answer.caveats == ()


class TestEveryAnswerStatesTheInstallationsHealth:
    """A broken installation says so on every answer, not only on placements.

    The rule is blanket on purpose: a finding is a true statement about the
    installation whatever was asked, while a map of which finding affects which
    answer would have to be maintained and could rot silently. What broke is in
    the ``data``; judging relevance is the client's.

    The findings come from the reads each route already makes — never from a
    second ``health()`` call inside a query — so the one-read-per-source
    invariant above covers this wiring too.
    """

    ESDE = (
        "/var/lib/flatpak/app/net.retrodeck.retrodeck/current/active/files/retrodeck/components"
        "/es-de/share/es-de/resources/systems/linux/es_systems.xml"
    )
    ES_SYSTEMS = (
        '<?xml version="1.0"?>\n<systemList>\n  <system><name>n64</name>\n'
        '    <command label="Mupen64Plus-Next">retroarch -L '
        "/app/cores/mupen64plus_next_libretro.so %ROM%</command>\n  </system>\n</systemList>\n"
    )
    # A marker whose `saves_path` is not a string: present, parseable, and
    # unusable — so the roots fall back to defaults that do not exist either.
    BROKEN = {RETRODECK_JSON: '{"paths": {"rd_home_path": "/mnt/sd/retrodeck", "saves_path": 7}}'}
    HEALTHY = {RETRODECK_JSON: RD_JSON, "/mnt/sd/retrodeck/roms/systeminfo.txt": ""}
    HEALTHY_DIRS = ["/mnt/sd/retrodeck/saves"]
    CORE_SO = "mgba_libretro.so"

    def _answers(self, rd) -> dict[str, tuple[atlas.Caveat, ...]]:
        return {
            "save_location": rd.save_location(core_so=self.CORE_SO).caveats,
            "systems": rd.systems().caveats,
            "emulators_for": rd.emulators_for("n64").caveats,
            "firmware_for_core": rd.firmware_for_core(core_so=self.CORE_SO).caveats,
            "firmware_for_system": rd.firmware_for_system(system="n64").caveats,
            "firmware_inventory": rd.firmware_inventory().caveats,
            "identify_firmware": rd.identify_firmware(md5="deadbeef").caveats,
        }

    def test_the_fixture_is_broken_in_three_ways(self):
        assert _retrodeck(self.BROKEN).health().codes == (
            atlas.HEALTH_ISSUE_MARKER_INVALID,
            atlas.HEALTH_ISSUE_ROOT_MISSING,
            atlas.HEALTH_ISSUE_SAVES_ROOT_MISSING,
        )

    def test_every_answer_leads_with_the_findings(self):
        rd = _retrodeck(self.BROKEN)
        findings = list(rd.health().issues)
        led = {q: list(c[: len(findings)]) for q, c in self._answers(rd).items()}
        assert led == {question: findings for question in led}

    def test_each_finding_arrives_exactly_once(self):
        rd = _retrodeck(self.BROKEN)
        codes = rd.health().codes
        repeated = {
            question: sorted(c for c in codes if [x.code for x in caveats].count(c) != 1)
            for question, caveats in self._answers(rd).items()
        }
        assert {q: r for q, r in repeated.items() if r} == {}

    def test_a_finding_keeps_its_own_message(self):
        # The re-wrap tell: a category prefix or a rebuilt message shows here.
        rd = _retrodeck(self.BROKEN)
        finding = rd.health().issues[0]
        carried = rd.firmware_inventory().caveats[0]
        assert (carried.code, carried.message, dict(carried.data)) == (
            finding.code,
            finding.message,
            dict(finding.data),
        )

    def test_a_healthy_installation_adds_nothing(self):
        rd = _retrodeck(self.HEALTHY, dirs=self.HEALTHY_DIRS)
        health_codes = {
            atlas.HEALTH_ISSUE_MARKER_INVALID,
            atlas.HEALTH_ISSUE_ROOT_MISSING,
            atlas.HEALTH_ISSUE_SAVES_ROOT_MISSING,
        }
        stated = {
            question: [c.code for c in caveats if c.code in health_codes]
            for question, caveats in self._answers(rd).items()
        }
        assert {q: codes for q, codes in stated.items() if codes} == {}

    def test_the_entry_route_carries_them_too(self):
        # The composed path — a catalogue entry answering for its own core —
        # goes through the placement seam, so it should come free. Checked.
        rd = _retrodeck({**self.BROKEN, self.ESDE: self.ES_SYSTEMS})
        entry = rd.emulators_for("n64").entries[0]
        placement = entry.save_location()
        assert not isinstance(placement, atlas.Unresolved)
        carried = [c.code for c in placement.caveats]
        assert carried[: len(rd.health().codes)] == list(rd.health().codes)


class TestFirmwareResolvesTheSystemDirectoryLikeTheCardRoute:
    """Item 14's rule, on the other route: absent resolves, cleared refuses.

    The firmware route used to refuse both spellings with one code, so a
    machine whose config simply never names ``system_directory`` — the ordinary
    case for a stock RetroArch — was told there was no root to check against,
    while the card route had already resolved that same machine to RetroArch's
    own default.
    """

    CFG = f"{HOME}/.config/retroarch/retroarch.cfg"
    CONFIG_TREE = f"{HOME}/.config/retroarch"
    CORES = f"{HOME}/.config/retroarch/cores"
    INFO = 'display_name = "mGBA"\nsystemname = "GBA"\n'
    DIRS = 'libretro_info_path = "~/.config/retroarch/cores"\n'

    def _inventory(self, cfg: str, **kwargs):
        machine = FixtureMachine(
            {
                self.CFG: cfg,
                f"{self.CORES}/mgba_libretro.info": self.INFO,
                **kwargs.pop("files", {}),
            },
            **kwargs,
        )
        return atlas.NativeRetroArch(HOME, machine).firmware_inventory()

    def test_an_absent_key_resolves_to_the_platform_default(self):
        answer = self._inventory(self.DIRS, dirs=[f"{self.CONFIG_TREE}/system"])
        assert answer.root == f"{self.CONFIG_TREE}/system"

    def test_an_absent_key_refuses_nothing(self):
        answer = self._inventory(self.DIRS, dirs=[f"{self.CONFIG_TREE}/system"])
        assert atlas.CAVEAT_SYSTEM_DIRECTORY_CLEARED not in [c.code for c in answer.caveats]

    def test_a_resolved_default_that_is_not_there_is_stated_like_any_root(self):
        # Resolving is not asserting: the default directory may not exist, and
        # that is the same fact a configured missing root states.
        answer = self._inventory(self.DIRS)
        assert atlas.CAVEAT_FIRMWARE_ROOT_MISSING in [c.code for c in answer.caveats]

    def test_a_cleared_key_refuses_with_its_own_code(self):
        answer = self._inventory(f'system_directory = ""\n{self.DIRS}')
        assert answer.root is None
        assert atlas.CAVEAT_SYSTEM_DIRECTORY_CLEARED in [c.code for c in answer.caveats]

    def test_the_cleared_refusal_carries_the_value(self):
        answer = self._inventory(f'system_directory = "default"\n{self.DIRS}')
        cleared = next(c for c in answer.caveats if c.code == atlas.CAVEAT_SYSTEM_DIRECTORY_CLEARED)
        assert cleared.data == {"value": "default"}

    def test_a_configured_key_is_unchanged(self):
        answer = self._inventory(
            f'system_directory = "/bios"\n{self.DIRS}', dirs=["/bios"]
        )
        assert answer.root == "/bios"
        assert atlas.CAVEAT_SYSTEM_DIRECTORY_CLEARED not in [c.code for c in answer.caveats]

    def test_both_routes_resolve_an_absent_key_to_the_same_directory(self):
        # The point of sharing the helper: one directory, two routes.
        machine = FixtureMachine(
            {self.CFG: self.DIRS, f"{self.CORES}/mgba_libretro.info": self.INFO},
            dirs=[f"{self.CONFIG_TREE}/system"],
        )
        handle = atlas.NativeRetroArch(HOME, machine)
        placement = handle.save_location(core_so="flycast_libretro.so")
        assert handle.firmware_inventory().root == f"{self.CONFIG_TREE}/system"
        assert placement.dir.startswith(f"{self.CONFIG_TREE}/system")

    def test_a_dropped_line_is_stated_beside_the_resolved_default(self):
        # The silence this split would otherwise create: the key ends up absent
        # because RetroArch refused the line, so the answer resolves — and says
        # which line set nothing, or the configured path vanishes without trace.
        answer = self._inventory(
            f'system_directory "/bios"\n{self.DIRS}', dirs=[f"{self.CONFIG_TREE}/system"]
        )
        assert answer.root == f"{self.CONFIG_TREE}/system"
        dropped = next(c for c in answer.caveats if c.code == atlas.CAVEAT_CFG_LINE_DROPPED)
        assert dropped.data == {"key": "system_directory", "line": 'system_directory "/bios"'}

    def test_a_dropped_line_for_another_key_is_not_this_routes_business(self):
        # Only the key this route resolves is stated here; the save-layout keys
        # are the card route's to report, and stating them twice would double
        # every dropped line on a machine that asks both questions.
        answer = self._inventory(
            f'savefile_directory "/saves"\n{self.DIRS}', dirs=[f"{self.CONFIG_TREE}/system"]
        )
        assert atlas.CAVEAT_CFG_LINE_DROPPED not in [c.code for c in answer.caveats]
