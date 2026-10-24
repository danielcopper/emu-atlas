"""Tests for the answers past the save folders that RetroArch's working directory decides (issue #660).

Inside RetroDECK's sandbox RetroArch runs in the caller's ``cwd`` only where
the sandbox shows it, otherwise in the sandbox's ``HOME`` (#647 D2). The
DeSmuME 2015 ``working_directory`` root and the MAME core's relative trees
read that same directory. The machine vectors hold each answer per outcome;
these hold what a vector does not isolate — one note per answer where a save
folder read the directory first, a ``HOME`` the model cannot tell, and the
rule's own account of whether it read the directory at all.
"""

from __future__ import annotations

import pytest

import atlas
from atlas.machine import FixtureMachine
from atlas.mode_rules import _mame_reads_cwd  # pyright: ignore[reportPrivateUsage]
from tests.answers import placed
from tests.test_appended_config import RD_CFG, RD_CORES, RD_MARKER, RD_METADATA

HOME = "/home/deck"
HIDDEN_CWD = f"{HOME}/.var/app/org.other.App"
UNTOLD_CWD = "/run/user/1000/launcher"
RD_OVERRIDES = f"{HOME}/.local/share/flatpak/overrides/net.retrodeck.retrodeck"
NDS_GAME = "/mnt/sd/retrodeck/roms/nds/Game (Europe).nds"
ARCADE_GAME = "/mnt/sd/retrodeck/roms/arcade/dkong.zip"
CORE_OPTIONS = f"{HOME}/.var/app/net.retrodeck.retrodeck/config/retroarch/retroarch-core-options.cfg"
RELATIVE_SAVES = 'savefile_directory = "saves"\nsort_savefiles_enable = "false"\n'
TOGGLE = {"default": "disabled", "values": ["disabled", "enabled"]}


def _rd(cfg: str, *, files=None, dirs=()):
    machine = FixtureMachine(
        {
            RD_MARKER: '{"paths": {"rd_home_path": "/mnt/sd/retrodeck", "saves_path": "/mnt/sd/retrodeck/saves"}}',
            RD_METADATA: "[Application]\nname=net.retrodeck.retrodeck\n\n[Context]\nfilesystems=host;\n",
            RD_CFG: f'{cfg}sort_savefiles_by_content_enable = "false"\nglobal_core_options = "true"\n'
            'libretro_directory = "/app/cores"\n',
            CORE_OPTIONS: 'mame_mame_paths_enable = "enabled"\n',
            NDS_GAME: "",
            ARCADE_GAME: "",
            **(files or {}),
        },
        dirs=["/mnt/sd/retrodeck/saves", f"{HOME}/.var/app/net.retrodeck.retrodeck/config/ROMs", *dirs],
        cores={
            f"{RD_CORES}/desmume2015_libretro.so": {"library_name": "DeSmuME 2015", "library_version": "git af397ff"},
            f"{RD_CORES}/mame_libretro.so": {
                "library_name": "MAME",
                "library_version": "0.282 (a90e86e1)",
                "options": {"mame_mame_paths_enable": TOGGLE, "mame_read_config": TOGGLE},
            },
        },
    )
    return atlas.RetroDeck(HOME, machine)


def _desmume(rd, cwd):
    return placed(rd.savefile_location(content_path=NDS_GAME, core_so="desmume2015_libretro.so", cwd=cwd))


def _mame(rd, cwd):
    return placed(rd.savefile_location(content_path=ARCADE_GAME, core_so="mame_libretro.so", cwd=cwd))


def _codes(placement) -> list[str]:
    return [c.code for c in placement.caveats]


def _redirects(placement) -> dict[str, str]:
    return {
        str(c.data["key"]): str(c.data["path"])
        for c in placement.caveats
        if c.code == atlas.CAVEAT_SAVE_ROOT_REDIRECTED
    }


class TestTheWorkingDirectoryRootStatesTheLaunchOnce:
    """A relative save folder read the same directory first; the root adds only what the chain left out."""

    def test_a_hidden_cwd_is_named_once(self):
        rd = _rd(RELATIVE_SAVES, dirs=[HIDDEN_CWD, f"{HOME}/saves"])
        placement = _desmume(rd, HIDDEN_CWD)
        assert placement.dir == HOME
        assert _codes(placement).count(atlas.CAVEAT_CWD_HIDDEN_IN_SANDBOX) == 1

    def test_a_cwd_the_model_cannot_tell_is_noted_once(self):
        rd = _rd(RELATIVE_SAVES, dirs=[f"{UNTOLD_CWD}/saves"])
        placement = _desmume(rd, UNTOLD_CWD)
        assert placement.dir == UNTOLD_CWD
        assert _codes(placement).count(atlas.CAVEAT_SAVE_DIRECTORY_VISIBILITY_UNESTABLISHED) == 1

    def test_a_home_the_model_cannot_tell_is_taken_and_says_so(self):
        # The cwd is hidden, so bubblewrap falls back to HOME; under /run/user
        # the model cannot tell whether the sandbox shows it. The save folder
        # is absolute and shown, so both notes can only come from the root.
        rd = _rd(
            'savefile_directory = "/mnt/sd/retrodeck/saves"\n',
            files={RD_OVERRIDES: "[Environment]\nHOME=/run/user/1000/home\n"},
            dirs=[HIDDEN_CWD],
        )
        placement = _desmume(rd, HIDDEN_CWD)
        assert placement.dir == "/run/user/1000/home"
        notes = {c.code: dict(c.data) for c in placement.caveats}
        assert notes[atlas.CAVEAT_CWD_HIDDEN_IN_SANDBOX]["working_directory"] == "/run/user/1000/home"
        assert atlas.CAVEAT_SAVE_DIRECTORY_VISIBILITY_UNESTABLISHED in notes


class TestMameTreesStateTheLaunchOnce:
    def test_a_hidden_cwd_is_named_once_beside_a_relative_save_folder(self):
        rd = _rd(RELATIVE_SAVES, dirs=[HIDDEN_CWD, f"{HOME}/saves"])
        placement = _mame(rd, HIDDEN_CWD)
        assert _redirects(placement)["nvram_directory"] == f"{HOME}/states/mame/nvram"
        assert _codes(placement).count(atlas.CAVEAT_CWD_HIDDEN_IN_SANDBOX) == 1

    def test_a_cwd_the_model_cannot_tell_is_noted_once_beside_a_relative_save_folder(self):
        rd = _rd(RELATIVE_SAVES, dirs=[f"{UNTOLD_CWD}/saves"])
        placement = _mame(rd, UNTOLD_CWD)
        assert _redirects(placement)["nvram_directory"] == f"{UNTOLD_CWD}/states/mame/nvram"
        assert _codes(placement).count(atlas.CAVEAT_SAVE_DIRECTORY_VISIBILITY_UNESTABLISHED) == 1


class TestTheMameRuleSaysWhetherItReadTheWorkingDirectory:
    """``reads_cwd`` is true exactly where a tree was joined to it — the notes ride on nothing else."""

    @pytest.mark.parametrize(
        ("values", "cwd", "reads"),
        [
            ({}, "/launch", True),
            ({}, None, False),
            (
                {"nvram_directory": "/a", "cfg_directory": "/b", "diff_directory": "$HOME/diff"},
                "/launch",
                False,
            ),
            ({"nvram_directory": "/a", "cfg_directory": "/b", "diff_directory": "diff"}, "/launch", True),
            ({"nvram_directory": "/a;rel", "cfg_directory": "/b", "diff_directory": "/c"}, "/launch", False),
        ],
        ids=["defaults", "no-cwd", "absolute-and-environment", "one-relative", "relative-second-element"],
    )
    def test_only_a_joined_tree_counts(self, values, cwd, reads):
        assert _mame_reads_cwd(values, cwd) is reads
