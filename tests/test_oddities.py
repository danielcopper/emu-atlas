"""Tests for atlas.oddities — rule cards and their application in the resolver."""

from __future__ import annotations

import importlib.resources
import json
from pathlib import Path
from typing import Mapping

import pytest

import atlas
from atlas.machine import CoreOption, FixtureMachine, RealMachine
from atlas.mode_rules import RULES as MODE_RULES
from atlas.oddities import (
    ANCHOR_KINDS,
    MODE_ALWAYS,
    CoreCard,
    load_audit,
    load_oddities,
    lookup_card,
    recorded_vocabulary,
)
from atlas.placement import (
    GRANULARITIES,
    GRANULARITY_NONE,
    GRANULARITY_PER_GAME_FILE,
    GRANULARITY_PER_GAME_FILES,
    GRANULARITY_SHARED_CARD,
    ROOT_KINDS,
)
from tests.answers import placed, state_placed

HOME = "/home/deck"
RETRODECK_JSON = f"{HOME}/.var/app/net.retrodeck.retrodeck/config/retrodeck/retrodeck.json"
RETRODECK_CFG = f"{HOME}/.var/app/net.retrodeck.retrodeck/config/retroarch/retroarch.cfg"
OPTIONS_CFG = f"{HOME}/.var/app/net.retrodeck.retrodeck/config/retroarch/retroarch-core-options.cfg"
FLYCAST_GAME_OPT = f"{HOME}/.var/app/net.retrodeck.retrodeck/config/retroarch/config/Flycast/Dreamcast Game (Europe).opt"
FLYCAST_CORE_OVERRIDE = f"{HOME}/.var/app/net.retrodeck.retrodeck/config/retroarch/config/Flycast/Flycast.cfg"
FLYCAST_CORE_OPT = f"{HOME}/.var/app/net.retrodeck.retrodeck/config/retroarch/config/Flycast/Flycast.opt"

RD_JSON = '{"paths": {"rd_home_path": "/mnt/sd/retrodeck", "saves_path": "/mnt/sd/retrodeck/saves"}}'
SAVES_KEEP = "/mnt/sd/retrodeck/saves/.keep"
CFG = (
    'savefile_directory = "/mnt/sd/retrodeck/saves"\n'
    'sort_savefiles_by_content_enable = "true"\nsort_savefiles_enable = "false"\n'
    'system_directory = "/mnt/sd/retrodeck/bios"\n'
    'global_core_options = "true"\n'
    'libretro_directory = "/app/cores"\n'
)
CFG_WITHOUT_GLOBAL_OPTS = CFG.replace('global_core_options = "true"\n', "")
DEPLOY = "/var/lib/flatpak/app/net.retrodeck.retrodeck/current/active/files/cores"
ROM_STEM = "Dreamcast Game (Europe)"
ROM = f"/mnt/sd/retrodeck/roms/dreamcast/{ROM_STEM}.gdi"

# Where RetroDECK deploys the core binaries RetroArch loads — the real ones, not
# a fixture's. Its per-emulator payload lives in the Flatpak, not in RetroDECK's
# Git repository.
DEPLOYED_CORES = Path(
    "/var/lib/flatpak/app/net.retrodeck.retrodeck/current/active/files/retrodeck/components"
    "/retroarch/rd_extras/cores"
)


def _mode(granularity: str, **group: object) -> dict[str, object]:
    """The smallest mode a card can state: one group, in the save directory."""
    return {
        "root": "savefile_directory",
        "groups": [{"granularity": granularity, "role": "battery", **group}],
    }


class TestCardLookup:
    def test_by_so_basename(self):
        card = lookup_card(so_basename="flycast_libretro.so", library_name=None)
        assert card is not None
        assert card.key == "flycast"

    def test_by_library_name(self):
        card = lookup_card(so_basename=None, library_name="Flycast")
        assert card is not None
        assert card.key == "flycast"

    def test_no_card_for_ordinary_core(self):
        assert lookup_card(so_basename="mgba_libretro.so", library_name="mGBA") is None

    def test_the_so_name_comes_from_the_card_key(self):
        # Not a field any more: the key IS the .so basename. What matters is
        # that every shipped card is still found under the name the derivation
        # produces — a restated spelling was a way for those two to disagree.
        for card in load_oddities():
            assert card.so_name == f"{card.key}_libretro.so"
            found = lookup_card(so_basename=card.so_name, library_name=None)
            assert found is not None
            assert found.key == card.key

    def test_card_modes_and_default(self):
        card = lookup_card(so_basename="flycast_libretro.so", library_name=None)
        assert card is not None
        assert card.option_key == "reicast_per_content_vmus"
        # No recorded default: the deployed core registers one, and a card copy
        # of a value the machine states is the second, ageing copy the boundary
        # rule refuses. LRPS2, which registers nothing, is the one that keeps it.
        assert card.option_default is None
        lrps2 = lookup_card(so_basename="pcsx2_libretro.so", library_name=None)
        assert lrps2 is not None
        assert lrps2.option_default == "enabled"
        assert set(card.modes) == {"disabled", "VMU A1", "All VMUs"}
        assert card.modes["disabled"].files is not None
        # 'VMU A1' states everything since #97: its own per-content card, and
        # the parts that stay behind as cross-root groups.
        assert card.modes["VMU A1"].files == ("<save_id>.A1.bin",)
        assert [g.root for g in card.modes["VMU A1"].groups] == [
            None,
            "system_directory",
            "system_directory",
        ]
        # 'All VMUs' names its files through the content's own id — a template,
        # kept as one: atlas states the shape, never the id. 'VMU A1' moves one
        # port and leaves the rest on the shared card, which one root cannot say.
        assert card.modes["All VMUs"].files == (
            "<save_id>.A1.bin",
            "<save_id>.B1.bin",
            "<save_id>.C1.bin",
            "<save_id>.D1.bin",
        )
        assert card.modes["All VMUs"].files_without_save_id == (
            "<rom_stem>.A1.bin",
            "<rom_stem>.B1.bin",
            "<rom_stem>.C1.bin",
            "<rom_stem>.D1.bin",
        )


def _retrodeck(files, **kwargs):
    machine = FixtureMachine(files, **kwargs)
    return atlas.RetroDeck(HOME, machine)


# What the deployed cores really register, so a fixture core answers the way the
# shipped binary does. The default lives here rather than in the card: the card
# records one only for a core that registers none, and these two register theirs.
# TestTheModeKeysAreTheDeployedCoresOwn measures both sets against the binaries.
FLYCAST_REGISTERED = {
    "reicast_per_content_vmus": {"default": "disabled", "values": ["disabled", "VMU A1", "All VMUs"]}
}
OPERA_REGISTERED = {"opera_nvram_storage": {"default": "per game", "values": ["per game", "shared"]}}
FLYCAST_CORE = {"library_name": "Flycast", "options": FLYCAST_REGISTERED}
OPERA_CORE = {"library_name": "Opera", "options": OPERA_REGISTERED}


def _flycast_query(files):
    rd = _retrodeck(files, cores={f"{DEPLOY}/flycast_libretro.so": FLYCAST_CORE})
    return placed(rd.savefile_location(content_path=ROM, core_so="flycast_libretro.so"))


FLYCAST_SLOT2_REGISTERED = {
    **FLYCAST_REGISTERED,
    **{
        f"reicast_device_port{i}_slot2": {
            "default": "Purupuru",
            "values": ["VMU", "Purupuru", "None"],
        }
        for i in (1, 2, 3, 4)
    },
}


class TestFlycastSlot2ObservationGate:
    """Issue #89: a slot-2 card that cannot exist here is not probed blind."""

    FILES = {
        RETRODECK_JSON: RD_JSON,
        RETRODECK_CFG: CFG,
        ROM: "",
        "/mnt/sd/retrodeck/bios/dc/vmu_save_A1.bin": {"status": "invalid-text"},
        "/mnt/sd/retrodeck/bios/dc/vmu_save_A2.bin": {"status": "invalid-text"},
        OPTIONS_CFG: 'reicast_per_content_vmus = "disabled"\n',
    }

    def _query(self, files, registered=FLYCAST_SLOT2_REGISTERED):
        core = {"library_name": "Flycast", "options": registered}
        rd = _retrodeck(files, cores={f"{DEPLOY}/flycast_libretro.so": core})
        return placed(rd.savefile_location(content_path=ROM, core_so="flycast_libretro.so"))

    def test_the_registered_default_rules_the_stale_card_out(self):
        p = self._query(self.FILES)
        assert p.file_set.state == "observed"
        assert "vmu_save_A2.bin" not in p.file_set.files
        assert p.granularity is not None
        slot2 = [r for r in p.granularity.readings if r.key.endswith("_slot2")]
        assert [(r.key, r.value) for r in slot2] == [
            (f"reicast_device_port{i}_slot2", "Purupuru") for i in (1, 2, 3, 4)
        ]

    def test_a_slot_set_to_vmu_keeps_its_candidate(self):
        p = self._query(
            {
                **self.FILES,
                OPTIONS_CFG: 'reicast_per_content_vmus = "disabled"\n'
                'reicast_device_port1_slot2 = "VMU"\n',
            }
        )
        assert "vmu_save_A2.bin" in p.file_set.files

    def test_a_switch_nobody_could_read_excludes_nothing(self):
        # "Cannot exist" is a claim, not a default: without the registration
        # and without a stored value, the candidates stay and the probe is
        # as blind as it always was.
        p = self._query(self.FILES, registered=FLYCAST_REGISTERED)
        assert "vmu_save_A2.bin" in p.file_set.files


MAME_ROM = "/mnt/sd/retrodeck/roms/arcade/dkong.zip"
MAME_REGISTERED = {
    "mame_mame_paths_enable": {"default": "disabled", "values": ["disabled", "enabled"]},
    "mame_read_config": {"default": "disabled", "values": ["disabled", "enabled"]},
}
MAME_CORE = {"library_name": "MAME", "options": MAME_REGISTERED}


def _mame_query(files):
    rd = _retrodeck(files, cores={f"{DEPLOY}/mame_libretro.so": MAME_CORE})
    return placed(rd.savefile_location(content_path=MAME_ROM, core_so="mame_libretro.so"))


class TestADirectoryWhoseNamesAreNotEstablished:
    """MAME's differencing tree: stated as a place, refused as a list of names."""

    FILES = {RETRODECK_JSON: RD_JSON, RETRODECK_CFG: CFG, SAVES_KEEP: ""}

    def test_it_is_a_group_like_any_other_place_the_save_lives(self):
        # The point of the shape: one walk over `groups` reaches every directory
        # the card knows about. Before, this one was reachable only by scanning
        # the caveats, which is a second structure to correlate and the kind of
        # thing a client silently skips.
        p = _mame_query(self.FILES)
        unnamed = [g for g in p.file_set.groups if g.files is None]
        assert len(unnamed) == 1
        assert unnamed[0].dir.endswith("/diff")
        assert unnamed[0].role == atlas.ROLE_DISK_DIFF

    def test_the_flat_list_is_untouched_by_it(self):
        # `files` stays the names a caller can look for, so a group without
        # names contributes nothing to it and no name moved.
        p = _mame_query(self.FILES)
        here = tuple(
            name
            for g in p.file_set.groups
            if g.dir == p.file_set.groups[0].dir and g.files is not None
            for name in g.files
        )
        assert here == p.file_set.files

    def test_the_caveat_still_travels_with_the_reason(self):
        # The group carries the place; the caveat carries the sentence a person
        # reads and the citation behind it. Both, not one instead of the other.
        p = _mame_query(self.FILES)
        caveat = next(c for c in p.caveats if c.code == atlas.CAVEAT_FILE_NAMES_UNESTABLISHED)
        assert caveat.data["dir"].endswith("/diff")
        assert caveat.data["role"] == atlas.ROLE_DISK_DIFF
        assert caveat.data["citation"]
        assert caveat.message

    def test_every_directory_the_caveats_name_is_also_a_group(self):
        p = _mame_query(self.FILES)
        flagged = {
            c.data["dir"] for c in p.caveats if c.code == atlas.CAVEAT_FILE_NAMES_UNESTABLISHED
        }
        assert flagged <= {g.dir for g in p.file_set.groups}


PRBOOM_ROM = "/mnt/sd/retrodeck/roms/doom/Doom (USA).wad"
VQ2_ROM = "/mnt/sd/retrodeck/roms/quake2/baseq2/pak0.pak"


def _prboom_query(files, content_path=PRBOOM_ROM):
    rd = _retrodeck(files, cores={f"{DEPLOY}/prboom_libretro.so": {"library_name": "PrBoom"}})
    return placed(rd.savefile_location(content_path=content_path, core_so="prboom_libretro.so"))


def _vitaquake2_query(files):
    rd = _retrodeck(files, cores={f"{DEPLOY}/vitaquake2_libretro.so": {"library_name": "vitaQuakeII"}})
    return placed(rd.savefile_location(content_path=VQ2_ROM, core_so="vitaquake2_libretro.so"))


class TestASubdirNamedAfterTheContent:
    """The two subdir templates: prboom keys by stem, vitaquake2 by directory name."""

    FILES = {RETRODECK_JSON: RD_JSON, RETRODECK_CFG: CFG, SAVES_KEEP: ""}

    def test_the_stem_template_fills_from_the_content(self):
        p = _prboom_query(self.FILES)
        # RetroDECK's content sorting puts the root at saves/doom, and the
        # core's own subdirectory — the content's stem — nests on top of it,
        # exactly the order the frontend hands directories to cores.
        assert p.dir == "/mnt/sd/retrodeck/saves/doom/Doom (USA)"
        assert p.needs == ()
        assert p.file_set.state == "declared"
        assert p.file_set.files == (
            "prbmsav0.dsg",
            "prbmsav1.dsg",
            "prbmsav2.dsg",
            "prbmsav3.dsg",
            "prbmsav4.dsg",
            "prbmsav5.dsg",
            "prbmsav6.dsg",
            "prbmsav7.dsg",
            "prboom.cfg",
        )

    def test_one_directory_splits_into_progress_and_settings(self):
        p = _prboom_query(self.FILES)
        assert [g.role for g in p.file_set.groups] == [atlas.ROLE_BATTERY, atlas.ROLE_SETTINGS]
        assert {g.dir for g in p.file_set.groups} == {"/mnt/sd/retrodeck/saves/doom/Doom (USA)"}

    def test_the_dehacked_scope_travels_as_a_caveat(self):
        # The eight savegame names hold unless a .deh the loader passes renames
        # the base — a fact about the content, so it rides machine-readably.
        p = _prboom_query(self.FILES)
        caveat = next(c for c in p.caveats if c.code == atlas.CAVEAT_FILENAMES_CONTENT_CONDITIONAL)
        assert "DeHackEd" in caveat.data["files_established_for"]

    def test_the_directory_name_template_fills_from_the_contents_directory(self):
        p = _vitaquake2_query(self.FILES)
        # Sorting keys by the content's directory name and so does the core, so
        # the segment really appears twice — that is what the machine does.
        assert p.dir == "/mnt/sd/retrodeck/saves/baseq2/baseq2"
        assert p.file_set.files == ("config.cfg",)
        unnamed = [g for g in p.file_set.groups if g.files is None]
        assert len(unnamed) == 1
        assert unnamed[0].dir == "/mnt/sd/retrodeck/saves/baseq2/baseq2/save"
        assert unnamed[0].role == atlas.ROLE_BATTERY
        caveat = next(c for c in p.caveats if c.code == atlas.CAVEAT_FILE_NAMES_UNESTABLISHED)
        assert caveat.data["dir"] == "/mnt/sd/retrodeck/saves/baseq2/baseq2/save"

    def test_an_unfilled_template_is_a_hole_not_a_guess(self):
        # Without content the token cannot fill; the answer keeps it in the
        # path and names the hole — the shape <content_dir> already has — and
        # nothing is observed at a path that is still a template.
        machine = FixtureMachine(
            {
                "/home/deck/.config/retroarch/retroarch.cfg": (
                    'savefile_directory = "/home/deck/saves"\n'
                    'sort_savefiles_by_content_enable = "false"\nsort_savefiles_enable = "false"\n'
                    'libretro_directory = "/home/deck/cores"\n'
                ),
                "/home/deck/saves/.keep": "",
            },
            cores={"/home/deck/cores/prboom_libretro.so": {"library_name": "PrBoom"}},
        )
        p = placed(
            atlas.BareRetroArchNative(HOME, machine).savefile_location(core_so="prboom_libretro.so")
        )
        assert p.dir == "/home/deck/saves/<rom_stem>"
        assert "rom_stem" in p.needs
        assert p.file_set.state == "unknown"

    def test_a_hole_in_the_base_does_not_swallow_the_cards_subdir(self):
        # Content sorting with no content named leaves a hole in the base —
        # which withholds the observation, not the nesting: the core appends
        # its subdirectory to whatever the frontend hands it, so the answer
        # must carry both template segments with both holes, never state the
        # parent as the final directory.
        machine = FixtureMachine(
            {
                "/home/deck/.config/retroarch/retroarch.cfg": (
                    'savefile_directory = "/home/deck/saves"\n'
                    'sort_savefiles_by_content_enable = "true"\nsort_savefiles_enable = "false"\n'
                    'libretro_directory = "/home/deck/cores"\n'
                ),
                "/home/deck/saves/.keep": "",
            },
            cores={
                "/home/deck/cores/prboom_libretro.so": {"library_name": "PrBoom"},
                "/home/deck/cores/tyrquake_libretro.so": {"library_name": "TyrQuake"},
            },
        )
        bare = atlas.BareRetroArchNative(HOME, machine)
        p = placed(bare.savefile_location(core_so="prboom_libretro.so"))
        assert p.dir == "/home/deck/saves/<content_dir>/<rom_stem>"
        assert "content_dir" in p.needs
        assert "rom_stem" in p.needs
        p = placed(bare.savefile_location(core_so="tyrquake_libretro.so"))
        assert p.dir == "/home/deck/saves/<content_dir>/<content_dir_name>"
        assert "content_dir" in p.needs
        assert "content_dir_name" in p.needs
        assert p.file_set.state == "unknown"


BOOM3_ROM = "/mnt/sd/retrodeck/roms/doom3/base/pak000.pk4"
VQ3_ROM = "/mnt/sd/retrodeck/roms/quake3/baseq3/pak0.pk3"


class TestACardRootedInTheContentsOwnTree:
    """boom3 and vitaquake3 write beside the content — the card's root is content_directory."""

    FILES = {RETRODECK_JSON: RD_JSON, RETRODECK_CFG: CFG, SAVES_KEEP: ""}

    def _boom3_query(self, files, content_path=BOOM3_ROM):
        rd = _retrodeck(files, cores={f"{DEPLOY}/boom3_libretro.so": {"library_name": "boom3"}})
        return placed(rd.savefile_location(content_path=content_path, core_so="boom3_libretro.so"))

    def test_the_answer_roots_in_the_contents_directory(self):
        # The live check that found the gap this class closes: a card may say
        # content_directory, and the resolver must not answer the save root.
        p = self._boom3_query(self.FILES)
        assert p.root_kind == atlas.ROOT_CONTENT_DIRECTORY
        assert p.dir == "/mnt/sd/retrodeck/roms/doom3/base"
        assert p.file_set.files == ("libretro.cfg",)
        unnamed = [g for g in p.file_set.groups if g.files is None]
        assert len(unnamed) == 1
        assert unnamed[0].dir == "/mnt/sd/retrodeck/roms/doom3/base/savegames"
        assert unnamed[0].role == atlas.ROLE_BATTERY
        caveat = next(c for c in p.caveats if c.code == atlas.CAVEAT_FILE_NAMES_UNESTABLISHED)
        assert caveat.data["dir"] == "/mnt/sd/retrodeck/roms/doom3/base/savegames"

    def test_an_observed_config_is_seen_where_it_lies(self):
        p = self._boom3_query(
            {**self.FILES, "/mnt/sd/retrodeck/roms/doom3/base/libretro.cfg": "cfg"}
        )
        assert p.file_set.state == "observed"
        assert p.file_set.files == ("libretro.cfg",)

    def test_vitaquake3_states_one_settings_file(self):
        rd = _retrodeck(
            self.FILES,
            cores={f"{DEPLOY}/vitaquake3_libretro.so": {"library_name": "vitaQuakeIII"}},
        )
        p = placed(rd.savefile_location(content_path=VQ3_ROM, core_so="vitaquake3_libretro.so"))
        assert p.root_kind == atlas.ROOT_CONTENT_DIRECTORY
        assert p.dir == "/mnt/sd/retrodeck/roms/quake3/baseq3"
        assert [g.role for g in p.file_set.groups] == [atlas.ROLE_SETTINGS]

    def test_without_content_the_directory_is_the_hole(self):
        rd = _retrodeck(
            self.FILES, cores={f"{DEPLOY}/boom3_libretro.so": {"library_name": "boom3"}}
        )
        p = placed(rd.savefile_location(core_so="boom3_libretro.so"))
        assert p.dir == "<content_dir>"
        assert atlas.HOLE_CONTENT_DIR in p.needs
        # The names are fixed, so the declaration stands even without content —
        # only the directory is the hole, exactly as the template says.
        assert p.file_set.state == "declared"
        assert p.file_set.files == ("libretro.cfg",)


class TestACardRootedInTheLaunchsWorkingDirectory:
    """DeSmuME 2015 writes relative to the launching process's cwd — a launch property."""

    FILES = {RETRODECK_JSON: RD_JSON, RETRODECK_CFG: CFG, SAVES_KEEP: ""}
    DS_ROM = "/mnt/sd/retrodeck/roms/nds/Game (Europe).nds"

    def _query(self, files):
        rd = _retrodeck(
            files,
            cores={f"{DEPLOY}/desmume2015_libretro.so": {"library_name": "DeSmuME 2015"}},
        )
        return placed(
            rd.savefile_location(content_path=self.DS_ROM, core_so="desmume2015_libretro.so")
        )

    def test_the_directory_is_the_launchs_and_stays_a_hole(self):
        p = self._query(self.FILES)
        assert p.root_kind == atlas.ROOT_WORKING_DIRECTORY
        assert p.dir == "<cwd>"
        assert p.needs == (atlas.HOLE_CWD,)
        # The names are established even though the directory is not — that is
        # the whole reason this root exists rather than a refusal.
        assert p.file_set.state == "declared"
        assert p.file_set.files == ("Game (Europe).dsv", "Game (Europe).dsv.bak")
        assert [g.dir for g in p.file_set.groups] == ["<cwd>"]

    def test_the_caveat_says_whose_property_the_directory_is(self):
        p = self._query(self.FILES)
        caveat = next(c for c in p.caveats if c.code == atlas.CAVEAT_SAVE_DIR_LAUNCH_DEPENDENT)
        assert caveat.data["core"] == "desmume2015"

    def test_nothing_on_the_machine_is_read_for_a_template(self):
        # A path that is still a template names nothing to look at: no
        # observation, no link view, no fallback — the machine has no say here.
        p = self._query(self.FILES)
        assert p.physical_dir is None
        assert p.fallback_dir is None


class TestAnOptionThatMovesTheRootItself:
    """quasi88: the same option picks between the save root and the content's own tree.

    The first card whose modes stand on different roots — disabled keeps a
    differencing file under the save directory, enabled writes the sectors into
    the loaded disk image itself, so the mode selection has to carry the answer
    through two different routes.
    """

    FILES = {RETRODECK_JSON: RD_JSON, RETRODECK_CFG: CFG, SAVES_KEEP: ""}
    PC88_ROM = "/mnt/sd/retrodeck/roms/pc88/Game (Japan).d88"

    def _query(self, files):
        rd = _retrodeck(
            files,
            cores={
                f"{DEPLOY}/quasi88_libretro.so": {
                    "library_name": "QUASI88",
                    "options": {
                        "q88_save_to_disk_image": {
                            "default": "disabled",
                            "values": ["enabled", "disabled"],
                        }
                    },
                }
            },
        )
        return placed(
            rd.savefile_location(content_path=self.PC88_ROM, core_so="quasi88_libretro.so")
        )

    def test_the_default_keeps_a_diff_under_the_save_root(self):
        p = self._query(self.FILES)
        assert p.root_kind == atlas.ROOT_SAVEFILE_DIRECTORY
        # Content sorting keys the root by the content's directory; the card
        # nests nothing below it, and the diff spells the frontend's own .srm
        # for a core where the frontend writes no such file.
        assert p.dir == "/mnt/sd/retrodeck/saves/pc88"
        assert p.file_set.state == "declared"
        assert p.file_set.files == ("Game (Japan).srm",)
        assert [g.role for g in p.file_set.groups] == [atlas.ROLE_DISK_DIFF]
        assert p.granularity is not None
        assert p.granularity.mode == "disabled"

    def test_the_enabled_option_moves_the_writes_into_the_image_itself(self):
        files = {**self.FILES, OPTIONS_CFG: 'q88_save_to_disk_image = "enabled"\n'}
        p = self._query(files)
        assert p.root_kind == atlas.ROOT_CONTENT_DIRECTORY
        assert p.dir == "/mnt/sd/retrodeck/roms/pc88"
        # No separate save file exists in this mode, and the answer says so as
        # a declared emptiness — never by presenting the content file as a
        # save, which would hand a sync client the ROM under a second name.
        # The caveat carries the fact; what to make of it is the caller's call.
        assert p.file_set.state == "declared"
        assert p.file_set.files == ()
        caveat = next(c for c in p.caveats if c.code == atlas.CAVEAT_SAVE_INSIDE_CONTENT)
        assert caveat.data["core"] == "quasi88"
        assert caveat.data["mode"] == "enabled"
        assert "disk image itself" in caveat.message
        assert p.granularity is not None
        assert p.granularity.mode == "enabled"
        assert ("disabled", ("per-game-files",)) in [(a.mode, a.values) for a in p.granularity.alternatives]

    def test_the_open_diff_set_is_scoped_by_a_caveat(self):
        p = self._query(self.FILES)
        caveat = next(c for c in p.caveats if c.code == atlas.CAVEAT_FILENAMES_CONTENT_CONDITIONAL)
        assert "m3u" in caveat.data["files_established_for"]


class TestFlycastResolution:
    def test_default_shared_vmus_in_system_directory(self):
        p = _flycast_query(
            {
                RETRODECK_JSON: RD_JSON,
                RETRODECK_CFG: CFG,
                OPTIONS_CFG: 'reicast_per_content_vmus = "disabled"\n',
                "/mnt/sd/retrodeck/bios/dc/vmu_save_A1.bin": "v",
            }
        )
        assert p.dir == "/mnt/sd/retrodeck/bios/dc"
        assert p.root_kind == atlas.ROOT_SYSTEM_DIRECTORY
        assert p.file_set.state == "observed"
        assert p.file_set.files == ("vmu_save_A1.bin",)
        assert p.granularity is not None
        assert p.granularity.value == GRANULARITY_SHARED_CARD
        assert ("VMU A1", (GRANULARITY_PER_GAME_FILE, GRANULARITY_SHARED_CARD)) in [
            (a.mode, a.values) for a in p.granularity.alternatives
        ]
        assert p.granularity.readings[0].options_file == OPTIONS_CFG

    def test_option_absent_uses_core_default(self):
        p = _flycast_query({RETRODECK_JSON: RD_JSON, RETRODECK_CFG: CFG})
        assert p.root_kind == atlas.ROOT_SYSTEM_DIRECTORY
        assert p.granularity is not None
        assert p.granularity.mode == "disabled"
        assert "core default" in p.granularity.readings[0].provenance

    def test_no_vmus_present_is_declared_from_card(self):
        p = _flycast_query(
            {
                RETRODECK_JSON: RD_JSON,
                RETRODECK_CFG: CFG,
                OPTIONS_CFG: 'reicast_per_content_vmus = "disabled"\n',
            }
        )
        assert p.file_set.state == "declared"
        assert "vmu_save_A1.bin" in p.file_set.files

    def test_one_directory_is_split_into_what_it_is_and_whose_it_is(self):
        # Flycast's shared mode keeps four memory cards and the console's own
        # flash under one directory. They are not the same kind of thing, so
        # they are two groups — and the flat list a client already read is
        # their concatenation, unchanged by the split.
        p = _flycast_query(
            {
                RETRODECK_JSON: RD_JSON,
                RETRODECK_CFG: CFG,
                OPTIONS_CFG: 'reicast_per_content_vmus = "disabled"\n',
            }
        )
        groups = p.file_set.groups
        assert [g.role for g in groups] == [atlas.ROLE_MEMORY_CARD, atlas.ROLE_BATTERY]
        assert groups[1].files == ("dc_nvmem.bin",)
        assert {g.dir for g in groups} == {"/mnt/sd/retrodeck/bios/dc"}
        assert tuple(name for g in groups for name in g.files or ()) == p.file_set.files

    def test_a_client_that_ignores_groups_reads_the_directory_it_always_did(self):
        # The invariant behind the split: `files` is every group under the
        # answer's own directory, so no name left the answer when the card
        # grew a second group.
        p = _flycast_query(
            {
                RETRODECK_JSON: RD_JSON,
                RETRODECK_CFG: CFG,
                OPTIONS_CFG: 'reicast_per_content_vmus = "disabled"\n',
            }
        )
        assert p.file_set.files == (
            "vmu_save_A1.bin",
            "vmu_save_B1.bin",
            "vmu_save_C1.bin",
            "vmu_save_D1.bin",
            "dc_nvmem.bin",
        )

    def test_slot2_vmus_are_observed_when_present(self):
        # The card's observe list is wider than the declared defaults: slot-2
        # VMUs exist only when a port's slot 2 is configured as VMU (M2).
        p = _flycast_query(
            {
                RETRODECK_JSON: RD_JSON,
                RETRODECK_CFG: CFG,
                OPTIONS_CFG: 'reicast_per_content_vmus = "disabled"\n',
                "/mnt/sd/retrodeck/bios/dc/vmu_save_A1.bin": "v",
                "/mnt/sd/retrodeck/bios/dc/vmu_save_A2.bin": "v",
            }
        )
        assert p.file_set.files == ("vmu_save_A1.bin", "vmu_save_A2.bin")
        assert p.file_set.complete is False

    def test_per_game_mode_switches_root_and_granularity(self):
        p = _flycast_query(
            {
                RETRODECK_JSON: RD_JSON,
                RETRODECK_CFG: CFG,
                OPTIONS_CFG: 'reicast_per_content_vmus = "VMU A1"\n',
                SAVES_KEEP: "",
            }
        )
        assert p.dir == "/mnt/sd/retrodeck/saves/dreamcast"
        assert p.root_kind == atlas.ROOT_SAVEFILE_DIRECTORY
        assert p.granularity is not None
        assert p.granularity.value == GRANULARITY_PER_GAME_FILE
        # Only port A1 goes per-content here (oslib.cpp:40-41) — B1..D1 and the
        # console flash keep using the shared card. Since #97 the mode states
        # every part: its own file with the save_id hole, and the parts left
        # behind as groups under the system directory, carried twice — in the
        # decomposition and in one spans-roots caveat per part.
        assert p.file_set.state == "declared"
        assert p.file_set.files == ("<save_id>.A1.bin",)
        assert p.needs == ("save_id",)
        assert [(g.dir, g.files, g.granularity, g.role) for g in p.file_set.groups] == [
            ("/mnt/sd/retrodeck/saves/dreamcast", ("<save_id>.A1.bin",), "per-game-file", "memory-card"),
            (
                "/mnt/sd/retrodeck/bios/dc",
                ("vmu_save_B1.bin", "vmu_save_C1.bin", "vmu_save_D1.bin"),
                "shared-card",
                "memory-card",
            ),
            ("/mnt/sd/retrodeck/bios/dc", ("dc_nvmem.bin",), "shared-card", "battery"),
        ]
        assert not any(c.code == atlas.CAVEAT_FILENAMES_UNVERIFIED for c in p.caveats)
        spans = [c for c in p.caveats if c.code == atlas.CAVEAT_FILE_SET_SPANS_ROOTS]
        assert [dict(c.data) for c in spans] == [
            {
                "core": "flycast",
                "mode": "VMU A1",
                "dir": "/mnt/sd/retrodeck/bios/dc",
                "files": "vmu_save_B1.bin, vmu_save_C1.bin, vmu_save_D1.bin",
            },
            {
                "core": "flycast",
                "mode": "VMU A1",
                "dir": "/mnt/sd/retrodeck/bios/dc",
                "files": "dc_nvmem.bin",
            },
        ]

    def test_all_vmus_declares_one_file_per_connected_port(self):
        p = _flycast_query(
            {
                RETRODECK_JSON: RD_JSON,
                RETRODECK_CFG: CFG,
                OPTIONS_CFG: 'reicast_per_content_vmus = "All VMUs"\n',
                SAVES_KEEP: "",
                "/mnt/sd/retrodeck/saves/dreamcast/.keep": "",
            }
        )
        assert p.dir == "/mnt/sd/retrodeck/saves/dreamcast"
        assert p.granularity is not None
        assert p.granularity.value == GRANULARITY_PER_GAME_FILES
        assert p.file_set.state == "declared"
        assert p.file_set.files == (
            "<save_id>.A1.bin",
            "<save_id>.B1.bin",
            "<save_id>.C1.bin",
            "<save_id>.D1.bin",
        )
        # A template names the shape, never the whole save: the console flash
        # stays in system_directory, and slot-2 VMUs appear when configured.
        assert p.file_set.complete is False
        assert p.needs == ("save_id",)

    def test_all_vmus_hands_over_both_spellings_of_the_names(self):
        # The id branch applies only to console content with a readable header
        # (oslib.cpp:44); everything else is named after the ROM (:62). atlas
        # cannot decide that — it states the id-keyed set and carries the
        # alternative, filled as far as it can fill it, in the caveat's data.
        p = _flycast_query(
            {
                RETRODECK_JSON: RD_JSON,
                RETRODECK_CFG: CFG,
                OPTIONS_CFG: 'reicast_per_content_vmus = "All VMUs"\n',
                SAVES_KEEP: "",
                "/mnt/sd/retrodeck/saves/dreamcast/.keep": "",
            }
        )
        conditional = [c for c in p.caveats if c.code == atlas.CAVEAT_FILENAMES_CONTENT_CONDITIONAL]
        assert len(conditional) == 1
        data = dict(conditional[0].data)
        assert data["core"] == "flycast"
        assert data["mode"] == "All VMUs"
        assert data["files"].split(", ") == list(p.file_set.files)
        assert data["files_without_save_id"].split(", ") == [
            f"{ROM_STEM}.A1.bin",
            f"{ROM_STEM}.B1.bin",
            f"{ROM_STEM}.C1.bin",
            f"{ROM_STEM}.D1.bin",
        ]

    def test_all_vmus_marks_the_port_set_as_the_console_one(self):
        # Not every content difference is a spelling: a Naomi board connects
        # VMUs on B1/C1 only (maple_cfg.cpp:246-253), so two of the four names
        # can never exist there. The scope is data, not prose.
        p = _flycast_query(
            {
                RETRODECK_JSON: RD_JSON,
                RETRODECK_CFG: CFG,
                OPTIONS_CFG: 'reicast_per_content_vmus = "All VMUs"\n',
                SAVES_KEEP: "",
                "/mnt/sd/retrodeck/saves/dreamcast/.keep": "",
            }
        )
        data = dict(
            next(c for c in p.caveats if c.code == atlas.CAVEAT_FILENAMES_CONTENT_CONDITIONAL).data
        )
        assert data["files_established_for"] == "console"
        assert "maple_cfg.cpp:246-253" in data["citation"]

    def test_the_alternative_stays_a_template_without_a_content_path(self):
        rd = _retrodeck(
            {
                RETRODECK_JSON: RD_JSON,
                RETRODECK_CFG: CFG,
                OPTIONS_CFG: 'reicast_per_content_vmus = "All VMUs"\n',
                SAVES_KEEP: "",
            },
            cores={f"{DEPLOY}/flycast_libretro.so": {"library_name": "Flycast"}},
        )
        p = placed(rd.savefile_location(core_so="flycast_libretro.so"))
        conditional = [c for c in p.caveats if c.code == atlas.CAVEAT_FILENAMES_CONTENT_CONDITIONAL]
        assert conditional
        assert conditional[0].data["files_without_save_id"].startswith("<rom_stem>.A1.bin")

    def test_the_card_provenance_rides_along_on_the_standard_route(self):
        # A mode switch MOVES the save — the shared cards stay behind stale —
        # and no field of the placement can say that. The card's provenance is
        # where it is written, so it reaches this route too, not only the
        # system_directory one.
        p = _flycast_query(
            {
                RETRODECK_JSON: RD_JSON,
                RETRODECK_CFG: CFG,
                OPTIONS_CFG: 'reicast_per_content_vmus = "All VMUs"\n',
                SAVES_KEEP: "",
            }
        )
        assert any(s.startswith("rule card 'flycast' governs this placement") for s in p.sources)

    def test_the_save_id_hole_is_never_filled_from_the_content_name(self):
        # The id lives in the disc header, which atlas does not read — a file
        # lying there under the ROM's name is not this save (REVIEW 13b).
        p = _flycast_query(
            {
                RETRODECK_JSON: RD_JSON,
                RETRODECK_CFG: CFG,
                OPTIONS_CFG: 'reicast_per_content_vmus = "All VMUs"\n',
                SAVES_KEEP: "",
                "/mnt/sd/retrodeck/saves/dreamcast/MK-5105950.A1.bin": "v",
            }
        )
        assert p.file_set.state == "declared"
        assert "MK-5105950.A1.bin" not in p.file_set.files
        assert p.needs == ("save_id",)

    def test_game_opt_file_wins_over_global(self):
        # runloop.c validate_per_core_options: the game .opt is THE source.
        p = _flycast_query(
            {
                RETRODECK_JSON: RD_JSON,
                RETRODECK_CFG: CFG,
                FLYCAST_GAME_OPT: 'reicast_per_content_vmus = "VMU A1"\n',
                OPTIONS_CFG: 'reicast_per_content_vmus = "disabled"\n',
            }
        )
        assert p.granularity is not None
        assert p.granularity.mode == "VMU A1"
        assert "Dreamcast Game (Europe).opt" in p.granularity.readings[0].provenance

    def test_unknown_option_value_applies_core_default_mode(self):
        # RetroArch's option manager keeps the core default on an invalid
        # persisted value (REVIEW M1) — for Flycast that is shared VMUs, not
        # the standard rule.
        p = _flycast_query(
            {
                RETRODECK_JSON: RD_JSON,
                RETRODECK_CFG: CFG,
                OPTIONS_CFG: 'reicast_per_content_vmus = "something new"\n',
            }
        )
        assert p.root_kind == atlas.ROOT_SYSTEM_DIRECTORY
        assert p.granularity is not None
        assert p.granularity.value == GRANULARITY_SHARED_CARD
        assert any(c.code == atlas.CAVEAT_UNKNOWN_OPTION_VALUE and c.data["value"] == "something new" for c in p.caveats)

    def test_override_can_switch_the_game_opt_layer_off(self):
        # game_specific_options is read from the MERGED config: the core's own
        # retro_set_environment (runloop.c:5037) drives runloop_init_core_options
        # and its settings->bools.game_specific_options read (runloop.c:1529),
        # one step after config_load_override merged the overrides (:5003). So
        # the override below really does keep RetroArch out of the game .opt.
        p = _flycast_query(
            {
                RETRODECK_JSON: RD_JSON,
                RETRODECK_CFG: CFG,
                FLYCAST_CORE_OVERRIDE: 'game_specific_options = "false"\n',
                FLYCAST_GAME_OPT: 'reicast_per_content_vmus = "VMU A1"\n',
                OPTIONS_CFG: 'reicast_per_content_vmus = "disabled"\n',
            }
        )
        assert p.granularity is not None
        assert p.granularity.mode == "disabled"
        assert p.granularity.readings[0].options_file == OPTIONS_CFG

    def test_a_dropped_game_specific_options_line_leaves_the_game_opt_on(self):
        # The line sets nothing, so the default (true) stands and the game .opt
        # governs after all — stated, because nothing else in the answer shows it.
        p = _flycast_query(
            {
                RETRODECK_JSON: RD_JSON,
                RETRODECK_CFG: CFG,
                FLYCAST_CORE_OVERRIDE: 'game_specific_options="false"\n',
                FLYCAST_GAME_OPT: 'reicast_per_content_vmus = "VMU A1"\n',
                OPTIONS_CFG: 'reicast_per_content_vmus = "disabled"\n',
                SAVES_KEEP: "",
            }
        )
        assert p.granularity is not None
        assert p.granularity.mode == "VMU A1"
        assert [c.data for c in p.caveats if c.code == atlas.CAVEAT_CFG_LINE_DROPPED] == [
            {"key": "game_specific_options", "line": 'game_specific_options="false"'}
        ]

    def test_a_dropped_core_options_path_line_leaves_the_default_file_governing(self):
        # The line sets nothing, so the options file stays the default one
        # beside retroarch.cfg and the file the line names is never opened.
        p = _flycast_query(
            {
                RETRODECK_JSON: RD_JSON,
                RETRODECK_CFG: CFG + 'core_options_path="/mnt/sd/retrodeck/elsewhere.cfg"\n',
                "/mnt/sd/retrodeck/elsewhere.cfg": 'reicast_per_content_vmus = "VMU A1"\n',
                OPTIONS_CFG: 'reicast_per_content_vmus = "disabled"\n',
                SAVES_KEEP: "",
            }
        )
        assert p.granularity is not None
        assert p.granularity.mode == "disabled"
        assert p.granularity.readings[0].options_file == OPTIONS_CFG
        assert [c.data for c in p.caveats if c.code == atlas.CAVEAT_CFG_LINE_DROPPED] == [
            {"key": "core_options_path", "line": 'core_options_path="/mnt/sd/retrodeck/elsewhere.cfg"'}
        ]

    def test_a_dropped_global_core_options_line_leaves_the_per_core_opt_governing(self):
        # Dropped, so the compile-time default (false) stands and the per-core
        # .opt IS consulted — the opposite of what the line asks for.
        p = _flycast_query(
            {
                RETRODECK_JSON: RD_JSON,
                RETRODECK_CFG: CFG_WITHOUT_GLOBAL_OPTS + 'global_core_options="true"\n',
                FLYCAST_CORE_OPT: 'reicast_per_content_vmus = "VMU A1"\n',
                OPTIONS_CFG: 'reicast_per_content_vmus = "disabled"\n',
                SAVES_KEEP: "",
            }
        )
        assert p.granularity is not None
        assert p.granularity.mode == "VMU A1"
        assert p.granularity.readings[0].options_file == FLYCAST_CORE_OPT
        assert [c.data for c in p.caveats if c.code == atlas.CAVEAT_CFG_LINE_DROPPED] == [
            {"key": "global_core_options", "line": 'global_core_options="true"'}
        ]

    def test_ordinary_core_has_no_granularity(self):
        rd = _retrodeck(
            {
                RETRODECK_JSON: RD_JSON,
                RETRODECK_CFG: CFG,
                "/mnt/sd/retrodeck/roms/gba/Game.zip": "",
            },
            cores={f"{DEPLOY}/mgba_libretro.so": {"library_name": "mGBA"}},
        )
        p = placed(rd.savefile_location(content_path="/mnt/sd/retrodeck/roms/gba/Game.zip", core_so="mgba_libretro.so"))
        assert p.granularity is None


class TestLRPS2Card:
    def test_lookup_by_so_and_library_name(self):
        by_so = lookup_card(so_basename="pcsx2_libretro.so", library_name=None)
        by_name = lookup_card(so_basename=None, library_name="LRPS2")
        assert by_so is not None
        assert by_so.key == "pcsx2"
        assert by_name is not None
        assert by_name.key == "pcsx2"

    def test_default_shared_memcards_in_system_directory(self):
        # pcsx2_shared_memory_cards defaults to "enabled" (libretro_core_options.h:169)
        rd = _retrodeck(
            {
                RETRODECK_JSON: RD_JSON,
                RETRODECK_CFG: CFG,
                "/mnt/sd/retrodeck/roms/ps2/Game.iso": "",
            },
            cores={f"{DEPLOY}/pcsx2_libretro.so": {"library_name": "LRPS2"}},
        )
        p = placed(rd.savefile_location(content_path="/mnt/sd/retrodeck/roms/ps2/Game.iso", core_so="pcsx2_libretro.so"))
        assert p.dir == "/mnt/sd/retrodeck/bios/pcsx2/memcards"
        assert p.root_kind == atlas.ROOT_SYSTEM_DIRECTORY
        assert p.file_set.state == "declared"
        assert p.file_set.files == ("Mcd001.ps2", "Mcd002.ps2")
        g = p.granularity
        assert g is not None
        assert g.value == GRANULARITY_SHARED_CARD
        assert g.readings[0].key == "pcsx2_shared_memory_cards"
        assert g.mode == "enabled"
        assert ("disabled", (GRANULARITY_PER_GAME_FILE,)) in [(a.mode, a.values) for a in g.alternatives]

    def test_per_content_mode_names_card_after_rom_stem(self):
        # main.cpp:2154-2166 + Pcsx2Config.cpp:995-997 — slot 1 = <rom_stem>.ps2
        # in the RetroArch save directory (standard resolution applies).
        rd = _retrodeck(
            {
                RETRODECK_JSON: RD_JSON,
                RETRODECK_CFG: CFG,
                OPTIONS_CFG: 'pcsx2_shared_memory_cards = "disabled"\n',
                SAVES_KEEP: "",
                "/mnt/sd/retrodeck/roms/ps2/Gran Turismo 4 (USA).iso": "",
            },
            cores={f"{DEPLOY}/pcsx2_libretro.so": {"library_name": "LRPS2"}},
        )
        p = placed(
            rd.savefile_location(
                content_path="/mnt/sd/retrodeck/roms/ps2/Gran Turismo 4 (USA).iso", core_so="pcsx2_libretro.so"
            )
        )
        assert p.dir == "/mnt/sd/retrodeck/saves/ps2"
        assert p.root_kind == atlas.ROOT_SAVEFILE_DIRECTORY
        assert p.file_set.state == "declared"
        assert p.file_set.files == ("Gran Turismo 4 (USA).ps2",)
        assert p.granularity is not None
        assert p.granularity.value == GRANULARITY_PER_GAME_FILE

    def test_existing_memcard_is_observed(self):
        rd = _retrodeck(
            {
                RETRODECK_JSON: RD_JSON,
                RETRODECK_CFG: CFG,
                "/mnt/sd/retrodeck/roms/ps2/Game.iso": "",
                "/mnt/sd/retrodeck/bios/pcsx2/memcards/Mcd001.ps2": "m",
            },
            cores={f"{DEPLOY}/pcsx2_libretro.so": {"library_name": "LRPS2"}},
        )
        p = placed(rd.savefile_location(content_path="/mnt/sd/retrodeck/roms/ps2/Game.iso", core_so="pcsx2_libretro.so"))
        assert p.file_set.state == "observed"
        assert p.file_set.files == ("Mcd001.ps2",)


class TestFeatureDetection:
    """Card applicability decided by what the core observably registers.

    Key registered → card confirmed, version drift demoted to provenance.
    Key gone → the card describes another generation and steps aside.
    Options not captured → the generation stays unknown and the version
    comparison keeps working, but nothing states the governing value either, so
    no mode can be selected.
    """

    FLYCAST_OPTIONS = FLYCAST_REGISTERED

    def _flycast(self, core_spec, files=None, rd_json=RD_JSON):
        base = {
            RETRODECK_JSON: rd_json,
            RETRODECK_CFG: CFG,
            ROM: "",
            SAVES_KEEP: "",
        }
        base.update(files or {})
        rd = _retrodeck(base, cores={f"{DEPLOY}/flycast_libretro.so": core_spec})
        return placed(rd.savefile_location(content_path=ROM, core_so="flycast_libretro.so"))

    def test_registered_key_confirms_card_despite_version_drift(self):
        # Version drifted (fffffff ≠ pinned 1dac369), but the governing option
        # is observably registered — no false alarm, decision on evidence.
        p = self._flycast(
            {
                "library_name": "Flycast",
                "library_version": "fffffff",
                "options": self.FLYCAST_OPTIONS,
            },
            rd_json='{"version": "0.10.9b", "paths": {"rd_home_path": "/mnt/sd/retrodeck", "saves_path": "/mnt/sd/retrodeck/saves"}}',
        )
        assert p.root_kind == atlas.ROOT_SYSTEM_DIRECTORY  # card applied
        assert not any(c.code == atlas.CAVEAT_UNVERIFIED_VERSION for c in p.caveats)
        assert any("feature-detected" in s for s in p.sources)
        assert any("version records differ" in s for s in p.sources)

    def test_missing_key_retires_the_card(self):
        # The LRPS2 lesson as a Flycast fixture: the core registers a
        # different option vocabulary — the card must not be applied.
        p = self._flycast(
            {
                "library_name": "Flycast",
                "options": {"flycast_vmu_layout": {"default": "new", "values": ["new", "old"]}},
            }
        )
        assert p.root_kind == atlas.ROOT_SAVEFILE_DIRECTORY  # standard frame
        assert p.granularity is None
        mismatch = [c for c in p.caveats if c.code == atlas.CAVEAT_CORE_GENERATION_MISMATCH]
        assert mismatch
        assert mismatch[0].data["core"] == "flycast"

    def test_uncaptured_options_still_reach_the_version_comparison(self):
        # The core answered, so the version comparison runs and reports what it
        # cannot check — that half is unchanged. What the probe did not capture
        # is the option registration, and with it the core's default.
        p = self._flycast({"library_name": "Flycast"})
        stale = [c for c in p.caveats if c.code == atlas.CAVEAT_UNVERIFIED_VERSION]
        assert stale
        assert stale[0].data["verification"] == "runtime-version-unknown"

    def test_uncaptured_options_leave_the_governing_value_unestablished(self):
        # Nothing states the value: no options file here, and the core declared
        # no default because the probe read no registration at all. The card
        # fits, and still cannot say which of its modes is in force — so it
        # steps aside rather than picking one, and says which half is missing.
        p = self._flycast({"library_name": "Flycast"})
        assert p.root_kind == atlas.ROOT_SAVEFILE_DIRECTORY  # standard frame
        assert p.granularity is None
        stated = [c for c in p.caveats if c.code == atlas.CAVEAT_CORE_OPTION_VALUE_UNESTABLISHED]
        assert [dict(c.data) for c in stated] == [
            {"core": "flycast", "option_key": "reicast_per_content_vmus"}
        ]

    def test_an_options_file_settles_it_even_with_the_options_uncaptured(self):
        # The probe read nothing, but the machine states the value outright —
        # which is all the card ever needed. Nothing is unestablished here.
        p = self._flycast(
            {"library_name": "Flycast"},
            files={OPTIONS_CFG: 'reicast_per_content_vmus = "All VMUs"\n'},
        )
        assert p.granularity is not None
        assert p.granularity.mode == "All VMUs"
        assert not any(
            c.code == atlas.CAVEAT_CORE_OPTION_VALUE_UNESTABLISHED for c in p.caveats
        )

    def test_the_live_default_is_what_governs_when_no_file_states_a_value(self):
        # The registered default says per-game VMUs — a generation that
        # flipped its default. No options file present: the live default
        # governs, and it is the only default there is.
        options = {
            "reicast_per_content_vmus": {
                "default": "VMU A1",
                "values": ["disabled", "VMU A1", "All VMUs"],
            }
        }
        p = self._flycast({"library_name": "Flycast", "options": options})
        assert p.root_kind == atlas.ROOT_SAVEFILE_DIRECTORY
        assert p.granularity is not None
        assert p.granularity.mode == "VMU A1"

    def test_stored_value_validated_against_live_definition(self):
        # Persisted junk value: RetroArch keeps the core default — confirmed
        # against the LIVE value set, applied via the card's default mode.
        p = self._flycast(
            {"library_name": "Flycast", "options": self.FLYCAST_OPTIONS},
            files={OPTIONS_CFG: 'reicast_per_content_vmus = "sideways"\n'},
        )
        assert p.root_kind == atlas.ROOT_SYSTEM_DIRECTORY
        assert any(c.code == atlas.CAVEAT_UNKNOWN_OPTION_VALUE for c in p.caveats)

    def test_live_value_unknown_to_card_is_generation_drift(self):
        # The live core registers a value the card has never heard of, and
        # the user selected it — the card lags the generation; never guess.
        options = {
            "reicast_per_content_vmus": {
                "default": "disabled",
                "values": ["disabled", "VMU A1", "All VMUs", "VMU A1+A2"],
            }
        }
        p = self._flycast(
            {"library_name": "Flycast", "options": options},
            files={OPTIONS_CFG: 'reicast_per_content_vmus = "VMU A1+A2"\n'},
        )
        assert p.granularity is None
        assert any(c.code == atlas.CAVEAT_CORE_GENERATION_MISMATCH for c in p.caveats)


class TestACoreThatCannotBeReadGetsNoCard:
    """Issue #81: no read, no generation — and therefore no card.

    The neighbouring case (core read, governing option absent) retires the card
    because the evidence contradicts it. Here there is no evidence at all, and
    the ``.so`` file name is not any: applying the card would state a recorded
    deviation as though its generation had been confirmed.
    """

    def _unreadable(self, so_name: str):
        rd = _retrodeck(
            {RETRODECK_JSON: RD_JSON, RETRODECK_CFG: CFG, ROM: "", SAVES_KEEP: ""},
            cores={f"{DEPLOY}/{so_name}": None},
        )
        return placed(rd.savefile_location(content_path=ROM, core_so=so_name))

    def test_the_card_is_not_applied(self):
        p = self._unreadable("flycast_libretro.so")
        assert p.root_kind == atlas.ROOT_SAVEFILE_DIRECTORY  # standard frame, not the card's root
        assert p.granularity is None

    def test_the_answer_says_which_generation_could_not_be_established(self):
        p = self._unreadable("flycast_libretro.so")
        stated = [c for c in p.caveats if c.code == atlas.CAVEAT_CORE_GENERATION_UNESTABLISHED]
        assert stated
        assert stated[0].data == {"core": "flycast"}

    def test_nothing_read_is_not_a_generation_mismatch(self):
        # The two codes answer different questions and can never ride together:
        # a mismatch is a core that ANSWERED, for a generation the record does
        # not describe.
        p = self._unreadable("flycast_libretro.so")
        assert not any(c.code == atlas.CAVEAT_CORE_GENERATION_MISMATCH for c in p.caveats)

    def test_a_generation_mismatch_is_not_nothing_read(self):
        # The same exclusivity from the other side, on the core that answers
        # with another option vocabulary.
        rd = _retrodeck(
            {RETRODECK_JSON: RD_JSON, RETRODECK_CFG: CFG, ROM: "", SAVES_KEEP: ""},
            cores={
                f"{DEPLOY}/flycast_libretro.so": {
                    "library_name": "Flycast",
                    "options": {"flycast_vmu_layout": {"default": "new", "values": ["new", "old"]}},
                }
            },
        )
        p = placed(rd.savefile_location(content_path=ROM, core_so="flycast_libretro.so"))
        codes = [c.code for c in p.caveats]
        assert atlas.CAVEAT_CORE_GENERATION_MISMATCH in codes
        assert atlas.CAVEAT_CORE_GENERATION_UNESTABLISHED not in codes

    def test_a_core_with_no_card_loses_nothing_and_says_nothing_extra(self):
        # Nothing was retired here, and core-unqueryable already states that the
        # read failed — a second caveat would announce the loss of knowledge
        # that never existed.
        p = self._unreadable("applewin_libretro.so")
        codes = [c.code for c in p.caveats]
        assert atlas.CAVEAT_CORE_UNQUERYABLE in codes
        assert atlas.CAVEAT_CORE_GENERATION_UNESTABLISHED not in codes

    def test_a_core_that_reads_is_untouched_by_this(self):
        rd = _retrodeck(
            {RETRODECK_JSON: RD_JSON, RETRODECK_CFG: CFG, ROM: "", SAVES_KEEP: ""},
            cores={
                f"{DEPLOY}/flycast_libretro.so": {
                    "library_name": "Flycast",
                    "options": {
                        "reicast_per_content_vmus": {
                            "default": "disabled",
                            "values": ["disabled", "VMU A1", "All VMUs"],
                        }
                    },
                }
            },
        )
        p = placed(rd.savefile_location(content_path=ROM, core_so="flycast_libretro.so"))
        assert p.root_kind == atlas.ROOT_SYSTEM_DIRECTORY  # card applied, as before
        assert not any(c.code == atlas.CAVEAT_CORE_GENERATION_UNESTABLISHED for c in p.caveats)


class TestAGoverningValueNothingEstablishes:
    """The card fits the core, and nothing says which of its modes is in force.

    One level below the two codes above. There the *generation* is in doubt —
    the core could not be read at all, or answered for a vocabulary the record
    does not describe. Here the generation is fine and the *setting* is missing:
    no options file states the value, and the core declared no default because
    the probe captured no registration. A card records a default only where the
    core states none, so for these two cards there is nothing to fall back on,
    and choosing a mode would be a save location asserted on the strength of
    nothing.
    """

    BASE = {RETRODECK_JSON: RD_JSON, RETRODECK_CFG: CFG, ROM: "", SAVES_KEEP: ""}

    def _probe_blind(self, files=None):
        base = dict(self.BASE)
        base.update(files or {})
        rd = _retrodeck(base, cores={f"{DEPLOY}/flycast_libretro.so": {"library_name": "Flycast"}})
        return placed(rd.savefile_location(content_path=ROM, core_so="flycast_libretro.so"))

    def test_the_standard_frame_answers_and_the_caveat_names_the_option(self):
        p = self._probe_blind()
        assert p.root_kind == atlas.ROOT_SAVEFILE_DIRECTORY
        assert p.granularity is None
        stated = [c for c in p.caveats if c.code == atlas.CAVEAT_CORE_OPTION_VALUE_UNESTABLISHED]
        assert [dict(c.data) for c in stated] == [
            {"core": "flycast", "option_key": "reicast_per_content_vmus"}
        ]

    def test_no_value_nobody_read_reaches_the_answer(self):
        # The failure this replaces: the empty string travelling into the answer
        # as though the machine had stated it, inside a caveat about a "value"
        # the card cannot interpret.
        p = self._probe_blind()
        assert not any("value" in c.data for c in p.caveats)

    def test_it_never_rides_with_a_generation_mismatch(self):
        p = self._probe_blind()
        assert not any(c.code == atlas.CAVEAT_CORE_GENERATION_MISMATCH for c in p.caveats)

    def test_it_never_rides_with_an_unestablished_generation(self):
        p = self._probe_blind()
        assert not any(c.code == atlas.CAVEAT_CORE_GENERATION_UNESTABLISHED for c in p.caveats)

    def test_a_generation_mismatch_is_not_this(self):
        # The other side of the first exclusion: a core that answers with
        # another option vocabulary. Its generation is what is wrong, and the
        # governing value was never the question.
        rd = _retrodeck(
            self.BASE,
            cores={
                f"{DEPLOY}/flycast_libretro.so": {
                    "library_name": "Flycast",
                    "options": {"flycast_vmu_layout": {"default": "new", "values": ["new", "old"]}},
                }
            },
        )
        p = placed(rd.savefile_location(content_path=ROM, core_so="flycast_libretro.so"))
        codes = [c.code for c in p.caveats]
        assert atlas.CAVEAT_CORE_GENERATION_MISMATCH in codes
        assert atlas.CAVEAT_CORE_OPTION_VALUE_UNESTABLISHED not in codes

    def test_a_core_that_could_not_be_read_is_not_this(self):
        # The other side of the second exclusion. Nothing was read, so no card
        # was in play and no option of it was ever looked for.
        rd = _retrodeck(self.BASE, cores={f"{DEPLOY}/flycast_libretro.so": None})
        p = placed(rd.savefile_location(content_path=ROM, core_so="flycast_libretro.so"))
        codes = [c.code for c in p.caveats]
        assert atlas.CAVEAT_CORE_GENERATION_UNESTABLISHED in codes
        assert atlas.CAVEAT_CORE_OPTION_VALUE_UNESTABLISHED not in codes

    def test_a_recorded_default_is_what_keeps_lrps2_answering(self):
        # The same probe blindness on the card that records a default: LRPS2
        # registers its options too late for the probe, which is exactly why
        # its card keeps one — so this machine is answered, not degraded.
        rd = _retrodeck(
            {**self.BASE, "/mnt/sd/retrodeck/roms/ps2/Game.iso": ""},
            cores={f"{DEPLOY}/pcsx2_libretro.so": {"library_name": "LRPS2"}},
        )
        p = placed(
            rd.savefile_location(
                content_path="/mnt/sd/retrodeck/roms/ps2/Game.iso", core_so="pcsx2_libretro.so"
            )
        )
        assert p.dir == "/mnt/sd/retrodeck/bios/pcsx2/memcards"
        assert p.granularity is not None
        assert p.granularity.mode == "enabled"
        assert not any(
            c.code == atlas.CAVEAT_CORE_OPTION_VALUE_UNESTABLISHED for c in p.caveats
        )

    def test_a_core_with_no_card_never_reaches_this(self):
        # No card, no governing option, nothing to leave unestablished.
        rd = _retrodeck(
            {**self.BASE, "/mnt/sd/retrodeck/roms/gba/Game.zip": ""},
            cores={f"{DEPLOY}/mgba_libretro.so": {"library_name": "mGBA"}},
        )
        p = placed(
            rd.savefile_location(
                content_path="/mnt/sd/retrodeck/roms/gba/Game.zip", core_so="mgba_libretro.so"
            )
        )
        assert not any(
            c.code == atlas.CAVEAT_CORE_OPTION_VALUE_UNESTABLISHED for c in p.caveats
        )


class TestACoreThatIsNotInstalledIsRefused:
    """Three ways to read nothing about a core, and only one of them is absence.

    Absence is a claim, so it takes a read that could have found the core: the
    directory RetroArch loads cores from, reached and read, without this ``.so``
    in it. Then there is no location to answer with, and the question is refused
    rather than answered with the directory a core that cannot run would use. A
    core that IS there and will not load still has a placement; a cores
    directory atlas could not read establishes nothing and keeps its answer too.
    """

    BASE = {RETRODECK_JSON: RD_JSON, RETRODECK_CFG: CFG, ROM: "", SAVES_KEEP: ""}

    def _ask(self, so_name, *, cores=None, files=None, **kwargs):
        base = dict(self.BASE)
        base.update(files or {})
        rd = _retrodeck(base, cores=cores, **kwargs)
        return rd.savefile_location(content_path=ROM, core_so=so_name)

    def test_a_core_that_is_not_in_the_cores_directory_is_refused(self):
        # The directory is real (another core sits in it) and this one is not
        # there: absence established, so no directory is offered for it.
        outcome = self._ask(
            "flycast_libretro.so", cores={f"{DEPLOY}/mgba_libretro.so": {"library_name": "mGBA"}}
        )
        assert isinstance(outcome, atlas.Unresolved)
        assert outcome.code == atlas.UNRESOLVED_CORE_NOT_INSTALLED
        assert outcome.data == {"core_so": "flycast_libretro.so"}

    def test_the_refusal_is_the_firmware_routes_word_for_the_same_fact(self):
        assert atlas.UNRESOLVED_CORE_NOT_INSTALLED == atlas.CAVEAT_CORE_NOT_INSTALLED

    def test_a_core_with_no_card_is_refused_the_same_way(self):
        # The refusal is about the core, not about the recorded knowledge: an
        # ordinary core nobody wrote anything down about is just as absent.
        outcome = self._ask(
            "applewin_libretro.so", cores={f"{DEPLOY}/mgba_libretro.so": {"library_name": "mGBA"}}
        )
        assert isinstance(outcome, atlas.Unresolved)
        assert outcome.code == atlas.UNRESOLVED_CORE_NOT_INSTALLED

    def test_nothing_was_retired_so_nothing_says_a_generation_is_unestablished(self):
        # The refusal replaces the answer; it does not also report the loss of a
        # card, because no card was in play once there is no core. The data
        # naming only the core is what proves nothing else was attached — a
        # refusal has no caveat list to carry a second statement in.
        outcome = self._ask(
            "flycast_libretro.so", cores={f"{DEPLOY}/mgba_libretro.so": {"library_name": "mGBA"}}
        )
        assert isinstance(outcome, atlas.Unresolved)
        assert dict(outcome.data) == {"core_so": "flycast_libretro.so"}
        assert not hasattr(outcome, "caveats")

    def test_a_core_that_is_there_and_will_not_load_still_gets_a_placement(self):
        # Present but unloadable is not absent — the directory it would use is
        # known, and only its generation is not.
        outcome = self._ask("flycast_libretro.so", cores={f"{DEPLOY}/flycast_libretro.so": None})
        p = placed(outcome)
        assert p.root_kind == atlas.ROOT_SAVEFILE_DIRECTORY
        assert any(c.code == atlas.CAVEAT_CORE_GENERATION_UNESTABLISHED for c in p.caveats)

    def test_a_cores_directory_atlas_cannot_read_establishes_no_absence(self):
        # The directory is there — another core is deployed in it — and the stat
        # on it fails, so "this one is not in it" was never read. Claiming the
        # core is not installed would be a claim about a place nobody looked
        # into, so the answer stays the placement it was before.
        outcome = self._ask(
            "flycast_libretro.so",
            cores={f"{DEPLOY}/mgba_libretro.so": {"library_name": "mGBA"}},
            inaccessible=[DEPLOY],
        )
        p = placed(outcome)
        assert any(c.code == atlas.CAVEAT_CORE_UNQUERYABLE for c in p.caveats)

    def test_a_core_directory_that_resolves_to_nothing_establishes_no_absence(self):
        # The other half of "cannot look": the configured libretro_directory
        # resolves nowhere on the host at all, so there is no directory to have
        # read and no absence to establish.
        outcome = self._ask("flycast_libretro.so")
        p = placed(outcome)
        assert any(c.code == atlas.CAVEAT_CORE_UNQUERYABLE for c in p.caveats)

    def test_a_healthy_core_is_untouched(self):
        outcome = self._ask(
            "flycast_libretro.so", cores={f"{DEPLOY}/flycast_libretro.so": FLYCAST_CORE}
        )
        p = placed(outcome)
        assert p.root_kind == atlas.ROOT_SYSTEM_DIRECTORY  # the card applies, as before

    def test_the_savestate_route_refuses_on_the_same_evidence(self):
        # Same lookup, same refusal: sorting by core and the savestate-support
        # declaration are both answers about a core that is not here.
        rd = _retrodeck(self.BASE, cores={f"{DEPLOY}/mgba_libretro.so": {"library_name": "mGBA"}})
        outcome = rd.savestate_location(content_path=ROM, core_so="flycast_libretro.so")
        assert isinstance(outcome, atlas.Unresolved)
        assert outcome.code == atlas.UNRESOLVED_CORE_NOT_INSTALLED

    def test_naming_no_core_is_not_an_absent_one(self):
        rd = _retrodeck(self.BASE, cores={f"{DEPLOY}/mgba_libretro.so": {"library_name": "mGBA"}})
        p = placed(rd.savefile_location(content_path=ROM))
        assert any(c.code == atlas.CAVEAT_NO_CORE for c in p.caveats)


class TestOperaCard:
    """3DO NVRAM: the core nests opera/per_game|shared under the save directory."""

    OPERA_CFG = (
        'savefile_directory = "/mnt/sd/retrodeck/saves"\n'
        'sort_savefiles_by_content_enable = "false"\nsort_savefiles_enable = "false"\n'
        'global_core_options = "true"\n'
        'libretro_directory = "/app/cores"\n'
    )
    ROM_3DO = "/mnt/sd/retrodeck/roms/3do/Game.chd"

    def _query(self, files, cfg=None):
        base = {
            RETRODECK_JSON: RD_JSON,
            RETRODECK_CFG: cfg or self.OPERA_CFG,
            self.ROM_3DO: "",
            SAVES_KEEP: "",
        }
        base.update(files)
        rd = _retrodeck(base, cores={f"{DEPLOY}/opera_libretro.so": OPERA_CORE})
        return placed(rd.savefile_location(content_path=self.ROM_3DO, core_so="opera_libretro.so"))

    def test_default_per_game_nests_subdir_under_save_dir(self):
        p = self._query({})
        assert p.dir == "/mnt/sd/retrodeck/saves/opera/per_game"
        assert p.root_kind == atlas.ROOT_SAVEFILE_DIRECTORY
        assert p.granularity is not None
        assert p.granularity.value == GRANULARITY_PER_GAME_FILE
        assert ("shared", (GRANULARITY_SHARED_CARD,)) in [(a.mode, a.values) for a in p.granularity.alternatives]

    def test_shared_mode_switches_subdir(self):
        p = self._query({OPTIONS_CFG: 'opera_nvram_storage = "shared"\n'})
        assert p.dir == "/mnt/sd/retrodeck/saves/opera/shared"
        assert p.granularity is not None
        assert p.granularity.value == GRANULARITY_SHARED_CARD

    def test_subdir_follows_the_sorted_directory(self):
        # GET_SAVE_DIRECTORY hands the core the redirected (sorted) dir
        # (runloop.c:2001, 8977) — the core's subtree nests under it.
        cfg = self.OPERA_CFG.replace(
            'sort_savefiles_by_content_enable = "false"', 'sort_savefiles_by_content_enable = "true"'
        )
        p = self._query({"/mnt/sd/retrodeck/saves/3do/.keep": ""}, cfg=cfg)
        assert p.dir == "/mnt/sd/retrodeck/saves/3do/opera/per_game"

    def test_version_parameterized_files_are_observed_not_declared(self):
        p = self._query({"/mnt/sd/retrodeck/saves/opera/per_game/Game.0.srm": "nv"})
        assert p.file_set.state == "observed"
        assert p.file_set.files == ("Game.0.srm",)


class TestAuditVerdictCaveats:
    """A verdict a caller cannot see is a verdict that did not happen.

    ``granularity`` is ``None`` for every core without a rule card, so the
    verdict has to arrive as a caveat or not at all.
    """

    def _query(self, *, core_so, library_name, system, rom, extra_files=None):
        rd = _retrodeck(
            {
                RETRODECK_JSON: RD_JSON,
                RETRODECK_CFG: CFG,
                f"/mnt/sd/retrodeck/roms/{system}/{rom}": "",
                f"/mnt/sd/retrodeck/saves/{system}/.keep": "",
                **(extra_files or {}),
            },
            cores={f"{DEPLOY}/{core_so}": {"library_name": library_name}},
        )
        # The system is named, because these cases are about a verdict rather
        # than about answering without one — unnamed, a record answers across
        # its systems and says so, which is a different question.
        return placed(
            rd.savefile_location(
                content_path=f"/mnt/sd/retrodeck/roms/{system}/{rom}",
                core_so=core_so,
                system=system,
            )
        )

    def test_multi_option_verdict_names_the_options_that_decide_granularity(self):
        p = self._query(
            core_so="neocd_libretro.so",
            library_name="NeoCD",
            system="neogeocd",
            rom="Metal Slug (Japan).chd",
        )
        # The record still answers — NeoCD really fills save RAM — so the
        # standard-rule granularity stands; what the verdict adds is the
        # caveat naming the option that can move the whole set, which no
        # record models. No card spoke, so no mode is named.
        assert p.granularity is not None
        assert p.granularity.mode is None
        stated = [c for c in p.caveats if c.code == atlas.CAVEAT_CORE_MULTI_OPTION]
        assert stated
        assert stated[0].data["core"] == "neocd"
        assert stated[0].data["options"] == "neocd_per_content_saves"

    # What these two cases are about: an audit verdict that adds nothing of its
    # own. `core-unaudited`, `core-suspect` and `core-multi-option` are the
    # three a verdict can contribute, so their absence is the assertion — not
    # an empty caveat list, which would also fail the day the *record* starts
    # speaking, as it now does once the system is named.
    VERDICT_CAVEATS = {"core-unaudited", "core-suspect", "core-multi-option"}

    def test_standard_verdict_stays_silent(self):
        # The pair from issue #23: same empty granularity, different meaning.
        p = self._query(
            core_so="mgba_libretro.so",
            library_name="mGBA",
            system="gba",
            rom="Golden Sun (USA).zip",
        )
        assert {c.code for c in p.caveats} & self.VERDICT_CAVEATS == set()

    def test_a_rule_card_with_uncaptured_options_and_no_file_steps_aside(self):
        # The _query fixtures register no options at all, which for a rule
        # card is the probe-limitation state: nothing states either switch's
        # value, so the rule refuses per missing option and the card steps
        # aside — the same honesty the single-option route answers with.
        p = self._query(
            core_so="mednafen_saturn_libretro.so",
            library_name="Beetle Saturn",
            system="saturn",
            rom="Sega Rally Championship (USA).chd",
        )
        assert p.granularity is None
        stated = [
            c for c in p.caveats if c.code == atlas.CAVEAT_CORE_OPTION_VALUE_UNESTABLISHED
        ]
        assert [c.data["option_key"] for c in stated] == [
            "beetle_saturn_shared_int",
            "beetle_saturn_shared_ext",
        ]


SATURN_REGISTERED = {
    "beetle_saturn_shared_int": {"default": "disabled", "values": ["enabled", "disabled"]},
    "beetle_saturn_shared_ext": {"default": "disabled", "values": ["enabled", "disabled"]},
}
SATURN_CORE = {"library_name": "Beetle Saturn", "options": SATURN_REGISTERED}
SATURN_ROM = "/mnt/sd/retrodeck/roms/saturn/Sega Rally Championship (USA).chd"
SATURN_STEM = "Sega Rally Championship (USA)"


def _saturn_query(files):
    rd = _retrodeck(files, cores={f"{DEPLOY}/mednafen_saturn_libretro.so": SATURN_CORE})
    return placed(
        rd.savefile_location(content_path=SATURN_ROM, core_so="mednafen_saturn_libretro.so")
    )


class TestARuleSelectedCard:
    """Beetle Saturn: two independent switches, four modes, one rule deciding.

    The card keeps the modes as data — what can exist — and the rule reads
    both options live and picks what holds here. The Flycast mechanic a
    client builds on ("is the wanted option active, and where does it
    change") survives pluralization: one reading per switch, and every
    alternative mode names the option combination that reaches it.
    """

    FILES = {
        RETRODECK_JSON: RD_JSON,
        RETRODECK_CFG: CFG,
        SATURN_ROM: "",
        "/mnt/sd/retrodeck/saves/saturn/.keep": "",
    }

    def test_registered_defaults_select_the_per_game_mode(self):
        p = _saturn_query(self.FILES)
        assert p.dir == "/mnt/sd/retrodeck/saves/saturn"
        assert p.file_set.state == "declared"
        assert p.file_set.files == (
            f"{SATURN_STEM}.bkr",
            f"{SATURN_STEM}.bcr",
            f"{SATURN_STEM}.smpc",
        )
        roles = [(g.files, g.granularity, g.role) for g in p.file_set.groups]
        assert roles == [
            ((f"{SATURN_STEM}.bkr",), "per-game-file", "battery"),
            ((f"{SATURN_STEM}.bcr",), "per-game-file", "battery"),
            ((f"{SATURN_STEM}.smpc",), "per-game-file", "settings"),
        ]
        assert p.granularity is not None
        assert p.granularity.value == "per-game-file"
        assert p.granularity.mode == "per-game"

    def test_each_switch_is_one_reading_with_its_own_provenance(self):
        g = _saturn_query(self.FILES).granularity
        assert g is not None
        assert [(r.key, r.value) for r in g.readings] == [
            ("beetle_saturn_shared_int", "disabled"),
            ("beetle_saturn_shared_ext", "disabled"),
        ]
        assert all("core default" in r.provenance for r in g.readings)

    def test_every_alternative_names_the_combination_that_reaches_it(self):
        g = _saturn_query(self.FILES).granularity
        assert g is not None
        assert [(a.mode, dict(a.options), a.values) for a in g.alternatives] == [
            (
                "internal-shared",
                {"beetle_saturn_shared_int": "enabled", "beetle_saturn_shared_ext": "disabled"},
                ("shared-file", "per-game-file"),
            ),
            (
                "cartridge-shared",
                {"beetle_saturn_shared_int": "disabled", "beetle_saturn_shared_ext": "enabled"},
                ("per-game-file", "shared-file"),
            ),
            (
                "both-shared",
                {"beetle_saturn_shared_int": "enabled", "beetle_saturn_shared_ext": "enabled"},
                ("shared-file",),
            ),
        ]

    def test_a_flipped_switch_swaps_the_internal_pair_to_the_shared_stem(self):
        p = _saturn_query(
            {**self.FILES, OPTIONS_CFG: 'beetle_saturn_shared_int = "enabled"\n'}
        )
        assert p.granularity is not None
        assert p.granularity.mode == "internal-shared"
        assert p.granularity.value == "shared-file"
        assert p.file_set.files == (
            "mednafen_saturn_libretro_shared.bkr",
            f"{SATURN_STEM}.bcr",
            "mednafen_saturn_libretro_shared.smpc",
        )
        int_reading = p.granularity.readings[0]
        assert int_reading.value == "enabled"
        assert int_reading.options_file == OPTIONS_CFG

    def test_the_observed_trio_outranks_the_declaration(self):
        p = _saturn_query(
            {
                **self.FILES,
                f"/mnt/sd/retrodeck/saves/saturn/{SATURN_STEM}.bkr": "backup",
                f"/mnt/sd/retrodeck/saves/saturn/{SATURN_STEM}.bcr": "backup",
                f"/mnt/sd/retrodeck/saves/saturn/{SATURN_STEM}.smpc": "smpc",
            }
        )
        assert p.file_set.state == "observed"
        assert p.file_set.files == (
            f"{SATURN_STEM}.bcr",
            f"{SATURN_STEM}.bkr",
            f"{SATURN_STEM}.smpc",
        )

    def test_a_stored_value_outside_the_registration_falls_to_the_default(self):
        # RetroArch's option manager keeps the core default when a persisted
        # value is invalid; the rule sees what the core would run with, and
        # the passed-over value is stated.
        p = _saturn_query(
            {**self.FILES, OPTIONS_CFG: 'beetle_saturn_shared_int = "sometimes"\n'}
        )
        assert p.granularity is not None
        assert p.granularity.mode == "per-game"
        stated = [c for c in p.caveats if c.code == atlas.CAVEAT_UNKNOWN_OPTION_VALUE]
        assert stated
        assert stated[0].data["value"] == "sometimes"

    def test_a_missing_switch_retires_the_card_as_a_generation_mismatch(self):
        core = {
            "library_name": "Beetle Saturn",
            "options": {
                "beetle_saturn_shared_int": {
                    "default": "disabled",
                    "values": ["enabled", "disabled"],
                }
            },
        }
        rd = _retrodeck(self.FILES, cores={f"{DEPLOY}/mednafen_saturn_libretro.so": core})
        p = placed(
            rd.savefile_location(content_path=SATURN_ROM, core_so="mednafen_saturn_libretro.so")
        )
        assert p.granularity is None
        stated = [c for c in p.caveats if c.code == atlas.CAVEAT_CORE_GENERATION_MISMATCH]
        assert stated
        assert stated[0].data["options"] == "beetle_saturn_shared_ext"


HATARI_REGISTERED = {
    "hatari_writeprotect_floppy": {"default": "off", "values": ["on", "off", "auto"]},
    "hatari_writeprotect_hd": {"default": "off", "values": ["on", "off", "auto"]},
}
HATARI_CORE = {"library_name": "hatari", "options": HATARI_REGISTERED}


def _hatari_query(files, *, content_path):
    rd = _retrodeck(files, cores={f"{DEPLOY}/hatari_libretro.so": HATARI_CORE})
    return placed(rd.savefile_location(content_path=content_path, core_so="hatari_libretro.so"))


class TestHatariContentClasses:
    """Which write-protect option governs is the content's class, read by the rule."""

    FLOPPY = "/mnt/sd/retrodeck/roms/atarist/Turrican (Europe).st"
    HARD_DISK = "/mnt/sd/retrodeck/roms/atarist/GEM Drive.vhd"
    FILES = {
        RETRODECK_JSON: RD_JSON,
        RETRODECK_CFG: CFG,
        FLOPPY: "",
        HARD_DISK: "",
        "/mnt/sd/retrodeck/saves/atarist/.keep": "",
    }

    def test_a_floppy_with_protection_off_saves_into_its_own_image(self):
        p = _hatari_query(self.FILES, content_path=self.FLOPPY)
        assert p.root_kind == "content_directory"
        assert p.dir == "/mnt/sd/retrodeck/roms/atarist"
        assert p.file_set.state == "declared"
        assert p.file_set.files == ()
        stated = [c for c in p.caveats if c.code == atlas.CAVEAT_SAVE_INSIDE_CONTENT]
        assert stated
        assert stated[0].data["mode"] == "floppy-writeback"

    def test_only_the_governing_class_switch_is_read(self):
        g = _hatari_query(self.FILES, content_path=self.FLOPPY).granularity
        assert g is not None
        assert [r.key for r in g.readings] == ["hatari_writeprotect_floppy"]
        assert [(a.mode, dict(a.options), a.values) for a in g.alternatives] == [
            ("floppy-discarded", {"hatari_writeprotect_floppy": "on"}, ("none",))
        ]

    def test_protection_on_states_that_nothing_keeps_the_progress(self):
        p = _hatari_query(
            {**self.FILES, OPTIONS_CFG: 'hatari_writeprotect_floppy = "on"\n'},
            content_path=self.FLOPPY,
        )
        assert p.file_set.state == "declared"
        assert p.file_set.files == ()
        stated = [c for c in p.caveats if c.code == atlas.CAVEAT_SAVE_WRITES_DISCARDED]
        assert stated
        assert stated[0].data["mode"] == "floppy-discarded"
        assert p.granularity is not None
        assert p.granularity.value == "none"
        assert [(a.mode, a.values) for a in p.granularity.alternatives] == [
            ("floppy-writeback", ("per-game-file",))
        ]

    def test_hard_disk_content_is_governed_by_its_own_switch(self):
        p = _hatari_query(self.FILES, content_path=self.HARD_DISK)
        assert p.granularity is not None
        assert p.granularity.mode == "hard-disk-writeback"
        assert [r.key for r in p.granularity.readings] == ["hatari_writeprotect_hd"]

    def test_without_content_the_class_is_unestablished_and_the_card_steps_aside(self):
        p = _hatari_query(self.FILES, content_path=None)
        assert p.granularity is None
        stated = [c for c in p.caveats if c.code == atlas.CAVEAT_CORE_MODE_UNESTABLISHED]
        assert stated
        assert stated[0].data["core"] == "hatari"
        assert "no content was named" in stated[0].data["reason"]


SCUMMVM_CORE = {"library_name": "ScummVM", "options": {}}
SCUMMVM_ROM = "/mnt/sd/retrodeck/roms/scummvm/monkey1.scummvm"
SCUMMVM_INI = "/mnt/sd/retrodeck/bios/scummvm.ini"


def _scummvm_query(files):
    rd = _retrodeck(files, cores={f"{DEPLOY}/scummvm_libretro.so": SCUMMVM_CORE})
    return placed(rd.savefile_location(content_path=SCUMMVM_ROM, core_so="scummvm_libretro.so"))


class TestScummvmSavepath:
    """The save directory is ScummVM's own setting, read the way the emulator does."""

    FILES = {
        RETRODECK_JSON: RD_JSON,
        RETRODECK_CFG: CFG,
        SCUMMVM_ROM: "",
        "/mnt/sd/retrodeck/saves/scummvm/.keep": "",
    }

    def test_without_an_ini_the_frontend_save_dir_holds_the_unnamed_slots(self):
        p = _scummvm_query(self.FILES)
        assert p.dir == "/mnt/sd/retrodeck/saves/scummvm"
        assert p.file_set.state == "declared"
        assert p.file_set.files == ()
        assert not p.file_set.complete
        assert [(g.files, g.granularity, g.role) for g in p.file_set.groups] == [
            (None, "per-game-files", "battery")
        ]
        stated = [c for c in p.caveats if c.code == atlas.CAVEAT_FILE_NAMES_UNESTABLISHED]
        assert stated
        assert stated[0].data["dir"] == "/mnt/sd/retrodeck/saves/scummvm"
        assert "launcher configuration" in stated[0].data["citation"]

    def test_the_savepath_reading_says_where_the_setting_would_change(self):
        g = _scummvm_query(self.FILES).granularity
        assert g is not None
        assert g.mode == "frontend-save-dir"
        assert g.value == "per-game-files"
        assert [(r.key, r.value, r.options_file) for r in g.readings] == [
            ("savepath", None, SCUMMVM_INI)
        ]
        assert g.alternatives == ()

    def test_a_savepath_spelling_the_save_dir_is_the_default_not_a_redirect(self):
        p = _scummvm_query(
            {
                **self.FILES,
                SCUMMVM_INI: "[scummvm]\nsavepath=/mnt/sd/retrodeck/saves/scummvm\n",
            }
        )
        assert p.granularity is not None
        assert p.granularity.mode == "frontend-save-dir"
        assert p.granularity.readings[0].value == "/mnt/sd/retrodeck/saves/scummvm"
        assert not any(c.code == atlas.CAVEAT_SAVE_ROOT_REDIRECTED for c in p.caveats)

    def test_a_savepath_routed_elsewhere_is_stated_machine_readably(self):
        p = _scummvm_query(
            {
                **self.FILES,
                SCUMMVM_INI: "[scummvm]\nsavepath=/mnt/sd/scumm-saves\n",
                "/mnt/sd/scumm-saves/.keep": "",
            }
        )
        assert p.granularity is None
        stated = [c for c in p.caveats if c.code == atlas.CAVEAT_SAVE_ROOT_REDIRECTED]
        assert stated
        assert stated[0].data["path"] == "/mnt/sd/scumm-saves"
        assert stated[0].data["options_file"] == SCUMMVM_INI

    def test_a_savepath_that_is_not_a_directory_falls_back_like_the_emulator(self):
        # checkPathSetting removes the bad key and re-sets the default — the
        # rule answers the default mode, and the reading's provenance says why.
        p = _scummvm_query(
            {**self.FILES, SCUMMVM_INI: "[scummvm]\nsavepath=/mnt/sd/gone\n"}
        )
        assert p.granularity is not None
        assert p.granularity.mode == "frontend-save-dir"
        assert "not a directory" in p.granularity.readings[0].provenance

    def test_an_unreadable_ini_leaves_the_mode_unestablished(self):
        p = _scummvm_query({**self.FILES, SCUMMVM_INI: {"status": "unreadable"}})
        assert p.granularity is None
        stated = [c for c in p.caveats if c.code == atlas.CAVEAT_CORE_MODE_UNESTABLISHED]
        assert stated
        assert "could not be read" in stated[0].data["reason"]


MAME_INI_DIR = "/mnt/sd/retrodeck/bios/mame/ini"
MAME_OWN = 'mame_mame_paths_enable = "enabled"\n'
MAME_OWN_INI = 'mame_mame_paths_enable = "enabled"\nmame_read_config = "enabled"\n'


class TestMameOwnPaths:
    """Two switches: the frontend's paths hold, or MAME's own world governs."""

    FILES = {RETRODECK_JSON: RD_JSON, RETRODECK_CFG: CFG, MAME_ROM: "", SAVES_KEEP: ""}

    def test_the_frontend_mode_reads_one_switch_and_offers_no_alternative(self):
        # With the paths switch off the glue's command line outranks every ini,
        # so the second switch cannot matter and is deliberately not consulted.
        g = _mame_query(self.FILES).granularity
        assert g is not None
        assert g.mode == "frontend-paths"
        assert [r.key for r in g.readings] == ["mame_mame_paths_enable"]
        assert g.alternatives == ()

    def test_paths_handed_back_without_ini_reading_anchor_at_the_process(self):
        p = _mame_query({**self.FILES, OPTIONS_CFG: MAME_OWN})
        assert p.granularity is None
        stated = [c for c in p.caveats if c.code == atlas.CAVEAT_SAVE_ROOT_UNRESOLVABLE]
        assert stated
        assert stated[0].data["nvram_directory"] == "states/mame/nvram"
        assert stated[0].data["cfg_directory"] == "states/mame/cfg"
        assert stated[0].data["diff_directory"] == "system/mame/diff"
        assert "working directory" in stated[0].message

    def test_an_absolute_ini_value_is_a_redirect_per_tree(self):
        p = _mame_query(
            {
                **self.FILES,
                OPTIONS_CFG: MAME_OWN_INI,
                f"{MAME_INI_DIR}/mame.ini": "nvram_directory   /mnt/sd/mame-nvram\n",
            }
        )
        redirected = [c for c in p.caveats if c.code == atlas.CAVEAT_SAVE_ROOT_REDIRECTED]
        assert [(c.data["key"], c.data["path"]) for c in redirected] == [
            ("nvram_directory", "/mnt/sd/mame-nvram")
        ]
        assert redirected[0].data["options_file"] == f"{MAME_INI_DIR}/mame.ini"
        unresolved = [c for c in p.caveats if c.code == atlas.CAVEAT_SAVE_ROOT_UNRESOLVABLE]
        assert unresolved
        assert "nvram_directory" not in unresolved[0].data
        assert unresolved[0].data["cfg_directory"] == "states/mame/cfg"
        assert unresolved[0].data["diff_directory"] == "system/mame/diff"

    def test_the_drivers_ini_outranks_the_main_one(self):
        p = _mame_query(
            {
                **self.FILES,
                OPTIONS_CFG: MAME_OWN_INI,
                f"{MAME_INI_DIR}/mame.ini": "cfg_directory   /mnt/sd/mame-cfg-main\n",
                f"{MAME_INI_DIR}/dkong.ini": 'cfg_directory   "/mnt/sd/mame-cfg-dkong"\n',
            }
        )
        redirected = [c for c in p.caveats if c.code == atlas.CAVEAT_SAVE_ROOT_REDIRECTED]
        assert [(c.data["path"], c.data["options_file"]) for c in redirected] == [
            ("/mnt/sd/mame-cfg-dkong", f"{MAME_INI_DIR}/dkong.ini")
        ]

    def test_a_home_ini_shadows_the_system_one(self):
        # $HOME/.mame comes first on the search path and the first find wins —
        # the system-dir failsafe never gets asked.
        p = _mame_query(
            {
                **self.FILES,
                OPTIONS_CFG: MAME_OWN_INI,
                f"{HOME}/.mame/mame.ini": "nvram_directory   /mnt/sd/home-nvram\n",
                f"{MAME_INI_DIR}/mame.ini": "nvram_directory   /mnt/sd/system-nvram\n",
            }
        )
        redirected = [c for c in p.caveats if c.code == atlas.CAVEAT_SAVE_ROOT_REDIRECTED]
        assert [(c.data["path"], c.data["options_file"]) for c in redirected] == [
            ("/mnt/sd/home-nvram", f"{HOME}/.mame/mame.ini")
        ]

    def test_a_cascade_member_nobody_can_attribute_refuses_the_mode(self):
        p = _mame_query(
            {
                **self.FILES,
                OPTIONS_CFG: MAME_OWN_INI,
                f"{MAME_INI_DIR}/mame.ini": "nvram_directory   /mnt/sd/mame-nvram\n",
                f"{MAME_INI_DIR}/vertical.ini": "nvram_directory   /mnt/sd/other\n",
            }
        )
        assert not any(c.code == atlas.CAVEAT_SAVE_ROOT_REDIRECTED for c in p.caveats)
        stated = [c for c in p.caveats if c.code == atlas.CAVEAT_CORE_MODE_UNESTABLISHED]
        assert stated
        assert "vertical.ini" in stated[0].data["reason"]

    def test_an_unreadable_main_ini_leaves_the_mode_unestablished(self):
        p = _mame_query(
            {
                **self.FILES,
                OPTIONS_CFG: MAME_OWN_INI,
                f"{MAME_INI_DIR}/mame.ini": {"status": "unreadable"},
            }
        )
        stated = [c for c in p.caveats if c.code == atlas.CAVEAT_CORE_MODE_UNESTABLISHED]
        assert stated
        assert "could not be established" in stated[0].data["reason"]

    def test_an_unlistable_search_directory_refuses_the_shadow_check(self):
        # The values read may be overridden by a file the listing would have
        # shown — deciding without the listing would be the guess.
        rd = _retrodeck(
            {
                **self.FILES,
                OPTIONS_CFG: MAME_OWN_INI,
            },
            unlistable=[MAME_INI_DIR],
            cores={f"{DEPLOY}/mame_libretro.so": MAME_CORE},
        )
        p = placed(rd.savefile_location(content_path=MAME_ROM, core_so="mame_libretro.so"))
        stated = [c for c in p.caveats if c.code == atlas.CAVEAT_CORE_MODE_UNESTABLISHED]
        assert stated
        assert "could not be listed" in stated[0].data["reason"]

    def test_the_second_switch_alone_changes_nothing(self):
        # read_config on while the frontend's paths hold: the command line
        # outranks whatever the ini says, and the answer stays the mode.
        g = _mame_query(
            {**self.FILES, OPTIONS_CFG: 'mame_read_config = "enabled"\n'}
        ).granularity
        assert g is not None
        assert g.mode == "frontend-paths"
        assert [r.key for r in g.readings] == ["mame_mame_paths_enable"]


SWAN_REGISTERED = {
    "swanstation_MemoryCards_Card1Type": {
        "default": "Libretro",
        "values": ["Libretro", "Shared", "PerGame", "PerGameTitle", "None"],
    },
    "swanstation_MemoryCards_Card2Type": {
        "default": "None",
        "values": ["None", "Shared", "PerGame", "PerGameTitle"],
    },
    "swanstation_MemoryCards_UsePlaylistTitle": {"default": "true", "values": ["true", "false"]},
}
SWAN_CORE = {"library_name": "SwanStation", "options": SWAN_REGISTERED}
SWAN_ROM = "/mnt/sd/retrodeck/roms/psx/Vagrant Story (USA).chd"


def _swan_query(files):
    rd = _retrodeck(files, cores={f"{DEPLOY}/swanstation_libretro.so": SWAN_CORE})
    return placed(rd.savefile_location(content_path=SWAN_ROM, core_so="swanstation_libretro.so"))


class TestSwanStationSlotPair:
    """Two slots, each with its own type — the mode is the pair."""

    FILES = {
        RETRODECK_JSON: RD_JSON,
        RETRODECK_CFG: CFG,
        SWAN_ROM: "",
        "/mnt/sd/retrodeck/saves/psx/.keep": "",
    }

    def test_the_default_pair_is_the_frontends_srm_alone(self):
        p = _swan_query(self.FILES)
        assert p.file_set.files == ("Vagrant Story (USA).srm",)
        assert p.granularity is not None
        assert p.granularity.mode == "card1-libretro+card2-none"
        assert p.granularity.value == "per-game-file"
        assert [r.key for r in p.granularity.readings] == [
            "swanstation_MemoryCards_Card1Type",
            "swanstation_MemoryCards_Card2Type",
        ]

    def test_the_alternatives_are_the_one_edit_neighbours(self):
        g = _swan_query(self.FILES).granularity
        assert g is not None
        # Four other slot-1 types and three other slot-2 types — never the
        # whole twenty-mode product.
        assert len(g.alternatives) == 7
        assert [(a.mode, dict(a.options)) for a in g.alternatives[:2]] == [
            (
                "card1-shared+card2-none",
                {"swanstation_MemoryCards_Card1Type": "Shared"},
            ),
            (
                "card1-per-code+card2-none",
                {"swanstation_MemoryCards_Card1Type": "PerGame"},
            ),
        ]

    def test_a_code_keyed_card_keeps_the_save_id_hole(self):
        p = _swan_query(
            {
                **self.FILES,
                OPTIONS_CFG: 'swanstation_MemoryCards_Card1Type = "PerGame"\n'
                'swanstation_MemoryCards_Card2Type = "Shared"\n',
            }
        )
        assert p.granularity is not None
        assert p.granularity.mode == "card1-per-code+card2-shared"
        assert p.file_set.files == ("<save_id>_1.mcd", "duckstation_shared_card_2.mcd")
        assert "save_id" in p.needs
        # A disc that yields no game code falls back to the shared card —
        # the id-less spelling travels in the caveat's data.
        stated = [c for c in p.caveats if c.code == atlas.CAVEAT_FILENAMES_CONTENT_CONDITIONAL]
        assert stated
        assert stated[0].data["files_without_save_id"] == "duckstation_shared_card_1.mcd"

    def test_no_cards_at_all_is_the_discarded_emptiness_under_the_save_root(self):
        p = _swan_query(
            {
                **self.FILES,
                OPTIONS_CFG: 'swanstation_MemoryCards_Card1Type = "None"\n',
            }
        )
        assert p.root_kind == "savefile_directory"
        assert p.file_set.state == "declared"
        assert p.file_set.files == ()
        stated = [c for c in p.caveats if c.code == atlas.CAVEAT_SAVE_WRITES_DISCARDED]
        assert stated
        assert stated[0].data["mode"] == "card1-none+card2-none"
        assert p.granularity is not None
        assert p.granularity.value == "none"

    def test_a_leftover_srm_is_not_presented_as_the_discarded_modes_save(self):
        p = _swan_query(
            {
                **self.FILES,
                OPTIONS_CFG: 'swanstation_MemoryCards_Card1Type = "None"\n',
                "/mnt/sd/retrodeck/saves/psx/Vagrant Story (USA).srm": "old",
            }
        )
        assert p.file_set.state == "declared"
        assert p.file_set.files == ()

    def test_a_title_keyed_slot_reads_the_playlist_switch_too(self):
        p = _swan_query(
            {
                **self.FILES,
                OPTIONS_CFG: 'swanstation_MemoryCards_Card1Type = "PerGameTitle"\n',
            }
        )
        assert p.granularity is not None
        assert p.granularity.mode == "card1-per-title+card2-none"
        assert p.file_set.files == ("Vagrant Story (USA)_1.mcd",)
        assert [r.key for r in p.granularity.readings] == [
            "swanstation_MemoryCards_Card1Type",
            "swanstation_MemoryCards_Card2Type",
            "swanstation_MemoryCards_UsePlaylistTitle",
        ]


BEETLE_REGISTERED = {
    "beetle_psx_use_mednafen_memcard0_method": {
        "default": "libretro",
        "values": ["libretro", "mednafen"],
    },
    "beetle_psx_enable_memcard1": {"default": "enabled", "values": ["enabled", "disabled"]},
    "beetle_psx_shared_memory_cards": {"default": "disabled", "values": ["enabled", "disabled"]},
    "beetle_psx_memcard_left_index": {"default": "0", "values": [str(i) for i in range(64)]},
    "beetle_psx_memcard_right_index": {"default": "1", "values": [str(i) for i in range(64)]},
}
BEETLE_CORE = {"library_name": "Beetle PSX", "options": BEETLE_REGISTERED}


def _beetle_query(files):
    rd = _retrodeck(files, cores={f"{DEPLOY}/mednafen_psx_libretro.so": BEETLE_CORE})
    return placed(rd.savefile_location(content_path=SWAN_ROM, core_so="mednafen_psx_libretro.so"))


class TestBeetlePsxSecondCard:
    """Issue #80's answer: the default-enabled slot 1 is a core-written .mcr."""

    FILES = {
        RETRODECK_JSON: RD_JSON,
        RETRODECK_CFG: CFG,
        SWAN_ROM: "",
        "/mnt/sd/retrodeck/saves/psx/.keep": "",
    }

    def test_the_default_answer_is_the_srm_beside_the_second_card(self):
        p = _beetle_query(self.FILES)
        assert p.granularity is not None
        assert p.granularity.mode == "srm+second-card"
        assert p.file_set.files == ("Vagrant Story (USA).srm", "Vagrant Story (USA).1.mcr")
        assert [(g.files, g.role) for g in p.file_set.groups] == [
            (("Vagrant Story (USA).srm",), "memory-card"),
            (("Vagrant Story (USA).1.mcr",), "memory-card"),
        ]
        # The index options are read only where a slot they name is in play —
        # slot 0 is on the libretro method here, so only the right index shows.
        assert [r.key for r in p.granularity.readings] == [
            "beetle_psx_use_mednafen_memcard0_method",
            "beetle_psx_enable_memcard1",
            "beetle_psx_shared_memory_cards",
            "beetle_psx_memcard_right_index",
        ]

    def test_sharing_swaps_the_core_written_stem_and_never_the_srm(self):
        p = _beetle_query(
            {**self.FILES, OPTIONS_CFG: 'beetle_psx_shared_memory_cards = "enabled"\n'}
        )
        assert p.granularity is not None
        assert p.granularity.mode == "srm+second-card-shared"
        assert p.file_set.files == (
            "Vagrant Story (USA).srm",
            "mednafen_psx_libretro_shared.1.mcr",
        )

    def test_an_offset_card_index_steps_aside_rather_than_misname(self):
        p = _beetle_query(
            {**self.FILES, OPTIONS_CFG: 'beetle_psx_memcard_right_index = "7"\n'}
        )
        assert p.granularity is None
        stated = [c for c in p.caveats if c.code == atlas.CAVEAT_CORE_MODE_UNESTABLISHED]
        assert stated
        assert "memcard_right_index" in stated[0].data["reason"]


GPGX_REGISTERED = {
    "genesis_plus_gx_system_bram": {"default": "per bios", "values": ["per bios", "per game"]},
    "genesis_plus_gx_cart_bram": {"default": "per cart", "values": ["per cart", "per game"]},
    "genesis_plus_gx_cart_size": {
        "default": "4meg",
        "values": ["disabled", "128k", "256k", "512k", "1meg", "2meg", "4meg"],
    },
}
GPGX_CORE = {"library_name": "Genesis Plus GX", "options": GPGX_REGISTERED}


def _gpgx_query(files, *, rom):
    rd = _retrodeck(files, cores={f"{DEPLOY}/genesis_plus_gx_libretro.so": GPGX_CORE})
    return placed(rd.savefile_location(content_path=rom, core_so="genesis_plus_gx_libretro.so"))


class TestGpgxContentClasses:
    """Cartridges fill the frontend's SRAM; CDs write the core's own BRAM tree."""

    CARTRIDGE = "/mnt/sd/retrodeck/roms/megadrive/Phantasy Star IV (USA).md"
    CD = "/mnt/sd/retrodeck/roms/megacd/Sonic CD (Europe).chd"
    FILES = {
        RETRODECK_JSON: RD_JSON,
        RETRODECK_CFG: CFG,
        CARTRIDGE: "",
        CD: "",
        "/mnt/sd/retrodeck/saves/megadrive/.keep": "",
        "/mnt/sd/retrodeck/saves/megacd/.keep": "",
    }

    def test_a_cartridge_is_the_frontends_srm_with_no_option_read(self):
        p = _gpgx_query(self.FILES, rom=self.CARTRIDGE)
        assert p.granularity is not None
        assert p.granularity.mode == "cartridge"
        assert p.file_set.files == ("Phantasy Star IV (USA).srm",)
        assert p.granularity.readings == ()

    def test_a_cd_at_the_defaults_keeps_the_region_trio_and_the_shared_cart(self):
        p = _gpgx_query(self.FILES, rom=self.CD)
        assert p.granularity is not None
        assert p.granularity.mode == "cd-bios-bram+cart-4meg"
        assert p.granularity.value == "shared-file"
        assert p.file_set.files == ("scd_E.brm", "scd_U.brm", "scd_J.brm", "4Mbit_cart.brm")
        assert [(g.granularity, g.role) for g in p.file_set.groups] == [
            ("shared-file", "battery"),
            ("shared-file", "battery"),
        ]

    def test_per_game_bram_keys_both_files_on_the_content(self):
        p = _gpgx_query(
            {
                **self.FILES,
                OPTIONS_CFG: 'genesis_plus_gx_system_bram = "per game"\n'
                'genesis_plus_gx_cart_bram = "per game"\n'
                'genesis_plus_gx_cart_size = "128k"\n',
            },
            rom=self.CD,
        )
        assert p.granularity is not None
        assert p.granularity.mode == "cd-game-bram+cart-128k-per-game"
        assert p.file_set.files == (
            "Sonic CD (Europe).brm",
            "Sonic CD (Europe)_128Kbit_cart.brm",
        )

    def test_a_raw_bin_is_the_scoped_cartridge_answer(self):
        rom = "/mnt/sd/retrodeck/roms/megadrive/Sonic 3 (USA).bin"
        p = _gpgx_query({**self.FILES, rom: ""}, rom=rom)
        assert p.granularity is not None
        assert p.granularity.mode == "cartridge-raw-image"
        stated = [c for c in p.caveats if c.code == atlas.CAVEAT_FILENAMES_CONTENT_CONDITIONAL]
        assert stated
        assert "Sega CD disc header" in stated[0].data["files_established_for"]


class TestAMixedModeAlternativeStatesEveryGrouping:
    """Issue #128's case: FinalBurn Neo's shared mode writes a per-game save
    beside a card every game shares, and a single-value alternative hid the
    shared file — the one direction the understatement could hurt. The plural
    carries both, the mode's own first."""

    FBNEO_ROM = "/mnt/sd/retrodeck/roms/arcade/mslug.zip"
    FBNEO_CORE = {
        "library_name": "FinalBurn Neo",
        "options": {
            "fbneo-memcard-mode": {
                "default": "disabled",
                "values": ["disabled", "shared", "per-game"],
            }
        },
    }

    def test_the_shared_alternative_carries_the_hidden_card(self):
        rd = _retrodeck(
            {
                RETRODECK_JSON: RD_JSON,
                RETRODECK_CFG: CFG,
                self.FBNEO_ROM: "",
                "/mnt/sd/retrodeck/saves/arcade/.keep": "",
            },
            cores={f"{DEPLOY}/fbneo_libretro.so": self.FBNEO_CORE},
        )
        p = placed(rd.savefile_location(content_path=self.FBNEO_ROM, core_so="fbneo_libretro.so"))
        assert p.granularity is not None
        assert p.granularity.mode == "disabled"
        by_mode = {a.mode: a.values for a in p.granularity.alternatives}
        assert by_mode["shared"] == ("per-game-file", "shared-card")
        assert by_mode["per-game"] == ("per-game-file",)


class TestStrictLoaders:
    """Packaged data is validated, never coerced — a broken build fails loudly."""

    def test_unknown_oddities_schema_is_rejected(self):
        with pytest.raises(ValueError, match="schema"):
            load_oddities('{"schema": 99, "cores": {}}')

    def test_missing_audit_schema_is_rejected(self):
        with pytest.raises(ValueError, match="schema"):
            load_audit('{"cores": {}}')

    def test_unknown_verdict_is_rejected(self):
        text = json.dumps(
            {
                "schema": 3,
                "cores": {
                    "x": {
                        "verdict": "fine-probably",
                        "per_game_capable": None,
                        "note": "unproven",
                        "verified": {},
                    }
                },
            }
        )
        with pytest.raises(ValueError, match="verdict"):
            load_audit(text)

    def test_audit_capability_and_note_are_loaded(self):
        text = json.dumps(
            {
                "schema": 3,
                "cores": {
                    "x": {
                        "verdict": "standard",
                        "per_game_capable": True,
                        "note": "source-verified",
                        "verified": {},
                    }
                },
            }
        )
        entry = load_audit(text)["x"]
        assert entry.per_game_capable is True
        assert entry.note == "source-verified"

    def test_missing_per_game_capability_is_rejected(self):
        text = json.dumps(
            {
                "schema": 3,
                "cores": {"x": {"verdict": "standard", "note": "source-verified", "verified": {}}},
            }
        )
        with pytest.raises(ValueError, match="per_game_capable"):
            load_audit(text)

    def test_non_boolean_per_game_capability_is_rejected(self):
        text = json.dumps(
            {
                "schema": 3,
                "cores": {
                    "x": {
                        "verdict": "standard",
                        "per_game_capable": 1,
                        "note": "source-verified",
                        "verified": {},
                    }
                },
            }
        )
        with pytest.raises(ValueError, match="boolean or null"):
            load_audit(text)

    @pytest.mark.parametrize("note", ["", None, 1])
    def test_invalid_audit_note_is_rejected(self, note):
        text = json.dumps(
            {
                "schema": 3,
                "cores": {
                    "x": {
                        "verdict": "standard",
                        "per_game_capable": None,
                        "note": note,
                        "verified": {},
                    }
                },
            }
        )
        with pytest.raises(ValueError, match="note"):
            load_audit(text)

    def test_multi_option_save_options_are_loaded(self):
        text = json.dumps(
            {
                "schema": 3,
                "cores": {
                    "x": {
                        "verdict": "multi-option",
                        "per_game_capable": True,
                        "note": "source-verified",
                        "save_options": ["x_card1", "x_card2"],
                        "verified": {},
                    }
                },
            }
        )
        assert load_audit(text)["x"].save_options == ("x_card1", "x_card2")

    def test_multi_option_without_save_options_is_rejected(self):
        # The verdict IS the claim that options decide the answer — an entry
        # that cannot name them would make the caveat say "unknown" again.
        text = json.dumps(
            {
                "schema": 3,
                "cores": {
                    "x": {
                        "verdict": "multi-option",
                        "per_game_capable": True,
                        "note": "source-verified",
                        "verified": {},
                    }
                },
            }
        )
        with pytest.raises(ValueError, match="save_options"):
            load_audit(text)

    def test_save_options_on_another_verdict_are_rejected(self):
        text = json.dumps(
            {
                "schema": 3,
                "cores": {
                    "x": {
                        "verdict": "standard",
                        "per_game_capable": True,
                        "note": "source-verified",
                        "save_options": ["x_card1"],
                        "verified": {},
                    }
                },
            }
        )
        with pytest.raises(ValueError, match="save_options"):
            load_audit(text)

    def test_non_string_save_options_are_rejected(self):
        text = json.dumps(
            {
                "schema": 3,
                "cores": {
                    "x": {
                        "verdict": "multi-option",
                        "per_game_capable": True,
                        "note": "source-verified",
                        "save_options": [1],
                        "verified": {},
                    }
                },
            }
        )
        with pytest.raises(ValueError, match="save_options"):
            load_audit(text)

    def test_non_boolean_complete_is_rejected(self):
        text = self._mode_card(
            {"root": "system_directory", "granularity": "shared-card", "complete": "false"}
        )
        with pytest.raises(ValueError, match="complete"):
            load_oddities(text)

    @pytest.mark.parametrize(
        "name",
        [
            "<game_id>.bin",  # well-formed, unknown
            "<rom_stem.A1.bin",  # never closes — a token scan would pass this
            "<<rom_stem>>.A1.bin",  # nested
            "rom_stem>.A1.bin",  # never opens
        ],
    )
    def test_a_malformed_or_unknown_template_is_rejected(self, name):
        # The guarantee the card format documents: a typo cannot travel into a
        # filename atlas states as fact. Only removal of the known tokens can
        # decide that — matching well-formed <…> would let three of these pass.
        text = self._mode_card({"granularity": "per-game-file", "files": [name]})
        with pytest.raises(ValueError, match="unknown template"):
            load_oddities(text)

    @pytest.mark.parametrize("field", ["files", "observe", "files_without_save_id"])
    def test_an_empty_file_list_is_rejected(self, field):
        # A declared set with no files is shape-identical to 'unknown' but
        # labelled 'declared' — and an empty alternative is an empty promise.
        mode = {"granularity": "per-game-file", "files": ["<save_id>.A1.bin"], field: []}
        with pytest.raises(ValueError, match="empty list"):
            load_oddities(self._mode_card(mode))

    def test_an_empty_file_name_is_rejected(self):
        text = self._mode_card({"granularity": "per-game-file", "files": [""]})
        with pytest.raises(ValueError, match="not a file name"):
            load_oddities(text)

    def test_the_retired_also_under_field_is_refused_loudly(self):
        # Retired with issue #97: a card still spelling it would silently
        # lose the claim it thinks it makes.
        text = self._mode_card({"granularity": "per-game-file", "also_under": "savefile_directory"})
        with pytest.raises(ValueError, match="retired"):
            load_oddities(text)

    def test_a_scope_without_a_declared_set_is_rejected(self):
        text = self._mode_card({"granularity": "per-game-file", "files_established_for": "console"})
        with pytest.raises(ValueError, match="files_established_for"):
            load_oddities(text)

    @pytest.mark.parametrize("field", ["files_established_for", "files_citation"])
    def test_an_empty_scope_or_citation_is_rejected(self, field):
        # Both reach the caller as caveat data; an empty one states nothing.
        mode = {
            "granularity": "per-game-file",
            "files": ["<save_id>.A1.bin"],
            "files_established_for": "console",
            field: "",
        }
        with pytest.raises(ValueError, match=field):
            load_oddities(self._mode_card(mode))

    def test_a_citation_without_a_scope_is_rejected(self):
        text = self._mode_card(
            {
                "granularity": "per-game-file",
                "files": ["<save_id>.A1.bin"],
                "files_citation": "somewhere.cpp:1",
            }
        )
        with pytest.raises(ValueError, match="files_citation"):
            load_oddities(text)

    @pytest.mark.parametrize("field", ["files", "observe"])
    def test_unknown_file_template_is_rejected(self, field):
        # A token nobody fills would be stated as literal text in a filename —
        # the card language is the placement's hole vocabulary, nothing else.
        text = self._mode_card(
            {"granularity": "per-game-file", "files": ["<save_id>.bin"], field: ["<game_id>.bin"]}
        )
        with pytest.raises(ValueError, match="unknown template"):
            load_oddities(text)

    def test_known_file_templates_are_kept_verbatim(self):
        text = self._mode_card(
            {"granularity": "per-game-file", "files": ["<save_id>.A1.bin", "<rom_stem>.srm"]}
        )
        assert load_oddities(text)[0].modes["always"].files == ("<save_id>.A1.bin", "<rom_stem>.srm")

    def test_a_subdir_template_must_be_the_whole_segment(self):
        # An affixed token is a spelling no read core writes, and _base_of's
        # segment arithmetic is exact only while one template fills to one
        # segment — so the loader refuses it rather than stating it.
        text = self._mode_card(
            {"granularity": "per-game-file", "files": ["a.srm"], "subdir": "pre<rom_stem>"}
        )
        with pytest.raises(ValueError, match="whole segment"):
            load_oddities(text)

    def test_an_unknown_subdir_token_is_rejected(self):
        text = self._mode_card(
            {"granularity": "per-game-file", "files": ["a.srm"], "subdir": "<rom_step>"}
        )
        with pytest.raises(ValueError, match="whole segment"):
            load_oddities(text)

    def test_a_subdir_template_under_another_root_is_rejected(self):
        # Both read behaviours key a directory the *save* root hands the core;
        # a content-keyed system directory is a behaviour no reading
        # established, so a card stating one fails to load.
        text = self._mode_card(
            {
                "root": "system_directory",
                "granularity": "per-game-file",
                "files": ["a.srm"],
                "subdir": "<rom_stem>",
            }
        )
        with pytest.raises(ValueError, match="subdir template"):
            load_oddities(text)

    def test_the_known_subdir_templates_load_verbatim(self):
        text = self._mode_card(
            {
                "granularity": "per-game-file",
                "files": ["a.srm"],
                "subdir": "<content_dir_name>/save",
            }
        )
        assert load_oddities(text)[0].modes["always"].subdir == "<content_dir_name>/save"

    def _inside_card(self, mode):
        return json.dumps(
            {
                "schema": 1,
                "cores": {
                    "x": {
                        "identifiers": {"library_name": ["X"]},
                        "saves": {"modes": {"always": mode}},
                    }
                },
            }
        )

    def test_a_save_inside_the_content_loads_as_the_groups_less_form(self):
        mode = load_oddities(
            self._inside_card(
                {"root": "content_directory", "inside_content": "the image is the medium"}
            )
        )[0].modes["always"]
        assert mode.inside_content == "the image is the medium"
        assert mode.groups == ()
        # The group-derived answers the form gives itself: an empty, closed
        # set of separate files, per game by the medium's own nature.
        assert mode.files == ()
        assert mode.complete is True
        assert mode.granularity == "per-game-file"
        assert mode.subdir is None
        assert mode.observe is None

    def test_a_save_inside_the_content_refuses_groups(self):
        text = self._inside_card(
            {
                "root": "content_directory",
                "inside_content": "why",
                "groups": [{"files": ["a.srm"], "granularity": "per-game-file", "role": "battery"}],
            }
        )
        with pytest.raises(ValueError, match="no separate file to group"):
            load_oddities(text)

    def test_a_save_inside_the_content_refuses_another_root(self):
        # The statement is about the content's own tree; under the save root it
        # would claim RetroArch's directory holds a file that is the content.
        text = self._inside_card(
            {"root": "savefile_directory", "inside_content": "why"}
        )
        with pytest.raises(ValueError, match="anchors where the writes would have landed"):
            load_oddities(text)

    def test_a_save_inside_the_content_refuses_the_retired_also_under(self):
        text = self._inside_card(
            {"root": "content_directory", "inside_content": "why", "also_under": "system_directory"}
        )
        with pytest.raises(ValueError, match="retired"):
            load_oddities(text)

    def test_discarded_writes_load_as_the_groups_less_form(self):
        mode = load_oddities(
            self._inside_card(
                {"root": "content_directory", "writes_discarded": "protection is on"}
            )
        )[0].modes["always"]
        assert mode.writes_discarded == "protection is on"
        assert mode.groups == ()
        # The harder emptiness answers its group questions the same way,
        # except the unit: nothing is kept, so there is nothing to group at
        # all — the granularity is "none", never the content file's per-game.
        assert mode.files == ()
        assert mode.complete is True
        assert mode.granularity == GRANULARITY_NONE
        assert mode.subdir is None
        assert mode.observe is None

    def test_discarded_writes_refuse_groups_and_other_roots(self):
        with_groups = self._inside_card(
            {
                "root": "content_directory",
                "writes_discarded": "why",
                "groups": [
                    {"files": ["a.srm"], "granularity": "per-game-file", "role": "battery"}
                ],
            }
        )
        with pytest.raises(ValueError, match="no separate file to group"):
            load_oddities(with_groups)
        # The discarded statement may also anchor at the save root — where
        # the writes would have gone (SwanStation with no cards at all) — but
        # never at a root no save was ever headed for.
        savefile_rooted = self._inside_card(
            {"root": "savefile_directory", "writes_discarded": "why"}
        )
        assert load_oddities(savefile_rooted)[0].modes["always"].writes_discarded == "why"
        other_root = self._inside_card({"root": "system_directory", "writes_discarded": "why"})
        with pytest.raises(ValueError, match="anchors where the writes would have landed"):
            load_oddities(other_root)

    def test_the_two_statements_contradict_each_other(self):
        # One says the content file keeps the save, the other that nothing
        # keeps it — a mode carrying both is not a mode, it is an argument.
        text = self._inside_card(
            {
                "root": "content_directory",
                "inside_content": "kept",
                "writes_discarded": "discarded",
            }
        )
        with pytest.raises(ValueError, match="contradict"):
            load_oddities(text)

    def test_a_mode_of_nothing_but_unnamed_groups_states_the_directory(self):
        # ScummVM's shape: the directory is known, the slot names are the
        # launcher target's and follow from nothing atlas reads. The mode's
        # statable set is empty and never claims to be the whole.
        mode = load_oddities(
            self._inside_card(
                {
                    "root": "savefile_directory",
                    "groups": [
                        {
                            "granularity": "per-game-files",
                            "role": "battery",
                            "unnamed": "slot names come from the launcher target",
                        }
                    ],
                }
            )
        )[0].modes["always"]
        assert mode.named == ()
        assert mode.files == ()
        assert mode.complete is False
        assert mode.granularity == "per-game-files"
        assert mode.subdir is None
        assert mode.unnamed[0].unnamed == "slot names come from the launcher target"

    # Everything a mode used to carry now belongs to one of its groups, except
    # the two fields that are still the mode's own. Splitting here keeps every
    # case below written the way it was — what it asserts did not change.
    _MODE_FIELDS = ("root", "also_under")

    def _mode_card(self, mode, *, anchors=None):
        group = {k: v for k, v in mode.items() if k not in self._MODE_FIELDS}
        group.setdefault("role", "battery")
        rest = {k: v for k, v in mode.items() if k in self._MODE_FIELDS}
        saves = {
            "modes": {"always": {"root": "savefile_directory", "groups": [group], **rest}}
        }
        if anchors is not None:
            saves["anchors"] = anchors
        return json.dumps(
            {"schema": 1, "cores": {"x": {"identifiers": {"library_name": ["X"]}, "saves": saves}}}
        )

    def test_a_restated_so_name_is_rejected(self):
        # The key IS the .so basename, so a second spelling can only ever be a
        # way for the two to disagree — and the disagreement would decide which
        # core a card is applied to. Ignoring the field would be the silent
        # version of the same bug.
        text = json.dumps(
            {
                "schema": 1,
                "cores": {
                    "x": {
                        "identifiers": {"so": ["x_libretro.so"], "library_name": ["X"]},
                        "saves": {"modes": {"always": _mode("shared-card")}},
                    }
                },
            }
        )
        with pytest.raises(ValueError, match="identifiers.so"):
            load_oddities(text)

    def test_an_anchor_for_a_name_the_card_does_not_record_is_rejected(self):
        # An anchor outliving its name protects nothing while looking like it
        # does — and the next name to arrive gets no anchor at all.
        text = self._mode_card(
            {"granularity": "per-game-file", "files": ["a.srm"]},
            anchors={"a.srm": {"literal": "a.srm"}, "b.srm": {"literal": "b.srm"}},
        )
        with pytest.raises(ValueError, match="does not record"):
            load_oddities(text)

    @pytest.mark.parametrize(
        "anchor",
        [
            {},  # states no protection at all
            {"literal": "a.srm", "unprotected": "and also not"},  # two at once
            {"read_it_somewhere": "a.srm"},  # not a kind
            {"literal": ""},  # matches every binary
            {"unprotected": ""},  # states no reason
            "a.srm",  # not an entry
        ],
    )
    def test_a_malformed_anchor_entry_is_rejected(self, anchor):
        text = self._mode_card(
            {"granularity": "per-game-file", "files": ["a.srm"]}, anchors={"a.srm": anchor}
        )
        with pytest.raises(ValueError, match="anchors"):
            load_oddities(text)

    def test_an_anchors_block_that_is_not_a_map_is_rejected(self):
        text = self._mode_card(
            {"granularity": "per-game-file", "files": ["a.srm"]}, anchors=["a.srm"]
        )
        with pytest.raises(ValueError, match="anchors"):
            load_oddities(text)

    @pytest.mark.parametrize("kind", ["literal", "unprotected", "arrangement"])
    def test_every_anchor_kind_loads(self, kind):
        text = self._mode_card(
            {"granularity": "per-game-file", "files": ["a.srm"]}, anchors={"a.srm": {kind: "because"}}
        )
        assert load_oddities(text)[0].key == "x"

    def test_the_recorded_vocabulary_is_every_name_a_card_states(self):
        # What the anchors have to cover: the option key, each segment of a
        # subdir, and every file name in all three lists. Mode keys are not in
        # it — the deployed core registers those, and a measurement beats an
        # anchor.
        text = json.dumps(
            {
                "schema": 1,
                "cores": {
                    "x": {
                        "identifiers": {"library_name": ["X"]},
                        "saves": {
                            "governing_option": {"key": "x_storage"},
                            "modes": {
                                "on": _mode(
                                    "per-game-files",
                                    subdir="x/per_game",
                                    files=["<save_id>.srm"],
                                    files_without_save_id=["<rom_stem>.srm"],
                                    observe=["<save_id>.srm", "<save_id>.bak"],
                                )
                            },
                        },
                    }
                },
            }
        )
        card = load_oddities(text)[0]
        assert recorded_vocabulary(option_key=card.option_key, modes=card.modes) == {
            "x_storage",
            "x",
            "per_game",
            "<save_id>.srm",
            "<save_id>.bak",
            "<rom_stem>.srm",
        }

    def _cross_card(self, groups, *, root="savefile_directory"):
        return json.dumps(
            {
                "schema": 1,
                "cores": {
                    "x": {
                        "identifiers": {"library_name": ["X"]},
                        "saves": {"modes": {"always": {"root": root, "groups": groups}}},
                    }
                },
            }
        )

    _HOME_GROUP = {"files": ["a.srm"], "granularity": "per-game-file", "role": "battery"}

    def test_a_cross_root_group_loads_and_stays_out_of_the_flat_set(self):
        text = self._cross_card(
            [
                self._HOME_GROUP,
                {
                    "root": "system_directory",
                    "subdir": "dc",
                    "files": ["flash.bin"],
                    "granularity": "shared-card",
                    "role": "battery",
                },
            ]
        )
        mode = load_oddities(text)[0].modes["always"]
        assert mode.groups[1].root == "system_directory"
        # The flat set and the mode's own directory stay the primary root's.
        assert mode.files == ("a.srm",)
        assert mode.here == (mode.groups[0],)
        # Every grouping still counts for the alternatives' plural.
        assert mode.granularities == ("per-game-file", "shared-card")

    def test_a_cross_root_group_on_the_first_position_is_refused(self):
        text = self._cross_card(
            [
                {
                    "root": "system_directory",
                    "files": ["flash.bin"],
                    "granularity": "shared-card",
                    "role": "battery",
                },
                self._HOME_GROUP,
            ]
        )
        with pytest.raises(ValueError, match="first group is the mode's own answer"):
            load_oddities(text)

    def test_a_restated_group_root_is_refused(self):
        text = self._cross_card(
            [
                self._HOME_GROUP,
                {
                    "root": "savefile_directory",
                    "files": ["b.srm"],
                    "granularity": "per-game-file",
                    "role": "battery",
                },
            ]
        )
        with pytest.raises(ValueError, match="restates the mode's own"):
            load_oddities(text)

    def test_an_unestablished_cross_root_shape_is_refused(self):
        # Only savefile-rooted modes keep parts behind under system/content —
        # every other direction waits for a core that shows it.
        text = self._cross_card(
            [
                {"subdir": "dc", "files": ["a.bin"], "granularity": "shared-card", "role": "battery"},
                {
                    "root": "savefile_directory",
                    "files": ["b.srm"],
                    "granularity": "per-game-file",
                    "role": "battery",
                },
            ],
            root="system_directory",
        )
        with pytest.raises(ValueError, match="established only from"):
            load_oddities(text)

    @pytest.mark.parametrize(
        "extra",
        [
            {"files": None, "unnamed": "why"},
            {"observe": ["c.bin"]},
            {"files_without_save_id": ["d.bin"]},
        ],
        ids=["unnamed", "observe", "id-less-scope"],
    )
    def test_the_home_directory_machinery_is_refused_on_a_cross_group(self, extra):
        group = {
            "root": "system_directory",
            "files": ["<save_id>.bin"] if "files_without_save_id" in extra else ["flash.bin"],
            "granularity": "shared-card",
            "role": "battery",
        }
        group.update(extra)
        if group["files"] is None:
            del group["files"]
        text = self._cross_card([self._HOME_GROUP, group])
        with pytest.raises(ValueError, match="cross-root group"):
            load_oddities(text)

    def test_the_id_less_alternative_needs_an_id_keyed_set_to_be_the_alternative_to(self):
        text = self._mode_card(
            {
                "granularity": "per-game-file",
                "files": ["<rom_stem>.srm"],
                "files_without_save_id": ["<rom_stem>.A1.bin"],
            }
        )
        with pytest.raises(ValueError, match="files_without_save_id"):
            load_oddities(text)

    def test_the_id_less_alternative_cannot_name_an_id(self):
        text = self._mode_card(
            {
                "granularity": "per-game-file",
                "files": ["<save_id>.A1.bin"],
                "files_without_save_id": ["<save_id>.A1.bin"],
            }
        )
        with pytest.raises(ValueError, match="without an id"):
            load_oddities(text)

    def test_an_unknown_granularity_is_rejected(self):
        # It reaches the caller as the contractual Granularity.value, so a
        # misspelling would be stated as this machine's actual grouping.
        text = self._mode_card({"granularity": "per-gaem-file", "files": ["<rom_stem>.srm"]})
        with pytest.raises(ValueError, match="granularity"):
            load_oddities(text)

    def test_the_card_vocabularies_are_the_placement_s_own(self):
        # One definition per vocabulary, imported rather than respelled: a
        # second copy is how data and contract drift apart. Every value the
        # placement can carry loads from a card — the tests above cover the
        # other direction, that nothing else does.
        for granularity in GRANULARITIES:
            card = load_oddities(self._mode_card({"granularity": granularity, "files": ["a.srm"]}))[0]
            assert card.modes["always"].granularity == granularity
        for root in ROOT_KINDS:
            mode = {"root": root, "granularity": "shared-card", "files": ["a.srm"]}
            card = load_oddities(self._mode_card(mode))[0]
            assert card.modes["always"].root == root

    def test_unknown_mode_root_is_rejected(self):
        text = json.dumps(
            {
                "schema": 1,
                "cores": {
                    "x": {
                        "identifiers": {"library_name": ["X"]},
                        "saves": {"modes": {"always": {"root": "wherever", "granularity": "shared-card"}}},
                    }
                },
            }
        )
        with pytest.raises(ValueError, match="root"):
            load_oddities(text)

    @pytest.mark.parametrize(
        "modes",
        [
            {},
            {"enabled": _mode("shared-card")},
            {"always": _mode("shared-card"), "legacy": _mode("per-game-file")},
        ],
    )
    def test_a_card_that_governs_nothing_must_state_the_one_mode_it_applies(self, modes):
        """Nothing selects between modes when no option governs the card.

        The resolver takes ``always`` and only ``always`` there, so any other
        shape describes behaviour that can never be applied — and the answer
        came back with no rule card behind it and no caveat either, because
        from the resolver's side nothing had gone wrong. A card ships with the
        code, so that is a build mistake and it fails the load.
        """
        text = json.dumps(
            {
                "schema": 1,
                "cores": {
                    "x": {
                        "identifiers": {"library_name": ["X"]},
                        "saves": {"modes": modes},
                    }
                },
            }
        )
        with pytest.raises(ValueError, match="governing_option"):
            load_oddities(text)

    def test_the_same_modes_load_once_an_option_governs_them(self):
        # The refusal is about what can be selected, not about the modes.
        text = json.dumps(
            {
                "schema": 1,
                "cores": {
                    "x": {
                        "identifiers": {"library_name": ["X"]},
                        "saves": {
                            "governing_option": {"key": "x_storage", "default": "enabled"},
                            "modes": {"enabled": _mode("shared-card")},
                        },
                    }
                },
            }
        )
        assert load_oddities(text)[0].option_key == "x_storage"

    @pytest.mark.parametrize("record", [{}, {"core_library_version": "1dac369", "date": "2026-08-05"}])
    def test_a_verification_record_that_pins_no_arrangement_version_is_rejected(self, record):
        """A record with no ``version`` can never drift, so it verifies forever.

        The drift check hangs on that field: with it null, no machine can
        disagree with the record, and the entry reads as *verified here* on
        every machine while pinning nothing at all — worse than never verified,
        because it claims the opposite.
        """
        text = json.dumps(
            {
                "schema": 3,
                "cores": {
                    "x": {
                        "verdict": "card",
                        "per_game_capable": True,
                        "note": "source-verified",
                        "verified": {"retrodeck": record},
                    }
                },
            }
        )
        with pytest.raises(ValueError, match="version"):
            load_audit(text)

    def test_a_record_may_still_leave_the_core_version_unstated(self):
        # Plenty of cores report none, and the arrangement version already
        # bounds what was checked — this shape ships today.
        text = json.dumps(
            {
                "schema": 3,
                "cores": {
                    "x": {
                        "verdict": "card",
                        "per_game_capable": True,
                        "note": "source-verified",
                        "verified": {"retrodeck": {"version": "0.10.9b", "core_library_version": None}},
                    }
                },
            }
        )
        record = load_audit(text)["x"].verified["retrodeck"]
        assert record is not None
        assert record.core_library_version is None


class TestAGoverningRule:
    """A card whose mode is selected by code: marker in data, decision in the rule."""

    def _rule_card(self, key: str, saves: dict[str, object]) -> str:
        return json.dumps(
            {
                "schema": 1,
                "cores": {key: {"identifiers": {"library_name": ["X"]}, "saves": saves}},
            }
        )

    _MODES = {
        "one": {
            "root": "savefile_directory",
            "groups": [{"files": ["a.srm"], "granularity": "per-game-file", "role": "battery"}],
        },
        "two": {
            "root": "savefile_directory",
            "groups": [{"files": ["b.srm"], "granularity": "shared-file", "role": "battery"}],
        },
    }

    def test_a_rule_card_loads_with_freely_named_modes(self):
        # The key must name a registered rule, so the fixture borrows a real
        # one — the modes here are synthetic and never applied.
        card = load_oddities(
            self._rule_card(
                "mednafen_saturn",
                {"governing_rule": {"options": ["opt_a", "opt_b"]}, "modes": self._MODES},
            )
        )[0]
        assert card.rule_options == ("opt_a", "opt_b")
        assert card.option_key is None
        assert sorted(card.modes) == ["one", "two"]

    def test_a_rule_and_a_governing_option_together_are_refused(self):
        text = self._rule_card(
            "mednafen_saturn",
            {
                "governing_option": {"key": "opt_a"},
                "governing_rule": {"options": ["opt_a"]},
                "modes": self._MODES,
            },
        )
        with pytest.raises(ValueError, match="one of them"):
            load_oddities(text)

    def test_a_rule_marker_without_a_registered_rule_is_refused(self):
        text = self._rule_card(
            "nonesuch", {"governing_rule": {"options": ["opt_a"]}, "modes": self._MODES}
        )
        with pytest.raises(ValueError, match="no selection rule is registered"):
            load_oddities(text)

    @pytest.mark.parametrize("block", [{"options": ["a"], "stray": 1}, {}, {"options": "a"}])
    def test_a_malformed_rule_block_is_refused(self, block):
        text = self._rule_card("mednafen_saturn", {"governing_rule": block, "modes": self._MODES})
        with pytest.raises(ValueError, match="governing_rule"):
            load_oddities(text)

    def test_every_registered_rule_has_a_shipped_card(self):
        # The loader holds the marker→rule direction; this is the mirror — a
        # rule with no card would be code describing nothing.
        carded = {card.key for card in CARDS if card.rule_options is not None}
        assert set(MODE_RULES) == carded, (
            f"rules registered for {sorted(MODE_RULES)} and rule cards shipped for {sorted(carded)}"
            " — the two ship together or not at all"
        )

    def test_the_rule_options_are_recorded_vocabulary(self):
        # The anchor tripwire covers them like a governing option's key: a
        # caller reads them back as this core's own words.
        words = recorded_vocabulary(option_key=None, modes={}, rule_options=("opt_a",))
        assert "opt_a" in words


class TestCardEvidence:
    """What each mode of a card rests on — stated per mode, never flattened."""

    def _raw_cards(self):
        text = (
            importlib.resources.files("atlas")
            .joinpath("data", "core_oddities.json")
            .read_text(encoding="utf-8")
        )
        return json.loads(text)["cores"]

    def test_every_mode_carries_its_own_provenance_status(self):
        for key, entry in self._raw_cards().items():
            modes = set(entry["saves"]["modes"])
            stated = set(entry["provenance"]["status"])
            assert stated == modes, (
                f"card {key!r}: provenance.status covers {sorted(stated)} but the card has "
                f"modes {sorted(modes)} — every mode says what its placement rests on"
            )

    def test_the_derived_flycast_mode_is_not_stated_as_observed(self):
        # 'All VMUs' was run on a real machine; 'VMU A1' follows from the same
        # code path and was never exercised. One status per mode is what keeps
        # a derivation from being laundered into an observation.
        status = self._raw_cards()["flycast"]["provenance"]["status"]
        assert status["All VMUs"].startswith("[V-live]")
        assert status["VMU A1"].startswith("[D]")

    def test_no_shipped_mode_claims_a_complete_file_set(self):
        """The premise three documents state — pinned so it cannot rot silently.

        ``FileSet.complete`` is documented as reserved: no card can yet
        establish which files a core writes *at all* for the active mode, so
        every answer carries ``False``. The day a card's evidence earns the
        claim, this test is what says so — and what to do about it. Deleting
        this test is then part of the work, not a nuisance.
        """
        claiming = sorted(
            f"{key}.{value}"
            for key, entry in self._raw_cards().items()
            for value, mode in entry["saves"]["modes"].items()
            if mode.get("complete")
        )
        assert claiming == [], (
            f"{claiming} now claims a complete file set, which retires the 'reserved' wording in "
            "atlas/placement.py (FileSet), docs/how-to-use.md ('Reading the file set') and "
            "vectors/README.md — update all three, then delete this test"
        )


class TestVerificationMatrix:
    def test_every_card_has_an_audit_entry(self):
        # Maintenance is enforced: a new card without a verification entry fails here.
        audit = load_audit()
        for card in load_oddities():
            assert card.key in audit, (
                f"rule card {card.key!r} has no entry in atlas/data/core_audit.json — "
                "add its verification record (see the file's spec)"
            )

    def test_matching_versions_carry_no_staleness_caveat(self):
        rd = _retrodeck(
            {
                RETRODECK_JSON: '{"version": "0.10.9b", "paths": {"rd_home_path": "/mnt/sd/retrodeck", "saves_path": "/mnt/sd/retrodeck/saves"}}',
                RETRODECK_CFG: CFG,
                "/mnt/sd/retrodeck/roms/dreamcast/Game.gdi": "",
            },
            cores={f"{DEPLOY}/flycast_libretro.so": {"library_name": "Flycast", "library_version": "1dac369"}},
        )
        p = placed(rd.savefile_location(content_path="/mnt/sd/retrodeck/roms/dreamcast/Game.gdi", core_so="flycast_libretro.so"))
        assert not any(c.code == atlas.CAVEAT_UNVERIFIED_VERSION for c in p.caveats)

    def test_arrangement_version_drift_fires_caveat(self):
        rd = _retrodeck(
            {
                RETRODECK_JSON: '{"version": "0.11.0", "paths": {"rd_home_path": "/mnt/sd/retrodeck", "saves_path": "/mnt/sd/retrodeck/saves"}}',
                RETRODECK_CFG: CFG,
                "/mnt/sd/retrodeck/roms/dreamcast/Game.gdi": "",
            },
            cores={f"{DEPLOY}/flycast_libretro.so": {"library_name": "Flycast"}},
        )
        p = placed(rd.savefile_location(content_path="/mnt/sd/retrodeck/roms/dreamcast/Game.gdi", core_so="flycast_libretro.so"))
        stale = [c for c in p.caveats if c.code == atlas.CAVEAT_UNVERIFIED_VERSION]
        assert stale
        assert stale[0].data["arrangement_live"] == "0.11.0"
        assert stale[0].data["arrangement_verified"] == "0.10.9b"

    def test_core_version_drift_fires_caveat(self):
        rd = _retrodeck(
            {
                RETRODECK_JSON: '{"version": "0.10.9b", "paths": {"rd_home_path": "/mnt/sd/retrodeck", "saves_path": "/mnt/sd/retrodeck/saves"}}',
                RETRODECK_CFG: CFG,
                "/mnt/sd/retrodeck/roms/dreamcast/Game.gdi": "",
            },
            cores={f"{DEPLOY}/flycast_libretro.so": {"library_name": "Flycast", "library_version": "fffffff"}},
        )
        p = placed(rd.savefile_location(content_path="/mnt/sd/retrodeck/roms/dreamcast/Game.gdi", core_so="flycast_libretro.so"))
        stale = [c for c in p.caveats if c.code == atlas.CAVEAT_UNVERIFIED_VERSION]
        assert stale
        assert stale[0].data["core_live"] == "fffffff"

    def test_unknown_live_versions_fail_closed(self):
        # The card is pinned to retrodeck 0.10.9b + core 1dac369, but this
        # machine exposes neither — missing evidence is not verification
        # (REVIEW M3).
        rd = _retrodeck(
            {
                RETRODECK_JSON: RD_JSON,  # no "version" key
                RETRODECK_CFG: CFG,
                "/mnt/sd/retrodeck/roms/dreamcast/Game.gdi": "",
            },
            cores={f"{DEPLOY}/flycast_libretro.so": {"library_name": "Flycast"}},  # no library_version
        )
        p = placed(rd.savefile_location(content_path="/mnt/sd/retrodeck/roms/dreamcast/Game.gdi", core_so="flycast_libretro.so"))
        stale = [c for c in p.caveats if c.code == atlas.CAVEAT_UNVERIFIED_VERSION]
        assert stale
        assert stale[0].data["verification"] == "runtime-version-unknown"
        assert "arrangement_version" in stale[0].data["missing"]
        assert "core_library_version" in stale[0].data["missing"]

    def test_an_empty_arrangement_version_is_unknown_not_drifted(self):
        # RetroDECK's shipped default config carries "version": "" and the
        # first run fills it in, so a machine can present one. It names no
        # version, and reporting a drift from the pin to nothing would be an
        # alarm about a value nobody wrote — the same reading of the key the
        # arrangement-level comparison uses.
        rd = _retrodeck(
            {
                RETRODECK_JSON: '{"version": "", "paths": {"rd_home_path": "/mnt/sd/retrodeck", "saves_path": "/mnt/sd/retrodeck/saves"}}',
                RETRODECK_CFG: CFG,
                "/mnt/sd/retrodeck/roms/dreamcast/Game.gdi": "",
            },
            cores={f"{DEPLOY}/flycast_libretro.so": {"library_name": "Flycast", "library_version": "1dac369"}},
        )
        p = placed(rd.savefile_location(content_path="/mnt/sd/retrodeck/roms/dreamcast/Game.gdi", core_so="flycast_libretro.so"))
        stale = [c for c in p.caveats if c.code == atlas.CAVEAT_UNVERIFIED_VERSION]
        assert stale
        assert stale[0].data["verification"] == "runtime-version-unknown"
        assert stale[0].data["missing"] == "arrangement_version"

    def test_confirmed_verification_lands_in_provenance(self):
        rd = _retrodeck(
            {
                RETRODECK_JSON: '{"version": "0.10.9b", "paths": {"rd_home_path": "/mnt/sd/retrodeck", "saves_path": "/mnt/sd/retrodeck/saves"}}',
                RETRODECK_CFG: CFG,
                "/mnt/sd/retrodeck/roms/dreamcast/Game.gdi": "",
            },
            cores={f"{DEPLOY}/flycast_libretro.so": {"library_name": "Flycast", "library_version": "1dac369"}},
        )
        p = placed(rd.savefile_location(content_path="/mnt/sd/retrodeck/roms/dreamcast/Game.gdi", core_so="flycast_libretro.so"))
        assert any("verified on retrodeck 0.10.9b" in s for s in p.sources)

    def test_unverified_arrangement_fires_caveat(self):
        # The flycast card was never verified on EmuDeck — the answer says so.
        machine = FixtureMachine(
            {
                f"{HOME}/.config/EmuDeck/settings.sh": 'savesPath="$HOME/Emulation/saves"\nromsPath="$HOME/Emulation/roms"\n',
                f"{HOME}/.var/app/org.libretro.RetroArch/config/retroarch/retroarch.cfg": (
                    'savefile_directory = "/home/deck/Emulation/saves/retroarch/saves"\n'
                    'system_directory = "/home/deck/Emulation/bios"\n'
                    'libretro_directory = "/cores"\n'
                ),
                f"{HOME}/Emulation/saves/.keep": "",
                f"{HOME}/Emulation/roms/dreamcast/Game.gdi": "",
            },
            cores={"/cores/flycast_libretro.so": {"library_name": "Flycast"}},
        )
        ed = atlas.EmuDeck(HOME, machine)
        p = placed(ed.savefile_location(content_path=f"{HOME}/Emulation/roms/dreamcast/Game.gdi", core_so="flycast_libretro.so"))
        stale = [c for c in p.caveats if c.code == atlas.CAVEAT_UNVERIFIED_VERSION]
        assert stale
        assert stale[0].data["arrangement"] == "emudeck"


CARDS = load_oddities()


def _deployed_core_info(machine: RealMachine, card: CoreCard):
    """One card's deployed core, queried — ``None`` when it cannot be read."""
    return machine.query_core(str(DEPLOYED_CORES / card.so_name))


def _registered_governing_option(machine: RealMachine, card: CoreCard) -> CoreOption | None:
    """A card's governing option as the deployed core registers it — ``None`` when unread.

    Three ways to read nothing, none of them evidence against the card: the core
    is not deployed on this machine, the probe captured no registration at all
    (LRPS2 registers its options later than ``retro_set_environment``), or the
    deployed core registers other keys than the card's — which is the generation
    mismatch the resolver already answers with ``core-generation-mismatch``.
    """
    if card.option_key is None:
        return None
    info = _deployed_core_info(machine, card)
    if info is None or info.options is None:
        return None
    return info.options.get(card.option_key)


@pytest.fixture(scope="module")
def prober() -> RealMachine:
    """One machine for the whole module, so each core is probed once."""
    return RealMachine()


@pytest.fixture(scope="module")
def deployed_core_bytes() -> Mapping[str, bytes]:
    """Every deployed card core, read whole, once for the module.

    The anchor check is byte containment, which needs the file in memory; the
    three cards together are ~50 MB and read in well under a second. Reading
    them per parametrised case instead would multiply that by the anchor count,
    and shelling out to ``strings`` would put binutils in a test path of a
    library whose zero-dependency contract is the reason vendoring it is a
    directory copy.
    """
    if not DEPLOYED_CORES.is_dir():
        return {}
    return {
        card.key: (DEPLOYED_CORES / card.so_name).read_bytes()
        for card in CARDS
        if (DEPLOYED_CORES / card.so_name).is_file()
    }


class TestADefaultIsRecordedOnlyWhereTheCoreStatesNone:
    """``governing_option.default`` exists for one reason: nothing else states it.

    Where a core registers its options during ``retro_set_environment``, the
    probe reads the default straight off the shipped binary, and the resolver
    uses that live value. A card copy would then be a second, ageing one — and
    ageing is exactly what it did: recorded option values in this repository had
    drifted from the cores they described, one of them into a "version drift"
    invented from a live value the shipped core registers perfectly well. So the
    rule is not "keep the copy correct", it is "do not keep a copy".

    The measurement runs both ways. A card that records a default the core also
    registers fails, because the copy is redundant and will drift. A card that
    records none must be one whose core registers one — otherwise nothing states
    the value and the resolver can only answer ``core-option-value-unestablished``
    for a machine that has no options file.

    Skipped where the cores are not deployed: the Flatpak is not a build
    dependency and CI has no emulator installation. Where they *are* deployed
    the module must really measure something, which is the second test's job.
    """

    @pytest.mark.parametrize("card", CARDS, ids=[card.key for card in CARDS])
    def test_a_card_records_no_default_the_deployed_core_already_states(
        self, prober: RealMachine, card: CoreCard
    ):
        if not DEPLOYED_CORES.is_dir():
            pytest.skip(f"no cores are deployed at {DEPLOYED_CORES}")
        if card.option_key is None:
            # A card whose core governs its layout with no option at all: one
            # mode, always in force, so there is no value to select with and no
            # default to state. The rule below is about a value that decides
            # between modes, and this card has no such decision to get wrong.
            assert card.option_default is None, (
                f"card {card.key!r} governs nothing and still records a default — there is no "
                "option for it to be the default of"
            )
            return
        option = _registered_governing_option(prober, card)
        # What decides the invariant is whether a *default* was registered, not
        # whether the option was. A core can register the key and declare no
        # default with it, and then nothing states the value either — reading
        # the option's presence instead would invert the rule for exactly that
        # core and let every machine without an options file answer
        # core-option-value-unestablished with a green suite.
        registered_default = option.default if option is not None else None
        if registered_default is None:
            # Nothing the probe can read states a default (LRPS2 registers its
            # options after retro_set_environment), so the card's is the only
            # one there is — the case the field exists for.
            #
            # For a core that registers the key with a NULL default the resolver
            # answers core-option-value-unestablished, which is the conservative
            # reading: whether RetroArch's option manager then falls back to the
            # first declared value is an upstream fact nobody here has verified,
            # and unverified upstream behaviour is not encoded. Seen, deferred.
            assert card.option_default is not None, (
                f"card {card.key!r} records no default and the deployed core states none for "
                f"{card.option_key!r} either, so nothing on such a machine says which mode is "
                "in force — record the default the audit read, with the reason in provenance"
            )
            return
        assert card.option_default is None, (
            f"card {card.key!r} records {card.option_default!r} as the default of "
            f"{card.option_key!r} and the deployed core registers {registered_default!r} itself — "
            "a card records a default only where the core states none, and this copy can only "
            "drift away from the binary that answers"
        )

    def test_the_defaults_are_really_measured_where_the_cores_are_deployed(
        self, prober: RealMachine
    ):
        # A run that skipped every card looks exactly like a run that checked
        # them all. On a machine carrying the cores, at least one card has to
        # have been read from a binary — a probe that silently stopped working
        # would otherwise retire this whole measurement without a failure. The
        # test above never skips now (it answers for both outcomes), so what
        # this guards is the reading itself.
        if not DEPLOYED_CORES.is_dir():
            pytest.skip(f"no cores are deployed at {DEPLOYED_CORES}")
        measured = sorted(
            card.key for card in CARDS if _registered_governing_option(prober, card) is not None
        )
        assert measured, (
            f"cores are deployed at {DEPLOYED_CORES} and not one card's governing option was read "
            "from a binary — either every card is a generation behind what is installed, or the "
            "probe (atlas._core_probe) stopped capturing registrations"
        )


class TestTheModeKeysAreTheDeployedCoresOwn:
    """Each card's mode keys, measured against the value set the binary registers.

    A mode key is not a label this repository chose: it is one value of the
    governing option, spelled the way the core spells it, and the resolver looks
    the live value up in that map. A key that drifts by a character selects
    nothing, and the answer then reports a generation mismatch that never
    happened — so the set is measured, not proof-read, exactly as the default
    above is.

    Equality both ways is the claim. A card missing one of the core's values
    cannot answer for a machine that selected it; a card inventing a value
    describes behaviour no configuration can reach.
    """

    @pytest.mark.parametrize("card", CARDS, ids=[card.key for card in CARDS])
    def test_a_cards_modes_are_the_values_the_deployed_core_registers(
        self, prober: RealMachine, card: CoreCard
    ):
        if not DEPLOYED_CORES.is_dir():
            pytest.skip(f"no cores are deployed at {DEPLOYED_CORES}")
        if card.rule_options is not None:
            # A rule card's mode keys are atlas's own vocabulary — the rule
            # selects them, no option's value set spells them, so there is no
            # binary set to measure them against. What the binary *does*
            # answer for is the options the rule reads: every declared one
            # must be registered by the deployed core, or the card describes
            # switches this generation does not have.
            info = _deployed_core_info(prober, card)
            if info is None or info.options is None:
                pytest.skip(f"the deployed core for card {card.key!r} exposes no options to measure")
            missing = [key for key in card.rule_options if key not in info.options]
            assert missing == [], (
                f"card {card.key!r} declares rule options {list(card.rule_options)} and the "
                f"deployed core does not register {missing} — the binary is the machine, so the "
                "card is what changes"
            )
            return
        if card.option_key is None:
            # Nothing selects between modes on such a card, so its one mode is
            # not a value anything registers — there is no set to measure
            # against rather than a measurement that came back empty.
            assert sorted(card.modes) == [MODE_ALWAYS], (
                f"card {card.key!r} governs nothing and still states modes {sorted(card.modes)} — "
                f"the resolver takes {MODE_ALWAYS!r} and only that one"
            )
            return
        option = _registered_governing_option(prober, card)
        if option is None:
            pytest.skip(f"the deployed cores register no {card.option_key!r} for card {card.key!r}")
        assert sorted(card.modes) == sorted(option.values), (
            f"card {card.key!r} describes modes {sorted(card.modes)} and the deployed core "
            f"registers {sorted(option.values)} for {card.option_key!r} — the binary is the "
            "machine, so the card is what changes"
        )

    def test_the_mode_keys_are_really_measured_where_the_cores_are_deployed(
        self, prober: RealMachine
    ):
        # Same guard as the defaults have, for the same reason: an all-skip run
        # passes exactly like a run that measured every card, so a probe that
        # quietly stopped capturing registrations would retire this measurement
        # without a single failure.
        if not DEPLOYED_CORES.is_dir():
            pytest.skip(f"no cores are deployed at {DEPLOYED_CORES}")
        measured = sorted(
            card.key for card in CARDS if _registered_governing_option(prober, card) is not None
        )
        assert measured, (
            f"cores are deployed at {DEPLOYED_CORES} and not one card's mode keys were read from a "
            "binary — either every card is a generation behind what is installed, or the probe "
            "(atlas._core_probe) stopped capturing registrations"
        )


def _shipped_anchors() -> dict[str, dict[str, dict[str, str]]]:
    """The packaged cards' ``anchors`` blocks, raw.

    Anchors are audit machinery: the loader validates them and drops them, so
    nothing that reaches a caller can carry them. Reading the file here is how
    ``provenance.status`` is checked too.
    """
    text = (
        importlib.resources.files("atlas").joinpath("data", "core_oddities.json").read_text(encoding="utf-8")
    )
    return {key: entry["saves"].get("anchors", {}) for key, entry in json.loads(text)["cores"].items()}


# One (card, literal) pair per distinct byte string the auditor read — several
# recorded names share an anchor (Flycast's four VMU files are all one stem).
LITERAL_ANCHORS = sorted(
    {
        (key, anchor["literal"])
        for key, anchors in _shipped_anchors().items()
        for anchor in anchors.values()
        if "literal" in anchor
    }
)


class TestEveryRecordedNameIsAnchoredOrMarked:
    """No silent opt-out: each name a card states is protected, or says it is not.

    A card names files and subdirectories that atlas hands to callers as fact,
    and those names came from somewhere — a literal in the shipped binary, or a
    run-time composition nobody can pin to one. Recording which is which is the
    difference between a vocabulary that fails loudly when a build renames it
    and one that quietly goes on describing a core that no longer exists.

    This is a claim about the *shipped* cards, so it lives here rather than in
    the loader: a synthetic card built inside some other test would otherwise
    have to carry anchors merely to load.
    """

    def test_every_shipped_card_covers_its_whole_vocabulary(self):
        anchors = _shipped_anchors()
        for card in CARDS:
            recorded = recorded_vocabulary(option_key=card.option_key, modes=card.modes)
            unprotected = sorted(recorded - set(anchors.get(card.key, {})))
            assert unprotected == [], (
                f"card {card.key!r} states {unprotected} and saves.anchors says nothing about them "
                "— give each the literal it was read from, or mark it 'unprotected' (composed at "
                "run time) or 'arrangement' (the path is not the core's) with a reason"
            )

    def test_the_shipped_cards_really_record_something_to_anchor(self):
        # The check above is vacuous for a card that states no names at all, so
        # a schema change that stopped exposing them would read as a clean run.
        recording = [
            card.key
            for card in CARDS
            if recorded_vocabulary(option_key=card.option_key, modes=card.modes)
        ]
        assert recording, (
            "not one shipped card records an option key, a subdir or a file name — either the cards "
            "state nothing any more, or recorded_vocabulary stopped seeing what they state, and the "
            "coverage check above is passing on an empty set"
        )

    def test_the_names_no_binary_check_reaches_are_the_ones_already_reasoned_about(self):
        """Which names the tripwire does not watch — pinned, so the set cannot grow quietly.

        ``unprotected`` and ``arrangement`` are the two ways out of the byte
        check. Both are right where they stand: Flycast composes its per-content
        VMU names at run time, its console flash name is in the binary but as
        instruction immediates no NUL-delimited literal can pin, and LRPS2's
        ``pcsx2/`` segment is a path RetroDECK builds. Each is one more name the
        tripwire does not watch, so the list lives here and a new one arrives as
        a visible diff rather than a quiet opt-out.
        """
        marked = sorted(
            (key, name, kind)
            for key, anchors in _shipped_anchors().items()
            for name, anchor in anchors.items()
            for kind in anchor
            if kind != "literal"
        )
        assert marked == [
            # bsnes-jg names its files from the content's stem at run time;
            # what the binary carries whole is the *resource* name the emulator
            # asks its host for ("time.rtc"), not the file it gets back. Its
            # .srm is not its name at all — the frontend writes that half,
            # which is why no such literal is in its binary. bsnes and
            # bsnes_hd_beta no longer record an .rtc: their branch is reachable
            # only through the Super Game Boy subsystem, so the cards retired
            # the claim.
            ("bsnes-jg", "<rom_stem>.rtc", "unprotected"),
            ("bsnes-jg", "<rom_stem>.srm", "unprotected"),
            # Cannonball's three names are whole in its binary; the ".sav" its
            # writer appends is short enough for the compiler to fold, so each
            # anchor points at the base name the rename would have to touch.
            ("cannonball", "hiscores.sav", "unprotected"),
            ("cannonball", "hiscores_continuous.sav", "unprotected"),
            ("cannonball", "hiscores_timetrial.sav", "unprotected"),
            # DOSBox Pure's legacy spelling is composed by a string replace; the
            # '.pure.zip' literal beside it is what the byte check watches, and
            # the '*.sav' search pattern is the legacy name's own whole trace.
            ("dosbox_pure", "<rom_stem>.sav", "unprotected"),
            # EasyRPG's fifteen slot names come from one fmt format the
            # compiler pooled into a longer string — visible in the binary but
            # not NUL-delimited; the whole '.save' archive-redirect suffix
            # beside it is what the byte check can hold.
            ("easyrpg", "Save01.lsd", "unprotected"),
            ("easyrpg", "Save02.lsd", "unprotected"),
            ("easyrpg", "Save03.lsd", "unprotected"),
            ("easyrpg", "Save04.lsd", "unprotected"),
            ("easyrpg", "Save05.lsd", "unprotected"),
            ("easyrpg", "Save06.lsd", "unprotected"),
            ("easyrpg", "Save07.lsd", "unprotected"),
            ("easyrpg", "Save08.lsd", "unprotected"),
            ("easyrpg", "Save09.lsd", "unprotected"),
            ("easyrpg", "Save10.lsd", "unprotected"),
            ("easyrpg", "Save11.lsd", "unprotected"),
            ("easyrpg", "Save12.lsd", "unprotected"),
            ("easyrpg", "Save13.lsd", "unprotected"),
            ("easyrpg", "Save14.lsd", "unprotected"),
            ("easyrpg", "Save15.lsd", "unprotected"),
            # The FB Alpha family names both its files after the loaded driver at
            # run time. What each binary carries whole is the format that builds
            # them — "%s%c%s.fs" and "%s%c%s.hi" — which is what a rename of the
            # scheme would take with it, and a stronger thing to watch than the
            # extension alone.
            ("fbalpha2012", "<rom_stem>.fs", "unprotected"),
            ("fbalpha2012", "<rom_stem>.hi", "unprotected"),
            # The CPS-1 build's pair: the same run-time composition, with the
            # EEPROM's "%s%c%s.nv" format standing where its siblings' ".fs" is.
            ("fbalpha2012_cps1", "<rom_stem>.hi", "unprotected"),
            ("fbalpha2012_cps1", "<rom_stem>.nv", "unprotected"),
            ("fbalpha2012_cps2", "<rom_stem>.fs", "unprotected"),
            ("fbalpha2012_cps2", "<rom_stem>.hi", "unprotected"),
            ("fbalpha2012_cps3", "<rom_stem>.fs", "unprotected"),
            ("fbalpha2012_cps3", "<rom_stem>.hi", "unprotected"),
            ("fbalpha2012_neogeo", "<rom_stem>.fs", "unprotected"),
            ("flycast", "<rom_stem>.A1.bin", "unprotected"),
            ("flycast", "<rom_stem>.B1.bin", "unprotected"),
            ("flycast", "<rom_stem>.C1.bin", "unprotected"),
            ("flycast", "<rom_stem>.D1.bin", "unprotected"),
            ("flycast", "<save_id>.A1.bin", "unprotected"),
            ("flycast", "<save_id>.B1.bin", "unprotected"),
            ("flycast", "<save_id>.C1.bin", "unprotected"),
            ("flycast", "<save_id>.D1.bin", "unprotected"),
            ("flycast", "dc_nvmem.bin", "unprotected"),
            # gpgx's per-game system BRAM appends a four-byte suffix the
            # compiler tail-merges into the cart literals; its .srm — like
            # swanstation's and the beetle pair's — is RetroArch's own naming,
            # reached through the SRAM interface, which no core literal can
            # spell.
            ("genesis_plus_gx", "<rom_stem>.brm", "unprotected"),
            ("genesis_plus_gx", "<rom_stem>.srm", "unprotected"),
            # geolith joins a stem to one of four extensions from a table. Three
            # of the four are whole strings and the fourth, "brm", is three
            # characters a compiler stores as an immediate — its siblings are
            # what establish the table is in the binary at all.
            ("geolith", "<rom_stem>.brm", "unprotected"),
            ("geolith", "<rom_stem>.mcr", "unprotected"),
            ("geolith", "<rom_stem>.nv", "unprotected"),
            ("geolith", "<rom_stem>.srm", "unprotected"),
            # Kronos composes every file name at run time from whole path
            # formats — the segments and option key are literals, the names are
            # not. The .nv is doubly composed: format plus the ST-V romset name.
            ("kronos", "<rom_stem>-ext1M.ram", "unprotected"),
            ("kronos", "<rom_stem>-ext2M.ram", "unprotected"),
            ("kronos", "<rom_stem>-ext4M.ram", "unprotected"),
            ("kronos", "<rom_stem>-ext512K.ram", "unprotected"),
            ("kronos", "<rom_stem>.bcr", "unprotected"),
            ("kronos", "<rom_stem>.bkr", "unprotected"),
            ("kronos", "<rom_stem>.nv", "unprotected"),
            ("kronos", "<rom_stem>.ram", "unprotected"),
            # MAME builds every one of these at run time: the tree segments come
            # from one pooled static table, the directory between them is a
            # build-time define, and the file names are the running machine's
            # own basename. Each anchor says which whole string stands beside it.
            ("mame", "<rom_stem>", "unprotected"),
            ("mame", "<rom_stem>.cfg", "unprotected"),
            ("mame", "cfg", "unprotected"),
            ("mame", "diff", "unprotected"),
            ("mame", "mame", "unprotected"),
            # The two older MAME builds compose their names the same way, and
            # each anchor names the whole string that stands beside it. MAME
            # 2000 is the one with something to say: its three file names are
            # carried in the binary as the sprintf formats that build them
            # ("%s/%s.nv" and its siblings), which is a stronger thing to watch
            # than the extension alone, and its 'cfg' and 'mame2000' segments
            # were pooled into longer strings by the compiler.
            ("mame2000", "<rom_stem>.cfg", "unprotected"),
            ("mame2000", "<rom_stem>.hi", "unprotected"),
            ("mame2000", "<rom_stem>.nv", "unprotected"),
            ("mame2000", "cfg", "unprotected"),
            ("mame2000", "mame2000", "unprotected"),
            # The 2003 pair pools its short tree names hardest — 'hi' and 'cfg'
            # in the plain build, 'memcard' in both — so each of those anchors
            # names the longer whole string that would go with a rename: the
            # writer's own log line, the option key that moves the tree, or the
            # file name 'MEMCARD.%03d' itself.
            ("mame2003", "<rom_stem>.cfg", "unprotected"),
            ("mame2003", "<rom_stem>.hi", "unprotected"),
            ("mame2003", "<rom_stem>.nv", "unprotected"),
            ("mame2003", "cfg", "unprotected"),
            ("mame2003", "hi", "unprotected"),
            ("mame2003", "memcard", "unprotected"),
            ("mame2003_plus", "<rom_stem>.cfg", "unprotected"),
            ("mame2003_plus", "<rom_stem>.hi", "unprotected"),
            ("mame2003_plus", "<rom_stem>.nv", "unprotected"),
            ("mame2003_plus", "memcard", "unprotected"),
            ("mame2010", "<rom_stem>.cfg", "unprotected"),
            ("mame2010", "<rom_stem>.hi", "unprotected"),
            ("mame2010", "<rom_stem>.nv", "unprotected"),
            # The beetle pair's slot-0 default fills the SRAM interface, so
            # the .srm is RetroArch's naming — same reasoning as gpgx's above.
            ("mednafen_psx", "<rom_stem>.srm", "unprotected"),
            ("mednafen_psx_hw", "<rom_stem>.srm", "unprotected"),
            ("melonds", "<rom_stem>.sav", "unprotected"),
            ("noods", "<rom_stem>.sav", "unprotected"),
            # NXEngine's five profile names come from one format the binary
            # carries whole, "profile%d.dat" — except slot 0's, which the source
            # writes as its own literal and the binary does not carry at all.
            ("nxengine", "profile.dat", "unprotected"),
            ("nxengine", "profile2.dat", "unprotected"),
            ("nxengine", "profile3.dat", "unprotected"),
            ("nxengine", "profile4.dat", "unprotected"),
            ("nxengine", "profile5.dat", "unprotected"),
            ("pcsx2", "pcsx2", "arrangement"),
            # PCSX-ReARMed's slot 1 fills the SRAM interface — the .srm is
            # RetroArch's naming, like the psx neighbours above.
            ("pcsx_rearmed", "<rom_stem>.srm", "unprotected"),
            # prboom's subdirectory is the placement's own template, and its
            # eight savegame names are composed from a base and a format the
            # binary carries whole ('prbmsav', '%s%c%s%d.dsg').
            ("prboom", "<rom_stem>", "unprotected"),
            ("prboom", "prbmsav0.dsg", "unprotected"),
            ("prboom", "prbmsav1.dsg", "unprotected"),
            ("prboom", "prbmsav2.dsg", "unprotected"),
            ("prboom", "prbmsav3.dsg", "unprotected"),
            ("prboom", "prbmsav4.dsg", "unprotected"),
            ("prboom", "prbmsav5.dsg", "unprotected"),
            ("prboom", "prbmsav6.dsg", "unprotected"),
            ("prboom", "prbmsav7.dsg", "unprotected"),
            # quasi88's diff is named after the opened disk image's stem at run
            # time, composed from the whole format '%s%c%s.srm'. The in-place
            # mode no longer records a name: the save is the content file
            # itself, which the card states as a fact rather than a file.
            ("quasi88", "<rom_stem>.srm", "unprotected"),
            # RACE's is the one name in this list the binary carries no trace of,
            # not even the extension: 'ngf' is three characters, which a compiler
            # stores as an immediate rather than as a string. Its anchor names the
            # build file that proves the unit is linked instead.
            ("race", "<rom_stem>.ngf", "unprotected"),
            # SwanStation's default slot-1 type fills the SRAM interface —
            # the .srm is RetroArch's naming, like the beetle pair's above.
            ("swanstation", "<rom_stem>.srm", "unprotected"),
            # TyrQuake's subdirectory is the basename-of-the-content's-directory
            # template, and its twelve slot names are composed from the menu's
            # 'save s%i' command and the save command's defaulted '.sav'
            # extension; the binary carries the menu's scan format
            # '%s%cs%i.sav' whole, which is what a rename would take with it.
            ("tyrquake", "<content_dir_name>", "unprotected"),
            ("tyrquake", "s0.sav", "unprotected"),
            ("tyrquake", "s1.sav", "unprotected"),
            ("tyrquake", "s10.sav", "unprotected"),
            ("tyrquake", "s11.sav", "unprotected"),
            ("tyrquake", "s2.sav", "unprotected"),
            ("tyrquake", "s3.sav", "unprotected"),
            ("tyrquake", "s4.sav", "unprotected"),
            ("tyrquake", "s5.sav", "unprotected"),
            ("tyrquake", "s6.sav", "unprotected"),
            ("tyrquake", "s7.sav", "unprotected"),
            ("tyrquake", "s8.sav", "unprotected"),
            ("tyrquake", "s9.sav", "unprotected"),
            # The vitaquake2 family's subdirectory is the placement's other
            # template — the basename of the content's directory, composed at
            # load; the four builds share the one libretro unit.
            ("vitaquake2", "<content_dir_name>", "unprotected"),
            ("vitaquake2-rogue", "<content_dir_name>", "unprotected"),
            ("vitaquake2-xatrix", "<content_dir_name>", "unprotected"),
            ("vitaquake2-zaero", "<content_dir_name>", "unprotected"),
        ], (
            "the set of recorded names no anchor watches has changed — every entry here is a name "
            "the byte tripwire cannot reach, so confirm the new one really cannot be pinned to a "
            "literal before updating this list"
        )
        for key, anchors in _shipped_anchors().items():
            for name, anchor in anchors.items():
                ((kind, _),) = anchor.items()
                assert kind in ANCHOR_KINDS, f"card {key!r}, {name!r}: unknown anchor kind {kind!r}"


class TestTheAnchorsAreLiteralsInTheDeployedCore:
    """The tripwire itself: every anchor, re-read in the binary it came from.

    What it catches is a vocabulary rename — a build that stops spelling
    ``vmu_save_`` or ``Mcd%03u.ps2`` fails here instead of leaving the card
    describing names the core no longer writes. What it cannot catch is the
    grammar around a literal (that ``%s.ps2`` is still the *save* name and not
    something else), or a name no literal carries at all — those stay marked
    ``unprotected``, each with the reason that says what does stand behind it.

    Skipped where the cores are not deployed: the Flatpak is not a build
    dependency and CI has no emulator installation.
    """

    @pytest.mark.parametrize(
        ("key", "literal"), LITERAL_ANCHORS, ids=[f"{key}:{literal}" for key, literal in LITERAL_ANCHORS]
    )
    def test_an_anchor_is_a_whole_string_in_the_shipped_binary(
        self, deployed_core_bytes: Mapping[str, bytes], key: str, literal: str
    ):
        if key not in deployed_core_bytes:
            pytest.skip(f"no {key} core is deployed at {DEPLOYED_CORES}")
        # Whole NUL-delimited, not a substring: 'dc' occurs inside a thousand
        # unrelated strings, and the flycast binary really does carry '/dc' —
        # its texture-dump path, which has nothing to do with saves.
        needle = b"\x00" + literal.encode() + b"\x00"
        assert needle in deployed_core_bytes[key], (
            f"card {key!r} anchors a recorded name to {literal!r} and the deployed "
            f"{key}_libretro.so carries no such string — the core's vocabulary moved, so the card "
            "describes names it no longer writes; re-audit before trusting the placement"
        )

    def test_the_anchors_are_really_read_where_the_cores_are_deployed(
        self, deployed_core_bytes: Mapping[str, bytes]
    ):
        # The same all-skip guard the option measurements carry: a run that
        # checked nothing is indistinguishable from a clean one otherwise.
        if not DEPLOYED_CORES.is_dir():
            pytest.skip(f"no cores are deployed at {DEPLOYED_CORES}")
        checked = sorted({key for key, _ in LITERAL_ANCHORS if key in deployed_core_bytes})
        assert checked, (
            f"cores are deployed at {DEPLOYED_CORES} and not one anchor was read from a binary — "
            "either no shipped card anchors anything any more, or the cores moved out of this "
            "directory and the tripwire is silently checking nothing"
        )
