"""Tests for a relative RetroArch save folder, opened from the launch's working directory (issue #647).

The machine vectors hold the answers end to end; these hold the pieces a
vector cannot reach on its own — the join in isolation, the hole the path math
leaves, and the routes no vector asks.
"""

from __future__ import annotations

import copy
import json
import re
from pathlib import Path
from typing import Any

import pytest

import atlas
from atlas.machine import FixtureMachine
from atlas.placement import TEMPLATE_CWD, build_savefile_placement
from atlas.retroarch_cfg import (
    CFG_LAYER_APPENDED,
    CFG_LAYER_CORE_OVERRIDE,
    SAVEFILE_KEYS,
    UPSTREAM_DEFAULTS,
    CfgSource,
    resolve_layout,
)
from tests.answers import placed, state_placed
from tests.test_machine_vectors import QUESTIONS, fixture_machine
from tests.test_appended_config import GBA_SYSTEM, RD_CFG, RD_CORES, RD_MARKER, RD_METADATA

HOME = "/home/deck"
LAUNCH = f"{HOME}/launcher"
APPENDED = CfgSource(CFG_LAYER_APPENDED, f"{LAUNCH}/redirect.cfg")
CORE = CfgSource(CFG_LAYER_CORE_OVERRIDE, "config/mGBA/mGBA.cfg")


def _layout(global_text, *, working_directory, appended=(), overrides=(), is_directory=None):
    return resolve_layout(
        global_text,
        keys=SAVEFILE_KEYS,
        home=HOME,
        cfg_label="retroarch.cfg",
        defaults=UPSTREAM_DEFAULTS,
        appended=appended,
        overrides=overrides,
        is_directory=is_directory,
        working_directory=working_directory,
    )


class TestTheLayoutOpensARelativeRootFromTheWorkingDirectory:
    """D1: RetroArch expands only ~ and :, so any other relative value hangs off the process's directory."""

    def test_a_relative_root_is_joined_to_the_working_directory_and_normalised(self):
        tested = []
        layout = _layout(
            'savefile_directory = "./saves/"\n',
            working_directory=LAUNCH,
            is_directory=lambda value: tested.append(value) or True,
        )
        assert (layout.directory, tested) == (f"{LAUNCH}/saves", [f"{LAUNCH}/saves"])

    def test_a_root_climbing_out_of_the_working_directory_is_normalised(self):
        layout = _layout('savefile_directory = "../saves"\n', working_directory=LAUNCH)
        assert layout.directory == f"{HOME}/saves"

    def test_the_template_is_joined_as_written(self):
        # Normalising "<cwd>/../saves" would cancel the climb against the
        # template itself and answer "saves", a folder nobody named.
        layout = _layout('savefile_directory = "../saves"\n', working_directory=TEMPLATE_CWD)
        assert layout.directory == "<cwd>/../saves"

    @pytest.mark.parametrize(
        ("value", "directory"),
        [("/mnt/saves", "/mnt/saves"), (":/saves", ":/saves"), ("~/saves", f"{HOME}/saves")],
        ids=["absolute", "app-relative", "tilde"],
    )
    def test_a_value_that_is_not_relative_is_not_joined(self, value, directory):
        layout = _layout(f'savefile_directory = "{value}"\n', working_directory=LAUNCH)
        assert layout.directory == directory
        assert not any("relative value" in source for source in layout.sources)

    def test_without_a_working_directory_the_value_is_taken_as_written(self):
        layout = _layout('savefile_directory = "saves"\n', working_directory=None)
        assert (layout.directory, layout.relative_root_read) == ("saves", False)

    def test_a_relative_value_a_later_read_replaced_does_not_count(self):
        layout = _layout(
            'savefile_directory = "saves"\n',
            working_directory=LAUNCH,
            overrides=((CORE, 'savefile_directory = "/mnt/saves"\n'),),
        )
        assert (layout.directory, layout.relative_root_read) == ("/mnt/saves", False)

    def test_a_default_reset_after_a_relative_value_does_not_count(self):
        layout = _layout(
            'savefile_directory = "saves"\n',
            working_directory=LAUNCH,
            overrides=((CORE, 'savefile_directory = "default"\n'),),
        )
        assert (layout.directory, layout.relative_root_read) == (None, False)

    def test_a_refused_relative_value_counts_beside_an_absolute_root(self):
        layout = _layout(
            'savefile_directory = "/mnt/saves"\n',
            working_directory=LAUNCH,
            overrides=((CORE, 'savefile_directory = "missing"\n'),),
            is_directory=lambda value: value == "/mnt/saves",
        )
        assert (layout.directory, layout.relative_root_read) == ("/mnt/saves", True)

    def test_an_absolute_chain_reads_no_relative_root(self):
        layout = _layout(
            'savefile_directory = "/mnt/saves"\n',
            working_directory=LAUNCH,
            overrides=((CORE, 'savefile_directory = "/mnt/missing"\n'),),
            is_directory=lambda value: value == "/mnt/saves",
        )
        assert [(entry.value, entry.tested) for entry in layout.rejected_directories] == [("/mnt/missing", None)]
        assert not layout.relative_root_read

    def test_every_layer_is_joined(self):
        # The boot read is the merged global cfg and appended file, so the
        # appended folder is the one read there; it is refused at its joined
        # spelling, and the override's joined folder then wins.
        tested = []
        layout = _layout(
            'savefile_directory = "global"\n',
            working_directory=LAUNCH,
            appended=((APPENDED, 'savefile_directory = "missing"\n'),),
            overrides=((CORE, 'savefile_directory = "override"\n'),),
            is_directory=lambda value: tested.append(value) or value != f"{LAUNCH}/missing",
        )
        assert layout.directory == f"{LAUNCH}/override"
        assert [(entry.layer.kind, entry.value, entry.tested) for entry in layout.rejected_directories] == [
            (CFG_LAYER_APPENDED, "missing", f"{LAUNCH}/missing")
        ]
        assert layout.relative_root_read
        assert set(tested) == {f"{LAUNCH}/missing", f"{LAUNCH}/override"}

    def test_the_source_says_the_value_is_relative(self):
        layout = _layout('savefile_directory = "saves"\n', working_directory=LAUNCH)
        assert any(
            source.startswith('retroarch.cfg: savefile_directory = "saves" — a relative value, opened from the '
                              "working directory of the RetroArch process")
            for source in layout.sources
        )


class TestTheTemplatedRootLeavesTheCwdHole:
    """The path math carries the hole wherever the template is the root it builds on."""

    def _placement(self, cfg):
        return build_savefile_placement(
            layout=_layout(cfg, working_directory=TEMPLATE_CWD),
            platform_default_dir=f"{HOME}/.config/retroarch/saves",
            content_dir_path="/roms/gba",
            content_dir_name="gba",
            library_name="mGBA",
        )

    def test_a_templated_root_needs_the_cwd(self):
        placement = self._placement('savefile_directory = "saves"\nsort_savefiles_enable = "true"\n')
        assert (placement.dir, placement.needs) == ("<cwd>/saves/mGBA", (atlas.HOLE_CWD,))

    def test_a_content_rooted_answer_needs_no_cwd(self):
        placement = self._placement('savefile_directory = "saves"\nsavefiles_in_content_dir = "true"\n')
        assert (placement.root_kind, placement.needs) == (atlas.ROOT_CONTENT_DIRECTORY, ())


def _bare(cfg: str, *, files=None, dirs=()):
    cores = f"{HOME}/.var/app/org.libretro.RetroArch/config/retroarch/cores"
    machine = FixtureMachine(
        {
            f"{HOME}/.var/app/org.libretro.RetroArch/config/retroarch/retroarch.cfg": (
                f'{cfg}libretro_directory = "{cores}"\nlibretro_info_path = "{cores}"\n'
            ),
            f"{cores}/mgba_libretro.info": 'corename = "mGBA"\nsystemname = "Game Boy Advance"\n',
            "/roms/gba/Game.gba": "",
            **(files or {}),
        },
        dirs=list(dirs),
        cores={f"{cores}/mgba_libretro.so": {"library_name": "mGBA"}},
    )
    return atlas.BareRetroArchFlatpak(HOME, machine)


class TestAContentRootedAnswerIsUntouchedByARelativeFolder:
    def test_the_content_directory_is_read_and_no_rider_rides(self):
        handle = _bare(
            'savefile_directory = "saves"\nsavefiles_in_content_dir = "true"\nsort_savefiles_enable = "false"\n',
            files={"/roms/gba/Game.srm": ""},
        )
        placement = placed(handle.savefile_location(content_path="/roms/gba/Game.gba", core_so="mgba_libretro.so"))
        assert (placement.dir, placement.needs) == ("/roms/gba", ())
        assert placement.file_set.state == atlas.FILE_SET_OBSERVED
        assert atlas.CAVEAT_SAVE_DIR_LAUNCH_DEPENDENT not in {caveat.code for caveat in placement.caveats}


class TestARelativeAppendedFolderIsTheLaunchsToo:
    """The #641 relative appended folder: no sandbox view is asked about a template, so no visibility note rides."""

    def test_without_a_cwd_it_is_the_template_and_carries_no_visibility_note(self):
        machine = FixtureMachine(
            {
                RD_MARKER: '{"paths": {"rd_home_path": "/mnt/sd/retrodeck", "saves_path": "/mnt/sd/retrodeck/saves"}}',
                RD_CFG: 'savefile_directory = "/mnt/sd/retrodeck/saves"\nlibretro_directory = "/app/cores"\n',
                RD_METADATA: "[Application]\nname=net.retrodeck.retrodeck\n\n[Context]\nfilesystems=host;\n",
                f"{LAUNCH}/redirect.cfg": 'savefile_directory = "launcher/saves"\n',
            },
            dirs=["/mnt/sd/retrodeck/saves", f"{HOME}/.var/app/net.retrodeck.retrodeck/config/ROMs"],
            cores={f"{RD_CORES}/ppsspp_libretro.so": {"library_name": "PPSSPP"}},
        )
        rd = atlas.RetroDeck(HOME, machine)
        placement = placed(
            rd.savefile_location(
                content_path="/mnt/sd/retrodeck/roms/psp/Lumines (USA).iso",
                core_so="ppsspp_libretro.so",
                appendconfig=[f"{LAUNCH}/redirect.cfg"],
            )
        )
        assert (placement.dir, placement.needs) == ("<cwd>/launcher/saves/PPSSPP", (atlas.HOLE_CWD,))
        codes = [caveat.code for caveat in placement.caveats]
        assert atlas.CAVEAT_SAVE_DIR_LAUNCH_DEPENDENT in codes
        assert atlas.CAVEAT_APPENDED_CONFIG_VISIBILITY_UNESTABLISHED not in codes
        assert atlas.CAVEAT_SAVE_DIRECTORY_VISIBILITY_UNESTABLISHED not in codes


# One fixture per arrangement, each with a relative savestate folder that only
# the caller's cwd completes — the savestate routes take cwd since #647.
RELATIVE_STATES = 'savestate_directory = "states"\nsort_savestates_enable = "false"\n'


def _retrodeck_gba():
    machine = FixtureMachine(
        {
            RD_MARKER: '{"paths": {"rd_home_path": "/mnt/sd/retrodeck", "saves_path": "/mnt/sd/retrodeck/saves"}}',
            RD_CFG: f'{RELATIVE_STATES}libretro_directory = "/app/cores"\n',
            "/var/lib/flatpak/app/net.retrodeck.retrodeck/current/active/files/retrodeck/components/es-de/share/"
            "es-de/resources/systems/linux/es_systems.xml": GBA_SYSTEM,
            RD_METADATA: "[Application]\nname=net.retrodeck.retrodeck\n\n[Context]\nfilesystems=host;\n",
        },
        dirs=[f"{HOME}/.var/app/net.retrodeck.retrodeck/config/ROMs", f"{LAUNCH}/states"],
        cores={f"{RD_CORES}/mgba_libretro.so": {"library_name": "mGBA"}},
    )
    return atlas.RetroDeck(HOME, machine)


def _emudeck_gba():
    cores = f"{HOME}/.var/app/org.libretro.RetroArch/config/retroarch/cores"
    machine = FixtureMachine(
        {
            f"{HOME}/.config/EmuDeck/settings.sh": 'romsPath="$HOME/Emulation/roms"\nsavesPath="$HOME/Emulation/saves"\n',
            f"{HOME}/.var/app/org.libretro.RetroArch/config/retroarch/retroarch.cfg": (
                f'{RELATIVE_STATES}libretro_directory = "{cores}"\n'
            ),
            f"{HOME}/ES-DE/custom_systems/es_systems.xml": GBA_SYSTEM,
            f"{HOME}/ES-DE/settings/es_settings.xml": (
                f'<?xml version="1.0"?>\n<string name="ROMDirectory" value="{HOME}/Emulation/roms" />\n'
            ),
        },
        dirs=[f"{HOME}/Emulation/bios", f"{LAUNCH}/states"],
        cores={f"{cores}/mgba_libretro.so": {"library_name": "mGBA"}},
    )
    return atlas.EmuDeck(HOME, machine)


def _bare_gba():
    return _bare(RELATIVE_STATES, dirs=[f"{LAUNCH}/states"])


@pytest.mark.parametrize("handle", [_retrodeck_gba, _emudeck_gba, _bare_gba], ids=["retrodeck", "emudeck", "bare"])
class TestEverySavestateRouteTakesTheCwd:
    def test_the_installation_question_opens_the_folder_from_the_cwd(self, handle):
        placement = state_placed(
            handle().savestate_location(content_path="/roms/gba/Game.gba", core_so="mgba_libretro.so", cwd=LAUNCH)
        )
        assert (placement.dir, placement.needs) == (f"{LAUNCH}/states", ())

    def test_the_entry_question_opens_the_folder_from_the_cwd(self, handle):
        [entry] = handle().emulators_for("gba").entries
        placement = state_placed(entry.savestate_location(content_path="/roms/gba/Game.gba", cwd=LAUNCH))
        assert (placement.dir, placement.needs) == (f"{LAUNCH}/states", ())

    def test_without_the_cwd_the_folder_is_the_template(self, handle):
        placement = state_placed(
            handle().savestate_location(content_path="/roms/gba/Game.gba", core_so="mgba_libretro.so")
        )
        assert (placement.dir, placement.needs) == ("<cwd>/states", (atlas.HOLE_CWD,))


# The rider goes where the cwd hole goes: onto an answer built on the save
# root, never onto one a card or the content flag roots elsewhere. Each case is
# a shipped vector's machine with its RetroDECK save folder made relative.
VECTORS = Path(__file__).resolve().parent.parent / "vectors" / "machines"


def _relative_save_folder_answer(family: str, name: str, question: str = "savefile_location") -> dict[str, Any]:
    vector = next(v for v in json.loads((VECTORS / f"{family}.json").read_text())["vectors"] if v["name"] == name)
    inp = copy.deepcopy(vector["input"])
    cfg = re.sub(r'savefile_directory = "[^"]*"\n', "", inp["files"][RD_CFG])
    inp["files"][RD_CFG] = f'{cfg}savefile_directory = "saves"\n'
    query_key, ask = QUESTIONS[question]
    query = {key: value for key, value in inp[query_key].items() if key != "cwd"}
    return ask(atlas.detect(inp["home"], fixture_machine(inp)), query, name)


def _rider_keys(answer: dict[str, Any]) -> list[str]:
    return [c["data"].get("key") for c in answer["caveats"] if c["code"] == atlas.CAVEAT_SAVE_DIR_LAUNCH_DEPENDENT]


class TestTheRiderGoesWhereTheHoleGoes:
    @pytest.mark.parametrize(
        ("family", "name", "question"),
        [
            ("named-cases", "flycast-shared-vmus-in-system-directory", "savefile_location"),
            ("named-cases", "lrps2-shared-memcards-through-symlink", "savefile_location"),
            ("save-file-sets", "a-floppy-saves-into-its-own-image", "savefile_location"),
            ("texture-packs", "a-wired-texture-root-answers-the-path-and-the-tree-behind-it", "texture_pack_location"),
        ],
        ids=["flycast-system-directory", "lrps2-memcards", "hatari-content-directory", "texture-system-root"],
    )
    def test_an_answer_rooted_elsewhere_carries_neither(self, family, name, question):
        answer = _relative_save_folder_answer(family, name, question)
        assert atlas.HOLE_CWD not in answer["needs"]
        assert _rider_keys(answer) == []

    @pytest.mark.parametrize(
        ("family", "name", "question"),
        [
            ("save-file-sets", "an-emulators-own-setting-routes-the-saves-elsewhere", "savefile_location"),
            ("named-cases", "flycast-per-game-vmu-mode", "savefile_location"),
            ("texture-packs", "a-saves-rooted-core-answers-and-says-nobody-has-seen-it-read", "texture_pack_location"),
        ],
        ids=["scummvm-save-root-card", "flycast-per-game-under-the-save-root", "texture-save-root"],
    )
    def test_an_answer_built_on_the_save_root_carries_both(self, family, name, question):
        answer = _relative_save_folder_answer(family, name, question)
        assert answer["dir"].startswith("<cwd>/saves")
        assert atlas.HOLE_CWD in answer["needs"]
        assert _rider_keys(answer) == ["savefile_directory"]


class TestTheRefusalKeepsTheCfgSpelling:
    def test_configured_is_the_line_to_edit_and_the_message_names_the_tested_folder(self):
        handle = _bare('savefile_directory = "saves"\nsort_savefiles_enable = "false"\n', dirs=[LAUNCH])
        placement = placed(handle.savefile_location(content_path="/roms/gba/Game.gba", cwd=LAUNCH))
        [refusal] = [c for c in placement.caveats if c.code == atlas.CAVEAT_INVALID_SAVE_DIRECTORY]
        assert refusal.data["configured"] == "saves"
        assert f"opened from the working directory as '{LAUNCH}/saves'" in refusal.message


# RetroDECK's RetroArch runs where bubblewrap starts it (#647 D2): the cwd
# where the sandbox shows it, else the sandbox's HOME, else "/".
RD_GAME = "/mnt/sd/retrodeck/roms/psp/Lumines (USA).iso"
RD_OVERRIDES = f"{HOME}/.local/share/flatpak/overrides/net.retrodeck.retrodeck"


def _rd(files, dirs=()):
    machine = FixtureMachine(
        {
            RD_MARKER: '{"paths": {"rd_home_path": "/mnt/sd/retrodeck", "saves_path": "/mnt/sd/retrodeck/saves"}}',
            RD_METADATA: "[Application]\nname=net.retrodeck.retrodeck\n\n[Context]\nfilesystems=host;\n",
            RD_GAME: "",
            **files,
        },
        dirs=["/mnt/sd/retrodeck/saves", f"{HOME}/.var/app/net.retrodeck.retrodeck/config/ROMs", *dirs],
        cores={f"{RD_CORES}/ppsspp_libretro.so": {"library_name": "PPSSPP"}},
    )
    return atlas.RetroDeck(HOME, machine)


RELATIVE_SAVES = 'savefile_directory = "saves"\nsort_savefiles_enable = "false"\nlibretro_directory = "/app/cores"\n'
HIDDEN_CWD = f"{HOME}/.var/app/org.other.App"


class TestRetroDecksWorkingDirectoryIsReadInTheSandbox:
    def test_with_its_home_hidden_too_the_process_runs_in_the_root(self):
        rd = _rd(
            {RD_CFG: RELATIVE_SAVES, RD_OVERRIDES: "[Environment]\nHOME=/var/elsewhere\n"},
            dirs=[HIDDEN_CWD, "/var/elsewhere", "/saves"],
        )
        placement = placed(rd.savefile_location(content_path=RD_GAME, core_so="ppsspp_libretro.so", cwd=HIDDEN_CWD))
        assert placement.dir == "/saves"
        [note] = [c for c in placement.caveats if c.code == atlas.CAVEAT_CWD_HIDDEN_IN_SANDBOX]
        assert dict(note.data) == {
            "app_id": "net.retrodeck.retrodeck",
            "cwd": HIDDEN_CWD,
            "working_directory": "/",
        }

    def test_a_hidden_cwd_says_nothing_where_no_folder_is_relative(self):
        rd = _rd(
            {RD_CFG: 'savefile_directory = "/mnt/sd/retrodeck/saves"\nlibretro_directory = "/app/cores"\n'},
            dirs=[HIDDEN_CWD],
        )
        placement = placed(rd.savefile_location(content_path=RD_GAME, core_so="ppsspp_libretro.so", cwd=HIDDEN_CWD))
        assert atlas.CAVEAT_CWD_HIDDEN_IN_SANDBOX not in {c.code for c in placement.caveats}

    def test_a_cwd_the_model_cannot_tell_notes_an_appended_relative_folder_too(self):
        # The appended folder's own unknown states the appended note; the cwd
        # it is joined to states the save-directory one (D2).
        rd = _rd(
            {
                RD_CFG: 'savefile_directory = "/mnt/sd/retrodeck/saves"\nlibretro_directory = "/app/cores"\n',
                RD_OVERRIDES: "[Context]\nfilesystems=host;\n",
                f"{LAUNCH}/redirect.cfg": 'savefile_directory = "saves"\n',
            },
            dirs=[f"{LAUNCH}/saves"],
        )
        placement = placed(
            rd.savefile_location(
                content_path=RD_GAME, core_so="ppsspp_libretro.so", cwd=LAUNCH, appendconfig=[f"{LAUNCH}/redirect.cfg"]
            )
        )
        assert placement.dir.startswith(f"{LAUNCH}/saves")
        assert atlas.CAVEAT_SAVE_DIRECTORY_VISIBILITY_UNESTABLISHED in {c.code for c in placement.caveats}

    def test_an_unmodelled_sandbox_runs_in_the_cwd_as_named(self):
        # D2 is RetroDECK's sandbox: elsewhere the cwd is the process's own.
        handle = _bare('savefile_directory = "saves"\nsort_savefiles_enable = "false"\n', dirs=[f"{HIDDEN_CWD}/saves"])
        placement = placed(handle.savefile_location(content_path="/roms/gba/Game.gba", cwd=HIDDEN_CWD))
        assert placement.dir == f"{HIDDEN_CWD}/saves"
        assert atlas.CAVEAT_CWD_HIDDEN_IN_SANDBOX not in {c.code for c in placement.caveats}

    def test_a_home_the_model_cannot_tell_is_taken_and_says_so(self):
        # The cwd is hidden, so bubblewrap falls back to HOME; under /run/user
        # the model cannot tell whether the sandbox shows it. The appended
        # file's folder carries its own note, so the save-directory one here
        # can only come from the HOME it was joined to.
        rd = _rd(
            {
                RD_CFG: 'savefile_directory = "/mnt/sd/retrodeck/saves"\nlibretro_directory = "/app/cores"\n',
                RD_OVERRIDES: "[Environment]\nHOME=/run/user/1000/home\n",
                f"{LAUNCH}/redirect.cfg": 'savefile_directory = "saves"\nsort_savefiles_enable = "false"\n',
            },
            dirs=[HIDDEN_CWD, "/run/user/1000/home/saves"],
        )
        placement = placed(
            rd.savefile_location(
                content_path=RD_GAME, core_so="ppsspp_libretro.so", cwd=HIDDEN_CWD, appendconfig=[f"{LAUNCH}/redirect.cfg"]
            )
        )
        assert placement.dir == "/run/user/1000/home/saves"
        notes = {c.code: dict(c.data) for c in placement.caveats}
        assert notes[atlas.CAVEAT_CWD_HIDDEN_IN_SANDBOX]["working_directory"] == "/run/user/1000/home"
        assert atlas.CAVEAT_SAVE_DIRECTORY_VISIBILITY_UNESTABLISHED in notes

    def test_a_relative_folder_an_absolute_override_replaces_says_nothing_about_the_cwd(self):
        rd = _rd(
            {
                RD_CFG: RELATIVE_SAVES,
                f"{HOME}/.var/app/net.retrodeck.retrodeck/config/retroarch/config/PPSSPP/PPSSPP.cfg": (
                    'savefile_directory = "/mnt/sd/retrodeck/saves"\n'
                ),
            },
            dirs=[HIDDEN_CWD, f"{HOME}/saves"],
        )
        placement = placed(rd.savefile_location(content_path=RD_GAME, core_so="ppsspp_libretro.so", cwd=HIDDEN_CWD))
        assert placement.dir == "/mnt/sd/retrodeck/saves"
        assert atlas.CAVEAT_CWD_HIDDEN_IN_SANDBOX not in {c.code for c in placement.caveats}

    def test_the_savestate_folder_opens_from_the_home_too(self):
        rd = _rd(
            {RD_CFG: 'savestate_directory = "states"\nsort_savestates_enable = "false"\nlibretro_directory = "/app/cores"\n'},
            dirs=[HIDDEN_CWD, f"{HOME}/states"],
        )
        placement = state_placed(
            rd.savestate_location(content_path=RD_GAME, core_so="ppsspp_libretro.so", cwd=HIDDEN_CWD)
        )
        assert placement.dir == f"{HOME}/states"
        [note] = [c for c in placement.caveats if c.code == atlas.CAVEAT_CWD_HIDDEN_IN_SANDBOX]
        assert dict(note.data) == {"app_id": "net.retrodeck.retrodeck", "cwd": HIDDEN_CWD, "working_directory": HOME}
