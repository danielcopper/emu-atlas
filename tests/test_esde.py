"""Tests for atlas.esde — catalogue parsing, merge semantics, entry handles."""

from __future__ import annotations

import atlas
from atlas.installations import parse_gamelist
from atlas.machine import FixtureMachine
from atlas.esde import (
    EmulatorSpec,
    expand_home_path,
    merge_layers,
    parse_es_settings,
    parse_es_systems,
    parse_gamelist_alternative,
    resolve_rom_path,
)

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
        parsed = parse_es_systems(BUNDLED_XML, provenance="test")
        assert set(parsed) == {"dreamcast", "n64", "ps3"}
        assert [e.label for e in parsed["n64"].entries] == ["Mupen64Plus-Next", "ParaLLEl N64"]

    def test_libretro_classification_extracts_core_so(self):
        parsed = parse_es_systems(BUNDLED_XML, provenance="test")
        entry = parsed["dreamcast"].entries[0]
        assert entry.kind == atlas.KIND_LIBRETRO
        assert entry.core_so == "flycast_libretro.so"

    def test_standalone_classification(self):
        parsed = parse_es_systems(BUNDLED_XML, provenance="test")
        entry = parsed["ps3"].entries[0]
        assert entry.kind == atlas.KIND_STANDALONE
        assert entry.core_so is None

    def test_malformed_xml_is_skipped_layer(self):
        assert parse_es_systems("<systemList><system>", provenance="test") == {}

    def test_commented_out_systems_yield_nothing(self):
        # RetroDECK ships a custom_systems overlay that is entirely commented out.
        text = '<?xml version="1.0"?>\n<systemList>\n<!-- <system><name>x</name></system> -->\n</systemList>'
        assert parse_es_systems(text, provenance="test") == {}


class TestParseSettings:
    """``es_settings.xml`` is a rootless fragment sequence, and the reader says which empty it got."""

    SETTING = "ROMDirectory"
    DIRECTORY = "/mnt/sd/roms"
    ROM_DIRECTORY = f'<string name="{SETTING}" value="{DIRECTORY}" />'
    ONLY_THE_DIRECTORY = {SETTING: DIRECTORY}

    def test_rootless_siblings_are_all_read(self):
        # The whole reason for the synthetic root: xml.etree stops at the second
        # element, and every real machine's file has more than one.
        text = f'<string name="MediaDirectory" value="/media" />\n{self.ROM_DIRECTORY}\n'
        assert parse_es_settings(text) == {"MediaDirectory": "/media", **self.ONLY_THE_DIRECTORY}

    def test_an_xml_declaration_is_stripped_before_the_wrap(self):
        text = f'<?xml version="1.0"?>\n{self.ROM_DIRECTORY}\n'
        assert parse_es_settings(text) == self.ONLY_THE_DIRECTORY

    def test_a_declaration_with_an_encoding_is_stripped_too(self):
        text = f'<?xml version="1.0" encoding="UTF-8"?>{self.ROM_DIRECTORY}'
        assert parse_es_settings(text) == self.ONLY_THE_DIRECTORY

    def test_a_byte_order_mark_is_stripped_and_the_file_parses(self):
        # pugixml detects the encoding from the mark and reads the file, so its
        # settings are the ones in force — refusing them would answer about a
        # configuration the frontend is not using.
        text = f'\ufeff<?xml version="1.0"?>\n{self.ROM_DIRECTORY}\n'
        assert parse_es_settings(text) == self.ONLY_THE_DIRECTORY

    def test_a_single_element_needs_no_wrapping_and_still_reads(self):
        assert parse_es_settings(self.ROM_DIRECTORY) == self.ONLY_THE_DIRECTORY

    def test_non_string_elements_are_left_alone(self):
        text = f'<bool name="Debug" value="true" />\n{self.ROM_DIRECTORY}'
        assert parse_es_settings(text) == self.ONLY_THE_DIRECTORY

    def test_a_string_without_a_value_reads_as_the_empty_setting(self):
        # Which is the state RetroDECK's shipped template is in before its first
        # sed, and the state that makes the frontend's own default apply.
        assert parse_es_settings(f'<string name="{self.SETTING}" />') == {self.SETTING: ""}

    def test_an_empty_file_states_no_settings(self):
        assert parse_es_settings("") == {}

    def test_whitespace_only_states_no_settings(self):
        assert parse_es_settings("\n  \n") == {}

    def test_junk_is_unparseable_and_says_so(self):
        # None, not {} — a file nobody could parse and a file that sets nothing
        # are the same mapping and opposite facts.
        assert parse_es_settings("not xml at all <<<") is None

    def test_a_truncated_element_is_unparseable(self):
        assert parse_es_settings(f'<string name="{self.SETTING}"') is None

    def test_the_two_empties_are_distinguishable(self):
        assert parse_es_settings("") is not None


class TestResolveRomPath:
    """``loadConfig``'s substitution: replace the token, then collapse — always."""

    DECLARED = "%ROMPATH%/n64"
    DIRECTORY = "/mnt/sd/roms"
    RESOLVED = f"{DIRECTORY}/n64"
    LITERAL = "/srv/games/n64"

    def test_the_token_is_replaced_with_the_configured_directory(self):
        assert resolve_rom_path(self.DECLARED, self.DIRECTORY) == self.RESOLVED

    def test_a_trailing_separator_is_absorbed_rather_than_doubled(self):
        # ES-DE appends the separator only where it is missing, so a configured
        # ".../roms/" must not spell the answer ".../roms//n64".
        assert resolve_rom_path(self.DECLARED, f"{self.DIRECTORY}/") == self.RESOLVED

    def test_a_run_of_separators_collapses_all_the_way(self):
        # Utils::String::replace re-scans until the pattern is gone; one Python
        # pass would leave a doubled separator behind.
        assert resolve_rom_path("%ROMPATH%///n64", f"{self.DIRECTORY}/") == self.RESOLVED

    def test_a_literal_path_resolves_to_itself(self):
        # ES-DE insists on the token only when generating placeholder
        # directories, never when loading the catalogue.
        assert resolve_rom_path(self.LITERAL, None) == self.LITERAL

    def test_a_literal_path_is_collapsed_too(self):
        # loadConfig collapses unconditionally, on a path that carried no token
        # just the same.
        assert resolve_rom_path("/srv//games/n64", None) == self.LITERAL

    def test_a_token_in_the_middle_is_still_substituted(self):
        assert resolve_rom_path("%ROMPATH%/sub/n64", self.DIRECTORY) == f"{self.DIRECTORY}/sub/n64"

    def test_an_unset_directory_leaves_the_token_unresolved(self):
        assert resolve_rom_path(self.DECLARED, None) is None

    def test_a_relative_directory_is_refused_rather_than_joined(self):
        assert resolve_rom_path(self.DECLARED, "Emulation/roms") is None

    def test_a_home_prefixed_directory_is_refused_rather_than_expanded(self):
        # Expansion is the handles' job, where the home is established — a
        # value still carrying ~ here was never expanded, and stays refused.
        assert resolve_rom_path(self.DECLARED, "~/Emulation/roms") is None

    def test_an_empty_declaration_resolves_to_nothing(self):
        assert resolve_rom_path("", self.DIRECTORY) is None


class TestExpandHomePath:
    """``expandHomePath`` is text substitution of every ``~``, not tilde grammar.

    ``Utils::String::replace(path, "~", getHomePath())`` is the whole body
    (``FileSystemUtil.cpp:663-675``, ES-DE v3.4.1), so the shapes a shell
    treats specially — ``~user``, a mid-path ``~`` — are plain replacements
    here, and the substituted string is the directory the frontend really
    launches from.
    """

    HOME = "/home/deck"

    def test_a_leading_tilde_slash_expands_to_the_home(self):
        assert expand_home_path("~/Emulation/roms", self.HOME) == "/home/deck/Emulation/roms"

    def test_a_bare_tilde_is_the_home(self):
        assert expand_home_path("~", self.HOME) == self.HOME

    def test_tilde_user_is_concatenation_not_a_user_lookup(self):
        # No getpwnam anywhere in the function: ~user is the home with "user"
        # glued on, and that (almost certainly absent) directory is ES-DE's.
        assert expand_home_path("~user/roms", self.HOME) == "/home/deckuser/roms"

    def test_a_tilde_in_the_middle_is_replaced_too(self):
        assert expand_home_path("/mnt/sd/~/roms", self.HOME) == "/mnt/sd//home/deck/roms"

    def test_every_occurrence_is_replaced(self):
        assert expand_home_path("~/a~b", self.HOME) == "/home/deck/a/home/deckb"

    def test_a_value_without_a_tilde_is_untouched(self):
        assert expand_home_path("/mnt/sd/roms", self.HOME) == "/mnt/sd/roms"

    def test_the_replacement_is_one_pass_never_rescanned(self):
        # StringUtil.cpp:293-294 breaks the outer loop when the home itself
        # carries a ~ — one full pass, exactly str.replace, no recursion.
        assert expand_home_path("~/x", "/home/user~1") == "/home/user~1/x"


class TestMerge:
    def test_custom_replaces_bundled_system(self):
        bundled = parse_es_systems(BUNDLED_XML, provenance="bundled")
        custom = parse_es_systems(
            '<systemList><system><name>n64</name>'
            '<command label="ParaLLEl N64">%EMULATOR_RETROARCH% -L %CORE_RETROARCH%/parallel_n64_libretro.so %ROM%</command>'
            "</system></systemList>",
            provenance="custom",
        )
        merged = merge_layers(bundled, custom)
        assert [e.label for e in merged["n64"].entries] == ["ParaLLEl N64"]
        assert merged["n64"].entries[0].provenance == "custom"
        assert "dreamcast" in merged  # untouched systems stay

    def test_custom_adds_new_system(self):
        merged = merge_layers(
            parse_es_systems(BUNDLED_XML, provenance="bundled"),
            parse_es_systems(
                '<systemList><system><name>mysystem</name>'
                "<command>%EMULATOR_SOMETHING% %ROM%</command></system></systemList>",
                provenance="custom",
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
    machine = FixtureMachine(files, **kwargs)
    return atlas.RetroDeck(HOME, machine)


# The roots `RD_JSON` names, so the fixture models a *working* installation.
# These tests are about what the catalogue declares; every answer from a broken
# installation now carries its health findings, and a fixture that was broken
# only by omission would put them in the way of every assertion here.
CATALOGUE_ROOTS = ["/mnt/sd/retrodeck", "/mnt/sd/retrodeck/saves"]


ESDE_SETTINGS = f"{HOME}/.var/app/net.retrodeck.retrodeck/config/ES-DE/settings/es_settings.xml"
# The ROM root ES-DE actually substitutes, and the value RetroDECK's own
# component_prepare.sh seds into it from roms_path. A catalogue fixture without
# this file models a machine that cannot exist: the frontend would be looking
# in its own <config home>/ROMs while the ROM tree sits under rd_home.
ESDE_SETTINGS_XML = (
    '<?xml version="1.0"?>\n<string name="ROMDirectory" value="/mnt/sd/retrodeck/roms" />\n'
)


def _catalogue_fixture(extra_files=None, **kwargs):
    files = {
        RETRODECK_JSON: RD_JSON,
        BUNDLED_ESDE: BUNDLED_XML,
        ESDE_SETTINGS: ESDE_SETTINGS_XML,
    }
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
        assert [c.code for c in answer.caveats] == [atlas.CAVEAT_EMULATOR_CATALOGUE_UNREADABLE]
        listing = rd.systems()
        assert listing.systems == ()
        assert [c.code for c in listing.caveats] == [atlas.CAVEAT_EMULATOR_CATALOGUE_UNREADABLE]

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
        assert entries[0].provenance == "es_systems.xml (custom_systems overlay)"


class TestEntrySavefileLocation:
    def test_full_circle_dreamcast_entry_hits_the_rule_card(self):
        # catalogue -> default entry -> savefile_location: core known, card applies,
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
        p = entry.savefile_location(content_path="/mnt/sd/retrodeck/roms/dreamcast/Dreamcast Game (Europe).gdi")
        assert isinstance(p, atlas.SavefilePlacement)
        assert p.dir == "/mnt/sd/retrodeck/bios/dc"
        assert p.root_kind == atlas.ROOT_SYSTEM_DIRECTORY
        assert not any(c.code == atlas.CAVEAT_NO_CORE for c in p.caveats)
        assert p.granularity is not None
        assert p.granularity.value == "shared-card"

    def test_standalone_entry_is_a_domain_outcome(self):
        # Outside the resolver's coverage is an answer, not an exception (M8).
        rd = _catalogue_fixture()
        entry = _entries(rd.emulators_for("ps3"))[0]
        outcome = entry.savefile_location(content_path="/mnt/sd/retrodeck/roms/ps3/game")
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
        assert parse_gamelist_alternative(self.REAL_SAMPLE) == "ParaLLEl N64"

    def test_parses_the_nested_shape(self):
        assert parse_gamelist_alternative(self.NESTED_SAMPLE) == "ParaLLEl N64"

    def test_document_level_wins_over_nested(self):
        # ES-DE takes the document-level element when both exist, label or not.
        both = (
            "<alternativeEmulator><label>ParaLLEl N64</label></alternativeEmulator>"
            "<gameList><alternativeEmulator><label>Mupen64Plus-Next</label></alternativeEmulator></gameList>"
        )
        assert parse_gamelist_alternative(both) == "ParaLLEl N64"

    def test_labelless_document_level_element_states_nothing(self):
        # ES-DE picks the document-level element, then reads its label — an
        # empty one selects nothing, it does not fall back to the nested one.
        both = (
            "<alternativeEmulator />"
            "<gameList><alternativeEmulator><label>ParaLLEl N64</label></alternativeEmulator></gameList>"
        )
        assert parse_gamelist_alternative(both) is None

    def test_selection_inside_a_game_is_not_a_system_selection(self):
        # Neither reader looks there; a game's own choice is <altemulator>.
        text = (
            "<gameList><game><path>./Fixture Game.zip</path>"
            "<alternativeEmulator><label>ParaLLEl N64</label></alternativeEmulator>"
            "</game></gameList>"
        )
        assert parse_gamelist_alternative(text) is None

    def test_absent_selection_is_none(self):
        assert parse_gamelist_alternative('<?xml version="1.0"?>\n<gameList />') is None

    def test_malformed_is_none_never_guessed(self):
        assert parse_gamelist_alternative("<alternativeEmulator><label>") is None

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
        sel = parse_gamelist(self.GAMELIST)
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

    def test_wrong_entry_savefile_location_gets_override_caveat(self):
        # caller picked the system default, but THIS game's altemulator points elsewhere
        rd = _catalogue_fixture(
            {
                **self.FILES,
                RETRODECK_CFG: 'savefile_directory = "/mnt/sd/retrodeck/saves"\n',
            }
        )
        parallel = _entries(rd.emulators_for("n64"))[0]  # ParaLLEl (system default)
        p = parallel.savefile_location(content_path="/mnt/sd/retrodeck/roms/n64/Paper Mario (USA).zip")
        assert isinstance(p, atlas.SavefilePlacement)
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

    # The two ROM paths a machine can carry, pointed at different trees. Only
    # one of them is the one ES-DE launches from.
    MARKER_ROMS = "/mnt/sd/games"
    SETTINGS_ROMS = "/mnt/sd/es-de-roms"

    def _diverged(self):
        """A machine whose marker and whose frontend settings disagree about the ROM root."""
        return _retrodeck(
            {
                RETRODECK_JSON: (
                    '{"paths": {"rd_home_path": "/mnt/sd/retrodeck", '
                    f'"saves_path": "/mnt/sd/retrodeck/saves", "roms_path": "{self.MARKER_ROMS}"}}}}'
                ),
                BUNDLED_ESDE: BUNDLED_XML,
                ESDE_SETTINGS: (
                    '<?xml version="1.0"?>\n'
                    f'<string name="ROMDirectory" value="{self.SETTINGS_ROMS}" />\n'
                ),
                **self.FILES,
            },
            dirs=CATALOGUE_ROOTS,
        )

    def test_the_anchor_follows_es_des_own_setting(self):
        # The rule this replaces read retrodeck.json's roms_path. That is only
        # the value RetroDECK seds INTO ES-DE's setting — the frontend reads its
        # own, so where the two have drifted apart the override belongs to the
        # game under ROMDirectory.
        entries = _entries(
            self._diverged().emulators_for(
                "n64", content_path=f"{self.SETTINGS_ROMS}/n64/Game.m3u"
            )
        )
        assert entries[0].label == "ParaLLEl N64"

    def test_the_anchor_does_not_follow_the_markers_roms_path(self):
        # The other half, and the actual regression guard: the game sitting
        # where RetroDECK's bookkeeping says carries no override, because ES-DE
        # would never launch it from there.
        entries = _entries(
            self._diverged().emulators_for("n64", content_path=f"{self.MARKER_ROMS}/n64/Game.m3u")
        )
        assert entries[0].label == "Mupen64Plus-Next"

    def test_the_entry_route_attaches_no_override_caveat_to_the_wrong_game(self):
        rd = _catalogue_fixture(
            {**self.FILES, RETRODECK_CFG: 'savefile_directory = "/mnt/sd/retrodeck/saves"\n'}
        )
        mupen = _entries(rd.emulators_for("n64"))[0]
        nested = mupen.savefile_location(content_path=f"{self.ROMS}/Collection/Game.m3u")
        assert isinstance(nested, atlas.SavefilePlacement)
        assert not [c for c in nested.caveats if c.code == atlas.CAVEAT_PER_GAME_OVERRIDE]
        # The game that does carry it still gets it.
        owner = mupen.savefile_location(content_path=f"{self.ROMS}/Game.m3u")
        assert isinstance(owner, atlas.SavefilePlacement)
        assert [c.data["label"] for c in owner.caveats if c.code == atlas.CAVEAT_PER_GAME_OVERRIDE] == [
            "ParaLLEl N64"
        ]


class TestAnAnchorThatCannotBeResolvedSaysSo:
    """The anchor can now refuse, and a skipped override must never be silent.

    Reading the marker always produced a string, so per-game matching always
    had something to match against. ES-DE's own setting can refuse — the file
    is unreadable, an override moved the tree it lives in — and then the
    override the frontend *would* apply is not applied here. That is a
    degradation, so it travels as the caveat that names which one, reusing the
    codes the ROM question already answers with rather than inventing a
    category code with the reason buried in its data.
    """

    CONTENT = "/mnt/sd/retrodeck/roms/n64/Paper Mario (USA).zip"
    GAMELIST = (
        '<?xml version="1.0"?>\n<gameList>\n'
        "\t<game>\n\t\t<path>./Paper Mario (USA).zip</path>\n"
        "\t\t<altemulator>ParaLLEl N64</altemulator>\n\t</game>\n</gameList>\n"
    )
    FILES = {"/mnt/sd/retrodeck/ES-DE/gamelists/n64/gamelist.xml": GAMELIST}

    def _unreadable_settings(self):
        return _catalogue_fixture({**self.FILES, ESDE_SETTINGS: {"status": "unreadable"}})

    def test_the_override_still_applies_when_the_anchor_resolves(self):
        # The counterpart the assertions below would be vacuous without.
        answer = _catalogue_fixture(self.FILES).emulators_for("n64", content_path=self.CONTENT)
        assert answer.entries[0].label == "ParaLLEl N64"
        assert answer.caveats == ()

    def test_settings_nobody_could_read_leave_the_override_unapplied(self):
        answer = self._unreadable_settings().emulators_for("n64", content_path=self.CONTENT)
        assert answer.entries[0].label == "Mupen64Plus-Next"

    def test_and_the_answer_says_why_rather_than_going_quiet(self):
        answer = self._unreadable_settings().emulators_for("n64", content_path=self.CONTENT)
        assert [c.code for c in answer.caveats] == [atlas.CAVEAT_FRONTEND_SETTINGS_UNREADABLE]

    def test_the_entry_route_states_it_too(self):
        rd = _catalogue_fixture(
            {
                **self.FILES,
                RETRODECK_CFG: 'savefile_directory = "/mnt/sd/retrodeck/saves"\n',
                ESDE_SETTINGS: {"status": "unreadable"},
            }
        )
        placement = _entries(rd.emulators_for("n64"))[0].savefile_location(content_path=self.CONTENT)
        assert isinstance(placement, atlas.SavefilePlacement)
        assert atlas.CAVEAT_FRONTEND_SETTINGS_UNREADABLE in [c.code for c in placement.caveats]

    def test_a_query_without_content_is_not_caveated_for_an_anchor_it_never_wanted(self):
        # Nothing to anchor means nothing failed: the settings are not even
        # opened, so complaining about them would be noise.
        answer = self._unreadable_settings().emulators_for("n64")
        assert [c.code for c in answer.caveats] == []

    def test_an_unread_catalogue_is_not_a_catalogue_that_declares_nothing(self):
        """The entry route reads the catalogue for the anchor and must keep its read flag.

        An unreadable ``es_systems.xml`` parses to an empty snapshot, and an
        empty snapshot looks exactly like one that was read and declares no
        ``<path>`` — so the resolution would answer ``rom-path-undeclared``, a
        statement about the declaration, for a machine where nobody could look.
        Handles are live, so the catalogue can go unreadable between the ask
        that produced the entry and this call.
        """
        machine = FixtureMachine(
            {
                RETRODECK_JSON: RD_JSON,
                RETRODECK_CFG: 'savefile_directory = "/mnt/sd/retrodeck/saves"\n',
                BUNDLED_ESDE: {"status": "unreadable"},
            },
            dirs=CATALOGUE_ROOTS,
        )
        spec = EmulatorSpec(
            system="n64",
            label="ParaLLEl N64",
            kind=atlas.KIND_LIBRETRO,
            core_so="parallel_n64_libretro.so",
            command="",
            provenance="test",
        )
        placement = atlas.RetroDeck(HOME, machine).entry_savefile_location(
            spec, content_path="/mnt/sd/retrodeck/roms/n64/Game.m3u"
        )
        codes = [c.code for c in placement.caveats]
        assert atlas.CAVEAT_EMULATOR_CATALOGUE_UNREADABLE in codes
        assert atlas.CAVEAT_ROM_PATH_UNDECLARED not in codes
