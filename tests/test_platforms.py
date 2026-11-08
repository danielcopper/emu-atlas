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
from typing import Any

import pytest

import atlas
from atlas.cli import run
from atlas.contract import platform_systems_contract, platform_translation_contract, system_platforms_contract
from atlas.machine import FixtureMachine
from atlas.platforms import (
    BROAD_MATCH,
    CAVEAT_PLATFORM_SOURCE_UNKNOWN,
    CAVEAT_PLATFORM_SOURCE_VERSION_DIFFERS,
    CAVEAT_PLATFORM_UNMAPPED,
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
    PlatformKey,
    PlatformPairAnswer,
    PlatformSourceKeys,
    PlatformTranslation,
    exact_platform_tags,
    known_platform_vocabularies,
    load_platform_table,
    platform_table,
    systems_named,
    translate,
)

# --- a small table to load ------------------------------------------------------------

SOURCES = {
    "esde": {"upstream": "es-de", "version": "v1", "revision": "r1"},
    "batocera": {"upstream": "batocera", "version": "b1", "revision": "r2"},
    "romm": {"upstream": "romm", "version": "m1", "revision": "r3"},
}
# The columns the questions read by name (ES-DE's platform ids, every folder
# vocabulary) must be in any table the loader takes; here each holds one key on
# a row of its own, so the tests above stay about the three sources they ask.
REQUIRED = {
    source: {"upstream": source, "version": "x1", "revision": "r9"}
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
        "valid_from": {**SOURCES, **REQUIRED}[source]["version"],
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
        assert dict(table.sources) == {"esde": "v1", "batocera": "b1", "romm": "m1", **{s: "x1" for s in REQUIRED}}

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
        sources = {**SOURCES, "folder": {"upstream": "x", "version": "1", "revision": "r"}}
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

    def test_a_kind_override_names_a_kind_and_a_subset(self):
        table = _load(overrides=overrides_doc(kind_override("romm/pico", "fantasy-console", subset_of="esde/gb")))
        assert table.kinds["romm:pico"] == "fantasy-console"
        assert table.subset_of["romm:pico"] == "esde:gb"

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
        assert {v.source: v.version for v in answer.source_versions} == dict(platform_table().sources)

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
        assert dict(answer.caveats[1].data) == {"source": "mobygames", "sources": ", ".join(_load().sources)}

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
        assert dict(answer.caveats[0].data) == {"source": "romm", "key": "gb", "version": "m2", "pinned": "m1"}
        assert answer.pairs[0].version == "m2"

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

    def test_the_asked_value_is_echoed_as_it_is_matched(self):
        answer = atlas.RetroDeck("/home/deck", FixtureMachine({})).systems_for_platform("romm", " gb ")
        assert answer.value == answer.translation.pairs[0].key == "gb"
