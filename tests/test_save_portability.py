"""Tests for atlas.save_portability — whether one entry reads the save another wrote (#577).

The conformance vectors (``vectors/machines/save-portability.json``) carry the
question end to end on whole machines. These hold the computation's own rules,
each against the smallest answer pair that shows it: the summary order (D9),
the part a target keeps none of (D8), a part the target's mode keeps but its
answer names no file for, the granularity a shared card differs by (D11), the
build a record was read at (D5, D12), and what an answer the records cannot
label says about itself.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

import pytest

import atlas
from atlas.machine import FixtureMachine
from atlas.placement import (
    FILE_SET_DECLARED,
    Caveat,
    GRANULARITY_PER_GAME_FILE,
    GRANULARITY_PER_GAME_FILES,
    GRANULARITY_SHARED_CARD,
    ROLE_BATTERY,
    ROLE_MEMORY_CARD,
    ROLE_UNKNOWN,
    ROOT_SAVEFILE_DIRECTORY,
    FileGroup,
    FileSet,
    Granularity,
    OptionReading,
    SavefilePlacement,
    Unresolved,
)
from atlas.portability_codes import (
    PART_NO_GAME_DATA,
    CAVEAT_ENTRY_LABEL_UNKNOWN,
    CAVEAT_SAVE_PART_UNESTABLISHED,
    CAVEAT_SAVE_ANSWER_UNRESOLVED,
    CAVEAT_SAVE_FORMAT_BUILD_UNCONFIRMED,
    CAVEAT_SAVE_FORMAT_UNRECORDED,
)
from atlas.save_formats import SaveFormats, load_save_formats
from atlas.save_portability import (
    MOVE_DIRECTORY,
    MOVE_GRANULARITY,
    MOVE_NAME,
    PORTABILITY_CONVERTIBLE,
    PORTABILITY_NOT_READABLE,
    PORTABILITY_SAME_DATA,
    PORTABILITY_SAME_FILES,
    PORTABILITY_UNESTABLISHED,
    PORTABILITY_VERDICTS,
    Conversion,
    SavePart,
    portability_answer,
)

SAVES = "/saves"
CARDS = "/cards"


@dataclass(frozen=True)
class _Entry:
    label: str
    core_so: str | None
    emulator: str | None


def _rule(memory: str, slot: str | None, fmt: str | None, names: list[str], **extra: object) -> dict[str, object]:
    return {"memory": memory, "slot": slot, "format": fmt, "names": names, "citation": "[V] x.c:1 at abc1234", **extra}


def _record(
    kind: str,
    parts: list[dict[str, object]],
    *,
    names: str = "commit",
    ignore=None,
    not_run=None,
    mirrors=None,
    regionless: dict[str, object] | None = None,
) -> dict[str, object]:
    system: dict[str, object] = {"parts": parts}
    if regionless:
        system["regionless"] = regionless
    if ignore:
        system["ignore"] = ignore
    if not_run:
        system["not_run"] = not_run
    if mirrors:
        system["mirrors"] = [{"kinds": ["mbc2"], "handling": mirrors, "citation": f"[V] {mirrors}.c:1"}]
    return {
        "kind": kind,
        "build": {"pins": ["1.0 abc1234"], "names": names, "revision": "abc1234", "citation": "c"},
        "systems": {"psx": system},
    }


def _table(emulators: dict[str, object], conversions=()) -> SaveFormats:
    return load_save_formats(
        json.dumps(
            {
                "schema": 1,
                "spec": "s",
                "formats": {
                    "card": "a card",
                    "folder": "a folder card",
                    "other": "another layout",
                    "clock": "a clock",
                    "own-clock": "another clock",
                },
                "conversions": list(conversions),
                "emulators": emulators,
                "retroarch": {"compressed": "[V] save.c:644-657 at a79435a", "loads_raw": "[V] save.c:492-541 at a79435a"},
            }
        )
    )


def _placement(*groups: FileGroup, mode: str | None = None, readings=(), caveats=()) -> SavefilePlacement:
    first = groups[0].dir if groups else SAVES
    files = tuple(f for g in groups if g.dir == first and g.files is not None for f in g.files)
    return SavefilePlacement(
        dir=first,
        root_kind=ROOT_SAVEFILE_DIRECTORY,
        needs=(),
        file_set=FileSet(state=FILE_SET_DECLARED, files=files, provenance="p", groups=groups),
        sources=(),
        caveats=tuple(caveats),
        granularity=Granularity(
            value=groups[0].granularity if groups else GRANULARITY_PER_GAME_FILE,
            mode=mode,
            readings=tuple(OptionReading(k, v, "p", None) for k, v in readings),
            alternatives=(),
            provenance="p",
        ),
    )


def _group(*files: str, directory: str = SAVES, granularity: str = GRANULARITY_PER_GAME_FILE, role=ROLE_MEMORY_CARD):
    return FileGroup(dir=directory, files=files or None, granularity=granularity, role=role)


SOURCE = _Entry("Source", "src_libretro.so", "src_libretro.so")
TARGET = _Entry("Target", "dst_libretro.so", "dst_libretro.so")


def _ask(source: SavefilePlacement | Unresolved, target: SavefilePlacement | Unresolved):
    answers = {SOURCE.label: source, TARGET.label: target}
    return lambda entry: answers[entry.label]


def _answer(source, target, table, *, running=lambda entry: "1.0 abc1234", labels=("Source", "Target")):
    return portability_answer(
        "psx",
        source=labels[0],
        target=labels[1],
        entries=(SOURCE, TARGET),
        ask=_ask(source, target),
        running=running,
        token=lambda entry: entry.emulator,
        table=table,
    )


CARD_RULES = [
    _rule("memory-card", "1", "card", ["<rom_stem>.srm", "<rom_stem>_1.mcd"]),
    _rule("memory-card", "2", "card", ["<rom_stem>.1.mcr", "<rom_stem>_2.mcd"]),
]
TWO_CORES = _table({"src": _record("libretro", CARD_RULES), "dst": _record("libretro", CARD_RULES)})


class TestTheSummaryIsTheMostSeverePart:
    def test_the_verdicts_run_from_the_mildest_to_the_most_severe(self):
        assert PORTABILITY_VERDICTS == (
            PORTABILITY_SAME_FILES,
            PORTABILITY_SAME_DATA,
            PORTABILITY_CONVERTIBLE,
            PORTABILITY_UNESTABLISHED,
            PORTABILITY_NOT_READABLE,
        )

    def test_a_lost_part_makes_the_pair_not_readable_while_the_other_carries_over(self):
        table = _table({"src": _record("libretro", CARD_RULES), "dst": _record("libretro", CARD_RULES[:1])})
        source = _placement(_group("Game.srm"), _group("Game.1.mcr"))
        target = _placement(_group("Game.srm"))
        answer = _answer(source, target, table)
        assert [p.verdict for p in answer.parts] == [PORTABILITY_SAME_FILES, PORTABILITY_NOT_READABLE]
        assert answer.verdict == PORTABILITY_NOT_READABLE
        assert answer.parts[1].target_group is None

    def test_parts_that_all_carry_over_in_place_are_the_same_files(self):
        answer = _answer(_placement(_group("Game.srm")), _placement(_group("Game.srm")), TWO_CORES)
        assert answer.verdict == PORTABILITY_SAME_FILES
        assert answer.caveats == ()


class TestWhatDiffersBetweenTwoGroupsOfOneData:
    def test_another_name_and_another_directory_are_both_named(self):
        target = _placement(_group("Game_1.mcd", directory=CARDS))
        part = _answer(_placement(_group("Game.srm")), target, TWO_CORES).parts[0]
        assert (part.verdict, part.moves) == (PORTABILITY_SAME_DATA, (MOVE_DIRECTORY, MOVE_NAME))

    def test_a_shared_card_against_a_per_game_one_names_the_granularity(self):
        target = _placement(_group("Game.srm", granularity=GRANULARITY_SHARED_CARD))
        part = _answer(_placement(_group("Game.srm")), target, TWO_CORES).parts[0]
        assert part.moves == (MOVE_GRANULARITY,)

    def test_two_per_game_spellings_are_one_granularity(self):
        # A group of several of one game's files and a group of one are both the
        # game's own, so nothing a copy overwrites differs between them.
        target = _placement(_group("Game.srm", granularity=GRANULARITY_PER_GAME_FILES))
        part = _answer(_placement(_group("Game.srm")), target, TWO_CORES).parts[0]
        assert part.verdict == PORTABILITY_SAME_FILES


class TestAPartTheTargetDoesNotName:
    def test_a_mode_that_keeps_no_such_part_does_not_read_it(self):
        rules = [CARD_RULES[0], {**CARD_RULES[1], "modes": "card2-on"}]
        table = _table({"src": _record("libretro", CARD_RULES), "dst": _record("libretro", rules)})
        source = _placement(_group("Game.srm"), _group("Game.1.mcr"))
        part = _answer(source, _placement(_group("Game.srm"), mode="card2-off"), table).parts[1]
        assert part.verdict == PORTABILITY_NOT_READABLE

    def test_a_mode_that_keeps_it_with_no_file_named_yet_is_unestablished(self):
        rules = [CARD_RULES[0], {**CARD_RULES[1], "modes": "card2-on"}]
        table = _table({"src": _record("libretro", CARD_RULES), "dst": _record("libretro", rules)})
        source = _placement(_group("Game.srm"), _group("Game.1.mcr"))
        answer = _answer(source, _placement(_group("Game.srm"), mode="card2-on"), table)
        assert (answer.parts[1].verdict, answer.parts[1].target_group) == (PORTABILITY_UNESTABLISHED, None)
        assert [(c.code, dict(c.data)) for c in answer.caveats] == [
            (CAVEAT_SAVE_PART_UNESTABLISHED, {"memory": "memory-card", "slot": "2", "reason": "target-file-unnamed"})
        ]


class TestFormats:
    def test_another_format_with_a_stated_conversion_is_convertible(self):
        folder = [_rule("memory-card", "1", "folder", ["Mcd001.ps2"], folder=True)]
        table = _table(
            {"src": _record("libretro", folder), "dst": _record("libretro", CARD_RULES)},
            conversions=[{"from": "folder", "to": "card", "rule": None, "citation": "[V] conv.c:1"}],
        )
        folder_card = FileGroup(
            dir="/cards/Mcd001.ps2", files=None, granularity=GRANULARITY_PER_GAME_FILES, role=ROLE_MEMORY_CARD
        )
        source = _placement(folder_card)
        answer = _answer(source, _placement(_group("Game.srm")), table)
        assert [p.verdict for p in answer.parts] == [PORTABILITY_CONVERTIBLE]
        assert "[V] conv.c:1" in answer.sources

    def test_another_format_with_no_conversion_is_unestablished(self):
        other = [_rule("memory-card", "1", "other", ["<rom_stem>.srm"])]
        table = _table({"src": _record("libretro", other), "dst": _record("libretro", CARD_RULES)})
        answer = _answer(_placement(_group("Game.srm")), _placement(_group("Game.srm")), table)
        assert answer.parts[0].verdict == PORTABILITY_UNESTABLISHED
        assert [(c.code, dict(c.data)) for c in answer.caveats] == [
            (CAVEAT_SAVE_PART_UNESTABLISHED, {"memory": "memory-card", "slot": "1", "reason": "no-conversion-stated"})
        ]


    def test_another_format_retroarch_loads_as_it_lies_is_not_readable(self):
        # RetroArch copies a file it keeps for a core into the core's buffer
        # byte for byte, so the target reads another layout as its own.
        other = [_rule("memory-card", "1", "other", ["<rom_stem>.srm"], frontend=True)]
        card = [_rule("memory-card", "1", "card", ["<rom_stem>.srm"], frontend=True)]
        table = _table({"src": _record("libretro", other), "dst": _record("libretro", card)})
        answer = _answer(_placement(_group("Game.srm")), _placement(_group("Game.srm")), table)
        assert (answer.parts[0].verdict, answer.caveats) == (PORTABILITY_NOT_READABLE, ())
        assert "[V] save.c:492-541 at a79435a" in answer.sources


def _sized(fmt: str, rule: str, revision: str | None = None, **extra: object) -> list[dict[str, object]]:
    """One cartridge-ram rule whose length *rule* (at *revision*, for a ROM list) decides, with its own citation."""
    length: dict[str, object] = {"rule": rule, "citation": f"[V] {rule}.c:1 at abc1234, list {revision}"}
    if revision is not None:
        length["revision"] = revision
    return [_rule("cartridge-ram", None, fmt, ["<rom_stem>.sav"], length=length, **extra)]


class TestAPartSizedPerGameInDifferentWays:
    """Two emulators that size one game's save each their own way may cut or pad it, for games atlas cannot name (D19)."""

    @staticmethod
    def _sized_answer(source: list[dict[str, object]], target: list[dict[str, object]]):
        table = _table({"src": _record("libretro", source), "dst": _record("libretro", target)})
        group = _group("Game.sav", role=ROLE_BATTERY)
        return _answer(_placement(group), _placement(group), table)

    def test_two_ways_of_sizing_leave_the_part_unestablished_naming_each(self):
        answer = self._sized_answer(_sized("card", "rom-list", "def5678"), _sized("card", "written-extent"))
        assert (answer.verdict, answer.parts[0].verdict) == (PORTABILITY_UNESTABLISHED, PORTABILITY_UNESTABLISHED)
        assert [(c.code, dict(c.data)) for c in answer.caveats] == [
            (
                CAVEAT_SAVE_PART_UNESTABLISHED,
                {
                    "memory": "cartridge-ram",
                    "reason": "save-length-differs-by-game",
                    "source_length": "rom-list",
                    "source_length_revision": "def5678",
                    "target_length": "written-extent",
                },
            )
        ]

    def test_two_rom_lists_at_different_revisions_stay_unestablished(self):
        answer = self._sized_answer(_sized("card", "rom-list", "def5678"), _sized("card", "rom-list", "fed8765"))
        assert answer.parts[0].verdict == PORTABILITY_UNESTABLISHED
        assert [dict(c.data) for c in answer.caveats] == [
            {
                "memory": "cartridge-ram",
                "reason": "save-length-differs-by-game",
                "source_length": "rom-list",
                "source_length_revision": "def5678",
                "target_length": "rom-list",
                "target_length_revision": "fed8765",
            }
        ]

    def test_each_sides_length_citation_rides_the_sources(self):
        answer = self._sized_answer(_sized("card", "rom-list", "def5678"), _sized("card", "written-extent"))
        assert {"[V] rom-list.c:1 at abc1234, list def5678", "[V] written-extent.c:1 at abc1234, list None"} <= set(
            answer.sources
        )

    def test_one_way_of_sizing_keeps_the_plain_verdict(self):
        listed = self._sized_answer(_sized("card", "rom-list", "def5678"), _sized("card", "rom-list", "def5678"))
        grown = self._sized_answer(_sized("card", "written-extent"), _sized("card", "written-extent"))
        assert (listed.verdict, listed.caveats, grown.verdict, grown.caveats) == (
            PORTABILITY_SAME_FILES,
            (),
            PORTABILITY_SAME_FILES,
            (),
        )

    def test_the_sizes_are_judged_before_the_formats(self):
        # Another layout toward a part RetroArch loads byte for byte would be
        # not-readable; a save cut or padded on the way is the earlier doubt,
        # since whether the bytes land at all is what the lengths decide.
        answer = self._sized_answer(_sized("other", "written-extent"), _sized("card", "rom-list", "def5678", frontend=True))
        assert answer.parts[0].verdict == PORTABILITY_UNESTABLISHED
        assert [c.data["reason"] for c in answer.caveats] == ["save-length-differs-by-game"]

    def test_a_target_that_keeps_no_such_part_still_does_not_read_it(self):
        table = _table(
            {"src": _record("libretro", _sized("card", "written-extent")), "dst": _record("libretro", CARD_RULES)}
        )
        answer = _answer(_placement(_group("Game.sav", role=ROLE_BATTERY)), _placement(_group("Game.srm")), table)
        assert (answer.parts[0].verdict, answer.caveats) == (PORTABILITY_NOT_READABLE, ())

    def test_a_length_word_outside_the_rules_is_refused_at_construction(self):
        with pytest.raises(ValueError, match="must be one of"):
            Caveat(CAVEAT_SAVE_PART_UNESTABLISHED, "m", {"source_length": "rom-list-def5678"})
        with pytest.raises(ValueError, match="must be one of"):
            Caveat(CAVEAT_SAVE_PART_UNESTABLISHED, "m", {"target_length": "the-game"})

REGION_CARD_RULES = [
    _rule("memory-card", "A", "folder", ["<region>/Card A"], folder=True),
    _rule("memory-card", "B", "folder", ["<region>/Card B"], folder=True),
]


def _region_folder(root: str, region: str, slot: str = "A") -> FileGroup:
    return FileGroup(
        dir=f"{root}/GC/{region}/Card {slot}", files=None, granularity=GRANULARITY_PER_GAME_FILES, role=ROLE_MEMORY_CARD
    )


class TestAMemoryKeptPerRegion:
    """One part per slot and region, each paired with the target's memory of the same region (#577 D21)."""

    TABLE = _table({"src": _record("libretro", REGION_CARD_RULES), "dst": _record("libretro", REGION_CARD_RULES)})

    def test_each_region_is_a_part_paired_with_the_targets_same_region(self):
        regions = ("USA", "EUR", "JAP")
        source = _placement(*(_region_folder("/a", region) for region in regions))
        target = _placement(*(_region_folder("/b", region) for region in reversed(regions)))
        answer = _answer(source, target, self.TABLE)
        assert [(p.slot, p.region, p.verdict, p.moves) for p in answer.parts] == [
            ("A", region, PORTABILITY_SAME_DATA, (MOVE_DIRECTORY,)) for region in regions
        ]
        assert [(p.source_group.dir, p.target_group.dir if p.target_group else None) for p in answer.parts] == [
            (f"/a/GC/{region}/Card A", f"/b/GC/{region}/Card A") for region in regions
        ]

    def test_a_region_the_target_keeps_no_memory_for_does_not_carry_over(self):
        source = _placement(_region_folder("/a", "USA"), _region_folder("/a", "JAP"))
        answer = _answer(source, _placement(_region_folder("/b", "USA")), self.TABLE)
        assert [(p.region, p.verdict) for p in answer.parts] == [
            ("USA", PORTABILITY_SAME_DATA),
            ("JAP", PORTABILITY_UNESTABLISHED),
        ]

    def test_two_slots_of_one_region_stay_apart(self):
        source = _placement(_region_folder("/a", "EUR", "B"))
        target = _placement(_region_folder("/b", "EUR", "A"), _region_folder("/b", "EUR", "B"))
        answer = _answer(source, target, self.TABLE)
        assert [(p.slot, p.region) for p in answer.parts] == [("B", "EUR")]
        assert answer.parts[0].target_group is not None
        assert answer.parts[0].target_group.dir == "/b/GC/EUR/Card B"

    @staticmethod
    def _regionless_table(*, target_rules=REGION_CARD_RULES) -> SaveFormats:
        record = _record(
            "libretro",
            REGION_CARD_RULES,
            regionless={"extensions": [".dol", ".elf"], "citation": "[V] Boot.cpp:1 at abc1234"},
        )
        return _table({"src": record, "dst": _record("libretro", target_rules)})

    def test_content_with_no_region_leaves_each_region_s_part_unestablished(self):
        source = _placement(_region_folder("/a", "USA"), _region_folder("/a", "EUR"))
        target = _placement(_region_folder("/b", "USA"), _region_folder("/b", "EUR"))
        answer = portability_answer(
            "psx", source="Source", target="Target", entries=(SOURCE, TARGET), ask=_ask(source, target),
            running=lambda entry: "1.0 abc1234", token=lambda entry: entry.emulator, table=self._regionless_table(),
            content_path="/roms/gc/Homebrew.DOL",
        )
        assert [(p.region, p.verdict, p.moves) for p in answer.parts] == [
            ("USA", PORTABILITY_UNESTABLISHED, ()),
            ("EUR", PORTABILITY_UNESTABLISHED, ()),
        ]
        assert [c.data["reason"] for c in answer.caveats] == ["region-not-in-content", "region-not-in-content"]
        assert "[V] Boot.cpp:1 at abc1234" in answer.sources

    def test_the_targets_statement_alone_leaves_the_part_unestablished(self):
        target_record = _record(
            "libretro", REGION_CARD_RULES, regionless={"extensions": [".dol"], "citation": "[V] Dst.cpp:1 at abc1234"}
        )
        table = _table({"src": _record("libretro", REGION_CARD_RULES), "dst": target_record})
        source, target = _placement(_region_folder("/a", "JAP")), _placement(_region_folder("/b", "JAP"))
        answer = portability_answer(
            "psx", source="Source", target="Target", entries=(SOURCE, TARGET), ask=_ask(source, target),
            running=lambda entry: "1.0 abc1234", token=lambda entry: entry.emulator, table=table,
            content_path="/roms/gc/Homebrew.dol",
        )
        assert (answer.parts[0].verdict, "[V] Dst.cpp:1 at abc1234" in answer.sources) == (PORTABILITY_UNESTABLISHED, True)

    def test_a_memory_kept_for_every_region_is_untouched_by_content_with_no_region(self):
        record = _record(
            "libretro", CARD_RULES, regionless={"extensions": [".dol"], "citation": "[V] Boot.cpp:1 at abc1234"}
        )
        table = _table({"src": record, "dst": _record("libretro", CARD_RULES)})
        source, target = _placement(_group("Game.srm")), _placement(_group("Game.srm"))
        answer = portability_answer(
            "psx", source="Source", target="Target", entries=(SOURCE, TARGET), ask=_ask(source, target),
            running=lambda entry: "1.0 abc1234", token=lambda entry: entry.emulator, table=table,
            content_path="/roms/gc/Homebrew.dol",
        )
        assert (answer.verdict, answer.caveats) == (PORTABILITY_SAME_FILES, ())

    def test_a_disc_keeps_its_verdict(self):
        source = _placement(_region_folder("/a", "USA"))
        target = _placement(_region_folder("/b", "USA"))
        answer = portability_answer(
            "psx", source="Source", target="Target", entries=(SOURCE, TARGET), ask=_ask(source, target),
            running=lambda entry: "1.0 abc1234", token=lambda entry: entry.emulator, table=self._regionless_table(),
            content_path="/roms/gc/Game.iso",
        )
        assert (answer.parts[0].verdict, answer.caveats) == (PORTABILITY_SAME_DATA, ())

    def test_a_slot_the_target_keeps_nothing_in_is_lost_whatever_the_region(self):
        source = _placement(_region_folder("/a", "USA", "B"))
        table = self._regionless_table(target_rules=REGION_CARD_RULES[:1])
        answer = portability_answer(
            "psx", source="Source", target="Target", entries=(SOURCE, TARGET), ask=_ask(source, _placement(_region_folder("/b", "USA"))),
            running=lambda entry: "1.0 abc1234", token=lambda entry: entry.emulator, table=table,
            content_path="/roms/gc/Homebrew.elf",
        )
        assert (answer.parts[0].verdict, answer.caveats) == (PORTABILITY_NOT_READABLE, ())

    RAW = [_rule("memory-card", letter, "card", ["<card>.<region>.<ext>"]) for letter in ("A", "B")]

    @staticmethod
    def _card(name: str, directory: str, *slots: str) -> FileGroup:
        return FileGroup(
            dir=directory, files=(name,), granularity=GRANULARITY_SHARED_CARD, role=ROLE_MEMORY_CARD, slots=slots
        )

    def _raw_answer(self, *source_groups: FileGroup, target_rules=None):
        table = _table({"src": _record("libretro", self.RAW), "dst": _record("libretro", target_rules or self.RAW[:1])})
        target = _placement(self._card("MemoryCardA.USA.raw", "/t", "A"))
        return _answer(_placement(*source_groups), target, table)

    def test_a_card_is_the_part_of_the_slot_the_answer_says_reads_it(self):
        # Slot A reads a file called MemoryCardB; slot B its default card; the
        # target keeps nothing in slot B.
        answer = self._raw_answer(
            self._card("MemoryCardB.USA.raw", "/cards", "A"), self._card("MemoryCardB.USA.raw", "/gc", "B")
        )
        assert [(p.slot, p.region, p.source_group.dir, p.verdict) for p in answer.parts] == [
            ("A", "USA", "/cards", PORTABILITY_SAME_DATA),
            ("B", "USA", "/gc", PORTABILITY_NOT_READABLE),
        ]

    def test_two_cards_of_one_name_are_each_their_own_slots(self):
        answer = self._raw_answer(self._card("Mine.USA.raw", "/a", "A"), self._card("Mine.USA.raw", "/b", "B"))
        assert [(p.slot, p.source_group.dir) for p in answer.parts] == [("A", "/a"), ("B", "/b")]

    def test_one_file_two_slots_read_is_a_part_of_each(self):
        answer = self._raw_answer(self._card("Mine.USA.raw", "/cards", "A", "B"), target_rules=self.RAW)
        assert [(p.slot, p.source_group.dir) for p in answer.parts] == [("A", "/cards"), ("B", "/cards")]

    def test_each_slot_takes_its_own_closest_rule(self):
        # Slot A's rule spells the default name more literally than slot B's;
        # a file both slots read is still a part of each.
        rules = [
            _rule("memory-card", "A", "card", ["MemoryCardA.<region>.raw"]),
            _rule("memory-card", "B", "card", ["<card>.<region>.<ext>"]),
        ]
        table = _table({"src": _record("libretro", rules), "dst": _record("libretro", rules)})
        source = _placement(self._card("MemoryCardA.USA.raw", "/gc", "A", "B"))
        target = _placement(self._card("MemoryCardA.USA.raw", "/t", "A"), self._card("MemoryCardB.USA.raw", "/t", "B"))
        answer = _answer(source, target, table)
        assert [p.slot for p in answer.parts] == ["A", "B"]

    def test_a_group_without_slots_is_claimed_by_name_alone(self):
        answer = self._raw_answer(self._card("Mine.USA.raw", "/cards"), target_rules=self.RAW)
        assert [p.slot for p in answer.parts] == ["A", "B"]

    @staticmethod
    def _unslotted(name: str, directory: str) -> FileGroup:
        return FileGroup(
            dir=directory, files=(name,), granularity=GRANULARITY_SHARED_CARD, role=ROLE_MEMORY_CARD, slots=None
        )

    def test_a_card_whose_slots_are_not_established_is_unestablished_for_each_slot(self):
        # Which slot reads the card is not established on the source side, so
        # neither is which of the target's slots it pairs with (#577 D24).
        answer = self._raw_answer(self._unslotted("MemoryCardA.USA.raw", "/cards"), target_rules=self.RAW)
        assert [(p.slot, p.verdict) for p in answer.parts] == [
            ("A", PORTABILITY_UNESTABLISHED),
            ("B", PORTABILITY_UNESTABLISHED),
        ]
        reasons = [c.data["reason"] for c in answer.caveats if c.code == atlas.CAVEAT_SAVE_PART_UNESTABLISHED]
        assert reasons == [atlas.REASON_SLOTS_UNESTABLISHED] * 2

    def test_a_target_card_whose_slots_are_not_established_leaves_the_pair_unestablished(self):
        table = _table({"src": _record("libretro", self.RAW[:1]), "dst": _record("libretro", self.RAW[:1])})
        source = _placement(self._card("MemoryCardA.USA.raw", "/gc", "A"))
        target = _placement(self._unslotted("MemoryCardA.USA.raw", "/t"))
        (part,) = _answer(source, target, table).parts
        assert (part.slot, part.verdict) == ("A", PORTABILITY_UNESTABLISHED)

    def test_a_memory_kept_for_every_region_has_no_region(self):
        answer = _answer(_placement(_group("Game.srm")), _placement(_group("Game.srm")), TWO_CORES)
        assert [(p.region, p.verdict) for p in answer.parts] == [(None, PORTABILITY_SAME_FILES)]

    def test_an_unestablished_part_names_its_region(self):
        other = [_rule("memory-card", "A", "other", ["<region>/Card A"], folder=True)]
        table = _table({"src": _record("libretro", other), "dst": _record("libretro", REGION_CARD_RULES)})
        answer = _answer(_placement(_region_folder("/a", "JAP")), _placement(_region_folder("/b", "JAP")), table)
        assert [dict(c.data) for c in answer.caveats] == [
            {"memory": "memory-card", "slot": "A", "region": "JAP", "reason": "no-conversion-stated"}
        ]

    def test_a_region_outside_the_vocabulary_is_refused_at_construction(self):
        group = _region_folder("/a", "USA")
        with pytest.raises(ValueError, match="region must be None or one of"):
            SavePart("memory-card", "A", PORTABILITY_SAME_FILES, group, group, region="KOR")
        with pytest.raises(ValueError, match="must be one of"):
            Caveat(CAVEAT_SAVE_PART_UNESTABLISHED, "m", {"region": "KOR"})


RAM = "cartridge-ram"
CLOCK = "cartridge-clock"
PACKED = [_rule(RAM, None, "card", ["<rom_stem>.srm"], frontend=True, kinds={"mbc2": "other"})]
PLAIN = [_rule(RAM, None, "card", ["<rom_stem>.srm"], frontend=True)]


def _cartridge_caveats(answer) -> list[dict[str, object]]:
    return [
        {key: dict(value) if key == "cartridges" else value for key, value in c.data.items()}
        for c in answer.caveats
        if c.code == CAVEAT_SAVE_PART_UNESTABLISHED
    ]


def _games(
    source_rules,
    target_rules,
    *,
    source_not_run=None,
    target_not_run=None,
    target_files=("Game.srm",),
    mirrors=(None, None),
):
    """A Game Boy-like pair: the source wrote what its rules name, the target's answer names *target_files*."""
    table = _table(
        {
            "src": _record("libretro", source_rules, not_run=source_not_run, mirrors=mirrors[0]),
            "dst": _record("libretro", target_rules, not_run=target_not_run, mirrors=mirrors[1]),
        }
    )
    written = ("Game.srm", "Game.rtc") if any(rule["memory"] == CLOCK for rule in source_rules) else ("Game.srm",)
    source = _placement(*(_group(name, role=ROLE_BATTERY) for name in written))
    target = _placement(*(_group(name, role=ROLE_BATTERY) for name in target_files))
    return _answer(source, target, table)


class TestAPartWhoseLayoutDiffersByTheCartridgesKind:
    """#577 D15: atlas reads no cartridge, so a verdict that depends on its kind is stated per kind."""

    def test_a_kind_laid_out_another_way_leaves_the_part_unestablished_with_each_kinds_verdict(self):
        answer = _games(PACKED, PLAIN)
        assert answer.parts[0].verdict == PORTABILITY_UNESTABLISHED
        assert _cartridge_caveats(answer) == [
            {
                "memory": RAM,
                "reason": "layout-differs-by-cartridge",
                "cartridges": {"mbc2": PORTABILITY_NOT_READABLE},
                "other_cartridges": PORTABILITY_SAME_FILES,
            }
        ]

    def test_two_records_that_lay_every_kind_out_alike_keep_the_plain_verdict(self):
        answer = _games(PACKED, PACKED)
        assert (answer.parts[0].verdict, answer.caveats) == (PORTABILITY_SAME_FILES, ())

    def test_a_kind_the_target_does_not_run_is_not_readable_and_its_citation_rides_the_sources(self):
        # A same-files verdict would not hold for a game of that kind, which the
        # target does not run (#577 D15) — even where the two layouts agree.
        refused = [{"kinds": ["mbc7"], "citation": "[V] loader.c:9 at abc1234"}]
        answer = _games(PLAIN, PLAIN, target_not_run=refused)
        assert _cartridge_caveats(answer) == [
            {
                "memory": RAM,
                "reason": "layout-differs-by-cartridge",
                "cartridges": {"mbc7": PORTABILITY_NOT_READABLE},
                "other_cartridges": PORTABILITY_SAME_FILES,
            }
        ]
        assert "[V] loader.c:9 at abc1234" in answer.sources

    def test_a_kind_whose_verdict_is_every_other_kinds_is_not_listed_apart(self):
        named = [_rule(RAM, None, "card", ["<rom_stem>.srm"], frontend=True, kinds={"mbc2": "other", "mbc7": "card"})]
        assert _cartridge_caveats(_games(named, PLAIN))[0]["cartridges"] == {"mbc2": PORTABILITY_NOT_READABLE}

    def test_a_kind_the_source_keeps_no_such_part_for_holds_no_game_data(self):
        # VBA-M's TAMA5 memory rides no memory id, so its .srm holds none of it:
        # nothing of such a game is lost, and beside a kind that differs the
        # answer says so (#577 D16).
        none_for_tama5 = [_rule(RAM, None, "card", ["<rom_stem>.srm"], frontend=True, kinds={"tama5": None})]
        assert _cartridge_caveats(_games(none_for_tama5, PACKED)) == [
            {
                "memory": RAM,
                "reason": "layout-differs-by-cartridge",
                "cartridges": {"mbc2": PORTABILITY_NOT_READABLE, "tama5": PART_NO_GAME_DATA},
                "other_cartridges": PORTABILITY_SAME_FILES,
            }
        ]

    def test_a_kind_that_holds_no_game_data_keeps_a_verdict_that_carries_the_rest_over(self):
        none_for_tama5 = [_rule(RAM, None, "card", ["<rom_stem>.srm"], frontend=True, kinds={"tama5": None})]
        answer = _games(none_for_tama5, PLAIN)
        assert (answer.parts[0].verdict, answer.caveats) == (PORTABILITY_SAME_FILES, ())

    def test_a_cartridge_kind_outside_the_vocabulary_is_refused_at_construction(self):
        with pytest.raises(ValueError, match="keys must be drawn from"):
            Caveat(CAVEAT_SAVE_PART_UNESTABLISHED, "m", {"cartridges": {"mbc4": PORTABILITY_SAME_FILES}})

    def test_a_word_per_kind_outside_the_statements_is_refused_at_construction(self):
        with pytest.raises(ValueError, match="must be one of"):
            Caveat(CAVEAT_SAVE_PART_UNESTABLISHED, "m", {"cartridges": {"mbc2": "lost"}})
        with pytest.raises(ValueError, match="must be one of"):
            Caveat(CAVEAT_SAVE_PART_UNESTABLISHED, "m", {"other_cartridges": "lost"})

    def test_a_kind_whose_layout_no_read_establishes_is_unestablished_for_it(self):
        # Never folded into every other kind's verdict (#577 D15): the plain
        # image of the other kinds says nothing about one nobody read.
        unread = [{**PLAIN[0], "unread": [{"kinds": ["huc1"], "citation": "[O] mapper.c at abc1234"}]}]
        answer = _games(PLAIN, unread)
        assert _cartridge_caveats(answer) == [
            {
                "memory": RAM,
                "reason": "layout-differs-by-cartridge",
                "cartridges": {"huc1": PORTABILITY_UNESTABLISHED},
                "other_cartridges": PORTABILITY_SAME_FILES,
            }
        ]
        assert "[O] mapper.c at abc1234" in answer.sources

    def test_two_records_that_keep_a_mirror_write_apart_leave_the_small_kind_unestablished(self):
        # The same layout, but a game writing through a mirror of MBC2's 512
        # cells lands elsewhere in each file (#577 D18).
        named = [{**PLAIN[0], "kinds": {"mbc2": "card"}}]
        answer = _games(named, named, mirrors=("folds", "stores-unfolded"))
        assert _cartridge_caveats(answer) == [
            {
                "memory": RAM,
                "reason": "layout-differs-by-cartridge",
                "cartridges": {"mbc2": PORTABILITY_UNESTABLISHED},
                "other_cartridges": PORTABILITY_SAME_FILES,
            }
        ]
        assert {"[V] folds.c:1", "[V] stores-unfolded.c:1"} <= set(answer.sources)

    def test_two_records_that_keep_a_mirror_write_alike_keep_the_plain_verdict(self):
        named = [{**PLAIN[0], "kinds": {"mbc2": "card"}}]
        answer = _games(named, named, mirrors=("drops", "drops"))
        assert (answer.parts[0].verdict, answer.caveats) == (PORTABILITY_SAME_FILES, ())

    def test_a_layout_that_differs_anyway_is_not_readable_whatever_the_mirrors(self):
        answer = _games(PACKED, PLAIN, mirrors=("folds", "drops"))
        assert _cartridge_caveats(answer)[0]["cartridges"] == {"mbc2": PORTABILITY_NOT_READABLE}

    def test_a_kind_the_source_does_not_run_is_skipped(self):
        # No save of that kind exists on the source's side to carry.
        refused = [{"kinds": ["mbc2"], "citation": "[V] loader.c:9 at abc1234"}]
        answer = _games(PLAIN, PACKED, source_not_run=refused)
        assert (answer.parts[0].verdict, answer.caveats) == (PORTABILITY_SAME_FILES, ())
        assert "[V] loader.c:9 at abc1234" in answer.sources


EVERY_GAME_CLOCK = _rule(CLOCK, None, "clock", ["<rom_stem>.rtc"], frontend=True)
TIMER_CLOCK = _rule(CLOCK, None, None, ["<rom_stem>.rtc"], frontend=True, kinds={"mbc3-timer": "clock"})
OWN_TIMER_CLOCK = _rule(CLOCK, None, None, ["<rom_stem>.rtc"], frontend=True, kinds={"mbc3-timer": "own-clock"})
NO_OTHER_CLOCK_KIND = [{"kinds": ["huc3", "tama5", "tpp1"], "citation": "[V] loader.c:9 at abc1234"}]


class TestAClockOnlySomeCartridgesHave:
    """#577 D16: a clock file a cartridge without a clock still gets holds no game data, so nothing is lost with it."""

    def test_a_clock_written_for_every_game_and_lost_for_clock_cartridges_is_unestablished(self):
        answer = _games(
            [*PLAIN, EVERY_GAME_CLOCK],
            [*PLAIN, OWN_TIMER_CLOCK],
            source_not_run=NO_OTHER_CLOCK_KIND,
            target_files=("Game.srm", "Game.rtc"),
        )
        assert [p.verdict for p in answer.parts] == [PORTABILITY_SAME_FILES, PORTABILITY_UNESTABLISHED]
        assert _cartridge_caveats(answer) == [
            {
                "memory": CLOCK,
                "reason": "layout-differs-by-cartridge",
                "cartridges": {"mbc3-timer": PORTABILITY_NOT_READABLE},
                "other_cartridges": PART_NO_GAME_DATA,
            }
        ]

    def test_a_clock_written_for_every_game_that_carries_over_where_it_matters_is_the_same_files(self):
        answer = _games(
            [*PLAIN, EVERY_GAME_CLOCK],
            [*PLAIN, EVERY_GAME_CLOCK],
            source_not_run=NO_OTHER_CLOCK_KIND,
            target_files=("Game.srm", "Game.rtc"),
        )
        assert ([p.verdict for p in answer.parts], answer.caveats) == ([PORTABILITY_SAME_FILES] * 2, ())

    def test_a_clock_written_for_clock_cartridges_alone_holds_nothing_for_the_rest(self):
        # The source writes no clock for a cartridge without one, so for those
        # games nothing is lost and the lost clock is not every game's (#577 D16).
        answer = _games(
            [*PLAIN, TIMER_CLOCK],
            [*PLAIN, OWN_TIMER_CLOCK],
            source_not_run=NO_OTHER_CLOCK_KIND,
            target_files=("Game.srm", "Game.rtc"),
        )
        assert [p.verdict for p in answer.parts] == [PORTABILITY_SAME_FILES, PORTABILITY_UNESTABLISHED]
        assert _cartridge_caveats(answer) == [
            {
                "memory": CLOCK,
                "reason": "layout-differs-by-cartridge",
                "cartridges": {"mbc3-timer": PORTABILITY_NOT_READABLE},
                "other_cartridges": PART_NO_GAME_DATA,
            }
        ]

    def test_a_target_with_no_clock_at_all_loses_only_the_clock_cartridges_clock(self):
        answer = _games([*PLAIN, EVERY_GAME_CLOCK], PLAIN, source_not_run=NO_OTHER_CLOCK_KIND)
        assert answer.parts[1].target_group is None
        assert _cartridge_caveats(answer) == [
            {
                "memory": CLOCK,
                "reason": "layout-differs-by-cartridge",
                "cartridges": {"mbc3-timer": PORTABILITY_NOT_READABLE},
                "other_cartridges": PART_NO_GAME_DATA,
            }
        ]

    def test_two_clocks_written_for_every_game_are_judged_for_each_clock_kind(self):
        # Neither rule names a kind, and the clock kinds are still told apart
        # from every other kind, whose file holds no game data.
        own = {**EVERY_GAME_CLOCK, "format": "own-clock"}
        answer = _games(
            [*PLAIN, EVERY_GAME_CLOCK],
            [*PLAIN, own],
            source_not_run=NO_OTHER_CLOCK_KIND,
            target_files=("Game.srm", "Game.rtc"),
        )
        assert _cartridge_caveats(answer)[0]["cartridges"] == {"mbc3-timer": PORTABILITY_NOT_READABLE}

    def test_a_target_that_opens_its_file_and_keeps_no_clock_for_a_kind_does_not_read_it(self):
        # No clock kept for the kind is not a layout the target might still
        # recognise: it is no part to read, whoever opens the file.
        timer_only = {**TIMER_CLOCK, "kinds": {"mbc3-timer": "clock", "huc3": None}, "frontend": False}
        both = _rule(CLOCK, None, None, ["<rom_stem>.rtc"], frontend=True, kinds={"mbc3-timer": "clock", "huc3": "clock"})
        answer = _games(
            [*PLAIN, both],
            [*PLAIN, timer_only],
            source_not_run=[{"kinds": ["tama5", "tpp1"], "citation": "c"}],
            target_files=("Game.srm", "Game.rtc"),
        )
        assert _cartridge_caveats(answer)[0]["cartridges"] == {
            "mbc3-timer": PORTABILITY_SAME_FILES,
            "huc3": PORTABILITY_NOT_READABLE,
        }

    def test_a_kind_the_target_runs_and_keeps_no_clock_for_is_not_readable_for_that_kind(self):
        both = _rule(CLOCK, None, None, ["<rom_stem>.rtc"], frontend=True, kinds={"mbc3-timer": "clock", "huc3": "clock"})
        timer_only = {**TIMER_CLOCK, "kinds": {"mbc3-timer": "clock", "huc3": None}}
        answer = _games(
            [*PLAIN, both],
            [*PLAIN, timer_only],
            source_not_run=[{"kinds": ["tama5", "tpp1"], "citation": "c"}],
            target_files=("Game.srm", "Game.rtc"),
        )
        assert _cartridge_caveats(answer) == [
            {
                "memory": CLOCK,
                "reason": "layout-differs-by-cartridge",
                "cartridges": {"mbc3-timer": PORTABILITY_SAME_FILES, "huc3": PORTABILITY_NOT_READABLE},
                "other_cartridges": PART_NO_GAME_DATA,
            }
        ]


COMPRESSED = Caveat("save-files-compressed", "m", {"key": "save_file_compression", "layer": "global", "file": "/c"})
FRONTEND_RULES = [{**CARD_RULES[0], "names": ["<rom_stem>.srm"], "frontend": True}, CARD_RULES[1]]
FILE_RULES = [{**CARD_RULES[0], "names": ["<rom_stem>_1.mcd"]}]


class TestACardRetroArchCompresses:
    """With save_file_compression on, RetroArch writes the file it saves for a core as an RZIP stream."""

    def test_an_emulator_that_opens_the_file_itself_has_it_decompressed_first(self):
        table = _table({"src": _record("libretro", FRONTEND_RULES), "dst": _record("standalone", FILE_RULES)})
        source = _placement(_group("Game.srm"), caveats=(COMPRESSED,))
        answer = _answer(source, _placement(_group("Game_1.mcd", directory=CARDS)), table)
        part = answer.parts[0]
        assert (part.verdict, part.moves, part.conversion) == (PORTABILITY_CONVERTIBLE, (), None)
        assert "[V] save.c:644-657 at a79435a" in answer.sources

    def test_a_core_retroarch_loads_it_for_reads_it_as_it_lies(self):
        table = _table({"src": _record("libretro", FRONTEND_RULES), "dst": _record("libretro", FRONTEND_RULES)})
        source = _placement(_group("Game.srm"), caveats=(COMPRESSED,))
        answer = _answer(source, _placement(_group("Game.srm")), table)
        assert answer.parts[0].verdict == PORTABILITY_SAME_FILES

    def test_an_uncompressed_card_carries_over_to_the_emulator_as_it_is(self):
        table = _table({"src": _record("libretro", FRONTEND_RULES), "dst": _record("standalone", FILE_RULES)})
        answer = _answer(_placement(_group("Game.srm")), _placement(_group("Game_1.mcd", directory=CARDS)), table)
        assert answer.parts[0].verdict == PORTABILITY_SAME_DATA


class TestTellingThePartsApart:
    def test_two_slots_on_one_card_file_are_both_its_parts(self):
        rules = [
            _rule("memory-card", "1", "card", ["Mcd001.ps2"], reading="Slot1_Filename"),
            _rule("memory-card", "2", "card", ["Mcd002.ps2"], reading="Slot2_Filename"),
        ]
        table = _table({"src": _record("standalone", rules), "dst": _record("libretro", CARD_RULES)})
        source_entry = _Entry("Source", None, "src")
        source = _placement(
            _group("one.ps2", directory=CARDS, granularity=GRANULARITY_SHARED_CARD),
            readings=(("Slot1_Filename", "one.ps2"), ("Slot2_Filename", "/cards/one.ps2")),
        )
        answer = portability_answer(
            "psx", source="Source", target="Target", entries=(source_entry, TARGET),
            ask=lambda entry: source if entry.label == "Source" else _placement(_group("Game.srm")),
            running=lambda entry: "1.0 abc1234", token=lambda entry: entry.emulator, table=table,
        )
        assert [(p.slot, p.source_group.files) for p in answer.parts] == [("1", ("one.ps2",)), ("2", ("one.ps2",))]

    def test_the_more_literal_name_claims_a_file_two_rules_match(self):
        rules = [
            _rule("internal-backup-ram", None, "card", ["<rom_stem>.ram"]),
            _rule("cartridge-backup-ram", None, "other", ["<rom_stem>-ext512K.ram"]),
        ]
        table = _table({"src": _record("libretro", rules), "dst": _record("libretro", rules)})
        both = _placement(_group("Game.ram", "Game-ext512K.ram", granularity=GRANULARITY_PER_GAME_FILES))
        parts = _answer(both, both, table).parts
        assert [(p.memory, p.source_group.files) for p in parts] == [
            ("internal-backup-ram", ("Game.ram",)),
            ("cartridge-backup-ram", ("Game-ext512K.ram",)),
        ]

    def test_a_directory_that_holds_nothing_of_the_system_is_set_aside(self):
        ignore = [{"dir_suffix": "kronos/stv", "citation": "c"}]
        table = _table(
            {"src": _record("libretro", CARD_RULES, ignore=ignore), "dst": _record("libretro", CARD_RULES)}
        )
        source = _placement(_group("Game.srm"), _group("Game.srm", directory="/saves/kronos/stv"))
        answer = _answer(source, _placement(_group("Game.srm")), table)
        assert (len(answer.parts), answer.caveats) == (1, ())
        assert "c" in answer.sources

    def test_a_file_the_record_sets_aside_by_name_is_no_part_and_says_why(self):
        ignore = [{"names": ["<rom_stem>.smpc"], "citation": "[V] smpc.cpp:357-369 at abc1234"}]
        table = _table(
            {"src": _record("libretro", CARD_RULES, ignore=ignore), "dst": _record("libretro", CARD_RULES)}
        )
        source = _placement(_group("Game.srm"), _group("Game.smpc"))
        answer = _answer(source, _placement(_group("Game.srm")), table)
        assert (len(answer.parts), answer.caveats, answer.verdict) == (1, (), PORTABILITY_SAME_FILES)
        assert "[V] smpc.cpp:357-369 at abc1234" in answer.sources

    def test_a_file_of_unknown_role_is_no_part(self):
        source = _placement(_group("Game.srm"), _group("Game.bak", role=ROLE_UNKNOWN))
        answer = _answer(source, _placement(_group("Game.srm")), TWO_CORES)
        assert (len(answer.parts), answer.verdict) == (1, PORTABILITY_SAME_FILES)

    def test_a_file_no_rule_claims_is_stated_and_keeps_the_pair_unestablished(self):
        source = _placement(_group("Game.srm"), _group("Game.xyz"))
        answer = _answer(source, _placement(_group("Game.srm")), TWO_CORES)
        assert [c.code for c in answer.caveats] == [CAVEAT_SAVE_FORMAT_UNRECORDED]
        assert answer.parts[0].verdict == PORTABILITY_SAME_FILES
        assert answer.verdict == PORTABILITY_UNESTABLISHED

    def test_an_answer_with_no_part_says_so(self):
        answer = _answer(_placement(mode="no-slot"), _placement(_group("Game.srm")), TWO_CORES)
        assert (answer.verdict, [c.code for c in answer.caveats]) == (
            PORTABILITY_UNESTABLISHED,
            [CAVEAT_SAVE_FORMAT_UNRECORDED],
        )


class TestTheBuildARecordWasReadAt:
    def test_another_build_leaves_every_part_unestablished_with_its_groups(self):
        answer = _answer(
            _placement(_group("Game.srm")),
            _placement(_group("Game.srm")),
            TWO_CORES,
            running=lambda entry: "1.0 fffffff" if entry is TARGET else "1.0 abc1234",
        )
        assert [(p.verdict, p.target_group is not None) for p in answer.parts] == [(PORTABILITY_UNESTABLISHED, True)]
        (caveat,) = answer.caveats
        assert caveat.code == CAVEAT_SAVE_FORMAT_BUILD_UNCONFIRMED
        assert dict(caveat.data) == {
            "side": "target",
            "core": "dst",
            "pinned": ("1.0 abc1234",),
            "reason": "other-build",
            "running": "1.0 fffffff",
        }

    def test_a_build_nothing_names_is_unread(self):
        same = _placement(_group("Game.srm"))
        answer = _answer(same, same, TWO_CORES, running=lambda e: None)
        assert [dict(c.data)["reason"] for c in answer.caveats] == ["build-unread", "build-unread"]

    def test_a_release_string_says_it_names_no_commit(self):
        table = _table(
            {"src": _record("libretro", CARD_RULES, names="release"), "dst": _record("libretro", CARD_RULES)}
        )
        answer = _answer(_placement(_group("Game.srm")), _placement(_group("Game.srm")), table)
        assert any("a release string only" in line for line in answer.sources)
        assert answer.verdict == PORTABILITY_SAME_FILES


class TestWhatTheTwoSidesAnswer:
    def test_a_label_no_entry_carries_is_stated_for_its_side(self):
        same = _placement(_group("Game.srm"))
        answer = _answer(same, same, TWO_CORES, labels=("Source", "Nobody"))
        assert (answer.verdict, answer.target) == (PORTABILITY_UNESTABLISHED, None)
        assert [(c.code, dict(c.data)["side"]) for c in answer.caveats] == [(CAVEAT_ENTRY_LABEL_UNKNOWN, "target")]

    def test_a_refused_save_question_is_stated_with_its_code(self):
        refused = Unresolved("standalone-unsupported", "m", {"token": "X"})
        answer = _answer(_placement(_group("Game.srm")), refused, TWO_CORES)
        assert [(c.code, dict(c.data)) for c in answer.caveats] == [
            (CAVEAT_SAVE_ANSWER_UNRESOLVED, {"side": "target", "code": "standalone-unsupported"})
        ]

    def test_an_emulator_no_record_covers_is_stated_for_its_side(self):
        table = _table({"src": _record("libretro", CARD_RULES)})
        answer = _answer(_placement(_group("Game.srm")), _placement(_group("Game.srm")), table)
        assert [(c.code, dict(c.data)["side"]) for c in answer.caveats] == [(CAVEAT_SAVE_FORMAT_UNRECORDED, "target")]
        assert answer.verdict == PORTABILITY_UNESTABLISHED


class TestACaveatNamesTheEmulatorInItsOwnVocabulary:
    """A core by its short name under ``core``, a carded emulator by its token under ``token``, never ``emulator``."""

    def test_a_core_is_named_by_its_short_name(self):
        table = _table({"src": _record("libretro", CARD_RULES)})
        same = _placement(_group("Game.srm"))
        (caveat,) = _answer(same, same, table).caveats
        assert dict(caveat.data) == {"side": "target", "core": "dst"}

    def test_a_recorded_standalone_is_named_by_its_token(self):
        table = _table({"src": _record("libretro", CARD_RULES), "DST": _record("standalone", CARD_RULES)})
        target = _Entry("Target", None, "DST")
        same = _placement(_group("Game.srm"))
        outcome = portability_answer(
            "psx", source="Source", target="Target", entries=(SOURCE, target), ask=lambda entry: same,
            running=lambda entry: None if entry is target else "1.0 abc1234",
            token=lambda entry: entry.emulator, table=table,
        )
        assert [dict(c.data) for c in outcome.caveats] == [
            {"side": "target", "token": "DST", "pinned": ("1.0 abc1234",), "reason": "build-unread"}
        ]

    def test_a_standalone_no_record_covers_is_named_by_its_side_alone(self):
        table = _table({"src": _record("libretro", CARD_RULES)})
        target = _Entry("Target", None, "SOMEONE")
        same = _placement(_group("Game.srm"))
        outcome = portability_answer(
            "psx", source="Source", target="Target", entries=(SOURCE, target), ask=lambda entry: same,
            running=lambda entry: "1.0 abc1234", token=lambda entry: entry.emulator, table=table,
        )
        assert [dict(c.data) for c in outcome.caveats] == [{"side": "target"}]


class TestALabelTwoEntriesCarry:
    def test_the_first_entry_with_the_label_answers(self):
        twin = _Entry("Source", "other_libretro.so", "other_libretro.so")
        same = _placement(_group("Game.srm"))
        outcome = portability_answer(
            "psx", source="Source", target="Target", entries=(SOURCE, twin, TARGET), ask=lambda entry: same,
            running=lambda entry: "1.0 abc1234", token=lambda entry: entry.emulator, table=TWO_CORES,
        )
        assert outcome.source is SOURCE


class TestAPartStatesOnlyWhatItsVerdictCarries:
    GROUP = FileGroup(dir=SAVES, files=("Game.srm",), granularity=GRANULARITY_PER_GAME_FILE, role=ROLE_MEMORY_CARD)

    def test_moves_stand_exactly_on_same_data(self):
        with pytest.raises(ValueError, match="moves stand exactly"):
            SavePart("memory-card", "1", PORTABILITY_SAME_DATA, self.GROUP, self.GROUP)
        with pytest.raises(ValueError, match="moves stand exactly"):
            SavePart("memory-card", "1", PORTABILITY_SAME_FILES, self.GROUP, self.GROUP, moves=(MOVE_NAME,))

    def test_a_memory_outside_the_vocabulary_is_refused(self):
        with pytest.raises(ValueError, match="memory must be one of"):
            SavePart("clock", "1", PORTABILITY_SAME_FILES, self.GROUP, self.GROUP)

    def test_a_conversion_rule_no_vocabulary_states_is_refused(self):
        with pytest.raises(ValueError, match="rule must be one of"):
            Conversion("strip-trailing", 122)


class TestTheQuestionOnAnInstallation:
    def _install(self):
        machine = FixtureMachine(
            {
                "/home/deck/.var/app/net.retrodeck.retrodeck/config/retrodeck/retrodeck.json": json.dumps(
                    {"paths": {"rd_home_path": "/mnt/sd/retrodeck"}}
                )
            },
            dirs=["/home/deck/.var/app/net.retrodeck.retrodeck/config/ROMs", "/mnt/sd/retrodeck/saves"],
        )
        return atlas.detect(home="/home/deck", machine=machine)

    def test_naming_one_entry_twice_is_refused_at_the_call(self):
        (install,) = self._install()
        with pytest.raises(ValueError, match="the same entry"):
            install.save_portability("psx", source="A", target="A")
        every = atlas.EveryInstallation((install,))
        with pytest.raises(ValueError, match="the same entry"):
            every.save_portability("psx", source="A", target="A")

    def test_a_working_folder_that_is_not_absolute_is_refused_at_the_call(self):
        (install,) = self._install()
        with pytest.raises(ValueError):
            install.save_portability("psx", source="A", target="B", cwd="relative")

    def test_the_aggregate_labels_each_installations_own_answer(self):
        installs = self._install()
        (labelled,) = atlas.EveryInstallation(installs).save_portability("psx", source="A", target="B")
        direct = installs[0].save_portability("psx", source="A", target="B")
        assert labelled.installation is installs[0]
        assert atlas.save_portability_contract(labelled.answer) == atlas.save_portability_contract(direct)


# The systems whose consoles read memory cards by slot: every card group a save
# answer states for one of them names the slots that read it (#577 D23).
_SLOTTED_SYSTEMS = {"psx", "ps2", "gc", "dreamcast"}
# Flycast keeps no save-format record yet, so the table below cannot name its
# system; every other core's systems are the ones its record states.
_SYSTEMS_WITHOUT_A_RECORD = {"flycast": ("dreamcast",)}


def _systems_asked(query: dict[str, Any]) -> set[str]:
    """The systems a vector's save question is about: its own, or the systems of the core it names."""
    if "system" in query:
        return {query["system"]}
    core = str(query.get("core_so", "")).removesuffix("_libretro.so")
    record = load_save_formats().emulators.get(core)
    return set(record.systems if record is not None else _SYSTEMS_WITHOUT_A_RECORD.get(core, ()))
_SAVE_QUESTIONS = (
    ("savefile_location", "savefile_query"),
    ("entry_savefile_location", "entry_savefile_query"),
    ("save_portability", "save_portability_query"),
)


def _card_groups(node: object):
    if isinstance(node, dict):
        if {"dir", "files", "granularity", "role", "slots"} <= set(node) and node["role"] == ROLE_MEMORY_CARD:
            yield node
        for value in node.values():
            yield from _card_groups(value)
    elif isinstance(node, list):
        for value in node:
            yield from _card_groups(value)


class TestEverySlotBoundCardStatesItsSlots:
    """A sweep of the vector corpus: no card a save answer states for a slotted console leaves its slots out (#577 D23)."""

    @staticmethod
    def _answers():
        # Each answer as the code gives it now, asked the way the vector asks it,
        # so a resolver or a card that drops a slot fails here, not only once a
        # vector is regenerated.
        from tests.test_machine_vectors import QUESTIONS, fixture_machine, load_vectors

        for param in load_vectors():
            vector: dict[str, Any] = param.values[0]  # pyright: ignore[reportAssignmentType] - one vector per param
            asked = [
                (key, query)
                for key, query in _SAVE_QUESTIONS
                if key in vector["expected"] and _systems_asked(vector["input"][query]) & _SLOTTED_SYSTEMS
            ]
            if not asked:
                continue
            installs = atlas.detect(vector["input"]["home"], fixture_machine(vector["input"]))
            for key, query in asked:
                yield vector["name"], QUESTIONS[key][1](installs, vector["input"][query], vector["name"])

    def _groups(self):
        for name, answer in self._answers():
            for group in _card_groups(answer):
                yield name, group

    def test_every_card_names_its_slots(self):
        # A list names the slots, null says they are not established (#577
        # D24); only an empty list, "this emulator keeps no slots", is wrong
        # on a console that reads its cards by slot.
        unslotted = [f"{name}: {group['dir']} {group['files']}" for name, group in self._groups() if group["slots"] == []]
        assert unslotted == []

    def test_the_sweep_holds_cards(self):
        assert sum(1 for _ in self._groups()) >= 100


class TestAPcsx2SlotWord:
    """PCSX2's ``[MemoryCards]`` filename keys name the console's ports and a multitap's slots (#577 D23)."""

    def test_the_ports_and_the_multitap_slots_are_named_as_pcsx2_names_them(self):
        from atlas.installations import _pcsx2_slot_word  # pyright: ignore[reportPrivateUsage]

        keys = ("Slot1_Filename", "Slot2_Filename", "Multitap1_Slot2_Filename", "Multitap2_Slot4_Filename")
        assert [_pcsx2_slot_word(key) for key in keys] == ["1", "2", "1B", "2D"]
