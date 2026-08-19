"""The install-first formats table: packaged shape, and a loader that never coerces."""

import json

import pytest

from atlas.launch_formats import (
    load_launch_formats,
    load_standalone_launch,
    lookup_install_first,
    lookup_standalone_launch,
)


def _table(extension, entry):
    return json.dumps({"schema": 1, "systems": {"ps3": {extension: entry}}})


ENTRY = {"statement": "an installer", "source": "[D] cited"}


class TestThePackagedTable:
    def test_the_psn_package_is_recorded(self):
        record = lookup_install_first("ps3", ".pkg")
        assert record is not None
        assert record.source.startswith("[")

    def test_lookup_is_exact_and_case_sensitive(self):
        # The keys are tokens the way ES-DE derives them — a .PKG file yields
        # a different token, and this table never smooths that over.
        assert lookup_install_first("ps3", ".PKG") is None
        assert lookup_install_first("psx", ".pkg") is None


def _card_table(entry):
    return json.dumps({"schema": 1, "emulators": {"AZAHAR": entry}})


class TestTheStandaloneLaunchCards:
    def test_the_azahar_card_is_recorded_and_matches_case_insensitively(self):
        # The gate is the emulator's loader, not ES-DE's case-exact scan.
        card = lookup_standalone_launch("AZAHAR")
        assert card is not None
        assert not card.archives
        assert card.takes(".3ds")
        assert card.takes(".3DS")
        assert not card.takes(".zip")

    def test_an_unknown_token_is_none(self):
        assert lookup_standalone_launch("RPCS3") is None
        assert lookup_standalone_launch(None) is None

    def test_an_uppercase_accept_token_fails_the_load(self):
        # The match lowercases the file's token, so an uppercase card token
        # could never be hit — refused rather than recorded dead.
        table = _card_table({"accepts": [".3DS"], "archives": False, "source": "?"})
        with pytest.raises(ValueError, match="lowercase"):
            load_standalone_launch(table)

    def test_archives_must_be_a_boolean(self):
        table = _card_table({"accepts": [".3ds"], "archives": "no", "source": "?"})
        with pytest.raises(ValueError, match="boolean"):
            load_standalone_launch(table)

    def test_a_card_names_exactly_its_three_fields(self):
        table = _card_table({"accepts": [".3ds"], "archives": False})
        with pytest.raises(ValueError, match="exactly"):
            load_standalone_launch(table)


class TestTheLoaderRefusesWhatItCannotState:
    def test_a_token_without_its_dot_fails(self):
        table = _table("pkg", ENTRY)
        with pytest.raises(ValueError, match="'.'"):
            load_launch_formats(table)

    def test_the_bare_dot_sentinel_is_not_a_format(self):
        table = _table(".", ENTRY)
        with pytest.raises(ValueError, match="names one"):
            load_launch_formats(table)

    def test_an_entry_without_a_source_fails(self):
        table = _table(".pkg", {"statement": "an installer"})
        with pytest.raises(ValueError, match="exactly"):
            load_launch_formats(table)

    def test_an_unknown_schema_fails(self):
        with pytest.raises(ValueError, match="schema"):
            load_launch_formats('{"schema": 9, "systems": {}}')
