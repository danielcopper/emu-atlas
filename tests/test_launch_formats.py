"""The install-first formats table: packaged shape, and a loader that never coerces."""

import dataclasses
import json
import re
from collections.abc import Mapping
from pathlib import Path

import pytest

from atlas.launch_formats import (
    FORMATS_SCHEMA,
    StandaloneLaunchCard,
    load_launch_formats,
    load_standalone_launch,
    lookup_install_first,
    lookup_standalone_launch,
)
from atlas.standalone_saves import StandaloneSaveCard, load_standalone_saves
from tests.test_standalone_build_tripwire import COMPONENTS, deployed_binaries, deployed_build


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
        table = _card_table({**CARD, "accepts": [".3DS"], "source": "?"})
        with pytest.raises(ValueError, match="lowercase"):
            load_standalone_launch(table)

    def test_archives_must_be_a_boolean(self):
        table = _card_table({**CARD, "accepts": [".3ds"], "archives": "no", "source": "?"})
        with pytest.raises(ValueError, match="boolean"):
            load_standalone_launch(table)

    def test_a_card_names_exactly_its_three_fields(self):
        table = _card_table({"accepts": [".3ds"], "archives": False})
        with pytest.raises(ValueError, match="may also name"):
            load_standalone_launch(table)

    def test_a_card_naming_a_key_outside_the_seven_fails(self):
        table = _card_table({**CARD, "boots": True})
        with pytest.raises(ValueError, match="may also name"):
            load_standalone_launch(table)


CARD = {
    "accepts": [".iso"],
    "archives": False,
    "directories": {"recognises": [], "source": "[V] cited"},
    "source": "[V] cited",
}
DOLPHIN_ARGUMENTS = ("-b", "-e", "%ROM%")


class TestTheDirectoryStatement:
    """Issue #776 D5: every card says whether its loader starts a directory, and by what it recognises one."""

    def test_a_card_without_its_directory_statement_fails(self):
        table = _card_table({key: value for key, value in CARD.items() if key != "directories"})
        with pytest.raises(ValueError, match="'directories'"):
            load_standalone_launch(table)

    @pytest.mark.parametrize(
        ("statement", "message"),
        [
            ({"recognises": []}, "exactly 'recognises' and 'source'"),
            ({"recognises": "EBOOT.PBP", "source": "c"}, "distinct entry names"),
            ({"recognises": ["a/b"], "source": "c"}, "distinct entry names"),
            ({"recognises": ["X", "X"], "source": "c"}, "distinct entry names"),
            ({"recognises": [], "source": ""}, "non-empty string"),
        ],
    )
    def test_a_malformed_statement_fails(self, statement, message):
        table = _card_table({**CARD, "directories": statement})
        with pytest.raises(ValueError, match=message):
            load_standalone_launch(table)

    def test_the_names_load_in_their_order(self):
        (card,) = load_standalone_launch(
            _card_table({**CARD, "directories": {"recognises": ["EBOOT.PBP", "PSP_GAME"], "source": "c"}})
        )
        assert card.directories is not None
        assert card.directories.recognises == ("EBOOT.PBP", "PSP_GAME")

    @pytest.mark.parametrize("token", ["AZAHAR", "DOLPHIN", "DUCKSTATION", "PCSX2", "PRIMEHACK"])
    def test_five_loaders_start_no_directory(self, token):
        card = lookup_standalone_launch(token)
        assert card is not None
        assert card.directories is not None
        assert card.directories.recognises == ()

    def test_ppsspp_starts_a_directory_by_its_eboot_or_its_disc_folder(self):
        card = lookup_standalone_launch("PPSSPP")
        assert card is not None
        assert card.directories is not None
        assert card.directories.recognises == ("EBOOT.PBP", "PSP_GAME")


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


TAKES = "takes"
REFUSES = "refuses"
BUILD_DEPENDENT = "build-dependent"

# Issue #744: how each card answers every token of the accept-list ES-DE and
# RetroDECK deploy for its system (D1-D5, D9, D10), folded to lowercase as
# the card matches, and of the formats the decisions name beyond that list
# (PCSX2's .zip, .7z and .cue). A token the deployed list carries and this
# table does not is a token nobody decided, which the deployed-list test
# below turns red.
CARD_ANSWERS = {
    ("PCSX2", "ps2"): {
        **dict.fromkeys(
            [".iso", ".bin", ".img", ".mdf", ".nrg", ".ngr", ".chd", ".cso", ".zso", ".gz", ".dump", ".elf"], TAKES
        ),
        **dict.fromkeys([".zip", ".7z", ".ciso", ".isz", ".cue", ".m3u", ".desktop"], REFUSES),
    },
    ("PPSSPP", "psp"): {
        **dict.fromkeys([".chd", ".cso", ".iso", ".pbp", ".elf", ".prx", ".zip"], TAKES),
        ".7z": REFUSES,
    },
    ("DUCKSTATION", "psx"): {
        **dict.fromkeys(
            [".cue", ".bin", ".img", ".iso", ".mds", ".m3u", ".chd", ".ecm", ".pbp", ".exe", ".psexe"], TAKES
        ),
        **dict.fromkeys([".psf", ".minipsf"], TAKES),
        **dict.fromkeys([".toc", ".mdf", ".cbn", ".z", ".znx", ".7z", ".zip"], REFUSES),
        ".ccd": BUILD_DEPENDENT,
    },
}


def _answer(token: str, extension: str) -> str:
    card = lookup_standalone_launch(token)
    assert card is not None, token
    if card.build_note(extension) is not None:
        return BUILD_DEPENDENT
    return TAKES if card.takes(extension) else REFUSES


class TestTheCardsOfIssue744:
    """PCSX2, PPSSPP and DuckStation, read at the builds RetroDECK ships."""

    @pytest.mark.parametrize(
        ("token", "extension", "answer"),
        [(token, ext, answer) for (token, _), table in CARD_ANSWERS.items() for ext, answer in table.items()],
    )
    def test_each_card_answers_its_lists_tokens_as_decided(self, token, extension, answer):
        assert _answer(token, extension) == answer
        assert _answer(token, extension.upper()) == answer

    @pytest.mark.parametrize("token", ["PCSX2", "PPSSPP", "DUCKSTATION"])
    def test_every_take_reads_the_content_after(self, token):
        card = lookup_standalone_launch(token)
        assert card is not None
        assert card.content_checked

    def test_only_ppsspp_opens_an_archive(self):
        # D3: PPSSPP boots the image inside a .zip; PCSX2 and DuckStation open none.
        assert [token for token in ("PCSX2", "PPSSPP", "DUCKSTATION") if _card(token).archives] == ["PPSSPP"]

    @pytest.mark.parametrize("extension", [".ps-exe", ".psx"])
    def test_duckstation_takes_the_executable_suffixes_no_list_carries(self, extension):
        # D9: both builds boot them as executables.
        assert _answer("DUCKSTATION", extension) == TAKES

    @pytest.mark.parametrize(
        ("extension", "builds"),
        [(".ccd", ("1f6de046b", "2dd5cc3c")), (".cpe", ("1f6de046b", "2dd5cc3c")), (".elf", ("1f6de046b", "2dd5cc3c"))],
    )
    def test_duckstation_leaves_what_upstream_adds_to_the_build(self, extension, builds):
        # D5: the upstream build EmuDeck installs takes these, RetroDECK's does not.
        note = _card("DUCKSTATION").build_note(extension)
        assert note is not None
        assert all(build in note for build in builds)

    @pytest.mark.parametrize(
        ("token", "extension"),
        [("DUCKSTATION", ".psxgpu"), ("DUCKSTATION", ".zst"), ("DUCKSTATION", ".xz"), ("PCSX2", ".gs")],
    )
    def test_a_gpu_dump_replay_is_refused_on_every_build(self, token, extension):
        # D9: a replay starts no game, so it is no build-dependent take either.
        assert _answer(token, extension) == REFUSES

    @pytest.mark.parametrize("token", ["PCSX2", "PPSSPP"])
    def test_the_build_emudeck_installs_decides_nothing_differently(self, token):
        assert _card(token).build_dependent == ()

    @pytest.mark.parametrize(
        ("token", "arguments"),
        [("PCSX2", ("-batch", "%ROM%")), ("PPSSPP", ("%ROM%",)), ("DUCKSTATION", ("-batch", "%ROM%"))],
    )
    def test_each_card_answers_for_the_command_it_was_read_for(self, token, arguments):
        card = _card(token)
        assert card.arguments == (arguments,)
        assert not card.covers(None)

    @pytest.mark.skipif(not DEPLOYED_SYSTEMS.is_file(), reason="RetroDECK is not deployed here")
    @pytest.mark.parametrize(("token", "system"), list(CARD_ANSWERS))
    def test_every_deployed_list_token_is_decided_and_every_command_is_covered(self, token, system):
        from atlas.installations import (
            _emulator_arguments,  # pyright: ignore[reportPrivateUsage] - the catalogue's commands are read as the answer reads them
        )

        text = DEPLOYED_SYSTEMS.read_text(encoding="utf-8")
        block = re.search(rf"<name>{system}</name>.*?</system>", text, re.DOTALL)
        assert block is not None, system
        listed = re.search(r"<extension>([^<]*)</extension>", block.group(0))
        assert listed is not None, system
        undecided = {ext.lower() for ext in listed.group(1).split()} - set(CARD_ANSWERS[(token, system)])
        assert undecided == set(), f"{system} lists {sorted(undecided)}, which no decision covers for {token}"
        commands = re.findall(rf"<command[^>]*>([^<]*%EMULATOR_{token}%[^<]*)</command>", block.group(0))
        assert commands, token
        for command in commands:
            assert _card(token).covers(_emulator_arguments(command)), command


def _card(token: str):
    card = lookup_standalone_launch(token)
    assert card is not None, token
    return card


# The cards that pin no build: read before issue #744 gave a card its `build`,
# their revisions stated in their source alone. A new card pins its build, or
# joins this set on purpose.
UNPINNED_CARDS = frozenset({"AZAHAR", "DOLPHIN", "PRIMEHACK"})


def _pinned_cards() -> tuple[StandaloneLaunchCard, ...]:
    return tuple(card for card in load_standalone_launch() if card.build is not None)


def _save_cards() -> dict[str, StandaloneSaveCard]:
    return {save.token: save for save in load_standalone_saves()}


def deployed_build_disagreements(
    cards: tuple[StandaloneLaunchCard, ...], binaries: Mapping[str, bytes]
) -> list[str]:
    """The pinned loader cards whose deployed binary runs another of the save card's builds.

    *binaries* maps a token to its deployed binary's bytes, as the stamp
    tripwire reads them. A binary that carries none of the save card's stamps
    is not judged here: the stamp tripwire names it.
    """
    saves = _save_cards()
    wrong: list[str] = []
    for card in cards:
        if card.token not in binaries or card.token not in saves:
            continue
        found = deployed_build(saves[card.token], binaries[card.token])
        if found is not None and found.revision != card.build:
            wrong.append(
                f"{card.token}: the card was read at {card.build!r} and the deployed binary carries the "
                f"stamp of {found.revision!r}"
            )
    return wrong


class TestEveryPinnedCardNamesABuildItsSaveCardPins:
    """D5: a card's build is one the save card holds against the deployed binary."""

    def test_the_cards_with_no_build_are_exactly_the_listed_ones(self):
        assert {card.token for card in load_standalone_launch() if card.build is None} == UNPINNED_CARDS

    @pytest.mark.parametrize("card", _pinned_cards(), ids=lambda c: c.token)
    def test_the_build_is_pinned_by_the_save_card_and_cited_by_the_source(self, card):
        saves = _save_cards()
        assert card.token in saves, f"{card.token} pins a build and has no save card holding it"
        assert card.build in [build.revision for build in saves[card.token].builds], (
            f"{card.token} names {card.build!r}, which its save card does not pin — the stamp test "
            "holds the deployed binary against the save card's builds only"
        )
        assert card.build in card.source

    @pytest.mark.parametrize("card", _pinned_cards(), ids=lambda c: c.token)
    def test_a_binary_carrying_the_named_builds_stamp_passes(self, card):
        stamp = next(build.stamp for build in _save_cards()[card.token].builds if build.revision == card.build)
        assert deployed_build_disagreements((card,), {card.token: b"\x00" + stamp.encode() + b"\x00"}) == []

    @pytest.mark.parametrize("card", _pinned_cards(), ids=lambda c: c.token)
    def test_a_binary_carrying_another_pinned_builds_stamp_is_named(self, card):
        # A save card pinning two builds — Vita3K's does — keeps a loader card
        # naming the older one green on the list alone; the deployed binary
        # says which of them runs.
        other = dataclasses.replace(card, build="0ther0ne")
        stamp = next(build.stamp for build in _save_cards()[card.token].builds if build.revision == card.build)
        named = deployed_build_disagreements((other,), {card.token: b"\x00" + stamp.encode() + b"\x00"})
        assert [entry.split(":")[0] for entry in named] == [card.token]

    def test_every_deployed_emulator_runs_the_build_its_card_was_read_at(self):
        if not COMPONENTS.is_dir():
            pytest.skip(f"nothing is deployed at {COMPONENTS}")
        binaries = deployed_binaries()
        if not any(card.token in binaries for card in _pinned_cards()):
            pytest.skip("no emulator a pinned loader card describes is deployed here")
        assert deployed_build_disagreements(_pinned_cards(), binaries) == []

    @pytest.mark.parametrize("build", ["", " ", " abc1234", 3, None])
    def test_a_build_that_names_no_revision_fails_the_load(self, build):
        table = _card_table({**CARD, "build": build})
        with pytest.raises(ValueError, match="build must be"):
            load_standalone_launch(table)

    def test_a_build_loads(self):
        (card,) = load_standalone_launch(_card_table({**CARD, "build": "abc1234"}))
        assert card.build == "abc1234"
