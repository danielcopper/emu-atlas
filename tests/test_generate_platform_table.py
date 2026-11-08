"""The platform-table generator's readers and joins, exercised at the function level.

``tests/test_platforms.py`` checks the shipped table through the loader; this
checks the script that produces it. Nothing here touches the network: every
source is a few lines written here, in the shape the pinned file has.
"""

from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path

import pytest

from scripts import generate_platform_table as generator
from scripts.generate_platform_table import SourceKey, Table, WikidataItem

ROOT = Path(__file__).resolve().parents[1]


def _block(name: str, licence: str = "MIT", decision: str = "ship", revision: str | None = None) -> str:
    pinned = next(u.revision for u in generator.UPSTREAMS if u.block == name)
    lines = [f"## `{name}` — {name}", "", "- **kind:** k", f"- **revision:** `{pinned if revision is None else revision}`"]
    if licence:
        lines.append(f"- **licence:** {licence}")
    if decision:
        lines.append(f"- **decision:** {decision}")
    return "\n".join(lines) + "\n\n"


def _sources(**changes: dict[str, str]) -> str:
    return "".join(_block(u.block, **changes.get(u.block, {})) for u in generator.UPSTREAMS)


class TestTheSourcesFile:
    def test_the_packaged_file_names_every_upstream_with_a_licence_and_a_decision(self):
        blocks = generator.check_sources((ROOT / "atlas" / "data" / "SOURCES.md").read_text(encoding="utf-8"))
        assert set(blocks) >= {u.block for u in generator.UPSTREAMS}

    @pytest.mark.parametrize("missing", ["licence", "decision"])
    def test_a_block_without_a_licence_or_a_decision_stops_the_generator(self, missing):
        with pytest.raises(SystemExit, match=f"'romm' has no {missing} line"):
            generator.check_sources(_sources(romm={missing: ""}))

    def test_a_block_that_does_not_record_the_pinned_revision_stops_the_generator(self):
        with pytest.raises(SystemExit, match=f"'romm' does not record the pinned revision {generator.ROMM.revision}"):
            generator.check_sources(_sources(romm={"revision": "0000000"}))

    def test_the_packaged_table_names_the_pinned_revisions(self):
        import json

        table = json.loads((ROOT / "atlas" / "data" / "platform_table.json").read_text(encoding="utf-8"))
        # A column fed by two upstreams (screenscraper) lists both, comma-separated, in one order.
        pinned = {u.block: u.revision for u in generator.UPSTREAMS}
        stated = {
            (column, upstream): revision
            for column, entry in table["sources"].items()
            for upstream, revision in zip(entry["upstream"].split(", "), entry["revision"].split(", "), strict=True)
        }
        assert stated == {(column, upstream): pinned[upstream] for column, upstream in stated}

    def test_an_upstream_without_a_block_stops_the_generator(self):
        text = _sources().replace("## `knulli`", "## `knulli-old`")
        with pytest.raises(SystemExit, match="no block for 'knulli'"):
            generator.check_sources(text)


class TestTheEsdeSourceFiles:
    CPP = 'static std::vector<std::string> platformNames {\n    "3do",      // 3DO\n    "snes",\n    "ignore"\n};\n'
    HEADER = "enum PlatformId : unsigned int {\n    THREEDO,\n    SUPER_NINTENDO,\n    PLATFORM_IGNORE\n};\n"

    def test_the_platform_ids_and_their_symbols_are_read_with_their_lines(self):
        names, symbols = generator.esde_platform_ids(self.CPP, self.HEADER)
        assert names == [("3do", 2), ("snes", 3), ("ignore", 4)]
        assert symbols == {"THREEDO": "3do", "SUPER_NINTENDO": "snes", "PLATFORM_IGNORE": "ignore"}

    def test_a_symbol_count_that_differs_from_the_names_stops_the_generator(self):
        with pytest.raises(SystemExit, match="3 enum symbols, 2 names"):
            generator.esde_platform_ids(self.CPP.replace('    "ignore"\n', ""), self.HEADER)

    def test_a_scraper_map_gives_each_platform_its_provider_id_and_line(self):
        cpp = "const std::map<PlatformId, unsigned short> screenscraper_platformid_map {\n    {THREEDO, 29},\n    {SUPER_NINTENDO, \"4\"},\n};\n"
        symbols = {"THREEDO": "3do", "SUPER_NINTENDO": "snes"}
        assert generator.scraper_map(cpp, "screenscraper_platformid_map", symbols) == {"3do": ("29", 2), "snes": ("4", 3)}

    def test_a_map_the_pinned_file_no_longer_has_stops_the_generator(self):
        with pytest.raises(SystemExit, match="pinned source no longer matches: gamesdb_new_platformid_map"):
            generator.scraper_map("", "gamesdb_new_platformid_map", {})


class TestTheReaders:
    def test_live_and_commented_systems_cite_the_line_of_their_name(self):
        text = (
            '<?xml version="1.0"?>\n<systemList>\n  <system>\n    <name>snes</name>\n'
            "    <fullname>Nintendo SNES</fullname>\n    <platform>snes</platform>\n"
            '    <command label="x">%EMULATOR_RETROARCH% -L %CORE_RETROARCH%/snes9x_libretro.so %ROM%</command>\n'
            "  </system>\n  <!--\n  <system>\n    <name>xbox360</name>\n"
            "    <platform>xbox360</platform>\n  </system>\n  -->\n</systemList>\n"
        )
        live = generator.esde_systems(text, "f")
        assert [(k.key, k.citation, k.platforms) for k in live] == [("snes", "f:4", ("snes",))]
        assert live[0].commands[0][1] == 7
        commented = generator.commented_systems(text, "f")
        assert [(k.key, k.citation) for k in commented] == [("xbox360", "f:11 (inside an XML comment)")]

    def test_a_batocera_key_reads_its_flat_fields_and_defaults_its_platform_to_itself(self):
        text = (
            "megadrive:\n  name: Mega Drive\n  hardware: console\n  platform: genesis, megadrive\n"
            "  emulators:\n    libretro:\n      name: not-a-field\n\nsnes:\n  name: SNES\n"
        )
        keys = generator.yaml_systems(text, "b")
        assert [(k.key, k.name, k.hardware, k.platforms, k.citation) for k in keys] == [
            ("megadrive", "Mega Drive", "console", ("genesis", "megadrive"), "b:1"),
            ("snes", "SNES", "", ("snes",), "b:9"),
        ]

    def test_romm_aliases_resolve_through_the_enum(self):
        slugs = 'import enum\nclass UniversalPlatformSlug(enum.StrEnum):\n    GENESIS = "genesis"\n'
        aliases = 'PLATFORM_FS_ALIASES: Final[dict[str, UPS]] = {\n    "megadrive": UPS.GENESIS,\n}\n'
        members = generator.romm_members(slugs)
        assert generator.romm_aliases(aliases, members) == {"megadrive": ("genesis", 2)}
        assert [(k.key, k.citation) for k in generator.romm_slugs(slugs, "r")] == [("genesis", "r:3")]


@pytest.mark.parametrize(
    "one, other, agree",
    [
        ("Atari 7800", "Atari 7800 ProSystem", True),
        ("Arduboy", "Arduboy Miniature Game System", True),
        ("Mega Drive", "Sega Mega Drive", True),
        ("Super Nintendo Entertainment System", "Nintendo SNES (Super Nintendo)", False),
        ("", "Anything", False),
    ],
)
def test_display_names_agree_when_every_word_of_the_shorter_is_in_the_longer(one, other, agree):
    assert generator.names_agree(one, other) is agree


def _esde(table: Table, *systems: tuple[str, str, str]) -> tuple[dict[str, SourceKey], dict[str, set[str]]]:
    keys = [
        SourceKey(name, f"es:{i}", name=full, platforms=generator.platform_tokens(tags), platform_line=i)
        for i, (name, full, tags) in enumerate(systems, start=1)
    ]
    tagged = generator.build_esde(table, keys, [(t, 0) for _, _, t in systems], "es")
    return {k.key: k for k in keys}, tagged


ESDE = (
    ("genesis", "Sega Genesis", "genesis"),
    ("megadrive", "Sega Mega Drive", "megadrive"),
    ("megadrivejp", "Sega Mega Drive", "megadrive"),
    ("naomi", "Sega NAOMI", "arcade"),
    ("mame", "Multiple Arcade Machine Emulator", "arcade"),
    ("dos", "DOS (PC)", "dos"),
    ("pc", "IBM PC", "pc"),
)


class TestTheBatoceraFamilyJoins:
    def _build(self, column: str, *keys: SourceKey, batocera: tuple[SourceKey, ...] = ()) -> Table:
        table = Table()
        esde, tagged = _esde(table, *ESDE)
        generator.build_batocera_family(table, "batocera", list(batocera), esde, tagged, {})
        by_key = {k.key: k for k in batocera}
        if column != "batocera":
            generator.build_batocera_family(table, column, list(keys), esde, tagged, by_key)
        else:
            generator.build_batocera_family(table, column, list(keys), esde, tagged, {})
        return table

    def _placed(self, table: Table, column: str, key: str) -> list[tuple[str, str]]:
        return sorted((p.row, p.via) for p in table.placements[(column, key)])

    def test_a_same_spelling_whose_names_and_list_agree_is_the_es_de_system(self):
        table = self._build("batocera", SourceKey("dos", "b:1", name="DOS", platforms=("pc",)))
        # `pc` is a family tag: it says how the key scrapes, not what it holds.
        assert self._placed(table, "batocera", "dos") == [("esde:dos", "same-name+platform-list")]

    def test_a_platform_list_that_reaches_further_makes_the_key_broad(self):
        key = SourceKey("megadrive", "b:1", name="Mega Drive", platforms=("genesis", "megadrive"))
        table = self._build("batocera", key)
        assert self._placed(table, "batocera", "megadrive") == [
            ("esde:genesis", "platform-list-broad"),
            ("esde:megadrive", "platform-list-broad"),
            ("esde:megadrivejp", "platform-list-broad"),
        ]

    def test_the_arcade_family_tag_reaches_nothing(self):
        key = SourceKey("naomi", "b:1", name="Naomi", platforms=("naomi", "arcade"))
        table = self._build("batocera", key)
        assert self._placed(table, "batocera", "naomi") == [("esde:naomi", "same-name+platform-list")]

    def test_a_same_spelling_whose_names_disagree_is_a_row_of_its_own_and_a_finding(self):
        table = self._build("batocera", SourceKey("mame", "b:1", name="Mame", platforms=("arcade",)))
        assert self._placed(table, "batocera", "mame") == [("batocera:mame", "own-row")]
        assert table.findings["names-disagree"]

    def test_a_knulli_key_spelled_as_a_batocera_key_shares_its_rows(self):
        batocera = SourceKey("jaguar", "b:1", name="Jaguar", platforms=("jaguar",))
        knulli = SourceKey("jaguar", "k:1", name="Jaguar", platforms=("jaguar",))
        table = self._build("knulli", knulli, batocera=(batocera,))
        assert self._placed(table, "knulli", "jaguar") == [
            ("batocera:jaguar", "same-name+platform-list (batocera)")
        ]

    def test_a_knulli_key_whose_list_differs_from_batoceras_is_its_own_row(self):
        batocera = SourceKey("c128", "b:1", name="Commodore 128", platforms=("c64",))
        rocknix = SourceKey("c128", "r:1", name="Commodore 128", platforms=("c128",))
        table = self._build("rocknix", rocknix, batocera=(batocera,))
        assert self._placed(table, "rocknix", "c128") == [("rocknix:c128", "own-row")]


class TestTheRommJoins:
    def _build(self) -> Table:
        table = Table()
        esde, tagged = _esde(table, *ESDE)
        generator.build_batocera_family(
            table, "batocera", [SourceKey("pico", "b:1", name="Sega Pico")], esde, tagged, {}
        )
        slugs = [SourceKey(s, f"u:{i}") for i, s in enumerate(("genesis", "pico", "altair-8800"))]
        aliases = {"megadrive": ("genesis", 10), "megadrivejp": ("genesis", 11)}
        generator.build_romm(table, slugs, aliases, "romm")
        return table

    def test_a_slug_covers_the_rows_of_every_folder_that_resolves_to_it(self):
        assert sorted(self._build().rows_of("romm", "genesis")) == [
            "esde:genesis",
            "esde:megadrive",
            "esde:megadrivejp",
        ]

    def test_a_folder_spelled_as_a_slug_joins_a_deployment_folder_only(self):
        # Batocera's `pico` is the Sega Pico, RomM's `pico` is PICO-8: no join.
        table = self._build()
        assert table.rows_of("romm", "pico") == ["romm:pico"]
        assert table.rows_of("batocera", "pico") == ["batocera:pico"]

    def test_a_slug_no_folder_reaches_is_its_own_row(self):
        table = self._build()
        assert table.rows_of("romm", "altair-8800") == ["romm:altair-8800"]
        assert table.rows_of("romm-folder", "altair-8800") == ["romm:altair-8800"]

    def test_a_folder_spelled_as_its_slug_cites_the_slugs_line_once(self):
        placements = self._build().placements[("romm", "genesis")]
        assert {p.row: p.citation for p in placements} == {
            "esde:genesis": "u:0",
            "esde:megadrive": "u:0; romm:backend/utils/platform_aliases.py:10",
            "esde:megadrivejp": "u:0; romm:backend/utils/platform_aliases.py:11",
        }

    def test_an_alias_folder_is_the_frontend_folder_of_its_spelling(self):
        assert self._build().rows_of("romm-folder", "megadrivejp") == ["esde:megadrivejp"]


def _tar(files: dict[str, str]) -> bytes:
    import tarfile

    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as tar:
        for name, text in files.items():
            data = text.encode("utf-8")
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return buffer.getvalue()


class TestTheLibretroJoin:
    def test_a_missing_info_and_an_info_without_a_database_are_two_findings(self):
        archive = _tar({"info/fbalpha2012_libretro.info": '# database = ""\ndisplay_name = "FB Alpha 2012"\n'})
        databases = generator.core_databases(archive)
        assert databases == {"fbalpha2012": ((), 0)}
        table = Table()
        systems = [
            SourceKey("doom", "es:1", commands=(("-L %CORE_RETROARCH%/boom3_xp_libretro.so", 7),)),
            SourceKey("fba", "es:2", commands=(("-L %CORE_RETROARCH%/fbalpha2012_libretro.so", 9),)),
        ]
        generator.build_libretro(table, systems, databases)
        path = generator.ESDE_SYSTEMS_PATH
        assert table.findings["core-without-info"] == [f"doom: boom3_xp_libretro.so (es-de:{path}:7) has no .info at the pin"]
        assert table.findings["core-declares-no-database"] == [
            f"fba: fbalpha2012_libretro.info declares no database (es-de:{path}:9)"
        ]

    def test_an_infos_database_names_are_read_with_their_line(self):
        archive = _tar({"info/gpgx_libretro.info": 'display_name = "x"\ndatabase = "Sega - Mega Drive - Genesis|Sega - Game Gear"\n'})
        assert generator.core_databases(archive) == {"gpgx": (("Sega - Mega Drive - Genesis", "Sega - Game Gear"), 2)}

    def test_a_name_maps_where_every_declaring_core_of_the_system_declares_it(self):
        table = Table()
        systems = [
            SourceKey("megadrive", "es:1", commands=(("-L %CORE_RETROARCH%/gpgx_libretro.so", 5), ("-L %CORE_RETROARCH%/blastem_libretro.so", 6))),
            SourceKey("arcade", "es:2", commands=(("-L %CORE_RETROARCH%/mame_libretro.so", 9), ("-L %CORE_RETROARCH%/fbneo_libretro.so", 10))),
        ]
        databases = {
            "gpgx": (("Sega - Mega Drive - Genesis", "Sega - Game Gear"), 17),
            "blastem": (("Sega - Mega Drive - Genesis",), 17),
            "mame": (("MAME",), 18),
            "fbneo": (("FBNeo - Arcade Games",), 31),
        }
        generator.build_libretro(table, systems, databases)
        assert table.findings["core-without-info"] == []
        assert table.rows_of("libretro", "Sega - Mega Drive - Genesis") == ["esde:megadrive"]
        assert table.rows_of("libretro", "Sega - Game Gear") == []
        assert table.rows_of("libretro", "MAME") == []
        assert set(table.keys["libretro"]) == {"Sega - Mega Drive - Genesis", "Sega - Game Gear", "MAME", "FBNeo - Arcade Games"}


def _item(qid: str, ss: tuple[str, ...] = (), tgdb: tuple[str, ...] = (), igdb: tuple[str, ...] = ()) -> WikidataItem:
    return WikidataItem(qid, 7, {"igdb": igdb, "screenscraper": ss, "thegamesdb": tgdb})


IGDB_TABLE = (
    "ARCADE_SS_ID: Final = 75\n"
    "CPS1_SS_ID: Final = 6\n"
    "IGDB_PLATFORM_LIST: dict[UPS, SlugToIGDB] = {\n"
    "    UPS.SNES: {\n"
    '        "id": 19,\n'
    '        "slug": "snes",\n'
    "    },\n"
    "}\n"
    "SCREENSAVER_PLATFORM_LIST: dict[UPS, SlugToSSId] = {\n"
    '    UPS.CPS1: {"id": CPS1_SS_ID, "name": "Capcom Play System"},\n'
    "}\n"
    "def newer_syntax():\n"
    "    try:\n"
    "        pass\n"
    "    except ValueError, TypeError:\n"
    "        pass\n"
)


class _Answers:
    """A fetcher stand-in: each URL answers the next of the given JSON documents."""

    def __init__(self, *documents: object) -> None:
        self.documents = list(documents)
        self.urls: list[str] = []

    def json(self, url: str) -> object:
        self.urls.append(url)
        return self.documents.pop(0)


class TestTheWikidataPin:
    def test_the_items_are_read_at_their_pinned_revisions(self):
        content = json.dumps({"claims": {"P7757": [{"rank": "normal", "mainsnak": {"datavalue": {"value": "3"}}}]}})
        answers = _Answers({"query": {"pages": [{"title": "Q1", "revisions": [{"revid": 11, "slots": {"main": {"content": content}}}]}]}})
        items = generator.wikidata_items(answers, {"Q1": 11})  # type: ignore[arg-type]
        assert items["Q1"].revision == 11
        assert items["Q1"].values["screenscraper"] == ("3",)
        assert "revids=11" in answers.urls[0]

    def test_a_revision_of_another_item_stops_the_generator(self):
        answers = _Answers({"query": {"pages": [{"title": "Q2", "revisions": [{"revid": 11, "slots": {"main": {"content": "{}"}}}]}]}})
        with pytest.raises(SystemExit, match="belongs to Q2, not Q1"):
            generator.wikidata_items(answers, {"Q1": 11})  # type: ignore[arg-type]

    def test_no_pinned_item_stops_the_generator(self):
        with pytest.raises(SystemExit, match="pins no Wikidata item"):
            generator.wikidata_items(_Answers(), {})  # type: ignore[arg-type]

    def test_a_query_answering_no_item_is_not_pinned(self):
        with pytest.raises(SystemExit, match="answered no item"):
            generator.repin_wikidata(_Answers({"results": {"bindings": []}}))  # type: ignore[arg-type]

    def test_the_pin_files_digest_is_the_revision_sources_md_records(self):
        text = (ROOT / "scripts" / "wikidata_revisions.json").read_text(encoding="utf-8")
        assert generator.WIKIDATA.revision == hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]
        assert generator.WIKIDATA.version == json.loads(text)["as_of"]


class TestTheFetcher:
    def test_the_cache_file_is_the_urls_digest_and_is_written_whole(self, tmp_path, monkeypatch):
        calls: list[str] = []

        class Response(io.BytesIO):
            def __enter__(self):
                return self

            def __exit__(self, *_):
                return False

        def urlopen(request, timeout):
            calls.append(request.full_url)
            return Response(b"payload")

        monkeypatch.setattr(generator.urllib.request, "urlopen", urlopen)
        url = "https://example.invalid/" + "x" * 400
        fetcher = generator.Fetcher(tmp_path)
        assert fetcher.bytes(url) == b"payload"
        assert [p.name for p in tmp_path.iterdir()] == [hashlib.sha256(url.encode("utf-8")).hexdigest()]
        assert fetcher.bytes(url) == b"payload"
        assert calls == [url]


def test_every_run_prints_a_count_per_kind_of_finding():
    findings = {"names-disagree": ["a", "b"], "core-without-info": ["c"]}
    assert generator.finding_counts(findings) == ["finding core-without-info: 1", "finding names-disagree: 2"]


class TestTheIdColumns:
    """D21 (changed 2026-11-08): the ids are ES-DE's and RomM's; Wikidata only seconds them."""

    def _table(self) -> Table:
        table = Table()
        _esde(table, ("nes", "Nintendo NES", "nes"), ("cps1", "Capcom Play System I", "arcade"))
        for slug, row in (("nes", "esde:nes"), ("cps1", "esde:cps1")):
            table.place("romm", slug, row, "romm-resolve", "u")
        return table

    def test_romms_tables_are_read_by_member_with_their_named_constants(self):
        members = {"SNES": "snes", "CPS1": "cps1"}
        assert generator.romm_igdb(IGDB_TABLE, members) == {"snes": ("19", "snes", 5)}
        assert generator.romm_screenscraper(IGDB_TABLE, members) == {"cps1": ("6", 10)}

    def test_es_des_map_places_the_id_on_every_system_carrying_the_tag(self):
        table = self._table()
        maps = {"screenscraper": {"nes": ("3", 96), "arcade": ("75", 38)}, "thegamesdb": {"nes": ("7", 90)}}
        tagged = {"nes": {"nes"}, "arcade": {"cps1"}}
        generator.build_esde_ids(table, tagged, maps, generator.corroborating_items({"Q1": _item("Q1", ss=("3",))}))
        assert table.rows_of("screenscraper", "75") == ["esde:cps1"]
        assert table.rows_of("thegamesdb", "7") == ["esde:nes"]
        cited = table.placements[("screenscraper", "3")][0].citation
        assert cited == "es-de:es-app/src/scrapers/ScreenScraper.cpp:96 (nes); wikidata:Q1 P7757=3 (revision 7)"
        assert table.findings["not-on-wikidata (thegamesdb)"] == ["7: es-de:es-app/src/scrapers/GamesDBJSONScraper.cpp:90 (nes)"]

    def test_romms_lists_place_the_numeric_igdb_id_and_the_ss_id_on_the_slugs_rows(self):
        table = self._table()
        corroboration = generator.corroborating_items({"Q2": _item("Q2", igdb=("nes",))})
        generator.build_romm_ids(table, {"nes": ("18", "nes", 300)}, {"cps1": ("6", 1475)}, corroboration)
        assert table.rows_of("igdb", "18") == table.rows_of("igdb", "nes") == ["esde:nes"]
        assert table.rows_of("screenscraper", "6") == ["esde:cps1"]
        assert table.keys["igdb"]["18"] == table.keys["igdb"]["nes"] == (
            "romm:backend/adapters/services/igdb.py:300 (id 18, slug nes)"
        )
        # Wikidata holds IGDB slugs, so the slug is what seconds the numeric id.
        assert table.placements[("igdb", "18")][0].citation.endswith("wikidata:Q2 P5795=nes (revision 7)")

    def test_an_id_no_system_carries_is_a_row_of_its_own(self):
        table = self._table()
        generator.build_esde_ids(table, {}, {"screenscraper": {"naomi": ("56", 92)}, "thegamesdb": {}}, {})
        generator.unplaced_rows(table)
        assert table.rows_of("screenscraper", "56") == ["screenscraper:56"]


def test_relations_follow_the_size_of_the_row_set_and_entries_are_open_ended():
    table = Table()
    table.rows |= {"a", "b"}
    table.place("romm", "x", "a", "v", "c")
    table.place("romm", "x", "b", "v", "c")
    table.place("romm", "y", "a", "v", "c")
    out = generator.mappings(table)
    assert [(m["key"], m["relation"], m["valid_from"], m["valid_to"]) for m in out] == [
        ("x", "broadMatch", "5.4.1", None),
        ("x", "broadMatch", "5.4.1", None),
        ("y", "exactMatch", "5.4.1", None),
    ]
