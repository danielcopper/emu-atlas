"""A save card's citations, and the build each of them belongs to (#246).

A reading shared by two emulators cannot carry its own line numbers: PrimeHack
is a Dolphin fork read by Dolphin's resolver, and the files it inherits sit
somewhere else in its source. So the citations live on the card — and, because
the two arrangements ship builds three years apart, one set per installation
beside them.

The crossing test is the one that matters: every slot the reading names, the
card states, and every slot the card states, some reading names.
"""

import json

import pytest

from atlas.installations import STANDALONE_SAVE_CITATION_SLOTS
from atlas.standalone_saves import (
    SAVES_SCHEMA,
    StandaloneSaveCard,
    load_standalone_saves,
    lookup_standalone_save_card,
)

PRIMEHACK_FLATPAK = "io.github.shiiion.primehack"


def _cards() -> dict[str, StandaloneSaveCard]:
    return {card.token: card for card in load_standalone_saves()}


def _table(citations) -> str:
    return json.dumps(
        {
            "schema": SAVES_SCHEMA,
            "emulators": {
                "DEMO": {
                    "saves": {"settings": "demo.ini", "systems": ["gc"], "citations": citations},
                    "provenance": {"source": "[V] a citation"},
                }
            },
        }
    )


class TestTheCardStatesWhatTheReadingNames:
    @pytest.mark.parametrize("token", sorted(STANDALONE_SAVE_CITATION_SLOTS))
    def test_every_slot_the_reading_names_is_stated(self, token):
        card = lookup_standalone_save_card(token)
        assert card is not None, f"{token} has citation slots and no card"
        missing = STANDALONE_SAVE_CITATION_SLOTS[token] - set(card.citations)
        assert missing == set(), f"{token} states no citation for {sorted(missing)}"

    @pytest.mark.parametrize("token", sorted(STANDALONE_SAVE_CITATION_SLOTS))
    def test_the_card_states_no_slot_the_reading_never_names(self, token):
        # Evidence written for nothing outlives the answer it was written for,
        # the same reason an anchor for nothing is refused.
        card = lookup_standalone_save_card(token)
        assert card is not None
        extra = set(card.citations) - STANDALONE_SAVE_CITATION_SLOTS[token]
        assert extra == set(), f"{token} states {sorted(extra)} and no reading names it"

    def test_a_card_whose_reading_needs_none_states_none(self):
        for card in load_standalone_saves():
            if card.token in STANDALONE_SAVE_CITATION_SLOTS:
                continue
            assert card.citations == {}, (
                f"{card.token} states citations and its reading names none — the slots and the "
                "code that speaks them shipped out of step"
            )


# The citation slots whose span the card's own prose states too, because the
# prose walks the path shape those slots cite and a reader compares the two.
# `nand_tree` is here because the two disagreed in a shipped release — the slot
# said NandPaths.cpp:49-58 and the prose :49-52, which stops before the half
# that appends `/data` — and nothing noticed, since prose is not contractual
# and no test read it.
#
# The other two slots are absent on purpose: the prose discusses neither
# `session_overrides` nor `wii_dir`, so requiring their spans in it would ask
# for sentences nobody needs. It does cite the same *files* for other facts,
# which is why this is a list of slots rather than a rule about files.
SLOTS_THE_PROSE_REPEATS = ("gci_names", "nand_tree", "slot_defaults", "slot_devices")


class TestTheProseAndTheSlotsAgree:
    """One card, two tellings of one citation — they have to be the same one."""

    @pytest.mark.parametrize("token", sorted(STANDALONE_SAVE_CITATION_SLOTS))
    @pytest.mark.parametrize("slot", SLOTS_THE_PROSE_REPEATS)
    def test_the_prose_states_the_slots_own_span(self, token, slot):
        card = lookup_standalone_save_card(token)
        assert card is not None
        cited = card.cite(slot, flatpak=None)
        assert cited in card.provenance, (
            f"{token} cites {slot} as {cited!r} and its own provenance prose does not say so — "
            "one of the two is the number somebody re-read, and a reader cannot tell which"
        )


class TestACitationBelongsToABuild:
    def test_the_two_forks_builds_are_cited_apart(self):
        card = lookup_standalone_save_card("PRIMEHACK")
        assert card is not None
        component = card.cite("nand_tree", flatpak=None)
        flathub = card.cite("nand_tree", flatpak=PRIMEHACK_FLATPAK)
        assert component != flathub
        assert card.cite("build", flatpak=None) != card.cite("build", flatpak=PRIMEHACK_FLATPAK)

    def test_an_installation_nothing_states_falls_back_to_the_default(self):
        # Dolphin's own flatpak is not PrimeHack's build, so naming it must not
        # be mistaken for an override of one.
        card = lookup_standalone_save_card("PRIMEHACK")
        assert card is not None
        assert card.cite("build", flatpak="org.DolphinEmu.dolphin-emu") == card.cite(
            "build", flatpak=None
        )

    def test_the_fork_and_the_emulator_it_forks_cite_different_lines(self):
        cards = _cards()
        dolphin, primehack = cards["DOLPHIN"], cards["PRIMEHACK"]
        differing = {
            slot
            for slot in STANDALONE_SAVE_CITATION_SLOTS["DOLPHIN"]
            if dolphin.cite(slot, flatpak=None) != primehack.cite(slot, flatpak=None)
        }
        assert differing == set(STANDALONE_SAVE_CITATION_SLOTS["DOLPHIN"]), (
            "the fork's build is a different source than the Dolphin release beside it — a slot "
            "that matched by hand would be worth re-reading before trusting"
        )

    def test_a_slot_the_card_does_not_state_fails_loudly(self):
        card = lookup_standalone_save_card("PRIMEHACK")
        assert card is not None
        with pytest.raises(ValueError, match="states no 'nowhere' citation"):
            card.cite("nowhere", flatpak=None)


class TestTheLoaderRefusesAPartialOverride:
    def test_an_override_stating_fewer_slots_is_refused(self):
        # A set that overrides some slots and inherits others would answer with
        # one build's evidence for part of the same sentence.
        text = _table(
            {
                "build": "demo 1",
                "nand_tree": "A.cpp:1-2",
                "installations": {"org.demo.Demo": {"build": "demo 2"}},
            }
        )
        with pytest.raises(ValueError, match="must state the same slots"):
            load_standalone_saves(text)

    def test_an_override_stating_the_same_slots_loads(self):
        text = _table(
            {
                "build": "demo 1",
                "nand_tree": "A.cpp:1-2",
                "installations": {
                    "org.demo.Demo": {"build": "demo 2", "nand_tree": "A.cpp:3-4"}
                },
            }
        )
        card = load_standalone_saves(text)[0]
        assert card.cite("nand_tree", flatpak="org.demo.Demo") == "A.cpp:3-4"
        assert card.cite("nand_tree", flatpak=None) == "A.cpp:1-2"

    def test_a_citation_that_is_not_a_string_is_refused(self):
        text = _table({"build": 7})
        with pytest.raises(ValueError, match="saves.citations"):
            load_standalone_saves(text)
