"""Tests for atlas.emulator_settings — the one address per settings file.

The point of this table is negative: after it, a disagreement between two
questions about where one emulator keeps one file is not expressible. So the
tests that matter most are the two crossing ones — every card names a file the
table carries, and every file the table carries is named by a card.

Since #246 the emulator's own directory is stated here too, once per emulator
rather than once per path, and per installation where the name belongs to the
build rather than to the emulator.
"""

import json

import pytest

from atlas.emulator_settings import (
    EMULATOR_SETTINGS_SCHEMA,
    emulator_directory,
    load_emulator_settings,
    settings_file,
    settings_files,
    user_directory,
)
from atlas.mods import load_standalone_mod_cards
from atlas.standalone_saves import load_standalone_saves
from atlas.standalone_savestates import load_standalone_savestates
from atlas.textures import load_standalone_texture_packs

HOMES = {"config_home": "/home/deck/.config", "data_home": "/home/deck/.local/share"}
HOST = {**HOMES, "flatpak": None}


def _anchors(
    segment: str = "demo", binary: str = "demo/bin/demo", **extra: str
) -> dict[str, object]:
    return {**extra, "binary": binary, "names": {segment: {"literal": segment}}}


def _directory(name: str = "demo", **extra: object) -> dict[str, object]:
    return {
        "name": name,
        "citation": "[V] a citation for the directory",
        "anchors": _anchors(name),
        **extra,
    }


def _table(directory=None, **files) -> str:
    return json.dumps(
        {
            "schema": EMULATOR_SETTINGS_SCHEMA,
            "emulators": {
                "DEMO": {
                    "directory": directory or _directory(),
                    "files": files
                    or {
                        "demo.ini": {
                            "bases": ["config"],
                            "path": "demo.ini",
                            "citation": "[V] a citation",
                        }
                    },
                }
            },
        }
    )


class TestOneAddressPerFile:
    def test_a_single_base_answers_one_location(self):
        file = settings_file("PCSX2", "PCSX2.ini")
        assert file.only(**HOST) == "/home/deck/.config/PCSX2/inis/PCSX2.ini"

    def test_the_base_is_the_emulators_own_rather_than_a_habit(self):
        # xemu keeps its settings under the data home; every route that reads
        # it now learns that here instead of carrying the fact itself.
        assert settings_file("XEMU", "xemu.toml").only(**HOST) == (
            "/home/deck/.local/share/xemu/xemu/xemu.toml"
        )

    def test_several_bases_come_back_in_probe_order(self):
        assert settings_file("DUCKSTATION", "settings.ini").locations(**HOST) == (
            "/home/deck/.config/duckstation/settings.ini",
            "/home/deck/.local/share/duckstation/settings.ini",
        )

    def test_a_file_whose_root_varies_refuses_to_answer_one_location(self):
        # Answering the first candidate would be a guess dressed as an address:
        # which one this launch opens is what the probe is for.
        with pytest.raises(ValueError, match="decided by the launch"):
            settings_file("DUCKSTATION", "settings.ini").only(**HOST)

    def test_an_emulator_the_table_does_not_carry_fails_loudly(self):
        with pytest.raises(ValueError, match="no settings file"):
            settings_file("NOBODY", "any.ini")

    def test_a_file_the_emulator_does_not_state_fails_loudly(self):
        # The other way to ask for an unstated address: the emulator is
        # carried, the file name is not.
        with pytest.raises(ValueError, match="no settings file"):
            settings_file("PCSX2", "nowhere.ini")

    def test_an_emulator_with_no_entry_lists_nothing(self):
        assert settings_files("NOBODY") == {}


class TestTheEmulatorsOwnDirectory:
    """Stated once per emulator — and per build where the name is the build's."""

    def test_two_files_of_one_emulator_hang_off_one_stated_directory(self):
        # Dolphin keeps its save settings and its graphics settings apart, and
        # the directory they share is written down once for both.
        assert settings_file("DOLPHIN", "Dolphin.ini").only(**HOST) == (
            "/home/deck/.config/dolphin-emu/Dolphin.ini"
        )
        assert settings_file("DOLPHIN", "GFX.ini").only(**HOST) == (
            "/home/deck/.config/dolphin-emu/GFX.ini"
        )

    def test_the_directory_can_be_asked_for_on_its_own(self):
        # The save trees and the texture trees hang off it too, and they are
        # not settings files — so the name answers without one.
        assert user_directory("PCSX2", flatpak=None) == "PCSX2"

    def test_an_installation_that_spells_it_differently_says_so(self):
        # PrimeHack's user directory is the build's, not the emulator's: the
        # revision RetroDECK ships spells it 'primehack', the one Flathub ships
        # spells it 'dolphin-emu'.
        assert user_directory("PRIMEHACK", flatpak=None) == "primehack"
        assert user_directory("PRIMEHACK", flatpak="io.github.shiiion.primehack") == "dolphin-emu"

    def test_the_override_moves_every_file_of_that_emulator(self):
        assert settings_file("PRIMEHACK", "GFX.ini").only(
            **HOMES, flatpak="io.github.shiiion.primehack"
        ) == "/home/deck/.config/dolphin-emu/GFX.ini"

    def test_an_unrelated_flatpak_leaves_the_default_standing(self):
        # Dolphin's own flatpak spells the directory the same way, so naming it
        # must not be mistaken for an override of someone else's.
        assert user_directory("PRIMEHACK", flatpak="org.DolphinEmu.dolphin-emu") == "primehack"

    def test_each_spelling_carries_its_own_evidence(self):
        directory = emulator_directory("PRIMEHACK")
        assert directory.stated(None).citation != directory.stated(
            "io.github.shiiion.primehack"
        ).citation

    def test_every_shipped_emulator_states_a_directory_with_evidence(self):
        for token, entry in load_emulator_settings().items():
            assert entry.directory.default.name, f"{token} states no directory"
            assert entry.directory.default.citation, f"{token} names a directory and no evidence"

    def test_an_emulator_the_table_does_not_carry_has_no_directory(self):
        with pytest.raises(ValueError, match="no directory is stated"):
            user_directory("NOBODY", flatpak=None)


class TestTheCardsAndTheTableAgree:
    """The crossing tests — what makes a disagreement inexpressible."""

    def test_every_save_card_names_a_file_the_table_carries(self):
        for card in load_standalone_saves():
            if card.settings is not None:
                assert settings_file(card.token, card.settings)

    def test_every_texture_card_names_a_file_the_table_carries(self):
        for card in load_standalone_texture_packs():
            assert settings_file(card.token, card.settings)

    def test_every_mod_card_names_a_file_the_table_carries(self):
        for card in load_standalone_mod_cards():
            if card.settings is not None:
                assert settings_file(card.token, card.settings)

    def test_every_savestate_card_names_a_file_the_table_carries(self):
        for card in load_standalone_savestates():
            if card.settings is not None:
                assert settings_file(card.token, card.settings)

    def test_the_table_carries_no_file_no_card_asks_for(self):
        # An address nobody reads outlives the question it was written for and
        # goes stale unnoticed — the same reason an anchor for nothing is
        # refused.
        named = {
            (card.token, card.settings)
            for cards in (
                load_standalone_saves(),
                load_standalone_texture_packs(),
                load_standalone_mod_cards(),
                load_standalone_savestates(),
            )
            for card in cards
            if card.settings is not None
        }
        # melonDS's legacy ini is read by the config reader rather than named
        # by a card: it is the file the emulator migrates from, not one a card
        # points a caller at.
        named.add(("MELONDS", "melonDS.ini"))
        # Every token the *table* carries, not every token a card names: taking
        # the token set from the cards made an emulator with a table row and no
        # card invisible here — its addresses could never be the ones nobody
        # asks for, because it was never looked at.
        stated = {
            (token, name)
            for token in load_emulator_settings()
            for name in settings_files(token)
        }
        assert stated - named == set()


class TestTheLoaderRefusesWhatItCannotStand:
    def test_an_unsupported_schema_is_refused(self):
        text = json.dumps({"schema": EMULATOR_SETTINGS_SCHEMA + 1})
        with pytest.raises(ValueError, match="unsupported schema"):
            load_emulator_settings(text)

    def test_an_emulator_stating_no_file_is_refused(self):
        text = json.dumps(
            {
                "schema": EMULATOR_SETTINGS_SCHEMA,
                "emulators": {
                    "DEMO": {"directory": _directory(), "files": {}}
                },
            }
        )
        with pytest.raises(ValueError, match="states no file"):
            load_emulator_settings(text)

    def test_an_emulator_stating_no_directory_is_refused(self):
        text = json.dumps(
            {
                "schema": EMULATOR_SETTINGS_SCHEMA,
                "emulators": {
                    "DEMO": {
                        "files": {
                            "demo.ini": {"bases": ["config"], "path": "demo.ini", "citation": "x"}
                        }
                    }
                },
            }
        )
        with pytest.raises(ValueError, match="exactly directory/files"):
            load_emulator_settings(text)

    def test_a_directory_without_a_citation_is_refused(self):
        text = _table(directory={**_directory(), "citation": ""})
        with pytest.raises(ValueError, match="citation"):
            load_emulator_settings(text)

    def test_an_absolute_directory_is_refused(self):
        text = _table(directory={**_directory(), "name": "/opt/demo"})
        with pytest.raises(ValueError, match="relative path"):
            load_emulator_settings(text)

    def test_an_override_that_repeats_the_default_is_refused(self):
        # An override stating the same name reads as "established for this
        # installation" while establishing nothing.
        text = _table(
            directory=_directory(
                installations={
                    "org.demo.Demo": {
                        "name": "demo",
                        "citation": "y",
                        "anchors": _anchors("demo", "bin/demo", flatpak="org.demo.Demo"),
                    }
                }
            )
        )
        with pytest.raises(ValueError, match="repeats the default name"):
            load_emulator_settings(text)

    def test_an_override_without_evidence_is_refused(self):
        text = _table(
            directory=_directory(installations={"org.demo.Demo": {"name": "other"}})
        )
        with pytest.raises(ValueError, match="exactly name/citation"):
            load_emulator_settings(text)

    def test_a_path_that_repeats_the_directory_is_refused(self):
        # The directory is named once for the emulator; spelling it into the
        # path again is how the same name comes to be written down twice.
        spec = {"bases": ["config"], "path": "demo/demo.ini", "citation": "x"}
        text = _table(**{"demo.ini": spec})
        with pytest.raises(ValueError, match="begins with the emulator's own directory"):
            load_emulator_settings(text)

    def test_a_path_that_repeats_an_installations_spelling_is_refused(self):
        # The guard covers every stated spelling: a path spelled with an
        # override installation's directory name is the same second copy.
        directory = _directory(
            installations={
                "org.demo.Demo": {
                    "name": "other",
                    "citation": "y",
                    "anchors": _anchors("other", "bin/demo", flatpak="org.demo.Demo"),
                }
            }
        )
        spec = {"bases": ["config"], "path": "other/demo.ini", "citation": "x"}
        text = _table(directory=directory, **{"demo.ini": spec})
        with pytest.raises(ValueError, match="begins with the emulator's own directory 'other'"):
            load_emulator_settings(text)

    def test_a_base_outside_the_vocabulary_is_refused(self):
        text = _table(**{"demo.ini": {"bases": ["cache"], "path": "demo.ini", "citation": "x"}})
        with pytest.raises(ValueError, match="bases"):
            load_emulator_settings(text)

    def test_a_repeated_base_is_refused(self):
        spec = {"bases": ["config", "config"], "path": "demo.ini", "citation": "x"}
        text = _table(**{"demo.ini": spec})
        with pytest.raises(ValueError, match="repeats a base"):
            load_emulator_settings(text)

    def test_an_absolute_path_is_refused(self):
        text = _table(**{"demo.ini": {"bases": ["config"], "path": "/etc/demo.ini", "citation": "x"}})
        with pytest.raises(ValueError, match="relative path"):
            load_emulator_settings(text)

    def test_a_path_that_climbs_out_of_the_base_is_refused(self):
        spec = {"bases": ["config"], "path": "../demo.ini", "citation": "x"}
        text = _table(**{"demo.ini": spec})
        with pytest.raises(ValueError, match="relative path"):
            load_emulator_settings(text)

    def test_a_key_that_is_not_the_files_own_name_is_refused(self):
        # Two spellings of one file is exactly what this table exists to
        # prevent, so the key and the path's last segment must agree.
        spec = {"bases": ["config"], "path": "inis/other.ini", "citation": "x"}
        text = _table(**{"demo.ini": spec})
        with pytest.raises(ValueError, match="the key is the file's own name"):
            load_emulator_settings(text)

    def test_an_entry_without_a_citation_is_refused(self):
        text = _table(**{"demo.ini": {"bases": ["config"], "path": "demo.ini"}})
        with pytest.raises(ValueError, match="bases/path/citation"):
            load_emulator_settings(text)

    def test_every_shipped_entry_carries_its_evidence(self):
        for token, entry in load_emulator_settings().items():
            for name, file in entry.files.items():
                assert file.citation, f"{token}/{name} states an address and no evidence"
