"""Tests for atlas.installations — handles, health, and the live resolver."""

from __future__ import annotations

import atlas

HOME = "/home/deck"
RETRODECK_JSON = f"{HOME}/.var/app/net.retrodeck.retrodeck/config/retrodeck/retrodeck.json"
RETRODECK_CFG = f"{HOME}/.var/app/net.retrodeck.retrodeck/config/retroarch/retroarch.cfg"
RETRODECK_OVERRIDES = f"{HOME}/.var/app/net.retrodeck.retrodeck/config/retroarch/config"
EMUDECK_SETTINGS = f"{HOME}/.config/EmuDeck/settings.sh"
STANDALONE_CFG = f"{HOME}/.var/app/org.libretro.RetroArch/config/retroarch/retroarch.cfg"

RD_JSON = '{"paths": {"rd_home_path": "/mnt/sd/retrodeck", "saves_path": "/mnt/sd/retrodeck/saves"}}'
RD_DEPLOY_CORES = "/var/lib/flatpak/app/net.retrodeck.retrodeck/current/active/files/cores"


def _retrodeck(files, **kwargs):
    machine = atlas.FixtureMachine(files, **kwargs)
    return atlas.RetroDeck(HOME, machine)


class TestRetroDeckPaths:
    def test_roots_from_json(self):
        rd = _retrodeck({RETRODECK_JSON: RD_JSON})
        assert rd.root() == "/mnt/sd/retrodeck"
        assert rd.saves_root() == "/mnt/sd/retrodeck/saves"

    def test_fallback_roots_when_json_lacks_paths(self):
        rd = _retrodeck({RETRODECK_JSON: "{}"})
        assert rd.root() == f"{HOME}/retrodeck"
        assert rd.bios_dir() == f"{HOME}/retrodeck/bios"


class TestRetroDeckSaveLocation:
    def test_cfg_is_the_truth_not_json(self):
        # The cfg is what RetroArch reads; a user-edited cfg wins over retrodeck.json.
        rd = _retrodeck(
            {
                RETRODECK_JSON: RD_JSON,
                RETRODECK_CFG: (
                    'savefile_directory = "/elsewhere/saves"\n'
                    'sort_savefiles_by_content_enable = "false"\nsort_savefiles_enable = "false"\n'
                ),
                "/elsewhere/saves/.keep": "",
                "/mnt/sd/retrodeck/roms/gba/Game.zip": "",
            }
        )
        p = rd.save_location(content_path="/mnt/sd/retrodeck/roms/gba/Game.zip")
        assert p.dir == "/elsewhere/saves"

    def test_missing_cfg_uses_platform_default(self):
        # Platform defaults are initialized before config load
        # (platform_unix.c:1844); upstream compile defaults sort by core.
        rd = _retrodeck(
            {RETRODECK_JSON: RD_JSON, "/mnt/sd/retrodeck/roms/gba/Game.zip": ""}
        )
        p = rd.save_location(content_path="/mnt/sd/retrodeck/roms/gba/Game.zip")
        assert p.dir == f"{HOME}/.var/app/net.retrodeck.retrodeck/config/retroarch/saves/<library_name>"
        assert p.root_kind == atlas.ROOT_SAVEFILE_DIRECTORY
        assert p.needs == ("library_name",)

    def test_core_override_applied(self):
        rd = _retrodeck(
            {
                RETRODECK_JSON: RD_JSON,
                RETRODECK_CFG: (
                    'savefile_directory = "/mnt/sd/retrodeck/saves"\n'
                    'sort_savefiles_by_content_enable = "true"\nsort_savefiles_enable = "false"\n'
                    'libretro_directory = "/app/cores"\n'
                ),
                f"{RETRODECK_OVERRIDES}/PPSSPP/PPSSPP.cfg": 'sort_savefiles_by_content_enable = "false"',
                "/mnt/sd/retrodeck/saves/.keep": "",
                "/mnt/sd/retrodeck/roms/psp/Game.iso": "",
            },
            cores={f"{RD_DEPLOY_CORES}/ppsspp_libretro.so": {"library_name": "PPSSPP"}},
        )
        p = rd.save_location(
            content_path="/mnt/sd/retrodeck/roms/psp/Game.iso", core_so="ppsspp_libretro.so"
        )
        assert p.dir == "/mnt/sd/retrodeck/saves"
        assert any("override wins" in s for s in p.sources)

    def test_library_name_from_binary_not_filename(self):
        rd = _retrodeck(
            {
                RETRODECK_JSON: RD_JSON,
                RETRODECK_CFG: (
                    'savefile_directory = "/mnt/sd/retrodeck/saves"\n'
                    'sort_savefiles_by_content_enable = "false"\n'
                    'sort_savefiles_enable = "true"\n'
                    'libretro_directory = "/app/cores"\n'
                ),
                "/mnt/sd/retrodeck/saves/.keep": "",
                "/mnt/sd/retrodeck/roms/gba/Game.zip": "",
            },
            cores={f"{RD_DEPLOY_CORES}/mgba_libretro.so": {"library_name": "mGBA"}},
        )
        p = rd.save_location(
            content_path="/mnt/sd/retrodeck/roms/gba/Game.zip", core_so="mgba_libretro.so"
        )
        assert p.dir == "/mnt/sd/retrodeck/saves/mGBA"

    def test_unqueryable_core_leaves_hole_and_caveat(self):
        rd = _retrodeck(
            {
                RETRODECK_JSON: RD_JSON,
                RETRODECK_CFG: (
                    'savefile_directory = "/mnt/sd/retrodeck/saves"\n'
                    'sort_savefiles_by_content_enable = "false"\n'
                    'sort_savefiles_enable = "true"\n'
                    'libretro_directory = "/app/cores"\n'
                ),
                "/mnt/sd/retrodeck/saves/.keep": "",
                "/mnt/sd/retrodeck/roms/apple2/game.dsk": "",
            },
            cores={f"{RD_DEPLOY_CORES}/applewin_libretro.so": None},
        )
        p = rd.save_location(
            content_path="/mnt/sd/retrodeck/roms/apple2/game.dsk", core_so="applewin_libretro.so"
        )
        assert p.dir == "/mnt/sd/retrodeck/saves/<library_name>"
        assert p.needs == ("library_name",)
        assert any(c.code == atlas.CAVEAT_CORE_UNQUERYABLE for c in p.caveats)

    def test_observed_file_set(self):
        rd = _retrodeck(
            {
                RETRODECK_JSON: RD_JSON,
                RETRODECK_CFG: (
                    'savefile_directory = "/mnt/sd/retrodeck/saves"\n'
                    'sort_savefiles_by_content_enable = "true"\nsort_savefiles_enable = "false"\n'
                ),
                "/mnt/sd/retrodeck/roms/n64/Paper Mario (USA).zip": "",
                "/mnt/sd/retrodeck/saves/n64/Paper Mario (USA).srm": "sram",
            }
        )
        p = rd.save_location(content_path="/mnt/sd/retrodeck/roms/n64/Paper Mario (USA).zip")
        assert p.file_set.state == "observed"
        assert p.file_set.files == ("Paper Mario (USA).srm",)

    def test_no_files_is_unknown_not_guessed(self):
        rd = _retrodeck(
            {
                RETRODECK_JSON: RD_JSON,
                RETRODECK_CFG: (
                    'savefile_directory = "/mnt/sd/retrodeck/saves"\n'
                    'sort_savefiles_by_content_enable = "true"\nsort_savefiles_enable = "false"\n'
                ),
                "/mnt/sd/retrodeck/saves/.keep": "",
                "/mnt/sd/retrodeck/roms/gba/Game.zip": "",
            }
        )
        p = rd.save_location(content_path="/mnt/sd/retrodeck/roms/gba/Game.zip")
        assert p.file_set.state == "unknown"
        assert p.file_set.files == ()

    def test_health_caveat_on_missing_root(self):
        rd = _retrodeck(
            {
                RETRODECK_JSON: '{"paths": {"rd_home_path": "/run/media/gone/retrodeck"}}',
            }
        )
        p = rd.save_location()
        assert any(
            c.code == atlas.CAVEAT_HEALTH and c.data["issue"] == atlas.HEALTH_ISSUE_ROOT_MISSING
            for c in p.caveats
        )

    def test_rom_stem_truncates_at_last_dot(self):
        # runloop.c:8710 — truncate at the last dot, but not a leading one.
        rd = _retrodeck(
            {
                RETRODECK_JSON: RD_JSON,
                RETRODECK_CFG: (
                    'savefile_directory = "/mnt/sd/retrodeck/saves"\n'
                    'sort_savefiles_by_content_enable = "false"\nsort_savefiles_enable = "false"\n'
                ),
                "/mnt/sd/retrodeck/roms/gb/Tetris (World) (Rev 1).zip": "",
                "/mnt/sd/retrodeck/saves/Tetris (World) (Rev 1).srm": "s",
                "/mnt/sd/retrodeck/saves/Tetris (World) (Rev 1).rtc": "r",
            }
        )
        p = rd.save_location(content_path="/mnt/sd/retrodeck/roms/gb/Tetris (World) (Rev 1).zip")
        assert p.file_set.files == ("Tetris (World) (Rev 1).rtc", "Tetris (World) (Rev 1).srm")


class TestConditionalPlacement:
    """H5: an absent sorted directory is a conditional result, not a fact."""

    CFG_SORT_CONTENT = (
        'savefile_directory = "/mnt/sd/retrodeck/saves"\n'
        'sort_savefiles_by_content_enable = "true"\nsort_savefiles_enable = "false"\n'
    )

    def test_existing_sorted_dir_is_unconditional(self):
        rd = _retrodeck(
            {
                RETRODECK_JSON: RD_JSON,
                RETRODECK_CFG: self.CFG_SORT_CONTENT,
                "/mnt/sd/retrodeck/roms/gba/Game.zip": "",
                "/mnt/sd/retrodeck/saves/gba/Game.srm": "s",
            }
        )
        p = rd.save_location(content_path="/mnt/sd/retrodeck/roms/gba/Game.zip")
        assert p.fallback_dir is None
        assert not any(c.code == atlas.CAVEAT_SORTED_DIR_MISSING for c in p.caveats)

    def test_missing_sorted_dir_carries_structural_fallback(self):
        rd = _retrodeck(
            {
                RETRODECK_JSON: RD_JSON,
                RETRODECK_CFG: self.CFG_SORT_CONTENT,
                "/mnt/sd/retrodeck/roms/gba/Game.zip": "",
                "/mnt/sd/retrodeck/saves/.keep": "",
            }
        )
        p = rd.save_location(content_path="/mnt/sd/retrodeck/roms/gba/Game.zip")
        assert p.dir == "/mnt/sd/retrodeck/saves/gba"
        assert p.fallback_dir == "/mnt/sd/retrodeck/saves"
        assert any(c.code == atlas.CAVEAT_SORTED_DIR_MISSING for c in p.caveats)

    def test_file_blocking_sorted_dir_makes_fallback_the_answer(self):
        # A file where the sorted dir should be: mkdir MUST fail, so the
        # fallback is not conditional — it is the known outcome.
        rd = _retrodeck(
            {
                RETRODECK_JSON: RD_JSON,
                RETRODECK_CFG: self.CFG_SORT_CONTENT,
                "/mnt/sd/retrodeck/roms/gba/Game.zip": "",
                "/mnt/sd/retrodeck/saves/gba": "i am a file, not a directory",
            }
        )
        p = rd.save_location(content_path="/mnt/sd/retrodeck/roms/gba/Game.zip")
        assert p.dir == "/mnt/sd/retrodeck/saves"
        assert p.fallback_dir is None
        assert any(c.code == atlas.CAVEAT_SORTED_DIR_UNCREATABLE for c in p.caveats)

    def test_content_dir_root_with_sorting_gets_fallback_too(self):
        # H6 established that sorting applies after content-root selection;
        # the conditional-creation rule applies there just the same.
        rd = _retrodeck(
            {
                RETRODECK_JSON: RD_JSON,
                RETRODECK_CFG: (
                    'savefiles_in_content_dir = "true"\n'
                    'sort_savefiles_by_content_enable = "true"\nsort_savefiles_enable = "false"\n'
                ),
                "/mnt/sd/retrodeck/roms/gba/Game.zip": "",
            },
            dirs=["/mnt/sd/retrodeck/saves"],
        )
        p = rd.save_location(content_path="/mnt/sd/retrodeck/roms/gba/Game.zip")
        assert p.dir == "/mnt/sd/retrodeck/roms/gba/gba"
        assert p.fallback_dir == "/mnt/sd/retrodeck/roms/gba"
        assert any(c.code == atlas.CAVEAT_SORTED_DIR_MISSING for c in p.caveats)


class TestLinkView:
    """M7: the emulator-side path and the physical path are both answers."""

    FLAT_CFG = (
        'savefile_directory = "/home/deck/links/saves"\n'
        'sort_savefiles_by_content_enable = "false"\nsort_savefiles_enable = "false"\n'
    )

    def test_symlinked_save_dir_reports_physical_dir(self):
        machine = atlas.FixtureMachine(
            {
                f"{HOME}/.config/retroarch/retroarch.cfg": self.FLAT_CFG,
                "/data/real-saves/Game.srm": "s",
                f"{HOME}/roms/gba/Game.zip": "",
            },
            symlinks={f"{HOME}/links/saves": "/data/real-saves"},
        )
        p = atlas.NativeRetroArch(HOME, machine).save_location(content_path=f"{HOME}/roms/gba/Game.zip")
        assert p.dir == f"{HOME}/links/saves"
        assert p.physical_dir == "/data/real-saves"

    def test_dead_link_in_card_directory_is_stated(self):
        # The LRPS2 dir_prep case: the memcards link points into an unmounted
        # volume — the emulator-side path is dead and the answer says so.
        machine = atlas.FixtureMachine(
            {
                RETRODECK_JSON: RD_JSON,
                RETRODECK_CFG: (
                    'savefile_directory = "/mnt/sd/retrodeck/saves"\n'
                    'system_directory = "/mnt/sd/retrodeck/bios"\n'
                    'libretro_directory = "/app/cores"\nsort_savefiles_enable = "false"\n'
                ),
                "/mnt/sd/retrodeck/roms/ps2/Game.iso": "",
                "/mnt/sd/retrodeck/saves/.keep": "",
            },
            symlinks={"/mnt/sd/retrodeck/bios/pcsx2/memcards": "/run/media/gone/saves/ps2/memcards"},
            cores={f"{RD_DEPLOY_CORES}/pcsx2_libretro.so": {"library_name": "LRPS2"}},
        )
        p = atlas.RetroDeck(HOME, machine).save_location(
            content_path="/mnt/sd/retrodeck/roms/ps2/Game.iso", core_so="pcsx2_libretro.so"
        )
        assert p.dir == "/mnt/sd/retrodeck/bios/pcsx2/memcards"
        assert p.physical_dir is None
        dead = [c for c in p.caveats if c.code == atlas.CAVEAT_DEAD_SYMLINK]
        assert dead and dead[0].data["link"] == "/mnt/sd/retrodeck/bios/pcsx2/memcards"

    def test_rejected_save_root_through_dead_link_says_why(self):
        machine = atlas.FixtureMachine(
            {
                f"{HOME}/.config/retroarch/retroarch.cfg": self.FLAT_CFG,
                f"{HOME}/roms/gba/Game.zip": "",
            },
            symlinks={f"{HOME}/links/saves": "/run/media/gone/saves"},
        )
        p = atlas.NativeRetroArch(HOME, machine).save_location(content_path=f"{HOME}/roms/gba/Game.zip")
        assert p.dir == f"{HOME}/.config/retroarch/saves"
        codes = [c.code for c in p.caveats]
        assert atlas.CAVEAT_INVALID_SAVE_DIRECTORY in codes
        assert atlas.CAVEAT_DEAD_SYMLINK in codes


class TestEmuDeck:
    def test_settings_parse_and_roots(self):
        machine = atlas.FixtureMachine(
            {
                EMUDECK_SETTINGS: 'romsPath="$HOME/Emulation/roms"\nsavesPath="$HOME/Emulation/saves"\n',
                f"{HOME}/Emulation/saves/.keep": "",
            }
        )
        ed = atlas.EmuDeck(HOME, machine)
        assert ed.root() == f"{HOME}/Emulation"
        assert ed.saves_root() == f"{HOME}/Emulation/saves"
        # No standalone RetroArch cfg in this fixture: the companion issue is
        # the only one — roots are fine.
        assert ed.health().codes == (atlas.HEALTH_ISSUE_COMPANION_CONFIG_MISSING,)

    def test_save_location_reads_standalone_cfg(self):
        machine = atlas.FixtureMachine(
            {
                EMUDECK_SETTINGS: 'savesPath="$HOME/Emulation/saves"\nromsPath="$HOME/Emulation/roms"\n',
                STANDALONE_CFG: (
                    'savefile_directory = "/home/deck/Emulation/saves/retroarch/saves"\n'
                    'sort_savefiles_by_content_enable = "false"\n'
                    'sort_savefiles_enable = "false"\n'
                ),
                f"{HOME}/Emulation/roms/gba/Game.zip": "",
                f"{HOME}/Emulation/saves/retroarch/saves/.keep": "",
            }
        )
        ed = atlas.EmuDeck(HOME, machine)
        p = ed.save_location(content_path=f"{HOME}/Emulation/roms/gba/Game.zip")
        assert p.dir == "/home/deck/Emulation/saves/retroarch/saves"
        assert p.root_kind == atlas.ROOT_SAVEFILE_DIRECTORY


class TestBareRetroArch:
    def test_native_upstream_default_sorts_by_core(self):
        # config.def.h:982 — upstream defaults to sort-by-core.
        machine = atlas.FixtureMachine(
            {
                f"{HOME}/.config/retroarch/retroarch.cfg": 'savefile_directory = "~/saves"\n',
                f"{HOME}/saves/.keep": "",
                f"{HOME}/roms/gba/Game.zip": "",
            }
        )
        inst = atlas.NativeRetroArch(HOME, machine)
        p = inst.save_location(content_path=f"{HOME}/roms/gba/Game.zip")
        assert p.dir == f"{HOME}/saves/<library_name>"
        assert p.needs == ("library_name",)
