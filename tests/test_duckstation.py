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
