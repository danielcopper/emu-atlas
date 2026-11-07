"""The platform table's release check (issue #784 D17): the packaged table passes, and each rule bites."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts import check_platform_table as release
from tests.test_platforms import excluded_doc, kind_override, overrides_doc, fixture_table

DATA = Path(__file__).resolve().parents[1] / "atlas" / "data"
# Every fixture row needs a kind before the fixture passes: the unclassified rows get one.
KINDS = (kind_override("esde/atarijaguar", "console"), kind_override("romm/pico", "fantasy-console"))


def _check(table=None, overrides=None, excluded=None) -> list[str]:
    return release.check(json.dumps(table or fixture_table()), overrides or overrides_doc(*KINDS), excluded or excluded_doc())


def test_the_packaged_table_passes():
    assert release.check(
        (DATA / "platform_table.json").read_text(encoding="utf-8"),
        (DATA / "platform_overrides.json").read_text(encoding="utf-8"),
        (DATA / "platform_excluded.json").read_text(encoding="utf-8"),
    ) == []


def test_the_fixture_passes():
    assert _check() == []


def test_a_key_neither_placed_nor_excluded_is_a_problem():
    table = fixture_table()
    table["keys"]["romm"]["dc"] = "c"
    assert _check(table) == ["romm/dc: neither placed on a row nor excluded with a reason"]


def test_an_excluded_key_passes():
    table = fixture_table()
    table["keys"]["romm"]["dc"] = "c"
    assert _check(table, excluded=excluded_doc({"source": "romm", "key": "dc", "reason": "no system"})) == []


def test_a_row_without_a_kind_is_a_problem():
    assert _check(overrides=overrides_doc(KINDS[0])) == ["a row carries no kind (romm/pico)"]


def test_a_generated_relation_that_disagrees_with_the_row_count_is_refused():
    # The loader refuses such a table, so the release check never sees one.
    table = fixture_table()
    table["mappings"][0]["relation"] = "broadMatch"
    with pytest.raises(ValueError, match="esde/genesis: on 1 row.s. the relation is exactMatch, the file states broadMatch"):
        _check(table)


def test_catalogues_stating_different_kinds_for_a_row_are_a_problem_until_an_override_decides():
    table = fixture_table()
    table["kinds"]["romm"] = {"gb": {"kind": "console", "citation": "c"}}
    assert _check(table) == [
        "catalogues state different kinds (console, handheld) and no override decides (esde/gb, batocera/gb, romm/gb)"
    ]
    assert _check(table, overrides_doc(*KINDS, kind_override("esde/gb", "handheld"))) == []


def test_main_passes_the_packaged_table_silently(capsys):
    assert release.main() == 0
    assert capsys.readouterr().out == ""


def test_main_prints_one_line_per_problem_and_fails(tmp_path, monkeypatch, capsys):
    table = fixture_table()
    table["keys"]["romm"]["dc"] = "c"
    (tmp_path / "platform_table.json").write_text(json.dumps(table), encoding="utf-8")
    (tmp_path / "platform_overrides.json").write_text(overrides_doc(*KINDS), encoding="utf-8")
    (tmp_path / "platform_excluded.json").write_text(excluded_doc(), encoding="utf-8")
    monkeypatch.setattr(release, "DATA", tmp_path)
    assert release.main() == 1
    assert capsys.readouterr().out == "romm/dc: neither placed on a row nor excluded with a reason\n"
