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
        with pytest.raises(ValueError, match="may also name"):
            load_standalone_launch(table)

    def test_a_card_naming_a_key_outside_the_six_fails(self):
        table = _card_table({**CARD, "boots": True})
        with pytest.raises(ValueError, match="may also name"):
            load_standalone_launch(table)


CARD = {"accepts": [".iso"], "archives": False, "source": "[V] cited"}
DOLPHIN_ARGUMENTS = ("-b", "-e", "%ROM%")


class TestTheDolphinAndPrimeHackCards:
    """Issue #618 (D6): both loaders take a file by extension, case folded, and open no archive."""

    @pytest.mark.parametrize("token", ["DOLPHIN", "PRIMEHACK"])
    @pytest.mark.parametrize("extension", [".zip", ".7z", ".ZIP"])
    def test_neither_loader_takes_an_archive(self, token, extension):
        card = lookup_standalone_launch(token)
        assert card is not None
        assert not card.archives
        assert not card.takes(extension)
        assert card.build_note(extension) is None

    @pytest.mark.parametrize("token", ["DOLPHIN", "PRIMEHACK"])
    @pytest.mark.parametrize("extension", [".rvz", ".ISO", ".wbfs", ".m3u8", ".nfs", ".json", ".dff", ".wad"])
    def test_both_loaders_take_their_extensions_and_read_the_content_after(self, token, extension):
        card = lookup_standalone_launch(token)
        assert card is not None
        assert card.takes(extension)
        assert card.content_checked

    def test_dolphin_takes_a_bin_at_every_build_it_serves(self):
        card = lookup_standalone_launch("DOLPHIN")
        assert card is not None
        assert card.takes(".bin")
        assert card.build_note(".bin") is None

    @pytest.mark.parametrize("extension", [".bin", ".BIN"])
    def test_primehack_leaves_a_bin_to_the_build(self, extension):
        # RetroDECK's build leaves .bin out of the disc set and the one EmuDeck
        # installs carries it: neither a take nor a refusal holds everywhere.
        card = lookup_standalone_launch("PRIMEHACK")
        assert card is not None
        assert not card.takes(extension)
        note = card.build_note(extension)
        assert note is not None
        assert "81bfb96" in note
        assert "53f53e0f" in note

    @pytest.mark.parametrize("token", ["DOLPHIN", "PRIMEHACK"])
    def test_both_cards_answer_for_the_command_their_loader_was_read_for(self, token):
        card = lookup_standalone_launch(token)
        assert card is not None
        assert card.covers(DOLPHIN_ARGUMENTS)
        assert not card.covers(("%ROM%",))
        assert not card.covers(None)

    @pytest.mark.skipif(not DEPLOYED_SYSTEMS.is_file(), reason="RetroDECK is not deployed here")
    def test_every_deployed_command_running_a_card_is_one_it_answers_for(self):
        # A card read for one command shape answers nothing for another, so a
        # deployed catalogue that changes the shape turns this red instead of
        # quietly leaving its entries unestablished.
        from atlas.installations import (
            _emulator_arguments,  # pyright: ignore[reportPrivateUsage] - the catalogue's commands are read as the answer reads them
        )

        text = DEPLOYED_SYSTEMS.read_text(encoding="utf-8")
        for token in ("DOLPHIN", "PRIMEHACK"):
            card = lookup_standalone_launch(token)
            assert card is not None
            commands = re.findall(rf"<command[^>]*>([^<]*%EMULATOR_{token}%[^<]*)</command>", text)
            assert commands, token
            for command in commands:
                assert card.covers(_emulator_arguments(command)), command

    def test_the_azahar_card_states_none_of_the_three(self):
        # Azahar's card is read as it was before issue #618: every command
        # naming its token, and no statement about the content.
        card = lookup_standalone_launch("AZAHAR")
        assert card is not None
        assert card.arguments is None
        assert card.covers(None)
        assert not card.content_checked
        assert card.build_dependent == ()


class TestTheCardKeysOfIssue618:
    def test_the_three_keys_load(self):
        (card,) = load_standalone_launch(
            _card_table(
                {
                    **CARD,
                    "arguments": [list(DOLPHIN_ARGUMENTS)],
                    "content_checked": True,
                    "build_dependent": {".bin": "[V] two builds disagree"},
                }
            )
        )
        assert card.arguments == (DOLPHIN_ARGUMENTS,)
        assert card.content_checked
        assert card.build_note(".BIN") == "[V] two builds disagree"

    @pytest.mark.parametrize(
        "arguments",
        [[], "-e %ROM%", [[]], [["-e", ""]], [["-e", 3]], [["-b"]], [["%ROM%", "%ROM%"]], [["%ROM%"], ["%ROM%"]]],
    )
    def test_arguments_name_distinct_lists_handing_the_file_once(self, arguments):
        table = _card_table({**CARD, "arguments": arguments})
        with pytest.raises(ValueError, match="arguments"):
            load_standalone_launch(table)

    def test_content_checked_must_be_a_boolean(self):
        table = _card_table({**CARD, "content_checked": "yes"})
        with pytest.raises(ValueError, match="content_checked"):
            load_standalone_launch(table)

    @pytest.mark.parametrize(
        ("build_dependent", "match"),
        [
            ({}, "non-empty object"),
            ([".bin"], "non-empty object"),
            ({"bin": "cited"}, "'.'"),
            ({".BIN": "cited"}, "lowercase"),
            ({".bin": ""}, "non-empty string"),
            ({".iso": "cited"}, "also in accepts"),
        ],
    )
    def test_build_dependent_names_extensions_outside_accepts_with_a_citation(self, build_dependent, match):
        table = _card_table({**CARD, "build_dependent": build_dependent})
        with pytest.raises(ValueError, match=match):
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
