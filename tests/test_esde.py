"""Tests for atlas.esde — catalogue parsing, merge semantics, entry handles."""

from __future__ import annotations

import atlas
from atlas.esde import merge_layers, parse_es_systems

HOME = "/home/deck"
RETRODECK_JSON = f"{HOME}/.var/app/net.retrodeck.retrodeck/config/retrodeck/retrodeck.json"
RETRODECK_CFG = f"{HOME}/.var/app/net.retrodeck.retrodeck/config/retroarch/retroarch.cfg"
OPTIONS_CFG = f"{HOME}/.var/app/net.retrodeck.retrodeck/config/retroarch/retroarch-core-options.cfg"

RD_JSON = '{"paths": {"rd_home_path": "/mnt/sd/retrodeck", "saves_path": "/mnt/sd/retrodeck/saves"}}'
DEPLOY = "/var/lib/flatpak/app/net.retrodeck.retrodeck/current/active/files"
BUNDLED_ESDE = f"{DEPLOY}/retrodeck/components/es-de/share/es-de/resources/systems/linux/es_systems.xml"
CUSTOM_ESDE = "/mnt/sd/retrodeck/ES-DE/custom_systems/es_systems.xml"

BUNDLED_XML = """<?xml version="1.0"?>
<systemList>
  <system>
    <name>dreamcast</name>
    <path>%ROMPATH%/dreamcast</path>
    <command label="Flycast">%EMULATOR_RETROARCH% -L %CORE_RETROARCH%/flycast_libretro.so %ROM%</command>
  </system>
  <system>
    <name>n64</name>
    <path>%ROMPATH%/n64</path>
    <command label="Mupen64Plus-Next">%EMULATOR_RETROARCH% -L %CORE_RETROARCH%/mupen64plus_next_libretro.so %ROM%</command>
    <command label="ParaLLEl N64">%EMULATOR_RETROARCH% -L %CORE_RETROARCH%/parallel_n64_libretro.so %ROM%</command>
  </system>
  <system>
    <name>ps3</name>
    <path>%ROMPATH%/ps3</path>
    <command label="RPCS3 Directory (Standalone)">%EMULATOR_RPCS3% --no-gui %ROM%</command>
  </system>
</systemList>
"""


class TestParse:
    def test_systems_and_order(self):
        parsed = parse_es_systems(BUNDLED_XML, source="test")
        assert set(parsed) == {"dreamcast", "n64", "ps3"}
        assert [e.label for e in parsed["n64"]] == ["Mupen64Plus-Next", "ParaLLEl N64"]

    def test_libretro_classification_extracts_core_so(self):
        parsed = parse_es_systems(BUNDLED_XML, source="test")
        entry = parsed["dreamcast"][0]
        assert entry.kind == atlas.KIND_LIBRETRO
        assert entry.core_so == "flycast_libretro.so"

    def test_standalone_classification(self):
        parsed = parse_es_systems(BUNDLED_XML, source="test")
        entry = parsed["ps3"][0]
        assert entry.kind == atlas.KIND_STANDALONE
        assert entry.core_so is None

    def test_malformed_xml_is_skipped_layer(self):
        assert parse_es_systems("<systemList><system>", source="test") == {}

    def test_commented_out_systems_yield_nothing(self):
        # RetroDECK ships a custom_systems overlay that is entirely commented out.
        text = '<?xml version="1.0"?>\n<systemList>\n<!-- <system><name>x</name></system> -->\n</systemList>'
        assert parse_es_systems(text, source="test") == {}


class TestMerge:
    def test_custom_replaces_bundled_system(self):
        bundled = parse_es_systems(BUNDLED_XML, source="bundled")
        custom = parse_es_systems(
            '<systemList><system><name>n64</name>'
            '<command label="ParaLLEl N64">%EMULATOR_RETROARCH% -L %CORE_RETROARCH%/parallel_n64_libretro.so %ROM%</command>'
            "</system></systemList>",
            source="custom",
        )
        merged = merge_layers(bundled, custom)
        assert [e.label for e in merged["n64"]] == ["ParaLLEl N64"]
        assert merged["n64"][0].source == "custom"
        assert "dreamcast" in merged  # untouched systems stay

    def test_custom_adds_new_system(self):
        merged = merge_layers(
            parse_es_systems(BUNDLED_XML, source="bundled"),
            parse_es_systems(
                '<systemList><system><name>mysystem</name>'
                "<command>%EMULATOR_SOMETHING% %ROM%</command></system></systemList>",
                source="custom",
            ),
        )
        assert "mysystem" in merged


def _entries(answer):
    """The entries of a catalogue answer, asserting it had nothing to caveat.

    These tests are about which emulators the catalogue declares and in what
    order; an answer that carried a caveat would mean the catalogue was not
    read at all, and comparing a shorter list would hide that.
    """
    assert not answer.caveats, answer.caveats
    return answer.entries


def _retrodeck(files, **kwargs):
    machine = atlas.FixtureMachine(files, **kwargs)
    return atlas.RetroDeck(HOME, machine)


# The roots `RD_JSON` names, so the fixture models a *working* installation.
# These tests are about what the catalogue declares; every answer from a broken
# installation now carries its health findings, and a fixture that was broken
# only by omission would put them in the way of every assertion here.
CATALOGUE_ROOTS = ["/mnt/sd/retrodeck", "/mnt/sd/retrodeck/saves"]


def _catalogue_fixture(extra_files=None, **kwargs):
    files = {RETRODECK_JSON: RD_JSON, BUNDLED_ESDE: BUNDLED_XML}
    files.update(extra_files or {})
    kwargs.setdefault("dirs", CATALOGUE_ROOTS)
    return _retrodeck(files, **kwargs)


class TestRetroDeckCatalogue:
    def test_emulators_for_declared_order(self):
        rd = _catalogue_fixture()
        entries = _entries(rd.emulators_for("n64"))
        assert [e.label for e in entries] == ["Mupen64Plus-Next", "ParaLLEl N64"]
        assert entries[0].core_so == "mupen64plus_next_libretro.so"

    def test_unknown_system_is_empty(self):
        # The catalogue was read and declares no emulator for this system —
        # an emptiness that is an answer about the machine, so nothing to
        # caveat alongside it.
        answer = _catalogue_fixture().emulators_for("does-not-exist")
        assert answer.entries == ()
        assert answer.caveats == ()

    def test_no_esde_at_all_says_nobody_could_look(self):
        # Without the bundled es_systems.xml there is no catalogue to read, and
        # a bare empty tuple would spell that exactly like a frontend that
        # knows no emulators at all. The caveat is the difference.
        rd = _retrodeck({RETRODECK_JSON: RD_JSON}, dirs=CATALOGUE_ROOTS)
        answer = rd.emulators_for("n64")
        assert answer.entries == ()
        assert [c.code for c in answer.caveats] == [atlas.CAVEAT_CATALOGUE_UNREADABLE]
        listing = rd.systems()
        assert listing.systems == ()
        assert [c.code for c in listing.caveats] == [atlas.CAVEAT_CATALOGUE_UNREADABLE]

    def test_systems_listing(self):
        assert _catalogue_fixture().systems().systems == ("dreamcast", "n64", "ps3")

    def test_custom_overlay_overrides(self):
        rd = _catalogue_fixture(
            {
                CUSTOM_ESDE: '<systemList><system><name>dreamcast</name>'
                '<command label="Custom Flycast">%EMULATOR_RETROARCH% -L %CORE_RETROARCH%/flycast_libretro.so %ROM%</command>'
                "</system></systemList>"
            }
        )
        entries = _entries(rd.emulators_for("dreamcast"))
        assert [e.label for e in entries] == ["Custom Flycast"]
        assert entries[0].source == "es_systems.xml (custom_systems overlay)"


class TestEntrySaveLocation:
    def test_full_circle_dreamcast_entry_hits_the_rule_card(self):
        # catalogue -> default entry -> save_location: core known, card applies,
        # the no-core caveat class does not exist on this path.
        rd = _catalogue_fixture(
            {
                RETRODECK_CFG: (
                    'savefile_directory = "/mnt/sd/retrodeck/saves"\n'
                    'system_directory = "/mnt/sd/retrodeck/bios"\n'
                    'global_core_options = "true"\n'
                    'libretro_directory = "/app/cores"\n'
                ),
                OPTIONS_CFG: 'reicast_per_content_vmus = "disabled"\n',
                "/mnt/sd/retrodeck/roms/dreamcast/Dreamcast Game (Europe).gdi": "",
                "/mnt/sd/retrodeck/bios/dc/vmu_save_A1.bin": "v",
            },
            cores={f"{DEPLOY}/cores/flycast_libretro.so": {"library_name": "Flycast"}},
        )
        entry = _entries(rd.emulators_for("dreamcast"))[0]
        p = entry.save_location(content_path="/mnt/sd/retrodeck/roms/dreamcast/Dreamcast Game (Europe).gdi")
        assert isinstance(p, atlas.SavePlacement)
        assert p.dir == "/mnt/sd/retrodeck/bios/dc"
        assert p.root_kind == atlas.ROOT_SYSTEM_DIRECTORY
        assert not any(c.code == atlas.CAVEAT_NO_CORE for c in p.caveats)
        assert p.granularity is not None
        assert p.granularity.value == "shared-card"

    def test_standalone_entry_is_a_domain_outcome(self):
        # Outside the resolver's coverage is an answer, not an exception (M8).
        rd = _catalogue_fixture()
        entry = _entries(rd.emulators_for("ps3"))[0]
        outcome = entry.save_location(content_path="/mnt/sd/retrodeck/roms/ps3/game")
        assert isinstance(outcome, atlas.Unresolved)
        assert outcome.code == atlas.UNRESOLVED_STANDALONE
        assert outcome.data["system"] == "ps3"


class TestGamelistAlternative:
    # The real ES-DE gamelist quirk: two root elements, not well-formed XML.
    REAL_SAMPLE = (
        '<?xml version="1.0"?>\n'
        "<alternativeEmulator>\n\t<label>ParaLLEl N64</label>\n</alternativeEmulator>\n"
        "<gameList />\n"
    )
    # The standards-compliant location, observed live on RetroDECK 0.10.9b.
    NESTED_SAMPLE = (
        '<?xml version="1.0"?>\n'
        "<gameList>\n"
        "  <alternativeEmulator>\n    <label>ParaLLEl N64</label>\n  </alternativeEmulator>\n"
        "</gameList>\n"
    )

    def test_parses_the_real_two_root_quirk(self):
        assert atlas.parse_gamelist_alternative(self.REAL_SAMPLE) == "ParaLLEl N64"

    def test_parses_the_nested_shape(self):
        assert atlas.parse_gamelist_alternative(self.NESTED_SAMPLE) == "ParaLLEl N64"

    def test_document_level_wins_over_nested(self):
        # ES-DE takes the document-level element when both exist, label or not.
        both = (
            "<alternativeEmulator><label>ParaLLEl N64</label></alternativeEmulator>"
            "<gameList><alternativeEmulator><label>Mupen64Plus-Next</label></alternativeEmulator></gameList>"
        )
        assert atlas.parse_gamelist_alternative(both) == "ParaLLEl N64"

    def test_labelless_document_level_element_states_nothing(self):
        # ES-DE picks the document-level element, then reads its label — an
        # empty one selects nothing, it does not fall back to the nested one.
        both = (
            "<alternativeEmulator />"
            "<gameList><alternativeEmulator><label>ParaLLEl N64</label></alternativeEmulator></gameList>"
        )
        assert atlas.parse_gamelist_alternative(both) is None

    def test_selection_inside_a_game_is_not_a_system_selection(self):
        # Neither reader looks there; a game's own choice is <altemulator>.
        text = (
            "<gameList><game><path>./Fixture Game.zip</path>"
            "<alternativeEmulator><label>ParaLLEl N64</label></alternativeEmulator>"
            "</game></gameList>"
        )
        assert atlas.parse_gamelist_alternative(text) is None

    def test_absent_selection_is_none(self):
        assert atlas.parse_gamelist_alternative('<?xml version="1.0"?>\n<gameList />') is None

    def test_malformed_is_none_never_guessed(self):
        assert atlas.parse_gamelist_alternative("<alternativeEmulator><label>") is None

    def test_selection_promotes_entry_to_default(self):
        rd = _catalogue_fixture(
            {"/mnt/sd/retrodeck/ES-DE/gamelists/n64/gamelist.xml": self.REAL_SAMPLE}
        )
        entries = _entries(rd.emulators_for("n64"))
        assert [e.label for e in entries] == ["ParaLLEl N64", "Mupen64Plus-Next"]
        assert entries[0].selection == 'gamelist.xml: alternativeEmulator = "ParaLLEl N64"'
        assert entries[1].selection is None

    def test_nested_selection_promotes_entry_to_default(self):
        # The live shape: what emulators_for answers must not depend on where
        # ES-DE happened to write the element.
        rd = _catalogue_fixture(
            {"/mnt/sd/retrodeck/ES-DE/gamelists/n64/gamelist.xml": self.NESTED_SAMPLE}
        )
        entries = _entries(rd.emulators_for("n64"))
        assert [e.label for e in entries] == ["ParaLLEl N64", "Mupen64Plus-Next"]
        assert entries[0].selection == 'gamelist.xml: alternativeEmulator = "ParaLLEl N64"'

    def test_unmatched_selection_keeps_declared_order(self):
        # ES-DE falls back to the declared default on an unknown label; so does atlas.
        rd = _catalogue_fixture(
            {
                "/mnt/sd/retrodeck/ES-DE/gamelists/n64/gamelist.xml": (
                    "<alternativeEmulator><label>Gone Emulator</label></alternativeEmulator><gameList />"
                )
            }
        )
        entries = _entries(rd.emulators_for("n64"))
        assert [e.label for e in entries] == ["Mupen64Plus-Next", "ParaLLEl N64"]
        assert all(e.selection is None for e in entries)


class TestPerGameAltemulator:
    GAMELIST = (
        '<?xml version="1.0"?>\n'
        "<alternativeEmulator>\n\t<label>ParaLLEl N64</label>\n</alternativeEmulator>\n"
        "<gameList>\n"
        "\t<game>\n\t\t<path>./Paper Mario (USA).zip</path>\n"
        "\t\t<altemulator>Mupen64Plus-Next</altemulator>\n\t</game>\n"
        "\t<game>\n\t\t<path>./Some Folder Game</path>\n"
        "\t\t<altemulator>Mupen64Plus-Next</altemulator>\n\t</game>\n"
        "</gameList>\n"
    )
    FILES = {"/mnt/sd/retrodeck/ES-DE/gamelists/n64/gamelist.xml": GAMELIST}

    def test_parse_both_levels(self):
        sel = atlas.parse_gamelist(self.GAMELIST)
        assert sel.system_label == "ParaLLEl N64"
        assert sel.per_game == {
            "Paper Mario (USA).zip": "Mupen64Plus-Next",
            "Some Folder Game": "Mupen64Plus-Next",
        }

    def test_per_game_wins_over_system(self):
        rd = _catalogue_fixture(self.FILES)
        entries = _entries(
            rd.emulators_for("n64", content_path="/mnt/sd/retrodeck/roms/n64/Paper Mario (USA).zip")
        )
        assert entries[0].label == "Mupen64Plus-Next"
        assert entries[0].selection == 'gamelist.xml: altemulator = "Mupen64Plus-Next" (per-game)'

    def test_folder_entry_matches_parent_dir(self):
        # multi-disc convention: the gamelist path is the folder, content is inside it
        rd = _catalogue_fixture(self.FILES)
        entries = _entries(
            rd.emulators_for("n64", content_path="/mnt/sd/retrodeck/roms/n64/Some Folder Game/disc.m3u")
        )
        assert entries[0].label == "Mupen64Plus-Next"

    def test_system_selection_for_other_games(self):
        rd = _catalogue_fixture(self.FILES)
        entries = _entries(
            rd.emulators_for("n64", content_path="/mnt/sd/retrodeck/roms/n64/Other Game.zip")
        )
        assert entries[0].label == "ParaLLEl N64"
        assert "alternativeEmulator" in (entries[0].selection or "")

    def test_system_level_ask_carries_caveat_when_per_game_exists(self):
        rd = _catalogue_fixture(self.FILES)
        entries = _entries(rd.emulators_for("n64"))
        assert entries[0].label == "ParaLLEl N64"  # system selection still applies
        assert any(c.code == atlas.CAVEAT_PER_GAME_OVERRIDES_PRESENT for c in entries[0].caveats)
        assert entries[0].caveats[0].data["count"] == "2"

    def test_no_caveat_without_per_game_entries(self):
        rd = _catalogue_fixture(
            {
                "/mnt/sd/retrodeck/ES-DE/gamelists/n64/gamelist.xml": (
                    "<alternativeEmulator><label>ParaLLEl N64</label></alternativeEmulator><gameList />"
                )
            }
        )
        assert all(not e.caveats for e in _entries(rd.emulators_for("n64")))

    def test_wrong_entry_save_location_gets_override_caveat(self):
        # caller picked the system default, but THIS game's altemulator points elsewhere
        rd = _catalogue_fixture(
            {
                **self.FILES,
                RETRODECK_CFG: 'savefile_directory = "/mnt/sd/retrodeck/saves"\n',
            }
        )
        parallel = _entries(rd.emulators_for("n64"))[0]  # ParaLLEl (system default)
        p = parallel.save_location(content_path="/mnt/sd/retrodeck/roms/n64/Paper Mario (USA).zip")
        assert isinstance(p, atlas.SavePlacement)
        override = [c for c in p.caveats if c.code == atlas.CAVEAT_PER_GAME_OVERRIDE]
        assert override
        assert override[0].data["label"] == "Mupen64Plus-Next"


class TestPerGameMatchIsAnchored:
    """A per-game selection belongs to one game, not to every same-named file.

    The shape is the live psx gamelist's (RetroDECK 0.10.9b): a root-level
    ``./<name>.m3u`` carrying the ``<altemulator>``, a ``<folder>`` of the same
    name, and inside it a second ``<name>.m3u`` carrying none. Matched by path
    suffix at any depth, the nested file inherits a selection ES-DE would never
    give it — so entries are resolved against the system's own ROM
    directory and compared as whole paths.
    """

    ROMS = "/mnt/sd/retrodeck/roms/n64"
    GAMELIST = (
        '<?xml version="1.0"?>\n'
        "<gameList>\n"
        "\t<folder>\n\t\t<path>./Collection</path>\n\t</folder>\n"
        "\t<game>\n\t\t<path>./Collection/Game.m3u</path>\n\t</game>\n"
        "\t<game>\n\t\t<path>./Game.m3u</path>\n"
        "\t\t<altemulator>ParaLLEl N64</altemulator>\n\t</game>\n"
        "</gameList>\n"
    )
    FILES = {"/mnt/sd/retrodeck/ES-DE/gamelists/n64/gamelist.xml": GAMELIST}

    def test_the_game_that_carries_the_override_still_matches(self):
        rd = _catalogue_fixture(self.FILES)
        entries = _entries(rd.emulators_for("n64", content_path=f"{self.ROMS}/Game.m3u"))
        assert entries[0].label == "ParaLLEl N64"
        assert entries[0].selection == 'gamelist.xml: altemulator = "ParaLLEl N64" (per-game)'

    def test_same_name_one_level_down_does_not_inherit_it(self):
        rd = _catalogue_fixture(self.FILES)
        entries = _entries(rd.emulators_for("n64", content_path=f"{self.ROMS}/Collection/Game.m3u"))
        assert [e.label for e in entries] == ["Mupen64Plus-Next", "ParaLLEl N64"]
        assert all(e.selection is None for e in entries)

    def test_same_name_deep_below_does_not_inherit_it(self):
        rd = _catalogue_fixture(self.FILES)
        entries = _entries(rd.emulators_for("n64", content_path=f"{self.ROMS}/deep/sub/Game.m3u"))
        assert [e.label for e in entries] == ["Mupen64Plus-Next", "ParaLLEl N64"]

    def test_content_outside_the_systems_rom_directory_matches_nothing(self):
        rd = _catalogue_fixture(self.FILES)
        entries = _entries(rd.emulators_for("n64", content_path="/elsewhere/Game.m3u"))
        assert [e.label for e in entries] == ["Mupen64Plus-Next", "ParaLLEl N64"]

    def test_a_redundant_spelling_of_the_same_path_still_matches(self):
        rd = _catalogue_fixture(self.FILES)
        entries = _entries(rd.emulators_for("n64", content_path=f"{self.ROMS}/Collection/..//Game.m3u"))
        assert entries[0].label == "ParaLLEl N64"

    def test_directory_entry_covers_the_files_inside_it(self):
        # The multi-disc convention: the gamelist names the folder, the content
        # is a disc inside it.
        rd = _catalogue_fixture(
            {
                "/mnt/sd/retrodeck/ES-DE/gamelists/n64/gamelist.xml": (
                    '<?xml version="1.0"?>\n<gameList>\n'
                    "\t<game>\n\t\t<path>./Collection</path>\n"
                    "\t\t<altemulator>ParaLLEl N64</altemulator>\n\t</game>\n</gameList>\n"
                )
            }
        )
        entries = _entries(rd.emulators_for("n64", content_path=f"{self.ROMS}/Collection/disc1.cue"))
        assert entries[0].label == "ParaLLEl N64"
        # …and not a file two levels below it, which is a different game.
        deeper = _entries(
            rd.emulators_for("n64", content_path=f"{self.ROMS}/Collection/extra/disc1.cue")
        )
        assert deeper[0].label == "Mupen64Plus-Next"

    def test_the_file_entry_wins_over_a_directory_entry_covering_it(self):
        rd = _catalogue_fixture(
            {
                "/mnt/sd/retrodeck/ES-DE/gamelists/n64/gamelist.xml": (
                    '<?xml version="1.0"?>\n<gameList>\n'
                    "\t<game>\n\t\t<path>./Collection</path>\n"
                    "\t\t<altemulator>ParaLLEl N64</altemulator>\n\t</game>\n"
                    "\t<game>\n\t\t<path>./Collection/disc1.cue</path>\n"
                    "\t\t<altemulator>Mupen64Plus-Next</altemulator>\n\t</game>\n</gameList>\n"
                )
            }
        )
        entries = _entries(rd.emulators_for("n64", content_path=f"{self.ROMS}/Collection/disc1.cue"))
        assert entries[0].label == "Mupen64Plus-Next"

    def test_a_symlinked_spelling_of_the_rom_directory_does_not_match(self):
        # The stated limit: the comparison is lexical. Resolving links would
        # cost a read per gamelist entry per query, which is what the
        # one-read-per-source rule exists to prevent — so the same file reached
        # through a link falls back to the per-system answer instead.
        rd = _catalogue_fixture(
            self.FILES, symlinks={"/mnt/sd/link-to-roms": "/mnt/sd/retrodeck/roms"}
        )
        entries = _entries(rd.emulators_for("n64", content_path="/mnt/sd/link-to-roms/n64/Game.m3u"))
        assert [e.label for e in entries] == ["Mupen64Plus-Next", "ParaLLEl N64"]

    def test_the_anchor_follows_a_configured_roms_path(self):
        rd = _retrodeck(
            {
                RETRODECK_JSON: (
                    '{"paths": {"rd_home_path": "/mnt/sd/retrodeck", '
                    '"saves_path": "/mnt/sd/retrodeck/saves", "roms_path": "/mnt/sd/games"}}'
                ),
                BUNDLED_ESDE: BUNDLED_XML,
                **self.FILES,
            },
            dirs=CATALOGUE_ROOTS,
        )
        configured = _entries(rd.emulators_for("n64", content_path="/mnt/sd/games/n64/Game.m3u"))
        assert configured[0].label == "ParaLLEl N64"
        # The default location is no longer the system's ROM directory.
        old_default = _entries(rd.emulators_for("n64", content_path=f"{self.ROMS}/Game.m3u"))
        assert old_default[0].label == "Mupen64Plus-Next"

    def test_the_entry_route_attaches_no_override_caveat_to_the_wrong_game(self):
        rd = _catalogue_fixture(
            {**self.FILES, RETRODECK_CFG: 'savefile_directory = "/mnt/sd/retrodeck/saves"\n'}
        )
        mupen = _entries(rd.emulators_for("n64"))[0]
        nested = mupen.save_location(content_path=f"{self.ROMS}/Collection/Game.m3u")
        assert isinstance(nested, atlas.SavePlacement)
        assert not [c for c in nested.caveats if c.code == atlas.CAVEAT_PER_GAME_OVERRIDE]
        # The game that does carry it still gets it.
        owner = mupen.save_location(content_path=f"{self.ROMS}/Game.m3u")
        assert isinstance(owner, atlas.SavePlacement)
        assert [c.data["label"] for c in owner.caveats if c.code == atlas.CAVEAT_PER_GAME_OVERRIDE] == [
            "ParaLLEl N64"
        ]
