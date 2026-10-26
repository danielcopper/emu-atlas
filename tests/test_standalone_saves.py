"""A save card's redirect block, and the save answer that carries it (#638).

Whether one launch can move an emulator's saves elsewhere — by a flag, a
variable or its working directory — is read out of the emulator's source at
the build the card pins, and stated once, on the card. The loader refuses a
block it cannot read rather than carrying it, and the save answer hands the
card's routes over unchanged: a list, empty where none exists, for a
standalone emulator, and ``None`` — nothing stated — for a RetroArch core.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest

import atlas
from atlas.placement import SavefilePlacement
from atlas.installations import (
    _redirect_setting_outranks,  # pyright: ignore[reportPrivateUsage] - the comparison D7 asks for, held directly
)
from atlas.placement import UNKNOWN_FILE_SET, Granularity, OptionReading
from atlas.standalone_saves import (
    REDIRECT_SCOPES,
    SAVES_SCHEMA,
    RedirectSetting,
    SaveRedirect,
    load_standalone_saves,
    lookup_standalone_save_card,
)
from tests.test_machine_vectors import fixture_machine

VECTORS = Path(__file__).resolve().parent.parent / "vectors" / "machines"
STANDALONE = "standalone-saves.json"
DOLPHIN_GCI = "dolphin-keeps-one-gci-folder-per-region-and-the-region-is-a-hole"
# A source reference as the cards write one: a file with its extension, then a
# line or a range — "UICommon.cpp:304-311", or ":53" continuing the file before.
CITED_LINE = re.compile(r"\w\.(?:cpp|cc|c|h|mm)(?::\d+)|(?<![\w.]):\d+(?:-\d+)?")


def _route(**changed: Any) -> dict[str, Any]:
    return {
        "via": "flag",
        "name": "--saves",
        "argument": "<dir>",
        "moves": ["saves"],
        "citation": "main.cpp:1-2",
        **changed,
    }


def _table(redirects: Any = None, *, absent: bool = False) -> str:
    card: dict[str, Any] = {
        "saves": {"settings": "demo.ini", "systems": ["gc"]},
        "provenance": {"source": "[V] a citation"},
    }
    if not absent:
        card["redirects"] = (
            redirects if redirects is not None else {"routes": [_route()], "provenance": "searched main.cpp"}
        )
    return json.dumps({"schema": SAVES_SCHEMA, "emulators": {"DEMO": card}})


def _block(*routes: dict[str, Any]) -> dict[str, Any]:
    return {"routes": list(routes), "provenance": "searched main.cpp"}


class TestEveryPackagedCardStatesItsRedirects:
    def test_every_card_states_a_block(self):
        # The loader takes an absent block as "not stated", so the shipped data
        # is what has to hold every card to stating one — an emulator nobody
        # looked at must not read as one without a route.
        unstated = [card.token for card in load_standalone_saves() if card.redirects is None]
        assert unstated == []

    def test_every_block_says_what_was_searched(self):
        for card in load_standalone_saves():
            assert card.redirects_provenance, card.token
            assert CITED_LINE.search(card.redirects_provenance), card.token

    def test_every_route_cites_its_source_lines(self):
        for card in load_standalone_saves():
            for route in card.redirects or ():
                assert CITED_LINE.search(route.citation), (card.token, route.name, route.argument)

    def test_the_cards_offering_no_route_are_the_three_the_source_reading_found(self):
        # Vita3K's XDG_DATA_HOME moves its file system and writes the moved
        # path into config.yml, so it is no redirect (the card's provenance).
        none = {card.token for card in load_standalone_saves() if card.redirects == ()}
        assert none == {"XEMU", "MELONDS", "VITA3K"}

    def test_the_working_directory_route_is_azahars(self):
        card = lookup_standalone_save_card("AZAHAR")
        assert card is not None
        assert card.redirects is not None
        route = next(r for r in card.redirects if r.via == atlas.REDIRECT_VIA_WORKING_DIRECTORY)
        assert (route.name, route.argument, route.moves) == (None, "<dir>/user/", REDIRECT_SCOPES)

    def test_dolphins_c_routes_move_no_savestates(self):
        # D4: -C is a redirect with its limits, and the first of them is that
        # no key it can set moves the savestates.
        for token in ("DOLPHIN", "PRIMEHACK"):
            card = lookup_standalone_save_card(token)
            assert card is not None, token
            assert card.redirects is not None, token
            routes = [r for r in card.redirects if r.name == "-C"]
            assert routes, token
            assert all(atlas.REDIRECT_MOVES_SAVESTATES not in r.moves for r in routes), token

    def test_the_routes_a_configuration_can_outrank_name_their_settings(self):
        # D7: each route names the settings that rank above it or that it
        # depends on, as structure — read off each citation and the source.
        named = {
            (card.token, route.name, route.argument): tuple(s.key for s in route.outranked_by)
            for card in load_standalone_saves()
            for route in card.redirects or ()
            if route.outranked_by
        }
        dolphin_xdg = (
            "[Core] MemcardAPath",
            "[Core] MemcardBPath",
            "[Core] GCIFolderAPath",
            "[Core] GCIFolderBPath",
            "[Core] GCIFolderAPathOverride",
            "[Core] GCIFolderBPathOverride",
            "[General] NANDRootPath",
        )
        expected: dict[tuple[str, str | None, str], tuple[str, ...]] = {
            ("CEMU", "XDG_DATA_HOME", "<dir>"): ("mlc_path",)
        }
        expected[("AZAHAR", "XDG_DATA_HOME", "<dir>")] = (
            "[Data Storage] sdmc_directory",
            "[Data Storage] nand_directory",
        )
        for token in ("DOLPHIN", "PRIMEHACK"):
            expected[(token, "XDG_DATA_HOME", "<dir>")] = dolphin_xdg
            expected[(token, "-C", "Dolphin.GBA.SavesPath=<dir>")] = ("[GBA] SavesInRomPath",)
            for slot in "AB":
                expected[(token, "-C", f"Dolphin.Core.GCIFolder{slot}PathOverride=<dir>")] = (f"[Core] Slot{slot}",)
                expected[(token, "-C", f"Dolphin.Core.Memcard{slot}Path=<file>")] = (f"[Core] Slot{slot}",)
        assert named == expected

    def test_no_card_states_an_appimage_route(self):
        # D5: the APPIMAGE routes are named in the provenance, never stated.
        for card in load_standalone_saves():
            assert all(r.name != "APPIMAGE" for r in card.redirects or ()), card.token


class TestTheLoaderReadsTheBlock:
    def test_a_stated_block_loads_its_routes(self):
        (card,) = load_standalone_saves(_table())
        assert card.redirects == (
            SaveRedirect(via="flag", name="--saves", argument="<dir>", moves=("saves",), citation="main.cpp:1-2"),
        )
        assert card.redirects_provenance == "searched main.cpp"

    def test_an_empty_list_is_the_stated_none(self):
        (card,) = load_standalone_saves(_table(_block()))
        assert card.redirects == ()

    def test_an_absent_block_states_nothing(self):
        (card,) = load_standalone_saves(_table(absent=True))
        assert card.redirects is None
        assert card.redirects_provenance is None

    def test_a_route_names_the_settings_that_outrank_it(self):
        route = _route(outranked_by=[{"file": "demo.ini", "key": "[Core] Slot"}])
        (card,) = load_standalone_saves(_table(_block(route)))
        assert card.redirects is not None
        assert card.redirects[0].outranked_by == (RedirectSetting(file="demo.ini", key="[Core] Slot"),)

    def test_a_working_directory_route_names_nothing(self):
        route = _route(via="working-directory", name=None, argument="<dir>/user/")
        (card,) = load_standalone_saves(_table(_block(route)))
        assert card.redirects is not None
        assert card.redirects[0].name is None


class TestTheLoaderRefusesWhatItCannotRead:
    @pytest.mark.parametrize(
        ("block", "message"),
        [
            pytest.param(_block(_route(moves=["saves", "keys"])), "moves must name values of", id="unknown-scope"),
            pytest.param(_block(_route(via="config-file")), "route.via must be one of", id="unknown-via"),
            pytest.param(_block({**_route(), "limits": "x"}), "a route must state exactly", id="stray-route-key"),
            pytest.param(
                {**_block(_route()), "searched": "x"}, "must state exactly routes/provenance", id="stray-block-key"
            ),
            pytest.param({"routes": [_route()]}, "must state exactly routes/provenance", id="no-provenance"),
            pytest.param({**_block(), "provenance": " "}, "non-blank", id="blank-provenance"),
            pytest.param({"routes": {}, "provenance": "x"}, "routes must be a list", id="routes-not-a-list"),
            pytest.param(
                _block(_route(via="working-directory", argument="<dir>/user/")),
                "a working-directory route names nothing",
                id="working-directory-named",
            ),
            pytest.param(_block(_route(name=None)), "names its flag or variable", id="flag-unnamed"),
            pytest.param(_block(_route(argument="")), "route.argument", id="empty-argument"),
            pytest.param(_block(_route(citation=" ")), "route.citation", id="blank-citation"),
            pytest.param(_block(_route(moves="saves")), "moves must be a list of strings", id="moves-not-a-list"),
            pytest.param(_block(_route(moves=["firmware", "saves"])), "in (", id="moves-out-of-order"),
            pytest.param(_block(_route(moves=["saves", "saves"])), "each value once", id="moves-twice"),
            pytest.param(
                _block(_route(moves=["configuration", "firmware"])),
                "moves neither saves nor savestates",
                id="moves-no-save",
            ),
            pytest.param(_block(_route(), _route(citation="other.cpp:3")), "one route twice", id="route-twice"),
            pytest.param([_route()], "must state exactly routes/provenance", id="block-not-an-object"),
            pytest.param(_block(_route(outranked_by=[])), "outranked_by must be a non-empty list", id="outranked-empty"),
            pytest.param(
                _block(_route(outranked_by=[{"file": "demo.ini"}])), "exactly file/key", id="outranking-setting-shape"
            ),
            pytest.param(
                _block(_route(outranked_by=[{"file": "other.ini", "key": "k"}])),
                "the one file the answer reads",
                id="outranking-setting-in-another-file",
            ),
            pytest.param(
                _block(_route(outranked_by=[{"file": "demo.ini", "key": "k"}, {"file": "demo.ini", "key": "k"}])),
                "names one setting twice",
                id="outranking-setting-twice",
            ),
        ],
    )
    def test_the_block_is_refused(self, block, message):
        table = _table(block)
        with pytest.raises(ValueError, match=re.escape(message)):
            load_standalone_saves(table)

    def test_the_type_refuses_an_unknown_scope_built_by_hand(self):
        with pytest.raises(ValueError, match="moves must name values of"):
            SaveRedirect(
                via="flag",
                name="-x",
                argument="<dir>",
                moves=("saves", "keys"),  # type: ignore[arg-type]
                citation="a.c:1",
            )


def _vector(file: str, name: str) -> dict[str, Any]:
    data = json.loads((VECTORS / file).read_text())
    return next(v for v in data["vectors"] if v["name"] == name)


def _entry_answer(file: str, name: str) -> SavefilePlacement:
    vector = _vector(file, name)
    inp = vector["input"]
    query = inp["entry_savefile_query"]
    (install,) = atlas.detect(inp["home"], fixture_machine(inp))
    entry = install.emulators_for(query["system"]).entries[0]
    answer = entry.savefile_location(cwd=query.get("cwd"))
    assert isinstance(answer, SavefilePlacement), name
    return answer


class TestTheSaveAnswerCarriesTheCardsRoutes:
    def test_a_standalone_answer_carries_the_cards_routes(self):
        answer = _entry_answer(STANDALONE, DOLPHIN_GCI)
        card = lookup_standalone_save_card("DOLPHIN")
        assert card is not None
        assert answer.redirects == card.redirects
        assert answer.redirects

    def test_an_emulator_with_no_route_answers_an_empty_list(self):
        answer = _entry_answer(STANDALONE, "xemus-saves-live-inside-the-emulated-hard-disk")
        assert answer.redirects == ()
        assert atlas.savefile_placement_contract(answer)["redirects"] == []

    def test_a_retroarch_answer_states_nothing(self):
        vector = _vector("named-cases.json", "retrodeck-sort-by-content-concrete")
        inp = vector["input"]
        (install,) = atlas.detect(inp["home"], fixture_machine(inp))
        answer = install.savefile_location(content_path=inp["savefile_query"]["content_path"])
        assert isinstance(answer, SavefilePlacement)
        assert answer.redirects is None
        assert atlas.savefile_placement_contract(answer)["redirects"] is None

    def test_the_serialized_route_carries_no_citation(self):
        # Prose stays out of the contract: a caller acts on how, what and where,
        # and the citation is the card's to keep.
        answer = _entry_answer(STANDALONE, DOLPHIN_GCI)
        (first, *_) = atlas.savefile_placement_contract(answer)["redirects"]
        assert set(first) == {"via", "name", "argument", "moves"}


def _answer_reading(key: str, value: str | None, mode: str = "folder+none") -> SavefilePlacement:
    return SavefilePlacement(
        dir="/saves",
        root_kind="emulator_directory",
        needs=(),
        file_set=UNKNOWN_FILE_SET,
        sources=(),
        caveats=(),
        granularity=Granularity(
            value="per-game-files",
            mode=mode,
            readings=(OptionReading(key, value, "p", None),),
            alternatives=(),
            provenance="p",
        ),
    )


class TestTheComparisonReadsOnlyWhatTheAnswerRead:
    def test_a_configured_path_outranks_a_route_that_names_it(self):
        route = SaveRedirect(
            via="variable",
            name="XDG_DATA_HOME",
            argument="<dir>",
            moves=("saves",),
            citation="a.c:1",
            outranked_by=(RedirectSetting("settings.xml", "mlc_path"),),
        )
        setting = route.outranked_by[0]
        assert _redirect_setting_outranks(route, setting, _answer_reading("mlc_path", "/mlc"))
        assert not _redirect_setting_outranks(route, setting, _answer_reading("mlc_path", None))
        # A setting the answer never read outranks nothing it could state.
        assert not _redirect_setting_outranks(route, setting, _answer_reading("other", "/x"))

    def test_a_slot_route_needs_its_device_in_the_slot(self):
        route = SaveRedirect(
            via="flag",
            name="-C",
            argument="Dolphin.Core.GCIFolderAPathOverride=<dir>",
            moves=("saves",),
            citation="a.c:1",
            outranked_by=(RedirectSetting("Dolphin.ini", "[Core] SlotA"),),
        )
        setting = route.outranked_by[0]
        assert not _redirect_setting_outranks(route, setting, _answer_reading("SlotA", "8", mode="folder+none"))
        assert _redirect_setting_outranks(route, setting, _answer_reading("SlotA", "1", mode="card+none"))

    def test_a_slot_route_the_code_does_not_know_raises(self):
        route = SaveRedirect(
            via="flag",
            name="-C",
            argument="Dolphin.Core.Unknown=<dir>",
            moves=("saves",),
            citation="a.c:1",
            outranked_by=(RedirectSetting("Dolphin.ini", "[Core] SlotA"),),
        )
        answer = _answer_reading("SlotA", "8")
        with pytest.raises(ValueError, match="out of step"):
            _redirect_setting_outranks(route, route.outranked_by[0], answer)
