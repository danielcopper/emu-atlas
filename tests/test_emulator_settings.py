"""Tests for atlas.emulator_settings — the one address per settings file.

The point of this table is negative: after it, a disagreement between two
questions about where one emulator keeps one file is not expressible. So the
tests that matter most are the two crossing ones — every card names a file the
table carries, and every file the table carries is named by a card.
"""

import json

import pytest

from atlas.emulator_settings import (
    EMULATOR_SETTINGS_SCHEMA,
    load_emulator_settings,
    settings_file,
    settings_files,
)
from atlas.mods import load_standalone_mod_cards
from atlas.standalone_saves import load_standalone_saves
from atlas.textures import load_standalone_texture_packs

HOMES = {"config_home": "/home/deck/.config", "data_home": "/home/deck/.local/share"}


def _table(**files) -> str:
    return json.dumps(
        {
            "schema": EMULATOR_SETTINGS_SCHEMA,
            "emulators": {
                "DEMO": {
                    "files": files
                    or {
                        "demo.ini": {
                            "bases": ["config"],
                            "path": "demo/demo.ini",
                            "citation": "[V] a citation",
                        }
                    }
                }
            },
        }
    )


class TestOneAddressPerFile:
    def test_a_single_base_answers_one_location(self):
        file = settings_file("PCSX2", "PCSX2.ini")
        assert file.only(**HOMES) == "/home/deck/.config/PCSX2/inis/PCSX2.ini"

    def test_the_base_is_the_emulators_own_rather_than_a_habit(self):
        # xemu keeps its settings under the data home; every route that reads
        # it now learns that here instead of carrying the fact itself.
        assert settings_file("XEMU", "xemu.toml").only(**HOMES) == (
            "/home/deck/.local/share/xemu/xemu/xemu.toml"
        )

    def test_several_bases_come_back_in_probe_order(self):
        assert settings_file("DUCKSTATION", "settings.ini").locations(**HOMES) == (
            "/home/deck/.config/duckstation/settings.ini",
            "/home/deck/.local/share/duckstation/settings.ini",
        )

    def test_a_file_whose_root_varies_refuses_to_answer_one_location(self):
        # Answering the first candidate would be a guess dressed as an address:
        # which one this launch opens is what the probe is for.
        with pytest.raises(ValueError, match="decided by the launch"):
            settings_file("DUCKSTATION", "settings.ini").only(**HOMES)

    def test_an_emulator_the_table_does_not_carry_fails_loudly(self):
        with pytest.raises(ValueError, match="no settings file"):
            settings_file("NOBODY", "any.ini")

    def test_an_emulator_with_no_entry_lists_nothing(self):
        assert settings_files("NOBODY") == {}


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
            )
            for card in cards
            if card.settings is not None
        }
        # melonDS's legacy ini is read by the config reader rather than named
        # by a card: it is the file the emulator migrates from, not one a card
        # points a caller at.
        named.add(("MELONDS", "melonDS.ini"))
        stated = {
            (token, name)
            for token in {t for t, _ in named} | {"MELONDS"}
            for name in settings_files(token)
        }
        assert stated - named == set()


class TestTheLoaderRefusesWhatItCannotStand:
    def test_an_unsupported_schema_is_refused(self):
        text = json.dumps({"schema": EMULATOR_SETTINGS_SCHEMA + 1})
        with pytest.raises(ValueError, match="unsupported schema"):
            load_emulator_settings(text)

    def test_an_emulator_stating_no_file_is_refused(self):
        text = json.dumps({"schema": EMULATOR_SETTINGS_SCHEMA, "emulators": {"DEMO": {"files": {}}}})
        with pytest.raises(ValueError, match="states no file"):
            load_emulator_settings(text)

    def test_a_base_outside_the_vocabulary_is_refused(self):
        text = _table(**{"demo.ini": {"bases": ["cache"], "path": "d/demo.ini", "citation": "x"}})
        with pytest.raises(ValueError, match="bases"):
            load_emulator_settings(text)

    def test_a_repeated_base_is_refused(self):
        spec = {"bases": ["config", "config"], "path": "d/demo.ini", "citation": "x"}
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
        spec = {"bases": ["config"], "path": "demo/other.ini", "citation": "x"}
        text = _table(**{"demo.ini": spec})
        with pytest.raises(ValueError, match="the key is the file's own name"):
            load_emulator_settings(text)

    def test_an_entry_without_a_citation_is_refused(self):
        text = _table(**{"demo.ini": {"bases": ["config"], "path": "demo/demo.ini"}})
        with pytest.raises(ValueError, match="bases/path/citation"):
            load_emulator_settings(text)

    def test_every_shipped_entry_carries_its_evidence(self):
        for token, files in load_emulator_settings().items():
            for name, file in files.items():
                assert file.citation, f"{token}/{name} states an address and no evidence"
