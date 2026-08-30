"""Tests for atlas.duckstation — the DataRoot probe and the BIOS recognition table.

The vectors carry the answers; these carry the reading's corners: which of two
roots speaks, what one settings value becomes, and how the emulator's own
preference rule ranks the files a directory happens to hold.
"""

import json

import pytest

from atlas import duckstation
from atlas.machine import FixtureMachine

HOME = "/home/deck"
CONFIG_HOME = f"{HOME}/.config"
DATA_HOME = f"{HOME}/.local/share"
CONFIG_INI = f"{CONFIG_HOME}/duckstation/settings.ini"
DATA_INI = f"{DATA_HOME}/duckstation/settings.ini"

# Real rows of the packaged table, so a test that stops agreeing with it fails
# rather than passing against an invention.
SCPH5501 = "490f666e1afb15b7362b406ed1cea246"  # NTSC-U, priority 5
SCPH5502 = "32736f17079d0b2b7024407c39bd3050"  # PAL, priority 5
SCPH1001 = "dc2b9bf8da62ec93e868cfd29f0d067d"  # NTSC-U, priority 10
PS2_KDL = "93ea3bcee4252627919175ff1b16a1d9"  # PAL, priority 150


def _read(files=None, **kwargs):
    return duckstation.read_settings(
        FixtureMachine(files or {}, **kwargs), config_home=CONFIG_HOME, data_home=DATA_HOME
    )


def _candidate(md5: str | None, path: str = "/bios/image.bin"):
    table = duckstation.bios_table()
    return duckstation.BiosCandidate(path=path, image=None if md5 is None else table.identify(md5))


class TestWhichRootSpeaks:
    def test_the_config_side_is_read_first(self):
        read = _read({CONFIG_INI: "[BIOS]\nSearchDirectory = /from/config\n"})
        assert read.stated_path == CONFIG_INI
        assert read.values[("BIOS", "SearchDirectory")] == "/from/config"

    def test_the_data_side_speaks_where_the_config_one_has_no_file(self):
        read = _read({DATA_INI: "[BIOS]\nSearchDirectory = /from/data\n"})
        assert read.stated_path == DATA_INI
        assert read.root == f"{DATA_HOME}/duckstation"

    def test_a_file_that_cannot_be_read_is_named_rather_than_skipped(self):
        # Stepping to the other candidate would answer from a file this launch
        # does not use — the read failed, and that is its own state.
        read = _read({CONFIG_INI: {"status": "unreadable"}, DATA_INI: "[BIOS]\n"})
        assert read.unreadable == CONFIG_INI
        assert read.values == {}

    def test_no_file_anywhere_is_ambiguous_not_empty(self):
        read = _read({})
        assert read.ambiguous
        assert read.stated_path is None
        assert read.root == f"{DATA_HOME}/duckstation"

    def test_a_pinned_launch_reads_only_the_config_side(self):
        # Inside a flatpak, XDG_CONFIG_HOME is force-pinned set, so the
        # environment-unset branch is unreachable: a settings.ini on the data
        # side is a file this launch would never open.
        assert _read({DATA_INI: "[BIOS]\nSearchDirectory = /from/data\n"}).stated_path == DATA_INI
        pinned = duckstation.read_settings(
            FixtureMachine({DATA_INI: "[BIOS]\nSearchDirectory = /from/data\n"}),
            config_home=CONFIG_HOME,
            data_home=DATA_HOME,
            xdg_pinned=True,
        )
        assert pinned.stated_path is None
        assert pinned.root == f"{CONFIG_HOME}/duckstation"

    def test_a_pinned_launch_with_no_file_is_not_ambiguous(self):
        # One candidate is not a choice: the DataRoot is settled and the tree
        # below it is simply empty, which is not a degradation.
        pinned = duckstation.read_settings(
            FixtureMachine({}), config_home=CONFIG_HOME, data_home=DATA_HOME, xdg_pinned=True
        )
        assert not pinned.ambiguous
        assert pinned.root == f"{CONFIG_HOME}/duckstation"

    def test_a_pinned_launch_still_reads_the_config_side_file(self):
        pinned = duckstation.read_settings(
            FixtureMachine({CONFIG_INI: "[BIOS]\nSearchDirectory = /from/config\n"}),
            config_home=CONFIG_HOME,
            data_home=DATA_HOME,
            xdg_pinned=True,
        )
        assert pinned.stated_path == CONFIG_INI
        assert pinned.values[("BIOS", "SearchDirectory")] == "/from/config"


class TestOneSettingBecomesADirectory:
    ROOT = "/root"

    def test_an_unset_value_is_the_default_below_the_root(self):
        assert duckstation.load_path({}, self.ROOT, "BIOS", "SearchDirectory", "bios") == (
            "/root/bios"
        )

    def test_an_empty_value_is_the_default_too(self):
        values = {("BIOS", "SearchDirectory"): ""}
        assert duckstation.load_path(values, self.ROOT, "BIOS", "SearchDirectory", "bios") == (
            "/root/bios"
        )

    def test_a_relative_value_hangs_off_the_root(self):
        values = {("BIOS", "SearchDirectory"): "images/ps1"}
        assert duckstation.load_path(values, self.ROOT, "BIOS", "SearchDirectory", "bios") == (
            "/root/images/ps1"
        )

    def test_a_degenerate_relative_spelling_composes_the_way_the_combine_does(self):
        # The compose is Path::Combine (settings.cpp:1958-1959 at 64655818e),
        # which collapses the separator run and strips the trailing separator
        # (#325) — os.path.join would have preserved both.
        values = {("BIOS", "SearchDirectory"): "images//ps1/"}
        assert duckstation.load_path(values, self.ROOT, "BIOS", "SearchDirectory", "bios") == (
            "/root/images/ps1"
        )

    def test_an_absolute_value_is_taken_as_it_stands(self):
        values = {("BIOS", "SearchDirectory"): "/mnt/sd/bios"}
        assert duckstation.load_path(values, self.ROOT, "BIOS", "SearchDirectory", "bios") == (
            "/mnt/sd/bios"
        )

    def test_a_case_variant_spelling_is_the_same_key(self):
        # CSimpleIniA matches sections and keys ASCII case-insensitively
        # (ini_settings_interface.h:65 at 64655818e), so [bios] searchdirectory
        # governs exactly as the canonical spelling would (#295).
        values = {("bios", "searchdirectory"): "images/ps1"}
        assert duckstation.load_path(values, self.ROOT, "BIOS", "SearchDirectory", "bios") == (
            "/root/images/ps1"
        )


class TestThePerGameLayerIsGatedByTheSwitchThatLoadsIt:
    """#302: ``[Main] ApplyGameSettings`` decides whether there is a layer at all.

    ``UpdateGameSettingsLayer`` loads nothing while it is false
    (system.cpp:1410), so an emulator switched off that way has no per-game
    layer to state — and stating one would be a claim about a file the launch
    never opens. Absent and unreadable-as-a-boolean are not off: the compiled
    default is true (settings.cpp:162, settings_interface.h:77-81).
    """

    def test_an_absent_switch_leaves_the_layer_on(self):
        assert duckstation.applies_game_settings({}) is True

    @pytest.mark.parametrize("raw", ["false", "no", "off", "0", "disabled", "FALSE", "Off"])
    def test_a_value_the_emulator_reads_as_false_switches_it_off(self, raw):
        assert duckstation.applies_game_settings({("Main", "ApplyGameSettings"): raw}) is False

    @pytest.mark.parametrize("raw", ["true", "yes", "on", "1", "enabled", "TRUE"])
    def test_a_value_the_emulator_reads_as_true_leaves_it_on(self, raw):
        assert duckstation.applies_game_settings({("Main", "ApplyGameSettings"): raw}) is True

    def test_a_value_the_emulator_cannot_read_leaves_the_default_standing(self):
        # GetBoolValue keeps the caller's default when FromChars<bool> yields
        # nothing, and that default is true — so an unreadable value is not a
        # licence to fall silent.
        values = {("Main", "ApplyGameSettings"): "sometimes"}
        assert duckstation.applies_game_settings(values) is True

    def test_a_case_variant_spelling_governs_the_way_simpleini_matches(self):
        assert duckstation.applies_game_settings({("main", "applygamesettings"): "off"}) is False


class TestThePerGameLayerStatedBesideAnAnswer:
    """#302: what a listing of the gamesettings directory is allowed to claim."""

    DIR = "/root/gamesettings"
    KEYS = ("[MemoryCards] Card1Type", "[MemoryCards] Card2Type")
    ONE_KEY = ("[BIOS] PathNTSCU",)

    def _caveats(self, files=None, **kwargs):
        return duckstation.per_game_caveats(
            FixtureMachine(files or {}, **kwargs),
            token="DUCKSTATION",
            directory=self.DIR,
            keys=self.KEYS,
            governs="which card each slot holds",
            read_through="settings.cpp:391-401",
        )

    def test_files_there_are_counted(self):
        caveats = self._caveats(
            {f"{self.DIR}/SLUS-00594.ini": "[MemoryCards]\nCard1Type = Shared\n"},
            dirs=[self.DIR],
        )
        assert caveats[0].data["count"] == "1"

    def test_the_count_names_the_directory_it_listed(self):
        caveats = self._caveats(
            {f"{self.DIR}/SLUS-00594.ini": "[MemoryCards]\n"}, dirs=[self.DIR]
        )
        assert caveats[0].data["dir"] == self.DIR

    def test_the_count_names_every_key_that_answer_depends_on(self):
        caveats = self._caveats(
            {f"{self.DIR}/SLUS-00594.ini": "[MemoryCards]\n"}, dirs=[self.DIR]
        )
        assert caveats[0].data["key"] == "[MemoryCards] Card1Type, [MemoryCards] Card2Type"

    def test_an_empty_directory_says_nothing_at_all(self):
        # Silence is available here — unlike Dolphin, DuckStation ships no
        # second layer inside its own build — and it means this answer holds
        # for every game.
        assert self._caveats(dirs=[self.DIR]) == []

    def test_a_directory_that_is_not_there_says_nothing_either(self):
        assert self._caveats() == []

    def test_a_directory_that_cannot_be_listed_says_the_check_did_not_happen(self):
        caveats = self._caveats(dirs=[self.DIR], unlistable=[self.DIR])
        assert caveats[0].code == "per-game-layer-unread"

    def test_a_failed_listing_never_asserts_that_per_game_files_exist(self):
        caveats = self._caveats(dirs=[self.DIR], unlistable=[self.DIR])
        assert [c.code for c in caveats if c.code == "per-game-overrides-present"] == []

    def test_a_failed_listing_still_names_the_directory_it_tried(self):
        caveats = self._caveats(dirs=[self.DIR], unlistable=[self.DIR])
        assert caveats[0].data["dir"] == self.DIR

    def test_only_ini_files_are_layer_candidates(self):
        # GetGameSettingsPath composes "<serial>.ini" and nothing else, so a
        # stray file in the directory is not a game carrying an override.
        caveats = self._caveats({f"{self.DIR}/notes.txt": "hello"}, dirs=[self.DIR])
        assert caveats == []

    def test_the_sandbox_variant_names_the_value_it_could_not_spell(self):
        stated = duckstation.per_game_unread_caveat(
            token="DUCKSTATION",
            directory="/app/gamesettings",
            keys=self.KEYS,
            governs="which card each slot holds",
            read_through="settings.cpp:391-401",
            sandbox_value="/app/gamesettings",
        )
        assert "only the emulator's sandbox can spell" in stated.message

    def test_one_key_reads_as_singular(self):
        # A sentence that says "the key ... are read" is the #301 defect; the
        # verb is derived beside the noun.
        caveats = duckstation.per_game_caveats(
            FixtureMachine({f"{self.DIR}/SLUS-00594.ini": ""}, dirs=[self.DIR]),
            token="DUCKSTATION",
            directory=self.DIR,
            keys=self.ONE_KEY,
            governs="which image the launch loads",
            read_through="bios.cpp:321-338",
        )
        assert "the key [BIOS] PathNTSCU" in caveats[0].message

    def test_one_key_takes_the_singular_verb_too(self):
        caveats = duckstation.per_game_caveats(
            FixtureMachine({f"{self.DIR}/SLUS-00594.ini": ""}, dirs=[self.DIR]),
            token="DUCKSTATION",
            directory=self.DIR,
            keys=self.ONE_KEY,
            governs="which image the launch loads",
            read_through="bios.cpp:321-338",
        )
        assert "is read through that layer" in caveats[0].message

    def test_several_keys_take_the_plural_verb(self):
        caveats = self._caveats(
            {f"{self.DIR}/SLUS-00594.ini": ""}, dirs=[self.DIR]
        )
        assert "are read through that layer" in caveats[0].message


class TestTheTableIsTheEmulatorsOwn:
    def test_it_carries_the_revision_it_was_read_at(self):
        assert duckstation.bios_table().meta["revision"]

    def test_every_row_is_reachable_by_its_content(self):
        table = duckstation.bios_table()
        for image in table.images:
            assert table.identify(image.md5) is image

    def test_a_hash_is_matched_whatever_case_it_arrives_in(self):
        assert duckstation.bios_table().identify(SCPH5501.upper()) is not None

    def test_content_the_table_does_not_know_is_none_not_an_error(self):
        assert duckstation.bios_table().identify("0" * 32) is None

    def test_the_three_sizes_the_search_keeps_are_the_emulators_own(self):
        # PS1, PS2 and PS3 images, ascending — bios.h:24-26.
        assert duckstation.bios_table().sizes == (524288, 4089584, 4194304)

    def test_a_file_of_any_other_size_is_skipped_before_its_bytes_are_read(self):
        table = duckstation.bios_table()
        assert table.accepts_size(524288)
        assert not table.accepts_size(524287)

    def test_a_size_that_could_not_be_read_is_not_a_yes(self):
        assert not duckstation.bios_table().accepts_size(None)

    def test_every_row_states_a_region_the_answer_can_carry(self):
        assert {image.region for image in duckstation.bios_table().images} <= {
            "ntsc-u",
            "ntsc-j",
            "pal",
            "any",
        }


class TestWhichImageWouldBoot:
    def test_nothing_to_choose_from_is_no_pick(self):
        assert duckstation.bios_table().pick((), "any") is None

    def test_a_known_image_is_never_displaced_by_an_unknown_one(self):
        table = duckstation.bios_table()
        pick = table.pick(
            (_candidate(None, "/bios/a.bin"), _candidate(SCPH1001, "/bios/b.bin")), "any"
        )
        assert pick is not None
        assert pick.chosen.path == "/bios/b.bin"
        assert pick.decided

    def test_between_two_known_images_the_lower_priority_number_holds(self):
        table = duckstation.bios_table()
        pick = table.pick(
            (_candidate(PS2_KDL, "/bios/ps2.bin"), _candidate(SCPH5501, "/bios/ps1.bin")), "any"
        )
        assert pick is not None
        assert pick.chosen.path == "/bios/ps1.bin"

    def test_a_region_match_outranks_a_lower_priority_number(self):
        # PathPAL is empty and the disc is PAL: the PAL image at priority 150
        # beats the NTSC-U one at 5, because the region test comes first.
        table = duckstation.bios_table()
        pick = table.pick(
            (_candidate(SCPH5501, "/bios/us.bin"), _candidate(PS2_KDL, "/bios/pal.bin")), "pal"
        )
        assert pick is not None
        assert pick.chosen.path == "/bios/pal.bin"

    def test_two_images_that_rank_alike_are_reported_as_a_tie(self):
        table = duckstation.bios_table()
        pick = table.pick(
            (_candidate(SCPH5501, "/bios/us.bin"), _candidate(SCPH5502, "/bios/eu.bin")), "any"
        )
        assert pick is not None
        assert not pick.decided
        assert {c.path for c in pick.tied} == {"/bios/us.bin", "/bios/eu.bin"}

    def test_two_unknown_images_tie_as_well(self):
        table = duckstation.bios_table()
        pick = table.pick((_candidate(None, "/bios/a.bin"), _candidate(None, "/bios/b.bin")), "any")
        assert pick is not None
        assert not pick.decided

    def test_a_console_of_no_stated_region_matches_every_image(self):
        table = duckstation.bios_table()
        for image in table.images:
            assert table.matches_region(image, "any")


class TestTheLoaderRefusesAMalformedTable:
    def test_a_table_without_images_is_refused(self):
        text = json.dumps({"sizes": {"ps1": 1}, "images": []})
        with pytest.raises(ValueError, match="images"):
            duckstation.load_bios_table(text)

    def test_a_table_without_sizes_is_refused(self):
        text = json.dumps({"images": [{"name": "x", "region": "pal", "md5": "a", "priority": 1}]})
        with pytest.raises(ValueError, match="sizes"):
            duckstation.load_bios_table(text)

    def test_a_row_missing_a_field_names_the_field(self):
        text = json.dumps({"sizes": {"ps1": 1}, "images": [{"name": "x", "region": "pal"}]})
        with pytest.raises(ValueError, match="md5"):
            duckstation.load_bios_table(text)

    ROW = {"name": "x", "region": "pal", "md5": "a", "priority": 1, "fast_boot_patch": "y"}

    def test_a_table_without_the_openbios_block_is_refused(self):
        # The offset speaks in a caveat's sentence; a silent default would
        # ship "offset None" instead of failing the load.
        text = json.dumps({"sizes": {"ps1": 1}, "images": [self.ROW], "_meta": {"revision": "r"}})
        with pytest.raises(ValueError, match="openbios"):
            duckstation.load_bios_table(text)

    def test_a_table_without_its_revision_is_refused(self):
        # The revision rides in the identified-image caveat's data; an empty
        # pin would claim the table without saying which table.
        openbios = {"signature": "OpenBIOS", "offset": 120}
        text = json.dumps({"sizes": {"ps1": 1}, "images": [self.ROW], "openbios": openbios})
        with pytest.raises(ValueError, match="_meta"):
            duckstation.load_bios_table(text)
