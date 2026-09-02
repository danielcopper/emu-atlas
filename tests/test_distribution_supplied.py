"""The distribution copy list: packaged shape, and a loader that never coerces."""

import json

import pytest

from atlas.distribution_supplied import (
    SUPPLIED_KINDS,
    load_distribution_supplied,
    lookup_distribution_supplied,
)

ENTRY = {
    "kind": "tree",
    "source": "dolphin-emu",
    "destination": "dolphin-emu",
    "purpose": "Dolphin's Sys tree",
    "citation": "[V-script] components/retroarch/component_prepare.sh:61",
}


def _table(entries, **distribution):
    return json.dumps(
        {
            "schema": 1,
            "version": "1",
            "reviewed": "2026-09-02",
            "distributions": {
                "retrodeck": {
                    "version": "0.10.9b",
                    "source_root": "/app/retrodeck/components/retroarch/rd_extras",
                    "source_root_citation": "component_functions.sh:7",
                    "destination_root": "the firmware root",
                    "entries": entries,
                    **distribution,
                }
            },
        }
    )


class TestThePackagedCard:
    def test_retrodeck_is_pinned_and_populated(self):
        card = lookup_distribution_supplied("retrodeck")
        assert card is not None
        assert card.version == "0.10.9b"
        assert card.card_version == "1"
        assert card.source_root == "/app/retrodeck/components/retroarch/rd_extras"
        assert card.entries

    def test_every_entry_states_a_known_kind(self):
        card = lookup_distribution_supplied("retrodeck")
        assert card is not None
        assert {entry.kind for entry in card.entries} <= set(SUPPLIED_KINDS)

    def test_the_sighting_that_opened_the_issue_resolves(self):
        # dolphin_libretro declares dolphin-emu/Sys/codehandler.bin, RetroDECK
        # copies the whole dolphin-emu tree, and the source keeps its own name.
        card = lookup_distribution_supplied("retrodeck")
        assert card is not None
        assert card.source_of("dolphin-emu/Sys/codehandler.bin") == "dolphin-emu/Sys/codehandler.bin"

    def test_the_renamed_trees_keep_their_shipped_spelling(self):
        # The two copies that do not land under their own name: a resolver
        # assuming source == destination would look in a directory RetroDECK
        # does not have.
        card = lookup_distribution_supplied("retrodeck")
        assert card is not None
        assert card.source_of("Databases/msxromdb.xml") == "MSX/Databases/msxromdb.xml"
        assert card.source_of("Machines/MSX2/msx2.rom") == "MSX/Machines/MSX2/msx2.rom"
        assert card.source_of("capsimg.so") == "Amiga/capsimg.so"

    def test_a_name_no_line_copies_is_not_covered(self):
        card = lookup_distribution_supplied("retrodeck")
        assert card is not None
        assert card.source_of("scph5501.bin") is None

    def test_a_tree_answers_below_itself_and_not_for_itself(self):
        # The destination of a tree entry is the directory, never a file in it.
        card = lookup_distribution_supplied("retrodeck")
        assert card is not None
        assert card.source_of("dolphin-emu") is None
        assert card.source_of("dolphin-emuX/f") is None

    def test_an_unknown_distribution_is_none(self):
        assert lookup_distribution_supplied("emudeck") is None
        assert lookup_distribution_supplied(None) is None


class TestTheLoaderRefusesWhatItCannotState:
    """Every table is built before the `with`, so the block holds only the call under test."""

    def test_an_unsupported_schema_fails(self):
        table = json.dumps({"schema": 99})
        with pytest.raises(ValueError, match="schema"):
            load_distribution_supplied(table)

    def test_a_kind_outside_the_vocabulary_fails(self):
        table = _table([{**ENTRY, "kind": "symlink"}])
        with pytest.raises(ValueError, match="kind"):
            load_distribution_supplied(table)

    def test_an_absolute_source_fails(self):
        table = _table([{**ENTRY, "source": "/app/dolphin-emu"}])
        with pytest.raises(ValueError, match="relative"):
            load_distribution_supplied(table)

    def test_a_parent_escape_in_the_destination_fails(self):
        table = _table([{**ENTRY, "destination": "../outside"}])
        with pytest.raises(ValueError, match="relative"):
            load_distribution_supplied(table)

    def test_a_repeated_destination_fails(self):
        table = _table([ENTRY, dict(ENTRY)])
        with pytest.raises(ValueError, match="same destination"):
            load_distribution_supplied(table)

    def test_a_destination_inside_another_tree_fails(self):
        # One placed file, one source: a nested destination would give it two.
        nested = {**ENTRY, "kind": "file", "source": "sys.bin", "destination": "dolphin-emu/Sys/x"}
        table = _table([ENTRY, nested])
        with pytest.raises(ValueError, match="two sources"):
            load_distribution_supplied(table)

    def test_an_entry_with_extra_keys_fails(self):
        table = _table([{**ENTRY, "note": "?"}])
        with pytest.raises(ValueError, match="exactly"):
            load_distribution_supplied(table)

    def test_a_distribution_with_no_entries_fails(self):
        table = _table([])
        with pytest.raises(ValueError, match="non-empty list"):
            load_distribution_supplied(table)

    def test_a_relative_source_root_fails(self):
        table = _table([ENTRY], source_root="retroarch/rd_extras")
        with pytest.raises(ValueError, match="source_root"):
            load_distribution_supplied(table)

    def test_a_distribution_missing_its_provenance_fails(self):
        raw = json.loads(_table([ENTRY]))
        del raw["distributions"]["retrodeck"]["source_root_citation"]
        table = json.dumps(raw)
        with pytest.raises(ValueError, match="exactly"):
            load_distribution_supplied(table)

    def test_a_card_without_its_own_version_fails(self):
        raw = json.loads(_table([ENTRY]))
        del raw["version"]
        table = json.dumps(raw)
        with pytest.raises(ValueError, match="version"):
            load_distribution_supplied(table)
