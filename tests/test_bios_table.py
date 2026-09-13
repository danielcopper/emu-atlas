"""The shared recognition table: one class, two tables, and what each states about itself.

DuckStation and its fork SwanStation recognise a BIOS the same way and by two
different tables, so the loader has to carry what differs between them as data
— the row shape, the hash scope, what an image no row holds does — and refuse a
table that states something nothing here reads. These hold that, and hold the
packaged SwanStation table against the source it was generated from.
"""

from __future__ import annotations

import json

import pytest

from atlas import bios_table, duckstation
from atlas.bios_table import (
    UNKNOWN_BOOTED,
    UNKNOWN_REFUSED,
    BiosCandidate,
    load_bios_table,
    packaged_bios_table,
)
from atlas.core_firmware import lookup_core_firmware

SWANSTATION_TABLE = "swanstation_bios.json"
ROW = {"name": "x", "region": "pal", "md5": "a"}
BASE = {"sizes": {"ps1": 1}, "images": [ROW], "_meta": {"revision": "r"}}


def _text(**overrides: object) -> str:
    return json.dumps({**BASE, **overrides})


class TestThePackagedSwanStationTable:
    """27 rows read out of one initialiser, and what they are beside the other table."""

    def _table(self):
        return packaged_bios_table(SWANSTATION_TABLE)

    def test_it_holds_the_rows_the_source_declares(self):
        # The count is upstream's own array size, which the generator holds
        # the parse against — so a row a regex silently missed fails there
        # rather than shipping a table that recognises one image fewer.
        assert len(self._table().images) == 27
        assert self._table().meta["revision"] == "4d309c0"

    def test_every_row_is_one_image(self):
        md5s = [image.md5 for image in self._table().images]
        assert len(set(md5s)) == len(md5s)
        assert {image.region for image in self._table().images} <= {
            "ntsc-j",
            "ntsc-u",
            "pal",
            "any",
        }

    def test_it_states_its_own_scope_and_its_refusal(self):
        # Both are why this table cannot be the other one: the core reads a
        # fixed 512 KiB out of every candidate, and its search hands back
        # nothing for an image no row holds.
        assert self._table().hash_scope == 524288
        assert self._table().unknown == UNKNOWN_REFUSED

    def test_it_pins_no_priority_and_no_patch_variant(self):
        # Columns the fork's older table has not got. Every row therefore
        # ranks alike, which is what its search does: the first region-valid
        # file the directory hands over wins.
        assert {image.priority for image in self._table().images} == {0}
        assert {image.fast_boot_patch for image in self._table().images} == {""}

    def test_the_rows_it_shares_with_duckstations_table_and_the_three_it_does_not(self):
        # Counting rule: each row's md5 looked up in the other packaged
        # table's images by md5, and the region compared where it is found.
        # The three it does not share are Auto rows, which is what hashing
        # only the first 512 KiB of a longer image produces.
        other = {image.md5: image.region for image in duckstation.bios_table().images}
        shared = [i for i in self._table().images if other.get(i.md5) == i.region]
        contradicted = [i for i in self._table().images if i.md5 in other and other[i.md5] != i.region]
        absent = [i for i in self._table().images if i.md5 not in other]
        assert (len(shared), len(contradicted), len(absent)) == (24, 0, 3)
        assert {image.region for image in absent} == {"any"}

    def test_the_entry_that_names_it_states_what_it_states(self):
        card = lookup_core_firmware("swanstation_libretro.so")
        assert card is not None
        assert card.content_route is not None
        assert card.content_route.table == SWANSTATION_TABLE
        assert card.content_route.hash_scope == self._table().hash_scope
        assert card.content_route.unknown == self._table().unknown


class TestTheLoaderReadsWhatEachTableStates:
    def test_a_table_that_states_no_scope_hashes_the_whole_file(self):
        table = load_bios_table(_text(), source="t")
        assert table.hash_scope is None
        assert table.unknown == UNKNOWN_BOOTED

    def test_a_scope_of_no_bytes_is_refused(self):
        text = _text(hash_scope=0)
        with pytest.raises(ValueError, match="hash_scope"):
            load_bios_table(text, source="t")

    def test_an_unknown_policy_outside_the_vocabulary_is_refused(self):
        text = _text(unknown="ignored")
        with pytest.raises(ValueError, match="unknown"):
            load_bios_table(text, source="t")

    def test_rows_that_disagree_about_their_columns_are_refused(self):
        # One table, one row shape: a row missing a column its neighbours
        # carry is a parse that went wrong, not a table with a gap.
        text = _text(images=[{**ROW, "priority": 1, "fast_boot_patch": "type1"}, ROW])
        with pytest.raises(ValueError, match="one row shape"):
            load_bios_table(text, source="t")

    def test_a_column_nothing_here_reads_is_refused(self):
        text = _text(images=[{**ROW, "patch_compatible": True}])
        with pytest.raises(ValueError, match="nothing here reads"):
            load_bios_table(text, source="t")

    def test_the_source_names_the_table_in_every_refusal(self):
        text = json.dumps({"sizes": {"ps1": 1}, "images": []})
        with pytest.raises(ValueError, match="a_table: images"):
            load_bios_table(text, source="a_table")

    def test_a_table_whose_emulator_recognises_openbios_needs_that_block(self):
        text = _text()
        with pytest.raises(ValueError, match="openbios"):
            load_bios_table(text, source="t", openbios=True)


class TestWhatTheUnknownPolicyDoesToAPick:
    """The one thing the policy decides: whether a file no row holds can be booted."""

    def test_a_boots_table_ranks_an_unrecognised_file_and_picks_it(self):
        table = load_bios_table(_text(), source="t")
        pick = table.pick((BiosCandidate(path="/x", image=None, size=1),), "ntsc-u")
        assert pick is not None
        assert pick.chosen.path == "/x"

    def test_a_refusing_table_picks_no_unrecognised_file_at_all(self):
        table = load_bios_table(_text(unknown=UNKNOWN_REFUSED), source="t")
        assert table.pick((BiosCandidate(path="/x", image=None, size=1),), "ntsc-u") is None

    def test_a_refusing_table_still_ranks_a_file_whose_bytes_did_not_come_back(self):
        # A read failure is atlas's, not the table's: the core hashes that file
        # and may well boot it, so dropping it would state an absence nobody
        # established.
        table = load_bios_table(_text(unknown=UNKNOWN_REFUSED), source="t")
        unread = BiosCandidate(path="/x", image=None, size=1, unreadable=True)
        pick = table.pick((unread,), "ntsc-u")
        assert pick is not None
        assert pick.chosen.unreadable

    def test_the_vocabulary_is_the_two_words_the_tables_state(self):
        assert bios_table.UNKNOWN_POLICIES == (UNKNOWN_BOOTED, UNKNOWN_REFUSED)
