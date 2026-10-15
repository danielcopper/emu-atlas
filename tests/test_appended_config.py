"""Tests for the files a RetroArch launch appends with ``--appendconfig`` (issue #637).

The machine vectors hold the answers end to end; these hold the pieces a
vector cannot reach on its own — the chain arithmetic in isolation, the input
rule, the provenance a vector does not assert, and the routes no vector asks.
"""

from __future__ import annotations

import pytest

import atlas
from atlas.cli import build_parser, run
from atlas.machine import FixtureMachine
from atlas.placement import checked_appendconfig
from atlas.retroarch_cfg import (
    CFG_LAYER_APPENDED,
    CFG_LAYER_CORE_OVERRIDE,
    CFG_LAYER_GLOBAL,
    SAVEFILE_KEYS,
    UPSTREAM_DEFAULTS,
    CfgSource,
    chain_bool_read,
    chain_value,
    chain_value_read,
    resolve_layout,
)

HOME = "/home/deck"
GLOBAL = CfgSource(CFG_LAYER_GLOBAL, "retroarch.cfg")
CORE = CfgSource(CFG_LAYER_CORE_OVERRIDE, "config/PPSSPP/PPSSPP.cfg")
FIRST = CfgSource(CFG_LAYER_APPENDED, "/home/deck/launcher/first.cfg")
SECOND = CfgSource(CFG_LAYER_APPENDED, "/home/deck/launcher/second.cfg")


def _layout(global_text, *, appended=(), overrides=(), is_directory=None):
    return resolve_layout(
        global_text,
        keys=SAVEFILE_KEYS,
        home=HOME,
        cfg_label="retroarch.cfg",
        defaults=UPSTREAM_DEFAULTS,
        appended=appended,
        overrides=overrides,
        is_directory=is_directory,
    )


def _only(*existing):
    return lambda value: value in existing


class TestTheAppendedLayerSitsBetweenTheGlobalCfgAndTheOverrides:
    """configuration.c:6309-6353 merges each appended file after the global cfg; the overrides come after."""

    def test_an_appended_root_replaces_the_global_root(self):
        layout = _layout(
            'savefile_directory = "/mnt/sd/saves"\n',
            appended=[(FIRST, 'savefile_directory = "/home/deck/launcher/saves"\n')],
        )
        assert layout.directory == "/home/deck/launcher/saves"
        assert layout.sources[3] == 'appended config /home/deck/launcher/first.cfg: savefile_directory = "/home/deck/launcher/saves"'

    def test_of_two_appended_files_the_later_one_wins(self):
        layout = _layout(
            None,
            appended=[
                (FIRST, 'sort_savefiles_enable = "true"\n'),
                (SECOND, 'sort_savefiles_enable = "false"\n'),
            ],
        )
        assert layout.sort_by_core is False

    def test_an_override_still_wins_over_an_appended_file(self):
        value, source, _ = chain_value_read(
            [
                (GLOBAL, 'savefile_directory = "/a"\n'),
                (FIRST, 'savefile_directory = "/b"\n'),
                (CORE, 'savefile_directory = "/c"\n'),
            ],
            "savefile_directory",
        )
        assert (value, source) == ("/c", CORE)

    def test_an_appended_file_listed_after_an_override_is_still_read_before_it(self):
        # The kind decides the read, not the position in the list: the boot
        # load takes the global cfg and the appended files, the reload the
        # overrides on top (configuration.c:6309-6353, :6355 onwards).
        value, _ = chain_value(
            [(GLOBAL, ""), (CORE, 'savefile_directory = "/c"\n'), (FIRST, 'savefile_directory = "/b"\n')],
            "savefile_directory",
        )
        assert value == "/c"

    def test_a_refused_appended_root_leaves_the_platform_default_not_the_global_root(self):
        # The boot read is the merged config's ONE entry for the key — the
        # appended one — so when path_is_directory refuses it, nothing is set
        # and the global cfg's root, shadowed by the merge, never applies.
        layout = _layout(
            'savefile_directory = "/mnt/sd/saves"\n',
            appended=[(FIRST, 'savefile_directory = "/gone"\n')],
            is_directory=_only("/mnt/sd/saves"),
        )
        assert layout.directory is None
        [rejected] = layout.rejected_directories
        assert (rejected.layer, rejected.value) == (FIRST, "/gone")


class TestAnOverrideWinningAnAppendedKeyIsNamed:
    """D3: one entry per governing key an appended file sets and an override's read decides."""

    def test_the_override_that_wins_is_named_with_the_key(self):
        layout = _layout(
            None,
            appended=[(FIRST, 'savefile_directory = "/b"\nsort_savefiles_enable = "false"\n')],
            overrides=[(CORE, 'savefile_directory = "/c"\n')],
        )
        assert [(entry.key, entry.override) for entry in layout.appended_overridden] == [
            ("savefile_directory", CORE)
        ]

    def test_an_override_the_getter_refuses_wins_nothing(self):
        layout = _layout(
            None,
            appended=[(FIRST, 'sort_savefiles_enable = "false"\n')],
            overrides=[(CORE, 'sort_savefiles_enable = "yes"\n')],
        )
        assert layout.sort_by_core is False
        assert layout.appended_overridden == ()

    def test_an_override_over_a_key_only_the_global_cfg_sets_is_not_named(self):
        layout = _layout(
            'sort_savefiles_enable = "false"\n',
            appended=[(FIRST, 'savefile_directory = "/b"\n')],
            overrides=[(CORE, 'sort_savefiles_enable = "true"\n')],
        )
        assert layout.appended_overridden == ()

    def test_a_reset_to_the_platform_default_in_an_override_wins_the_key(self):
        layout = _layout(
            None,
            appended=[(FIRST, 'savefile_directory = "/b"\n')],
            overrides=[(CORE, 'savefile_directory = "default"\n')],
        )
        assert layout.directory is None
        assert [entry.key for entry in layout.appended_overridden] == ["savefile_directory"]


class TestTheGateReadNamesItsFile:
    def test_the_deciding_layer_is_the_appended_file(self):
        value, source, _ = chain_bool_read(
            [(GLOBAL, 'auto_overrides_enable = "true"\n'), (FIRST, 'auto_overrides_enable = "false"\n')],
            "auto_overrides_enable",
            default=True,
        )
        assert (value, source) == (False, FIRST)

    def test_a_refused_value_decides_nothing(self):
        value, source, ignored = chain_bool_read(
            [(FIRST, 'auto_overrides_enable = "no"\n')], "auto_overrides_enable", default=True
        )
        assert (value, source) == (True, None)
        assert [setting.text for setting in ignored] == ["no"]


class TestTheFilesAreTakenAsTheCommandLineSpellsThem:
    def test_a_pipe_joined_value_is_split_as_retroarch_splits_it(self):
        assert checked_appendconfig(["/a.cfg||/b.cfg|", "/c.cfg"]) == ("/a.cfg", "/b.cfg", "/c.cfg")

    def test_a_bare_string_is_refused_rather_than_iterated(self):
        with pytest.raises(TypeError):
            checked_appendconfig("/a.cfg")

    def test_a_relative_path_is_a_usage_error(self):
        with pytest.raises(ValueError, match="absolute"):
            checked_appendconfig(["/a.cfg|launcher/b.cfg"])

    def test_every_installation_checks_before_asking_anyone(self):
        every = atlas.EveryInstallation(())
        with pytest.raises(ValueError, match="absolute"):
            every.savefile_location(appendconfig=["b.cfg"])


RD_MARKER = f"{HOME}/.var/app/net.retrodeck.retrodeck/config/retrodeck/retrodeck.json"
RD_CFG = f"{HOME}/.var/app/net.retrodeck.retrodeck/config/retroarch/retroarch.cfg"
RD_METADATA = "/var/lib/flatpak/app/net.retrodeck.retrodeck/current/active/metadata"
RD_CORES = "/var/lib/flatpak/app/net.retrodeck.retrodeck/current/active/files/cores"
REDIRECT = f"{HOME}/launcher/redirect.cfg"
GAME = "/mnt/sd/retrodeck/roms/psp/Lumines (USA).iso"


def _retrodeck(files, *, grants: str | None = "host;"):
    base = {
        RD_MARKER: '{"paths": {"rd_home_path": "/mnt/sd/retrodeck", "saves_path": "/mnt/sd/retrodeck/saves"}}',
        RD_CFG: 'savefile_directory = "/mnt/sd/retrodeck/saves"\nlibretro_directory = "/app/cores"\n',
        GAME: "",
        "/mnt/sd/retrodeck/saves/.keep": "",
    }
    if grants is not None:
        base[RD_METADATA] = f"[Application]\nname=net.retrodeck.retrodeck\n\n[Context]\nfilesystems={grants}\n"
    machine = FixtureMachine(
        {**base, **files},
        dirs=[f"{HOME}/.var/app/net.retrodeck.retrodeck/config/ROMs", "/mnt/sd/retrodeck/bios", f"{HOME}/launcher/saves"],
        cores={f"{RD_CORES}/ppsspp_libretro.so": {"library_name": "PPSSPP"}},
    )
    return atlas.RetroDeck(HOME, machine)


class TestTheOverrideGatesReadTheAppendedFiles:
    """D2: auto_overrides_enable and rgui_config_directory come from the boot read, the appended files in it."""

    def test_an_appended_false_names_its_file_in_the_provenance(self):
        rd = _retrodeck(
            {
                REDIRECT: 'auto_overrides_enable = "false"\n',
                f"{HOME}/.var/app/net.retrodeck.retrodeck/config/retroarch/config/PPSSPP/PPSSPP.cfg": (
                    'savefile_directory = "/home/deck/launcher/saves"\n'
                ),
            }
        )
        placement = rd.savefile_location(content_path=GAME, core_so="ppsspp_libretro.so", appendconfig=[REDIRECT])
        assert isinstance(placement, atlas.SavefilePlacement)
        assert placement.dir.startswith("/mnt/sd/retrodeck/saves")
        assert (
            f'appended config {REDIRECT}: auto_overrides_enable = "false" — override files not applied'
            in placement.sources
        )

    def test_an_appended_override_directory_is_where_the_overrides_are_found(self):
        rd = _retrodeck(
            {
                REDIRECT: f'rgui_config_directory = "{HOME}/launcher/overrides"\n',
                f"{HOME}/launcher/overrides/PPSSPP/PPSSPP.cfg": 'savefile_directory = "/home/deck/launcher/saves"\n',
            }
        )
        placement = rd.savefile_location(content_path=GAME, core_so="ppsspp_libretro.so", appendconfig=[REDIRECT])
        assert isinstance(placement, atlas.SavefilePlacement)
        assert placement.dir.startswith(f"{HOME}/launcher/saves")
        assert any(source.startswith(f"appended config {REDIRECT}: rgui_config_directory") for source in placement.sources)


class TestAFileRetroArchCannotOpenRefusesTheAnswer:
    """D4/D6: no answer with the redirect exists once RetroArch launches without the file."""

    def test_a_directory_at_the_path_refuses(self):
        rd = _retrodeck({f"{REDIRECT}/.keep": ""}, grants=None)
        outcome = rd.savestate_location(content_path=GAME, core_so="ppsspp_libretro.so", appendconfig=[REDIRECT])
        assert isinstance(outcome, atlas.Unresolved)
        assert (outcome.code, dict(outcome.data)) == (atlas.UNRESOLVED_APPENDED_CONFIG_UNREACHABLE, {"file": REDIRECT})
        assert outcome.message == (
            f"appended config {REDIRECT!r} cannot be opened: it is no regular file or cannot be read — "
            "RetroArch skips a file it cannot open and launches without it (configuration.c:6341-6347, "
            "config_file.c:773-779), so there is no answer with this file appended"
        )

    def test_a_file_the_sandbox_hides_says_so(self):
        rd = _retrodeck({REDIRECT: 'savefile_directory = "/home/deck/launcher/saves"\n'}, grants="host;!~/launcher;")
        outcome = rd.savefile_location(content_path=GAME, appendconfig=[REDIRECT])
        assert isinstance(outcome, atlas.Unresolved)
        assert outcome.message.startswith(
            f"appended config {REDIRECT!r} cannot be opened: the program's sandbox shows no file there — "
            "RetroArch skips a file it cannot open"
        )

    def test_a_file_that_is_not_valid_utf8_refuses_and_says_atlas_could_not_read_it(self):
        rd = _retrodeck({REDIRECT: {"status": "invalid-text"}})
        outcome = rd.savefile_location(content_path=GAME, core_so="ppsspp_libretro.so", appendconfig=[REDIRECT])
        assert isinstance(outcome, atlas.Unresolved)
        assert (outcome.code, dict(outcome.data)) == (atlas.UNRESOLVED_APPENDED_CONFIG_UNREACHABLE, {"file": REDIRECT})
        assert outcome.message == (
            f"appended config {REDIRECT!r} is not valid UTF-8 text, so atlas could not read what it sets — "
            "RetroArch reads it byte-wise and applies it, so an answer without it would be false"
        )

    def test_the_first_file_that_cannot_be_opened_is_the_one_named(self):
        other = f"{HOME}/launcher/other.cfg"
        rd = _retrodeck({REDIRECT: 'savefile_directory = "/home/deck/launcher/saves"\n'})
        outcome = rd.savefile_location(content_path=GAME, appendconfig=[f"{REDIRECT}|{other}"])
        assert isinstance(outcome, atlas.Unresolved)
        assert dict(outcome.data) == {"file": other}


class TestARetroArchOnTheHostCarriesNoVisibilityNote:
    """D8: the note is about a sandbox; a native RetroArch has none."""

    def test_a_native_install_reads_the_host_path_and_says_nothing_about_visibility(self):
        machine = FixtureMachine(
            {
                f"{HOME}/.config/retroarch/retroarch.cfg": 'savefile_directory = "/home/deck/saves"\n',
                REDIRECT: f'savefile_directory = "{HOME}/launcher/saves"\n',
            },
            dirs=[f"{HOME}/saves", f"{HOME}/launcher/saves"],
        )
        [native] = atlas.detect(HOME, machine)
        placement = native.savefile_location(appendconfig=[REDIRECT])
        assert isinstance(placement, atlas.SavefilePlacement)
        assert placement.dir.startswith(f"{HOME}/launcher/saves")
        assert all(caveat.code != atlas.CAVEAT_APPENDED_CONFIG_VISIBILITY_UNESTABLISHED for caveat in placement.caveats)


MELON_SYSTEMS = (
    '<?xml version="1.0"?>\n<systemList>\n  <system>\n    <name>nds</name>\n    <path>%ROMPATH%/nds</path>\n'
    "    <extension>.nds</extension>\n"
    '    <command label="melonDS (Standalone)">%EMULATOR_MELONDS% %ROM%</command>\n'
    '    <command label="Missing (Standalone)">%EMULATOR_NOBODY_CARDS_THIS% %ROM%</command>\n'
    "  </system>\n</systemList>\n"
)
MELON_ROM = "/mnt/sd/retrodeck/roms/nds/Some Game.nds"


def _melon_entries():
    machine = FixtureMachine(
        {
            RD_MARKER: '{"paths": {"rd_home_path": "/mnt/sd/retrodeck", "saves_path": "/mnt/sd/retrodeck/saves"}}',
            RD_CFG: 'savefile_directory = "/mnt/sd/retrodeck/saves"\n',
            "/var/lib/flatpak/app/net.retrodeck.retrodeck/current/active/files/retrodeck/components/es-de/share/"
            "es-de/resources/systems/linux/es_systems.xml": MELON_SYSTEMS,
            f"{HOME}/.var/app/net.retrodeck.retrodeck/config/melonDS/melonDS.toml": (
                '[Instance0]\nSaveFilePath = "/mnt/sd/retrodeck/saves/nds/melonds"\n'
                'SavestatePath = "/mnt/sd/retrodeck/states/nds/melonds"\n'
            ),
            MELON_ROM: "",
        },
        dirs=[
            "/mnt/sd/retrodeck/saves/nds/melonds",
            "/mnt/sd/retrodeck/states/nds/melonds",
            f"{HOME}/.var/app/net.retrodeck.retrodeck/config/ROMs",
            "/mnt/sd/retrodeck/bios",
        ],
    )
    return atlas.RetroDeck(HOME, machine).emulators_for("nds", content_path=MELON_ROM).entries


class TestAStandaloneEntryReadsNoAppendedFile:
    """D5: the answer without the files, and a note saying so — a refusal passes through untouched."""

    def test_the_savestate_answer_is_unchanged_and_carries_the_note(self):
        melon = _melon_entries()[0]
        plain = melon.savestate_location(content_path=MELON_ROM)
        asked = melon.savestate_location(content_path=MELON_ROM, appendconfig=["/nowhere/redirect.cfg"])
        assert isinstance(plain, atlas.SavestatePlacement)
        assert isinstance(asked, atlas.SavestatePlacement)
        assert asked.dir == plain.dir
        assert asked.caveats[-1].code == atlas.CAVEAT_APPENDED_CONFIG_NOT_READ
        assert dict(asked.caveats[-1].data) == {}
        assert asked.caveats[:-1] == plain.caveats

    def test_a_refusal_stays_the_whole_answer(self):
        uncarded = _melon_entries()[1]
        outcome = uncarded.savefile_location(content_path=MELON_ROM, appendconfig=["/nowhere/redirect.cfg"])
        assert isinstance(outcome, atlas.Unresolved)
        assert outcome.code == atlas.UNRESOLVED_STANDALONE

    def test_a_relative_path_raises_on_a_standalone_entry_too(self):
        melon = _melon_entries()[0]
        with pytest.raises(ValueError, match="absolute"):
            melon.savefile_location(appendconfig=["redirect.cfg"])


class TestTheCliTakesTheFlagRepeatedAndPipeJoined:
    def test_a_relative_value_is_a_usage_error(self, capsys):
        machine = FixtureMachine({})
        with pytest.raises(SystemExit) as excinfo:
            run(["savefile-location", "--appendconfig", "redirect.cfg"], home=HOME, machine=machine)
        assert excinfo.value.code == 2
        assert "absolute" in capsys.readouterr().err

    def test_repeated_and_joined_values_reach_the_question_in_order(self):
        args = build_parser().parse_args(
            ["savestate-location", "--appendconfig", "/a.cfg|/b.cfg", "--appendconfig", "/c.cfg"]
        )
        assert checked_appendconfig(args.appendconfig) == ("/a.cfg", "/b.cfg", "/c.cfg")

    def test_without_the_flag_nothing_is_appended(self):
        assert build_parser().parse_args(["savefile-location"]).appendconfig == []


# The libretro entry routes and the aggregate hand the files on: one fixture per
# arrangement, each with the same appended file moving both roots.
LAUNCHER_FILE = (
    f'savefile_directory = "{HOME}/launcher/saves"\nsavestate_directory = "{HOME}/launcher/states"\n'
    'sort_savefiles_by_content_enable = "false"\nsort_savefiles_enable = "false"\n'
    'sort_savestates_by_content_enable = "false"\nsort_savestates_enable = "false"\n'
)
LAUNCHER_DIRS = [f"{HOME}/launcher/saves", f"{HOME}/launcher/states"]
MGBA_COMMAND = '<command label="mGBA">%EMULATOR_RETROARCH% -L %CORE_RETROARCH%/mgba_libretro.so %ROM%</command>'
GBA_SYSTEM = (
    '<?xml version="1.0"?>\n<systemList>\n  <system>\n    <name>gba</name>\n    <path>%ROMPATH%/gba</path>\n'
    f"    <extension>.gba</extension>\n    {MGBA_COMMAND}\n  </system>\n</systemList>\n"
)


def _retrodeck_gba():
    machine = FixtureMachine(
        {
            RD_MARKER: '{"paths": {"rd_home_path": "/mnt/sd/retrodeck", "saves_path": "/mnt/sd/retrodeck/saves"}}',
            RD_CFG: 'savefile_directory = "/mnt/sd/retrodeck/saves"\nlibretro_directory = "/app/cores"\n',
            "/var/lib/flatpak/app/net.retrodeck.retrodeck/current/active/files/retrodeck/components/es-de/share/"
            "es-de/resources/systems/linux/es_systems.xml": GBA_SYSTEM,
            RD_METADATA: "[Application]\nname=net.retrodeck.retrodeck\n\n[Context]\nfilesystems=host;\n",
            REDIRECT: LAUNCHER_FILE,
        },
        dirs=["/mnt/sd/retrodeck/saves", f"{HOME}/.var/app/net.retrodeck.retrodeck/config/ROMs", *LAUNCHER_DIRS],
        cores={f"{RD_CORES}/mgba_libretro.so": {"library_name": "mGBA"}},
    )
    return atlas.RetroDeck(HOME, machine)


def _emudeck_gba():
    cores = f"{HOME}/.var/app/org.libretro.RetroArch/config/retroarch/cores"
    machine = FixtureMachine(
        {
            f"{HOME}/.config/EmuDeck/settings.sh": 'romsPath="$HOME/Emulation/roms"\nsavesPath="$HOME/Emulation/saves"\n',
            f"{HOME}/.var/app/org.libretro.RetroArch/config/retroarch/retroarch.cfg": (
                f'savefile_directory = "{HOME}/Emulation/saves/retroarch/saves"\nlibretro_directory = "{cores}"\n'
            ),
            f"{HOME}/ES-DE/custom_systems/es_systems.xml": GBA_SYSTEM,
            f"{HOME}/ES-DE/settings/es_settings.xml": (
                f'<?xml version="1.0"?>\n<string name="ROMDirectory" value="{HOME}/Emulation/roms" />\n'
            ),
            REDIRECT: LAUNCHER_FILE,
        },
        dirs=[f"{HOME}/Emulation/saves/retroarch/saves", f"{HOME}/Emulation/bios", *LAUNCHER_DIRS],
        cores={f"{cores}/mgba_libretro.so": {"library_name": "mGBA"}},
    )
    return atlas.EmuDeck(HOME, machine)


def _bare_gba():
    cores = f"{HOME}/.var/app/org.libretro.RetroArch/config/retroarch/cores"
    machine = FixtureMachine(
        {
            f"{HOME}/.var/app/org.libretro.RetroArch/config/retroarch/retroarch.cfg": (
                f'savefile_directory = "{HOME}/saves"\nlibretro_directory = "{cores}"\nlibretro_info_path = "{cores}"\n'
            ),
            f"{cores}/mgba_libretro.info": 'corename = "mGBA"\nsystemname = "Game Boy Advance"\n',
            REDIRECT: LAUNCHER_FILE,
        },
        dirs=[f"{HOME}/saves", *LAUNCHER_DIRS],
        cores={f"{cores}/mgba_libretro.so": {"library_name": "mGBA"}},
    )
    return atlas.BareRetroArchFlatpak(HOME, machine)


@pytest.mark.parametrize("handle", [_retrodeck_gba, _emudeck_gba, _bare_gba], ids=["retrodeck", "emudeck", "bare"])
class TestALibretroEntryReadsTheAppendedFiles:
    def test_the_save_lands_where_the_appended_file_puts_it(self, handle):
        [entry] = handle().emulators_for("gba").entries
        placement = entry.savefile_location(content_path="/roms/gba/Game.gba", appendconfig=[REDIRECT])
        assert isinstance(placement, atlas.SavefilePlacement)
        assert placement.dir == f"{HOME}/launcher/saves"

    def test_the_states_land_where_the_appended_file_puts_them(self, handle):
        [entry] = handle().emulators_for("gba").entries
        placement = entry.savestate_location(content_path="/roms/gba/Game.gba", appendconfig=[REDIRECT])
        assert isinstance(placement, atlas.SavestatePlacement)
        assert placement.dir == f"{HOME}/launcher/states"


class TestTheAggregateHandsTheFilesToEveryHandle:
    def test_each_answer_is_read_with_the_appended_file(self):
        answers = atlas.EveryInstallation([_bare_gba(), _retrodeck_gba()]).savestate_location(
            content_path="/roms/gba/Game.gba", core_so="mgba_libretro.so", appendconfig=[REDIRECT]
        )
        dirs = [answer.answer.dir for answer in answers if isinstance(answer.answer, atlas.SavestatePlacement)]
        assert dirs == [f"{HOME}/launcher/states", f"{HOME}/launcher/states"]


class TestAQuestionWithoutAppendedFilesSaysNothingAboutThem:
    def test_an_unmodelled_sandbox_carries_no_visibility_note_when_nothing_is_appended(self):
        placement = _emudeck_gba().savefile_location(content_path="/roms/gba/Game.gba", core_so="mgba_libretro.so")
        assert isinstance(placement, atlas.SavefilePlacement)
        assert atlas.CAVEAT_APPENDED_CONFIG_VISIBILITY_UNESTABLISHED not in {caveat.code for caveat in placement.caveats}


class TestTheBootReadDecidesWhereTheCoreAndItsInfoAre:
    """M-1: libretro_info_path is read from the global cfg and the appended files together, as RetroArch boots."""

    def test_an_appended_info_path_is_where_the_cores_declaration_is_read(self):
        info_dir = f"{HOME}/launcher/info"
        rd = _retrodeck(
            {
                REDIRECT: f'libretro_info_path = "{info_dir}"\n',
                f"{info_dir}/ppsspp_libretro.info": 'corename = "PPSSPP"\nsavestate = "false"\n',
            }
        )
        placement = rd.savestate_location(content_path=GAME, core_so="ppsspp_libretro.so", appendconfig=[REDIRECT])
        assert isinstance(placement, atlas.SavestatePlacement)
        codes = [caveat.code for caveat in placement.caveats]
        assert atlas.CAVEAT_CORE_SAVESTATES_UNSUPPORTED in codes
        assert "info-path-unresolved" not in codes
