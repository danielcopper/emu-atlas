"""Tests for atlas.save_formats — the packaged save-format records the portability question compares.

A record is world knowledge about one emulator's save files, read at one
build. Three things can go wrong with it, and each has its guard here: a
**table** stating something it has no source for (the loader, which fails
closed), a record whose **pin** drifts from the build atlas already recorded for
the same emulator (the pin tripwire), and a record that no longer **covers** the
save card it labels, so a file of a save answer would stay unlabelled (the
coverage tripwire).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from atlas.oddities import load_oddities
from atlas.save_formats import (
    SAVE_MEMORIES,
    load_save_formats,
    save_formats,
)
from atlas.standalone_saves import load_standalone_saves

_REGISTRATIONS = Path(__file__).resolve().parent / "data" / "core_registrations.json"

# Card templates no rule names by spelling, with the reason. PCEE2's slots open
# the card file a core option names, so its record finds each slot's file by
# that option's reading in the save answer (the `reading` of its two rules)
# rather than by a name the card spells.
_CLAIMED_BY_READING = {("pcee2", "<memory_card_file>")}


def _card_templates(mode: Any, rules: Any) -> tuple[str, ...]:
    """Every file name a card mode spells, outside what the record sets aside by directory or by name."""
    return tuple(
        template
        for group in mode.groups
        if group.subdir is None or not any(ignored.covers("/" + group.subdir) for ignored in rules.ignore)
        for template in (*(group.files or ()), *(group.observe or ()))
        if not any(template in ignored.names for ignored in rules.ignore)
    )


def _claimed(template: str, rules: Any, mode_name: str) -> bool:
    """Whether a rule speaking for *mode_name* claims *template*."""
    return any(rule.applies(mode_name) and rule.specificity(template, {}) is not None for rule in rules.parts)


def _table(**changes: Any) -> dict[str, Any]:
    """The smallest well-formed table, with top-level *changes* applied."""
    table: dict[str, Any] = {
        "schema": 1,
        "spec": "docs/research/save-portability.md",
        "formats": {"raw": "a raw image"},
        "conversions": [],
        "retroarch": {"compressed": "[V] save.c:1 at a79435a"},
        "emulators": {
            "demo": {
                "kind": "libretro",
                "build": {"pins": ["1.0 abc1234"], "names": "commit", "revision": "abc1234", "citation": "c"},
                "systems": {
                    "psx": {
                        "parts": [
                            {
                                "memory": "memory-card",
                                "slot": "1",
                                "format": "raw",
                                "names": ["<rom_stem>.srm"],
                                "citation": "[V] demo.c:1 at abc1234",
                            }
                        ]
                    }
                },
            }
        },
    }
    table.update(changes)
    return table


def _with_part(**changes: Any) -> str:
    """The table with the demo record's one part changed — a value of None drops the key."""
    table = _table()
    part = table["emulators"]["demo"]["systems"]["psx"]["parts"][0]
    for key, value in changes.items():
        if value is None:
            part.pop(key, None)
        else:
            part[key] = value
    return json.dumps(table)


class TestTheLoaderFailsClosed:
    def test_the_smallest_table_loads(self):
        table = load_save_formats(json.dumps(_table()))
        assert table.emulators["demo"].systems["psx"].parts[0].memory == "memory-card"

    def test_an_unknown_schema_is_refused(self):
        text = json.dumps(_table(schema=2))
        with pytest.raises(ValueError, match="unsupported schema"):
            load_save_formats(text)

    def test_a_memory_outside_the_vocabulary_is_refused(self):
        text = _with_part(memory="clock")
        with pytest.raises(ValueError, match="memory must be one of"):
            load_save_formats(text)

    def test_a_format_the_table_does_not_state_is_refused(self):
        text = _with_part(format="psx-card")
        with pytest.raises(ValueError, match="is not one the table states"):
            load_save_formats(text)

    def test_a_citation_that_names_no_revision_is_refused(self):
        text = _with_part(citation="[V] demo.c:1")
        with pytest.raises(ValueError, match="does not name the revision"):
            load_save_formats(text)

    def test_an_unknown_key_on_a_part_is_refused(self):
        text = _with_part(listed=False)
        with pytest.raises(ValueError, match="expected keys"):
            load_save_formats(text)

    def test_a_frontend_flag_that_is_not_a_boolean_is_refused(self):
        text = _with_part(frontend="yes")
        with pytest.raises(ValueError, match="must be true or false"):
            load_save_formats(text)

    def test_a_blank_pin_is_refused(self):
        table = _table()
        table["emulators"]["demo"]["build"]["pins"] = [" "]
        text = json.dumps(table)
        with pytest.raises(ValueError, match="non-blank"):
            load_save_formats(text)

    def test_a_pin_kind_outside_the_three_is_refused(self):
        table = _table()
        table["emulators"]["demo"]["build"]["names"] = "tag"
        text = json.dumps(table)
        with pytest.raises(ValueError, match="names must be one of"):
            load_save_formats(text)

    def test_a_conversion_between_unstated_formats_is_refused(self):
        conversion = {"from": "raw", "to": "folder", "rule": None, "citation": "c"}
        text = json.dumps(_table(conversions=[conversion]))
        with pytest.raises(ValueError, match="not a format the table states"):
            load_save_formats(text)

    def test_a_conversion_rule_no_vocabulary_states_is_refused(self):
        table = _table(formats={"raw": "a", "other": "b"})
        conversion = {"from": "raw", "to": "other", "rule": "pad", "citation": "c"}
        text = json.dumps({**table, "conversions": [conversion]})
        with pytest.raises(ValueError, match="rule must be null"):
            load_save_formats(text)

    def test_an_ignore_states_a_directory_or_names_but_not_both(self):
        table = _table()
        table["emulators"]["demo"]["systems"]["psx"]["ignore"] = [
            {"dir_suffix": "stv", "names": ["<rom_stem>.smpc"], "citation": "c"}
        ]
        text = json.dumps(table)
        with pytest.raises(ValueError, match="exactly one of dir_suffix and names"):
            load_save_formats(text)

    def test_a_system_the_same_as_one_with_no_rules_is_refused(self):
        table = _table()
        table["emulators"]["demo"]["systems"]["psxjp"] = {"same_as": "ps2"}
        text = json.dumps(table)
        with pytest.raises(ValueError, match="states no rules"):
            load_save_formats(text)

    def test_a_system_the_same_as_another_shares_its_rules(self):
        table = _table()
        table["emulators"]["demo"]["systems"]["psxjp"] = {"same_as": "psx"}
        systems = load_save_formats(json.dumps(table)).emulators["demo"].systems
        assert systems["psxjp"] is systems["psx"]


class TestARuleMatchesTheNamesItsAnswerStates:
    def _rule(self, **changes: Any):
        return load_save_formats(_with_part(**changes)).emulators["demo"].systems["psx"].parts[0]

    def test_a_hole_matches_the_name_that_filled_it_and_its_own_spelling(self):
        rule = self._rule()
        assert rule.specificity("Example Game (USA).srm", {}) is not None
        assert rule.specificity("<rom_stem>.srm", {}) is not None
        assert rule.specificity("Example Game (USA).mcr", {}) is None

    def test_the_more_literal_template_is_the_closer_match(self):
        plain = self._rule(names=["<rom_stem>.ram"])
        cartridge = self._rule(names=["<rom_stem>-ext512K.ram"])
        name = "Example Game (USA)-ext512K.ram"
        plain_score, cartridge_score = plain.specificity(name, {}), cartridge.specificity(name, {})
        assert plain_score is not None
        assert cartridge_score is not None
        assert cartridge_score > plain_score

    def test_a_reading_the_answer_states_names_the_file_instead_of_the_templates(self):
        rule = self._rule(names=["Mcd001.ps2"], reading="Slot1_Filename")
        assert rule.specificity("mine.ps2", {"Slot1_Filename": "/cards/mine.ps2"}) is not None
        assert rule.specificity("Mcd001.ps2", {"Slot1_Filename": "/cards/mine.ps2"}) is None
        assert rule.specificity("Mcd001.ps2", {}) is not None

    def test_a_rule_speaks_only_for_the_modes_it_names(self):
        rule = self._rule(modes="^card1-libretro\\+")
        assert rule.applies("card1-libretro+card2-none")
        assert not rule.applies("card1-shared+card2-none")
        assert not rule.applies(None)
        assert self._rule().applies(None)


class TestThePackagedTable:
    def test_it_loads(self):
        assert save_formats().emulators

    def test_every_memory_word_is_one_a_record_states(self):
        stated = {
            rule.memory
            for record in save_formats().emulators.values()
            for system in record.systems.values()
            for rule in system.parts
        }
        assert stated == set(SAVE_MEMORIES)

    def test_every_format_is_one_a_record_or_a_conversion_uses(self):
        table = save_formats()
        used = {
            rule.format
            for record in table.emulators.values()
            for system in record.systems.values()
            for rule in system.parts
        } | {side for c in table.conversions for side in (c.source, c.target)}
        assert used == set(table.formats)


class TestEveryPinIsTheBuildAtlasAlreadyRecorded:
    """A record pins the build its reads were taken at, and that build is one atlas already recorded elsewhere.

    For a core it is the ``library_version`` the deployed build answered when
    its registration was captured (``tests/data/core_registrations.json``),
    and the card that labels its groups names the same revision; for a
    standalone emulator it is the release its save card pins, at that build's
    revision. A re-pinned card or a recaptured core fails here until its
    format record is read again at the new build.
    """

    def test_a_cores_pin_is_its_recorded_build_at_the_revision_it_names(self):
        # A pin that names a commit names the record's revision itself; one that
        # names a release only (PCEE2's) leaves the revision to the card that
        # labels its groups, which states the commit it was read at.
        captured = json.loads(_REGISTRATIONS.read_text(encoding="utf-8"))["cores"]
        cards = {card.key: card for card in load_oddities()}
        for record in save_formats().emulators.values():
            if record.kind != "libretro":
                continue
            build = captured[record.key]["build"]
            assert build in record.build.pins, record.key
            named_by = build if record.build.names == "commit" else cards[record.key].provenance
            assert record.build.revision in named_by, record.key

    def test_a_standalone_pin_is_its_save_cards_build(self):
        cards = {card.token: card for card in load_standalone_saves()}
        for record in save_formats().emulators.values():
            if record.kind != "standalone":
                continue
            builds = {build.release: build for build in cards[record.key].builds}
            assert set(record.build.pins) <= set(builds), record.key
            assert {builds[pin].revision for pin in record.build.pins} == {record.build.revision}, record.key


class TestEveryCardFileIsAPart:
    """Every file a rule card names for a covered system is claimed by a rule of its record, or set aside with a reason.

    A save answer's groups follow the card, so a card file no rule claims is a
    group the portability answer would state as unrecorded — this catches the
    record falling behind its card before an answer does.
    """

    def test_every_template_of_every_mode_is_claimed(self):
        cards = {card.key: card for card in load_oddities()}
        unclaimed = [
            f"{record.key} {system} {mode_name}: {template}"
            for record in save_formats().emulators.values()
            if record.key in cards
            for system, rules in record.systems.items()
            for mode_name, mode in cards[record.key].modes.items()
            for template in _card_templates(mode, rules)
            if (record.key, template) not in _CLAIMED_BY_READING and not _claimed(template, rules, mode_name)
        ]
        assert unclaimed == []

    def test_a_rule_is_retroarchs_exactly_where_its_card_says_retroarch_writes_the_file(self):
        # A frontend rule is what makes RetroArch's save_file_compression apply
        # to a part, so it is held to the card's own statement of who writes
        # the file (a group's `frontend`), file by file.
        cards = {card.key: card for card in load_oddities()}
        wrong = [
            f"{record.key} {system} {mode_name}: {template}"
            for record in save_formats().emulators.values()
            if record.key in cards
            for system, rules in record.systems.items()
            for mode_name, mode in cards[record.key].modes.items()
            for group in mode.groups
            for template in group.files or ()
            for rule in rules.parts
            if rule.applies(mode_name)
            and rule.specificity(template, {}) is not None
            and rule.frontend != (group.frontend is not None)
        ]
        assert wrong == []

    def test_a_rule_names_only_what_its_card_spells(self):
        cards = {card.key: card for card in load_oddities()}
        invented: list[str] = []
        for record in save_formats().emulators.values():
            card = cards.get(record.key)
            if card is None:
                continue
            spelled = {
                template
                for mode in card.modes.values()
                for group in mode.groups
                for template in (*(group.files or ()), *(group.observe or ()))
            }
            for system, rules in record.systems.items():
                for rule in rules.parts:
                    invented += [
                        f"{record.key} {system}: {name}"
                        for name in rule.names
                        if name not in spelled and rule.reading is None
                    ]
        assert invented == []
