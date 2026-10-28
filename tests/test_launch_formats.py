"""The install-first formats table: packaged shape, and a loader that never coerces."""

import json
import re
from pathlib import Path

import pytest

from atlas.launch_formats import (
    FORMATS_SCHEMA,
    load_launch_formats,
    load_standalone_launch,
    lookup_install_first,
    lookup_standalone_launch,
)


def _table(extension, entry):
    return json.dumps({"schema": FORMATS_SCHEMA, "systems": {"ps3": {extension: entry}}})


ENTRY = {"statement": "an installer", "installers": ["RPCS3"], "source": "[D] cited"}

# The catalogue RetroDECK deploys, where the installer tokens are checked
# against the commands that spell them.
DEPLOYED_SYSTEMS = Path(
    "/var/lib/flatpak/app/net.retrodeck.retrodeck/current/active/files/retrodeck/components"
    "/es-de/share/es-de/resources/systems/linux/es_systems.xml"
)


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

    @pytest.mark.parametrize(
        ("system", "extension", "installers"),
        [
            ("ps3", ".pkg", ("RPCS3",)),
            ("ps3", ".rap", ("RPCS3",)),
            ("ps3", ".edat", ("RPCS3",)),
            ("psvita", ".vpk", ("VITA3K",)),
            ("psvita", ".zip", ("VITA3K",)),
            ("psvita", ".pkg", ("VITA3K",)),
            ("psvita", ".rif", ("VITA3K",)),
            ("n3ds", ".cia", ("AZAHAR", "AZAHARPLUS")),
            ("n3ds", ".zcia", ("AZAHAR", "AZAHARPLUS")),
            ("ps4", ".pkg", ()),
        ],
    )
    def test_each_install_first_format_names_its_installers(self, system, extension, installers):
        # Issue #717: the installing emulators are data, in the record's order;
        # a PS4 package is install-first with nothing here to install it.
        record = lookup_install_first(system, extension)
        assert record is not None
        assert record.installers == installers

    def test_a_vita_bin_is_not_recorded(self):
        # Only the command line keys on the exact name work.bin; the dialogs
        # take any .bin, which a record keyed by extension cannot tell from
        # other .bin content.
        assert lookup_install_first("psvita", ".bin") is None

    @pytest.mark.skipif(not DEPLOYED_SYSTEMS.is_file(), reason="RetroDECK is not deployed here")
    def test_every_installer_token_is_one_the_deployed_system_spells(self):
        # A token is only joined to an entry when it is spelled the way the
        # catalogue's commands spell it. RetroDECK keeps AzaharPlus's command
        # in a comment, so the system's whole block is searched, comments
        # included.
        text = DEPLOYED_SYSTEMS.read_text(encoding="utf-8")
        for record in load_launch_formats():
            block = re.search(
                rf"<name>{re.escape(record.system)}</name>.*?</system>", text, re.DOTALL
            )
            assert block is not None, record.system
            for token in record.installers:
                assert f"%EMULATOR_{token}%" in block.group(0), (record.system, token)


def _card_table(entry):
    return json.dumps({"schema": FORMATS_SCHEMA, "emulators": {"AZAHAR": entry}})


class TestTheStandaloneLaunchCards:
    def test_the_azahar_card_is_recorded_and_matches_case_insensitively(self):
        # The gate is the emulator's loader, not ES-DE's case-exact scan.
        card = lookup_standalone_launch("AZAHAR")
        assert card is not None
        assert not card.archives
        assert card.takes(".3ds")
        assert card.takes(".3DS")
        assert not card.takes(".zip")

    @pytest.mark.parametrize("extension", [".cia", ".zcia"])
    def test_the_azahar_card_does_not_claim_what_it_installs(self, extension):
        # Issue #717: booting a .cia only offers to install it, and a .zcia
        # finds no loader — both came from the compress and decompress
        # dialogs' filters.
        card = lookup_standalone_launch("AZAHAR")
        assert card is not None
        assert not card.takes(extension)

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

    def test_an_entry_without_installers_fails(self):
        table = _table(".pkg", {"statement": "an installer", "source": "[D] cited"})
        with pytest.raises(ValueError, match="exactly"):
            load_launch_formats(table)

    def test_installers_must_be_a_list(self):
        table = _table(".pkg", {**ENTRY, "installers": "RPCS3"})
        with pytest.raises(ValueError, match="list of emulator tokens"):
            load_launch_formats(table)

    @pytest.mark.parametrize("token", ["", None, 3])
    def test_an_installer_must_be_a_non_empty_string(self, token):
        table = _table(".pkg", {**ENTRY, "installers": ["RPCS3", token]})
        with pytest.raises(ValueError, match="non-empty string"):
            load_launch_formats(table)

    def test_an_installer_named_twice_fails(self):
        table = _table(".pkg", {**ENTRY, "installers": ["RPCS3", "RPCS3"]})
        with pytest.raises(ValueError, match="listed twice"):
            load_launch_formats(table)

    def test_an_empty_installer_list_is_a_statement(self):
        # Nothing here installs it — still a record, never a refused load.
        (record,) = load_launch_formats(_table(".pkg", {**ENTRY, "installers": []}))
        assert record.installers == ()
