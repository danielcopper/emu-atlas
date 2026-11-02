"""Where the libretro Dolphin core keeps its saves (#616).

The core keeps the standalone emulator's whole user tree below the directory
RetroArch hands it as the save directory (Boot.cpp:61-69 at libretro/dolphin
fd1aca3a), so its answer is the standard rule's redirected directory, then
``User``, then the standalone Dolphin's reading of the ``Dolphin.ini`` found
there. These tests drive it through a RetroDECK machine whose catalogue runs
the core for GameCube and Wii, the way a launcher asks it.
"""

from __future__ import annotations

import dataclasses
from typing import Any

import atlas
from atlas import installations
from atlas.installations import (
    _as_core_statement,  # pyright: ignore[reportPrivateUsage] - the respelling under test
    _dolphin_try_parse_int,  # pyright: ignore[reportPrivateUsage] - the slot parse both builds share
)
from atlas.machine import FixtureMachine
from atlas.oddities import VerifiedOn, lookup_audit
from atlas.placement import Caveat, ModPlacement, SavefilePlacement, TexturePlacement, Unresolved
from tests.answers import placed

HOME = "/home/deck"
RD = f"{HOME}/.var/app/net.retrodeck.retrodeck"
ES = (
    "/var/lib/flatpak/app/net.retrodeck.retrodeck/current/active/files/retrodeck/components/es-de"
    "/share/es-de/resources/systems/linux/es_systems.xml"
)
CORE = "/var/lib/flatpak/app/net.retrodeck.retrodeck/current/active/files/cores/dolphin_libretro.so"
SAVES = "/mnt/sd/retrodeck/saves"
BIOS = "/mnt/sd/retrodeck/bios"
ES_XML = """<?xml version="1.0"?>
<systemList>
  <system>
    <name>gc</name>
    <path>%ROMPATH%/gc</path>
    <extension>.rvz .RVZ</extension>
    <command label="Dolphin">%EMULATOR_RETROARCH% -L %CORE_RETROARCH%/dolphin_libretro.so %ROM%</command>
  </system>
  <system>
    <name>wii</name>
    <path>%ROMPATH%/wii</path>
    <extension>.rvz .RVZ</extension>
    <command label="Dolphin">%EMULATOR_RETROARCH% -L %CORE_RETROARCH%/dolphin_libretro.so %ROM%</command>
  </system>
</systemList>
"""
CFG = (
    f'savefile_directory = "{SAVES}"\n'
    'sort_savefiles_by_content_enable = "true"\n'
    'sort_savefiles_enable = "false"\n'
    f'system_directory = "{BIOS}"\n'
    'libretro_directory = "/app/cores"\n'
)


def _machine(
    system: str,
    *,
    files: dict[str, Any] | None = None,
    dirs: tuple[str, ...] = (),
    sorted_dir: bool = True,
    unlistable: tuple[str, ...] = (),
) -> FixtureMachine:
    return FixtureMachine(
        {
            f"{RD}/config/retrodeck/retrodeck.json": (
                '{"paths": {"rd_home_path": "/mnt/sd/retrodeck", "saves_path": "/mnt/sd/retrodeck/saves"}}'
            ),
            f"{RD}/config/retroarch/retroarch.cfg": CFG,
            ES: ES_XML,
            **(files or {}),
        },
        dirs=[SAVES, BIOS, f"{RD}/config/ROMs", *([f"{SAVES}/{system}"] if sorted_dir else []), *dirs],
        cores={CORE: {"library_name": "dolphin-emu", "library_version": "fd1aca3a"}},
        unlistable=list(unlistable),
    )


def _install(machine: FixtureMachine) -> Any:
    installs = atlas.detect(HOME, machine)
    assert installs
    assert installs[0].kind == "retrodeck"
    return installs[0]


def _entry_answer(system: str, *, content: bool = True, **machine: Any) -> SavefilePlacement | Unresolved:
    entry = _install(_machine(system, **machine)).emulators_for(system).entries[0]
    return entry.savefile_location(
        content_path=f"/mnt/sd/retrodeck/roms/{system}/Game (USA).rvz" if content else None
    )


def _codes(placement: SavefilePlacement) -> list[str]:
    return [c.code for c in placement.caveats]


def _of(placement: SavefilePlacement, code: str) -> list[Caveat]:
    return [c for c in placement.caveats if c.code == code]


def _ini(system: str) -> str:
    return f"{SAVES}/{system}/User/Config/Dolphin.ini"


class TestTheGameCubeTree:
    def test_a_gamecube_disc_keeps_its_gci_folders_below_the_sorted_save_directorys_user_tree(self):
        p = placed(_entry_answer("gc"))
        assert p.dir == f"{SAVES}/gc/User/GC/<region>/Card A"
        assert p.root_kind == "savefile_directory"
        assert p.needs == ("region", "save_id")
        assert [g.dir for g in p.file_set.groups] == [
            f"{SAVES}/gc/User/GC/{region}/Card A" for region in ("USA", "EUR", "JAP")
        ]
        assert all(g.files is None for g in p.file_set.groups)
        assert p.granularity is not None
        assert p.granularity.mode == "folder+none"

    def test_the_gci_folder_states_its_members_by_header_under_the_core(self):
        members = _of(placed(_entry_answer("gc")), "file-membership-by-header")
        assert len(members) == 3
        assert all(c.data["core"] == "dolphin" for c in members)
        assert all("token" not in c.data for c in members)
        assert [c.data["citation"] for c in members] == 3 * [
            "GCMemcardDirectory.cpp:193, FileSearch.h:13-15, FileSearch.cpp:32-63, :86-91, "
            "GCMemcardDirectory.cpp:210, GCMemcard.h:245-246, EXI_DeviceMemoryCard.cpp:177-183 "
            "at libretro/dolphin fd1aca3a"
        ]

    def test_no_dolphin_ini_yet_is_stated_as_the_defaults_governing(self):
        missing = _of(placed(_entry_answer("gc")), "emulator-config-missing")
        assert [dict(c.data) for c in missing] == [{"core": "dolphin", "config": _ini("gc")}]

    def test_a_raw_card_in_slot_a_is_one_file_per_region_in_user_gc(self):
        p = placed(_entry_answer("gc", files={_ini("gc"): "[Core]\nSlotA = 1\n"}))
        assert p.dir == f"{SAVES}/gc/User/GC"
        assert p.file_set.files == ("MemoryCardA.USA.raw", "MemoryCardA.EUR.raw", "MemoryCardA.JAP.raw")
        assert p.granularity is not None
        assert p.granularity.value == "shared-file"

    def test_a_binary_slot_value_takes_the_default_on_the_core(self):
        # The core links the plain strtoll, which stops at the b: SlotA = 0b1
        # does not parse, so slot A keeps its default GCI folder — where the
        # standalone builds, linking C23's, read the raw card.
        p = placed(_entry_answer("gc", files={_ini("gc"): "[Core]\nSlotA = 0b1\n"}))
        assert p.granularity is not None
        assert p.granularity.mode == "folder+none"
        assert _dolphin_try_parse_int("0b1", binary_prefix=False) is None
        assert _dolphin_try_parse_int("0b1") == 1

    def test_a_configured_folder_is_rewritten_to_the_modern_region_on_the_core(self):
        p = placed(
            _entry_answer("gc", files={_ini("gc"): '[Core]\nGCIFolderAPath = "/mnt/sd/cards/JPN"\n'})
        )
        assert [g.dir for g in p.file_set.groups] == [
            f"/mnt/sd/cards/{region}" for region in ("USA", "EUR", "JPN")
        ]

    def test_a_configured_raw_card_keeps_a_region_code_the_core_does_not_strip(self):
        p = placed(
            _entry_answer(
                "gc", files={_ini("gc"): '[Core]\nSlotA = 1\nMemcardAPath = "/mnt/sd/a.JPN.raw"\n'}
            )
        )
        assert p.file_set.files == ("a.JPN.USA.raw", "a.JPN.EUR.raw", "a.JPN.JAP.raw")

    def test_an_uninterpreted_slot_device_is_refused_naming_the_core(self):
        refusal = _entry_answer("gc", files={_ini("gc"): "[Core]\nSlotA = 3\n"})
        assert isinstance(refusal, Unresolved)
        assert refusal.code == "slot-device-uninterpreted"
        assert dict(refusal.data) == {"core": "dolphin", "slot": "A", "value": "3", "config": _ini("gc")}

    def test_an_unreadable_dolphin_ini_is_refused_naming_the_core(self):
        refusal = _entry_answer("gc", files={_ini("gc"): {"status": "unreadable"}})
        assert isinstance(refusal, Unresolved)
        assert refusal.code == "emulator-config-unreadable"
        assert dict(refusal.data) == {"core": "dolphin", "config": _ini("gc")}


class TestTheWiiTree:
    def test_a_wii_disc_keeps_its_save_in_the_nand_below_user_wii(self):
        p = placed(_entry_answer("wii"))
        assert p.dir == f"{SAVES}/wii/User/Wii/title/<save_id>/data"
        assert p.root_kind == "savefile_directory"
        assert p.needs == ("save_id",)
        names = _of(p, "file-names-unestablished")
        assert [c.data["save_id_spelling"] for c in names] == ["title-id-words"]

    def test_the_titles_already_in_the_nand_are_listed(self):
        p = placed(_entry_answer("wii", dirs=(f"{SAVES}/wii/User/Wii/title/00010000/41424344/data",)))
        [listing] = _of(p, "save-id-candidates")
        assert listing.data["titles"] == ("00010000/41424344",)
        assert listing.data["core"] == "dolphin"

    def test_an_empty_nand_lists_nothing(self):
        assert "save-id-candidates" not in _codes(placed(_entry_answer("wii")))

    def test_nandrootpath_moves_the_nand(self):
        p = placed(
            _entry_answer(
                "wii",
                files={_ini("wii"): '[General]\nNANDRootPath = "/mnt/sd/nand"\n'},
                dirs=("/mnt/sd/nand",),
            )
        )
        assert p.dir == "/mnt/sd/nand/title/<save_id>/data"


class TestWhatTheAnswerStates:
    def test_the_card_rides_never_verified_and_no_core_suspect(self):
        for system in ("gc", "wii"):
            p = placed(_entry_answer(system))
            assert "core-suspect" not in _codes(p)
            [never] = _of(p, "unverified-version")
            assert dict(never.data) == {
                "core": "dolphin",
                "arrangement": "retrodeck",
                "verification": "never-verified",
            }

    def test_the_answer_names_no_srm(self):
        for system, ini in (("gc", ""), ("gc", "[Core]\nSlotA = 1\n"), ("wii", "")):
            p = placed(_entry_answer(system, files={_ini(system): ini} if ini else None))
            named = [*p.file_set.files, *(f for g in p.file_set.groups for f in g.files or ())]
            assert not [name for name in named if name.endswith(".srm")]

    def test_every_statement_names_the_core_and_none_a_card_token(self):
        for system in ("gc", "wii"):
            p = placed(_entry_answer(system))
            assert not [c.code for c in p.caveats if "token" in c.data]

    def test_a_sorted_directory_still_to_be_created_maps_the_fallback_below_the_root(self):
        p = placed(_entry_answer("gc", sorted_dir=False))
        assert "sorted-dir-missing" in _codes(p)
        assert p.dir == f"{SAVES}/gc/User/GC/<region>/Card A"
        assert p.fallback_dir == f"{SAVES}/User/GC/<region>/Card A"

    def test_a_save_directory_the_launch_completes_reads_no_ini(self):
        p = placed(_entry_answer("wii", content=False))
        assert p.needs == ("content_dir", "save_id")
        [unread] = _of(p, "emulator-config-unread")
        assert unread.data["config"] == f"{SAVES}/<content_dir>/User/Config/Dolphin.ini"
        [layer] = _of(p, "per-game-layer-unread")
        assert layer.data["dir"] == f"{SAVES}/<content_dir>/User/GameSettings"

    def test_a_save_directory_the_launch_completes_is_neither_walked_nor_listed(self):
        # The template is a spelling, not a directory RetroArch writes: even
        # where a path of that spelling exists, nothing below it is looked at.
        p = placed(
            _entry_answer(
                "wii", content=False, dirs=(f"{SAVES}/<content_dir>/User/Wii/title/00010000/41424344/data",)
            )
        )
        assert "save-id-candidates" not in _codes(p)
        assert p.physical_dir is None

    def test_both_game_settings_layers_are_counted(self):
        p = placed(
            _entry_answer(
                "gc",
                files={
                    f"{SAVES}/gc/User/GameSettings/GABC01.ini": "[Core]\nSlotA = 1\n",
                    f"{BIOS}/dolphin-emu/Sys/GameSettings/GABC.ini": "[Core]\n",
                    f"{BIOS}/dolphin-emu/Sys/GameSettings/GABD.ini": "[Core]\n",
                },
            )
        )
        assert [(c.data["dir"], c.data["count"]) for c in _of(p, "per-game-overrides-present")] == [
            (f"{SAVES}/gc/User/GameSettings", "1"),
            (f"{BIOS}/dolphin-emu/Sys/GameSettings", "2"),
        ]
        assert "per-game-build-layer-unread" not in _codes(p)

    def test_an_unlistable_user_layer_is_stated_unread(self):
        p = placed(_entry_answer("gc", unlistable=(f"{SAVES}/gc/User/GameSettings",)))
        assert [c.data["dir"] for c in _of(p, "per-game-layer-unread")] == [f"{SAVES}/gc/User/GameSettings"]


class TestTheRuleNeedsTheConsole:
    def _direct(self, system: str | None) -> SavefilePlacement:
        return placed(
            _install(_machine("gc")).savefile_location(
                core_so="dolphin_libretro.so",
                content_path="/mnt/sd/retrodeck/roms/gc/Game (USA).rvz",
                system=system,
            )
        )

    def test_no_system_leaves_the_rule_undecided_and_the_standard_answer_stands(self):
        p = self._direct(None)
        [undecided] = _of(p, "core-mode-unestablished")
        assert dict(undecided.data) == {"core": "dolphin", "reason": "content-class-unnamed"}
        assert p.dir == f"{SAVES}/gc"
        assert "core-suspect" not in _codes(p)

    def test_a_system_neither_console_is_unrecorded_and_named(self):
        [undecided] = _of(self._direct("n64"), "core-mode-unestablished")
        assert dict(undecided.data) == {
            "core": "dolphin",
            "reason": "content-class-unrecorded",
            "system": "n64",
        }

    def test_the_direct_route_with_a_system_answers_the_tree(self):
        assert self._direct("gc").dir == f"{SAVES}/gc/User/GC/<region>/Card A"


class TestTheRespelling:
    def test_the_token_becomes_the_core_and_every_other_value_stays(self):
        respelled = _as_core_statement(
            Caveat("save-writes-discarded", "m", {"token": "x", "mode": "none+none"}), "dolphin"
        )
        assert dict(respelled.data) == {"core": "dolphin", "mode": "none+none"}
        assert respelled.code == "save-writes-discarded"
        assert respelled.message == "m"

    def test_a_statement_without_a_token_is_left_as_it_is(self):
        caveat = Caveat("sorted-dir-missing", "m", {"dir": "/a", "fallback_dir": "/b"})
        assert _as_core_statement(caveat, "dolphin") is caveat


class TestTheContentTreesShareTheUserTree:
    """#616 D2: the core's texture and mod trees hang off the same user tree as its saves."""

    CONTENT = "/mnt/sd/retrodeck/roms/gc/Game (USA).rvz"

    def _textures(self) -> TexturePlacement:
        answer = _install(_machine("gc")).texture_pack_location(
            content_path=self.CONTENT, core_so="dolphin_libretro.so"
        )
        assert isinstance(answer, TexturePlacement)
        return answer

    def _mods(self) -> ModPlacement:
        answer = _install(_machine("gc")).mod_location(content_path=self.CONTENT, core_so="dolphin_libretro.so")
        assert isinstance(answer, ModPlacement)
        return answer

    def test_the_cores_textures_hang_off_the_sorted_save_directorys_user_tree(self):
        assert self._textures().dir == f"{SAVES}/gc/User/Load/Textures"

    def test_the_cores_mods_and_their_ini_hang_off_the_same_tree(self):
        mods = self._mods()
        assert [tree.dir for tree in mods.trees] == [f"{SAVES}/gc/User/Load/GraphicMods"]
        [unread] = [c for c in mods.caveats if c.code == "emulator-config-unread"]
        assert unread.data["config"] == f"{SAVES}/gc/User/Config/GFX.ini"

    def test_the_open_root_caveat_stays_beside_the_card_verdict(self):
        for answer in (self._textures(), self._mods()):
            [hedge] = [c for c in answer.caveats if c.code == "emulator-read-unestablished"]
            assert dict(hedge.data) == {"core": "dolphin", "verdict": "card"}
            assert "saves/dolphin-emu/User/Load" in hedge.message

    def test_a_record_verified_on_an_arrangement_retires_the_open_root_caveat(self, monkeypatch: Any):
        entry = lookup_audit("dolphin")
        assert entry is not None
        verified = dataclasses.replace(
            entry, verified={**entry.verified, "retrodeck": VerifiedOn("0.10.10b", "fd1aca3a", None)}
        )
        monkeypatch.setattr(installations, "lookup_audit", lambda key: verified if key == "dolphin" else None)
        assert installations._read_unestablished_caveat("dolphin") is None  # pyright: ignore[reportPrivateUsage]
