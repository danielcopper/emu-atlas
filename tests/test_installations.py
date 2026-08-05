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
        # (platform_unix.c:2133-2134); upstream compile defaults sort by core.
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

    def test_observation_is_literal_for_glob_metacharacters(self):
        # '[USA]' in a ROM name must match itself, never act as a class (M2).
        rd = _retrodeck(
            {
                RETRODECK_JSON: RD_JSON,
                RETRODECK_CFG: (
                    'savefile_directory = "/mnt/sd/retrodeck/saves"\n'
                    'sort_savefiles_by_content_enable = "false"\nsort_savefiles_enable = "false"\n'
                ),
                "/mnt/sd/retrodeck/roms/gba/Game [USA].zip": "",
                "/mnt/sd/retrodeck/saves/Game [USA].srm": "s",
                "/mnt/sd/retrodeck/saves/Game U.srm": "decoy",
            }
        )
        p = rd.save_location(content_path="/mnt/sd/retrodeck/roms/gba/Game [USA].zip")
        assert p.file_set.files == ("Game [USA].srm",)

    def test_disk_index_companion_is_filtered(self):
        # <stem>.ldci is RetroArch's disk-control index, not save data
        # (disk_index_file.c:201-249, file_path_special.h:83).
        rd = _retrodeck(
            {
                RETRODECK_JSON: RD_JSON,
                RETRODECK_CFG: (
                    'savefile_directory = "/mnt/sd/retrodeck/saves"\n'
                    'sort_savefiles_by_content_enable = "false"\nsort_savefiles_enable = "false"\n'
                ),
                "/mnt/sd/retrodeck/roms/psx/Game.chd": "",
                "/mnt/sd/retrodeck/saves/Game.srm": "s",
                "/mnt/sd/retrodeck/saves/Game.ldci": "{}",
            }
        )
        p = rd.save_location(content_path="/mnt/sd/retrodeck/roms/psx/Game.chd")
        assert p.file_set.files == ("Game.srm",)

    def test_observed_never_claims_completeness(self):
        rd = _retrodeck(
            {
                RETRODECK_JSON: RD_JSON,
                RETRODECK_CFG: (
                    'savefile_directory = "/mnt/sd/retrodeck/saves"\n'
                    'sort_savefiles_by_content_enable = "false"\nsort_savefiles_enable = "false"\n'
                ),
                "/mnt/sd/retrodeck/roms/gba/Game.zip": "",
                "/mnt/sd/retrodeck/saves/Game.srm": "s",
            }
        )
        p = rd.save_location(content_path="/mnt/sd/retrodeck/roms/gba/Game.zip")
        assert p.file_set.state == "observed"
        assert p.file_set.complete is False

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
        assert dead
        assert dead[0].data["link"] == "/mnt/sd/retrodeck/bios/pcsx2/memcards"

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


class TestFlatpakSandboxPaths:
    """A Flatpak app writes its config from inside its sandbox, so the paths in
    it are sandbox paths: the live RetroDECK cfg spells its override directory
    ``/var/config/retroarch/config``, which Flatpak binds to
    ``~/.var/app/<app id>/config/retroarch/config`` on the host."""

    SANDBOX_CFG_DIR = "/var/config/retroarch"
    SORTED_CFG = (
        'savefile_directory = "/mnt/sd/retrodeck/saves"\n'
        'sort_savefiles_by_content_enable = "true"\nsort_savefiles_enable = "false"\n'
        'libretro_directory = "/app/cores"\n'
    )

    def _psp_query(self, cfg_lines, files=None):
        rd = _retrodeck(
            {
                RETRODECK_JSON: RD_JSON,
                RETRODECK_CFG: cfg_lines,
                "/mnt/sd/retrodeck/saves/.keep": "",
                "/mnt/sd/retrodeck/roms/psp/Game.iso": "",
                **(files or {}),
            },
            cores={f"{RD_DEPLOY_CORES}/ppsspp_libretro.so": {"library_name": "PPSSPP"}},
        )
        return rd.save_location(
            content_path="/mnt/sd/retrodeck/roms/psp/Game.iso", core_so="ppsspp_libretro.so"
        )

    def test_override_dir_in_sandbox_spelling_reaches_the_host_override(self):
        # The stock RetroDECK override config/PPSSPP/PPSSPP.cfg flips the layout
        # flat; reached only if the sandbox spelling is translated.
        p = self._psp_query(
            self.SORTED_CFG + f'rgui_config_directory = "{self.SANDBOX_CFG_DIR}/config"\n',
            files={f"{RETRODECK_OVERRIDES}/PPSSPP/PPSSPP.cfg": 'sort_savefiles_by_content_enable = "false"'},
        )
        assert p.dir == "/mnt/sd/retrodeck/saves"

    def test_translation_names_both_spellings_in_the_sources(self):
        p = self._psp_query(self.SORTED_CFG + f'rgui_config_directory = "{self.SANDBOX_CFG_DIR}/config"\n')
        assert any(
            f'rgui_config_directory = "{self.SANDBOX_CFG_DIR}/config"' in s and RETRODECK_OVERRIDES in s
            for s in p.sources
        )

    def test_untranslatable_override_dir_is_stated_not_silent(self):
        # /var/db is the runtime's own filesystem inside the sandbox — no host
        # location exists, so the overrides there cannot be read from here.
        p = self._psp_query(self.SORTED_CFG + 'rgui_config_directory = "/var/db/retroarch/config"\n')
        assert [c.data for c in p.caveats if c.code == atlas.CAVEAT_SANDBOX_PATH_UNTRANSLATED] == [
            {"key": "rgui_config_directory", "path": "/var/db/retroarch/config"}
        ]

    def test_save_directory_in_sandbox_spelling_is_translated(self):
        p = self._psp_query(
            'savefile_directory = "/var/data/saves"\n'
            'sort_savefiles_by_content_enable = "false"\nsort_savefiles_enable = "false"\n',
            files={f"{HOME}/.var/app/net.retrodeck.retrodeck/data/saves/.keep": ""},
        )
        assert p.dir == f"{HOME}/.var/app/net.retrodeck.retrodeck/data/saves"

    def test_untranslatable_save_directory_keeps_the_emulators_own_path(self):
        # RetroArch's "not an existing directory" test happens inside the
        # sandbox; atlas cannot reproduce it, so it must not report its outcome.
        p = self._psp_query(
            'savefile_directory = "/run/user/1000/saves"\n'
            'sort_savefiles_by_content_enable = "false"\nsort_savefiles_enable = "false"\n'
            'libretro_directory = "/app/cores"\n'
        )
        assert p.dir == "/run/user/1000/saves"
        codes = [c.code for c in p.caveats]
        assert atlas.CAVEAT_SANDBOX_PATH_UNTRANSLATED in codes
        assert atlas.CAVEAT_INVALID_SAVE_DIRECTORY not in codes

    def test_host_shared_paths_pass_through_untranslated(self):
        # /run/media is the same directory inside and outside the sandbox.
        p = self._psp_query(
            'savefile_directory = "/run/media/deck/card/saves"\n'
            'sort_savefiles_by_content_enable = "false"\nsort_savefiles_enable = "false"\n',
            files={"/run/media/deck/card/saves/.keep": ""},
        )
        assert p.dir == "/run/media/deck/card/saves"

    def test_options_path_in_sandbox_spelling_governs_the_option(self):
        # The card's option is read from the file core_options_path names —
        # here in the sandbox's spelling of the app's own config directory.
        rd = _retrodeck(
            {
                RETRODECK_JSON: RD_JSON,
                RETRODECK_CFG: (
                    'savefile_directory = "/mnt/sd/retrodeck/saves"\n'
                    'sort_savefiles_by_content_enable = "false"\nsort_savefiles_enable = "false"\n'
                    'libretro_directory = "/app/cores"\nglobal_core_options = "true"\n'
                    f'core_options_path = "{self.SANDBOX_CFG_DIR}/opts.cfg"\n'
                ),
                f"{HOME}/.var/app/net.retrodeck.retrodeck/config/retroarch/opts.cfg": (
                    'opera_nvram_storage = "shared"'
                ),
                "/mnt/sd/retrodeck/saves/.keep": "",
                "/mnt/sd/retrodeck/roms/3do/Game.chd": "",
            },
            cores={f"{RD_DEPLOY_CORES}/opera_libretro.so": {"library_name": "Opera"}},
        )
        p = rd.save_location(
            content_path="/mnt/sd/retrodeck/roms/3do/Game.chd", core_so="opera_libretro.so"
        )
        assert p.granularity is not None
        assert p.granularity.option_value == "shared"

    def test_unreachable_root_is_not_observed_at_all(self):
        # The sorted directory, its fallback and the link view all come from
        # reads that cannot apply to a path atlas just declared unreadable.
        p = self._psp_query(
            'savefile_directory = "/run/user/1000/saves"\n'
            'sort_savefiles_by_content_enable = "true"\nsort_savefiles_enable = "false"\n'
            'libretro_directory = "/app/cores"\n'
        )
        assert p.dir == "/run/user/1000/saves/psp"  # RetroArch's path math still applies
        assert (p.fallback_dir, p.physical_dir, p.file_set.state) == (None, None, "unknown")
        assert atlas.CAVEAT_SORTED_DIR_MISSING not in [c.code for c in p.caveats]

    def test_untranslatable_options_path_is_silent_for_a_core_that_reads_none(self):
        # A core without a rule card consults no options file, so an
        # unreachable core_options_path is not a degradation of its answer.
        p = self._psp_query(
            'savefile_directory = "/mnt/sd/retrodeck/saves"\n'
            'sort_savefiles_by_content_enable = "false"\nsort_savefiles_enable = "false"\n'
            'libretro_directory = "/app/cores"\ncore_options_path = "/var/db/opts.cfg"\n'
        )
        assert atlas.CAVEAT_SANDBOX_PATH_UNTRANSLATED not in [c.code for c in p.caveats]

    def test_native_install_cfg_is_not_translated(self):
        # NativeRetroArch writes its cfg outside any sandbox: /var/config there
        # is a real host path, and substituting one would answer with a
        # directory this RetroArch never touches.
        machine = atlas.FixtureMachine(
            {
                f"{HOME}/.config/retroarch/retroarch.cfg": (
                    'savefile_directory = "/var/config/saves"\n'
                    'sort_savefiles_by_content_enable = "false"\nsort_savefiles_enable = "false"\n'
                ),
                "/var/config/saves/.keep": "",
                f"{HOME}/roms/gba/Game.zip": "",
            }
        )
        p = atlas.NativeRetroArch(HOME, machine).save_location(content_path=f"{HOME}/roms/gba/Game.zip")
        assert p.dir == "/var/config/saves"


class TestCfgFidelity:
    """A config atlas reads more permissively than RetroArch would attest paths
    the emulator never uses — so the resolver drops what the parser drops, keeps
    the previous value where the getter refuses one, and says so."""

    SORTED_CFG = (
        'savefile_directory = "/mnt/sd/retrodeck/saves"\n'
        'sort_savefiles_by_content_enable = "true"\nsort_savefiles_enable = "false"\n'
        'libretro_directory = "/app/cores"\n'
    )

    def _psp_query(self, cfg_lines, files=None):
        rd = _retrodeck(
            {
                RETRODECK_JSON: RD_JSON,
                RETRODECK_CFG: cfg_lines,
                "/mnt/sd/retrodeck/saves/.keep": "",
                "/mnt/sd/retrodeck/roms/psp/Game.iso": "",
                **(files or {}),
            },
            cores={f"{RD_DEPLOY_CORES}/ppsspp_libretro.so": {"library_name": "PPSSPP"}},
        )
        return rd.save_location(
            content_path="/mnt/sd/retrodeck/roms/psp/Game.iso", core_so="ppsspp_libretro.so"
        )

    def test_dropped_save_dir_line_falls_back_and_is_stated(self):
        # RetroArch drops 'savefile_directory="…"' (no space before the '='), so
        # the platform default governs — and the answer says which line was lost.
        p = self._psp_query(
            'savefile_directory="/mnt/sd/retrodeck/saves"\n'
            'sort_savefiles_by_content_enable = "false"\nsort_savefiles_enable = "false"\n'
            'libretro_directory = "/app/cores"\n'
        )
        assert p.dir == f"{HOME}/.var/app/net.retrodeck.retrodeck/config/retroarch/saves"
        assert [c.data for c in p.caveats if c.code == atlas.CAVEAT_CFG_LINE_DROPPED] == [
            {"key": "savefile_directory", "line": 'savefile_directory="/mnt/sd/retrodeck/saves"'}
        ]

    def test_rejected_override_value_keeps_the_global_layout_and_is_stated(self):
        p = self._psp_query(
            self.SORTED_CFG,
            files={
                f"{RETRODECK_OVERRIDES}/PPSSPP/PPSSPP.cfg": 'sort_savefiles_by_content_enable = "yes"'
            },
        )
        assert p.dir == "/mnt/sd/retrodeck/saves/psp"  # the global "true" still governs
        assert [c.data for c in p.caveats if c.code == atlas.CAVEAT_CFG_VALUE_REJECTED] == [
            {"key": "sort_savefiles_by_content_enable", "value": "yes"}
        ]

    def test_rejected_gate_value_keeps_the_gates_default(self):
        # auto_overrides_enable defaults true; "no" is not a boolean, so the
        # override below still applies — it is not silently switched off.
        p = self._psp_query(
            self.SORTED_CFG + 'auto_overrides_enable = "no"\n',
            files={
                f"{RETRODECK_OVERRIDES}/PPSSPP/PPSSPP.cfg": 'sort_savefiles_by_content_enable = "false"'
            },
        )
        assert p.dir == "/mnt/sd/retrodeck/saves"
        assert [c.data for c in p.caveats if c.code == atlas.CAVEAT_CFG_VALUE_REJECTED] == [
            {"key": "auto_overrides_enable", "value": "no"}
        ]

    def test_cleared_rgui_dir_puts_overrides_beside_retroarch_cfg(self):
        # rgui_config_directory = "default" clears the setting
        # (configuration.c:6825), and the override directory then falls back to
        # the directory of retroarch.cfg itself (file_path_special.c:196-207) —
        # one level above the platform-default 'config' subdirectory.
        beside = f"{HOME}/.var/app/net.retrodeck.retrodeck/config/retroarch"
        p = self._psp_query(
            self.SORTED_CFG + 'rgui_config_directory = "default"\n',
            files={
                f"{beside}/PPSSPP/PPSSPP.cfg": 'sort_savefiles_by_content_enable = "false"',
                f"{RETRODECK_OVERRIDES}/PPSSPP/PPSSPP.cfg": 'savefile_directory = "/mnt/sd/wrong"',
            },
        )
        assert p.dir == "/mnt/sd/retrodeck/saves"

    def test_absent_rgui_dir_keeps_the_config_subdirectory(self):
        p = self._psp_query(
            self.SORTED_CFG,
            files={
                f"{RETRODECK_OVERRIDES}/PPSSPP/PPSSPP.cfg": 'sort_savefiles_by_content_enable = "false"'
            },
        )
        assert p.dir == "/mnt/sd/retrodeck/saves"

    def test_app_relative_save_dir_is_stated_unexpanded(self):
        # ':' resolves against the running RetroArch executable's own directory
        # (file_path.c:1066-1101) — unknowable from disk, so atlas states the
        # value as configured instead of testing a host path it invented.
        p = self._psp_query(
            'savefile_directory = ":/saves"\n'
            'sort_savefiles_by_content_enable = "false"\nsort_savefiles_enable = "false"\n'
            'libretro_directory = "/app/cores"\n'
        )
        assert p.dir == ":/saves"
        codes = [c.code for c in p.caveats]
        assert atlas.CAVEAT_APP_RELATIVE_PATH_UNEXPANDED in codes
        assert atlas.CAVEAT_INVALID_SAVE_DIRECTORY not in codes
        assert p.file_set.state == "unknown"

    def test_app_relative_override_dir_reads_no_overrides(self):
        p = self._psp_query(
            self.SORTED_CFG + 'rgui_config_directory = ":/config"\n',
            files={
                f"{RETRODECK_OVERRIDES}/PPSSPP/PPSSPP.cfg": 'sort_savefiles_by_content_enable = "false"'
            },
        )
        assert p.dir == "/mnt/sd/retrodeck/saves/psp"  # the unreachable override did not apply
        assert [c.data for c in p.caveats if c.code == atlas.CAVEAT_APP_RELATIVE_PATH_UNEXPANDED] == [
            {"key": "rgui_config_directory", "path": ":/config"}
        ]


class TestOverrideChainSemantics:
    """What an override can and cannot change, read the way RetroArch reads it.

    The overrides are merged into the global cfg and re-read in one pass
    (configuration.c:7161-7243), so a value the merged config refuses leaves the
    global cfg's own value standing — not the platform default, and not a layer
    in between.
    """

    PLATFORM_DEFAULT = f"{HOME}/.var/app/net.retrodeck.retrodeck/config/retroarch/saves"
    GLOBAL_CFG = (
        'savefile_directory = "/mnt/sd/retrodeck/saves"\n'
        'sort_savefiles_by_content_enable = "false"\nsort_savefiles_enable = "false"\n'
        'libretro_directory = "/app/cores"\n'
    )

    def _psp_query(self, cfg_lines, files=None):
        rd = _retrodeck(
            {
                RETRODECK_JSON: RD_JSON,
                RETRODECK_CFG: cfg_lines,
                "/mnt/sd/retrodeck/saves/.keep": "",
                "/mnt/sd/retrodeck/roms/psp/Game.iso": "",
                **(files or {}),
            },
            cores={f"{RD_DEPLOY_CORES}/ppsspp_libretro.so": {"library_name": "PPSSPP"}},
        )
        return rd.save_location(
            content_path="/mnt/sd/retrodeck/roms/psp/Game.iso", core_so="ppsspp_libretro.so"
        )

    def test_unusable_override_root_keeps_the_global_root(self):
        # The per-core override points at an unmounted card. path_is_directory
        # fails, so that read sets nothing and the global cfg's root — which the
        # boot load already accepted — is where RetroArch writes.
        p = self._psp_query(
            self.GLOBAL_CFG,
            files={
                f"{RETRODECK_OVERRIDES}/PPSSPP/PPSSPP.cfg": 'savefile_directory = "/run/media/gone/saves"'
            },
        )
        assert p.dir == "/mnt/sd/retrodeck/saves"
        assert [c.data for c in p.caveats if c.code == atlas.CAVEAT_INVALID_SAVE_DIRECTORY] == [
            {
                "layer": "core override config/PPSSPP/PPSSPP.cfg",
                "configured": "/run/media/gone/saves",
                "effective": "/mnt/sd/retrodeck/saves",
            }
        ]

    def test_unusable_global_root_still_falls_to_the_platform_default(self):
        # The single-layer case is unchanged: nothing stood before the boot load
        # but the platform default under the config tree.
        p = self._psp_query(
            'savefile_directory = "/run/media/gone/saves"\n'
            'sort_savefiles_by_content_enable = "false"\nsort_savefiles_enable = "false"\n'
            'libretro_directory = "/app/cores"\n'
        )
        assert p.dir == self.PLATFORM_DEFAULT
        assert [c.data for c in p.caveats if c.code == atlas.CAVEAT_INVALID_SAVE_DIRECTORY] == [
            {
                "layer": "retroarch.cfg",
                "configured": "/run/media/gone/saves",
                "effective": self.PLATFORM_DEFAULT,
            }
        ]

    def test_a_usable_override_root_still_wins(self):
        p = self._psp_query(
            self.GLOBAL_CFG,
            files={
                f"{RETRODECK_OVERRIDES}/PPSSPP/PPSSPP.cfg": 'savefile_directory = "/mnt/sd/elsewhere"',
                "/mnt/sd/elsewhere/.keep": "",
            },
        )
        assert p.dir == "/mnt/sd/elsewhere"
        assert not [c for c in p.caveats if c.code == atlas.CAVEAT_INVALID_SAVE_DIRECTORY]

    def test_an_override_cannot_switch_overrides_off(self):
        # auto_overrides_enable is copied into a local BEFORE config_load_override
        # runs (runloop.c:4941 vs :5002-5003), so a file that only exists because
        # overrides are on cannot turn them off — the sort flip below still lands.
        p = self._psp_query(
            self.GLOBAL_CFG,
            files={
                f"{RETRODECK_OVERRIDES}/PPSSPP/PPSSPP.cfg": (
                    'auto_overrides_enable = "false"\nsort_savefiles_by_content_enable = "true"\n'
                ),
                "/mnt/sd/retrodeck/saves/psp/.keep": "",
            },
        )
        assert p.dir == "/mnt/sd/retrodeck/saves/psp"

    def test_dropped_rgui_line_moves_the_override_tree_and_is_stated(self):
        # The line sets nothing, so the override tree stays at the platform
        # default and the override below is never read — invisible in the answer
        # without the caveat.
        p = self._psp_query(
            self.GLOBAL_CFG + 'rgui_config_directory="/mnt/sd/retrodeck/rgui"\n',
            files={
                "/mnt/sd/retrodeck/rgui/PPSSPP/PPSSPP.cfg": 'sort_savefiles_by_content_enable = "true"',
                "/mnt/sd/retrodeck/saves/psp/.keep": "",
            },
        )
        assert p.dir == "/mnt/sd/retrodeck/saves"
        assert [c.data for c in p.caveats if c.code == atlas.CAVEAT_CFG_LINE_DROPPED] == [
            {
                "key": "rgui_config_directory",
                "line": 'rgui_config_directory="/mnt/sd/retrodeck/rgui"',
            }
        ]

    def test_dropped_auto_overrides_line_leaves_overrides_on_and_is_stated(self):
        p = self._psp_query(
            self.GLOBAL_CFG + 'auto_overrides_enable="false"\n',
            files={
                f"{RETRODECK_OVERRIDES}/PPSSPP/PPSSPP.cfg": 'sort_savefiles_by_content_enable = "true"',
                "/mnt/sd/retrodeck/saves/psp/.keep": "",
            },
        )
        assert p.dir == "/mnt/sd/retrodeck/saves/psp"  # the overrides did apply
        assert [c.data for c in p.caveats if c.code == atlas.CAVEAT_CFG_LINE_DROPPED] == [
            {"key": "auto_overrides_enable", "line": 'auto_overrides_enable="false"'}
        ]

    def test_a_dropped_line_for_an_ungoverning_key_is_still_silent(self):
        p = self._psp_query(self.GLOBAL_CFG + 'video_driver="gl"\n')
        assert not [c for c in p.caveats if c.code == atlas.CAVEAT_CFG_LINE_DROPPED]


class TestOstreeHomeIsHostSide:
    """Fedora Silverblue and Bazzite — both ship RetroDECK — make ``/home`` a
    symlink to ``/var/home``, so real home directories live under ``/var``.
    Nothing scopes atlas to SteamOS: ``home`` is whatever the caller passes."""

    HOME = "/var/home/deck"
    CFG = f"{HOME}/.var/app/net.retrodeck.retrodeck/config/retroarch/retroarch.cfg"
    JSON = f"{HOME}/.var/app/net.retrodeck.retrodeck/config/retrodeck/retrodeck.json"
    MARKER = '{"paths": {"rd_home_path": "/var/home/deck/retrodeck", "saves_path": "/var/home/deck/retrodeck/saves"}}'

    def _retrodeck_at(self, home, cfg_body, files=None, cores=None):
        machine = atlas.FixtureMachine(
            {
                self.JSON: self.MARKER,
                self.CFG: cfg_body,
                f"{self.HOME}/retrodeck/saves/.keep": "",
                f"{self.HOME}/retrodeck/roms/gba/Game.zip": "",
                **(files or {}),
            },
            cores=cores,
        )
        return atlas.RetroDeck(home, machine)

    def test_configured_directories_under_var_home_are_not_sandbox_paths(self):
        rd = self._retrodeck_at(
            self.HOME,
            f'savefile_directory = "{self.HOME}/retrodeck/saves"\n'
            'sort_savefiles_by_content_enable = "false"\nsort_savefiles_enable = "false"\n',
        )
        p = rd.save_location(content_path=f"{self.HOME}/retrodeck/roms/gba/Game.zip")
        assert p.dir == f"{self.HOME}/retrodeck/saves"
        assert atlas.CAVEAT_SANDBOX_PATH_UNTRANSLATED not in [c.code for c in p.caveats]

    def test_firmware_root_under_var_home_still_resolves(self):
        rd = self._retrodeck_at(
            self.HOME,
            f'system_directory = "{self.HOME}/retrodeck/bios"\n',
            files={f"{self.HOME}/retrodeck/bios/panafz1.bin": "rom"},
        )
        assert rd.firmware_inventory().root == f"{self.HOME}/retrodeck/bios"

    def test_var_home_is_host_side_even_when_home_is_spelled_the_other_way(self):
        # RetroArch resolves /home -> /var/home when it writes the cfg, while
        # the caller may still pass the symlink spelling as `home` — so the
        # configured path matches neither `home` nor a sandbox bind.
        machine = atlas.FixtureMachine(
            {
                RETRODECK_JSON: self.MARKER,
                RETRODECK_CFG: f'system_directory = "{self.HOME}/retrodeck/bios"\n',
                f"{self.HOME}/retrodeck/bios/panafz1.bin": "rom",
                f"{self.HOME}/retrodeck/saves/.keep": "",
            }
        )
        rd = atlas.RetroDeck(HOME, machine)
        assert rd.firmware_inventory().root == f"{self.HOME}/retrodeck/bios"

    def test_the_apps_own_config_dir_still_translates_into_that_home(self):
        # /var/config is still a bind of the app's config directory — which on
        # this host lives under /var/home. Both halves have to hold at once.
        overrides = f"{self.HOME}/.var/app/net.retrodeck.retrodeck/config/retroarch/config"
        rd = self._retrodeck_at(
            self.HOME,
            f'savefile_directory = "{self.HOME}/retrodeck/saves"\n'
            'sort_savefiles_by_content_enable = "true"\nsort_savefiles_enable = "false"\n'
            'libretro_directory = "/app/cores"\n'
            'rgui_config_directory = "/var/config/retroarch/config"\n',
            files={f"{overrides}/mGBA/mGBA.cfg": 'sort_savefiles_by_content_enable = "false"'},
            cores={f"{RD_DEPLOY_CORES}/mgba_libretro.so": {"library_name": "mGBA"}},
        )
        p = rd.save_location(
            content_path=f"{self.HOME}/retrodeck/roms/gba/Game.zip", core_so="mgba_libretro.so"
        )
        assert p.dir == f"{self.HOME}/retrodeck/saves"


class TestSandboxPathsInFirmware:
    CORE_INFO = 'display_name = "Opera"\nfirmware_count = "1"\nfirmware0_path = "panafz1.bin"\n'

    def test_info_path_in_sandbox_spelling_resolves_on_the_host(self):
        rd = _retrodeck(
            {
                RETRODECK_JSON: RD_JSON,
                RETRODECK_CFG: (
                    'system_directory = "/mnt/sd/retrodeck/bios"\n'
                    'libretro_info_path = "/var/config/retroarch/cores"\n'
                ),
                f"{HOME}/.var/app/net.retrodeck.retrodeck/config/retroarch/cores/opera_libretro.info": (
                    self.CORE_INFO
                ),
                "/mnt/sd/retrodeck/bios/panafz1.bin": "rom",
            }
        )
        assert [c.core_so for c in rd.firmware_inventory().cores] == ["opera_libretro.so"]

    def test_untranslatable_system_directory_is_stated_not_reported_missing(self):
        rd = _retrodeck(
            {
                RETRODECK_JSON: RD_JSON,
                RETRODECK_CFG: 'system_directory = "/var/db/bios"\n',
            }
        )
        answer = rd.firmware_inventory()
        assert answer.root is None
        assert [c.data for c in answer.caveats if c.code == atlas.CAVEAT_SANDBOX_PATH_UNTRANSLATED] == [
            {"key": "system_directory", "path": "/var/db/bios"}
        ]


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
