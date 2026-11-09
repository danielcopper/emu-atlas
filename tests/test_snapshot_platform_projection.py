"""The packaged platform table against its committed projection snapshot (#784 D33).

The vectors pin the pairs they ask; this pins every key of every source: a
regeneration that moves, adds or drops a mapping fails here, naming the keys,
until ``scripts/snapshot_platform_projection.py`` renews the snapshot in the
same change.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from scripts import snapshot_platform_projection as snapshot_script
from tests.platform_projection import single_key_entry

RECORDED = json.loads(snapshot_script.SNAPSHOT_PATH.read_text(encoding="utf-8"))


def moved_keys(recorded: dict[str, Any], current: dict[str, Any]) -> list[str]:
    """Every key whose answer differs between two snapshots, or that only one of them holds, sorted."""
    old, new = recorded["answers"], current["answers"]
    return sorted(name for name in old.keys() | new.keys() if old.get(name) != new.get(name))


def test_every_single_key_answer_is_the_recorded_one():
    current = snapshot_script.snapshot()
    moved = moved_keys(RECORDED, current)
    assert not moved, (
        f"{len(moved)} key(s) answer otherwise than tests/data/platform_projection.json records — renew it with "
        f"`mise run snapshot-platforms` if each move is meant: {', '.join(moved[:40])}"
    )
    assert current["table_version"] == RECORDED["table_version"], "the table version is recorded once, in the snapshot"


def test_the_committed_file_is_what_the_script_writes():
    # Byte for byte, so the snapshot stays one answer per line and a moved key is one changed line in a diff.
    assert snapshot_script.serialize(RECORDED) == snapshot_script.SNAPSHOT_PATH.read_text(encoding="utf-8")


class TestTheComparison:
    def test_a_moved_key_an_added_key_and_a_dropped_key_are_named(self):
        recorded = {"answers": {"romm/a": {"status": "known"}, "romm/b": {"status": "known"}}}
        current = {"answers": {"romm/a": {"status": "unknown"}, "romm/c": {"status": "known"}}}
        assert moved_keys(recorded, current) == ["romm/a", "romm/b", "romm/c"]

    def test_unchanged_answers_name_nothing(self):
        recorded = {"answers": {"romm/a": {"status": "known", "keys": {"esde": {"a": "exactMatch"}}}}}
        assert moved_keys(recorded, json.loads(json.dumps(recorded))) == []


class TestTheEntry:
    PAIR = {"source": "romm", "key": "a", "version": None, "status": "known"}
    SOURCES = [
        {"source": "esde", "status": "keys", "keys": [{"key": "a", "relation": "exactMatch"}]},
        {"source": "romm", "status": "none", "keys": []},
    ]

    def _projection(self, **changes):
        projection = {
            "table_version": 1,
            "pairs": [{**self.PAIR, "sources": self.SOURCES}],
            "intersection_status": "agreed",
            "intersection": self.SOURCES,
            "conflict": [],
            "caveats": [],
        }
        return {**projection, **changes}

    def test_a_source_without_a_key_is_left_out_and_nothing_else_is(self):
        assert single_key_entry(self._projection()) == {"status": "known", "keys": {"esde": {"a": "exactMatch"}}}

    def test_an_intersection_other_than_the_pairs_own_is_refused(self):
        with pytest.raises(AssertionError, match="one pair's intersection is its own"):
            single_key_entry(self._projection(intersection=[]))
