"""Tests for atlas.oddities — rule cards and their application in the resolver."""

from __future__ import annotations

import atlas
from atlas.oddities import lookup_card

HOME = "/home/deck"
RETRODECK_JSON = f"{HOME}/.var/app/net.retrodeck.retrodeck/config/retrodeck/retrodeck.json"
RETRODECK_CFG = f"{HOME}/.var/app/net.retrodeck.retrodeck/config/retroarch/retroarch.cfg"
OPTIONS_CFG = f"{HOME}/.var/app/net.retrodeck.retrodeck/config/retroarch/retroarch-core-options.cfg"
FLYCAST_GAME_OPT = f"{HOME}/.var/app/net.retrodeck.retrodeck/config/retroarch/config/Flycast/Shenmue (Europe).opt"

RD_JSON = '{"paths": {"rd_home_path": "/mnt/sd/retrodeck", "saves_path": "/mnt/sd/retrodeck/saves"}}'
CFG = (
    'savefile_directory = "/mnt/sd/retrodeck/saves"\n'
    'sort_savefiles_by_content_enable = "true"\n'
    'system_directory = "/mnt/sd/retrodeck/bios"\n'
    'global_core_options = "true"\n'
    'libretro_directory = "/app/cores"\n'
)
DEPLOY = "/var/lib/flatpak/app/net.retrodeck.retrodeck/current/active/files/cores"
ROM = "/mnt/sd/retrodeck/roms/dreamcast/Shenmue (Europe).gdi"


class TestCardLookup:
    def test_by_so_basename(self):
        card = lookup_card(so_basename="flycast_libretro.so", library_name=None)
        assert card is not None and card.key == "flycast"

    def test_by_library_name(self):
        card = lookup_card(so_basename=None, library_name="Flycast")
        assert card is not None and card.key == "flycast"

    def test_no_card_for_ordinary_core(self):
        assert lookup_card(so_basename="mgba_libretro.so", library_name="mGBA") is None

    def test_card_modes_and_default(self):
        card = lookup_card(so_basename="flycast_libretro.so", library_name=None)
        assert card is not None
        assert card.option_key == "reicast_per_content_vmus"
        assert card.option_default == "disabled"
        assert set(card.modes) == {"disabled", "VMU A1", "All VMUs"}
        assert card.modes["disabled"].files is not None
        assert card.modes["VMU A1"].files is None  # unverified, never guessed


def _retrodeck(files, **kwargs):
    machine = atlas.FixtureMachine(files, **kwargs)
    return atlas.RetroDeck(HOME, machine, files.get(RETRODECK_JSON))


def _flycast_query(files):
    rd = _retrodeck(files, cores={f"{DEPLOY}/flycast_libretro.so": {"library_name": "Flycast"}})
    return rd.save_location(content_path=ROM, core_so="flycast_libretro.so")


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
        assert p.granularity.value == "shared-card"
        assert ("VMU A1", "per-game-file") in p.granularity.alternatives
        assert p.granularity.options_file == OPTIONS_CFG

    def test_option_absent_uses_core_default(self):
        p = _flycast_query({RETRODECK_JSON: RD_JSON, RETRODECK_CFG: CFG})
        assert p.root_kind == atlas.ROOT_SYSTEM_DIRECTORY
        assert p.granularity is not None
        assert p.granularity.option_value == "disabled"
        assert "core default" in p.granularity.option_source

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

    def test_per_game_mode_switches_root_and_granularity(self):
        p = _flycast_query(
            {
                RETRODECK_JSON: RD_JSON,
                RETRODECK_CFG: CFG,
                OPTIONS_CFG: 'reicast_per_content_vmus = "VMU A1"\n',
            }
        )
        assert p.dir == "/mnt/sd/retrodeck/saves/dreamcast"
        assert p.root_kind == atlas.ROOT_SAVEFILE_DIRECTORY
        assert p.granularity is not None
        assert p.granularity.value == "per-game-file"
        assert p.file_set.state == "unknown"
        assert any(c.code == atlas.CAVEAT_FILENAMES_UNVERIFIED for c in p.caveats)

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
        assert p.granularity.option_value == "VMU A1"
        assert "Shenmue (Europe).opt" in p.granularity.option_source

    def test_unknown_option_value_falls_back_with_caveat(self):
        p = _flycast_query(
            {
                RETRODECK_JSON: RD_JSON,
                RETRODECK_CFG: CFG,
                OPTIONS_CFG: 'reicast_per_content_vmus = "something new"\n',
            }
        )
        assert p.root_kind == atlas.ROOT_SAVEFILE_DIRECTORY  # standard rule
        assert p.granularity is None
        assert any(c.code == atlas.CAVEAT_UNKNOWN_OPTION_VALUE and c.data["value"] == "something new" for c in p.caveats)

    def test_ordinary_core_has_no_granularity(self):
        rd = _retrodeck(
            {
                RETRODECK_JSON: RD_JSON,
                RETRODECK_CFG: CFG,
                "/mnt/sd/retrodeck/roms/gba/Game.zip": "",
            },
            cores={f"{DEPLOY}/mgba_libretro.so": {"library_name": "mGBA"}},
        )
        p = rd.save_location(content_path="/mnt/sd/retrodeck/roms/gba/Game.zip", core_so="mgba_libretro.so")
        assert p.granularity is None


class TestLRPS2Card:
    def test_lookup_by_so_and_library_name(self):
        assert lookup_card(so_basename="pcsx2_libretro.so", library_name=None).key == "lrps2"
        assert lookup_card(so_basename=None, library_name="LRPS2").key == "lrps2"

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
        p = rd.save_location(content_path="/mnt/sd/retrodeck/roms/ps2/Game.iso", core_so="pcsx2_libretro.so")
        assert p.dir == "/mnt/sd/retrodeck/bios/pcsx2/memcards"
        assert p.root_kind == atlas.ROOT_SYSTEM_DIRECTORY
        assert p.file_set.state == "declared"
        assert p.file_set.files == ("Mcd001.ps2", "Mcd002.ps2")
        g = p.granularity
        assert g is not None and g.value == "shared-card"
        assert g.option_key == "pcsx2_shared_memory_cards"
        assert g.option_value == "enabled"
        assert ("disabled", "per-game-file") in g.alternatives

    def test_per_content_mode_names_card_after_rom_stem(self):
        # main.cpp:2154-2166 + Pcsx2Config.cpp:995-997 — slot 1 = <rom_stem>.ps2
        # in the RetroArch save directory (standard resolution applies).
        rd = _retrodeck(
            {
                RETRODECK_JSON: RD_JSON,
                RETRODECK_CFG: CFG,
                OPTIONS_CFG: 'pcsx2_shared_memory_cards = "disabled"\n',
                "/mnt/sd/retrodeck/roms/ps2/Gran Turismo 4 (USA).iso": "",
            },
            cores={f"{DEPLOY}/pcsx2_libretro.so": {"library_name": "LRPS2"}},
        )
        p = rd.save_location(
            content_path="/mnt/sd/retrodeck/roms/ps2/Gran Turismo 4 (USA).iso", core_so="pcsx2_libretro.so"
        )
        assert p.dir == "/mnt/sd/retrodeck/saves/ps2"
        assert p.root_kind == atlas.ROOT_SAVEFILE_DIRECTORY
        assert p.file_set.state == "declared"
        assert p.file_set.files == ("Gran Turismo 4 (USA).ps2",)
        assert p.granularity is not None and p.granularity.value == "per-game-file"

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
        p = rd.save_location(content_path="/mnt/sd/retrodeck/roms/ps2/Game.iso", core_so="pcsx2_libretro.so")
        assert p.file_set.state == "observed"
        assert p.file_set.files == ("Mcd001.ps2",)
