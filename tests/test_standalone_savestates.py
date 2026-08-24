"""The standalone savestate cards: loader discipline, and the citation crossing (#225).

The loader half mirrors the texture cards' (a card states its directory one of
two ways and exactly one; anchors are validated against exactly the words the
card records); the citation half mirrors the save cards' (#246): every slot
the shared reading names, the card states, and every slot the card states,
some reading names — with one set per build where the builds differ.
"""

import json

import pytest

from atlas.installations import (
    _STANDALONE_SAVESTATE_RESOLVERS,
    STANDALONE_SAVESTATE_CITATION_SLOTS,
)
from atlas.standalone_savestates import (
    SAVESTATES_SCHEMA,
    StandaloneSavestateCard,
    load_standalone_savestates,
    lookup_standalone_savestate_card,
)

PRIMEHACK_FLATPAK = "io.github.shiiion.primehack"


def _table(savestates, extra=None) -> str:
    entry = {
        "savestates": savestates,
        "provenance": {"source": "[V] a citation"},
    }
    if extra:
        entry.update(extra)
    return json.dumps({"schema": SAVESTATES_SCHEMA, "emulators": {"DEMO": entry}})


def _fixed(**overrides):
    savestates = {
        "settings": None,
        "systems": ["gc"],
        "base": "data",
        "subdir": "States",
        "names": {"pattern": "<game_id>.s<slot>", "citation": "State.cpp:1-2"},
    }
    savestates.update(overrides)
    return savestates


class TestEveryCardHasItsReading:
    def test_the_packaged_cards_load(self):
        assert load_standalone_savestates()

    def test_every_packaged_card_has_a_resolver_registered(self):
        # A card without a resolver is a marker selecting nothing — the
        # dispatch raises for it, so the pairing is proven here instead of in
        # a caller's answer.
        for card in load_standalone_savestates():
            assert card.token in _STANDALONE_SAVESTATE_RESOLVERS, (
                f"{card.token} has a card and no resolver — the card and the code "
                "shipped out of step"
            )

    def test_lookup_finds_no_card_for_nothing(self):
        assert lookup_standalone_savestate_card(None) is None
        assert lookup_standalone_savestate_card("NOT-AN-EMULATOR") is None


class TestTheCardStatesWhatTheReadingNames:
    @pytest.mark.parametrize("token", sorted(STANDALONE_SAVESTATE_CITATION_SLOTS))
    def test_every_slot_the_reading_names_is_stated(self, token):
        card = lookup_standalone_savestate_card(token)
        assert card is not None, f"{token} has citation slots and no card"
        missing = STANDALONE_SAVESTATE_CITATION_SLOTS[token] - set(card.citations)
        assert missing == set(), f"{token} states no citation for {sorted(missing)}"

    @pytest.mark.parametrize("token", sorted(STANDALONE_SAVESTATE_CITATION_SLOTS))
    def test_the_card_states_no_slot_the_reading_never_names(self, token):
        card = lookup_standalone_savestate_card(token)
        assert card is not None
        extra = set(card.citations) - STANDALONE_SAVESTATE_CITATION_SLOTS[token]
        assert extra == set(), f"{token} states {sorted(extra)} and no reading names it"

    def test_a_card_whose_reading_needs_none_states_none(self):
        for card in load_standalone_savestates():
            if card.token in STANDALONE_SAVESTATE_CITATION_SLOTS:
                continue
            assert card.citations == {}, (
                f"{card.token} states citations and its reading names none — the slots and "
                "the code that speaks them shipped out of step"
            )


class TestTheProseAndTheSlotsAgree:
    """One card, two tellings of one citation — they have to be the same one."""

    @pytest.mark.parametrize("token", sorted(STANDALONE_SAVESTATE_CITATION_SLOTS))
    @pytest.mark.parametrize("slot", ("tree", "names"))
    def test_the_prose_states_the_slots_own_span(self, token, slot):
        card = lookup_standalone_savestate_card(token)
        assert card is not None
        cited = card.cite(slot, flatpak=None)
        assert cited in card.provenance, (
            f"{token} cites {slot} as {cited!r} and its own provenance prose does not say so — "
            "one of the two is the number somebody re-read, and a reader cannot tell which"
        )


class TestACitationBelongsToABuild:
    def test_the_two_forks_builds_are_cited_apart(self):
        card = lookup_standalone_savestate_card("PRIMEHACK")
        assert card is not None
        assert card.cite("names", flatpak=None) != card.cite("names", flatpak=PRIMEHACK_FLATPAK)
        assert card.cite("build", flatpak=None) != card.cite("build", flatpak=PRIMEHACK_FLATPAK)

    def test_an_installation_nothing_states_falls_back_to_the_default(self):
        # Dolphin's own flatpak is not PrimeHack's build, so naming it must not
        # be mistaken for an override of one.
        card = lookup_standalone_savestate_card("PRIMEHACK")
        assert card is not None
        assert card.cite("build", flatpak="org.DolphinEmu.dolphin-emu") == card.cite(
            "build", flatpak=None
        )

    def test_the_fork_and_the_emulator_it_forks_cite_different_lines(self):
        dolphin = lookup_standalone_savestate_card("DOLPHIN")
        fork = lookup_standalone_savestate_card("PRIMEHACK")
        assert dolphin is not None and fork is not None
        assert dolphin.cite("names", flatpak=None) != fork.cite("names", flatpak=None)

    def test_an_unstated_slot_raises_instead_of_answering_nothing(self):
        card = lookup_standalone_savestate_card("DOLPHIN")
        assert card is not None
        with pytest.raises(ValueError, match="no 'nonsense' citation"):
            card.cite("nonsense", flatpak=None)


class TestTheLoaderRefusesWhatItCannotStand:
    def test_a_wrong_schema_is_refused(self):
        with pytest.raises(ValueError, match="unsupported schema"):
            load_standalone_savestates(json.dumps({"schema": 99, "emulators": {}}))

    def test_a_card_stating_both_shapes_is_refused(self):
        table = _table(
            _fixed(directory={"section": "S", "key": "K", "default": "d", "citation": "c:1"})
        )
        with pytest.raises(ValueError, match="never both or neither"):
            load_standalone_savestates(table)

    def test_a_card_stating_neither_shape_is_refused(self):
        with pytest.raises(ValueError, match="never both or neither"):
            load_standalone_savestates(_table(_fixed(base=None, subdir=None)))

    def test_an_unknown_base_is_refused(self):
        with pytest.raises(ValueError, match="savestates.base"):
            load_standalone_savestates(_table(_fixed(base="cache")))

    def test_an_absolute_subdir_is_refused(self):
        with pytest.raises(ValueError, match="absolute"):
            load_standalone_savestates(_table(_fixed(subdir="/States")))

    def test_a_subdir_climbing_out_is_refused(self):
        with pytest.raises(ValueError, match="climbs out"):
            load_standalone_savestates(_table(_fixed(subdir="../States")))

    def test_a_card_without_names_is_refused(self):
        with pytest.raises(ValueError, match="names"):
            load_standalone_savestates(_table(_fixed(names=None)))

    def test_a_directory_setting_without_a_settings_file_is_refused(self):
        savestates = {
            "settings": None,
            "systems": ["ps2"],
            "directory": {"section": "S", "key": "K", "default": "d", "citation": "c:1"},
            "names": {"pattern": "<serial>.p2s", "citation": "c:2"},
        }
        with pytest.raises(ValueError, match="names none"):
            load_standalone_savestates(_table(savestates))

    def test_a_flatpak_field_is_refused_as_a_second_record(self):
        # Which app id an arrangement runs an emulator under lives on the save
        # card; a copy here could only drift from it.
        with pytest.raises(ValueError, match="save\\s+card's record"):
            load_standalone_savestates(_table(_fixed(), extra={"flatpak": "org.demo.Demo"}))

    def test_a_partial_installations_override_is_refused(self):
        table = _table(
            _fixed(
                citations={
                    "build": "demo 1",
                    "tree": "a.cpp:1",
                    "names": "State.cpp:1-2",
                    "installations": {"org.demo.Demo": {"build": "demo 2"}},
                }
            )
        )
        with pytest.raises(ValueError, match="same slots"):
            load_standalone_savestates(table)

    def test_disagreeing_names_citations_are_refused(self):
        # The 'names' slot and savestates.names.citation state the same fact
        # for the default build — two spellings of one span is the drift this
        # table exists to prevent.
        table = _table(
            _fixed(
                citations={"build": "demo 1", "tree": "a.cpp:1", "names": "State.cpp:9-9"}
            )
        )
        with pytest.raises(ValueError, match="disagree"):
            load_standalone_savestates(table)

    def test_an_anchor_for_an_unrecorded_name_is_refused(self):
        table = _table(
            _fixed(),
            extra={
                "anchors": {
                    "binary": "demo/bin/demo",
                    "names": {"elsewhere": {"literal": "elsewhere"}},
                }
            },
        )
        with pytest.raises(ValueError, match="does not record"):
            load_standalone_savestates(table)

    def test_empty_systems_are_refused(self):
        with pytest.raises(ValueError, match="non-empty list"):
            load_standalone_savestates(_table(_fixed(systems=[])))


class TestTheCardShape:
    def test_a_config_stated_card_carries_its_key_and_default(self):
        card = lookup_standalone_savestate_card("PCSX2")
        assert card is not None
        assert card.directory is not None
        assert (card.directory.section, card.directory.key) == ("Folders", "Savestates")
        assert card.directory.default == "sstates"
        assert card.settings == "PCSX2.ini"

    def test_a_fixed_card_carries_its_base_and_subdir(self):
        card = lookup_standalone_savestate_card("RPCS3")
        assert isinstance(card, StandaloneSavestateCard)
        assert (card.base, card.subdir) == ("config", "savestates")
        assert card.settings is None and card.directory is None

    def test_melonds_default_is_the_empty_string_not_a_directory(self):
        # An empty SavestatePath routes the state beside the ROM — the default
        # is a real value, and _expect_str would have refused it.
        card = lookup_standalone_savestate_card("MELONDS")
        assert card is not None
        assert card.directory is not None
        assert card.directory.default == ""
