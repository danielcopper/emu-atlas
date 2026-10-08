"""The content-tree wiring table: packaged shape, and a loader that never coerces."""

import json

import pytest

from atlas.content_tree_wiring import (
    WIRING_BASES,
    WIRING_FAMILIES,
    load_content_tree_wiring,
    lookup_content_tree_wiring,
)


def _table(rows, *more_row_sets):
    return json.dumps(
        {
            "schema": 2,
            "arrangements": {
                "retrodeck": [{"version": "0.10.9b", "rows": rows}, *more_row_sets]
            },
        }
    )


ROW = {
    "family": "texture_packs",
    "hub": "Cemu/graphicPacks",
    "base": "xdg-data",
    "path": "Cemu/graphicPacks",
    "source": "[V-script] components/cemu/component_prepare.sh:20",
}


READ_AT = ("0.10.9b", "0.10.10b")

# The one pair 0.10.10b added to what 0.10.9b promised: the RetroArch PPSSPP
# core's plugin tree, wired on prepare and on the upgrade into that release.
PLUGINS = ("mods", "retroarch-core/PPSSPP/PLUGINS", "xdg-config", "retroarch/saves/PPSSPP/PSP/PLUGINS")


def _pairs(version):
    wiring = lookup_content_tree_wiring("retrodeck", version)
    assert wiring is not None
    return {(row.family, row.hub, row.base, row.path) for row in wiring.rows}


class TestThePackagedTable:
    @pytest.mark.parametrize("version", READ_AT)
    def test_retrodeck_is_pinned_and_populated(self, version):
        wiring = lookup_content_tree_wiring("retrodeck", version)
        assert wiring is not None
        assert wiring.version == version
        # Both content-tree families are wired — the very reason issue #104
        # names them together.
        assert {row.family for row in wiring.rows} == set(WIRING_FAMILIES)

    def test_the_later_release_promises_the_earlier_pairs_and_one_more(self):
        assert _pairs("0.10.10b") == _pairs("0.10.9b") | {PLUGINS}
        assert PLUGINS not in _pairs("0.10.9b")

    @pytest.mark.parametrize("version", READ_AT)
    def test_every_row_hangs_off_a_known_base(self, version):
        wiring = lookup_content_tree_wiring("retrodeck", version)
        assert wiring is not None
        assert {row.base for row in wiring.rows} <= set(WIRING_BASES)

    def test_an_unknown_arrangement_is_none(self):
        assert lookup_content_tree_wiring("emudeck", "0.10.10b") is None

    def test_a_version_never_read_is_none(self):
        assert lookup_content_tree_wiring("retrodeck", "0.11.0b") is None


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

    @pytest.mark.parametrize("schema", [1, 3])
    def test_an_unknown_schema_fails(self, schema):
        table = json.dumps({"schema": schema, "arrangements": {}})
        with pytest.raises(ValueError, match="schema"):
            load_content_tree_wiring(table)

    def test_a_version_with_two_row_sets_fails(self):
        table = _table([ROW], {"version": "0.10.9b", "rows": [ROW]})
        with pytest.raises(ValueError, match="two row sets"):
            load_content_tree_wiring(table)

    def test_an_arrangement_that_is_not_a_list_of_row_sets_fails(self):
        table = json.dumps(
            {"schema": 2, "arrangements": {"retrodeck": {"version": "0.10.9b", "rows": [ROW]}}}
        )
        with pytest.raises(ValueError, match="list of row sets"):
            load_content_tree_wiring(table)

    def test_each_row_set_is_keyed_by_its_version(self):
        table = _table([ROW], {"version": "0.10.10b", "rows": [ROW]})
        assert set(load_content_tree_wiring(table)["retrodeck"]) == {"0.10.9b", "0.10.10b"}
