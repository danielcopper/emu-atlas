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
from atlas.save_memory import load_save_memory
from atlas.save_formats import (
    CARTRIDGE_KINDS,
    CLOCK_CARTRIDGE_KINDS,
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

# Cores whose card names no file of the tree its record labels, with the
# reason. The Dolphin core's card leaves the tree below User to the code that
# reads its Dolphin.ini (installations.py, _dolphin_tree_answer), so its
# record's names are held to that code's answer, not to the card.
_NAMED_BY_THE_CODE = {"dolphin"}

# The systems whose records tell cartridge kinds apart: every record covering
# one of them is held to a cited statement per kind.
_GAME_BOY_SYSTEMS = {"gb", "gbc"}

# The systems whose emulators size a game's save per game, each its own way:
# every rule of every record covering one of them states what decides it
# (#577 D19).
_LENGTH_SYSTEMS = {"nds"}


def _card_templates(mode: Any, rules: Any) -> tuple[str, ...]:
    """Every file name a card mode spells, outside what the record sets aside by directory or by name."""
    return tuple(
        template
        for group in mode.groups
        if group.subdir is None or not any(ignored.covers("/" + group.subdir) for ignored in rules.ignore)
        for template in (*(group.files or ()), *(group.observe or ()))
        if not any(template in ignored.names for ignored in rules.ignore)
    )


def _claimed(template: str, rules: Any, mode_name: str | None) -> bool:
    """Whether a rule speaking for *mode_name* claims *template*."""
    return any(rule.applies(mode_name) and rule.specificity(template, {}) is not None for rule in rules.parts)


def _table(**changes: Any) -> dict[str, Any]:
    """The smallest well-formed table, with top-level *changes* applied."""
    table: dict[str, Any] = {
        "schema": 1,
        "spec": "docs/research/save-portability.md",
        "formats": {"raw": "a raw image"},
        "conversions": [],
        "retroarch": {"compressed": "[V] save.c:1 at a79435a", "loads_raw": "[V] save.c:2 at a79435a"},
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

    def test_the_retroarch_load_citation_is_required(self):
        text = json.dumps(_table(retroarch={"compressed": "[V] save.c:1 at a79435a"}))
        with pytest.raises(ValueError, match="loads_raw"):
            load_save_formats(text)

    def test_a_kind_outside_the_vocabulary_is_refused(self):
        text = _with_part(kinds={"mbc4": "raw"})
        with pytest.raises(ValueError, match="kind must be one of"):
            load_save_formats(text)

    def test_a_kind_format_the_table_does_not_state_is_refused(self):
        text = _with_part(kinds={"mbc2": "packed"})
        with pytest.raises(ValueError, match="for 'mbc2' is not one the table states"):
            load_save_formats(text)

    def test_a_clock_names_only_kinds_that_carry_a_clock(self):
        text = _with_part(memory="cartridge-clock", kinds={"mbc2": "raw"})
        with pytest.raises(ValueError, match="names only kinds that carry a clock"):
            load_save_formats(text)

    def test_a_null_format_needs_a_kind_that_names_one(self):
        table = _table()
        part = table["emulators"]["demo"]["systems"]["psx"]["parts"][0]
        part.update(memory="cartridge-clock", format=None, kinds={"huc3": None})
        text = json.dumps(table)
        with pytest.raises(ValueError, match="a null format needs kinds that name a format"):
            load_save_formats(text)

    def test_a_null_format_keeps_the_part_for_the_kinds_it_names_alone(self):
        table = _table()
        part = table["emulators"]["demo"]["systems"]["psx"]["parts"][0]
        part.update(memory="cartridge-clock", format=None, kinds={"mbc3-timer": "raw", "huc3": None})
        rule = load_save_formats(json.dumps(table)).emulators["demo"].systems["psx"].parts[0]
        assert [rule.format_for(kind) for kind in ("mbc3-timer", "huc3", "tama5", None)] == ["raw", None, None, None]

    def test_a_kind_a_rule_names_overrides_the_format_for_it_alone(self):
        table = _table(formats={"raw": "a", "packed": "b"})
        table["emulators"]["demo"]["systems"]["psx"]["parts"][0]["kinds"] = {"mbc2": "packed"}
        rule = load_save_formats(json.dumps(table)).emulators["demo"].systems["psx"].parts[0]
        assert [rule.format_for(kind) for kind in ("mbc2", "mbc7", None)] == ["packed", "raw", "raw"]

    def test_an_unread_kind_without_its_revision_in_the_citation_is_refused(self):
        text = _with_part(unread=[{"kinds": ["huc1"], "citation": "[O] not read"}])
        with pytest.raises(ValueError, match="does not name the revision"):
            load_save_formats(text)

    def test_an_unread_kind_outside_the_vocabulary_is_refused(self):
        text = _with_part(unread=[{"kinds": ["mbc4"], "citation": "[O] x.c at abc1234"}])
        with pytest.raises(ValueError, match="kinds must be a non-empty list drawn from"):
            load_save_formats(text)

    def test_a_kind_both_stated_and_unread_is_refused(self):
        text = _with_part(kinds={"huc1": "raw"}, unread=[{"kinds": ["huc1"], "citation": "[O] x.c at abc1234"}])
        with pytest.raises(ValueError, match="either stated or unread"):
            load_save_formats(text)

    def test_an_unread_kind_is_found_with_its_citation(self):
        text = _with_part(unread=[{"kinds": ["huc1", "mmm01"], "citation": "[O] x.c at abc1234"}])
        rule = load_save_formats(text).emulators["demo"].systems["psx"].parts[0]
        assert (rule.unread_for("mmm01"), rule.unread_for("mbc2"), rule.unread_for(None)) == (
            "[O] x.c at abc1234",
            None,
            None,
        )

    def test_a_length_is_found_with_its_rule_revision_and_citation(self):
        listed = {"rule": "rom-list", "revision": "def5678", "citation": "[V] list.c:1 at abc1234, list def5678"}
        written = {"rule": "written-extent", "citation": "[V] mc.c:1 at abc1234"}
        rule = load_save_formats(_with_part(length=listed)).emulators["demo"].systems["psx"].parts[0]
        grown = load_save_formats(_with_part(length=written)).emulators["demo"].systems["psx"].parts[0]
        assert rule.length is not None
        assert grown.length is not None
        assert (rule.length.rule, rule.length.revision, rule.length.citation) == (
            "rom-list",
            "def5678",
            "[V] list.c:1 at abc1234, list def5678",
        )
        assert (grown.length.rule, grown.length.revision) == ("written-extent", None)
        assert load_save_formats(_with_part()).emulators["demo"].systems["psx"].parts[0].length is None

    def test_a_length_rule_outside_the_vocabulary_is_refused(self):
        text = _with_part(length={"rule": "rom-list-abc1234", "citation": "[V] list.c:1 at abc1234"})
        with pytest.raises(ValueError, match="rule must be one of"):
            load_save_formats(text)

    def test_a_rom_list_length_without_its_revision_is_refused(self):
        text = _with_part(length={"rule": "rom-list", "citation": "[V] list.c:1 at abc1234"})
        with pytest.raises(ValueError, match="a revision stands exactly where"):
            load_save_formats(text)

    def test_a_written_extent_length_with_a_revision_is_refused(self):
        text = _with_part(length={"rule": "written-extent", "revision": "abc1234", "citation": "[V] mc.c:1 at abc1234"})
        with pytest.raises(ValueError, match="a revision stands exactly where"):
            load_save_formats(text)

    def test_a_length_whose_citation_names_not_the_records_revision_is_refused(self):
        text = _with_part(length={"rule": "rom-list", "revision": "def5678", "citation": "[V] list.c:1 at def5678"})
        with pytest.raises(ValueError, match="does not name the revision 'abc1234'"):
            load_save_formats(text)

    def test_a_length_whose_citation_names_not_its_own_revision_is_refused(self):
        text = _with_part(length={"rule": "rom-list", "revision": "def5678", "citation": "[V] list.c:1 at abc1234"})
        with pytest.raises(ValueError, match="does not name the revision 'def5678'"):
            load_save_formats(text)

    def test_a_blank_length_revision_is_refused(self):
        text = _with_part(length={"rule": "rom-list", "revision": " ", "citation": "[V] list.c:1 at abc1234"})
        with pytest.raises(ValueError, match="expected a non-blank string"):
            load_save_formats(text)

    def test_a_length_with_a_key_outside_its_own_is_refused(self):
        text = _with_part(length={"rule": "written-extent", "citation": "[V] mc.c:1 at abc1234", "size": 512})
        with pytest.raises(ValueError, match="expected keys"):
            load_save_formats(text)

    def test_regionless_content_is_found_by_its_extension_in_any_case(self):
        table = _table()
        table["emulators"]["demo"]["systems"]["psx"]["regionless"] = {
            "extensions": [".dol", ".elf"],
            "citation": "[V] Boot.cpp:1 at abc1234",
        }
        regionless = load_save_formats(json.dumps(table)).emulators["demo"].systems["psx"].regionless
        assert regionless is not None
        assert [regionless.covers(path) for path in ("/g/Game.DOL", "/g/Game.elf", "/g/Game.iso", None)] == [
            True,
            True,
            False,
            False,
        ]

    def test_regionless_extensions_are_lowercase_with_their_dot(self):
        table = _table()
        table["emulators"]["demo"]["systems"]["psx"]["regionless"] = {"extensions": ["DOL"], "citation": "c at abc1234"}
        text = json.dumps(table)
        with pytest.raises(ValueError, match="lowercase '.ext' strings"):
            load_save_formats(text)

    def test_a_regionless_citation_that_names_no_revision_is_refused(self):
        table = _table()
        table["emulators"]["demo"]["systems"]["psx"]["regionless"] = {"extensions": [".dol"], "citation": "Boot.cpp:1"}
        text = json.dumps(table)
        with pytest.raises(ValueError, match="does not name the revision"):
            load_save_formats(text)

    def test_a_name_that_spells_the_region_hole_twice_is_refused(self):
        text = _with_part(names=["<region>/<region>.raw"])
        with pytest.raises(ValueError, match="hole once at most"):
            load_save_formats(text)

    def test_a_mirror_handling_outside_the_vocabulary_is_refused(self):
        table = _table()
        table["emulators"]["demo"]["systems"]["psx"]["mirrors"] = [{"kinds": ["mbc2"], "handling": "wraps", "citation": "c"}]
        text = json.dumps(table)
        with pytest.raises(ValueError, match="handling must be one of"):
            load_save_formats(text)

    def test_a_mirror_statement_names_kinds_of_the_vocabulary(self):
        table = _table()
        table["emulators"]["demo"]["systems"]["psx"]["mirrors"] = [{"kinds": ["mbc4"], "handling": "folds", "citation": "c"}]
        text = json.dumps(table)
        with pytest.raises(ValueError, match="kinds must be a non-empty list drawn from"):
            load_save_formats(text)

    def test_a_mirror_statement_is_found_for_its_kinds(self):
        table = _table()
        table["emulators"]["demo"]["systems"]["psx"]["mirrors"] = [{"kinds": ["mbc2"], "handling": "drops", "citation": "c"}]
        system = load_save_formats(json.dumps(table)).emulators["demo"].systems["psx"]
        found = system.mirrors_for("mbc2")
        assert found is not None
        assert (found.handling, system.mirrors_for("mbc7")) == ("drops", None)

    def test_a_kind_not_run_outside_the_vocabulary_is_refused(self):
        table = _table()
        table["emulators"]["demo"]["systems"]["psx"]["not_run"] = [{"kinds": ["mbc4"], "citation": "c"}]
        text = json.dumps(table)
        with pytest.raises(ValueError, match="kinds must be a non-empty list drawn from"):
            load_save_formats(text)

    def test_a_kind_not_run_is_found_with_its_citation(self):
        table = _table()
        table["emulators"]["demo"]["systems"]["psx"]["not_run"] = [{"kinds": ["mbc6", "mbc7"], "citation": "c"}]
        system = load_save_formats(json.dumps(table)).emulators["demo"].systems["psx"]
        refusal = system.does_not_run("mbc7")
        assert refusal is not None
        assert refusal.citation == "c"
        assert system.does_not_run("mbc2") is None


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


class TestARegionAndAFolderAreReadOffTheName:
    """A ``<region>`` hole matches a word of SAVE_REGIONS alone and names it; a folder rule reads trailing segments (D21)."""

    @staticmethod
    def _rule(**changes: Any):
        return load_save_formats(_with_part(**changes)).emulators["demo"].systems["psx"].parts[0]

    def test_a_region_hole_matches_a_region_word_and_names_it(self):
        rule = self._rule(names=["MemoryCardA.<region>.raw"])
        assert rule.claim("MemoryCardA.EUR.raw", {}) == (len("MemoryCardA..raw"), "EUR")

    def test_a_region_hole_matches_no_other_word(self):
        rule = self._rule(names=["MemoryCardA.<region>.raw"])
        assert rule.claim("MemoryCardA.USA.251.raw", {}) is None
        assert rule.claim("MemoryCardA.KOR.raw", {}) is None

    def test_a_name_without_a_region_hole_names_no_region(self):
        assert self._rule().claim("Example Game (USA).srm", {}) == (len(".srm"), None)

    def test_a_folder_rule_reads_the_directorys_trailing_segments(self):
        rule = self._rule(names=["<region>/Card A"], folder=True)
        assert rule.claim("/saves/dolphin-emu/GC/JAP/Card A", {}) == (len("/Card A"), "JAP")
        assert rule.claim("/saves/dolphin-emu/GC/JAP/Card B", {}) is None
        assert rule.claim("/saves/dolphin-emu/GC/USA/Card A/", {}) == (len("/Card A"), "USA")

    def test_a_folder_rule_matches_at_a_segment_boundary(self):
        rule = self._rule(names=["data"], folder=True)
        assert rule.claim("/nand/title/00010000/52534245/data", {}) is not None
        assert rule.claim("/nand/title/00010000/52534245/userdata", {}) is None

    def test_a_hole_in_a_folder_name_spans_the_segments_that_fill_it(self):
        rule = self._rule(names=["title/<save_id>/data"], folder=True)
        assert rule.claim("/nand/title/00010000/52534245/data", {}) == (len("title//data"), None)
        assert rule.claim("/nand/title/<save_id>/data", {}) == (len("title//data"), None)


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
            format_name
            for record in table.emulators.values()
            for system in record.systems.values()
            for rule in system.parts
            for format_name in (rule.format, *(value for _, value in rule.kinds))
            if format_name is not None
        } | {side for c in table.conversions for side in (c.source, c.target)}
        assert used == set(table.formats)

    def test_every_cartridge_kind_is_one_a_record_states(self):
        stated = {
            kind
            for record in save_formats().emulators.values()
            for system in record.systems.values()
            for kind in (
                *(kind for rule in system.parts for kind, _ in rule.kinds),
                *(kind for statement in system.not_run for kind in statement.kinds),
            )
        }
        assert stated == set(CARTRIDGE_KINDS)


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
            if record.kind != "libretro" or record.key not in captured:
                continue
            build = captured[record.key]["build"]
            assert build in record.build.pins, record.key
            named_by = build if record.build.names == "commit" else cards[record.key].provenance
            assert record.build.revision in named_by, record.key

    def test_a_core_without_a_capture_pins_its_save_memory_build(self):
        # A core whose registration was never captured has its build recorded
        # where its save files are: the verified_core of each system its
        # record covers. A commit-named pin names the revision; a release-named
        # one cannot, and its build citation says what it was read at instead.
        captured = json.loads(_REGISTRATIONS.read_text(encoding="utf-8"))["cores"]
        memory = {record.key: record for record in load_save_memory()}
        checked = []
        for record in save_formats().emulators.values():
            if record.kind != "libretro" or record.key in captured:
                continue
            for system in record.systems:
                stated = memory[record.key].for_system(system)
                assert stated is not None, (record.key, system)
                assert stated.verified_core in record.build.pins, (record.key, system)
            named_by = record.build.pins[0] if record.build.names == "commit" else record.build.citation
            assert record.build.revision in named_by, record.key
            checked.append(record.key)
        assert checked, "no record is held to a save_memory build"

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
            if card is None or record.key in _NAMED_BY_THE_CODE:
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


class TestEveryRetroArchFileIsAPart:
    """For a core RetroArch writes the save for, every file its memory ids give is a part, and RetroArch's.

    Such a core has no save card: its save answer names the files RetroArch
    writes for the memory ids the core fills (``atlas/data/save_memory.json``),
    so the record is held to that statement the way a carded core's record is
    held to its card.
    """

    @staticmethod
    def _covered() -> list[tuple[Any, str, Any]]:
        cards = {card.key for card in load_oddities()}
        memory = {record.key: record for record in load_save_memory()}
        return [
            (record, system, memory[record.key].for_system(system))
            for record in save_formats().emulators.values()
            if record.kind == "libretro" and record.key not in cards and record.key in memory
            for system in record.systems
        ]

    def test_every_memory_id_file_is_claimed(self):
        unclaimed = [
            f"{record.key} {system}: {template}"
            for record, system, stated in self._covered()
            for template in stated.file_templates
            if not _claimed(template, record.systems[system], None)
        ]
        assert self._covered()
        assert unclaimed == []

    def test_every_rule_is_retroarchs(self):
        wrong = [
            f"{record.key} {system}: {rule.names}"
            for record, system, _ in self._covered()
            for rule in record.systems[system].parts
            if not rule.frontend
        ]
        assert wrong == []

    def test_a_rule_names_only_what_the_memory_ids_give(self):
        invented = [
            f"{record.key} {system}: {name}"
            for record, system, stated in self._covered()
            for rule in record.systems[system].parts
            for name in rule.names
            if name not in stated.file_templates
        ]
        assert invented == []


class TestEveryCartridgeKindIsAccountedFor:
    """In a record that tells cartridge kinds apart, every kind a part can hold game data for is stated, cited, apart.

    Every record of a Game Boy system is held. A kind counts as covered for a
    rule when exactly one cited statement names
    it: the system's ``not_run`` (the emulator does not run it), the rule's
    ``kinds`` (the format the rule's citation reads for it, or ``null`` where
    it keeps no such part), or the rule's ``unread`` (no read establishes it,
    and the citation says why). Nothing falls back to the rule's base format
    unnamed, so a verdict never rests on a kind no read covered (#577 D15). A
    clock rule is held to the kinds that carry a clock; for every other kind a
    clock part holds no game data by construction (#577 D16).
    """

    @staticmethod
    def _miscounted(rule: Any, refused: set[str]) -> list[tuple[str, int]]:
        """Every kind of the rule's universe whose cited statements are not exactly one, with their count."""
        universe = CLOCK_CARTRIDGE_KINDS if rule.memory == "cartridge-clock" else CARTRIDGE_KINDS
        stated = [kind for kind, _ in rule.kinds]
        unread = [kind for group in rule.unread for kind in group.kinds]
        counts = ((kind, (kind in refused) + stated.count(kind) + unread.count(kind)) for kind in universe)
        return [(kind, count) for kind, count in counts if count != 1]

    def test_every_kind_has_exactly_one_cited_statement(self):
        # Every record of a Game Boy system is held, whatever it states: one
        # that tells no kind apart fails rather than escaping the sweep.
        unaccounted = [
            f"{record.key} {system} {rule.memory} {kind}: {count} statements"
            for record in save_formats().emulators.values()
            for system, rules in record.systems.items()
            if system in _GAME_BOY_SYSTEMS
            for rule in rules.parts
            for kind, count in self._miscounted(rule, {k for statement in rules.not_run for k in statement.kinds})
        ]
        assert unaccounted == []

    def test_every_game_boy_record_that_runs_mbc2_states_its_mirror_handling(self):
        # Every MBC2 cartridge's RAM is small enough to be mirrored, so where a
        # record's emulator runs it, how it keeps a mirror write is stated (#577 D18).
        unstated = [
            f"{record.key} {system}"
            for record in save_formats().emulators.values()
            for system, rules in record.systems.items()
            if system in _GAME_BOY_SYSTEMS
            and rules.does_not_run("mbc2") is None
            and rules.mirrors_for("mbc2") is None
        ]
        assert unstated == []

    def test_the_sweep_holds_records(self):
        held = {record.key for record in save_formats().emulators.values() if set(record.systems) & _GAME_BOY_SYSTEMS}
        assert len(held) >= 8


class TestEveryLengthIsStatedForItsWholeSystem:
    """Where a system's emulators size a game's save each their own way, every rule of the system says how (#577 D19).

    A rule that stated none would be judged on its format alone, and two
    formats that match would claim a verdict a game whose length differs
    breaks. So on a system in :data:`_LENGTH_SYSTEMS` every rule of every record
    states its ``length``, and on every other system none does, so a length
    never meets a rule without one.
    """

    def test_a_system_states_a_length_on_every_rule_or_on_none(self):
        stated = [
            (system, record.key, rule.length is not None)
            for record in save_formats().emulators.values()
            for system, rules in record.systems.items()
            for rule in rules.parts
        ]
        wrong = [
            f"{key} {system}: {'states' if has else 'lacks'} a length"
            for system, key, has in stated
            if has != (system in _LENGTH_SYSTEMS)
        ]
        assert wrong == []

    def test_the_sweep_holds_records(self):
        held = {record.key for record in save_formats().emulators.values() if set(record.systems) & _LENGTH_SYSTEMS}
        assert len(held) >= 5
