"""The content-tree wiring table: packaged shape, and a loader that never coerces."""

import json

import pytest

from atlas.content_tree_wiring import (
    WIRING_BASES,
    WIRING_FAMILIES,
    load_content_tree_wiring,
    lookup_content_tree_wiring,
)


def _table(rows):
    return json.dumps(
        {
            "schema": 1,
            "arrangements": {"retrodeck": {"version": "0.10.9b", "rows": rows}},
        }
    )


ROW = {
    "family": "texture_packs",
    "hub": "Cemu/graphicPacks",
    "base": "xdg-data",
    "path": "Cemu/graphicPacks",
    "source": "[V-script] components/cemu/component_prepare.sh:20",
}


class TestThePackagedTable:
    def test_retrodeck_is_pinned_and_populated(self):
        wiring = lookup_content_tree_wiring("retrodeck")
        assert wiring is not None
        assert wiring.version == "0.10.9b"
        # Both content-tree families are wired — the very reason issue #104
        # names them together.
        assert {row.family for row in wiring.rows} == set(WIRING_FAMILIES)

    def test_every_row_hangs_off_a_known_base(self):
        wiring = lookup_content_tree_wiring("retrodeck")
        assert wiring is not None
        assert {row.base for row in wiring.rows} <= set(WIRING_BASES)

    def test_an_unknown_arrangement_is_none(self):
        assert lookup_content_tree_wiring("emudeck") is None


class TestTheLoaderRefusesWhatItCannotState:
    def test_a_family_outside_the_vocabulary_fails(self):
        table = _table([{**ROW, "family": "cheats"}])
        with pytest.raises(ValueError, match="family"):
            load_content_tree_wiring(table)

    def test_a_base_outside_the_vocabulary_fails(self):
        table = _table([{**ROW, "base": "home"}])
        with pytest.raises(ValueError, match="base"):
            load_content_tree_wiring(table)

    def test_an_absolute_hub_path_fails(self):
        table = _table([{**ROW, "hub": "/mnt/sd/hub"}])
        with pytest.raises(ValueError, match="relative"):
            load_content_tree_wiring(table)

    def test_a_parent_escape_fails(self):
        table = _table([{**ROW, "path": "../outside"}])
        with pytest.raises(ValueError, match="relative"):
            load_content_tree_wiring(table)

    def test_a_repeated_pair_fails(self):
        table = _table([ROW, dict(ROW)])
        with pytest.raises(ValueError, match="repeat"):
            load_content_tree_wiring(table)

    def test_a_row_with_extra_keys_fails(self):
        table = _table([{**ROW, "note": "?"}])
        with pytest.raises(ValueError, match="exactly"):
            load_content_tree_wiring(table)

    def test_a_row_without_a_source_fails(self):
        table = _table([{k: v for k, v in ROW.items() if k != "source"}])
        with pytest.raises(ValueError, match="exactly"):
            load_content_tree_wiring(table)

    def test_an_unknown_schema_fails(self):
        with pytest.raises(ValueError, match="schema"):
            load_content_tree_wiring('{"schema": 2, "arrangements": {}}')
