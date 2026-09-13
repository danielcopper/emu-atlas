"""Tests for atlas.core_firmware — the locating word, its table, and its two rules.

The word is world knowledge, so the two things that can go wrong with it are the
two this file holds apart: a **table** that states something it has no source
for (the loader, which fails closed), and an **answer** that states a word its
table never gave it (the two seams, checked through the resolver rather than by
reading them).
"""

from __future__ import annotations

import ast
import json
import typing
from pathlib import Path
from typing import Mapping

import pytest

import atlas
from atlas.core_firmware import (
    FIRMWARE_LOCATING,
    FirmwareLocating,
    LOCATING_BY_NAME,
    LOCATING_BY_NAME_THEN_CONTENT,
    LOCATING_UNESTABLISHED,
    core_firmware_cards,
    load_core_firmware,
    locating_of_card,
    locating_of_core,
    lookup_core_firmware,
)
from atlas.firmware import (
    DECLARATION_ABSENT,
    DECLARATION_UNREADABLE,
    Catalogue,
    CatalogueEntry,
    FirmwareContext,
    FirmwareRequirement,
    InventoryCatalogue,
    firmware_for_core,
    firmware_inventory,
    load_hashes,
    read_core_declarations,
)
from atlas.machine import FixtureMachine
from atlas.standalone_firmware import (
    StandaloneFirmwareCard,
    StandaloneFirmwareConfigFile,
    StandaloneFirmwareFile,
    StandaloneFirmwareSearch,
    standalone_firmware_cards,
)
from atlas.system_firmware import SystemFirmware, load_system_firmware

INFO_DIR = "/cores"
BIOS_DIR = "/bios"
DATA_HOME = "/home/u/.local/share"
CONFIG_HOME = "/home/u/.config"

# One card of each shape, so a single answer carries both halves of the card
# rule: DuckStation's card states a search, Cemu's states files.
CATALOGUE = InventoryCatalogue(
    by_system={
        "psx": Catalogue(
            (
                CatalogueEntry(
                    label="DuckStation (Standalone)",
                    kind="standalone",
                    core_so=None,
                    emulator="DUCKSTATION",
                    declared_index=0,
                    standalone_token="DUCKSTATION",
                ),
            )
        ),
        "wiiu": Catalogue(
            (
                CatalogueEntry(
                    label="Cemu (Standalone)",
                    kind="standalone",
                    core_so=None,
                    emulator="CEMU",
                    declared_index=0,
                    standalone_token="CEMU",
                ),
            )
        ),
    }
)

# One declaration, used for every core here. The fixtures vary the core NAME
# and nothing else, which is what makes a difference in the answer attributable
# to the packaged entry that name reaches rather than to the declaration.
# `Demo System` is in no recorded system table, so no verdict about a machine
# rides along either.
DEMO_INFO = """
systemname = "Demo System"
firmware_count = 1
firmware0_desc = "demo.bin (Demo BIOS)"
firmware0_path = "demo.bin"
firmware0_opt = "false"
"""

# The three names this file asks about: one in no packaged entry, and the two
# packaged words. Their real declarations are not what is under test — the rule
# that maps a name to a word is.
CORES = ("demo", "swanstation", "mednafen_psx_hw")


def _machine(**extra: object) -> FixtureMachine:
    tree: dict[str, object] = {}
    for stem in CORES:
        tree[f"{INFO_DIR}/{stem}_libretro.info"] = DEMO_INFO
        tree[f"{INFO_DIR}/{stem}_libretro.so"] = {"status": "invalid-text"}
    return FixtureMachine(tree, **extra)  # type: ignore[arg-type]


def _context(machine: FixtureMachine) -> FirmwareContext:
    return FirmwareContext(
        root=BIOS_DIR,
        cores=read_core_declarations(machine, INFO_DIR, core_dir=INFO_DIR).cores,
        hashes=load_hashes(),
        system_firmware=_shipped(),
    )


def _shipped() -> Mapping[str, SystemFirmware]:
    return load_system_firmware()


def _doc(**overrides: object) -> str:
    entry: dict[str, object] = {
        "locating": {"mode": "by-name", "citation": "libretro.cpp:176 at abc1234"},
        "build": {"revision": "abc1234", "citation": "the build answers '1.0 abc1234'"},
        "provenance": {"source": "someone/somecore at abc1234"},
    }
    entry.update(overrides)
    return json.dumps({"schema": 1, "cores": {"somecore": entry}})


class TestTheVocabularyIsTotal:
    """Every value explained where the page reads meanings from, and no value spare.

    Totality runs both ways here. Each word has to carry a sentence, because
    the generator refuses a vocabulary explained in part; and each word has to
    be reachable, because a value nothing produces is a branch a consumer would
    write and never enter.
    """

    def test_the_tuple_and_the_annotation_admit_the_same_values(self):
        # The tuple is what a client branches on and the Literal is what the
        # annotation narrows to; a value in one and not the other publishes a
        # vocabulary nothing can produce, or produces one nothing published.
        assert set(typing.get_args(FirmwareLocating)) == set(FIRMWARE_LOCATING)
        assert len(FIRMWARE_LOCATING) == len(set(FIRMWARE_LOCATING))

    def test_every_value_has_a_constant_and_every_constant_a_sentence(self):
        # The generator publishes a value's meaning from the docstring under
        # its constant and refuses a vocabulary explained in part
        # (`scripts/generate_contract_reference.py`). Holding it here as well
        # makes the requirement visible where the constants are written.
        import atlas.core_firmware as module

        constants = {
            LOCATING_BY_NAME: "LOCATING_BY_NAME",
            LOCATING_BY_NAME_THEN_CONTENT: "LOCATING_BY_NAME_THEN_CONTENT",
            LOCATING_UNESTABLISHED: "LOCATING_UNESTABLISHED",
        }
        assert sorted(constants) == sorted(FIRMWARE_LOCATING)
        tree = ast.parse(Path(module.__file__).read_text(encoding="utf-8"))
        stated: set[str] = set()
        body = tree.body
        for index, node in enumerate(body):
            if not (isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name)):
                continue
            following = body[index + 1] if index + 1 < len(body) else None
            docstring = (
                isinstance(following, ast.Expr)
                and isinstance(following.value, ast.Constant)
                and isinstance(following.value.value, str)
            )
            if docstring:
                stated.add(node.target.id)
        assert set(constants.values()) <= stated

    def test_every_published_value_has_something_that_produces_it(self):
        # The other half of totality, and the reason the vocabulary is three
        # words rather than four. Only two rules and one default put a word on
        # an answer, so their union IS the set of reachable values; a value
        # outside it is published with no mechanism behind it, and a consumer
        # branching on it writes a branch nothing enters. Adding a word is a
        # compatible change and removing one is not, so the list grows with the
        # readings rather than ahead of them — and this is what holds it there.
        reachable = (
            {locating_of_core(card.so_name) for card in core_firmware_cards()}
            | {locating_of_card(card) for card in standalone_firmware_cards()}
            | {LOCATING_UNESTABLISHED}
        )
        assert reachable == set(FIRMWARE_LOCATING)

    def test_the_word_reaches_the_serialized_answer(self):
        machine = _machine()
        answer = firmware_for_core(machine, _context(machine), core_so="demo_libretro.so")
        block = atlas.firmware_contract(answer)
        assert [core["locating"] for core in block["cores"]] == [LOCATING_UNESTABLISHED]


class TestALibretroCoreAnswersItsEntryOrNothing:
    """The rule for a `.so`: the packaged word, or the honest absence of one."""

    def test_a_core_with_no_entry_is_unestablished(self):
        assert lookup_core_firmware("demo_libretro.so") is None
        assert locating_of_core("demo_libretro.so") == LOCATING_UNESTABLISHED

    def test_that_core_carries_it_into_its_answer(self):
        machine = _machine()
        answer = firmware_for_core(machine, _context(machine), core_so="demo_libretro.so")
        assert [core.locating for core in answer.cores] == [LOCATING_UNESTABLISHED]

    def test_an_entry_answers_its_own_word(self):
        assert locating_of_core("swanstation_libretro.so") == LOCATING_BY_NAME_THEN_CONTENT
        assert locating_of_core("mednafen_psx_hw_libretro.so") == LOCATING_BY_NAME
        assert locating_of_core("mednafen_psx_libretro.so") == LOCATING_BY_NAME

    def test_that_core_carries_it_into_its_answer_too(self):
        machine = _machine()
        answer = firmware_for_core(machine, _context(machine), core_so="swanstation_libretro.so")
        assert [core.locating for core in answer.cores] == [LOCATING_BY_NAME_THEN_CONTENT]

    def test_all_three_spellings_of_a_core_name_reach_one_entry(self):
        # The same three spellings `firmware_for_core` takes, so the word does
        # not depend on how the question was asked.
        for spelling in (
            "swanstation",
            "swanstation_libretro",
            "swanstation_libretro.so",
            "/usr/lib/libretro/swanstation_libretro.so",
        ):
            assert locating_of_core(spelling) == LOCATING_BY_NAME_THEN_CONTENT

    def test_nothing_names_no_core(self):
        assert lookup_core_firmware(None) is None
        assert locating_of_core(None) == LOCATING_UNESTABLISHED

    def test_a_core_the_declaration_could_not_be_read_for_still_states_its_word(self):
        # The word is about the core's code, not about this installation, so an
        # unreadable `.info` withholds the requirement list and not the word.
        machine = FixtureMachine(
            {
                f"{INFO_DIR}/swanstation_libretro.info": {"status": "unreadable"},
                f"{INFO_DIR}/swanstation_libretro.so": {"status": "invalid-text"},
            }
        )
        core = firmware_for_core(
            machine, _context(machine), core_so="swanstation_libretro.so"
        ).cores[0]
        assert core.declaration == DECLARATION_UNREADABLE
        assert core.locating == LOCATING_BY_NAME_THEN_CONTENT

    def test_a_core_this_machine_does_not_have_still_states_its_word(self):
        # `absent` is a claim about the machine; the knowledge describes the
        # core either way, so the word is the same one an installed copy states.
        machine = FixtureMachine({f"{INFO_DIR}/demo_libretro.info": DEMO_INFO})
        core = firmware_for_core(
            machine, _context(machine), core_so="swanstation_libretro.so"
        ).cores[0]
        assert core.declaration == DECLARATION_ABSENT
        assert core.locating == LOCATING_BY_NAME_THEN_CONTENT


class TestACardStatesItsWordFromItsShape:
    """The rule for a standalone emulator: the card's shape is the statement."""

    def _card(self, **overrides: object) -> StandaloneFirmwareCard:
        fields: dict[str, object] = {
            "token": "DEMO",
            "systems": ("psx",),
            "files": (),
            "config_files": (),
            "search": None,
            "provenance": "a source",
        }
        fields.update(overrides)
        return StandaloneFirmwareCard(**fields)  # type: ignore[arg-type]

    def test_a_search_card_locates_by_name_then_content(self):
        search = StandaloneFirmwareSearch(
            directory_key="BIOS/SearchDirectory",
            directory_default="bios",
            region_keys=(("ntsc-u", "BIOS/PathNTSCU"),),
            purpose="a BIOS image",
            citation="bios.cpp:364-400",
        )
        assert locating_of_card(self._card(search=search)) == LOCATING_BY_NAME_THEN_CONTENT

    def test_a_files_card_locates_by_name(self):
        probe = StandaloneFirmwareFile(
            name="keys.txt",
            base="data",
            subdir="Cemu",
            need="optional",
            purpose="keys",
            citation="KeyCache.cpp:63",
        )
        assert locating_of_card(self._card(files=(probe,))) == LOCATING_BY_NAME

    def test_a_config_files_card_locates_by_name(self):
        probe = StandaloneFirmwareConfigFile(
            key="DS.BIOS9Path", purpose="bios", citation="EmuInstance.cpp:487"
        )
        assert locating_of_card(self._card(config_files=(probe,))) == LOCATING_BY_NAME

    def test_every_shipped_card_answers_one_of_the_two_shapes(self):
        # Totality over the file rather than over today's four tokens: a card
        # added tomorrow answers a word by construction or fails here.
        for card in standalone_firmware_cards():
            expected = (
                LOCATING_BY_NAME_THEN_CONTENT if card.search is not None else LOCATING_BY_NAME
            )
            assert locating_of_card(card) == expected, card.token

    def test_a_carded_answer_keeps_its_cards_word_through_the_answer_seam(self):
        # Through the resolver, not through the rule. The two seams partition
        # on `core_so`, and the libretro one runs last: a carded entry has no
        # `.so`, so the word its card stated has to survive the answer-level
        # seam rather than being replaced by the `unestablished` a core with no
        # packaged entry takes. One card of each shape, in one answer.
        machine = FixtureMachine(
            {
                f"{CONFIG_HOME}/duckstation/settings.ini": (
                    f"[BIOS]\nSearchDirectory = {BIOS_DIR}\n"
                    "PathNTSCU = \nPathNTSCJ = \nPathPAL = \n"
                ),
                f"{INFO_DIR}/demo_libretro.info": DEMO_INFO,
                f"{INFO_DIR}/demo_libretro.so": {"status": "invalid-text"},
            },
            dirs=[BIOS_DIR, INFO_DIR],
        )
        context = FirmwareContext(
            root=BIOS_DIR,
            cores=read_core_declarations(machine, INFO_DIR, core_dir=INFO_DIR).cores,
            hashes=load_hashes(),
            standalone_data_home=DATA_HOME,
            standalone_config_home=CONFIG_HOME,
            system_firmware=_shipped(),
        )
        answer = firmware_inventory(machine, context, catalogue=CATALOGUE, verify=False)
        by_emulator = {core.emulator: core.locating for core in answer.cores}
        assert by_emulator == {
            "demo_libretro.so": LOCATING_UNESTABLISHED,
            "DUCKSTATION": LOCATING_BY_NAME_THEN_CONTENT,
            "CEMU": LOCATING_BY_NAME,
        }


class TestTheLoaderFailsClosed:
    """A table that cannot cite what it states does not load at all."""

    def test_the_packaged_file_loads_and_looks_up(self):
        cards = core_firmware_cards()
        assert {card.key for card in cards} == {
            "swanstation",
            "mednafen_psx_hw",
            "mednafen_psx",
        }
        card = lookup_core_firmware("swanstation_libretro.so")
        assert card is not None
        assert card.locating.mode == LOCATING_BY_NAME_THEN_CONTENT
        assert card.build.revision == "4d309c0"
        assert lookup_core_firmware("nope_libretro.so") is None

    def test_the_key_and_the_so_name_are_the_two_spellings_they_look_like(self):
        # The card is keyed short and `CoreFirmware.core_so` is the basename;
        # this is the one place the two are written down beside each other, so
        # a lookup against the wrong one shows up here rather than as a card
        # that quietly matches nothing.
        for card in core_firmware_cards():
            assert not card.key.endswith(("_libretro", ".so")), card.key
            assert card.so_name == f"{card.key}_libretro.so"
            assert lookup_core_firmware(card.so_name) is card

    def test_every_packaged_entry_cites_every_fact_it_states(self):
        for card in core_firmware_cards():
            assert card.locating.citation.strip(), card.key
            assert card.build.citation.strip(), card.key
            assert card.provenance.strip(), card.key
            assert card.locating.mode in FIRMWARE_LOCATING, card.key

    def test_a_wrong_schema_is_refused(self):
        with pytest.raises(ValueError, match="unsupported schema"):
            load_core_firmware('{"schema": 2, "cores": {}}')

    def test_a_word_outside_the_vocabulary_is_refused(self):
        bad = _doc(locating={"mode": "by-vibes", "citation": "somewhere"})
        with pytest.raises(ValueError, match="must be one of"):
            load_core_firmware(bad)

    def test_an_entry_claiming_unestablished_is_refused(self):
        # The value a core with no entry already answers. An entry stating it
        # would be a citation for having read nothing.
        bad = _doc(locating={"mode": "unestablished", "citation": "nothing was read"})
        with pytest.raises(ValueError, match="is what a core with no entry"):
            load_core_firmware(bad)

    def test_an_empty_citation_is_refused(self):
        bad = _doc(locating={"mode": "by-name", "citation": ""})
        with pytest.raises(ValueError, match="locating.citation"):
            load_core_firmware(bad)

    def test_an_empty_build_citation_is_refused(self):
        bad = _doc(build={"revision": "abc1234", "citation": ""})
        with pytest.raises(ValueError, match="build.citation"):
            load_core_firmware(bad)

    def test_an_empty_provenance_is_refused(self):
        bad = _doc(provenance={"source": "   "})
        with pytest.raises(ValueError, match="provenance.source"):
            load_core_firmware(bad)

    def test_a_missing_provenance_is_refused(self):
        bad = _doc(provenance={})
        with pytest.raises(ValueError, match="provenance.source"):
            load_core_firmware(bad)

    def test_a_stray_field_on_a_card_is_refused(self):
        bad = _doc(note="a field nobody reads")
        with pytest.raises(ValueError, match="locating/build/provenance"):
            load_core_firmware(bad)

    def test_a_stray_field_inside_locating_is_refused(self):
        bad = _doc(locating={"mode": "by-name", "citation": "somewhere", "region": "pal"})
        with pytest.raises(ValueError, match="mode/citation"):
            load_core_firmware(bad)

    def test_a_stray_field_inside_build_is_refused(self):
        bad = _doc(build={"revision": "abc1234", "citation": "somewhere", "date": "2025"})
        with pytest.raises(ValueError, match="revision/citation"):
            load_core_firmware(bad)

    def test_a_missing_fact_is_refused(self):
        bad = json.dumps(
            {"schema": 1, "cores": {"somecore": {"provenance": {"source": "a source"}}}}
        )
        with pytest.raises(ValueError, match="locating/build/provenance"):
            load_core_firmware(bad)

    def test_a_cores_key_that_is_not_an_object_is_refused(self):
        with pytest.raises(ValueError, match="expected a 'cores' object"):
            load_core_firmware('{"schema": 1, "cores": []}')


class TestTheWordMovesNoVerdict:
    """`requirements_met` answers to the declaration, never to this field."""

    def test_three_words_over_one_declaration_reach_one_verdict(self):
        # The fixture gives all three cores the same declaration, so the only
        # thing that differs between these answers is the word the core's name
        # reaches. All three verdicts have to be the same one — a field about
        # how a file is found can neither satisfy a requirement nor fail one.
        machine = _machine()
        context = _context(machine)
        answers = {
            stem: firmware_for_core(machine, context, core_so=f"{stem}_libretro.so").cores[0]
            for stem in CORES
        }
        assert {core.locating for core in answers.values()} == {
            LOCATING_UNESTABLISHED,
            LOCATING_BY_NAME_THEN_CONTENT,
            LOCATING_BY_NAME,
        }
        assert {core.requirements_met for core in answers.values()} == {False}
        # And the requirement lists are the same list, so the equal verdict is
        # not two different declarations arriving at one answer by accident.
        assert {
            tuple(
                (r.path, r.need, r.present)
                for r in core.requirements
                if isinstance(r, FirmwareRequirement)
            )
            for core in answers.values()
        } == {((f"{BIOS_DIR}/demo.bin", "required", False),)}
        assert all(
            all(isinstance(r, FirmwareRequirement) for r in core.requirements)
            for core in answers.values()
        )


class TestTheWordIsStatedAtTwoSeamsAndNowhereElse:
    """The partition, held against the module's source rather than a reading of it."""

    def _module(self) -> ast.Module:
        import atlas.firmware

        return ast.parse(Path(atlas.firmware.__file__).read_text(encoding="utf-8"))

    def _sites(self) -> dict[str, int]:
        owners: dict[str, int] = {}
        for node in ast.walk(self._module()):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for inner in ast.walk(node):
                if isinstance(inner, ast.Call) and any(
                    keyword.arg == "locating" for keyword in inner.keywords
                ):
                    owners[node.name] = owners.get(node.name, 0) + 1
        return owners

    def test_exactly_the_two_seams_state_it(self):
        # A third site would be a route deciding the word for itself, and the
        # failure it produces is silent: an answer that states `by-name` for an
        # emulator no entry describes reads exactly like one that was read.
        assert self._sites() == {"_stating_system_firmware": 1, "_carded_standalone_core": 1}

    def test_the_libretro_seam_states_it_only_for_a_core_that_has_a_so(self):
        # What keeps the two seams from colliding: the libretro one runs last
        # over every core of every answer, and a carded entry has no `.so`, so
        # the guard is the whole reason a card's word survives it.
        seam = next(
            node
            for node in self._module().body
            if isinstance(node, ast.FunctionDef) and node.name == "_stating_system_firmware"
        )
        compares = [
            node
            for node in ast.walk(seam)
            if isinstance(node, ast.Compare)
            and isinstance(node.ops[0], ast.Is)
            and isinstance(node.comparators[0], ast.Constant)
            and node.comparators[0].value is None
            and isinstance(node.left, ast.Attribute)
            and node.left.attr == "core_so"
        ]
        assert len(compares) == 1
