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

import pytest

import atlas
from atlas.machine import FixtureMachine
from atlas.placement import (
    FILE_SET_DECLARED,
    Caveat,
    GRANULARITY_PER_GAME_FILE,
    GRANULARITY_PER_GAME_FILES,
    GRANULARITY_SHARED_CARD,
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


def _rule(memory: str, slot: str | None, fmt: str, names: list[str], **extra: object) -> dict[str, object]:
    return {"memory": memory, "slot": slot, "format": fmt, "names": names, "citation": "[V] x.c:1 at abc1234", **extra}


def _record(kind: str, parts: list[dict[str, object]], *, names: str = "commit", ignore=None) -> dict[str, object]:
    system: dict[str, object] = {"parts": parts}
    if ignore:
        system["ignore"] = ignore
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
                "formats": {"card": "a card", "folder": "a folder card", "other": "another layout"},
                "conversions": list(conversions),
                "emulators": emulators,
                "retroarch": {"compressed": "[V] save.c:644-657 at a79435a"},
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
