"""Tests for atlas.platforms — the table loader, the overrides, and the pure lookup.

Three things are held down: the loader refuses a table it cannot stand behind
(every refusal is an answer a question could otherwise be given out of a table
that does not add up), the overrides apply exactly as documented and anchor on
keys the sources have at their pins (#784 D2), and the lookup states keys,
relations, none, unknown and conflict as the decisions describe (D7–D9, D11,
D14) without a row name ever reaching an answer (D4). The machine-qualified
half lives on the handles and is held by the vectors; the value boundary,
which refuses instead of answering, is held here.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

import atlas
from atlas.cli import run
from atlas.contract import platform_systems_contract, platform_translation_contract, system_platforms_contract
from atlas.machine import FixtureMachine
from atlas.placement import Caveat
from atlas.platforms import (
    BROAD_MATCH,
    CAVEAT_PLATFORM_EXCLUDED,
    CAVEAT_PLATFORM_PARTS_UNESTABLISHED,
    CAVEAT_PLATFORM_SOURCE_UNKNOWN,
    CAVEAT_PLATFORM_SOURCE_VERSION_DIFFERS,
    CAVEAT_PLATFORM_UNMAPPED,
    CAVEAT_PLATFORM_VERSION_UNAPPLIED,
    EXACT_MATCH,
    INTERSECTION_AGREED,
    INTERSECTION_CONFLICT,
    INTERSECTION_UNKNOWN,
    KEYS_FOUND,
    KEYS_NONE,
    NARROW_MATCH,
    PAIR_KNOWN,
    PAIR_UNKNOWN,
    RELATED_MATCH,
    UNKNOWN_PAIR_CODES,
    PlatformKey,
    PlatformPairAnswer,
    PlatformSourceKeys,
    PlatformTable,
    PlatformTranslation,
    exact_platform_tags,
    known_platform_vocabularies,
    load_platform_table,
    names_one_system,
    platform_table,
    systems_named,
    translate,
)

# --- a small table to load ------------------------------------------------------------

SOURCES = {
    "esde": [{"upstream": "es-de", "version": "v1", "revision": "r1"}],
    "batocera": [{"upstream": "batocera", "version": "b1", "revision": "r2"}],
    "romm": [{"upstream": "romm", "version": "m1", "revision": "r3"}],
}
# The columns the questions read by name (ES-DE's platform ids, every folder
# vocabulary) must be in any table the loader takes; here each holds one key on
# a row of its own, so the tests above stay about the three sources they ask.
REQUIRED = {
    source: [{"upstream": source, "version": "x1", "revision": "r9"}]
    for source in ("esde-platform", "retrodeck", "emudeck", "romm-folder", "knulli", "rocknix")
}


def _mapping(source: str, key: str, row: str, relation: str = EXACT_MATCH, **extra: Any) -> dict[str, Any]:
    return {
        "source": source,
        "key": key,
        "row": row,
        "relation": relation,
        "via": "own-key",
        "citation": f"{source}:file:1",
        "valid_from": {**SOURCES, **REQUIRED}[source][0]["version"],
        "valid_to": None,
        **extra,
    }


def fixture_table(**changes: Any) -> dict[str, Any]:
    table: dict[str, Any] = {
        "schema": 1,
        "spec": "spec",
        "table_version": 3,
        "sources": {**SOURCES, **REQUIRED},
        "rows": [
            "esde:genesis",
            "esde:megadrive",
            "esde:gb",
            "batocera:jaguar",
            "esde:atarijaguar",
            "romm:pico",
            "esde:n64",
        ],
        "kinds": {
            "batocera": {"jaguar": {"kind": "console", "citation": "c"}, "megadrive": {"kind": "console", "citation": "c"}, "gb": {"kind": "handheld", "citation": "c"}},
            "rocknix": {"n64": {"kind": "console", "citation": "c"}},
        },
        "keys": {
            "esde": {"genesis": "c", "megadrive": "c", "gb": "c", "atarijaguar": "c"},
            "batocera": {"megadrive": "c", "jaguar": "c", "gb": "c"},
            "romm": {"genesis": "c", "pico": "c", "gb": "c"},
            **{source: {"n64": "c"} for source in REQUIRED},
        },
        "mappings": [
            _mapping("esde", "genesis", "esde:genesis"),
            _mapping("esde", "megadrive", "esde:megadrive"),
            _mapping("esde", "gb", "esde:gb"),
            _mapping("esde", "atarijaguar", "esde:atarijaguar"),
            _mapping("batocera", "megadrive", "esde:genesis", BROAD_MATCH),
            _mapping("batocera", "megadrive", "esde:megadrive", BROAD_MATCH),
            _mapping("batocera", "jaguar", "batocera:jaguar"),
            _mapping("batocera", "gb", "esde:gb"),
            _mapping("romm", "genesis", "esde:genesis", BROAD_MATCH),
            _mapping("romm", "genesis", "esde:megadrive", BROAD_MATCH),
            _mapping("romm", "pico", "romm:pico"),
            _mapping("romm", "gb", "esde:gb"),
            *(_mapping(source, "n64", "esde:n64") for source in REQUIRED),
        ],
    }
    table.update(changes)
    return table


def overrides_doc(*entries: dict[str, Any]) -> str:
    return json.dumps({"schema": 1, "spec": "spec", "overrides": list(entries)})


def excluded_doc(*entries: dict[str, Any]) -> str:
    return json.dumps({"schema": 1, "spec": "spec", "excluded": list(entries)})


def _load(table: dict[str, Any] | None = None, overrides: str | None = None, excluded: str | None = None):
    return load_platform_table(
        json.dumps(table or fixture_table()), overrides or overrides_doc(), excluded or excluded_doc()
    )


def _merge(merge: str, into: str, **extra: Any) -> dict[str, Any]:
    a, b = merge.split("/"), into.split("/")
    return {
        "merge": {"source": a[0], "key": a[1]},
        "into": {"source": b[0], "key": b[1]},
        "reason": "one system",
        "citation": "both lines",
        **extra,
    }


def _map(key: str, *rows: str, relation: str | None = None, **extra: Any) -> dict[str, Any]:
    source, name = key.split("/")
    return {
        "source": source,
        "key": name,
        "rows": [{"source": r.split("/")[0], "key": r.split("/")[1]} for r in rows],
        "relation": relation,
        "reason": "judgement",
        "citation": "a line",
        **extra,
    }


def kind_override(anchor: str, kind: str, subset_of: str | None = None) -> dict[str, Any]:
    source, key = anchor.split("/")
    return {
        "row_of": {"source": source, "key": key},
        "kind": kind,
        "subset_of": None if subset_of is None else dict(zip(("source", "key"), subset_of.split("/"))),
        "reason": "stated",
        "citation": "a line",
    }


def _citations(translation: PlatformTranslation, source: str, key: str) -> tuple[str, ...]:
    return next(k.citations for s in translation.pairs[0].sources if s.source == source for k in s.keys if k.key == key)


def _keys(translation: PlatformTranslation, source: str, part: str = "pair") -> list[tuple[str, str]]:
    keys = translation.pairs[0].sources if part == "pair" else translation.intersection
    return [(k.key, k.relation) for s in keys if s.source == source for k in s.keys]


class TestTheLoaderRefusesATableItCannotStandBehind:
    def test_the_fixture_loads(self):
        table = _load()
        assert table.table_version == 3
        assert dict(table.sources) == {
            "esde": (("es-de", "v1"),),
            "batocera": (("batocera", "b1"),),
            "romm": (("romm", "m1"),),
            **{s: ((s, "x1"),) for s in REQUIRED},
        }

    def test_an_unknown_schema_is_refused(self):
        table = fixture_table(schema=9)
        with pytest.raises(ValueError, match="unsupported schema 9"):
            _load(table)

    def test_a_placed_key_its_source_does_not_list_is_refused(self):
        table = fixture_table()
        table["mappings"].append(_mapping("romm", "dc", "esde:gb"))
        with pytest.raises(ValueError, match="not listed under 'keys'"):
            _load(table)

    def test_a_mapping_onto_no_row_is_refused(self):
        table = fixture_table()
        table["mappings"].append(_mapping("romm", "gb", "esde:nowhere"))
        with pytest.raises(ValueError, match="is not a row"):
            _load(table)

    def test_a_relation_outside_the_skos_four_is_refused(self):
        table = fixture_table()
        table["mappings"][0]["relation"] = "closeMatch"
        with pytest.raises(ValueError, match="relation 'closeMatch'"):
            _load(table)

    def test_an_uncited_mapping_is_refused(self):
        table = fixture_table()
        table["mappings"][0]["citation"] = ""
        with pytest.raises(ValueError, match="citation"):
            _load(table)

    def test_an_entry_not_open_ended_at_its_pin_is_refused(self):
        table = fixture_table()
        table["mappings"][0]["valid_to"] = "v2"
        with pytest.raises(ValueError, match="open-ended"):
            _load(table)

    def test_a_kind_outside_the_vocabulary_is_refused(self):
        table = fixture_table()
        table["kinds"]["batocera"]["gb"] = {"kind": "gadget", "citation": "c"}
        with pytest.raises(ValueError, match="kind 'gadget'"):
            _load(table)

    def test_a_stated_kind_without_its_citation_is_refused(self):
        table = fixture_table()
        table["kinds"]["batocera"]["gb"] = {"kind": "handheld", "citation": ""}
        with pytest.raises(ValueError, match="citation"):
            _load(table)

    @pytest.mark.parametrize("column", ["esde-platform", "rocknix"])
    def test_a_table_without_a_column_the_questions_read_is_refused(self, column):
        table = fixture_table()
        del table["sources"][column], table["keys"][column]
        table["kinds"].pop(column, None)
        table["mappings"] = [m for m in table["mappings"] if m["source"] != column]
        with pytest.raises(ValueError, match=f"the questions read the column.s. {column}, which it lacks"):
            _load(table)

    def test_folder_is_not_a_source_name(self):
        sources = {**SOURCES, "folder": [{"upstream": "x", "version": "1", "revision": "r"}]}
        table = fixture_table(sources=sources)
        with pytest.raises(ValueError, match="pseudo-source"):
            _load(table)


class TestTheOverrides:
    def test_a_merge_moves_every_key_of_the_row_and_keeps_a_stated_kind(self):
        table = _load(overrides=overrides_doc(_merge("batocera/jaguar", "esde/atarijaguar")))
        assert translate([("batocera", "jaguar")], table).pairs[0].status == PAIR_KNOWN
        assert _keys(translate([("batocera", "jaguar")], table), "esde") == [("atarijaguar", EXACT_MATCH)]
        assert "batocera:jaguar" not in table.kinds
        assert table.kinds["esde:atarijaguar"] == "console"

    def test_a_map_replaces_one_keys_rows_and_its_relation_is_recomputed(self):
        table = _load(overrides=overrides_doc(_map("batocera/megadrive", "esde/megadrive")))
        assert _keys(translate([("esde", "megadrive")], table), "batocera") == [("megadrive", EXACT_MATCH)]

    def test_a_map_states_related_or_narrow_only(self):
        table = _load(overrides=overrides_doc(_map("romm/pico", "esde/gb", relation=RELATED_MATCH)))
        assert _keys(translate([("esde", "gb")], table), "romm") == [("gb", EXACT_MATCH), ("pico", RELATED_MATCH)]
        overrides = overrides_doc(_map("romm/pico", "esde/gb", relation=EXACT_MATCH))
        with pytest.raises(ValueError, match="only narrowMatch or relatedMatch"):
            _load(overrides=overrides)

    def test_a_merge_cites_itself_on_every_key_it_moves(self):
        table = _load(overrides=overrides_doc(_merge("batocera/jaguar", "esde/atarijaguar", citation="the merge's line")))
        answer = translate([("esde", "atarijaguar")], table)
        moved = [k.citations for s in answer.pairs[0].sources if s.source == "batocera" for k in s.keys]
        # The mapping's own citation, then the merge's: its anchors' key citations ("c" in the fixture) and its evidence.
        assert moved == [("batocera:file:1", "c", "the merge's line")]
        own = [k.citations for s in answer.pairs[0].sources if s.source == "esde" for k in s.keys]
        assert own == [("esde:file:1",)]

    def test_a_merge_folding_two_rows_of_one_key_keeps_both_placements_citations(self):
        table = fixture_table()
        table["mappings"][9]["citation"] = "romm:file:2"  # romm/genesis on esde:megadrive
        assert (table["mappings"][9]["key"], table["mappings"][9]["row"]) == ("genesis", "esde:megadrive")
        loaded = _load(table, overrides=overrides_doc(_merge("esde/genesis", "esde/megadrive", citation="merge line")))
        assert _citations(translate([("romm", "genesis")], loaded), "romm", "genesis") == (
            "c",
            "merge line",
            "romm:file:1",
            "romm:file:2",
        )

    def test_a_map_resolves_its_anchors_through_the_merges(self):
        table = _load(
            overrides=overrides_doc(
                _map("romm/pico", "batocera/jaguar"), _merge("batocera/jaguar", "esde/atarijaguar")
            )
        )
        assert _keys(translate([("romm", "pico")], table), "esde") == [("atarijaguar", EXACT_MATCH)]

    def test_a_kind_override_names_a_content_subset_and_its_parent(self):
        table = _load(overrides=overrides_doc(kind_override("romm/pico", "content-subset", subset_of="esde/gb")))
        assert table.kinds["romm:pico"] == "content-subset"
        assert table.subset_of["romm:pico"] == "esde:gb"

    def test_a_subset_of_with_another_kind_is_refused(self):
        overrides = overrides_doc(kind_override("romm/pico", "fantasy-console", subset_of="esde/gb"))
        with pytest.raises(ValueError, match="so its kind is content-subset, not fantasy-console"):
            _load(overrides=overrides)

    def test_a_content_subset_kind_without_a_subset_of_is_refused(self):
        overrides = overrides_doc(kind_override("romm/pico", "content-subset"))
        with pytest.raises(ValueError, match="a content-subset row names the row it is a subset_of"):
            _load(overrides=overrides)

    def test_a_catalogue_stated_content_subset_kind_without_a_subset_of_is_refused(self):
        # romm/pico is alone on its row, so the kind a catalogue states for it is the row's kind.
        table = fixture_table()
        table["kinds"]["romm"] = {"pico": {"kind": "content-subset", "citation": "c"}}
        with pytest.raises(ValueError, match="romm:pico are of kind content-subset but name no row they are a subset_of"):
            _load(table)
        assert _load(table, overrides=overrides_doc(PICO_IN_GB)).subset_of["romm:pico"] == "esde:gb"

    @pytest.mark.parametrize(
        "entry, message",
        [
            (_merge("batocera/lynx", "esde/gb"), "not a key of its source at the pin"),
            (_map("romm/dc", "esde/gb"), "not a key of its source at the pin"),
            (_map("romm/pico", "esde/sega32x"), "not a key of its source at the pin"),
            (kind_override("romm/sega-pico", "console"), "not a key of its source at the pin"),
        ],
    )
    def test_an_override_naming_a_key_its_source_does_not_have_is_refused(self, entry, message):
        overrides = overrides_doc(entry)
        with pytest.raises(ValueError, match=message):
            _load(overrides=overrides)

    def test_a_merge_of_a_row_into_itself_is_refused(self):
        overrides = overrides_doc(_merge("batocera/gb", "esde/gb"))
        with pytest.raises(ValueError, match="on one row already"):
            _load(overrides=overrides)

    def test_a_merge_needs_anchors_on_one_row(self):
        overrides = overrides_doc(_merge("batocera/megadrive", "esde/gb"))
        with pytest.raises(ValueError, match="covers 2 rows"):
            _load(overrides=overrides)

    def test_a_key_is_mapped_once(self):
        overrides = overrides_doc(_map("romm/pico", "esde/gb"), _map("romm/pico", "esde/genesis"))
        with pytest.raises(ValueError, match="mapped once"):
            _load(overrides=overrides)

    def test_an_override_without_a_reason_is_refused(self):
        overrides = overrides_doc(_merge("batocera/jaguar", "esde/atarijaguar", reason=""))
        with pytest.raises(ValueError, match="reason"):
            _load(overrides=overrides)

    def test_an_override_of_no_known_shape_is_refused(self):
        overrides = overrides_doc({"source": "romm", "key": "pico"})
        with pytest.raises(ValueError, match="a merge, a map or a kind"):
            _load(overrides=overrides)

    def test_an_excluded_key_is_placed_nowhere(self):
        table = _load(excluded=excluded_doc({"source": "romm", "key": "pico", "reason": "a wrong value"}))
        assert translate([("romm", "pico")], table).pairs[0].status == PAIR_UNKNOWN
        assert table.excluded[("romm", "pico")] == "a wrong value"

    @pytest.mark.parametrize(
        "entry",
        [
            _map("romm/pico", "esde/gb"),
            _map("batocera/jaguar", "romm/pico"),
            kind_override("romm/pico", "fantasy-console"),
        ],
    )
    def test_an_override_naming_an_excluded_key_is_refused(self, entry):
        overrides = overrides_doc(entry)
        excluded = excluded_doc({"source": "romm", "key": "pico", "reason": "r"})
        with pytest.raises(ValueError, match="romm/pico is excluded, so no judgement may also name it"):
            _load(overrides=overrides, excluded=excluded)

    def test_a_map_places_a_key_the_generator_placed_nowhere(self):
        table = fixture_table()
        table["keys"]["romm"]["dc"] = "c"
        loaded = _load(table, overrides=overrides_doc(_map("romm/dc", "esde/gb")))
        assert _keys(translate([("romm", "dc")], loaded), "esde") == [("gb", EXACT_MATCH)]

    def test_a_row_is_no_subset_of_itself(self):
        overrides = overrides_doc(kind_override("romm/pico", "content-subset", subset_of="romm/pico"))
        with pytest.raises(ValueError, match="subset_of names the row itself"):
            _load(overrides=overrides)

    @pytest.mark.parametrize(
        "anchoring",
        [
            _map("batocera/jaguar", "romm/pico"),
            kind_override("romm/pico", "fantasy-console"),
            _merge("romm/pico", "esde/atarijaguar"),
        ],
    )
    def test_a_mapped_key_is_no_anchor_for_another_judgement(self, anchoring):
        overrides = overrides_doc(_map("romm/pico", "esde/gb"), anchoring)
        with pytest.raises(ValueError, match="romm/pico is itself mapped"):
            _load(overrides=overrides)

    def test_a_map_is_cited_by_its_anchors_and_by_the_evidence_it_adds(self):
        table = fixture_table()
        table["keys"]["romm"]["pico"] = "romm:slugs:7"
        table["keys"]["esde"]["gb"] = "es:systems:3"
        bare = _load(table, overrides=overrides_doc(_map("romm/pico", "esde/gb", citation=None)))
        assert _citations(translate([("romm", "pico")], bare), "romm", "pico") == ("es:systems:3", "romm:slugs:7")
        cited = _load(table, overrides=overrides_doc(_map("romm/pico", "esde/gb", citation="a page")))
        assert _citations(translate([("romm", "pico")], cited), "romm", "pico") == ("a page", "es:systems:3", "romm:slugs:7")

    @pytest.mark.parametrize(
        "entry",
        [
            _map("romm/pico", "esde/gb", citation="a page; romm:slugs:7"),
            _merge("batocera/jaguar", "esde/atarijaguar", citation="es:systems:9"),
        ],
    )
    def test_evidence_that_repeats_an_anchors_citation_is_refused(self, entry):
        table = fixture_table()
        table["keys"]["romm"]["pico"] = "romm:slugs:7"
        table["keys"]["esde"]["atarijaguar"] = "es:systems:9"
        overrides = overrides_doc(entry)
        with pytest.raises(ValueError, match="citation repeats .*, which the loader attaches from the anchors"):
            _load(table, overrides=overrides)

    def test_an_override_states_its_evidence_or_null(self):
        overrides = overrides_doc(_map("romm/pico", "esde/gb", citation=""))
        with pytest.raises(ValueError, match="citation: expected a non-empty string"):
            _load(overrides=overrides)

    def test_an_excluded_key_its_source_does_not_have_is_refused(self):
        excluded = excluded_doc({"source": "romm", "key": "dc", "reason": "r"})
        with pytest.raises(ValueError, match="not a key of its source at the pin"):
            _load(excluded=excluded)


class TestThePackagedTable:
    def test_it_loads_with_every_override_anchored_on_a_key_at_its_pin(self):
        table = platform_table()
        assert table.placements

    def test_the_vocabularies_are_the_tables_own_columns(self):
        assert known_platform_vocabularies() == tuple(platform_table().sources)
        assert "romm" in known_platform_vocabularies()

    def test_every_answer_carries_the_table_and_source_versions(self):
        answer = translate([("esde", "gb")])
        assert answer.table_version == platform_table().table_version
        pinned = [(source, upstream, version) for source, pins in platform_table().sources.items() for upstream, version in pins]
        assert [(v.source, v.upstream, v.version) for v in answer.source_versions] == pinned
        assert [(v.upstream, v.version) for v in answer.source_versions if v.source == "screenscraper"] == [
            ("es-de", "v3.5.0"),
            ("romm", "5.4.1"),
        ]

    def test_the_contract_lists_each_project_a_column_is_read_from(self):
        versions = platform_translation_contract(translate([("esde", "gb")]))["source_versions"]
        assert [v for v in versions if v["source"] == "screenscraper"] == [
            {"source": "screenscraper", "upstream": "es-de", "version": "v3.5.0"},
            {"source": "screenscraper", "upstream": "romm", "version": "5.4.1"},
        ]

    def test_every_key_is_exact_to_itself_in_its_own_source(self):
        table = platform_table()
        not_exact = []
        for source, key in table.placements:
            relations = dict(_relations(translate([(source, key)])))
            if relations.get((source, key)) != EXACT_MATCH:
                not_exact.append((source, key, relations.get((source, key))))
        assert not_exact == []

    def test_every_relation_is_the_inverse_of_the_way_back(self):
        # A reaches B as broad exactly when B reaches A as narrow; exact and related are their own inverse.
        inverse = {EXACT_MATCH: EXACT_MATCH, BROAD_MATCH: NARROW_MATCH, NARROW_MATCH: BROAD_MATCH, RELATED_MATCH: RELATED_MATCH}
        table = platform_table()
        relations = {pair: dict(_relations(translate([pair]))) for pair in table.placements}
        asymmetric = [
            (a, b, relation, relations[b].get(a))
            for a, reached in relations.items()
            for b, relation in reached.items()
            if b != a and relations[b].get(a) != inverse[relation]
        ]
        assert asymmetric == []

    def test_no_row_name_reaches_any_answer(self):
        # D4: every key of every source asked, and no string in any answer is a row name.
        table = platform_table()
        rows = set(table.kinds) | set(table.subset_of.values())
        leaked: set[str] = set()
        for source, keys in table.keys.items():
            for key in keys:
                leaked |= rows & set(_strings(platform_translation_contract(translate([(source, key)]))))
        assert leaked == set()

    def test_no_row_name_reaches_an_intersection(self):
        # D4 over more than one pair: the first two keys of every row, asked together.
        table = platform_table()
        leaked: set[str] = set()
        for by_source in table.by_row.values():
            keys = [(source, key) for source, ks in by_source.items() for key in ks][:2]
            if len(keys) == 2:
                leaked |= _row_names() & set(_strings(platform_translation_contract(translate(keys))))
        assert leaked == set()

    def test_no_row_name_reaches_a_handle_answer(self):
        # D4 on the public surface: both handle questions, for every key and every catalogue name.
        table = platform_table()
        handle = atlas.detect("/home/deck", _retrodeck_machine())[0]
        assert isinstance(handle, atlas.RetroDeck)
        answers = [platform_systems_contract(handle.systems_for_platform(s, k)) for s, k in table.placements]
        names = {key for column in ("esde", "retrodeck") for key in table.keys[column]}
        answers += [system_platforms_contract(handle.platform_ids(name)) for name in sorted(names)]
        assert _row_names() & set(_strings(answers)) == set()

    def test_no_row_name_reaches_the_cli(self, capsys):
        # One key per source, and a catalogue name, through the command line's own output.
        table = platform_table()
        out = []
        for source in table.sources:
            key = sorted(table.keys[source])[0]
            assert run(["systems-for-platform", source, key, "--installation", "retrodeck"], home="/home/deck", machine=_retrodeck_machine()) == 0
            out.append(capsys.readouterr().out)
        assert run(["platform-ids", "snes", "--installation", "retrodeck"], home="/home/deck", machine=_retrodeck_machine()) == 0
        out.append(capsys.readouterr().out)
        assert _row_names() & set(_strings([json.loads(o) for o in out])) == set()


def _row_names() -> set[str]:
    table = platform_table()
    return set(table.kinds) | set(table.subset_of.values())


def _retrodeck_machine() -> FixtureMachine:
    # A RetroDECK installation with one declared system, so the handle and the CLI both answer.
    deploy = "/var/lib/flatpak/app/net.retrodeck.retrodeck/current/active/files/retrodeck/components/es-de"
    systems = (
        '<?xml version="1.0"?>\n<systemList>\n  <system><name>snes</name><fullname>Nintendo SNES</fullname>'
        "<path>%ROMPATH%/snes</path>\n    <extension>.sfc</extension>\n    <command label=\"Snes9x\">"
        "%EMULATOR_RETROARCH% -L %CORE_RETROARCH%/snes9x_libretro.so %ROM%</command>\n    <platform>snes</platform>\n"
        "  </system>\n</systemList>\n"
    )
    return FixtureMachine(
        {
            "/home/deck/.var/app/net.retrodeck.retrodeck/config/retrodeck/retrodeck.json": (
                '{"paths": {"rd_home_path": "/mnt/sd/retrodeck", "saves_path": "/mnt/sd/retrodeck/saves"}}'
            ),
            "/home/deck/.var/app/net.retrodeck.retrodeck/config/retroarch/retroarch.cfg": "",
            f"{deploy}/share/es-de/resources/systems/linux/es_systems.xml": systems,
        },
        dirs=["/mnt/sd/retrodeck/saves", "/home/deck/.var/app/net.retrodeck.retrodeck/config/ROMs"],
    )


def _relations(translation: PlatformTranslation) -> list[tuple[tuple[str, str], str]]:
    return [((s.source, k.key), k.relation) for s in translation.pairs[0].sources for k in s.keys]


def _strings(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [s for k, v in value.items() for s in (*_strings(k), *_strings(v))]
    if isinstance(value, list):
        return [s for item in value for s in _strings(item)]
    return []


class TestTheLookup:
    def test_a_coarser_key_is_broad_and_a_finer_one_narrow(self):
        table = _load()
        assert _keys(translate([("esde", "genesis")], table), "romm") == [("genesis", BROAD_MATCH)]
        assert _keys(translate([("romm", "genesis")], table), "esde") == [
            ("genesis", NARROW_MATCH),
            ("megadrive", NARROW_MATCH),
        ]
        assert _keys(translate([("romm", "genesis")], table), "batocera") == [("megadrive", EXACT_MATCH)]

    def test_the_asked_key_is_exact_to_itself_whatever_its_placement(self):
        table = _load(overrides=overrides_doc(_map("romm/pico", "esde/gb", relation=RELATED_MATCH)))
        answer = translate([("romm", "pico")], table)
        assert _keys(answer, "romm") == [("gb", RELATED_MATCH), ("pico", EXACT_MATCH)]
        assert _keys(answer, "esde") == [("gb", RELATED_MATCH)]

    def test_a_part_of_a_system_is_narrow_to_it_and_the_system_broad_to_the_part(self):
        table = _load(overrides=overrides_doc(_map("romm/pico", "esde/gb", relation=NARROW_MATCH)))
        assert _keys(translate([("romm", "pico")], table), "esde") == [("gb", BROAD_MATCH)]
        assert _keys(translate([("esde", "gb")], table), "romm") == [("gb", EXACT_MATCH), ("pico", NARROW_MATCH)]

    def test_two_parts_of_one_system_are_related_not_narrow(self):
        table = _load(
            overrides=overrides_doc(
                _map("romm/pico", "esde/gb", relation=NARROW_MATCH),
                _map("batocera/jaguar", "esde/gb", relation=NARROW_MATCH),
            )
        )
        assert _keys(translate([("romm", "pico")], table), "batocera") == [("gb", BROAD_MATCH), ("jaguar", RELATED_MATCH)]
        assert _keys(translate([("batocera", "jaguar")], table), "romm") == [("gb", BROAD_MATCH), ("pico", RELATED_MATCH)]

    def test_a_source_without_a_key_for_the_systems_says_none(self):
        answer = translate([("romm", "pico")], _load())
        statuses = {s.source: s.status for s in answer.pairs[0].sources}
        assert statuses == {"esde": KEYS_NONE, "batocera": KEYS_NONE, "romm": KEYS_FOUND, **{s: KEYS_NONE for s in REQUIRED}}

    def test_an_unknown_key_or_source_is_unknown_and_stated(self):
        # #784 D31: a source the table does not include has its own caveat; an unknown key keeps platform-unmapped.
        answer = translate([("romm", "dc"), ("mobygames", "1")], _load())
        assert [p.status for p in answer.pairs] == [PAIR_UNKNOWN, PAIR_UNKNOWN]
        assert answer.intersection_status == INTERSECTION_UNKNOWN
        assert [c.code for c in answer.caveats] == [CAVEAT_PLATFORM_UNMAPPED, CAVEAT_PLATFORM_SOURCE_UNKNOWN]
        assert dict(answer.caveats[0].data) == {"source": "romm", "key": "dc"}
        assert dict(answer.caveats[1].data) == {"source": "mobygames", "key": "1", "sources": tuple(_load().sources)}

    def test_a_bare_folder_name_nothing_spells_is_an_unmapped_key_not_an_unknown_source(self):
        answer = translate([("folder", "nowhere")], _load())
        assert [c.code for c in answer.caveats] == [CAVEAT_PLATFORM_UNMAPPED]

    def test_two_pairs_intersect(self):
        answer = translate([("romm", "genesis"), ("esde", "megadrive")], _load())
        assert answer.intersection_status == INTERSECTION_AGREED
        assert _keys(answer, "esde", "intersection") == [("megadrive", EXACT_MATCH)]
        assert _keys(answer, "romm", "intersection") == [("genesis", BROAD_MATCH)]

    def test_pairs_that_share_no_system_are_a_conflict_naming_them(self):
        answer = translate([("esde", "gb"), ("romm", "genesis"), ("romm", "dc")], _load())
        assert answer.intersection_status == INTERSECTION_CONFLICT
        assert [(p.source, p.key) for p in answer.conflict] == [("esde", "gb"), ("romm", "genesis")]
        assert answer.intersection == ()

    def test_a_bare_folder_is_the_union_of_the_folder_vocabularies(self):
        answer = translate([("folder", "megadrive")], _load())
        assert _keys(answer, "esde") == [("genesis", NARROW_MATCH), ("megadrive", NARROW_MATCH)]
        narrowed = translate([("folder", "megadrive"), ("esde", "megadrive")], _load())
        assert _keys(narrowed, "esde", "intersection") == [("megadrive", EXACT_MATCH)]

    def test_a_stated_version_the_table_does_not_hold_is_a_caveat(self):
        answer = translate([("romm", "gb", "m2"), ("esde", "gb", "v1")], _load())
        assert [c.code for c in answer.caveats] == [CAVEAT_PLATFORM_SOURCE_VERSION_DIFFERS]
        assert dict(answer.caveats[0].data) == {"source": "romm", "key": "gb", "version": "m2", "pins": {"romm": "m1"}}
        assert answer.pairs[0].version == "m2"

    def _two_upstreams(self) -> PlatformTable:
        # romm's column read from two projects, as ScreenScraper's is from ES-DE's map and RomM's list (#784 D42).
        table = fixture_table()
        table["sources"]["romm"] = [
            {"upstream": "romm", "version": "m1", "revision": "r3"},
            {"upstream": "es-de", "version": "v1", "revision": "r1"},
        ]
        for mapping in table["mappings"]:
            if mapping["source"] == "romm":
                mapping["valid_from"] = "romm m1, es-de v1"
        return _load(table)

    @pytest.mark.parametrize("version", ["m1", "v1"])
    def test_a_version_of_either_project_a_column_is_read_from_matches(self, version):
        answer = translate([("romm", "gb", version)], self._two_upstreams())
        assert answer.caveats == ()

    def test_a_version_no_project_of_the_column_is_at_names_each_projects_version(self):
        answer = translate([("romm", "gb", "m2")], self._two_upstreams())
        assert [dict(c.data) for c in answer.caveats] == [
            {"source": "romm", "key": "gb", "version": "m2", "pins": {"romm": "m1", "es-de": "v1"}}
        ]

    def test_a_column_lists_each_project_once(self):
        table = fixture_table()
        table["sources"]["romm"] = SOURCES["romm"] * 2
        with pytest.raises(ValueError, match="a project is listed twice"):
            _load(table)

    def test_one_project_at_two_versions_is_refused(self):
        table = fixture_table()
        table["sources"]["romm"] = [*SOURCES["romm"], {"upstream": "romm", "version": "m2", "revision": "r4"}]
        with pytest.raises(ValueError, match="a project is listed twice"):
            _load(table)

    def test_a_mapping_of_a_column_two_projects_fill_runs_from_both(self):
        table = fixture_table()
        table["sources"]["romm"] = [*SOURCES["romm"], *SOURCES["esde"]]
        with pytest.raises(ValueError, match="an entry runs from its source's pinned version"):
            _load(table)

    def test_surrounding_whitespace_is_stripped(self):
        assert translate([("romm", " gb ")], _load()).pairs[0].status == PAIR_KNOWN

    def test_every_key_carries_its_citations(self):
        answer = translate([("esde", "gb")], _load())
        assert [k.citations for s in answer.pairs[0].sources for k in s.keys] == [
            ("esde:file:1",),
            ("batocera:file:1",),
            ("romm:file:1",),
        ]

    @pytest.mark.parametrize("pair", [("romm", 7), (7, "gb"), ("romm", None), ("romm", "gb", 2)])
    def test_a_value_that_is_not_a_string_is_refused(self, pair):
        table = _load()
        with pytest.raises(ValueError, match="expected a string"):
            translate([pair], table)

    def test_a_malformed_pair_or_no_pair_is_refused(self):
        table = _load()
        with pytest.raises(ValueError, match="at least one"):
            translate([], table)
        with pytest.raises(ValueError, match=r"\(source, key\)"):
            translate([("romm",)], table)

    def test_the_machine_half_reads_the_esde_names_and_the_exact_tags(self):
        table = _load()
        answer = translate([("romm", "genesis")], table)
        assert systems_named(answer) == ("genesis", "megadrive")
        assert exact_platform_tags(answer) == ()

    def test_the_exact_platform_tags_are_the_ones_on_exactly_the_asked_systems(self):
        assert exact_platform_tags(translate([("rocknix", "n64")], _load())) == ("n64",)
        widened = _load(overrides=overrides_doc(_map("esde-platform/n64", "rocknix/n64", "esde/gb")))
        assert exact_platform_tags(translate([("rocknix", "n64")], widened)) == ()

    def test_the_machine_half_reads_the_catalogue_columns_it_is_given(self):
        answer = translate([("romm", "genesis")], _load())
        assert systems_named(answer, ("batocera",)) == ("megadrive",)
        assert systems_named(answer, ("esde", "batocera")) == ("genesis", "megadrive")
        assert systems_named(translate([("romm", "pico")], _load()), ("esde", "batocera")) == ()


class TestTheAnswerTypesHoldTheirInvariants:
    """Each answer type refuses a combination translate never builds."""

    KEY = PlatformKey("gb", EXACT_MATCH, ("c",))
    FOUND = PlatformSourceKeys("esde", KEYS_FOUND, (KEY,))
    KNOWN = PlatformPairAnswer("esde", "gb", None, PAIR_KNOWN, (FOUND,))
    UNKNOWN = PlatformPairAnswer("romm", "dc", None, PAIR_UNKNOWN, ())

    def test_a_key_carries_a_skos_relation_and_a_citation(self):
        with pytest.raises(ValueError, match="relation 'closeMatch'"):
            PlatformKey("gb", "closeMatch", ("c",))  # type: ignore[arg-type]
        with pytest.raises(ValueError, match="without a citation"):
            PlatformKey("gb", EXACT_MATCH, ())

    def test_a_source_lists_keys_exactly_under_keys(self):
        with pytest.raises(ValueError, match="exactly when"):
            PlatformSourceKeys("esde", KEYS_NONE, (self.KEY,))
        with pytest.raises(ValueError, match="exactly when"):
            PlatformSourceKeys("esde", KEYS_FOUND, ())

    def test_a_known_pair_lists_sources_and_an_unknown_one_none(self):
        with pytest.raises(ValueError, match="a known pair lists every source"):
            PlatformPairAnswer("esde", "gb", None, PAIR_KNOWN, ())
        with pytest.raises(ValueError, match="a known pair lists every source"):
            PlatformPairAnswer("romm", "dc", None, PAIR_UNKNOWN, (self.FOUND,))

    @pytest.mark.parametrize(
        "status, pairs, intersection, conflict, message",
        [
            (INTERSECTION_AGREED, "known", (), (), "the intersection lists sources exactly when it is agreed"),
            (INTERSECTION_CONFLICT, "known", (), (), "conflict names pairs exactly when"),
            (INTERSECTION_CONFLICT, "known", (FOUND,), ("known",), "the intersection lists sources exactly when"),
            (INTERSECTION_UNKNOWN, "known", (), (), "unknown exactly when every pair is"),
            (INTERSECTION_AGREED, "unknown", (FOUND,), (), "unknown exactly when every pair is"),
            (INTERSECTION_UNKNOWN, None, (), (), "at least one pair"),
        ],
    )
    def test_a_translation_states_one_intersection(self, status, pairs, intersection, conflict, message):
        answer = {"known": self.KNOWN, "unknown": self.UNKNOWN}
        asked = () if pairs is None else (answer[pairs],)
        conflicting = tuple(answer[c] for c in conflict)
        with pytest.raises(ValueError, match=message):
            PlatformTranslation(1, (), asked, status, intersection, conflicting, ())


class TestTheHandleQuestion:
    def test_a_numeric_value_is_refused_through_an_installation(self):
        handle = atlas.RetroDeck("/home/deck", FixtureMachine({}))
        with pytest.raises(ValueError, match="expected a string"):
            handle.systems_for_platform("screenscraper", 7)  # type: ignore[arg-type]

    def test_an_unknown_key_answers_no_matches_and_unmapped(self):
        answer = atlas.RetroDeck("/home/deck", FixtureMachine({})).systems_for_platform("romm", "not-a-slug")
        assert answer.matches == ()
        assert CAVEAT_PLATFORM_UNMAPPED in [c.code for c in answer.caveats]

    def test_platform_unmapped_has_one_data_shape_inside_and_outside_the_translation(self):
        handle = atlas.RetroDeck("/home/deck", FixtureMachine({}))
        forward = handle.systems_for_platform("romm", "not-a-slug")
        reverse = handle.platform_ids("not-a-system")
        for answer in (forward, reverse):
            inner = [dict(c.data) for c in answer.translation.caveats if c.code == CAVEAT_PLATFORM_UNMAPPED]
            outer = [dict(c.data) for c in answer.caveats if c.code == CAVEAT_PLATFORM_UNMAPPED]
            assert inner == outer
        assert [dict(c.data) for c in reverse.caveats if c.code == CAVEAT_PLATFORM_UNMAPPED] == [
            {"source": "esde", "key": "not-a-system"}
        ]

    def test_the_asked_key_is_named_in_the_translation_as_it_is_matched(self):
        answer = atlas.RetroDeck("/home/deck", FixtureMachine({})).systems_for_platform("romm", " gb ")
        assert answer.translation.pairs[0].key == "gb"


# --- cut 2a: coverage, parts, intersections, caveats (#784 D32–D38) ---------------------


# romm:pico, a row of its own in the fixture, declared a content subset of ES-DE's gb.
PICO_IN_GB = kind_override("romm/pico", "content-subset", subset_of="esde/gb")
# batocera:jaguar, a row of its own in the fixture, declared a second content subset of ES-DE's gb.
JAGUAR_IN_GB = kind_override("batocera/jaguar", "content-subset", subset_of="esde/gb")


class TestTheLoaderStoresCoverage:
    def test_a_generated_placement_covers_its_row_whole_and_a_map_covers_what_it_states(self):
        table = _load(
            overrides=overrides_doc(
                _map("romm/pico", "esde/gb", relation=NARROW_MATCH),
                _map("batocera/jaguar", "esde/gb", relation=RELATED_MATCH),
                _map("batocera/gb", "esde/gb"),
            )
        )
        coverage = {pair: {r: p.coverage for r, p in table.placements[pair].items()} for pair in table.placements}
        assert coverage[("romm", "genesis")] == {"esde:genesis": "whole", "esde:megadrive": "whole"}
        assert coverage[("romm", "pico")] == {"esde:gb": "part"}
        assert coverage[("batocera", "jaguar")] == {"esde:gb": "overlap"}
        assert coverage[("batocera", "gb")] == {"esde:gb": "whole"}

    def test_several_rows_stated_exact_are_refused_as_one_row_stated_broad_is(self):
        table = fixture_table()
        romm_genesis = [m for m in table["mappings"] if (m["source"], m["key"]) == ("romm", "genesis")]
        for mapping in romm_genesis:
            mapping["relation"] = EXACT_MATCH
        refused = "romm/genesis: on 2 row.s. the relation is broadMatch, the file states exactMatch"
        with pytest.raises(ValueError, match=refused):
            _load(table)


class TestAContentSubsetAnswersAsAPart:
    """#784 D37: a row an override declares a subset of another is a part of it in every answer."""

    def test_the_parents_keys_are_broad_from_the_part(self):
        answer = translate([("romm", "pico")], _load(overrides=overrides_doc(PICO_IN_GB)))
        assert _keys(answer, "esde") == [("gb", BROAD_MATCH)]
        assert _keys(answer, "romm") == [("gb", BROAD_MATCH), ("pico", EXACT_MATCH)]

    def test_a_question_about_the_parent_lists_the_part_as_narrow(self):
        answer = translate([("esde", "gb")], _load(overrides=overrides_doc(PICO_IN_GB)))
        assert _keys(answer, "romm") == [("gb", EXACT_MATCH), ("pico", NARROW_MATCH)]

    def test_two_keys_on_one_subset_row_stay_exact_to_each_other(self):
        table = fixture_table()
        table["keys"]["batocera"]["pico8"] = "c"
        table["mappings"].append(_mapping("batocera", "pico8", "romm:pico"))
        answer = translate([("batocera", "pico8")], _load(table, overrides=overrides_doc(PICO_IN_GB)))
        assert _keys(answer, "romm") == [("gb", BROAD_MATCH), ("pico", EXACT_MATCH)]

    @pytest.mark.parametrize("pairs", [[("esde", "gb"), ("romm", "pico")], [("romm", "pico"), ("esde", "gb")]])
    def test_a_part_and_its_parent_agree_on_the_part_in_either_order(self, pairs):
        answer = translate(pairs, _load(overrides=overrides_doc(PICO_IN_GB)))
        assert answer.intersection_status == INTERSECTION_AGREED
        # The part narrows the asked parent: ES-DE's gb, asked, covers the part and more.
        assert _keys(answer, "esde", "intersection") == [("gb", BROAD_MATCH)]
        assert _keys(answer, "romm", "intersection") == [("gb", BROAD_MATCH), ("pico", EXACT_MATCH)]
        assert _keys(answer, "batocera", "intersection") == [("gb", BROAD_MATCH)]

    @pytest.mark.parametrize(
        ("asked", "sibling"), [(("romm", "pico"), ("batocera", "jaguar")), (("batocera", "jaguar"), ("romm", "pico"))]
    )
    def test_two_content_subsets_of_one_row_list_each_other_as_related(self, asked, sibling):
        # As two map parts of one row do: neither holds the other, and the table cannot say whether they overlap.
        answer = translate([asked], _load(overrides=overrides_doc(PICO_IN_GB, JAGUAR_IN_GB)))
        assert _keys(answer, sibling[0]) == [("gb", BROAD_MATCH), (sibling[1], RELATED_MATCH)]

    @pytest.mark.parametrize(("asked", "sibling"), [("gzdoom", "prboom"), ("prboom", "gzdoom")])
    def test_the_packaged_engine_rows_list_each_other_as_related(self, asked, sibling):
        # Batocera's gzdoom and prboom rows are both content subsets of ES-DE's doom.
        answer = translate([("batocera", asked)])
        assert sorted(_keys(answer, "batocera")) == sorted([(asked, EXACT_MATCH), (sibling, RELATED_MATCH)])
        assert _keys(answer, "esde") == [("doom", BROAD_MATCH)]

    def test_a_subset_of_a_subset_is_refused(self):
        overrides = overrides_doc(PICO_IN_GB, kind_override("esde/gb", "content-subset", subset_of="rocknix/n64"))
        with pytest.raises(ValueError, match="is itself a subset of another, so the part would be a part of a part"):
            _load(overrides=overrides)


class TestTheIntersectionCarriesCoverage:
    """#784 D36: a composite per shared row — whole where every pair covers it whole, else a part."""

    def _table(self):
        # romm/pico covers only part of ES-DE's gb; batocera/jaguar covers another part of it.
        return _load(
            overrides=overrides_doc(
                _map("romm/pico", "esde/gb", relation=NARROW_MATCH),
                _map("batocera/jaguar", "esde/gb", relation=NARROW_MATCH),
            )
        )

    def _overlap_table(self):
        # romm/pico and batocera/jaguar each overlap ES-DE's gb: each may hold something gb does not.
        return _load(
            overrides=overrides_doc(
                _map("romm/pico", "esde/gb", relation=RELATED_MATCH),
                _map("batocera/jaguar", "esde/gb", relation=RELATED_MATCH),
            )
        )

    @pytest.mark.parametrize("pairs", [[("romm", "pico"), ("romm", "gb")], [("romm", "gb"), ("romm", "pico")]])
    def test_a_whole_key_over_the_part_the_pairs_mean_is_broad(self, pairs):
        answer = translate(pairs, self._table())
        assert _keys(answer, "esde", "intersection") == [("gb", BROAD_MATCH)]
        assert _keys(answer, "batocera", "intersection") == [("gb", BROAD_MATCH), ("jaguar", RELATED_MATCH)]

    @pytest.mark.parametrize("pairs", [[("romm", "pico"), ("romm", "gb")], [("romm", "gb"), ("romm", "pico")]])
    def test_an_asked_pair_the_others_do_not_narrow_is_exact_and_one_they_narrow_is_not(self, pairs):
        # pico's part of gb is the composite; romm/gb, asked too, covers that part and more.
        answer = translate(pairs, self._table())
        assert _keys(answer, "romm", "intersection") == [("gb", BROAD_MATCH), ("pico", EXACT_MATCH)]

    @pytest.mark.parametrize("pairs", [[("romm", "pico"), ("esde", "gb")], [("esde", "gb"), ("romm", "pico")]])
    def test_an_overlap_met_with_the_whole_row_is_the_part_of_it_the_overlap_key_covers(self, pairs):
        # What both cover is the share of gb that pico covers: gb holds it and more, pico is exactly it.
        answer = translate(pairs, self._overlap_table())
        assert _keys(answer, "esde", "intersection") == [("gb", BROAD_MATCH)]
        assert _keys(answer, "romm", "intersection") == [("gb", BROAD_MATCH), ("pico", EXACT_MATCH)]

    def test_two_overlaps_of_one_row_share_an_overlap_with_it(self):
        # Neither overlap need lie inside gb, so what they share may hold something gb does not either.
        answer = translate([("romm", "pico"), ("batocera", "jaguar")], self._overlap_table())
        assert _keys(answer, "esde", "intersection") == [("gb", RELATED_MATCH)]

    def test_one_part_key_asked_twice_stays_exact_to_its_part(self):
        answer = translate([("romm", "pico"), ("romm", "pico")], self._table())
        assert _keys(answer, "romm", "intersection") == [("gb", BROAD_MATCH), ("pico", EXACT_MATCH)]

    def test_two_different_parts_share_a_part_no_key_is_exact_to(self):
        # The table cannot know whether pico's and jaguar's parts of gb overlap: both are related to what is shared.
        answer = translate([("romm", "pico"), ("batocera", "jaguar")], self._table())
        assert _keys(answer, "romm", "intersection") == [("gb", BROAD_MATCH), ("pico", RELATED_MATCH)]
        assert _keys(answer, "batocera", "intersection") == [("gb", BROAD_MATCH), ("jaguar", RELATED_MATCH)]

    def test_pairs_that_all_cover_a_row_whole_share_it_whole(self):
        answer = translate([("esde", "gb"), ("batocera", "gb")], self._table())
        assert _keys(answer, "romm", "intersection") == [("gb", EXACT_MATCH), ("pico", NARROW_MATCH)]


PICO_PART_OF_GB = _map("romm/pico", "esde/gb", relation=NARROW_MATCH)
JAGUAR_PART_OF_GB = _map("batocera/jaguar", "esde/gb", relation=NARROW_MATCH)
PICO_AND_JAGUAR = [[("romm", "pico"), ("batocera", "jaguar")], [("batocera", "jaguar"), ("romm", "pico")]]


def _parts(translation: PlatformTranslation) -> list[tuple[str, dict[str, Any]]]:
    return [(c.code, dict(c.data)) for c in translation.caveats if c.code == CAVEAT_PLATFORM_PARTS_UNESTABLISHED]


class TestPairsMeetingThroughPartsSayTheTableCannotCompareThem:
    """#784 D40: one rule for every way two pairs share a system only through pieces of it.

    The pieces are two part keys, a part key and a content subset, two content subsets of one parent, two overlaps,
    or a part and an overlap.
    """

    SHAPES = {
        "two-part-keys": (PICO_PART_OF_GB, JAGUAR_PART_OF_GB),
        "a-part-key-and-a-content-subset": (PICO_PART_OF_GB, JAGUAR_IN_GB),
        "two-content-subsets-of-one-parent": (PICO_IN_GB, JAGUAR_IN_GB),
    }

    @pytest.mark.parametrize("shape", SHAPES)
    @pytest.mark.parametrize("pairs", PICO_AND_JAGUAR)
    def test_the_system_is_shared_the_parts_are_related_and_a_caveat_names_the_pairs(self, shape, pairs):
        answer = translate(pairs, _load(overrides=overrides_doc(*self.SHAPES[shape])))
        assert answer.intersection_status == INTERSECTION_AGREED
        assert _keys(answer, "esde", "intersection") == [("gb", BROAD_MATCH)]
        assert _keys(answer, "romm", "intersection") == [("gb", BROAD_MATCH), ("pico", RELATED_MATCH)]
        assert _keys(answer, "batocera", "intersection") == [("gb", BROAD_MATCH), ("jaguar", RELATED_MATCH)]
        named = {"sources": tuple(s for s, _ in pairs), "keys": tuple(k for _, k in pairs)}
        assert _parts(answer) == [(CAVEAT_PLATFORM_PARTS_UNESTABLISHED, named)]

    @pytest.mark.parametrize(
        "pico_and_jaguar",
        [(RELATED_MATCH, RELATED_MATCH), (NARROW_MATCH, RELATED_MATCH), (RELATED_MATCH, NARROW_MATCH)],
        ids=["two-overlaps", "a-part-and-an-overlap", "an-overlap-and-a-part"],
    )
    def test_overlaps_meet_like_parts_and_say_the_table_cannot_compare_them(self, pico_and_jaguar):
        # #784 D40 (changed): an overlap counts like a part — the shared content is unknown either way.
        pico, jaguar = pico_and_jaguar
        table = _load(
            overrides=overrides_doc(
                _map("romm/pico", "esde/gb", relation=pico), _map("batocera/jaguar", "esde/gb", relation=jaguar)
            )
        )
        answer = translate([("romm", "pico"), ("batocera", "jaguar")], table)
        assert answer.intersection_status == INTERSECTION_AGREED
        assert [(c.code, dict(c.data)) for c in answer.caveats] == [
            (CAVEAT_PLATFORM_PARTS_UNESTABLISHED, {"sources": ("romm", "batocera"), "keys": ("pico", "jaguar")})
        ]

    def test_a_pair_covering_the_shared_row_whole_is_not_named(self):
        pairs = [("romm", "pico"), ("esde", "gb"), ("batocera", "jaguar")]
        answer = translate(pairs, _load(overrides=overrides_doc(PICO_PART_OF_GB, JAGUAR_PART_OF_GB)))
        assert _parts(answer) == [
            (CAVEAT_PLATFORM_PARTS_UNESTABLISHED, {"sources": ("romm", "batocera"), "keys": ("pico", "jaguar")})
        ]

    @pytest.mark.parametrize(
        "pairs",
        [
            [("romm", "pico"), ("esde", "gb")],
            [("romm", "pico"), ("romm", "pico")],
            [("folder", "jaguar"), ("esde", "gb")],
        ],
        ids=["a-part-and-its-whole-row", "one-part-asked-twice", "a-folder-part-and-its-whole-row"],
    )
    def test_one_part_meeting_its_row_or_itself_states_nothing(self, pairs):
        answer = translate(pairs, _load(overrides=overrides_doc(PICO_PART_OF_GB, JAGUAR_PART_OF_GB)))
        assert answer.intersection_status == INTERSECTION_AGREED
        assert _parts(answer) == []

    @pytest.mark.parametrize("pairs", [[("folder", "jaguar"), ("batocera", "jaguar")], [("batocera", "jaguar"), ("folder", "jaguar")]])
    def test_a_folder_part_meeting_the_vocabularys_own_key_is_that_one_part(self, pairs):
        # The bare folder's part of gb is batocera's own jaguar placement, so the two pairs mean one part.
        answer = translate(pairs, _load(overrides=overrides_doc(PICO_PART_OF_GB, JAGUAR_PART_OF_GB)))
        assert _parts(answer) == []
        assert _keys(answer, "batocera", "intersection") == [("gb", BROAD_MATCH), ("jaguar", EXACT_MATCH)]

    def test_the_packaged_folder_part_and_its_own_key_state_nothing(self):
        # The bare folder pc's overlap of dos is ROCKNIX's own pc placement, so asked beside it they mean one thing.
        answer = translate([("folder", "pc"), ("rocknix", "pc")])
        assert _parts(answer) == []
        assert ("pc", EXACT_MATCH) in _keys(answer, "rocknix", "intersection")

    def test_a_bare_folder_holds_the_vocabularys_own_part_and_more(self):
        # The folder union is ES-DE's whole pc row plus ROCKNIX's pc, which only overlaps dos.
        assert ("pc", NARROW_MATCH) in _keys(translate([("folder", "pc")]), "rocknix")

    @pytest.mark.parametrize(
        "pairs", [[("romm", "doom"), ("batocera", "gzdoom")], [("batocera", "gzdoom"), ("romm", "doom")]]
    )
    def test_a_key_on_a_row_and_its_subsets_meets_one_subset_inside_the_row(self, pairs):
        # RomM's doom is placed on doom and on the engine rows; it covers doom whole, so gzdoom's part is inside it.
        # The intersection is the gzdoom part: its keys are exact, doom's are broad, and prboom's sibling part is
        # related to it, not shared.
        answer = translate(pairs)
        assert _parts(answer) == []
        assert _keys(answer, "batocera", "intersection") == [("gzdoom", EXACT_MATCH), ("prboom", RELATED_MATCH)]
        assert _keys(answer, "esde", "intersection") == [("doom", BROAD_MATCH)]

    @pytest.mark.parametrize(
        "placed, pairs",
        [
            (("romm", "gb"), [("romm", "gb"), ("batocera", "jaguar")]),
            (("romm", "gb"), [("batocera", "jaguar"), ("romm", "gb")]),
            (("batocera", "gb"), [("folder", "gb"), ("batocera", "jaguar")]),
            (("batocera", "gb"), [("batocera", "jaguar"), ("folder", "gb")]),
        ],
        ids=["a-key", "a-key-asked-second", "a-bare-folder", "a-bare-folder-asked-second"],
    )
    def test_a_parent_covered_whole_with_one_subset_meets_another_subset_inside_it(self, placed, pairs):
        # The placed key covers gb whole and pico, one of gb's subsets; jaguar, the other subset, lies inside gb.
        # Asked as a bare folder, gb is the union of ES-DE's gb row and Batocera's gb, which holds pico too.
        table = fixture_table()
        for mapping in table["mappings"]:
            if (mapping["source"], mapping["key"]) == placed:
                mapping["relation"] = BROAD_MATCH
        table["mappings"].append(_mapping(placed[0], placed[1], "romm:pico", BROAD_MATCH))
        answer = translate(pairs, _load(table, overrides=overrides_doc(PICO_IN_GB, JAGUAR_IN_GB)))
        assert _parts(answer) == []
        assert _keys(answer, "batocera", "intersection") == [("gb", BROAD_MATCH), ("jaguar", EXACT_MATCH)]

    @pytest.mark.parametrize("pairs", [[("romm", "gb"), ("romm", "pico")], [("romm", "pico"), ("romm", "gb")]])
    def test_a_parent_covered_in_part_keeps_its_subset(self, pairs):
        # romm/gb covers part of gb and part of pico, one of gb's subsets: gb is not covered whole, so pico stays
        # in romm/gb's extent, and the two pairs meet on pico without a statement.
        overrides = overrides_doc(PICO_IN_GB, JAGUAR_IN_GB, _map("romm/gb", "esde/gb", "romm/pico", relation=NARROW_MATCH))
        answer = translate(pairs, _load(overrides=overrides))
        assert answer.intersection_status == INTERSECTION_AGREED
        assert _parts(answer) == []
        assert _keys(answer, "romm", "intersection") == [("gb", BROAD_MATCH), ("pico", BROAD_MATCH)]
        asked_gb = next(pair for pair in answer.pairs if pair.key == "gb")
        assert ("gb", EXACT_MATCH) in [(k.key, k.relation) for s in asked_gb.sources if s.source == "romm" for k in s.keys]

    def test_the_folder_vocabularies_spelling_one_engine_row_are_that_one_part(self):
        # Batocera's gzdoom row is a content subset of doom (D37), and KNULLI spells the same row.
        answer = translate([("knulli", "gzdoom"), ("batocera", "gzdoom")])
        assert answer.intersection_status == INTERSECTION_AGREED
        assert _parts(answer) == []
        assert ("gzdoom", EXACT_MATCH) in _keys(answer, "knulli", "intersection")

    def test_a_content_subset_inside_a_parent_covered_whole_states_nothing(self):
        answer = translate([("romm", "pico"), ("esde", "gb")], _load(overrides=overrides_doc(PICO_IN_GB)))
        assert _parts(answer) == []

    def test_the_reverse_question_lifts_the_statement_whatever_the_intersection(self, monkeypatch):
        # A declared system whose pairs meet through parts or overlaps: the translation is agreed and still states it.
        met = translate([("libretro", "Nintendo - Wii (Digital)"), ("retrodeck", "primehack")])
        monkeypatch.setattr("atlas.installations._system_translation", lambda *_: met)
        answer = atlas.RetroDeck("/home/deck", _retrodeck_machine()).platform_ids("snes")
        assert met.intersection_status == INTERSECTION_AGREED
        assert [c for c in answer.caveats if c.code == CAVEAT_PLATFORM_PARTS_UNESTABLISHED] == list(met.caveats)

    def test_the_handle_answer_lifts_the_statement(self):
        handle = atlas.RetroDeck("/home/deck", _retrodeck_machine())
        answer = handle.systems_for_platforms([("libretro", "Nintendo - Wii (Digital)"), ("retrodeck", "primehack")])
        assert answer.translation.intersection_status == INTERSECTION_AGREED
        lifted = [c for c in answer.caveats if c.code == CAVEAT_PLATFORM_PARTS_UNESTABLISHED]
        assert lifted == [c for c in answer.translation.caveats if c.code == CAVEAT_PLATFORM_PARTS_UNESTABLISHED]
        assert [dict(c.data) for c in lifted] == [
            {"sources": ("libretro", "retrodeck"), "keys": ("Nintendo - Wii (Digital)", "primehack")}
        ]


class TestTheUnknownPairCaveats:
    EXCLUDED = excluded_doc({"source": "romm", "key": "pico", "reason": "a wrong value"})

    def test_an_excluded_key_states_its_recorded_reason(self):
        answer = translate([("romm", "pico")], _load(excluded=self.EXCLUDED))
        assert [(c.code, dict(c.data)) for c in answer.caveats] == [
            (CAVEAT_PLATFORM_EXCLUDED, {"source": "romm", "key": "pico", "reason": "a wrong value"})
        ]

    def test_a_bare_folder_a_folder_vocabulary_excludes_states_that_reason(self):
        # gamegear is a key of romm-folder alone, excluded there: the bare folder has no rows and that reason.
        table = fixture_table()
        table["keys"]["romm-folder"]["gamegear"] = "c"
        excluded = excluded_doc({"source": "romm-folder", "key": "gamegear", "reason": "not a folder"})
        answer = translate([("folder", "gamegear"), ("folder", "nowhere")], _load(table, excluded=excluded))
        assert [(c.code, dict(c.data)) for c in answer.caveats] == [
            (CAVEAT_PLATFORM_EXCLUDED, {"source": "folder", "key": "gamegear", "reason": "not a folder"}),
            (CAVEAT_PLATFORM_UNMAPPED, {"source": "folder", "key": "nowhere"}),
        ]

    def test_a_version_on_a_bare_folder_is_stated_as_not_applied_and_the_union_stands(self):
        answer = translate([("folder", "megadrive", "b2")], _load())
        assert [(c.code, dict(c.data)) for c in answer.caveats] == [
            (CAVEAT_PLATFORM_VERSION_UNAPPLIED, {"source": "folder", "key": "megadrive", "version": "b2"})
        ]
        assert _keys(answer, "esde") == [("genesis", NARROW_MATCH), ("megadrive", NARROW_MATCH)]

    def test_a_folder_pair_without_a_version_states_nothing_about_one(self):
        assert translate([("folder", "megadrive")], _load()).caveats == ()

    def test_keys_compare_exactly_case_included(self):
        answer = translate([("romm", "GB"), ("romm", "gb")], _load())
        assert [p.status for p in answer.pairs] == [PAIR_UNKNOWN, PAIR_KNOWN]


class TestTheTranslationTiesEachUnknownPairToItsCaveat:
    """Each refusal is a combination translate never builds."""

    KEY = PlatformKey("gb", EXACT_MATCH, ("c",))
    FOUND = PlatformSourceKeys("esde", KEYS_FOUND, (KEY,))
    NONE = PlatformSourceKeys("esde", KEYS_NONE, ())
    KNOWN = PlatformPairAnswer("esde", "gb", None, PAIR_KNOWN, (FOUND,))
    OTHER = PlatformPairAnswer("esde", "nes", None, PAIR_KNOWN, (FOUND,))
    UNKNOWN = PlatformPairAnswer("romm", "dc", None, PAIR_UNKNOWN, ())
    UNMAPPED = Caveat(CAVEAT_PLATFORM_UNMAPPED, "m", {"source": "romm", "key": "dc"})

    def _translation(self, pairs, status, intersection=(), conflict=(), caveats=()):
        return PlatformTranslation(1, (), pairs, status, intersection, conflict, caveats)

    def test_an_unknown_pair_without_its_caveat_is_refused(self):
        with pytest.raises(ValueError, match="every unknown pair, and nothing else, states why in one caveat"):
            self._translation((self.UNKNOWN,), INTERSECTION_UNKNOWN)

    def test_a_caveat_for_a_pair_that_is_known_is_refused(self):
        with pytest.raises(ValueError, match="every unknown pair, and nothing else, states why in one caveat"):
            self._translation((self.KNOWN,), INTERSECTION_AGREED, (self.FOUND,), caveats=(self.UNMAPPED,))

    def test_two_unknown_pairs_of_one_source_each_state_their_own_key(self):
        second = PlatformPairAnswer("romm", "x", None, PAIR_UNKNOWN, ())
        own = Caveat(CAVEAT_PLATFORM_UNMAPPED, "m", {"source": "romm", "key": "x"})
        self._translation((self.UNKNOWN, second), INTERSECTION_UNKNOWN, caveats=(self.UNMAPPED, own))
        with pytest.raises(ValueError, match="every unknown pair, and nothing else, states why in one caveat"):
            self._translation((self.UNKNOWN, second), INTERSECTION_UNKNOWN, caveats=(self.UNMAPPED, self.UNMAPPED))

    NAMES_PAIRS = "names at least two known pairs, in pair order"
    SOURCES_AND_KEYS = "carries sources and keys, and nothing else"

    @pytest.mark.parametrize(
        "data, refused",
        [
            ({"sources": ("esde",), "keys": ("gb",)}, NAMES_PAIRS),
            ({"sources": ("esde", "esde"), "keys": ("nes", "gb")}, NAMES_PAIRS),
            ({"sources": ("esde", "romm"), "keys": ("gb", "dc")}, NAMES_PAIRS),
            ({"sources": ("esde", "esde"), "keys": ("gb",)}, NAMES_PAIRS),
            ({"sources": ("esde", "esde"), "keys": ("gb", "nes"), "rows": ("esde:gb",)}, SOURCES_AND_KEYS),
            ({"sources": ("esde", "esde")}, SOURCES_AND_KEYS),
        ],
        ids=["one-pair", "out-of-pair-order", "an-unknown-pair", "lists-of-two-lengths", "an-extra-key", "no-keys"],
    )
    def test_the_parts_caveat_names_at_least_two_known_pairs_in_order(self, data, refused):
        parts = Caveat(CAVEAT_PLATFORM_PARTS_UNESTABLISHED, "m", data)
        unmapped = (self.UNMAPPED,) if "dc" in data.get("keys", ()) else ()
        pairs = (self.KNOWN, self.OTHER, self.UNKNOWN) if unmapped else (self.KNOWN, self.OTHER)
        with pytest.raises(ValueError, match=refused):
            self._translation(pairs, INTERSECTION_AGREED, (self.FOUND,), caveats=(*unmapped, parts))

    def test_a_source_the_table_includes_is_not_stated_unknown(self):
        # romm is among the caveat's own sources, so the stated cause is the wrong one.
        stated = {"source": "romm", "key": "dc", "sources": ("esde", "romm")}
        unknown = Caveat(CAVEAT_PLATFORM_SOURCE_UNKNOWN, "m", stated)
        with pytest.raises(ValueError, match="platform-source-unknown names a source its own list of sources lacks"):
            self._translation((self.UNKNOWN,), INTERSECTION_UNKNOWN, caveats=(unknown,))
        lacking = Caveat(CAVEAT_PLATFORM_SOURCE_UNKNOWN, "m", {"source": "romm", "key": "dc", "sources": ("esde",)})
        self._translation((self.UNKNOWN,), INTERSECTION_UNKNOWN, caveats=(lacking,))

    def test_pairs_meet_through_parts_once_and_only_on_an_agreed_intersection(self):
        parts = Caveat(CAVEAT_PLATFORM_PARTS_UNESTABLISHED, "m", {"sources": ("esde", "esde"), "keys": ("gb", "nes")})
        refused = "pairs meet through parts or overlaps at most once, and only on an agreed intersection"
        self._translation((self.KNOWN, self.OTHER), INTERSECTION_AGREED, (self.FOUND,), caveats=(parts,))
        with pytest.raises(ValueError, match=refused):
            self._translation((self.KNOWN, self.OTHER), INTERSECTION_AGREED, (self.FOUND,), caveats=(parts, parts))
        with pytest.raises(ValueError, match=refused):
            self._translation(
                (self.KNOWN, self.OTHER), INTERSECTION_CONFLICT, conflict=(self.KNOWN, self.OTHER), caveats=(parts,)
            )

    def test_a_conflict_names_every_known_pair_and_at_least_two(self):
        with pytest.raises(ValueError, match="a conflict names every known pair"):
            self._translation((self.KNOWN,), INTERSECTION_CONFLICT, conflict=(self.KNOWN,))
        with pytest.raises(ValueError, match="a conflict names every known pair"):
            self._translation((self.KNOWN, self.OTHER), INTERSECTION_CONFLICT, conflict=(self.KNOWN,))

    def test_a_known_pair_and_an_agreed_intersection_have_a_key_somewhere(self):
        with pytest.raises(ValueError, match="a known pair has a key in some source"):
            PlatformPairAnswer("esde", "gb", None, PAIR_KNOWN, (self.NONE,))
        with pytest.raises(ValueError, match="an agreed intersection has a key in some source"):
            self._translation((self.KNOWN,), INTERSECTION_AGREED, (self.NONE,))

    def test_what_translate_builds_passes(self):
        answer = self._translation(
            (self.KNOWN, self.OTHER, self.UNKNOWN), INTERSECTION_CONFLICT, conflict=(self.KNOWN, self.OTHER),
            caveats=(self.UNMAPPED,),
        )
        assert answer.intersection_status == INTERSECTION_CONFLICT


class TestNamesOneSystem:
    def test_a_key_on_one_row_covering_it_whole_names_one_system(self):
        assert names_one_system("rocknix", "n64", _load())
        assert names_one_system("esde", "gb", _load())

    def test_a_key_on_several_rows_names_none_alone(self):
        assert not names_one_system("batocera", "megadrive", _load())

    def test_a_key_covering_part_of_its_one_row_names_none(self):
        table = _load(overrides=overrides_doc(_map("romm/pico", "esde/gb", relation=NARROW_MATCH)))
        assert not names_one_system("romm", "pico", table)

    def test_a_key_the_table_does_not_place_names_none(self):
        assert not names_one_system("romm", "dc", _load())


class TestCitationsNameEachLineOnce:
    """#784 D38: compound citations are deduplicated per '; '-fragment, in order."""

    def test_a_key_on_two_rows_names_a_line_both_cite_once(self):
        table = fixture_table()
        boc = [m for m in table["mappings"] if (m["source"], m["key"]) == ("batocera", "megadrive")]
        boc[0]["citation"], boc[1]["citation"] = "m:1; m:2", "m:1"
        answer = translate([("esde", "genesis")], _load(table))
        assert _citations(answer, "batocera", "megadrive") == ("m:1", "m:2")

    def test_a_fold_keeps_the_new_lines_of_a_citation_that_repeats_some(self):
        table = fixture_table()
        table["mappings"][9]["citation"] = "romm:file:1; romm:file:3"  # romm/genesis on esde:megadrive
        loaded = _load(table, overrides=overrides_doc(_merge("esde/genesis", "esde/megadrive", citation="merge line")))
        # Read off the loaded placement: the answer folds its citations again, which would hide a whole-string fold.
        (placement,) = loaded.placements[("romm", "genesis")].values()
        assert placement.citations == ("romm:file:1", "c", "merge line", "romm:file:3")

    def test_no_citation_in_the_packaged_table_names_a_line_twice(self):
        raw = json.loads((Path(atlas.__file__).parent / "data" / "platform_table.json").read_text(encoding="utf-8"))
        cited = [m["citation"] for m in raw["mappings"]] + [c for keys in raw["keys"].values() for c in keys.values()]
        assert [c for c in cited if len(c.split("; ")) != len(set(c.split("; ")))] == []

    def test_no_single_key_answer_names_a_line_twice(self):
        table = platform_table()
        repeated = []
        for source, key in table.placements:
            for s in translate([(source, key)]).pairs[0].sources:
                for k in s.keys:
                    fragments = [f for c in k.citations for f in c.split("; ")]
                    if len(fragments) != len(set(fragments)):
                        repeated.append((source, key, s.source, k.key))
        assert repeated == []


class TestTheMultiPairQuestion:
    def test_pairs_that_share_no_system_match_nothing_and_lift_each_unknown_pairs_caveat(self):
        handle = atlas.RetroDeck("/home/deck", _retrodeck_machine())
        answer = handle.systems_for_platforms([("esde", "sgb"), ("esde", "gbc"), ("romm", "bubble")])
        assert answer.translation.intersection_status == INTERSECTION_CONFLICT
        assert answer.matches == ()
        assert [c for c in answer.caveats if c.code in UNKNOWN_PAIR_CODES] == list(answer.translation.caveats)

    def test_the_single_pair_form_is_the_multi_pair_form_with_one_pair(self):
        handle = atlas.RetroDeck("/home/deck", _retrodeck_machine())
        assert handle.systems_for_platform("romm", "snes") == handle.systems_for_platforms([("romm", "snes")])

    def test_a_name_without_snapshot_tags_still_translates_through_every_catalogue_column(self, monkeypatch):
        # primehack is RetroDECK's own name; with no snapshot tags for it, the reverse question still reads
        # RetroDECK's column instead of answering unknown from ES-DE's alone.
        monkeypatch.setattr("atlas.installations.vocabulary_platform_tags", lambda system: None)
        answer = atlas.RetroDeck("/home/deck", _retrodeck_machine()).platform_ids("primehack")
        assert answer.translation.pairs[0].source == "retrodeck"
        assert answer.translation.intersection_status == INTERSECTION_AGREED
        assert [c.code for c in answer.caveats if c.code in UNKNOWN_PAIR_CODES] == []

    def test_every_installation_answers_the_pairs(self):
        machine = _retrodeck_machine()
        every = atlas.EveryInstallation(atlas.detect("/home/deck", machine) * 2)
        answers = every.systems_for_platforms([("romm", "snes"), ("folder", "snes")])
        assert [[p.key for p in a.answer.translation.pairs] for a in answers] == [["snes", "snes"], ["snes", "snes"]]

    def test_no_source_or_key_contains_the_clis_separators(self):
        # The CLI splits SOURCE:KEY@VERSION once on each, which holds only while no key or source contains either.
        table = platform_table()
        spelled = [*table.sources, *(key for keys in table.keys.values() for key in keys)]
        assert [s for s in spelled if ":" in s or "@" in s] == []

    @pytest.mark.parametrize("version", ["v3.5.0", "5.4.1"])
    def test_one_projects_version_on_the_command_line_matches_a_column_two_projects_fill(self, capsys, version):
        # ScreenScraper's column is read from ES-DE's map and RomM's list; either pinned version is the table's (D42).
        argv = ["systems-for-platforms", "--pair", f"screenscraper:1@{version}", "--installation", "retrodeck"]
        assert run(argv, home="/home/deck", machine=_retrodeck_machine()) == 0
        caveats = json.loads(capsys.readouterr().out)["translation"]["caveats"]
        assert [c["code"] for c in caveats] == []

    def test_a_pair_on_the_command_line_is_source_colon_key_at_version(self, capsys):
        argv = ["systems-for-platforms", "--pair", "romm:genesis", "--pair", "folder:megadrive@1.0"]
        assert run([*argv, "--installation", "retrodeck"], home="/home/deck", machine=_retrodeck_machine()) == 0
        pairs = json.loads(capsys.readouterr().out)["translation"]["pairs"]
        assert [(p["source"], p["key"], p["version"]) for p in pairs] == [
            ("romm", "genesis", None),
            ("folder", "megadrive", "1.0"),
        ]

    @pytest.mark.parametrize("raw", ["romm", ":genesis", "romm:", "romm:genesis@"])
    def test_a_malformed_pair_on_the_command_line_is_a_usage_error(self, raw, capsys):
        with pytest.raises(SystemExit) as stopped:
            run(["systems-for-platforms", "--pair", raw], home="/home/deck", machine=_retrodeck_machine())
        assert stopped.value.code == 2
        assert "a pair is SOURCE:KEY or SOURCE:KEY@VERSION" in capsys.readouterr().err
