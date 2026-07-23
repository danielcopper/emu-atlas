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
SAVES_KEEP = "/mnt/sd/retrodeck/saves/.keep"
CFG = (
    'savefile_directory = "/mnt/sd/retrodeck/saves"\n'
    'sort_savefiles_by_content_enable = "true"\nsort_savefiles_enable = "false"\n'
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
                SAVES_KEEP: "",
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
        assert p.granularity is not None and p.granularity.value == "shared-card"
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
        by_so = lookup_card(so_basename="pcsx2_libretro.so", library_name=None)
        by_name = lookup_card(so_basename=None, library_name="LRPS2")
        assert by_so is not None and by_so.key == "pcsx2"
        assert by_name is not None and by_name.key == "pcsx2"

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
                SAVES_KEEP: "",
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


class TestVerificationMatrix:
    def test_every_card_has_an_audit_entry(self):
        # Maintenance is enforced: a new card without a verification entry fails here.
        from atlas.oddities import load_audit, load_oddities

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
        p = rd.save_location(content_path="/mnt/sd/retrodeck/roms/dreamcast/Game.gdi", core_so="flycast_libretro.so")
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
        p = rd.save_location(content_path="/mnt/sd/retrodeck/roms/dreamcast/Game.gdi", core_so="flycast_libretro.so")
        stale = [c for c in p.caveats if c.code == atlas.CAVEAT_UNVERIFIED_VERSION]
        assert stale and stale[0].data["arrangement_live"] == "0.11.0"
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
        p = rd.save_location(content_path="/mnt/sd/retrodeck/roms/dreamcast/Game.gdi", core_so="flycast_libretro.so")
        stale = [c for c in p.caveats if c.code == atlas.CAVEAT_UNVERIFIED_VERSION]
        assert stale and stale[0].data["core_live"] == "fffffff"

    def test_unverified_arrangement_fires_caveat(self):
        # The flycast card was never verified on EmuDeck — the answer says so.
        machine = atlas.FixtureMachine(
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
        ed = atlas.EmuDeck(HOME, machine, machine.read_text(f"{HOME}/.config/EmuDeck/settings.sh").text or "")
        p = ed.save_location(content_path=f"{HOME}/Emulation/roms/dreamcast/Game.gdi", core_so="flycast_libretro.so")
        stale = [c for c in p.caveats if c.code == atlas.CAVEAT_UNVERIFIED_VERSION]
        assert stale and stale[0].data["arrangement"] == "emudeck"
