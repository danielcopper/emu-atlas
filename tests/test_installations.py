"""Tests for atlas.installations — handles, health, and the live resolver."""

from __future__ import annotations

import json
from collections import Counter

import pytest

import atlas
from atlas.firmware import resolve_links
from atlas.machine import SYMLINK_HOPS, FixtureMachine
from tests.answers import placed, state_placed

HOME = "/home/deck"
RETRODECK_JSON = f"{HOME}/.var/app/net.retrodeck.retrodeck/config/retrodeck/retrodeck.json"
RETRODECK_CFG = f"{HOME}/.var/app/net.retrodeck.retrodeck/config/retroarch/retroarch.cfg"
RETRODECK_OVERRIDES = f"{HOME}/.var/app/net.retrodeck.retrodeck/config/retroarch/config"
ESDE_SETTINGS = f"{HOME}/.var/app/net.retrodeck.retrodeck/config/ES-DE/settings/es_settings.xml"
EMUDECK_SETTINGS = f"{HOME}/.config/EmuDeck/settings.sh"
STANDALONE_CFG = f"{HOME}/.var/app/org.libretro.RetroArch/config/retroarch/retroarch.cfg"

RD_JSON = '{"paths": {"rd_home_path": "/mnt/sd/retrodeck", "saves_path": "/mnt/sd/retrodeck/saves"}}'
RD_DEPLOY_CORES = "/var/lib/flatpak/app/net.retrodeck.retrodeck/current/active/files/cores"


def _retrodeck(files, **kwargs):
    machine = FixtureMachine(files, **kwargs)
    return atlas.RetroDeck(HOME, machine)


RD_OVERLAY = "/mnt/sd/retrodeck/ES-DE/custom_systems/es_systems.xml"
RD_BUNDLED_ESDE = (
    "/var/lib/flatpak/app/net.retrodeck.retrodeck/current/active/files/retrodeck/"
    "components/es-de/share/es-de/resources/systems/linux/es_systems.xml"
)
GB_SYSTEM = (
    '<?xml version="1.0"?>\n<systemList>\n  <system>\n    <name>gb</name>\n'
    "    <path>%ROMPATH%/gb</path>\n    <extension>.gb</extension>\n"
    '    <command label="Gambatte">retroarch -L /app/cores/gambatte_libretro.so %ROM%</command>\n'
    "  </system>\n</systemList>\n"
)


class TestABrokenCatalogueEmptiesTheFrontend:
    """Issue #100: a systems file ES-DE cannot load aborts its whole catalogue."""

    BASE = {
        RETRODECK_JSON: RD_JSON,
        RETRODECK_CFG: 'savefile_directory = "/mnt/sd/retrodeck/saves"\n'
        'libretro_directory = "/app/cores"\n',
        RD_BUNDLED_ESDE: GB_SYSTEM,
    }

    def _rd(self, overlay):
        return _retrodeck(
            {**self.BASE, RD_OVERLAY: overlay}, dirs=["/mnt/sd/retrodeck/saves"]
        )

    def test_an_unparseable_overlay_kills_the_bundled_catalogue_too(self):
        rd = self._rd("<systemList><system><name>gb</name>")
        answer = rd.systems()
        assert answer.systems == ()
        stated = [c for c in answer.caveats if c.code == atlas.HEALTH_ISSUE_CATALOGUE_INVALID]
        assert stated
        assert stated[0].data == {"path": RD_OVERLAY, "problem": "parse-error"}

    def test_a_document_without_a_systemlist_is_the_same_refusal(self):
        # RetroDECK's real stub carries an EMPTY <systemList/>, which is fine;
        # a document with none at all is INVALID_FILE (SystemData.cpp:900-903).
        rd = self._rd('<?xml version="1.0"?>\n<!-- nothing else -->\n')
        answer = rd.systems()
        assert answer.systems == ()
        stated = [c for c in answer.caveats if c.code == atlas.HEALTH_ISSUE_CATALOGUE_INVALID]
        assert stated
        assert stated[0].data["problem"] == "missing-systemlist"

    def test_the_finding_is_installation_health(self):
        rd = self._rd("<systemList><system><name>gb</name>")
        assert atlas.HEALTH_ISSUE_CATALOGUE_INVALID in [c.code for c in rd.health().issues]

    def test_an_empty_systemlist_stays_healthy(self):
        # The healthy stub shape: parses, declares nothing, ES-DE moves on.
        rd = self._rd('<?xml version="1.0"?>\n<systemList>\n<!-- stub -->\n</systemList>\n')
        answer = rd.systems()
        assert answer.systems == ("gb",)
        assert not any(
            c.code == atlas.HEALTH_ISSUE_CATALOGUE_INVALID for c in answer.caveats
        )


class TestAnUnwiredContentTreeIsAFinding:
    """Issue #104: a hub tree without its emulator-side link loses what is filed there.

    The exemplar pair is the Citra texture row (hub
    ``texture_packs/retroarch-core/Citra/textures``, emulator side
    ``<xdg-config>/retroarch/saves/Citra/load/textures``); the wired contrast
    is the PPSSPP mods row. Every gate is exercised as its own case, because
    each one failing open would alarm a machine the table knows nothing about.
    """

    VERSIONED_JSON = (
        '{"version": "0.10.9b", '
        '"paths": {"rd_home_path": "/mnt/sd/retrodeck", "saves_path": "/mnt/sd/retrodeck/saves"}}'
    )
    HUB_TREE = "/mnt/sd/retrodeck/texture_packs/retroarch-core/Citra/textures"
    EMULATOR_SIDE = f"{HOME}/.var/app/net.retrodeck.retrodeck/config/retroarch/saves/Citra/load/textures"

    def _rd(self, *, marker=VERSIONED_JSON, dirs=(), symlinks=None, inaccessible=()):
        machine = FixtureMachine(
            {RETRODECK_JSON: marker, RETRODECK_CFG: 'libretro_directory = "/app/cores"\n'},
            dirs=["/mnt/sd/retrodeck/saves", *dirs],
            symlinks=symlinks or {},
            inaccessible=list(inaccessible),
        )
        return atlas.RetroDeck(HOME, machine)

    def _findings(self, rd):
        return [c for c in rd.health().issues if c.code == atlas.HEALTH_ISSUE_CONTENT_TREE_UNWIRED]

    def test_a_plain_directory_in_place_of_the_link_fires(self):
        rd = self._rd(dirs=[self.HUB_TREE, self.EMULATOR_SIDE])
        findings = self._findings(rd)
        assert [f.data for f in findings] == [
            {
                "family": "texture_packs",
                "hub": self.HUB_TREE,
                "path": self.EMULATOR_SIDE,
                "problem": "not-a-link",
            }
        ]

    def test_a_missing_emulator_side_fires_its_own_problem(self):
        rd = self._rd(dirs=[self.HUB_TREE])
        findings = self._findings(rd)
        assert [f.data["problem"] for f in findings] == ["missing"]

    def test_a_link_settling_outside_the_hub_names_where_it_went(self):
        rd = self._rd(
            dirs=[self.HUB_TREE, "/mnt/other/textures"],
            symlinks={self.EMULATOR_SIDE: "/mnt/other/textures"},
        )
        findings = self._findings(rd)
        assert [f.data for f in findings] == [
            {
                "family": "texture_packs",
                "hub": self.HUB_TREE,
                "path": self.EMULATOR_SIDE,
                "problem": "diverted",
                "target": "/mnt/other/textures",
            }
        ]

    def test_a_wired_pair_is_silent(self):
        rd = self._rd(dirs=[self.HUB_TREE], symlinks={self.EMULATOR_SIDE: self.HUB_TREE})
        assert self._findings(rd) == []

    def test_a_dead_link_into_the_hub_is_still_wired(self):
        # Creating the hub side brings the link to life — the routing stands,
        # so the pair supports no finding (the per-answer dead-symlink caveat
        # is the place that state is told).
        rd = self._rd(
            dirs=[self.HUB_TREE],
            symlinks={self.EMULATOR_SIDE: "/mnt/sd/retrodeck/texture_packs/somewhere/else"},
        )
        assert self._findings(rd) == []

    def test_a_link_into_an_older_hub_layout_is_still_wired(self):
        # RetroDECK 0.7-era upgrades linked Dolphin's whole hub directory, not
        # today's Textures tree below it. That link is hub wiring; the weak
        # criterion is what keeps a legitimately upgraded machine quiet.
        dolphin_side = f"{HOME}/.var/app/net.retrodeck.retrodeck/data/dolphin-emu/Load/Textures"
        rd = self._rd(
            dirs=["/mnt/sd/retrodeck/texture_packs/Dolphin/Textures"],
            symlinks={dolphin_side: "/mnt/sd/retrodeck/texture_packs/Dolphin"},
        )
        assert self._findings(rd) == []

    def test_an_absent_hub_tree_checks_nothing(self):
        rd = self._rd(dirs=[self.EMULATOR_SIDE])
        assert self._findings(rd) == []

    def test_a_version_the_table_never_read_checks_nothing(self):
        other = self.VERSIONED_JSON.replace("0.10.9b", "0.11.0b")
        rd = self._rd(marker=other, dirs=[self.HUB_TREE, self.EMULATOR_SIDE])
        assert self._findings(rd) == []

    def test_a_marker_without_a_version_checks_nothing(self):
        rd = self._rd(marker=RD_JSON, dirs=[self.HUB_TREE, self.EMULATOR_SIDE])
        assert self._findings(rd) == []

    def test_an_unstatable_emulator_side_supports_no_claim(self):
        rd = self._rd(dirs=[self.HUB_TREE], inaccessible=[self.EMULATOR_SIDE])
        assert self._findings(rd) == []


class TestLaunchable:
    """Issue #36: whether a file launches as a system's content — and why not, when not.

    The accept-list is matched the way ES-DE matches it: the file name's token
    from its last dot, case preserved, compared exactly against the declared
    tokens (FileSystemUtil.cpp:630-645, SystemData.cpp:669 @ v3.4.1).
    """

    DREAMCAST = (
        '<?xml version="1.0"?>\n<systemList>\n  <system>\n    <name>dreamcast</name>\n'
        "    <path>%ROMPATH%/dreamcast</path>\n    <extension>.chd .cue .gdi</extension>\n"
        '    <command label="Flycast">retroarch -L /app/cores/flycast_libretro.so %ROM%</command>\n'
        "  </system>\n  <system>\n    <name>ps3</name>\n"
        "    <path>%ROMPATH%/ps3</path>\n    <extension>.iso .ps3 .ps3dir</extension>\n"
        '    <command label="RPCS3">%EMULATOR_RPCS3% %ROM%</command>\n'
        "  </system>\n</systemList>\n"
    )
    FILES = {
        RETRODECK_JSON: RD_JSON,
        RETRODECK_CFG: 'savefile_directory = "/mnt/sd/retrodeck/saves"\n',
        RD_BUNDLED_ESDE: DREAMCAST,
    }

    def _rd(self, files=None):
        return _retrodeck({**self.FILES, **(files or {})}, dirs=["/mnt/sd/retrodeck/saves"])

    def test_an_accepted_extension_is_launchable_with_its_entry(self):
        answer = self._rd().launchable("dreamcast", "/roms/dreamcast/Game.chd")
        assert answer.verdict == atlas.VERDICT_LAUNCHABLE
        assert answer.extension == ".chd"
        assert answer.accepted == (".chd", ".cue", ".gdi")
        assert answer.entry is not None
        assert answer.entry.label == "Flycast"

    def test_the_match_is_case_sensitive_the_way_esde_scans(self):
        # ES-DE never lowercases: a .CHD file with only .chd declared never
        # appears in the menu, and the verdict says so.
        answer = self._rd().launchable("dreamcast", "/roms/dreamcast/Game.CHD")
        assert answer.verdict == atlas.VERDICT_NOT_ACCEPTED
        assert answer.extension == ".CHD"
        assert answer.entry is None

    def test_a_track_file_is_not_accepted(self):
        # The GDI-rip case: the accept-list deliberately excludes track files,
        # and the verdict steers a largest-file fallback away from them.
        answer = self._rd().launchable("dreamcast", "/roms/dreamcast/Game (Track 1).bin")
        assert answer.verdict == atlas.VERDICT_NOT_ACCEPTED

    def test_a_psn_package_needs_installation_first(self):
        answer = self._rd().launchable("ps3", "/roms/ps3/Game.pkg")
        assert answer.verdict == atlas.VERDICT_NEEDS_INSTALLATION
        assert answer.extension == ".pkg"
        assert answer.entry is None

    def test_an_undeclared_system_is_unknown_not_refused_as_content(self):
        answer = self._rd().launchable("wonderswan", "/roms/wonderswan/Game.ws")
        assert answer.verdict == atlas.VERDICT_UNKNOWN
        assert atlas.CAVEAT_SYSTEM_UNKNOWN in [c.code for c in answer.caveats]

    def test_a_system_esde_would_skip_is_unknown(self):
        # A declared system without a <command> never loads (loadConfig skips
        # it, SystemData.cpp:1109-1119) — its accept-list judges nothing.
        broken = self.DREAMCAST.replace(
            '<command label="Flycast">retroarch -L /app/cores/flycast_libretro.so %ROM%</command>\n',
            "",
        )
        answer = self._rd({RD_BUNDLED_ESDE: broken}).launchable(
            "dreamcast", "/roms/dreamcast/Game.chd"
        )
        assert answer.verdict == atlas.VERDICT_UNKNOWN
        assert atlas.CAVEAT_SYSTEM_UNKNOWN in [c.code for c in answer.caveats]

    N3DS = (
        '<?xml version="1.0"?>\n<systemList>\n  <system>\n    <name>n3ds</name>\n'
        "    <path>%ROMPATH%/n3ds</path>\n    <extension>.3ds .zip .7z</extension>\n"
        '    <command label="Azahar">%EMULATOR_AZAHAR% %ROM%</command>\n'
        '    <command label="Citra">retroarch -L /app/cores/citra_libretro.so %ROM%</command>\n'
        "  </system>\n</systemList>\n"
    )
    DEPLOY_CORES = "/var/lib/flatpak/app/net.retrodeck.retrodeck/current/active/files/cores"
    CFG_WITH_CORES = (
        'savefile_directory = "/mnt/sd/retrodeck/saves"\nlibretro_directory = "/app/cores"\n'
    )

    def _n3ds(self, cores=None):
        return _retrodeck(
            {
                **self.FILES,
                RETRODECK_CFG: self.CFG_WITH_CORES,
                RD_BUNDLED_ESDE: self.N3DS,
            },
            dirs=["/mnt/sd/retrodeck/saves"],
            cores=cores or {},
        )

    def test_a_recorded_standalone_refusal_flips_the_verdict(self):
        # Issue #66's founding case: the union says yes, the default entry is
        # a standalone recorded as opening no archive, and the alternative
        # that would take the container is named.
        citra = {
            f"{self.DEPLOY_CORES}/citra_libretro.so": {
                "library_name": "Citra",
                "valid_extensions": "3ds|3dsx|cia|elf",
                "block_extract": False,
            }
        }
        answer = self._n3ds(citra).launchable("n3ds", "/roms/n3ds/Game.zip")
        assert answer.verdict == atlas.VERDICT_ENTRY_NOT_ACCEPTED
        assert answer.entry is not None
        assert answer.entry.label == "Azahar"
        assert answer.alternatives == ("Citra",)

    def test_a_block_extract_core_handed_an_unclaimed_archive_refuses(self):
        # The one libretro refusal this can establish: block_extract means the
        # archive goes to the core raw (task_content.c:742, :1735), and the
        # core never claimed to read one.
        core = {
            f"{self.DEPLOY_CORES}/citra_libretro.so": {
                "library_name": "Citra",
                "valid_extensions": "3ds|3dsx",
                "block_extract": True,
            }
        }
        catalogue = self.N3DS.replace(
            '    <command label="Azahar">%EMULATOR_AZAHAR% %ROM%</command>\n', ""
        )
        rd = _retrodeck(
            {**self.FILES, RETRODECK_CFG: self.CFG_WITH_CORES, RD_BUNDLED_ESDE: catalogue},
            dirs=["/mnt/sd/retrodeck/saves"],
            cores=core,
        )
        answer = rd.launchable("n3ds", "/roms/n3ds/Game.zip")
        assert answer.verdict == atlas.VERDICT_ENTRY_NOT_ACCEPTED
        assert answer.alternatives == ()

    def test_an_archive_for_an_extracting_core_is_launchable_with_the_boundary(self):
        core = {
            f"{self.DEPLOY_CORES}/citra_libretro.so": {
                "library_name": "Citra",
                "valid_extensions": "3ds|3dsx",
                "block_extract": False,
            }
        }
        catalogue = self.N3DS.replace(
            '    <command label="Azahar">%EMULATOR_AZAHAR% %ROM%</command>\n', ""
        )
        rd = _retrodeck(
            {**self.FILES, RETRODECK_CFG: self.CFG_WITH_CORES, RD_BUNDLED_ESDE: catalogue},
            dirs=["/mnt/sd/retrodeck/saves"],
            cores=core,
        )
        answer = rd.launchable("n3ds", "/roms/n3ds/Game.zip")
        assert answer.verdict == atlas.VERDICT_LAUNCHABLE
        assert atlas.CAVEAT_ARCHIVE_CONTENTS_UNREAD in [c.code for c in answer.caveats]

    def test_a_direct_file_outside_the_claims_is_attempted_with_a_statement(self):
        core = {
            f"{self.DEPLOY_CORES}/citra_libretro.so": {
                "library_name": "Citra",
                "valid_extensions": "3dsx|cia",
                "block_extract": False,
            }
        }
        catalogue = self.N3DS.replace(
            '    <command label="Azahar">%EMULATOR_AZAHAR% %ROM%</command>\n', ""
        )
        rd = _retrodeck(
            {**self.FILES, RETRODECK_CFG: self.CFG_WITH_CORES, RD_BUNDLED_ESDE: catalogue},
            dirs=["/mnt/sd/retrodeck/saves"],
            cores=core,
        )
        answer = rd.launchable("n3ds", "/roms/n3ds/Game.3ds")
        assert answer.verdict == atlas.VERDICT_LAUNCHABLE
        assert atlas.CAVEAT_ENTRY_FORMAT_UNCLAIMED in [c.code for c in answer.caveats]

    def test_a_standalone_without_a_card_is_unestablished_never_refuses(self):
        # Azahar HAS a card and takes .3ds — the uncarded standalone is the
        # discriminating entry here.
        rpcs3 = self.N3DS.replace("AZAHAR", "RPCS3").replace(
            '<command label="Azahar">%EMULATOR_RPCS3% %ROM%</command>',
            '<command label="RPCS3">%EMULATOR_RPCS3% %ROM%</command>',
        )
        rd = _retrodeck(
            {**self.FILES, RETRODECK_CFG: self.CFG_WITH_CORES, RD_BUNDLED_ESDE: rpcs3},
            dirs=["/mnt/sd/retrodeck/saves"],
        )
        answer = rd.launchable("n3ds", "/roms/n3ds/Game.3ds")
        assert answer.verdict == atlas.VERDICT_LAUNCHABLE
        assert atlas.CAVEAT_ENTRY_FORMAT_UNESTABLISHED in [c.code for c in answer.caveats]

    def test_the_recorded_azahar_card_confirms_its_own_formats(self):
        answer = self._n3ds().launchable("n3ds", "/roms/n3ds/Game.3ds")
        assert answer.verdict == atlas.VERDICT_LAUNCHABLE
        assert answer.entry is not None
        assert answer.entry.label == "Azahar"
        assert [c.code for c in answer.caveats] == []

    def test_the_loader_archive_quirk_is_upstreams(self):
        # path_is_compressed_file folds zip/zst/apk per character and the 7z
        # only at its digit — a '.7Z' is not compressed to that loader, so it
        # is a plain unclaimed format here where '.7z' is an opened container.
        core = {
            f"{self.DEPLOY_CORES}/citra_libretro.so": {
                "library_name": "Citra",
                "valid_extensions": "3ds|3dsx",
                "block_extract": False,
            }
        }
        catalogue = self.N3DS.replace(
            '    <command label="Azahar">%EMULATOR_AZAHAR% %ROM%</command>\n', ""
        ).replace("<extension>.3ds .zip .7z</extension>", "<extension>.3ds .7z .7Z</extension>")
        rd = _retrodeck(
            {**self.FILES, RETRODECK_CFG: self.CFG_WITH_CORES, RD_BUNDLED_ESDE: catalogue},
            dirs=["/mnt/sd/retrodeck/saves"],
            cores=core,
        )
        lower = rd.launchable("n3ds", "/roms/n3ds/Game.7z")
        upper = rd.launchable("n3ds", "/roms/n3ds/Game.7Z")
        assert atlas.CAVEAT_ARCHIVE_CONTENTS_UNREAD in [c.code for c in lower.caveats]
        assert atlas.CAVEAT_ENTRY_FORMAT_UNCLAIMED in [c.code for c in upper.caveats]

    def test_a_catalogue_less_arrangement_answers_unknown(self):
        machine = FixtureMachine(
            {f"{HOME}/.config/retroarch/retroarch.cfg": 'savefile_directory = "~/saves"\n'}
        )
        answer = atlas.BareRetroArchNative(HOME, machine).launchable(
            "dreamcast", "/roms/dreamcast/Game.chd"
        )
        assert answer.verdict == atlas.VERDICT_UNKNOWN
        assert answer.accepted == ()

    def test_a_sealed_catalogue_keeps_an_undeclared_system_unclaimed(self):
        # EmuDeck's bundled layer is sealed: the system may be declared in the
        # part atlas cannot open, so unknown rides the sealed statement alone —
        # never system-unknown, which would claim a complete read.
        machine = FixtureMachine(
            {
                EMUDECK_SETTINGS: 'romsPath="$HOME/Emulation/roms"\n',
                STANDALONE_CFG: 'savefile_directory = "~/saves"\n',
                f"{HOME}/ES-DE/custom_systems/es_systems.xml": (
                    '<?xml version="1.0"?><systemList></systemList>'
                ),
            }
        )
        answer = atlas.EmuDeck(HOME, machine).launchable("dreamcast", "/roms/dreamcast/Game.chd")
        assert answer.verdict == atlas.VERDICT_UNKNOWN
        codes = [c.code for c in answer.caveats]
        assert atlas.CAVEAT_EMULATOR_CATALOGUE_SEALED in codes
        assert atlas.CAVEAT_SYSTEM_UNKNOWN not in codes


class TestADerivedEmulatorList:
    """Issue #133: a catalogue-less arrangement's emulator list, from the cores' own .info."""

    CFG_PATH = f"{HOME}/.var/app/org.libretro.RetroArch/config/retroarch/retroarch.cfg"
    CORES_DIR = f"{HOME}/.var/app/org.libretro.RetroArch/config/retroarch/cores"
    CFG = (
        'savefile_directory = "~/saves"\n'
        f'libretro_directory = "{CORES_DIR}"\nlibretro_info_path = "{CORES_DIR}"\n'
        'sort_savefiles_by_content_enable = "false"\nsort_savefiles_enable = "false"\n'
    )
    MGBA_INFO = (
        'corename = "mGBA"\nsystemname = "Game Boy Advance"\nfirmware_count = "0"\n'
    )

    def _bare(self):
        machine = FixtureMachine(
            {
                self.CFG_PATH: self.CFG,
                f"{self.CORES_DIR}/mgba_libretro.info": self.MGBA_INFO,
            },
            cores={f"{self.CORES_DIR}/mgba_libretro.so": {"library_name": "mGBA"}},
            dirs=[f"{HOME}/saves"],
        )
        return atlas.BareRetroArchFlatpak(HOME, machine)

    def test_the_entries_are_the_cores_with_their_own_names(self):
        answer = self._bare().emulators_for("gba")
        assert [(e.label, e.core_so, e.kind) for e in answer.entries] == [
            ("mGBA", "mgba_libretro.so", atlas.KIND_LIBRETRO)
        ]
        # No catalogue declares a command — empty is the honest statement.
        assert answer.entries[0].command == ""
        codes = [c.code for c in answer.caveats]
        assert atlas.CAVEAT_EMULATOR_LIST_DERIVED in codes
        assert atlas.CAVEAT_EMULATOR_CATALOGUE_UNAVAILABLE in codes

    def test_a_derived_entry_answers_the_save_question(self):
        # The client gain: the entry route works, because a derived entry is a
        # core and the core question has always been answerable here.
        entry = self._bare().emulators_for("gba").entries[0]
        placement = placed(entry.savefile_location(content_path="/roms/gba/Game.gba"))
        assert placement.dir == f"{HOME}/saves"

    def test_the_systems_list_is_what_the_cores_file_under(self):
        answer = self._bare().systems()
        assert answer.systems == ("gba",)
        assert atlas.CAVEAT_EMULATOR_LIST_DERIVED in [c.code for c in answer.caveats]

    def test_a_system_no_core_files_under_is_empty_and_derived(self):
        answer = self._bare().emulators_for("n64")
        assert answer.entries == ()
        assert atlas.CAVEAT_EMULATOR_LIST_DERIVED in [c.code for c in answer.caveats]

    def test_emudeck_derives_where_the_readable_layers_are_silent(self):
        machine = FixtureMachine(
            {
                EMUDECK_SETTINGS: 'savesPath="$HOME/Emulation/saves"\n',
                STANDALONE_CFG: self.CFG,
                f"{HOME}/ES-DE/custom_systems/es_systems.xml": (
                    '<?xml version="1.0"?><systemList></systemList>'
                ),
                f"{self.CORES_DIR}/mgba_libretro.info": self.MGBA_INFO,
            },
            cores={f"{self.CORES_DIR}/mgba_libretro.so": {"library_name": "mGBA"}},
        )
        answer = atlas.EmuDeck(HOME, machine).emulators_for("gba")
        assert [e.label for e in answer.entries] == ["mGBA"]
        codes = [c.code for c in answer.caveats]
        assert atlas.CAVEAT_EMULATOR_CATALOGUE_SEALED in codes
        assert atlas.CAVEAT_EMULATOR_LIST_DERIVED in codes

    def test_emudecks_systems_list_joins_the_derived_ones_while_sealed(self):
        overlay = (
            '<?xml version="1.0"?><systemList><system><name>atarijaguar</name>'
            "<path>%ROMPATH%/atarijaguar</path><extension>.j64</extension>"
            '<command label="Virtual Jaguar">%EMULATOR_RETROARCH% -L '
            "%CORE_RETROARCH%/virtualjaguar_libretro.so %ROM%</command>"
            "</system></systemList>"
        )
        machine = FixtureMachine(
            {
                EMUDECK_SETTINGS: 'savesPath="$HOME/Emulation/saves"\n',
                STANDALONE_CFG: self.CFG,
                f"{HOME}/ES-DE/custom_systems/es_systems.xml": overlay,
                f"{self.CORES_DIR}/mgba_libretro.info": self.MGBA_INFO,
            },
            cores={f"{self.CORES_DIR}/mgba_libretro.so": {"library_name": "mGBA"}},
        )
        answer = atlas.EmuDeck(HOME, machine).systems()
        assert answer.systems == ("atarijaguar", "gba")
        assert atlas.CAVEAT_EMULATOR_LIST_DERIVED in [c.code for c in answer.caveats]

    def test_emudeck_keeps_the_catalogue_where_the_overlay_declares(self):
        # A declared system stays the frontend's own answer — the derivation
        # never overrides a read.
        overlay = (
            '<?xml version="1.0"?><systemList><system><name>gba</name>'
            "<path>%ROMPATH%/gba</path><extension>.gba</extension>"
            '<command label="mGBA (Standalone)">%EMULATOR_MGBA% %ROM%</command>'
            "</system></systemList>"
        )
        machine = FixtureMachine(
            {
                EMUDECK_SETTINGS: 'savesPath="$HOME/Emulation/saves"\n',
                STANDALONE_CFG: self.CFG,
                f"{HOME}/ES-DE/custom_systems/es_systems.xml": overlay,
                f"{self.CORES_DIR}/mgba_libretro.info": self.MGBA_INFO,
            },
            cores={f"{self.CORES_DIR}/mgba_libretro.so": {"library_name": "mGBA"}},
        )
        answer = atlas.EmuDeck(HOME, machine).emulators_for("gba")
        assert [e.label for e in answer.entries] == ["mGBA (Standalone)"]
        assert atlas.CAVEAT_EMULATOR_LIST_DERIVED not in [c.code for c in answer.caveats]


class TestARevokedSaveRootIsStated:
    """Issue #103: an override can take filesystem access away, and the answer says so.

    Differential on purpose: the caveat fires exactly when visibility flips
    from the app's own metadata grants to the effective (override-merged)
    table — a root the metadata never granted is not *revoked*.
    """

    METADATA = "/var/lib/flatpak/app/net.retrodeck.retrodeck/current/active/metadata"
    DEPLOY = "/var/lib/flatpak/app/net.retrodeck.retrodeck/current/active/files"
    USER_APP = f"{HOME}/.local/share/flatpak/overrides/net.retrodeck.retrodeck"
    CORE = f"{DEPLOY}/cores/mgba_libretro.so"
    ROM = "/run/media/mmcblk0p1/retrodeck/roms/gba/Game.gba"
    CFG = (
        'savefile_directory = "/run/media/mmcblk0p1/retrodeck/saves"\n'
        'libretro_directory = "/app/cores"\n'
        'sort_savefiles_by_content_enable = "false"\nsort_savefiles_enable = "false"\n'
    )

    def _placement(self, override=None, metadata='[Context]\nfilesystems=host;\n'):
        files = {
            RETRODECK_JSON: RD_JSON,
            RETRODECK_CFG: self.CFG,
            self.METADATA: metadata,
            self.ROM: "",
        }
        if override is not None:
            files[self.USER_APP] = override
        machine = FixtureMachine(
            files,
            cores={self.CORE: {"library_name": "mGBA"}},
            dirs=["/run/media/mmcblk0p1/retrodeck/saves", self.DEPLOY],
        )
        rd = atlas.RetroDeck(HOME, machine)
        return placed(rd.savefile_location(content_path=self.ROM, core_so="mgba_libretro.so"))

    def _revoked(self, placement):
        return [c for c in placement.caveats if c.code == atlas.CAVEAT_SAVE_ROOT_REVOKED]

    def test_a_revoking_entry_over_the_save_root_is_stated(self):
        placement = self._placement(
            override="[Context]\nfilesystems=!/run/media/mmcblk0p1;\n"
        )
        stated = self._revoked(placement)
        assert [c.data for c in stated] == [
            {
                "path": "/run/media/mmcblk0p1/retrodeck/saves",
                "entry": "!/run/media/mmcblk0p1",
                "options_file": self.USER_APP,
            }
        ]
        # The answer stands — the config really names this directory.
        assert placement.dir == "/run/media/mmcblk0p1/retrodeck/saves"

    def test_a_more_specific_grant_restores_the_tree(self):
        # flatpak's application: the longest covering entry wins
        # (path_is_mapped, flatpak-exports.c:340-378) — a grant below the
        # revoked tree brings the save root back.
        placement = self._placement(
            override=(
                "[Context]\nfilesystems=!/run/media/mmcblk0p1;"
                "/run/media/mmcblk0p1/retrodeck;\n"
            )
        )
        assert self._revoked(placement) == []

    def test_a_dropped_special_grant_is_stated_with_its_entry(self):
        placement = self._placement(override="[Context]\nfilesystems=!host;\n")
        stated = self._revoked(placement)
        assert len(stated) == 1
        assert stated[0].data["entry"] == "!host"

    def test_a_root_the_metadata_never_granted_is_not_revoked(self):
        # Visibility did not flip: nothing was taken away, and "the app never
        # had access" is a statement nobody asked this question for.
        placement = self._placement(
            override="[Context]\nfilesystems=!/run/media/mmcblk0p1;\n",
            metadata="[Context]\nfilesystems=xdg-music;\n",
        )
        assert self._revoked(placement) == []

    def test_without_overrides_nothing_fires(self):
        assert self._revoked(self._placement()) == []

    def test_a_hide_colliding_with_hosts_own_export_is_powerless(self):
        # 'host' exports /run/media itself (flatpak-context.c:2884-2888), and
        # two entries on the SAME path collapse with the higher mode winning
        # (do_export_path, flatpak-exports.c:760-798) — so '!/run/media'
        # beside a host grant hides nothing, and neither does the answer
        # claim it would. Verified live before this test pinned it.
        placement = self._placement(override="[Context]\nfilesystems=!/run/media;\n")
        assert self._revoked(placement) == []

    def test_host_reset_drops_the_grant_base(self):
        # '!host-reset' clears everything merged so far and implies '!host'
        # (flatpak-context.c:1086-1090, :1046-1051); with no rebuilt grant
        # covering the root, the revocation stands.
        placement = self._placement(override="[Context]\nfilesystems=!host:reset;\n")
        assert len(self._revoked(placement)) == 1


class TestRetroDeckPaths:
    def test_roots_from_json(self):
        rd = _retrodeck({RETRODECK_JSON: RD_JSON})
        assert rd.root() == "/mnt/sd/retrodeck"
        assert rd.saves_root() == "/mnt/sd/retrodeck/saves"

    def test_fallback_roots_when_json_lacks_paths(self):
        rd = _retrodeck({RETRODECK_JSON: "{}"})
        assert rd.root() == f"{HOME}/retrodeck"
        assert rd.bios_dir() == f"{HOME}/retrodeck/bios"


class TestMarkerPathValuesMustBeStrings:
    """A marker with a non-string path is present and broken, not usable.

    ``retrodeck.json`` is editable and drives every read that follows. Handing
    a non-string on unchecked made ``health()`` raise ``AttributeError`` on the
    fixture seam and ``TypeError`` from ``os.stat`` on the real one — and
    ``os.stat(123)`` reads an *int* as a file descriptor, so it can even answer
    for something that is not a path at all.
    """

    FALLBACK_DIRS = [f"{HOME}/retrodeck", f"{HOME}/retrodeck/saves"]

    def test_non_string_path_value_makes_the_marker_invalid(self):
        rd = _retrodeck({RETRODECK_JSON: '{"paths": {"rd_home_path": 123}}'}, dirs=self.FALLBACK_DIRS)
        assert rd.health().codes == (atlas.HEALTH_ISSUE_MARKER_INVALID,)

    def test_the_offending_key_is_named(self):
        rd = _retrodeck({RETRODECK_JSON: '{"paths": {"rd_home_path": "/mnt/sd/rd", "saves_path": {"a": 1}}}'})
        issue = rd.health().issues[0]
        assert issue.code == atlas.HEALTH_ISSUE_MARKER_INVALID
        assert issue.data["key"] == "paths.saves_path"

    def test_health_does_not_raise_and_roots_stay_strings(self):
        rd = _retrodeck({RETRODECK_JSON: '{"paths": {"rd_home_path": 123, "saves_path": {"a": 1}}}'})
        assert rd.health().codes[0] == atlas.HEALTH_ISSUE_MARKER_INVALID
        for value in (rd.root(), rd.saves_root(), rd.bios_dir()):
            assert isinstance(value, str)
        assert rd.root() == f"{HOME}/retrodeck"
        # roms_dir() is not in that list any more: it answers off ES-DE's
        # settings, which a broken marker says nothing about, and None is a
        # refusal it is allowed to give. Here nothing refuses — no settings
        # file means the frontend's own default applies.
        assert rd.roms_dir() == f"{HOME}/.var/app/net.retrodeck.retrodeck/config/ROMs"

    def test_a_paths_section_of_the_wrong_type_is_invalid_too(self):
        rd = _retrodeck({RETRODECK_JSON: '{"paths": ["/mnt/sd/retrodeck"]}'})
        issue = rd.health().issues[0]
        assert issue.code == atlas.HEALTH_ISSUE_MARKER_INVALID
        assert issue.data["key"] == "paths"

    def test_a_null_path_value_is_invalid(self):
        # JSON null is not "unset" here: RetroDECK writes the key or omits it.
        rd = _retrodeck({RETRODECK_JSON: '{"paths": {"saves_path": null}}'}, dirs=self.FALLBACK_DIRS)
        assert rd.health().codes == (atlas.HEALTH_ISSUE_MARKER_INVALID,)

    def test_a_marker_without_a_paths_section_is_not_invalid(self):
        rd = _retrodeck({RETRODECK_JSON: '{"version": "0.10.9b"}'}, dirs=[f"{HOME}/retrodeck"])
        assert rd.health().codes == (atlas.HEALTH_ISSUE_SAVES_ROOT_MISSING,)

    def test_a_null_paths_section_is_invalid_not_absent(self):
        # Omitting the section and writing null in it are different states: the
        # second is a section no key can be read from, and null is not an object.
        rd = _retrodeck({RETRODECK_JSON: '{"paths": null}'}, dirs=self.FALLBACK_DIRS)
        issue = rd.health().issues[0]
        assert issue.code == atlas.HEALTH_ISSUE_MARKER_INVALID
        assert issue.data["key"] == "paths"

    def test_every_path_key_atlas_reads_is_checked(self):
        # Keeps the checked set honest against the read set: root(), saves_root()
        # and bios_dir() are the reads through _config_path, so those three are
        # validated. roms_path left the set when the ROM root moved to ES-DE's
        # own ROMDirectory — nothing reads it now, and the test below holds that
        # other half.
        for key in ("rd_home_path", "saves_path", "bios_path"):
            rd = _retrodeck({RETRODECK_JSON: json.dumps({"paths": {key: 5}})}, dirs=self.FALLBACK_DIRS)
            assert rd.health().codes == (atlas.HEALTH_ISSUE_MARKER_INVALID,), key

    def test_a_key_atlas_does_not_read_is_none_of_its_business(self):
        # atlas reports on what it reads. A value under a key no read touches
        # says nothing about this installation, and calling the marker broken
        # over it would be a claim about who wrote the file — the day RetroDECK
        # nests something new under paths, a healthy install must stay healthy.
        rd = _retrodeck(
            {
                RETRODECK_JSON: (
                    '{"paths": {"rd_home_path": "/mnt/sd/retrodeck", '
                    '"saves_path": "/mnt/sd/retrodeck/saves", "videos_path": {"nested": 1}}}'
                )
            },
            dirs=["/mnt/sd/retrodeck/saves"],
        )
        assert rd.health() == atlas.Health()
        assert rd.root() == "/mnt/sd/retrodeck"

    def test_roms_path_is_now_such_a_key(self):
        # The scoping rule applied to the key that just left the read set: the
        # ROM root comes off ES-DE's ROMDirectory now, so a roms_path atlas
        # never opens cannot make this installation unhealthy.
        rd = _retrodeck(
            {
                RETRODECK_JSON: (
                    '{"paths": {"rd_home_path": "/mnt/sd/retrodeck", '
                    '"saves_path": "/mnt/sd/retrodeck/saves", "roms_path": 5}}'
                )
            },
            dirs=["/mnt/sd/retrodeck/saves"],
        )
        assert rd.health() == atlas.Health()

    def test_placement_carries_the_marker_issue_instead_of_crashing(self):
        rd = _retrodeck(
            {
                RETRODECK_JSON: '{"paths": {"saves_path": 7}}',
                RETRODECK_CFG: (
                    'savefile_directory = "/mnt/sd/retrodeck/saves"\n'
                    'sort_savefiles_by_content_enable = "false"\nsort_savefiles_enable = "false"\n'
                ),
                "/mnt/sd/retrodeck/saves/.keep": "",
            },
            dirs=self.FALLBACK_DIRS,
        )
        p = placed(rd.savefile_location(content_path="/mnt/sd/retrodeck/roms/gba/Game.zip"))
        assert p.dir == "/mnt/sd/retrodeck/saves"
        # The finding rides in the placement under its own code, carrying the
        # key it is about — not wrapped in a category with the condition nested.
        issue = next(c for c in p.caveats if c.code == atlas.HEALTH_ISSUE_MARKER_INVALID)
        assert issue.data["key"] == "paths.saves_path"


class TestAMarkerThatIsGoneIsStatedNotDetected:
    """``marker-missing`` is the one health code no vector can reach.

    Detection triggers on the marker, so a machine without one has no
    installation to ask — the code exists for the caller who kept a handle (or
    built one) across the marker being moved, renamed, or unmounted, and that
    is a direct-handle question by construction. Hence a test rather than a
    fixture machine: the corpus would have to state an installation that
    ``detect()`` cannot find.
    """

    def test_a_retrodeck_handle_whose_marker_is_gone_says_so(self):
        rd = _retrodeck({})
        assert rd.health().codes[0] == atlas.HEALTH_ISSUE_MARKER_MISSING

    def test_the_finding_names_the_marker_it_looked_for(self):
        issue = _retrodeck({}).health().issues[0]
        assert issue.data["path"] == RETRODECK_JSON

    def test_detection_finds_nothing_there(self):
        # The other half of the reason this is not a vector: the same machine
        # detects no installation at all, so no vector could ask it anything.
        assert atlas.detect(HOME, FixtureMachine({})) == []

    def test_a_bare_retroarch_handle_whose_cfg_is_gone_says_so(self):
        # The bare arrangements have no marker but their cfg, so "missing
        # marker" and "unreadable config" are two findings there, not one.
        bare = atlas.BareRetroArchNative(HOME, FixtureMachine({}))
        assert bare.health().codes == (atlas.HEALTH_ISSUE_MARKER_MISSING,)


class TestTheMarkerVersionIsAKeyAtlasReads:
    """The marker's version drives a comparison, so its type is checked too.

    Marker validation covers exactly the keys atlas reads, and the version is
    one: it is what the arrangement's verified pin is compared against. What a
    bad value must *not* do is take the snapshot down with it — the roots come
    from ``paths`` and are readable whatever stands under ``version``, so the
    defect costs the comparison and nothing else.
    """

    PATHS = {"rd_home_path": "/mnt/sd/retrodeck", "saves_path": "/mnt/sd/retrodeck/saves"}
    DIRS = ["/mnt/sd/retrodeck", "/mnt/sd/retrodeck/saves"]
    CFG = (
        'savefile_directory = "/mnt/sd/retrodeck/saves"\n'
        'sort_savefiles_by_content_enable = "false"\nsort_savefiles_enable = "false"\n'
    )

    def _rd(self, marker: dict[str, object]):
        files = {RETRODECK_JSON: json.dumps({"paths": self.PATHS, **marker}), RETRODECK_CFG: self.CFG}
        return _retrodeck(files, dirs=self.DIRS)

    def test_a_non_string_version_is_a_stated_finding(self):
        issue = self._rd({"version": 11}).health().issues[0]
        assert issue.code == atlas.HEALTH_ISSUE_MARKER_INVALID
        assert issue.data["key"] == "version"

    def test_a_null_version_is_a_finding_too(self):
        # Absent and null are different states here for the same reason they
        # are under paths: null is a value somebody wrote, and it is not one.
        assert self._rd({"version": None}).health().codes == (atlas.HEALTH_ISSUE_MARKER_INVALID,)

    def test_the_roots_survive_it(self):
        rd = self._rd({"version": 11})
        assert rd.root() == "/mnt/sd/retrodeck"
        assert rd.saves_root() == "/mnt/sd/retrodeck/saves"

    def test_the_placement_states_the_finding_and_still_resolves(self):
        placement = placed(
            self._rd({"version": 11}).savefile_location(
                content_path="/mnt/sd/retrodeck/roms/gba/Game.zip"
            )
        )
        assert placement.dir == "/mnt/sd/retrodeck/saves"
        assert atlas.HEALTH_ISSUE_MARKER_INVALID in [c.code for c in placement.caveats]

    def test_no_drift_is_claimed_from_a_version_that_is_not_one(self):
        # Nothing was compared, so nothing is stated — a value atlas refused to
        # read must not come back out as the version this machine runs.
        placement = placed(
            self._rd({"version": 11}).savefile_location(
                content_path="/mnt/sd/retrodeck/roms/gba/Game.zip"
            )
        )
        assert atlas.CAVEAT_ARRANGEMENT_VERSION_DRIFTED not in [c.code for c in placement.caveats]

    def test_a_marker_that_names_no_version_is_healthy(self):
        assert self._rd({}).health() == atlas.Health()

    def test_an_empty_version_is_not_a_defect(self):
        # RetroDECK's shipped default config carries "version": "" and the
        # first run fills it in, so a machine can genuinely present it.
        assert self._rd({"version": ""}).health() == atlas.Health()


class TestRetroDeckSavefileLocation:
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
        p = placed(rd.savefile_location(content_path="/mnt/sd/retrodeck/roms/gba/Game.zip"))
        assert p.dir == "/elsewhere/saves"

    def test_missing_cfg_uses_platform_default(self):
        # Platform defaults are initialized before config load
        # (platform_unix.c:2133-2134); upstream compile defaults sort by core.
        rd = _retrodeck(
            {RETRODECK_JSON: RD_JSON, "/mnt/sd/retrodeck/roms/gba/Game.zip": ""}
        )
        p = placed(rd.savefile_location(content_path="/mnt/sd/retrodeck/roms/gba/Game.zip"))
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
        p = placed(
            rd.savefile_location(
                content_path="/mnt/sd/retrodeck/roms/psp/Game.iso", core_so="ppsspp_libretro.so"
            )
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
        p = placed(
            rd.savefile_location(
                content_path="/mnt/sd/retrodeck/roms/gba/Game.zip", core_so="mgba_libretro.so"
            )
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
        p = placed(
            rd.savefile_location(
                content_path="/mnt/sd/retrodeck/roms/apple2/game.dsk", core_so="applewin_libretro.so"
            )
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
        p = placed(rd.savefile_location(content_path="/mnt/sd/retrodeck/roms/n64/Paper Mario (USA).zip"))
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
        p = placed(rd.savefile_location(content_path="/mnt/sd/retrodeck/roms/gba/Game.zip"))
        assert p.file_set.state == "unknown"
        assert p.file_set.files == ()

    def test_health_caveat_on_missing_root(self):
        rd = _retrodeck(
            {
                RETRODECK_JSON: '{"paths": {"rd_home_path": "/run/media/gone/retrodeck"}}',
            }
        )
        p = placed(rd.savefile_location())
        issue = next(c for c in p.caveats if c.code == atlas.HEALTH_ISSUE_ROOT_MISSING)
        assert issue.data["path"] == "/run/media/gone/retrodeck"

    def test_the_placement_carries_the_health_findings_unchanged(self):
        # Equality, deliberately not identity: handles are live, so health()
        # and savefile_location() each read their own snapshot and build their own
        # Caveat objects — `is` would be false by design, not by defect. What
        # equality pins is everything a re-wrap would disturb, message
        # included: the retired envelope carried a different code, a nested
        # data["issue"], and an "installation health: " prefix, so any of the
        # three coming back fails this.
        rd = _retrodeck({RETRODECK_JSON: '{"paths": {"rd_home_path": "/run/media/gone/retrodeck"}}'})
        findings = rd.health().issues
        assert [c for c in placed(rd.savefile_location()).caveats if c in findings] == list(findings)

    def test_a_finding_reaches_the_placement_with_its_own_message(self):
        # The prefix the envelope added is the cheapest tell that something
        # rebuilt the finding on the way.
        rd = _retrodeck({RETRODECK_JSON: '{"paths": {"rd_home_path": "/run/media/gone/retrodeck"}}'})
        finding = rd.health().issues[0]
        carried = next(c for c in placed(rd.savefile_location()).caveats if c.code == finding.code)
        assert carried.message == finding.message

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
        p = placed(rd.savefile_location(content_path="/mnt/sd/retrodeck/roms/gba/Game [USA].zip"))
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
        p = placed(rd.savefile_location(content_path="/mnt/sd/retrodeck/roms/psx/Game.chd"))
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
        p = placed(rd.savefile_location(content_path="/mnt/sd/retrodeck/roms/gba/Game.zip"))
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
        p = placed(rd.savefile_location(content_path="/mnt/sd/retrodeck/roms/gb/Tetris (World) (Rev 1).zip"))
        assert p.file_set.files == ("Tetris (World) (Rev 1).rtc", "Tetris (World) (Rev 1).srm")


class TestArchiveContentResolves:
    """The consequences of the stem: the observed set and the per-game override."""

    ARCHIVE = "/mnt/sd/retrodeck/roms/gba/Pack.zip"
    ENTRY = f"{ARCHIVE}#Golden Sun (USA).gba"
    CFG = (
        'savefile_directory = "/mnt/sd/retrodeck/saves"\n'
        'sort_savefiles_by_content_enable = "true"\nsort_savefiles_enable = "false"\n'
        'libretro_directory = "/app/cores"\n'
    )

    def _machine(self, extra=None):
        files = {
            RETRODECK_JSON: RD_JSON,
            RETRODECK_CFG: self.CFG,
            self.ARCHIVE: "",
            "/mnt/sd/retrodeck/saves/gba/Golden Sun (USA).srm": "sram",
            **(extra or {}),
        }
        return _retrodeck(files, cores={f"{RD_DEPLOY_CORES}/mgba_libretro.so": {"library_name": "mGBA"}})

    def test_the_entry_names_the_save(self):
        rd = self._machine()
        p = placed(rd.savefile_location(content_path=self.ENTRY, core_so="mgba_libretro.so"))
        assert p.dir == "/mnt/sd/retrodeck/saves/gba"
        assert p.file_set.files == ("Golden Sun (USA).srm",)

    def test_the_game_override_is_found_under_the_entry_name(self):
        rd = self._machine(
            {
                f"{RETRODECK_OVERRIDES}/mGBA/Golden Sun (USA).cfg": (
                    'sort_savefiles_by_content_enable = "false"\n'
                ),
                "/mnt/sd/retrodeck/saves/Golden Sun (USA).srm": "sram",
            }
        )
        p = placed(rd.savefile_location(content_path=self.ENTRY, core_so="mgba_libretro.so"))
        assert p.dir == "/mnt/sd/retrodeck/saves"

    def test_the_archive_itself_is_not_a_save(self):
        # In content-dir mode the archive lies where the save does and carries
        # the same stem when it is named after its entry.
        rd = _retrodeck(
            {
                RETRODECK_JSON: RD_JSON,
                RETRODECK_CFG: (
                    'savefiles_in_content_dir = "true"\n'
                    'sort_savefiles_by_content_enable = "false"\nsort_savefiles_enable = "false"\n'
                ),
                "/mnt/sd/retrodeck/roms/gba/Golden Sun (USA).zip": "",
                "/mnt/sd/retrodeck/roms/gba/Golden Sun (USA).srm": "sram",
            }
        )
        p = placed(
            rd.savefile_location(
                content_path="/mnt/sd/retrodeck/roms/gba/Golden Sun (USA).zip#Golden Sun (USA).gba"
            )
        )
        assert p.file_set.files == ("Golden Sun (USA).srm",)


class TestContentDirObservation:
    """M10: an observation in the ROM's own directory says what it is."""

    CFG = (
        'savefiles_in_content_dir = "true"\n'
        'sort_savefiles_by_content_enable = "false"\nsort_savefiles_enable = "false"\n'
    )
    CUE = "/mnt/sd/retrodeck/roms/psx/Game.cue"

    def _machine(self):
        return _retrodeck(
            {
                RETRODECK_JSON: RD_JSON,
                RETRODECK_CFG: self.CFG,
                self.CUE: "FILE \"Game.bin\" BINARY\n",
                "/mnt/sd/retrodeck/roms/psx/Game.bin": "track",
                "/mnt/sd/retrodeck/roms/psx/Game.png": "cover",
                "/mnt/sd/retrodeck/roms/psx/Game.srm": "sram",
            }
        )

    def test_the_content_siblings_are_stated_not_hidden(self):
        p = placed(self._machine().savefile_location(content_path=self.CUE))
        assert p.file_set.state == "observed"
        assert p.file_set.files == ("Game.bin", "Game.png", "Game.srm")
        assert p.file_set.complete is False

    def test_the_observation_carries_the_caveat_that_says_so(self):
        p = placed(self._machine().savefile_location(content_path=self.CUE))
        caveat = next(c for c in p.caveats if c.code == atlas.CAVEAT_CONTENT_DIR_OBSERVATION)
        assert caveat.data == {"dir": "/mnt/sd/retrodeck/roms/psx"}

    def test_a_trailing_slash_still_filters_the_content_file(self):
        # The ROM is filtered by the name it has on disk, and that name has to
        # survive the trailing slash the rest of the math already normalizes
        # away — otherwise the content file reads as save data.
        with_slash = placed(self._machine().savefile_location(content_path=f"{self.CUE}/"))
        without = placed(self._machine().savefile_location(content_path=self.CUE))
        assert with_slash.file_set.files == without.file_set.files
        assert "Game.cue" not in with_slash.file_set.files

    def test_a_save_directory_of_its_own_carries_no_such_caveat(self):
        rd = _retrodeck(
            {
                RETRODECK_JSON: RD_JSON,
                RETRODECK_CFG: (
                    'savefile_directory = "/mnt/sd/retrodeck/saves"\n'
                    'sort_savefiles_by_content_enable = "false"\nsort_savefiles_enable = "false"\n'
                ),
                self.CUE: "",
                "/mnt/sd/retrodeck/saves/Game.srm": "sram",
            }
        )
        p = placed(rd.savefile_location(content_path=self.CUE))
        assert p.file_set.files == ("Game.srm",)
        assert not any(c.code == atlas.CAVEAT_CONTENT_DIR_OBSERVATION for c in p.caveats)


class TestUnnamedContentPath:
    """L11: a content path RetroArch derives no name from is refused, loudly."""

    CFG = (
        'savefile_directory = "/mnt/sd/retrodeck/saves"\n'
        'sort_savefiles_by_content_enable = "true"\nsort_savefiles_enable = "false"\n'
    )

    def _machine(self):
        return _retrodeck(
            {
                RETRODECK_JSON: RD_JSON,
                RETRODECK_CFG: self.CFG,
                "/mnt/sd/retrodeck/roms/psx/Game/disc.bin": "",
                "/mnt/sd/retrodeck/saves/psx/.hidden": "not a save",
            }
        )

    def test_no_dotfile_is_reported_as_a_save(self):
        p = placed(self._machine().savefile_location(content_path="/mnt/sd/retrodeck/roms/psx/Game/"))
        assert p.file_set.state == "unknown"
        assert p.file_set.files == ()

    def test_the_refusal_is_stated(self):
        p = placed(self._machine().savefile_location(content_path="/mnt/sd/retrodeck/roms/psx/Game/"))
        caveat = next(c for c in p.caveats if c.code == atlas.CAVEAT_CONTENT_PATH_UNNAMED)
        assert caveat.data == {"content_path": "/mnt/sd/retrodeck/roms/psx/Game/"}

    def test_the_directory_is_still_answered(self):
        # The name is missing, not the layout: the sort component is the
        # directory of the last component (file_path.c:493-534).
        p = placed(self._machine().savefile_location(content_path="/mnt/sd/retrodeck/roms/psx/Game/"))
        assert p.dir == "/mnt/sd/retrodeck/saves/psx"

    def test_an_empty_content_path_is_answered_not_raised(self):
        # An empty string fills no coordinate at all, so every hole stays a
        # hole — and the answer says why it was not filled. Domain states never
        # raise, and a content-directory root would otherwise build an empty
        # dir (which SavefilePlacement refuses).
        rd = _retrodeck(
            {
                RETRODECK_JSON: RD_JSON,
                RETRODECK_CFG: (
                    'savefiles_in_content_dir = "true"\n'
                    'sort_savefiles_by_content_enable = "false"\nsort_savefiles_enable = "false"\n'
                ),
                "/mnt/sd/retrodeck/saves/.keep": "",
            }
        )
        p = placed(rd.savefile_location(content_path=""))
        assert p.dir == "<content_dir>"
        assert p.needs == ("content_dir",)
        assert p.file_set.state == "unknown"
        assert any(c.code == atlas.CAVEAT_CONTENT_PATH_UNNAMED for c in p.caveats)


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
        p = placed(rd.savefile_location(content_path="/mnt/sd/retrodeck/roms/gba/Game.zip"))
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
        p = placed(rd.savefile_location(content_path="/mnt/sd/retrodeck/roms/gba/Game.zip"))
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
        p = placed(rd.savefile_location(content_path="/mnt/sd/retrodeck/roms/gba/Game.zip"))
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
        p = placed(rd.savefile_location(content_path="/mnt/sd/retrodeck/roms/gba/Game.zip"))
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
        machine = FixtureMachine(
            {
                f"{HOME}/.config/retroarch/retroarch.cfg": self.FLAT_CFG,
                "/data/real-saves/Game.srm": "s",
                f"{HOME}/roms/gba/Game.zip": "",
            },
            symlinks={f"{HOME}/links/saves": "/data/real-saves"},
        )
        p = placed(atlas.BareRetroArchNative(HOME, machine).savefile_location(content_path=f"{HOME}/roms/gba/Game.zip"))
        assert p.dir == f"{HOME}/links/saves"
        assert p.physical_dir == "/data/real-saves"

    def test_dead_link_in_card_directory_is_stated(self):
        # The LRPS2 dir_prep case: the memcards link points into an unmounted
        # volume — the emulator-side path is dead and the answer says so.
        machine = FixtureMachine(
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
        p = placed(
            atlas.RetroDeck(HOME, machine).savefile_location(
                content_path="/mnt/sd/retrodeck/roms/ps2/Game.iso", core_so="pcsx2_libretro.so"
            )
        )
        assert p.dir == "/mnt/sd/retrodeck/bios/pcsx2/memcards"
        assert p.physical_dir is None
        dead = [c for c in p.caveats if c.code == atlas.CAVEAT_DEAD_SYMLINK]
        assert dead
        assert dead[0].data["link"] == "/mnt/sd/retrodeck/bios/pcsx2/memcards"

    def test_rejected_save_root_through_dead_link_says_why(self):
        machine = FixtureMachine(
            {
                f"{HOME}/.config/retroarch/retroarch.cfg": self.FLAT_CFG,
                f"{HOME}/roms/gba/Game.zip": "",
            },
            symlinks={f"{HOME}/links/saves": "/run/media/gone/saves"},
        )
        p = placed(atlas.BareRetroArchNative(HOME, machine).savefile_location(content_path=f"{HOME}/roms/gba/Game.zip"))
        assert p.dir == f"{HOME}/.config/retroarch/saves"
        codes = [c.code for c in p.caveats]
        assert atlas.CAVEAT_INVALID_SAVE_DIRECTORY in codes
        assert atlas.CAVEAT_DEAD_SYMLINK in codes

    def test_rejected_save_root_through_a_symlink_loop_says_why(self):
        """A chain that never settles is stated, not half-followed.

        The kernel answers ELOOP for the whole resolution — there is no path it
        got partway to. A resolver that handed back where it stopped would name
        a directory nothing can open as the physical one, and say nothing about
        it: the caller would read an ordinary answer. It is its own code because
        a dead link points at a name somebody can create, while a loop is a
        cycle somebody has to break.
        """
        machine = FixtureMachine(
            {
                f"{HOME}/.config/retroarch/retroarch.cfg": self.FLAT_CFG,
                f"{HOME}/roms/gba/Game.zip": "",
            },
            symlinks={f"{HOME}/links/saves": "/data/a", "/data/a": "/data/b", "/data/b": "/data/a"},
        )
        p = placed(atlas.BareRetroArchNative(HOME, machine).savefile_location(content_path=f"{HOME}/roms/gba/Game.zip"))
        assert p.dir == f"{HOME}/.config/retroarch/saves"
        looping = [c for c in p.caveats if c.code == atlas.CAVEAT_SYMLINK_LOOP]
        assert looping
        assert looping[0].data["link"] == f"{HOME}/links/saves"
        assert atlas.CAVEAT_DEAD_SYMLINK not in [c.code for c in p.caveats]

    @pytest.mark.parametrize("length, settles", [(SYMLINK_HOPS, True), (SYMLINK_HOPS + 1, False)])
    def test_the_link_view_gives_up_on_the_hop_the_kernel_does(self, length, settles):
        """The resolver behind ``physical_dir`` stops where the seam's does.

        A chain of exactly ``SYMLINK_HOPS`` links resolves on a real filesystem
        and one link more answers ELOOP, so this reads the same constant the
        seam does rather than a second copy of the number — a chain that
        resolved in the seam and not here would report *no physical directory*
        for a place the emulator writes to perfectly well.
        """
        chain = {f"{HOME}/links/saves": "/c/l1", f"/c/l{length - 1}": "/data/real-saves"}
        chain.update({f"/c/l{i}": f"/c/l{i + 1}" for i in range(1, length - 1)})
        machine = FixtureMachine(
            {
                f"{HOME}/.config/retroarch/retroarch.cfg": self.FLAT_CFG,
                "/data/real-saves/Game.srm": "s",
                f"{HOME}/roms/gba/Game.zip": "",
            },
            symlinks=chain,
        )
        p = placed(atlas.BareRetroArchNative(HOME, machine).savefile_location(content_path=f"{HOME}/roms/gba/Game.zip"))
        assert (p.physical_dir == "/data/real-saves") is settles
        assert (atlas.CAVEAT_SYMLINK_LOOP in [c.code for c in p.caveats]) is not settles


class TestSystemDirectoryRoot:
    """M6: a card rooted in the system directory answers the root the core is handed.

    ``RETRO_ENVIRONMENT_GET_SYSTEM_DIRECTORY`` (runloop.c:1958-1999) is not a
    read of ``system_directory``: the flag, and an emptied key, send the core to
    the content's own directory, and an absent key resolves to RetroArch's
    platform default. None of those is a hole — ``needs`` only ever carries what
    the caller fills from the content.
    """

    CONTENT = "/mnt/sd/retrodeck/roms/dreamcast/Game (Europe).gdi"
    SYSTEM_DIR_CFG = 'system_directory = "/mnt/sd/retrodeck/bios"\n'
    BASE_CFG = (
        'savefile_directory = "/mnt/sd/retrodeck/saves"\n'
        'sort_savefiles_by_content_enable = "false"\nsort_savefiles_enable = "false"\n'
        'global_core_options = "true"\nlibretro_directory = "/app/cores"\n'
    )
    VMU = "vmu_save_A1.bin"

    def _flycast(self, cfg, files=None, **kwargs):
        return _retrodeck(
            {
                RETRODECK_JSON: RD_JSON,
                RETRODECK_CFG: self.BASE_CFG + cfg,
                f"{HOME}/.var/app/net.retrodeck.retrodeck/config/retroarch/retroarch-core-options.cfg": (
                    'reicast_per_content_vmus = "disabled"\n'
                ),
                self.CONTENT: "",
                "/mnt/sd/retrodeck/saves/.keep": "",
                **(files or {}),
            },
            cores={f"{RD_DEPLOY_CORES}/flycast_libretro.so": {"library_name": "Flycast"}},
            **kwargs,
        )

    def _placement(self, cfg, files=None, content: str | None = CONTENT, **kwargs):
        return placed(
            self._flycast(cfg, files, **kwargs).savefile_location(
                content_path=content, core_so="flycast_libretro.so"
            )
        )

    def test_the_configured_directory_is_the_root_when_nothing_moves_it(self):
        p = self._placement(self.SYSTEM_DIR_CFG, {f"/mnt/sd/retrodeck/bios/dc/{self.VMU}": "v"})
        assert p.dir == "/mnt/sd/retrodeck/bios/dc"
        assert p.root_kind == atlas.ROOT_SYSTEM_DIRECTORY
        assert p.file_set.files == (self.VMU,)

    def test_system_files_in_the_content_dir_move_the_root_to_the_content(self):
        p = self._placement(
            self.SYSTEM_DIR_CFG + 'systemfiles_in_content_dir = "true"\n',
            {f"/mnt/sd/retrodeck/roms/dreamcast/dc/{self.VMU}": "v"},
        )
        assert p.dir == "/mnt/sd/retrodeck/roms/dreamcast/dc"
        assert p.root_kind == atlas.ROOT_CONTENT_DIRECTORY
        assert p.file_set.state == "observed"

    def test_without_content_the_content_root_is_a_hole_the_caller_can_fill(self):
        p = self._placement(
            self.SYSTEM_DIR_CFG + 'systemfiles_in_content_dir = "true"\n', content=None
        )
        assert p.dir == "<content_dir>/dc"
        assert p.needs == ("content_dir",)
        assert p.file_set.state == "declared"

    def test_an_override_can_set_the_flag_too(self):
        # The flag is read through the merged config like every other key —
        # not from the global cfg alone.
        p = self._placement(
            self.SYSTEM_DIR_CFG,
            {
                f"{RETRODECK_OVERRIDES}/Flycast/Flycast.cfg": 'systemfiles_in_content_dir = "true"\n',
                f"/mnt/sd/retrodeck/roms/dreamcast/dc/{self.VMU}": "v",
            },
        )
        assert p.dir == "/mnt/sd/retrodeck/roms/dreamcast/dc"

    def test_a_blank_system_directory_hands_the_core_the_content_dir(self):
        # config_get_path copies an empty value through (config_file.c:1202-1216)
        # because system_directory passes handle_setting=true — the opposite of
        # savefile_directory, where blank keeps the standing root.
        p = self._placement(
            'system_directory = ""\n', {f"/mnt/sd/retrodeck/roms/dreamcast/dc/{self.VMU}": "v"}
        )
        assert p.dir == "/mnt/sd/retrodeck/roms/dreamcast/dc"
        assert p.root_kind == atlas.ROOT_CONTENT_DIRECTORY

    def test_a_cleared_key_without_content_leaves_the_content_hole_too(self):
        # Upstream's own no-content fallback (runloop.c:1986-1987) hands back the
        # empty value — nothing a save can be placed in. The template is.
        p = self._placement('system_directory = ""\n', content=None)
        assert p.dir == "<content_dir>/dc"
        assert p.needs == ("content_dir",)

    def test_the_literal_default_clears_it_the_same_way(self):
        p = self._placement(
            'system_directory = "default"\n', {f"/mnt/sd/retrodeck/roms/dreamcast/dc/{self.VMU}": "v"}
        )
        assert p.dir == "/mnt/sd/retrodeck/roms/dreamcast/dc"

    def test_an_unset_key_resolves_to_the_platform_default(self):
        # config_set_defaults put 'system' under the config tree there before
        # any cfg was read (configuration.c:5746-5749, platform_unix.c:2142-2143).
        config_tree = f"{HOME}/.var/app/net.retrodeck.retrodeck/config/retroarch"
        p = self._placement("", {f"{config_tree}/system/dc/{self.VMU}": "v"})
        assert p.dir == f"{config_tree}/system/dc"
        assert p.root_kind == atlas.ROOT_SYSTEM_DIRECTORY
        assert p.file_set.state == "observed"
        assert p.needs == ()
        # Nothing to refuse: an absent key resolves, so neither route states a
        # refusal for it — the cleared key is the one that still does.
        assert atlas.CAVEAT_SYSTEM_DIRECTORY_CLEARED not in [c.code for c in p.caveats]

    def test_a_dropped_system_directory_line_is_stated(self):
        # The line sets nothing, so the platform default stands — silently,
        # unless the answer says the file tried to say otherwise.
        p = self._placement('system_directory "/mnt/sd/retrodeck/bios"\n')
        dropped = next(c for c in p.caveats if c.code == atlas.CAVEAT_CFG_LINE_DROPPED)
        assert dropped.data["key"] == "system_directory"
        assert p.dir.endswith("/config/retroarch/system/dc")

    def test_a_rejected_flag_value_is_stated_and_changes_nothing(self):
        p = self._placement(
            self.SYSTEM_DIR_CFG + 'systemfiles_in_content_dir = "yes"\n',
            {f"/mnt/sd/retrodeck/bios/dc/{self.VMU}": "v"},
        )
        assert p.dir == "/mnt/sd/retrodeck/bios/dc"
        rejected = next(c for c in p.caveats if c.code == atlas.CAVEAT_CFG_VALUE_REJECTED)
        assert rejected.data["key"] == "systemfiles_in_content_dir"

    def test_a_sandbox_only_directory_is_stated_as_configured_and_not_observed(self):
        # The emulator writes there; atlas cannot look. Naming the spelling the
        # emulator uses is the answer — a hole would ask the caller for a value
        # no caller has.
        p = self._placement('system_directory = "/var/db/bios"\n')
        assert p.dir == "/var/db/bios/dc"
        assert p.needs == ()
        assert p.file_set.state == "declared"
        caveat = next(c for c in p.caveats if c.code == atlas.CAVEAT_SANDBOX_PATH_UNTRANSLATED)
        assert caveat.data == {"key": "system_directory", "path": "/var/db/bios"}

    def test_an_application_relative_directory_is_stated_unexpanded(self):
        p = self._placement('system_directory = ":/system"\n')
        assert p.dir == ":/system/dc"
        assert p.needs == ()
        assert any(c.code == atlas.CAVEAT_APP_RELATIVE_PATH_UNEXPANDED for c in p.caveats)

    def test_the_cross_root_parts_follow_the_resolved_system_root(self):
        # 'VMU A1' moves port A1 to the save directory and leaves the rest on
        # the shared card — which the flag has just moved to the content's own
        # directory. The caveat names the directory as this machine resolves
        # it; a consumer following the cfg key back would look where this core
        # no longer writes.
        def spanning(flag):
            rd = self._flycast(
                self.SYSTEM_DIR_CFG + flag,
                {
                    f"{HOME}/.var/app/net.retrodeck.retrodeck/config/retroarch/"
                    "retroarch-core-options.cfg": 'reicast_per_content_vmus = "VMU A1"\n'
                },
            )
            p = placed(rd.savefile_location(content_path=self.CONTENT, core_so="flycast_libretro.so"))
            return next(c for c in p.caveats if c.code == atlas.CAVEAT_FILE_SET_SPANS_ROOTS)

        assert spanning("").data["dir"] == "/mnt/sd/retrodeck/bios/dc"
        content_dir = spanning('systemfiles_in_content_dir = "true"\n').data["dir"]
        assert content_dir == self.CONTENT.rsplit("/", 1)[0] + "/dc"

    def test_no_configured_directory_is_ever_a_hole(self):
        for cfg in ("", 'system_directory = ""\n', 'system_directory = "/var/db/bios"\n'):
            p = self._placement(cfg)
            assert "system_directory" not in p.needs, cfg


class TestScreenshotLocation:
    """The screenshot family's own two rules the vectors do not carry."""

    CFG = (
        'savefile_directory = "/mnt/sd/retrodeck/saves"\n'
        'libretro_directory = "/app/cores"\n'
    )
    ROM = "/mnt/sd/retrodeck/roms/n64/Game (Europe).z64"

    def _handle(self, cfg_extra, dirs=()):
        machine = FixtureMachine(
            {
                f"{HOME}/.var/app/net.retrodeck.retrodeck/config/retrodeck/retrodeck.json": (
                    '{"paths": {"rd_home_path": "/mnt/sd/retrodeck", '
                    '"saves_path": "/mnt/sd/retrodeck/saves"}}'
                ),
                f"{HOME}/.var/app/net.retrodeck.retrodeck/config/retroarch/retroarch.cfg": (
                    self.CFG + cfg_extra
                ),
                self.ROM: "",
            },
            dirs=["/mnt/sd/retrodeck/saves", *dirs],
        )
        return atlas.RetroDeck(HOME, machine)

    def test_the_in_content_flag_outranks_a_configured_directory(self):
        p = self._handle(
            'screenshot_directory = "/mnt/sd/retrodeck/screenshots"\n'
            'screenshots_in_content_dir = "true"\n',
            dirs=("/mnt/sd/retrodeck/screenshots",),
        ).screenshot_location(content_path=self.ROM)
        assert not isinstance(p, atlas.Unresolved)
        assert p.root_kind == "content_directory"
        assert p.dir == "/mnt/sd/retrodeck/roms/n64"

    def test_the_default_literal_counts_as_unset(self):
        p = self._handle('screenshot_directory = "default"\n').screenshot_location(
            content_path=self.ROM
        )
        assert not isinstance(p, atlas.Unresolved)
        assert p.root_kind == "content_directory"
        assert not any(
            c.code == atlas.CAVEAT_INVALID_SCREENSHOT_DIRECTORY for c in p.caveats
        )

    def test_sorting_without_content_keeps_the_hole(self):
        p = self._handle(
            'screenshot_directory = "/mnt/sd/retrodeck/screenshots"\n'
            'sort_screenshots_by_content_enable = "true"\n',
            dirs=("/mnt/sd/retrodeck/screenshots",),
        ).screenshot_location()
        assert not isinstance(p, atlas.Unresolved)
        assert p.dir == "/mnt/sd/retrodeck/screenshots/<content_dir_name>"
        assert p.needs == ("content_dir_name",)


DOLPHIN_INI_PATH = f"{HOME}/.var/app/net.retrodeck.retrodeck/config/dolphin-emu/Dolphin.ini"
DOLPHIN_ESDE = (
    "/var/lib/flatpak/app/net.retrodeck.retrodeck/current/active/files/retrodeck/"
    "components/es-de/share/es-de/resources/systems/linux/es_systems.xml"
)
DOLPHIN_SYSTEMS = (
    '<?xml version="1.0"?><systemList>'
    "<system><name>gc</name><path>%ROMPATH%/gc</path><extension>.rvz</extension>"
    '<command label="Dolphin (Standalone)">%EMULATOR_DOLPHIN% -b -e %ROM%</command></system>'
    "<system><name>wii</name><path>%ROMPATH%/wii</path><extension>.rvz</extension>"
    '<command label="Dolphin (Standalone)">%EMULATOR_DOLPHIN% -b -e %ROM%</command></system>'
    "</systemList>"
)
DOLPHIN_GC_USER = f"{HOME}/.var/app/net.retrodeck.retrodeck/data/dolphin-emu/GC"


class TestDolphinStandaloneSaves:
    """The corners of the Dolphin card the vector family does not carry."""

    BASE = {
        RETRODECK_JSON: RD_JSON,
        RETRODECK_CFG: 'savefile_directory = "/mnt/sd/retrodeck/saves"\n'
        'libretro_directory = "/app/cores"\n',
        DOLPHIN_ESDE: DOLPHIN_SYSTEMS,
    }

    def _answer(self, ini, system="gc"):
        files = dict(self.BASE)
        if ini is not None:
            files[DOLPHIN_INI_PATH] = ini
        rd = _retrodeck(files, dirs=["/mnt/sd/retrodeck/saves"])
        entry = rd.emulators_for(system).entries[0]
        return entry.savefile_location()

    def test_a_missing_ini_answers_from_the_registered_defaults(self):
        # SlotA defaults to the GCI folder and SlotB to nothing
        # (MainSettings.cpp:133-136) — a fresh Dolphin answers, it does not refuse.
        p = self._answer(None)
        assert not isinstance(p, atlas.Unresolved)
        assert p.root_kind == atlas.ROOT_EMULATOR_DIRECTORY
        assert p.needs == (atlas.HOLE_REGION,)
        assert p.granularity is not None
        assert p.granularity.mode == "folder+none"

    def test_no_card_in_either_slot_keeps_no_save(self):
        p = self._answer("[Core]\nSlotA = 255\nSlotB = 255\n")
        assert not isinstance(p, atlas.Unresolved)
        assert p.file_set.files == ()
        stated = [c for c in p.caveats if c.code == atlas.CAVEAT_SAVE_WRITES_DISCARDED]
        assert stated
        assert stated[0].data["mode"] == "none+none"

    def test_an_uninterpreted_slot_device_is_refused_not_guessed(self):
        p = self._answer("[Core]\nSlotA = 7\nSlotB = 255\n")
        assert not isinstance(p, atlas.Unresolved)
        stated = [c for c in p.caveats if c.code == atlas.CAVEAT_CORE_MODE_UNESTABLISHED]
        assert stated
        assert "uninterpreted" in stated[0].data["reason"]

    def test_a_configured_card_path_is_a_region_template(self):
        # The emulator replaces the region code in a configured filename
        # (GetMemcardPath, MainSettings.cpp:777-819) — so does the answer.
        p = self._answer(
            "[Core]\nSlotA = 1\nSlotB = 255\nMemcardAPath = /mnt/sd/cards/mine.USA.raw\n"
        )
        assert not isinstance(p, atlas.Unresolved)
        assert p.file_set.files == ("mine.USA.raw", "mine.EUR.raw", "mine.JAP.raw")
        assert p.file_set.groups[0].dir == "/mnt/sd/cards"

    def test_a_second_slot_adds_its_own_groups(self):
        p = self._answer("[Core]\nSlotA = 8\nSlotB = 1\n")
        assert not isinstance(p, atlas.Unresolved)
        assert p.granularity is not None
        assert p.granularity.mode == "folder+card"
        dirs = {g.dir for g in p.file_set.groups if g.files is not None}
        assert dirs == {DOLPHIN_GC_USER}
        assert len([g for g in p.file_set.groups if g.files is None]) == 3

    def test_a_session_override_says_the_cards_live_elsewhere_while_it_runs(self):
        p = self._answer(
            "[Core]\nSlotA = 8\nSlotB = 255\nGCIFolderAPathOverride = /tmp/movie\n"
        )
        assert not isinstance(p, atlas.Unresolved)
        stated = [c for c in p.caveats if c.code == atlas.CAVEAT_CORE_MODE_UNESTABLISHED]
        assert stated
        assert "GCIFolderAPathOverride" in stated[0].data["reason"]

    def test_the_savestate_question_answers_the_compiled_tree(self):
        # The refusal this asserted outlived its reason (#225): the states
        # tree is a compiled join below the emulator's own data tree
        # (FileUtil.cpp:852 at 2603a), so the card answers it with the naming
        # pattern in the caveat that says why no file can be listed.
        files = dict(self.BASE)
        rd = _retrodeck(files, dirs=["/mnt/sd/retrodeck/saves"])
        entry = rd.emulators_for("gc").entries[0]
        p = entry.savestate_location()
        assert isinstance(p, atlas.SavestatePlacement)
        assert p.dir == f"{HOME}/.var/app/net.retrodeck.retrodeck/data/dolphin-emu/StateSaves"
        assert p.root_kind == "emulator_directory"
        assert p.file_set.state == "unknown"
        named = next(c for c in p.caveats if c.code == atlas.CAVEAT_FILE_NAMES_UNESTABLISHED)
        assert named.data["pattern"] == "<game_id>.s<slot>"
        assert named.data["citation"] == "State.cpp:304-308"


TRIO_ESDE = (
    '<?xml version="1.0"?><systemList>'
    "<system><name>psp</name><path>%ROMPATH%/psp</path><extension>.iso</extension>"
    '<command label="PPSSPP (Standalone)">%EMULATOR_PPSSPP% %ROM%</command></system>'
    "<system><name>xbox</name><path>%ROMPATH%/xbox</path><extension>.iso</extension>"
    '<command label="xemu (Standalone)">%EMULATOR_XEMU% %ROM%</command></system>'
    "<system><name>wiiu</name><path>%ROMPATH%/wiiu</path><extension>.wua</extension>"
    '<command label="Cemu (Standalone)">%EMULATOR_CEMU% --mlc /tmp/other %ROM%</command></system>'
    "<system><name>n3ds</name><path>%ROMPATH%/n3ds</path><extension>.3ds</extension>"
    '<command label="Azahar (Standalone)">%EMULATOR_AZAHAR% %ROM%</command></system>'
    "<system><name>psx</name><path>%ROMPATH%/psx</path><extension>.chd</extension>"
    '<command label="DuckStation (Legacy) (Standalone)">%EMULATOR_DUCKSTATION% -batch %ROM%</command></system>'
    "<system><name>ps2</name><path>%ROMPATH%/ps2</path><extension>.chd</extension>"
    '<command label="PCSX2 (Standalone)">%EMULATOR_PCSX2% -batch %ROM%</command></system>'
    "<system><name>nds</name><path>%ROMPATH%/nds</path><extension>.nds .zip</extension>"
    '<command label="melonDS (Standalone)">%EMULATOR_MELONDS% %ROM%</command></system>'
    "</systemList>"
)
# xemu opens its configuration under the data home; a config-tree copy is the
# arrangement's own arrangement, reached only through the link it makes.
XEMU_TOML_PATH = f"{HOME}/.var/app/net.retrodeck.retrodeck/data/xemu/xemu/xemu.toml"
XEMU_CONFIG_TREE_TOML = f"{HOME}/.var/app/net.retrodeck.retrodeck/config/xemu/xemu.toml"
CEMU_XML_PATH = f"{HOME}/.var/app/net.retrodeck.retrodeck/config/Cemu/settings.xml"
AZAHAR_INI_PATH = f"{HOME}/.var/app/net.retrodeck.retrodeck/config/azahar-emu/qt-config.ini"
DUCKSTATION_CONFIG_INI = f"{HOME}/.var/app/net.retrodeck.retrodeck/config/duckstation/settings.ini"
PCSX2_INI_PATH = f"{HOME}/.var/app/net.retrodeck.retrodeck/config/PCSX2/inis/PCSX2.ini"
MELONDS_TOML_PATH = f"{HOME}/.var/app/net.retrodeck.retrodeck/config/melonDS/melonDS.toml"
MELONDS_INI_PATH = f"{HOME}/.var/app/net.retrodeck.retrodeck/config/melonDS/melonDS.ini"


class TestMoreStandaloneSaves:
    """The ppsspp/xemu/cemu corners the vector family does not carry."""

    BASE = {
        RETRODECK_JSON: RD_JSON,
        RETRODECK_CFG: 'savefile_directory = "/mnt/sd/retrodeck/saves"\n'
        'libretro_directory = "/app/cores"\n',
        DOLPHIN_ESDE: TRIO_ESDE,
    }

    def _answer(self, system, files=None, **kwargs):
        rd = _retrodeck({**self.BASE, **(files or {})}, dirs=["/mnt/sd/retrodeck/saves"], **kwargs)
        return rd.emulators_for(system).entries[0].savefile_location()

    def test_ppsspp_answers_from_the_build_alone(self):
        # No config governs the memstick on Linux — the answer needs no file.
        p = self._answer("psp")
        assert not isinstance(p, atlas.Unresolved)
        assert p.dir.endswith("/config/ppsspp/PSP/SAVEDATA")
        assert p.granularity is not None
        assert p.granularity.readings == ()

    def test_xemu_without_a_toml_states_the_missing_disk(self):
        p = self._answer("xbox")
        assert isinstance(p, atlas.Unresolved)
        assert p.code == atlas.UNRESOLVED_EMULATOR_CONFIG_UNREADABLE

    def test_xemu_with_a_disk_carries_the_inside_image_layout(self):
        p = self._answer(
            "xbox",
            files={XEMU_TOML_PATH: "[sys.files]\nhdd_path = '/mnt/sd/hdd.qcow2'\n"},
        )
        assert not isinstance(p, atlas.Unresolved)
        stated = [c for c in p.caveats if c.code == atlas.CAVEAT_SAVE_INSIDE_IMAGE]
        assert stated
        assert stated[0].data["image"] == "/mnt/sd/hdd.qcow2"
        assert stated[0].data["layout"] == "UDATA/<title id>"

    def test_xemu_without_an_hdd_path_refuses_to_invent_a_disk(self):
        p = self._answer(
            "xbox",
            files={
                XEMU_TOML_PATH: "[sys.files]\neeprom_path = '/mnt/sd/eeprom.bin'\n"
            },
        )
        assert not isinstance(p, atlas.Unresolved)
        stated = [c for c in p.caveats if c.code == atlas.CAVEAT_CORE_MODE_UNESTABLISHED]
        assert stated
        assert "hdd_path" in stated[0].data["reason"]

    def test_xemu_does_not_read_a_config_tree_copy_no_link_reaches(self):
        # The file sits where a distribution likes to keep it and nothing links
        # it to where xemu opens it, so this launch has no configuration at all.
        p = self._answer(
            "xbox",
            files={XEMU_CONFIG_TREE_TOML: "[sys.files]\nhdd_path = '/mnt/sd/hdd.qcow2'\n"},
        )
        assert isinstance(p, atlas.Unresolved)
        assert p.code == atlas.UNRESOLVED_EMULATOR_CONFIG_UNREADABLE

    def test_xemu_with_unparseable_toml_refuses(self):
        p = self._answer("xbox", files={XEMU_TOML_PATH: "[sys.files\nnot toml"})
        assert isinstance(p, atlas.Unresolved)
        assert p.code == atlas.UNRESOLVED_EMULATOR_CONFIG_UNREADABLE

    def test_cemu_without_a_settings_xml_answers_the_default_mlc(self):
        # The catalogue command carries --mlc, which outranks the config — the
        # answer still stands, with the caveat saying so.
        p = self._answer("wiiu")
        assert not isinstance(p, atlas.Unresolved)
        assert p.dir.endswith("/data/Cemu/mlc01/usr/save/<save_id>")
        assert p.needs == ("save_id",)
        assert p.granularity is not None
        assert p.granularity.value == atlas.GRANULARITY_PER_GAME_DIRECTORY
        stated = [c for c in p.caveats if c.code == atlas.CAVEAT_CORE_MODE_UNESTABLISHED]
        assert stated
        assert "--mlc" in stated[0].data["reason"]

    def test_cemu_with_unparseable_xml_refuses(self):
        p = self._answer("wiiu", files={CEMU_XML_PATH: "<content><mlc_path>"})
        assert isinstance(p, atlas.Unresolved)
        assert p.code == atlas.UNRESOLVED_EMULATOR_CONFIG_UNREADABLE

    def test_azahar_a_default_marked_directory_is_read_as_the_default(self):
        # `<key>\default=true` makes the compiled default win over any stored
        # value (ReadSetting, config.cpp:1442-1450) — a stored path with the
        # marker set must NOT govern.
        p = self._answer(
            "n3ds",
            files={
                AZAHAR_INI_PATH: (
                    "[Data%20Storage]\nuse_custom_storage=true\n"
                    "use_custom_storage\\default=true\n"
                    "sdmc_directory=/mnt/elsewhere/sdmc/\nsdmc_directory\\default=false\n"
                )
            },
        )
        assert not isinstance(p, atlas.Unresolved)
        assert "/data/azahar-emu/sdmc/" in p.dir
        assert p.dir.endswith("/title/<save_id>/data/00000001")
        assert p.needs == ("save_id",)

    def test_azahar_custom_storage_with_an_empty_path_leaves_the_default(self):
        # UpdateUserPath returns on an empty path — custom switched on with
        # nothing configured changes nothing.
        p = self._answer(
            "n3ds",
            files={
                AZAHAR_INI_PATH: (
                    "[Data%20Storage]\nuse_custom_storage=true\n"
                    "use_custom_storage\\default=false\n"
                )
            },
        )
        assert not isinstance(p, atlas.Unresolved)
        assert "/data/azahar-emu/sdmc/" in p.dir

    def test_azahar_with_the_virtual_sd_off_says_so(self):
        p = self._answer(
            "n3ds",
            files={
                AZAHAR_INI_PATH: (
                    "[Data%20Storage]\nuse_virtual_sd=false\nuse_virtual_sd\\default=false\n"
                )
            },
        )
        assert not isinstance(p, atlas.Unresolved)
        stated = [c for c in p.caveats if c.code == atlas.CAVEAT_CORE_MODE_UNESTABLISHED]
        assert stated
        assert "use_virtual_sd" in stated[0].data["reason"]

    def test_azahar_with_an_unreadable_ini_refuses(self):
        p = self._answer("n3ds", files={AZAHAR_INI_PATH: {"status": "unreadable"}})
        assert isinstance(p, atlas.Unresolved)
        assert p.code == atlas.UNRESOLVED_EMULATOR_CONFIG_UNREADABLE

    def test_azahar_the_extdata_tree_rides_as_its_own_group(self):
        p = self._answer("n3ds")
        assert not isinstance(p, atlas.Unresolved)
        extdata = [g for g in p.file_set.groups if g.dir.endswith("/extdata")]
        assert len(extdata) == 1
        assert extdata[0].files is None

    def test_duckstation_the_config_side_ini_outranks_the_data_side(self):
        # Both DataRoot candidates carry a settings.ini — the probe reads the
        # config side first, the order the launch environment implies
        # (qthost.cpp:562-582), and the readings say which file answered.
        p = self._answer(
            "psx",
            files={
                DUCKSTATION_CONFIG_INI: (
                    "[MemoryCards]\nCard1Type = PerGame\nCard2Type = None\n"
                    "Directory = /mnt/sd/config-side\n"
                ),
                f"{HOME}/.var/app/net.retrodeck.retrodeck/data/duckstation/settings.ini": (
                    "[MemoryCards]\nCard1Type = Shared\nCard2Type = None\n"
                    "Directory = /mnt/sd/data-side\n"
                ),
            },
        )
        assert not isinstance(p, atlas.Unresolved)
        assert p.dir == "/mnt/sd/config-side"
        assert p.file_set.files == ("<save_id>_1.mcd",)

    def test_duckstation_a_shared_slot_takes_its_configured_absolute_path(self):
        p = self._answer(
            "psx",
            files={
                DUCKSTATION_CONFIG_INI: (
                    "[MemoryCards]\nCard1Type = Shared\nCard2Type = None\n"
                    "Card1Path = /mnt/sd/cards/everyone.mcd\n"
                    "Directory = /mnt/sd/memcards\n"
                ),
            },
        )
        assert not isinstance(p, atlas.Unresolved)
        assert p.dir == "/mnt/sd/cards"
        assert p.file_set.files == ("everyone.mcd",)
        assert p.granularity is not None
        assert p.granularity.value == atlas.GRANULARITY_SHARED_CARD

    def test_duckstation_a_relative_shared_path_joins_the_memcard_directory(self):
        p = self._answer(
            "psx",
            files={
                DUCKSTATION_CONFIG_INI: (
                    "[MemoryCards]\nCard1Type = Shared\nCard2Type = None\n"
                    "Card1Path = my_card.mcd\nDirectory = /mnt/sd/memcards\n"
                ),
            },
        )
        assert not isinstance(p, atlas.Unresolved)
        assert p.dir == "/mnt/sd/memcards"
        assert p.file_set.files == ("my_card.mcd",)

    def test_duckstation_two_empty_slots_state_the_discarded_writes(self):
        p = self._answer(
            "psx",
            files={
                DUCKSTATION_CONFIG_INI: (
                    "[MemoryCards]\nCard1Type = None\nCard2Type = NonPersistent\n"
                    "Directory = /mnt/sd/memcards\n"
                ),
            },
        )
        assert not isinstance(p, atlas.Unresolved)
        assert p.file_set.groups == ()
        assert p.granularity is not None
        assert p.granularity.value == atlas.GRANULARITY_NONE
        stated = [c for c in p.caveats if c.code == atlas.CAVEAT_SAVE_WRITES_DISCARDED]
        assert len(stated) == 2  # the NonPersistent slot's own, and the answer's

    def test_duckstation_an_unknown_type_falls_to_the_compiled_default(self):
        # ParseMemoryCardTypeName -> .value_or(default) (settings.cpp:391-398):
        # an unparseable value is the default, stated in the reading.
        p = self._answer(
            "psx",
            files={
                DUCKSTATION_CONFIG_INI: (
                    "[MemoryCards]\nCard1Type = SomethingNew\nCard2Type = None\n"
                    "Directory = /mnt/sd/memcards\n"
                ),
            },
        )
        assert not isinstance(p, atlas.Unresolved)
        assert p.file_set.files == ("<save_id>_1.mcd",)
        assert p.granularity is not None
        assert p.granularity.mode == "PerGameTitle+None"

    def test_duckstation_with_an_unreadable_ini_refuses(self):
        p = self._answer("psx", files={DUCKSTATION_CONFIG_INI: {"status": "unreadable"}})
        assert isinstance(p, atlas.Unresolved)
        assert p.code == atlas.UNRESOLVED_EMULATOR_CONFIG_UNREADABLE

    @pytest.mark.parametrize(
        "card1,card2,files",
        [
            ("PerGameTitle", "PerGameTitle", ("<save_id>_1.mcd", "<save_id>_2.mcd")),
            ("Shared", "Shared", ("shared_card_1.mcd", "shared_card_2.mcd")),
            ("PerGameTitle", "Shared", ("<save_id>_1.mcd", "shared_card_2.mcd")),
            ("Shared", "PerGameTitle", ("shared_card_1.mcd", "<save_id>_2.mcd")),
            ("PerGame", "PerGameFileTitle", ("<save_id>_1.mcd", "<rom_stem>_2.mcd")),
        ],
    )
    def test_duckstation_both_slots_occupied_name_both_cards(self, card1, card2, files):
        # Two cards in one directory is the console's own shape, and the
        # answer's flat `files` is every name lying in `dir` — the FileSet
        # invariant. Naming only slot 1 raised instead of answering.
        p = self._answer(
            "psx",
            files={
                DUCKSTATION_CONFIG_INI: (
                    f"[MemoryCards]\nCard1Type = {card1}\nCard2Type = {card2}\n"
                    "Directory = /mnt/sd/memcards\n"
                ),
            },
        )
        assert not isinstance(p, atlas.Unresolved)
        assert p.dir == "/mnt/sd/memcards"
        assert p.file_set.files == files
        assert tuple(g.files for g in p.file_set.groups) == tuple((f,) for f in files)
        assert p.granularity is not None
        assert p.granularity.mode == f"{card1}+{card2}"

    def test_duckstation_a_second_card_elsewhere_stays_out_of_the_flat_list(self):
        # `files` is the names in the answer's own directory; the slot whose
        # configured path leads somewhere else is reachable through `groups`.
        p = self._answer(
            "psx",
            files={
                DUCKSTATION_CONFIG_INI: (
                    "[MemoryCards]\nCard1Type = Shared\nCard2Type = Shared\n"
                    "Card2Path = /mnt/sd/elsewhere/second.mcd\n"
                    "Directory = /mnt/sd/memcards\n"
                ),
            },
        )
        assert not isinstance(p, atlas.Unresolved)
        assert p.dir == "/mnt/sd/memcards"
        assert p.file_set.files == ("shared_card_1.mcd",)
        assert [(g.dir, g.files) for g in p.file_set.groups] == [
            ("/mnt/sd/memcards", ("shared_card_1.mcd",)),
            ("/mnt/sd/elsewhere", ("second.mcd",)),
        ]

    def test_duckstation_a_dead_link_on_the_answers_own_directory_is_stated(self):
        # The link caveats used to be computed on the memory-card directory,
        # which is not where the answer points once a slot names an absolute
        # path of its own — a dead link there produced no caveat at all.
        rd = _retrodeck(
            {
                **self.BASE,
                DUCKSTATION_CONFIG_INI: (
                    "[MemoryCards]\nCard1Type = Shared\nCard2Type = None\n"
                    "Card1Path = /mnt/sd/cards/everyone.mcd\n"
                    "Directory = /mnt/sd/memcards\n"
                ),
            },
            dirs=["/mnt/sd/retrodeck/saves", "/mnt/sd/memcards"],
            symlinks={"/mnt/sd/cards": "/mnt/sd/gone"},
        )
        p = rd.emulators_for("psx").entries[0].savefile_location()
        assert not isinstance(p, atlas.Unresolved)
        assert p.dir == "/mnt/sd/cards"
        assert [c.code for c in p.caveats if c.code == atlas.CAVEAT_DEAD_SYMLINK]

    def test_pcsx2_a_dead_link_on_the_answers_own_directory_is_stated(self):
        rd = _retrodeck(
            {
                **self.BASE,
                PCSX2_INI_PATH: (
                    "[MemoryCards]\nSlot1_Enable = true\n"
                    "Slot1_Filename = /mnt/sd/cards/Mcd001.ps2\n"
                    "Slot2_Enable = false\n"
                ),
            },
            dirs=["/mnt/sd/retrodeck/saves"],
            symlinks={"/mnt/sd/cards": "/mnt/sd/gone"},
        )
        p = rd.emulators_for("ps2").entries[0].savefile_location()
        assert not isinstance(p, atlas.Unresolved)
        assert p.dir == "/mnt/sd/cards"
        assert [c.code for c in p.caveats if c.code == atlas.CAVEAT_DEAD_SYMLINK]

    def test_duckstation_a_linked_answer_directory_states_its_physical_one(self):
        rd = _retrodeck(
            {
                **self.BASE,
                DUCKSTATION_CONFIG_INI: (
                    "[MemoryCards]\nCard1Type = Shared\nCard2Type = None\n"
                    "Card1Path = /mnt/sd/cards/everyone.mcd\n"
                    "Directory = /mnt/sd/memcards\n"
                ),
            },
            dirs=["/mnt/sd/retrodeck/saves", "/mnt/sd/memcards", "/mnt/sd/real-cards"],
            symlinks={"/mnt/sd/cards": "/mnt/sd/real-cards"},
        )
        p = rd.emulators_for("psx").entries[0].savefile_location()
        assert not isinstance(p, atlas.Unresolved)
        assert p.dir == "/mnt/sd/cards"
        assert p.physical_dir == "/mnt/sd/real-cards"

    @pytest.mark.parametrize("word", ["1", "yes", "on", "enabled", "TRUE", "t"])
    def test_pcsx2_reads_every_spelling_of_on_the_emulator_reads(self, word):
        # A hand-edited `Slot2_Enable = 1` is on for PCSX2, and atlas used to
        # answer that the slot was empty.
        rd = _retrodeck(
            {
                **self.BASE,
                PCSX2_INI_PATH: (
                    f"[MemoryCards]\nSlot1_Enable = false\nSlot2_Enable = {word}\n"
                ),
            },
            dirs=["/mnt/sd/retrodeck/saves"],
        )
        p = rd.emulators_for("ps2").entries[0].savefile_location()
        assert not isinstance(p, atlas.Unresolved)
        assert p.file_set.files == ("Mcd002.ps2",)
        assert p.granularity is not None
        assert p.granularity.mode == "off+file"

    def test_pcsx2_a_value_neither_true_nor_false_leaves_the_default(self):
        # GetBoolValue returns false without writing the caller's variable, so
        # the compiled default governs — and the reading says which and why.
        rd = _retrodeck(
            {**self.BASE, PCSX2_INI_PATH: "[MemoryCards]\nSlot1_Enable = maybe\n"},
            dirs=["/mnt/sd/retrodeck/saves"],
        )
        p = rd.emulators_for("ps2").entries[0].savefile_location()
        assert not isinstance(p, atlas.Unresolved)
        assert "Mcd001.ps2" in p.file_set.files  # the slot's default is on
        assert p.granularity is not None
        stated = next(r for r in p.granularity.readings if r.key == "Slot1_Enable")
        assert "neither true nor false" in stated.provenance

    def test_pcsx2_the_texture_switch_reads_the_same_spellings(self):
        rd = _retrodeck(
            {**self.BASE, PCSX2_INI_PATH: "[EmuCore/GS]\nLoadTextureReplacements = 1\n"},
            dirs=["/mnt/sd/retrodeck/saves"],
        )
        answer = rd.emulators_for("ps2").entries[0].texture_pack_location()
        assert not isinstance(answer, atlas.Unresolved)
        assert answer.enabled is True

    def test_pcsx2_a_per_game_settings_file_is_stated_beside_the_switch(self):
        # PCSX2 installs <DataRoot>/gamesettings/<serial>_<crc>.ini as a layer over
        # the global configuration while that game runs, and every core key is
        # read through it — so "replacement is off" is the answer for games
        # that have no such file, and the answer has to say so.
        gamesettings = (
            f"{HOME}/.var/app/net.retrodeck.retrodeck/config/PCSX2/gamesettings"
        )
        rd = _retrodeck(
            {
                **self.BASE,
                PCSX2_INI_PATH: "[EmuCore/GS]\nLoadTextureReplacements = false\n",
                f"{gamesettings}/SLES-12345_A1B2C3D4.ini": (
                    "[EmuCore/GS]\nLoadTextureReplacements = true\n"
                ),
            },
            dirs=["/mnt/sd/retrodeck/saves", gamesettings],
        )
        answer = rd.emulators_for("ps2").entries[0].texture_pack_location()
        assert not isinstance(answer, atlas.Unresolved)
        assert answer.enabled is False
        stated = [
            c for c in answer.caveats if c.code == atlas.CAVEAT_PER_GAME_OVERRIDES_PRESENT
        ]
        assert stated
        assert stated[0].data["count"] == "1"
        assert stated[0].data["dir"] == gamesettings

    def test_pcsx2_says_nothing_about_a_per_game_layer_that_is_not_there(self):
        rd = _retrodeck(
            {**self.BASE, PCSX2_INI_PATH: "[EmuCore/GS]\nLoadTextureReplacements = false\n"},
            dirs=["/mnt/sd/retrodeck/saves"],
        )
        answer = rd.emulators_for("ps2").entries[0].texture_pack_location()
        assert not isinstance(answer, atlas.Unresolved)
        assert atlas.CAVEAT_PER_GAME_OVERRIDES_PRESENT not in [
            c.code for c in answer.caveats
        ]
        assert atlas.CAVEAT_PER_GAME_LAYER_UNREAD not in [c.code for c in answer.caveats]

    def test_pcsx2_a_per_game_layer_that_cannot_be_read_is_not_an_absent_one(self):
        # Silence here means "this answer holds for every game", so answering
        # an unreadable directory the way an empty one is answered claims
        # exactly what the failed listing never established.
        gamesettings = (
            f"{HOME}/.var/app/net.retrodeck.retrodeck/config/PCSX2/gamesettings"
        )
        rd = _retrodeck(
            {**self.BASE, PCSX2_INI_PATH: "[EmuCore/GS]\nLoadTextureReplacements = false\n"},
            dirs=["/mnt/sd/retrodeck/saves", gamesettings],
            unlistable=[gamesettings],
        )
        answer = rd.emulators_for("ps2").entries[0].texture_pack_location()
        assert not isinstance(answer, atlas.Unresolved)
        stated = [c for c in answer.caveats if c.code == atlas.CAVEAT_PER_GAME_LAYER_UNREAD]
        assert stated
        assert stated[0].data["dir"] == gamesettings
        assert stated[0].data["key"] == "LoadTextureReplacements"
        # Not the sibling that asserts they exist, and not silence.
        assert atlas.CAVEAT_PER_GAME_OVERRIDES_PRESENT not in [
            c.code for c in answer.caveats
        ]

    @pytest.mark.parametrize("value", ["maybe", "truthy", "2", "sure"])
    def test_pcsx2_a_switch_value_the_emulator_rejects_is_stated(self, value):
        # GetBoolValue leaves the caller's variable untouched, so the compiled
        # default keeps governing — the setting does not become false, and the
        # save route says so in a reading this answer has no room for.
        rd = _retrodeck(
            {
                **self.BASE,
                PCSX2_INI_PATH: f"[EmuCore/GS]\nLoadTextureReplacements = {value}\n",
            },
            dirs=["/mnt/sd/retrodeck/saves"],
        )
        answer = rd.emulators_for("ps2").entries[0].texture_pack_location()
        assert not isinstance(answer, atlas.Unresolved)
        stated = [c for c in answer.caveats if c.code == atlas.CAVEAT_CFG_VALUE_REJECTED]
        assert stated
        assert stated[0].data["value"] == value
        assert stated[0].data["key"] == "EmuCore/GS/LoadTextureReplacements"
        # The compiled default is false, and that is what governs — the caveat
        # is what keeps it from reading as a key nobody wrote.
        assert answer.enabled is False

    @pytest.mark.parametrize("value", ["1", "true", "off", "disabled"])
    def test_pcsx2_a_switch_value_the_emulator_reads_is_not_stated_as_rejected(self, value):
        rd = _retrodeck(
            {
                **self.BASE,
                PCSX2_INI_PATH: f"[EmuCore/GS]\nLoadTextureReplacements = {value}\n",
            },
            dirs=["/mnt/sd/retrodeck/saves"],
        )
        answer = rd.emulators_for("ps2").entries[0].texture_pack_location()
        assert not isinstance(answer, atlas.Unresolved)
        assert atlas.CAVEAT_CFG_VALUE_REJECTED not in [c.code for c in answer.caveats]

    def test_pcsx2_an_unset_switch_is_not_stated_as_rejected(self):
        # An absent key is not a rejected value: nothing was written there.
        rd = _retrodeck(
            {**self.BASE, PCSX2_INI_PATH: "[Folders]\nTextures = /mnt/sd/tex\n"},
            dirs=["/mnt/sd/retrodeck/saves"],
        )
        answer = rd.emulators_for("ps2").entries[0].texture_pack_location()
        assert not isinstance(answer, atlas.Unresolved)
        assert atlas.CAVEAT_CFG_VALUE_REJECTED not in [c.code for c in answer.caveats]

    @pytest.mark.parametrize(
        "stem,expected",
        [
            ("Rock*Star", "Rock_Star_1.mcd"),
            ("Vol: Two", "Vol: Two_1.mcd"),
            ("A<B>C", "A<B>C_1.mcd"),
        ],
    )
    def test_duckstation_sanitizes_a_file_title_the_linux_way(self, stem, expected):
        # The Linux arm of FileSystemCharacterIsSane rejects '/' and '*' only;
        # ':' is macOS and the angle brackets are Windows.
        rd = _retrodeck(
            {
                **self.BASE,
                DUCKSTATION_CONFIG_INI: (
                    "[MemoryCards]\nCard1Type = PerGameFileTitle\nCard2Type = None\n"
                    "Directory = /mnt/sd/memcards\n"
                ),
            },
            dirs=["/mnt/sd/retrodeck/saves", "/mnt/sd/memcards"],
        )
        p = rd.emulators_for("psx").entries[0].savefile_location(
            content_path=f"/mnt/sd/roms/psx/{stem}.chd"
        )
        assert not isinstance(p, atlas.Unresolved)
        assert p.file_set.files == (expected,)

    def test_duckstation_two_per_game_slots_state_both_holes_once(self):
        p = self._answer(
            "psx",
            files={
                DUCKSTATION_CONFIG_INI: (
                    "[MemoryCards]\nCard1Type = PerGameTitle\n"
                    "Card2Type = PerGameFileTitle\nDirectory = /mnt/sd/memcards\n"
                ),
            },
        )
        assert not isinstance(p, atlas.Unresolved)
        assert p.needs == ("save_id", "rom_stem")

    def test_pcsx2_defaults_without_an_ini_answer_the_dataroot_memcards(self):
        # One DataRoot spelling on Linux — the config side either way
        # (Pcsx2Config.cpp:2197-2217) — so no ini still answers one tree.
        p = self._answer("ps2")
        assert not isinstance(p, atlas.Unresolved)
        assert p.dir.endswith("/config/PCSX2/memcards")
        assert p.file_set.files == ("Mcd001.ps2", "Mcd002.ps2")
        assert p.granularity is not None
        assert p.granularity.mode == "file+file"

    def test_pcsx2_an_enabled_multitap_slot_joins_the_answer(self):
        p = self._answer(
            "ps2",
            files={
                PCSX2_INI_PATH: (
                    "[Folders]\nMemoryCards = /mnt/sd/memcards\n"
                    "[MemoryCards]\nSlot1_Enable = true\nSlot1_Filename = Mcd001.ps2\n"
                    "Slot2_Enable = false\n"
                    "Multitap1_Slot2_Enable = true\n"
                ),
            },
        )
        assert not isinstance(p, atlas.Unresolved)
        assert p.file_set.files == ("Mcd001.ps2", "Mcd-Multitap1-Slot02.ps2")
        assert p.granularity is not None
        assert p.granularity.mode == "file+off+file"

    def test_pcsx2_an_empty_filename_empties_the_slot(self):
        p = self._answer(
            "ps2",
            files={
                PCSX2_INI_PATH: (
                    "[Folders]\nMemoryCards = /mnt/sd/memcards\n"
                    "[MemoryCards]\nSlot1_Enable = true\nSlot1_Filename =\n"
                    "Slot2_Enable = false\n"
                ),
            },
        )
        assert not isinstance(p, atlas.Unresolved)
        assert p.file_set.groups == ()
        stated = [c for c in p.caveats if c.code == atlas.CAVEAT_SAVE_WRITES_DISCARDED]
        assert stated
        assert p.granularity is not None
        assert p.granularity.mode == "empty+off"

    def test_pcsx2_with_an_unreadable_ini_refuses(self):
        p = self._answer("ps2", files={PCSX2_INI_PATH: {"status": "unreadable"}})
        assert isinstance(p, atlas.Unresolved)
        assert p.code == atlas.UNRESOLVED_EMULATOR_CONFIG_UNREADABLE

    def _melonds(self, files=None, content_path=None):
        rd = _retrodeck({**self.BASE, **(files or {})}, dirs=["/mnt/sd/retrodeck/saves"])
        entry = rd.emulators_for("nds").entries[0]
        return entry.savefile_location(content_path=content_path)

    def test_melonds_answers_the_configured_save_directory(self):
        p = self._melonds(
            {MELONDS_TOML_PATH: '[Instance0]\nSaveFilePath = "/mnt/sd/retrodeck/saves/nds/melonds"\n'}
        )
        assert not isinstance(p, atlas.Unresolved)
        assert p.dir == "/mnt/sd/retrodeck/saves/nds/melonds"
        assert p.file_set.files == ("<rom_stem>.sav",)
        assert p.needs == ("rom_stem",)
        assert p.granularity is not None
        assert p.granularity.value == atlas.GRANULARITY_PER_GAME_FILE
        assert p.granularity.mode == "save-file-path"

    def test_melonds_fills_the_stem_when_content_is_named(self):
        p = self._melonds(
            {MELONDS_TOML_PATH: '[Instance0]\nSaveFilePath = "/mnt/sd/retrodeck/saves/nds/melonds/"\n'},
            content_path="/mnt/sd/retrodeck/roms/nds/Some Game.nds",
        )
        assert not isinstance(p, atlas.Unresolved)
        # The trailing separator comes off the way getAssetPath trims it.
        assert p.dir == "/mnt/sd/retrodeck/saves/nds/melonds"
        assert p.file_set.files == ("Some Game.sav",)
        assert p.needs == ()

    def test_melonds_archive_content_keeps_the_hole_open(self):
        # The save is named after the file INSIDE the archive
        # (EmuInstance.cpp:1846-1848) — the zip's own stem must not be stated.
        p = self._melonds(
            {MELONDS_TOML_PATH: '[Instance0]\nSaveFilePath = "/mnt/sd/retrodeck/saves/nds/melonds"\n'},
            content_path="/mnt/sd/retrodeck/roms/nds/Some Game.zip",
        )
        assert not isinstance(p, atlas.Unresolved)
        assert p.file_set.files == ("<rom_stem>.sav",)
        assert p.needs == ("rom_stem",)
        stated = [
            c for c in p.caveats if c.code == atlas.CAVEAT_FILENAMES_CONTENT_CONDITIONAL
        ]
        assert stated
        assert "archive" in stated[0].message

    def test_melonds_empty_save_path_lands_beside_the_rom(self):
        p = self._melonds({MELONDS_TOML_PATH: '[Instance0]\nSaveFilePath = ""\n'})
        assert not isinstance(p, atlas.Unresolved)
        assert p.root_kind == "content_directory"
        assert p.dir == "<content_dir>"
        assert p.needs == ("content_dir", "rom_stem")

    def test_melonds_empty_save_path_with_content_names_the_roms_directory(self):
        p = self._melonds(
            {MELONDS_TOML_PATH: '[Instance0]\nSaveFilePath = ""\n'},
            content_path="/mnt/sd/retrodeck/roms/nds/Some Game.nds",
        )
        assert not isinstance(p, atlas.Unresolved)
        assert p.root_kind == "content_directory"
        assert p.dir == "/mnt/sd/retrodeck/roms/nds"
        assert p.file_set.files == ("Some Game.sav",)
        assert p.needs == ()

    def test_melonds_unparseable_toml_is_factory_defaults_not_the_legacy_file(self):
        # melonDS catches the syntax error and keeps an empty table
        # (Config.cpp:796-803) — it never steps to melonDS.ini, so a legacy
        # value beside a broken TOML must NOT govern.
        p = self._melonds(
            {
                MELONDS_TOML_PATH: "[Instance0\nnot toml",
                MELONDS_INI_PATH: "SaveFilePath=/mnt/sd/elsewhere\n",
            }
        )
        assert not isinstance(p, atlas.Unresolved)
        assert p.root_kind == "content_directory"
        assert p.granularity is not None
        assert "factory defaults" in p.granularity.readings[0].provenance

    def test_melonds_missing_toml_reads_the_legacy_ini(self):
        # The built-in migration: 1.1 reads the pre-1.0 file line by line
        # while no TOML exists (Config.cpp:785-795).
        p = self._melonds({MELONDS_INI_PATH: "SaveFilePath=/mnt/sd/legacy/saves\n"})
        assert not isinstance(p, atlas.Unresolved)
        assert p.dir == "/mnt/sd/legacy/saves"
        assert p.granularity is not None
        assert p.granularity.readings[0].options_file == MELONDS_INI_PATH

    def test_melonds_with_neither_config_lands_beside_the_rom(self):
        p = self._melonds()
        assert not isinstance(p, atlas.Unresolved)
        assert p.root_kind == "content_directory"
        assert p.granularity is not None
        assert p.granularity.readings[0].options_file is None

    def test_melonds_relative_save_path_is_the_launchs_working_directory(self):
        # getAssetPath composes the value verbatim and the process opens it —
        # a relative value anchors at the launch's cwd, not anywhere atlas
        # could read.
        p = self._melonds({MELONDS_TOML_PATH: '[Instance0]\nSaveFilePath = "saves"\n'})
        assert not isinstance(p, atlas.Unresolved)
        assert p.root_kind == "working_directory"
        assert p.dir == "<cwd>/saves"
        assert p.needs == ("cwd", "rom_stem")
        stated = [c for c in p.caveats if c.code == atlas.CAVEAT_SAVE_DIR_LAUNCH_DEPENDENT]
        assert stated

    @pytest.mark.parametrize(
        "name,expected",
        [
            ("Some Game.nds", "Some Game.sav"),
            ("Some.Game.v1.nds", "Some.Game.v1.sav"),
            ("noextension", "noextension.sav"),
            # All extension and no base: getAssetPath writes "firmware" where
            # the stem would go (EmuInstance.cpp:473-476), which this mirrors
            # rather than repairs.
            (".nds", "firmware.sav"),
        ],
    )
    def test_melonds_names_the_save_after_the_loaded_files_stem(self, name, expected):
        p = self._melonds(
            {MELONDS_TOML_PATH: '[Instance0]\nSaveFilePath = "/mnt/sd/saves"\n'},
            content_path=f"/mnt/sd/retrodeck/roms/nds/{name}",
        )
        assert not isinstance(p, atlas.Unresolved)
        assert p.file_set.files == (expected,)

    def test_melonds_with_an_unreadable_toml_refuses(self):
        p = self._melonds({MELONDS_TOML_PATH: {"status": "unreadable"}})
        assert isinstance(p, atlas.Unresolved)
        assert p.code == atlas.UNRESOLVED_EMULATOR_CONFIG_UNREADABLE


PER_USER_ESDE = (
    '<?xml version="1.0"?><systemList>'
    "<system><name>psvita</name><path>%ROMPATH%/psvita</path><extension>.psvita</extension>"
    '<command label="Vita3K (Standalone)">%EMULATOR_VITA3K% -r %INJECT%=%BASENAME%.psvita'
    "</command></system>"
    "<system><name>ps3</name><path>%ROMPATH%/ps3</path><extension>.ps3</extension>"
    '<command label="RPCS3 (Standalone)">%EMULATOR_RPCS3% %ROM%</command></system>'
    "</systemList>"
)
VITA3K_CONFIG_YML = f"{HOME}/.var/app/net.retrodeck.retrodeck/config/Vita3K/config.yml"
RPCS3_VFS_YML = f"{HOME}/.var/app/net.retrodeck.retrodeck/config/rpcs3/vfs.yml"


class TestTheUserAPerUserTreeWouldOpen:
    """Which user account answers, and what each configuration actually says.

    The two emulators reach the same shape — every user directory is a group —
    from opposite facts. RPCS3 records the running user nowhere; Vita3K records
    it as ``user-id`` and leaves only *whether a launch honours it* to the
    launch. The answer used to tell RPCS3's story for both.
    """

    BASE = {
        RETRODECK_JSON: RD_JSON,
        RETRODECK_CFG: 'savefile_directory = "/mnt/sd/retrodeck/saves"\n'
        'libretro_directory = "/app/cores"\n',
        DOLPHIN_ESDE: PER_USER_ESDE,
    }

    def _answer(self, system, files=None, dirs=()):
        rd = _retrodeck(
            {**self.BASE, **(files or {})},
            dirs=["/mnt/sd/retrodeck/saves", *dirs],
        )
        return rd.emulators_for(system).entries[0].savefile_location()

    def _user_caveat(self, placement):
        stated = [c for c in placement.caveats if c.code == atlas.CAVEAT_CORE_MODE_UNESTABLISHED]
        assert stated
        return stated[0]

    def _readings(self, placement):
        assert placement.granularity is not None
        return {r.key: r.value for r in placement.granularity.readings}

    @staticmethod
    def _user_xml(user):
        return f'<?xml version="1.0" encoding="utf-8"?>\n<user id="{user}" name="deck"/>\n'

    def test_vita3k_states_the_user_its_config_records(self):
        p = self._answer(
            "psvita",
            files={
                VITA3K_CONFIG_YML: "pref-path: /mnt/sd/vita\nuser-id: 01\n",
                "/mnt/sd/vita/ux0/user/00/user.xml": self._user_xml("00"),
                "/mnt/sd/vita/ux0/user/01/user.xml": self._user_xml("01"),
            },
            dirs=["/mnt/sd/vita/ux0/user/00", "/mnt/sd/vita/ux0/user/01"],
        )
        assert not isinstance(p, atlas.Unresolved)
        assert self._readings(p)["user-id"] == "01"
        assert self._user_caveat(p).data["configured_user"] == "01"
        # Every tree still answers: which user a launch opens is the launch's
        # business, and the caveat names the recorded one beside them.
        assert [g.dir for g in p.file_set.groups] == [
            "/mnt/sd/vita/ux0/user/00/savedata",
            "/mnt/sd/vita/ux0/user/01/savedata",
        ]

    def test_vita3k_headline_follows_the_recorded_users_tree(self):
        # A frontend launch names an app on the command line, and init_home
        # then reopens exactly the recorded user — so where the emulator's own
        # listing holds it (a directory whose user.xml answers to that id),
        # the headline is its tree, not the alphabetically first one. No
        # user-auto-connect required: the follow rides on the launch, which is
        # how both frontends launch.
        p = self._answer(
            "psvita",
            files={
                VITA3K_CONFIG_YML: "pref-path: /mnt/sd/vita\nuser-id: 01\n",
                "/mnt/sd/vita/ux0/user/00/user.xml": self._user_xml("00"),
                "/mnt/sd/vita/ux0/user/01/user.xml": self._user_xml("01"),
            },
            dirs=["/mnt/sd/vita/ux0/user/00", "/mnt/sd/vita/ux0/user/01"],
        )
        assert not isinstance(p, atlas.Unresolved)
        assert p.dir == "/mnt/sd/vita/ux0/user/01/savedata"
        assert p.physical_dir is None
        caveat = self._user_caveat(p)
        assert caveat.data["reason"] == "the configured user's tree is the one named"
        assert caveat.data["configured_user"] == "01"
        # The groups stay every tree found, in order — the headline moved, the
        # survey did not.
        assert [g.dir for g in p.file_set.groups] == [
            "/mnt/sd/vita/ux0/user/00/savedata",
            "/mnt/sd/vita/ux0/user/01/savedata",
        ]
        # The names caveat talks about the headline tree.
        names = [c for c in p.caveats if c.code == atlas.CAVEAT_FILE_NAMES_UNESTABLISHED]
        assert names[0].data["dir"] == "/mnt/sd/vita/ux0/user/01/savedata"

    def test_vita3k_the_identity_outranks_the_directory_name(self):
        # get_users_list keys a user by its user.xml's id attribute, the
        # directory name only standing in where the file carries none — and
        # io.user_id becomes that key, so the savedata path composes from the
        # identity. A directory 00 answering to id 01 means the launch opens
        # 01 and writes ux0/user/01/savedata, a tree the first save creates.
        p = self._answer(
            "psvita",
            files={
                VITA3K_CONFIG_YML: "pref-path: /mnt/sd/vita\nuser-id: 01\n",
                "/mnt/sd/vita/ux0/user/00/user.xml": self._user_xml("01"),
            },
            dirs=["/mnt/sd/vita/ux0/user/00"],
        )
        assert not isinstance(p, atlas.Unresolved)
        assert p.dir == "/mnt/sd/vita/ux0/user/01/savedata"
        caveat = self._user_caveat(p)
        assert caveat.data["reason"] == "the configured user's tree is the one named"
        # The survey still states what is on disk: 00's tree, nothing else.
        assert [g.dir for g in p.file_set.groups] == ["/mnt/sd/vita/ux0/user/00/savedata"]

    def test_vita3k_a_directory_without_user_xml_is_not_a_set_up_user(self):
        # The directory exists and holds saves, but get_users_list skips a
        # directory whose user.xml does not load — the launch would open the
        # user manager, so the headline stays put and the caveat says the
        # recorded user is not set up. The tree still answers as a group: what
        # is on disk is stated regardless of what Vita3K would list.
        p = self._answer(
            "psvita",
            files={VITA3K_CONFIG_YML: "pref-path: /mnt/sd/vita\nuser-id: 00\n"},
            dirs=["/mnt/sd/vita/ux0/user/00/savedata"],
        )
        assert not isinstance(p, atlas.Unresolved)
        assert p.dir == "/mnt/sd/vita/ux0/user/00/savedata"
        caveat = self._user_caveat(p)
        assert caveat.data["reason"] == "the configured user is not set up here"
        assert caveat.data["configured_user"] == "00"
        assert "has no user.xml" in caveat.message
        assert [g.dir for g in p.file_set.groups] == ["/mnt/sd/vita/ux0/user/00/savedata"]

    def test_vita3k_an_unreadable_user_xml_leaves_the_listing_unestablished(self):
        # Vita3K skips a user.xml that fails to load, but a file atlas cannot
        # read is not known to fail for the emulator — whether the recorded
        # user is listed is unknowable, and the answer says so instead of
        # deciding either way.
        p = self._answer(
            "psvita",
            files={
                VITA3K_CONFIG_YML: "pref-path: /mnt/sd/vita\nuser-id: 01\n",
                "/mnt/sd/vita/ux0/user/00/user.xml": {"status": "unreadable"},
            },
            dirs=["/mnt/sd/vita/ux0/user/00"],
        )
        assert not isinstance(p, atlas.Unresolved)
        assert p.dir == "/mnt/sd/vita/ux0/user/00/savedata"
        caveat = self._user_caveat(p)
        assert (
            caveat.data["reason"]
            == "whether the configured user is set up here was not established"
        )
        assert caveat.data["configured_user"] == "01"
        assert "could not be read" in caveat.message

    def test_vita3k_an_unprovisioned_directory_of_the_recorded_name_does_not_attract_the_headline(
        self,
    ):
        # The witness the single-user fixtures cannot be: with two directories
        # and the recorded one not set up, follow-vs-stay is observable — a
        # resolver that follows the directory name alone names 01's tree here,
        # and only the user.xml check keeps the headline at the first found.
        p = self._answer(
            "psvita",
            files={VITA3K_CONFIG_YML: "pref-path: /mnt/sd/vita\nuser-id: 01\n"},
            dirs=["/mnt/sd/vita/ux0/user/00/savedata", "/mnt/sd/vita/ux0/user/01/savedata"],
        )
        assert not isinstance(p, atlas.Unresolved)
        assert p.dir == "/mnt/sd/vita/ux0/user/00/savedata"
        caveat = self._user_caveat(p)
        assert caveat.data["reason"] == "the configured user is not set up here"
        assert caveat.data["configured_user"] == "01"
        assert caveat.data["users"] == "00,01"

    def test_vita3k_the_identity_fallback_is_the_directory_stem(self):
        # An id-less user.xml keys the user by the directory's stem, not its
        # whole name — upstream takes path.stem() (get_users_list,
        # user_management.cpp:97), so a directory 01.bak answers to 01 and the
        # recorded 01 is listed, its tree composed from the identity.
        p = self._answer(
            "psvita",
            files={
                VITA3K_CONFIG_YML: "pref-path: /mnt/sd/vita\nuser-id: 01\n",
                "/mnt/sd/vita/ux0/user/01.bak/user.xml": '<?xml version="1.0"?>\n<user/>\n',
            },
            dirs=["/mnt/sd/vita/ux0/user/01.bak"],
        )
        assert not isinstance(p, atlas.Unresolved)
        assert p.dir == "/mnt/sd/vita/ux0/user/01/savedata"
        caveat = self._user_caveat(p)
        assert caveat.data["reason"] == "the configured user's tree is the one named"
        assert [g.dir for g in p.file_set.groups] == ["/mnt/sd/vita/ux0/user/01.bak/savedata"]

    def test_vita3k_the_stem_cuts_at_the_rightmost_period_like_the_filesystem(self):
        # std::filesystem::path::stem cuts at the rightmost period unless it
        # leads the name or the name is "." or ".." (libstdc++
        # _M_find_extension), so a directory ..bak answers to "." — a reader
        # that skips the whole leading run of periods would say ..bak. Pinned
        # at the helper because the resolver cannot meet such a name: the
        # user listing comes from a glob whose "*" never matches a leading
        # period (the emulator's directory_iterator would see it), and the
        # mirror stays exact rather than narrowed to what the glob passes.
        from atlas.installations import (
            _VITA3K_USER_LISTED,  # pyright: ignore[reportPrivateUsage] - the mirror is the unit under test
            _vita3k_listed_users,  # pyright: ignore[reportPrivateUsage] - the mirror is the unit under test
        )

        machine = FixtureMachine(
            {"/mnt/sd/vita/ux0/user/..bak/user.xml": '<?xml version="1.0"?>\n<user/>\n'}
        )
        (listed,) = _vita3k_listed_users(machine, "/mnt/sd/vita/ux0/user", ("..bak",))
        assert listed.identity == "."
        assert listed.fate == _VITA3K_USER_LISTED

    def test_vita3k_a_user_xml_that_does_not_parse_is_the_emulators_own_skip(self):
        # load_file fails on a malformed user.xml exactly as on a missing one
        # (get_users_list, user_management.cpp:89) — the directory is not a
        # user Vita3K lists, and the sentence names the parse failure rather
        # than a generic absence.
        p = self._answer(
            "psvita",
            files={
                VITA3K_CONFIG_YML: "pref-path: /mnt/sd/vita\nuser-id: 00\n",
                "/mnt/sd/vita/ux0/user/00/user.xml": '<user id="00">',
            },
            dirs=["/mnt/sd/vita/ux0/user/00"],
        )
        assert not isinstance(p, atlas.Unresolved)
        assert p.dir == "/mnt/sd/vita/ux0/user/00/savedata"
        caveat = self._user_caveat(p)
        assert caveat.data["reason"] == "the configured user is not set up here"
        assert "does not parse" in caveat.message

    def test_vita3k_a_directory_answering_to_another_id_is_not_the_recorded_user(self):
        # The recorded directory exists and its user.xml loads — but it
        # answers to a different id, and nothing else answers to the recorded
        # one, so the launch has no user 01 to reopen and the sentence names
        # the id the directory does answer to.
        p = self._answer(
            "psvita",
            files={
                VITA3K_CONFIG_YML: "pref-path: /mnt/sd/vita\nuser-id: 01\n",
                "/mnt/sd/vita/ux0/user/00/user.xml": self._user_xml("00"),
                "/mnt/sd/vita/ux0/user/01/user.xml": self._user_xml("07"),
            },
            dirs=["/mnt/sd/vita/ux0/user/00", "/mnt/sd/vita/ux0/user/01"],
        )
        assert not isinstance(p, atlas.Unresolved)
        assert p.dir == "/mnt/sd/vita/ux0/user/00/savedata"
        caveat = self._user_caveat(p)
        assert caveat.data["reason"] == "the configured user is not set up here"
        assert 'answers to id "07" instead' in caveat.message

    def test_vita3k_a_recorded_user_without_a_tree_keeps_the_first_found(self):
        # The recorded id names no directory here, so a launch has nothing to
        # reopen and the player picks: the launch's user is genuinely
        # unknowable, the headline stays the first tree found, and the caveat
        # says why in data a client can branch on.
        p = self._answer(
            "psvita",
            files={VITA3K_CONFIG_YML: "pref-path: /mnt/sd/vita\nuser-id: 02\n"},
            dirs=["/mnt/sd/vita/ux0/user/00", "/mnt/sd/vita/ux0/user/01"],
        )
        assert not isinstance(p, atlas.Unresolved)
        assert p.dir == "/mnt/sd/vita/ux0/user/00/savedata"
        caveat = self._user_caveat(p)
        assert caveat.data["reason"] == "the configured user has no tree here"
        assert caveat.data["configured_user"] == "02"
        assert caveat.data["users"] == "00,01"
        assert "no directory of that name" in caveat.message

    def test_vita3k_a_recorded_user_over_an_empty_tree_stays_the_default(self):
        # An empty tree answers as an empty tree — the compiled default stands
        # in, never as a directory found — and the recorded user is simply one
        # more tree that is not there, which the emptiness sentence now says.
        p = self._answer(
            "psvita",
            files={VITA3K_CONFIG_YML: "pref-path: /mnt/sd/vita\nuser-id: 01\n"},
            dirs=["/mnt/sd/vita/ux0/user"],
        )
        assert not isinstance(p, atlas.Unresolved)
        assert p.dir == "/mnt/sd/vita/ux0/user/00/savedata"
        caveat = self._user_caveat(p)
        assert caveat.data["reason"] == "no user directory was found"
        assert caveat.data["configured_user"] == "01"
        assert "the recorded user 01 included" in caveat.message

    def test_vita3k_an_unlistable_tree_does_not_follow_the_recorded_user(self):
        # Whether the recorded user's tree exists is itself not established
        # when the tree could not be listed — the headline makes no new claim.
        p = self._unlistable_tree(
            "psvita", {VITA3K_CONFIG_YML: "pref-path: /mnt/sd/vita\nuser-id: 01\n"}
        )
        assert not isinstance(p, atlas.Unresolved)
        assert p.dir == "/mnt/sd/vita/ux0/user/00/savedata"
        caveat = self._user_caveat(p)
        assert caveat.data["reason"] == "which users exist here was not established"
        assert caveat.data["configured_user"] == "01"

    def test_vita3k_without_a_user_id_preselects_nobody(self):
        p = self._answer(
            "psvita",
            files={VITA3K_CONFIG_YML: "pref-path: /mnt/sd/vita\n"},
            dirs=["/mnt/sd/vita/ux0/user/00"],
        )
        assert not isinstance(p, atlas.Unresolved)
        caveat = self._user_caveat(p)
        assert "configured_user" not in caveat.data
        assert caveat.data["reason"] == "the configuration preselects no user"
        assert self._readings(p)["user-id"] is None

    def test_vita3k_reads_the_auto_connect_switch_beside_it(self):
        p = self._answer(
            "psvita",
            files={
                VITA3K_CONFIG_YML: "pref-path: /mnt/sd/vita\nuser-id: 00\nuser-auto-connect: true\n"
            },
            dirs=["/mnt/sd/vita/ux0/user/00"],
        )
        assert not isinstance(p, atlas.Unresolved)
        assert self._readings(p)["user-auto-connect"] == "true"

    def test_vita3k_an_unread_user_id_is_not_an_absent_one(self):
        # A list under the key is a construct the scalar reader passes over.
        # Reading that as "no user is preselected" states something the file
        # contradicts.
        p = self._answer(
            "psvita",
            files={VITA3K_CONFIG_YML: "pref-path: /mnt/sd/vita\nuser-id: [01]\n"},
            dirs=["/mnt/sd/vita/ux0/user/00"],
        )
        assert not isinstance(p, atlas.Unresolved)
        caveat = self._user_caveat(p)
        assert "configured_user" not in caveat.data
        assert "does not read" in caveat.data["reason"]

    def test_an_empty_user_tree_does_not_claim_a_user_was_found(self):
        # The compiled default is what the emulator would create, not a home
        # anyone saw. The answer used to say every user home found here is
        # stated while naming one that was not found at all.
        p = self._answer("ps3", files={RPCS3_VFS_YML: "/dev_hdd0/: /mnt/sd/hdd/\n"})
        assert not isinstance(p, atlas.Unresolved)
        caveat = self._user_caveat(p)
        assert caveat.data["reason"] == "no user directory was found"
        assert "no user home exists" in caveat.message
        assert "would create" in caveat.message

    def _unlistable_tree(self, system, files):
        rd = _retrodeck(
            {**self.BASE, **files},
            dirs=["/mnt/sd/retrodeck/saves", "/mnt/sd/hdd/home", "/mnt/sd/vita/ux0/user"],
            unlistable=["/mnt/sd/hdd/home", "/mnt/sd/vita/ux0/user"],
        )
        return rd.emulators_for(system).entries[0].savefile_location()

    def test_an_unlistable_user_tree_is_a_structured_caveat(self):
        # "the tree could not be listed in full" used to be a clause glued onto
        # another caveat's prose, which no client can branch on.
        p = self._unlistable_tree("ps3", {RPCS3_VFS_YML: "/dev_hdd0/: /mnt/sd/hdd/\n"})
        assert not isinstance(p, atlas.Unresolved)
        stated = [c for c in p.caveats if c.code == atlas.CAVEAT_SAVE_DIR_UNLISTABLE]
        assert stated
        # `path` is the key the code's other emitter uses and the guide
        # documents; a second shape for one code is a client reading nothing.
        assert stated[0].data["path"] == "/mnt/sd/hdd/home"
        assert stated[0].data["core"] == "RPCS3"

    @pytest.mark.parametrize(
        "system,files",
        [
            ("ps3", {RPCS3_VFS_YML: "/dev_hdd0/: /mnt/sd/hdd/\n"}),
            ("psvita", {VITA3K_CONFIG_YML: "pref-path: /mnt/sd/vita\n"}),
        ],
    )
    def test_a_tree_that_could_not_be_listed_does_not_say_it_is_empty(self, system, files):
        # A failed listing establishes neither "these users exist" nor "none
        # does". Answering it with the empty tree's sentence — "nothing has
        # saved here yet" — is a claim about contents the listing never
        # reached, beside a second caveat saying it was never read.
        p = self._unlistable_tree(system, files)
        assert not isinstance(p, atlas.Unresolved)
        caveat = self._user_caveat(p)
        assert caveat.data["reason"] == "which users exist here was not established"
        assert "nothing has saved here yet" not in caveat.message
        assert "could not be listed" in caveat.message
        # And the two states it is not: neither existence sentence.
        assert "every user" not in caveat.message
        assert [c.code for c in p.caveats].count(atlas.CAVEAT_SAVE_DIR_UNLISTABLE) == 1

    def test_rpcs3_virtual_memory_cards_are_a_directory_group_not_an_image(self):
        # `save-inside-image` means the answer names a FILE and nothing inside
        # it is addressable. The vmc path is a directory, so a client following
        # that code would have copied a directory "as a file".
        p = self._answer(
            "ps3",
            files={RPCS3_VFS_YML: "/dev_hdd0/: /mnt/sd/hdd/\n"},
            dirs=["/mnt/sd/hdd/home/00000001"],
        )
        assert not isinstance(p, atlas.Unresolved)
        assert atlas.CAVEAT_SAVE_INSIDE_IMAGE not in [c.code for c in p.caveats]
        vmc = [g for g in p.file_set.groups if g.dir.endswith("/savedata/vmc")]
        assert len(vmc) == 1
        assert vmc[0].files is None
        assert vmc[0].role == atlas.ROLE_MEMORY_CARD
        spans = [c for c in p.caveats if c.code == atlas.CAVEAT_FILE_SET_SPANS_ROOTS]
        assert spans
        assert spans[0].data["dir"] == vmc[0].dir

    def test_vita3k_an_unread_pref_path_refuses_for_the_right_reason(self):
        p = self._answer("psvita", files={VITA3K_CONFIG_YML: "pref-path:\n  - /mnt/sd/vita\n"})
        assert isinstance(p, atlas.Unresolved)
        assert p.code == atlas.UNRESOLVED_EMULATOR_CONFIG_UNREADABLE
        assert p.data["reason"] == "pref-path is unread"

    def test_rpcs3_an_unread_drive_refuses_instead_of_the_compiled_default(self):
        # The key IS set; atlas did not read it. Answering the compiled default
        # and calling it "the compiled default governs" was a claim about a key
        # the file states.
        p = self._answer("ps3", files={RPCS3_VFS_YML: "/dev_hdd0/:\n  - /mnt/sd/hdd\n"})
        assert isinstance(p, atlas.Unresolved)
        assert p.code == atlas.UNRESOLVED_EMULATOR_CONFIG_UNREADABLE
        assert p.data["reason"] == "/dev_hdd0/ is unread"

    def test_rpcs3_still_says_nothing_records_its_user(self):
        # The fix is Vita3K's alone: RPCS3 really does record no user.
        p = self._answer(
            "ps3",
            files={RPCS3_VFS_YML: "/dev_hdd0/: /mnt/sd/hdd/\n"},
            dirs=["/mnt/sd/hdd/home/00000001"],
        )
        assert not isinstance(p, atlas.Unresolved)
        caveat = self._user_caveat(p)
        assert caveat.data["reason"] == "the active user account is not recorded on disk"
        assert "configured_user" not in caveat.data


class TestEmuDeckStandaloneLaunchers:
    """The launcher route's refusal corners the vector family does not carry."""

    OVERLAY = (
        '<?xml version="1.0"?>\n<systemList>\n  <system>\n    <name>wiiu</name>\n'
        "    <path>%ROMPATH%/wiiu/roms</path>\n    <extension>.rpx</extension>\n"
        '    <command label="Cemu (Native)">/bin/bash /home/deck/Emulation/tools/launchers/cemu.sh'
        " -f -g %ROM%</command>\n"
        "  </system>\n  <system>\n    <name>atarijaguar</name>\n"
        "    <path>%ROMPATH%/atarijaguar</path>\n    <extension>.j64</extension>\n"
        '    <command label="BigPEmu (Proton)">/bin/bash /home/deck/Emulation/tools/launchers/bigpemu.sh'
        " %ROM%</command>\n"
        "  </system>\n  <system>\n    <name>n64</name>\n"
        "    <path>%ROMPATH%/n64</path>\n    <extension>.z64</extension>\n"
        '    <command label="Somewhere Else">/usr/bin/some-emulator %ROM%</command>\n'
        "  </system>\n  <system>\n    <name>n3ds</name>\n"
        "    <path>%ROMPATH%/n3ds</path>\n    <extension>.3ds</extension>\n"
        '    <command label="Azahar (Standalone)">%EMULATOR_AZAHAR% %ROM%</command>\n'
        "  </system>\n</systemList>\n"
    )
    BASE = {
        EMUDECK_SETTINGS: 'romsPath="$HOME/Emulation/roms"\nsavesPath="$HOME/Emulation/saves"\n',
        f"{HOME}/ES-DE/custom_systems/es_systems.xml": OVERLAY,
        f"{HOME}/.config/Cemu/settings.xml": (
            "<content><mlc_path>/home/deck/Emulation/roms/wiiu/mlc01</mlc_path></content>"
        ),
    }

    def _answer(self, system, files=None, **kwargs):
        machine = FixtureMachine({**self.BASE, **(files or {})}, **kwargs)
        ed = atlas.EmuDeck(HOME, machine)
        return ed.emulators_for(system).entries[0].savefile_location()

    def test_an_unallowlisted_launcher_stays_unsupported(self):
        # bigpemu.sh is a real EmuDeck launcher, and nobody has established
        # its wiring — the allowlist is per emulator, never the directory.
        p = self._answer("atarijaguar")
        assert isinstance(p, atlas.Unresolved)
        assert p.code == atlas.UNRESOLVED_STANDALONE

    def test_a_command_naming_no_launcher_stays_unsupported(self):
        p = self._answer("n64")
        assert isinstance(p, atlas.Unresolved)
        assert p.code == atlas.UNRESOLVED_STANDALONE

    def test_with_nothing_installed_the_launcher_falls_to_proton(self):
        # cemu.sh probes the AppImage, then the flatpak, and otherwise runs
        # the Windows build — an empty machine ends there.
        p = self._answer("wiiu", dirs=[f"{HOME}/Applications"])
        assert isinstance(p, atlas.Unresolved)
        assert p.code == atlas.UNRESOLVED_STANDALONE_VARIANT_UNESTABLISHED
        assert p.data["variant"] == "proton"

    def test_a_user_installed_flatpak_is_found_by_the_second_probe(self):
        p = self._answer(
            "wiiu",
            dirs=[
                f"{HOME}/Applications",
                f"{HOME}/.local/share/flatpak/app/info.cemu.Cemu",
            ],
        )
        assert isinstance(p, atlas.Unresolved)
        assert p.code == atlas.UNRESOLVED_STANDALONE_VARIANT_UNESTABLISHED
        assert p.data["variant"] == "flatpak"

    def _entry(self, system, files=None, **kwargs):
        machine = FixtureMachine({**self.BASE, **(files or {})}, **kwargs)
        return atlas.EmuDeck(HOME, machine).emulators_for(system).entries[0]

    @pytest.mark.parametrize("question", ["texture_pack_location", "mod_location"])
    def test_the_other_questions_refuse_at_the_same_gate_the_save_route_does(self, question):
        # The gate is shared by all four questions since #255; only the save
        # route's refusals had a test, so the texture and mod ones could have
        # answered from a tree their binary never reads and nothing would say.
        entry = self._entry(
            "wiiu",
            dirs=[
                f"{HOME}/Applications",
                f"{HOME}/.local/share/flatpak/app/info.cemu.Cemu",
            ],
        )
        outcome = getattr(entry, question)()
        assert isinstance(outcome, atlas.Unresolved)
        assert outcome.code == atlas.UNRESOLVED_STANDALONE_VARIANT_UNESTABLISHED
        assert outcome.data["variant"] == "flatpak"

    @pytest.mark.parametrize("question", ["texture_pack_location", "mod_location"])
    def test_the_other_questions_refuse_a_proton_launch_too(self, question):
        entry = self._entry("wiiu", dirs=[f"{HOME}/Applications"])
        outcome = getattr(entry, question)()
        assert isinstance(outcome, atlas.Unresolved)
        assert outcome.code == atlas.UNRESOLVED_STANDALONE_VARIANT_UNESTABLISHED
        assert outcome.data["variant"] == "proton"

    def test_an_unlistable_applications_directory_is_not_a_no(self):
        # The launcher would still look there — an unreadable directory makes
        # the pick unestablished, never "no AppImage, so Proton".
        p = self._answer("wiiu", unlistable=[f"{HOME}/Applications"])
        assert isinstance(p, atlas.Unresolved)
        assert p.code == atlas.UNRESOLVED_STANDALONE_VARIANT_UNESTABLISHED
        assert p.data["variant"] == "unestablished"

    def test_the_appimage_match_is_case_insensitive_like_the_launchers_find(self):
        # find -iname "Cemu*.AppImage" (cemu.sh:42) — a lowercase file still counts.
        p = self._answer("wiiu", files={f"{HOME}/Applications/cemu-2.6.appimage": ""})
        assert not isinstance(p, atlas.Unresolved)
        assert p.dir.endswith("/mlc01/usr/save/<save_id>")

    def test_a_token_entry_goes_through_the_same_variant_gate(self):
        # EmuDeck overlays also name emulators by %EMULATOR_...% token — the
        # binary is still picked at run time (ES-DE's find rules), so the
        # same AppImage-first gate applies.
        p = self._answer(
            "n3ds",
            files={
                f"{HOME}/Applications/azahar.AppImage": "",
                f"{HOME}/.config/azahar-emu/qt-config.ini": (
                    "[Data%20Storage]\nuse_custom_storage=true\n"
                    "use_custom_storage\\default=false\n"
                    "sdmc_directory=/home/deck/Emulation/storage/azahar/sdmc/\n"
                    "sdmc_directory\\default=false\n"
                ),
            },
        )
        assert not isinstance(p, atlas.Unresolved)
        assert p.dir.startswith("/home/deck/Emulation/storage/azahar/sdmc/")
        assert p.dir.endswith("/title/<save_id>/data/00000001")

    def test_a_token_entry_with_only_a_flatpak_refuses_the_variant(self):
        p = self._answer(
            "n3ds",
            dirs=[
                f"{HOME}/Applications",
                "/var/lib/flatpak/app/io.github.azahar.Azahar",
            ],
        )
        assert isinstance(p, atlas.Unresolved)
        assert p.code == atlas.UNRESOLVED_STANDALONE_VARIANT_UNESTABLISHED
        assert p.data["variant"] == "flatpak"

    MELONDS_OVERLAY = (
        '<?xml version="1.0"?>\n<systemList>\n  <system>\n    <name>nds</name>\n'
        "    <path>%ROMPATH%/nds</path>\n    <extension>.nds</extension>\n"
        '    <command label="melonDS (Standalone)">/bin/bash '
        "/home/deck/Emulation/tools/launchers/melonds.sh %ROM%</command>\n"
        '    <command label="melonDS (Token)">%EMULATOR_MELONDS% -f %ROM%</command>\n'
        "  </system>\n</systemList>\n"
    )
    MELONDS_APP_INI = f"{HOME}/.var/app/net.kuribo64.melonDS/config/melonDS/melonDS.ini"

    def _melonds(self, files=None, **kwargs):
        merged = {
            **self.BASE,
            f"{HOME}/ES-DE/custom_systems/es_systems.xml": self.MELONDS_OVERLAY,
            **(files or {}),
        }
        return atlas.EmuDeck(HOME, FixtureMachine(merged, **kwargs))

    def test_melonds_launcher_pins_the_flatpak_over_any_appimage(self):
        # melonds.sh performs no probe — it runs the installed flatpak
        # outright (melonds.sh:4) — so an AppImage under ~/Applications must
        # NOT reroute the answer to the host tree: the app's own INI governs.
        ed = self._melonds(
            {
                f"{HOME}/Applications/melonDS.AppImage": "",
                f"{HOME}/.config/melonDS/melonDS.ini": "SaveFilePath=/wrong/host/tree\n",
                self.MELONDS_APP_INI: "SaveFilePath=/home/deck/Emulation/saves/melonds/saves\n",
            }
        )
        p = ed.emulators_for("nds").entries[0].savefile_location()
        assert not isinstance(p, atlas.Unresolved)
        assert p.dir == "/home/deck/Emulation/saves/melonds/saves"
        assert p.file_set.files == ("<rom_stem>.sav",)

    def test_melonds_token_route_answers_from_the_flatpaks_own_homes(self):
        # The token goes through the probe: no AppImage, the installed
        # flatpak matches — and the card names its app id, so the variant
        # answers from ~/.var/app instead of refusing.
        ed = self._melonds(
            {self.MELONDS_APP_INI: "SaveFilePath=/home/deck/Emulation/saves/melonds/saves\n"},
            dirs=[
                f"{HOME}/Applications",
                "/var/lib/flatpak/app/net.kuribo64.melonDS",
            ],
        )
        entry = next(e for e in ed.emulators_for("nds").entries if e.label == "melonDS (Token)")
        p = entry.savefile_location()
        assert not isinstance(p, atlas.Unresolved)
        assert p.dir == "/home/deck/Emulation/saves/melonds/saves"

    def test_melonds_flatpak_without_any_config_lands_beside_the_rom(self):
        ed = self._melonds(
            dirs=[
                f"{HOME}/Applications",
                "/var/lib/flatpak/app/net.kuribo64.melonDS",
            ],
        )
        entry = next(e for e in ed.emulators_for("nds").entries if e.label == "melonDS (Token)")
        p = entry.savefile_location()
        assert not isinstance(p, atlas.Unresolved)
        assert p.root_kind == "content_directory"

    def test_melonds_firmware_token_follows_the_same_gate(self):
        ed = self._melonds(
            dirs=[
                f"{HOME}/Applications",
                "/var/lib/flatpak/app/net.kuribo64.melonDS",
            ],
        )
        assert ed.standalone_firmware_token("%EMULATOR_MELONDS% -f %ROM%") == "MELONDS"

    def test_an_unpacked_binary_is_a_variant_of_its_own(self):
        # EmuDeck unpacks some emulators out of their AppImage and keeps the
        # executable at ~/Applications/<Name>/<Name> (emuDeckVita3K.sh:21-24),
        # which is where ES-DE's own find rule looks right after the AppImage
        # patterns. The directory alone is not enough — the executable inside
        # it is what the probe answers on.
        machine = FixtureMachine(
            {**self.BASE, f"{HOME}/Applications/Vita3K/Vita3K": ""},
        )
        ed = atlas.EmuDeck(HOME, machine)
        assert ed.standalone_firmware_token("%EMULATOR_VITA3K% %ROM%") == "VITA3K"

    def test_a_directory_without_the_executable_is_not_the_binary_variant(self):
        machine = FixtureMachine(
            self.BASE, dirs=[f"{HOME}/Applications/Vita3K"]
        )
        ed = atlas.EmuDeck(HOME, machine)
        assert ed.standalone_firmware_token("%EMULATOR_VITA3K% %ROM%") is None

    def test_a_carded_flatpak_without_an_app_id_still_refuses_the_firmware_token(self):
        # Azahar's card names no flatpak id — a flatpak-only launch stays
        # ungated for firmware exactly as it does for saves.
        ed = self._melonds(
            dirs=[
                f"{HOME}/Applications",
                "/var/lib/flatpak/app/io.github.azahar.Azahar",
            ],
        )
        assert ed.standalone_firmware_token("%EMULATOR_AZAHAR% %ROM%") is None


class TestEmuDeck:
    def test_settings_parse_and_roots(self):
        machine = FixtureMachine(
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

    def test_partially_quoted_marker_paths_read_the_way_source_reads_them(self):
        # What the app-driven installer actually writes: every path key quotes
        # only its jq-read prefix (jsonToBashVars.sh:116-123 @ 863ab69), and
        # bash concatenates the segments into one word. The parse must too, or
        # every marker-derived root is a path that exists nowhere and health
        # false-alarms root-missing on a healthy machine.
        machine = FixtureMachine(
            {
                EMUDECK_SETTINGS: (
                    'romsPath="/run/media/deck/Emulation"/Emulation/roms\n'
                    'savesPath="/run/media/deck/Emulation"/Emulation/saves\n'
                    'biosPath="/run/media/deck/Emulation"/Emulation/bios\n'
                ),
                STANDALONE_CFG: (
                    'savefile_directory = "/run/media/deck/Emulation/Emulation/saves/retroarch/saves"\n'
                ),
                "/run/media/deck/Emulation/Emulation/saves/retroarch/saves/.keep": "",
            }
        )
        ed = atlas.EmuDeck(HOME, machine)
        assert ed.root() == "/run/media/deck/Emulation/Emulation"
        assert ed.saves_root() == "/run/media/deck/Emulation/Emulation/saves"
        assert ed.bios_dir() == "/run/media/deck/Emulation/Emulation/bios"
        assert ed.health().codes == ()

    def test_single_quoted_and_bare_marker_values_dequote_alike(self):
        # The other shapes a real marker carries: a whole-single-quoted value
        # and a bare one read the same as the double-quoted stock shape.
        for marker in (
            "romsPath='/x/Emulation/roms'\n",
            "romsPath=/x/Emulation/roms\n",
            'romsPath="/x"/Emulation/roms\n',
        ):
            ed = atlas.EmuDeck(HOME, FixtureMachine({EMUDECK_SETTINGS: marker}))
            assert ed.root() == "/x/Emulation", marker

    def test_home_expands_after_the_quotes_come_off(self):
        # A $HOME inside a quoted segment concatenated with a bare one: the
        # dequoting yields the word bash would, and the expansion runs on it.
        machine = FixtureMachine({EMUDECK_SETTINGS: 'romsPath="$HOME"/Emulation/roms\n'})
        assert atlas.EmuDeck(HOME, machine).root() == f"{HOME}/Emulation"

    def test_unterminated_quote_stays_verbatim(self):
        # The discriminating direction: bash refuses a line whose quote never
        # closes, and atlas does not emulate a shell — the value stays
        # verbatim, so the derived root is visibly not a real path and health
        # states root-missing instead of a silently invented dequoting.
        machine = FixtureMachine({EMUDECK_SETTINGS: 'romsPath="/x/Emulation/roms\n'})
        ed = atlas.EmuDeck(HOME, machine)
        assert ed.root() == '"/x/Emulation'
        assert atlas.HEALTH_ISSUE_ROOT_MISSING in ed.health().codes

    def test_savefile_location_reads_standalone_cfg(self):
        machine = FixtureMachine(
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
        p = placed(ed.savefile_location(content_path=f"{HOME}/Emulation/roms/gba/Game.zip"))
        assert p.dir == "/home/deck/Emulation/saves/retroarch/saves"
        assert p.root_kind == atlas.ROOT_SAVEFILE_DIRECTORY


class TestEmuDeckEsdeCatalogue:
    """The edges of the ES-DE side the vectors do not pin: presence decisions,
    the marker cross-check's silence, the per-branch refusals of the ROM
    resolution in the sealed state, and the roms_dir root question. The stock
    shapes live in the vectors."""

    OVERLAY = (
        '<?xml version="1.0"?><systemList><system><name>atarijaguar</name>'
        "<path>%ROMPATH%/atarijaguar</path><extension>.j64 .J64</extension>"
        '<command label="Virtual Jaguar">%EMULATOR_RETROARCH% -L %CORE_RETROARCH%/virtualjaguar_libretro.so %ROM%</command>'
        "</system></systemList>"
    )
    MARKER = 'romsPath="$HOME/Emulation/roms"\nsavesPath="$HOME/Emulation/saves"\n'
    APPIMAGE = f"{HOME}/Applications/ES-DE.AppImage"

    def _emudeck(self, files, **kwargs):
        base = {
            EMUDECK_SETTINGS: self.MARKER,
            STANDALONE_CFG: 'savefile_directory = "/home/deck/Emulation/saves"\n',
            f"{HOME}/Emulation/saves/.keep": "",
        }
        base.update(files)
        return atlas.EmuDeck(HOME, FixtureMachine(base, **kwargs))

    def _codes(self, answer):
        return [c.code for c in answer.caveats]

    def test_the_appdata_tree_alone_is_presence(self):
        # An AppImage moved away from a configuration that still runs: the
        # ~/ES-DE tree decides presence on its own.
        ed = self._emudeck({f"{HOME}/ES-DE/custom_systems/es_systems.xml": self.OVERLAY})
        answer = ed.emulators_for("atarijaguar")
        assert [e.label for e in answer.entries] == ["Virtual Jaguar"]
        assert atlas.CAVEAT_EMULATOR_CATALOGUE_SEALED in self._codes(answer)

    def test_the_appimage_alone_is_presence(self):
        # The reverse: EmuDeck's own installed-test (the AppImage stat) with no
        # appdata tree yet — sealed and empty, never the unestablished refusal.
        ed = self._emudeck({self.APPIMAGE: {"status": "invalid-text"}})
        codes = self._codes(ed.emulators_for("atarijaguar"))
        assert atlas.CAVEAT_EMULATOR_CATALOGUE_SEALED in codes
        assert atlas.CAVEAT_EMULATOR_CATALOGUE_UNESTABLISHED not in codes

    def test_a_quoted_marker_value_is_the_same_statement(self):
        # setSetting writes bare values; a hand-edited quoted one parses the
        # same (the parser quote-strips), so agreement stays silent.
        ed = self._emudeck(
            {
                EMUDECK_SETTINGS: self.MARKER + 'doInstallESDE="true"\n',
                f"{HOME}/ES-DE/custom_systems/es_systems.xml": self.OVERLAY,
            }
        )
        assert atlas.CAVEAT_FRONTEND_MARKER_MISMATCH not in self._codes(ed.emulators_for("atarijaguar"))

    def test_a_marker_that_states_neither_true_nor_false_stays_silent(self):
        # No doInstallESDE key: nothing is stated, so no disagreement can be
        # manufactured — in either presence state.
        with_esde = self._emudeck({f"{HOME}/ES-DE/custom_systems/es_systems.xml": self.OVERLAY})
        without = self._emudeck({})
        assert atlas.CAVEAT_FRONTEND_MARKER_MISMATCH not in self._codes(with_esde.emulators_for("gb"))
        assert atlas.CAVEAT_FRONTEND_MARKER_MISMATCH not in self._codes(without.emulators_for("gb"))

    def test_no_es_de_means_no_relocation_statement(self):
        # A stray portable.txt on a machine with no ES-DE stays silent: with
        # no ES-DE on this disk there is nothing it could have moved, so the
        # absence refusal never reads it.
        without = self._emudeck({f"{HOME}/Applications/portable.txt": ""})
        assert atlas.CAVEAT_CONFIG_HOME_RELOCATED not in self._codes(without.emulators_for("gb"))

    def test_a_marker_value_that_is_neither_true_nor_false_stays_silent(self):
        # The value case, not only the absent key: jq emits `null` for a
        # missing installFrontends key (jsonToBashVars.sh:71), so
        # doInstallESDE=null is a real marker state — and it states nothing.
        # With ES-DE present, treating "not true" as "stated false" would
        # manufacture a disagreement out of silence; these pin that it cannot.
        for value in ("null", "yes"):
            ed = self._emudeck(
                {
                    EMUDECK_SETTINGS: self.MARKER + f"doInstallESDE={value}\n",
                    f"{HOME}/ES-DE/custom_systems/es_systems.xml": self.OVERLAY,
                }
            )
            assert atlas.CAVEAT_FRONTEND_MARKER_MISMATCH not in self._codes(
                ed.emulators_for("atarijaguar")
            ), value

    def test_unreadable_settings_refuse_the_rom_directory(self):
        ed = self._emudeck(
            {
                f"{HOME}/ES-DE/custom_systems/es_systems.xml": self.OVERLAY,
                f"{HOME}/ES-DE/settings/es_settings.xml": {"status": "unreadable"},
            }
        )
        placement = ed.rom_location("atarijaguar")
        assert placement.dir is None
        assert placement.extensions == (".j64", ".J64")
        codes = self._codes(placement)
        assert atlas.CAVEAT_FRONTEND_SETTINGS_UNREADABLE in codes
        assert atlas.CAVEAT_EMULATOR_CATALOGUE_SEALED in codes

    def test_a_relative_rom_directory_is_refused_not_guessed(self):
        # Relative resolves against the ES-DE process's working directory,
        # which atlas has not established — unlike ~, which the frontend
        # expands against a home this handle knows.
        ed = self._emudeck(
            {
                f"{HOME}/ES-DE/custom_systems/es_systems.xml": self.OVERLAY,
                f"{HOME}/ES-DE/settings/es_settings.xml": '<string name="ROMDirectory" value="Emulation/roms" />',
            }
        )
        placement = ed.rom_location("atarijaguar")
        assert placement.dir is None
        assert atlas.CAVEAT_ROM_PATH_UNRESOLVED in self._codes(placement)

    def test_a_refusal_names_the_raw_text_even_when_a_tilde_expanded(self):
        # Emulation/~/roms expands and is still relative: the refusal must
        # carry the setting's own text — the value whose remedy is an edit —
        # never the half-expanded string.
        ed = self._emudeck(
            {
                f"{HOME}/ES-DE/custom_systems/es_systems.xml": self.OVERLAY,
                f"{HOME}/ES-DE/settings/es_settings.xml": '<string name="ROMDirectory" value="Emulation/~/roms" />',
            }
        )
        placement = ed.rom_location("atarijaguar")
        assert placement.dir is None
        caveat = next(c for c in placement.caveats if c.code == atlas.CAVEAT_ROM_PATH_UNRESOLVED)
        assert caveat.data["configured"] == "Emulation/~/roms"

    def test_a_tilde_rom_directory_expands_against_the_users_home(self):
        # ES-DE expands every ~ in the setting (FileData.cpp:289 via
        # expandHomePath), and this ES-DE's home is the user's own — the
        # launcher passes no --home.
        ed = self._emudeck(
            {
                f"{HOME}/ES-DE/custom_systems/es_systems.xml": self.OVERLAY,
                f"{HOME}/ES-DE/settings/es_settings.xml": '<string name="ROMDirectory" value="~/Emulation/roms" />',
            }
        )
        placement = ed.rom_location("atarijaguar")
        assert placement.dir == f"{HOME}/Emulation/roms/atarijaguar"
        assert atlas.CAVEAT_ROM_PATH_UNRESOLVED not in self._codes(placement)

    def test_an_unset_rom_directory_resolves_the_upstream_default(self):
        # No es_settings.xml at all: ES-DE's own <home>/ROMs default applies —
        # against the user's real home, because EmuDeck launches with no --home.
        ed = self._emudeck({f"{HOME}/ES-DE/custom_systems/es_systems.xml": self.OVERLAY})
        assert ed.rom_location("atarijaguar").dir == f"{HOME}/ROMs/atarijaguar"

    def test_an_unreadable_overlay_answers_empty_and_sealed(self):
        # The overlay is skipped like any malformed layer; with the bundled
        # layer sealed the enumeration is then empty — and says sealed, never
        # unreadable (the shadow is the only file whose failure means that).
        ed = self._emudeck(
            {f"{HOME}/ES-DE/custom_systems/es_systems.xml": {"status": "unreadable"}}
        )
        answer = ed.emulators_for("atarijaguar")
        assert answer.entries == ()
        codes = self._codes(answer)
        assert atlas.CAVEAT_EMULATOR_CATALOGUE_SEALED in codes
        assert atlas.CAVEAT_EMULATOR_CATALOGUE_UNREADABLE not in codes

    def test_the_firmware_route_reads_the_broken_shadow_as_unreadable(self):
        # The bundled layer on disk that cannot be read is the unreadable
        # catalogue on the firmware route too — never sealed, never the
        # machine-shaped unavailable — and the riders join that statement.
        ed = self._emudeck(
            {
                f"{HOME}/ES-DE/resources/systems/linux/es_systems.xml": {"status": "unreadable"},
                f"{HOME}/Applications/portable.txt": "",
            }
        )
        codes = self._codes(ed.firmware_for_system("gb"))
        status = codes.index(atlas.CAVEAT_EMULATOR_CATALOGUE_UNREADABLE)
        assert codes[status + 1] == atlas.CAVEAT_CONFIG_HOME_RELOCATED
        assert atlas.CAVEAT_EMULATOR_CATALOGUE_SEALED not in codes
        assert atlas.CAVEAT_EMULATOR_CATALOGUE_UNAVAILABLE not in codes

    def test_the_firmware_route_reads_the_readable_shadow_as_the_whole_catalogue(self):
        # A readable resource shadow IS the bundled layer, on disk: nothing is
        # sealed away, and a system it does not declare is genuinely not the
        # frontend's — with no catalogue-status statement to sit behind, the
        # riders LEAD the resolver's own caveats instead of following one.
        ed = self._emudeck(
            {
                f"{HOME}/ES-DE/resources/systems/linux/es_systems.xml": self.OVERLAY,
                f"{HOME}/Applications/portable.txt": "",
            }
        )
        codes = self._codes(ed.firmware_for_system("gb"))
        assert atlas.CAVEAT_EMULATOR_CATALOGUE_SEALED not in codes
        assert codes.index(atlas.CAVEAT_CONFIG_HOME_RELOCATED) + 1 == codes.index(
            atlas.CAVEAT_SYSTEM_UNKNOWN
        )

    def test_an_own_spelling_firmware_answer_is_not_catalogue_informed(self):
        # A word no build declares is answered from the cores on every
        # arrangement — the catalogue is never consulted, so neither its
        # sealed statement nor the riders have anything to qualify.
        ed = self._emudeck(
            {
                f"{HOME}/ES-DE/custom_systems/es_systems.xml": self.OVERLAY,
                f"{HOME}/Applications/portable.txt": "",
            }
        )
        codes = self._codes(ed.firmware_for_system("ti83"))
        assert atlas.CAVEAT_SYSTEM_NOT_IN_CATALOGUE in codes
        assert atlas.CAVEAT_EMULATOR_CATALOGUE_SEALED not in codes
        assert atlas.CAVEAT_CONFIG_HOME_RELOCATED not in codes

    def test_a_cleared_system_directory_refuses_before_the_catalogue(self):
        # root None: the resolver refused before consulting the catalogue, so
        # the answer carries neither the sealed statement nor the riders —
        # the same empty answer the route gave before the catalogue wiring.
        ed = self._emudeck(
            {
                STANDALONE_CFG: 'system_directory = ""\n',
                f"{HOME}/ES-DE/custom_systems/es_systems.xml": self.OVERLAY,
                f"{HOME}/Applications/portable.txt": "",
            }
        )
        answer = ed.firmware_for_system("atarijaguar")
        assert answer.root is None
        codes = self._codes(answer)
        assert atlas.CAVEAT_SYSTEM_DIRECTORY_CLEARED in codes
        assert atlas.CAVEAT_EMULATOR_CATALOGUE_SEALED not in codes
        assert atlas.CAVEAT_CONFIG_HOME_RELOCATED not in codes

    def test_roms_dir_answers_the_root_es_de_substitutes(self):
        # The root, not a system's directory — no <path> is applied to it.
        ed = self._emudeck(
            {
                f"{HOME}/ES-DE/custom_systems/es_systems.xml": self.OVERLAY,
                f"{HOME}/ES-DE/settings/es_settings.xml": '<string name="ROMDirectory" value="/r" />',
            }
        )
        assert ed.roms_dir() == "/r"

    def test_roms_dir_does_not_follow_the_markers_roms_path(self):
        # settings.sh's romsPath is EmuDeck's bookkeeping; ES-DE's ROMDirectory
        # is what the frontend substitutes — the same cfg-over-marker rule as
        # RetroDECK's roms_dir, over this arrangement's files.
        ed = self._emudeck(
            {
                f"{HOME}/ES-DE/custom_systems/es_systems.xml": self.OVERLAY,
                f"{HOME}/ES-DE/settings/es_settings.xml": (
                    '<string name="ROMDirectory" value="/mnt/sd/es-de-roms" />'
                ),
            }
        )
        assert ed.roms_dir() == "/mnt/sd/es-de-roms"

    def test_roms_dir_resolves_the_upstream_default_when_unset(self):
        # No es_settings.xml at all: ES-DE's own <home>/ROMs default applies —
        # the user's real home, because EmuDeck launches with no --home.
        ed = self._emudeck({f"{HOME}/ES-DE/custom_systems/es_systems.xml": self.OVERLAY})
        assert ed.roms_dir() == f"{HOME}/ROMs"

    def test_roms_dir_refuses_without_es_de_on_disk(self):
        # No AppImage, no ~/ES-DE tree: there is no frontend whose %ROMPATH%
        # substitution this could be, so the default must not resolve either.
        assert self._emudeck({}).roms_dir() is None

    def test_roms_dir_refuses_rather_than_inventing_a_root(self):
        # A bare string cannot carry which way it refused, so it answers None
        # and the caveated route is rom_location(system).
        ed = self._emudeck(
            {
                f"{HOME}/ES-DE/custom_systems/es_systems.xml": self.OVERLAY,
                f"{HOME}/ES-DE/settings/es_settings.xml": {"status": "unreadable"},
            }
        )
        assert ed.roms_dir() is None

    def test_roms_dir_refuses_the_default_under_a_portable_txt(self):
        # portable.txt may move the very home the <home>/ROMs default derives
        # from, so the default branch stops resolving...
        ed = self._emudeck(
            {
                f"{HOME}/ES-DE/custom_systems/es_systems.xml": self.OVERLAY,
                f"{HOME}/Applications/portable.txt": "",
            }
        )
        assert ed.roms_dir() is None

    def test_roms_dir_still_answers_a_configured_root_under_a_portable_txt(self):
        # ...while a configured absolute value is still answered — the
        # relocation suspicion rides rom_location's caveats, which a bare
        # string cannot carry.
        ed = self._emudeck(
            {
                f"{HOME}/ES-DE/custom_systems/es_systems.xml": self.OVERLAY,
                f"{HOME}/Applications/portable.txt": "",
                f"{HOME}/ES-DE/settings/es_settings.xml": '<string name="ROMDirectory" value="/r" />',
            }
        )
        assert ed.roms_dir() == "/r"

    def test_roms_dir_refuses_a_relative_setting(self):
        ed = self._emudeck(
            {
                f"{HOME}/ES-DE/custom_systems/es_systems.xml": self.OVERLAY,
                f"{HOME}/ES-DE/settings/es_settings.xml": (
                    '<string name="ROMDirectory" value="Emulation/roms" />'
                ),
            }
        )
        assert ed.roms_dir() is None

    def test_roms_dir_expands_a_tilde_setting_against_the_users_home(self):
        ed = self._emudeck(
            {
                f"{HOME}/ES-DE/custom_systems/es_systems.xml": self.OVERLAY,
                f"{HOME}/ES-DE/settings/es_settings.xml": (
                    '<string name="ROMDirectory" value="~/Emulation/roms" />'
                ),
            }
        )
        assert ed.roms_dir() == f"{HOME}/Emulation/roms"

    def test_roms_dir_refuses_a_tilde_setting_under_a_portable_txt(self):
        # portable.txt moves the very home the ~ would expand against — the
        # same reason the unset default stops resolving, on the configured
        # branch that shares its derivation.
        ed = self._emudeck(
            {
                f"{HOME}/ES-DE/custom_systems/es_systems.xml": self.OVERLAY,
                f"{HOME}/Applications/portable.txt": "",
                f"{HOME}/ES-DE/settings/es_settings.xml": (
                    '<string name="ROMDirectory" value="~/Emulation/roms" />'
                ),
            }
        )
        assert ed.roms_dir() is None

    def test_roms_dir_does_not_need_the_catalogue(self):
        # A broken resource shadow refuses every catalogue-shaped answer, but
        # the root question reads es_settings.xml, not es_systems.xml — the
        # same split as RetroDECK, whose roms_dir answers under an unreadable
        # bundled layer.
        ed = self._emudeck(
            {
                f"{HOME}/ES-DE/resources/systems/linux/es_systems.xml": {"status": "unreadable"},
                f"{HOME}/ES-DE/settings/es_settings.xml": '<string name="ROMDirectory" value="/r" />',
            }
        )
        assert ed.roms_dir() == "/r"

    def test_the_entry_route_states_absence_when_esde_vanished(self):
        # A live handle re-reads per query: an entry answered while ES-DE was
        # present can be asked again after it is gone, and the per-game check
        # then states the refusal instead of silently skipping.
        present = self._emudeck(
            {
                f"{HOME}/ES-DE/custom_systems/es_systems.xml": self.OVERLAY,
                f"{HOME}/Emulation/roms/atarijaguar/Game.j64": "",
            }
        )
        entry = present.emulators_for("atarijaguar").entries[0]
        vanished = self._emudeck({f"{HOME}/Emulation/roms/atarijaguar/Game.j64": ""})
        placement = vanished.entry_savefile_location(
            entry._spec,  # pyright: ignore[reportPrivateUsage] - the spec survives the machine change
            content_path=f"{HOME}/Emulation/roms/atarijaguar/Game.j64",
        )
        assert atlas.CAVEAT_EMULATOR_CATALOGUE_UNESTABLISHED in self._codes(placement)


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
        return placed(
            rd.savefile_location(
                content_path="/mnt/sd/retrodeck/roms/psp/Game.iso", core_so="ppsspp_libretro.so"
            )
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

    def test_rejected_sandbox_save_directory_names_where_atlas_looked(self):
        # The cfg line to edit is the sandbox spelling, but the directory atlas
        # found missing is the host one — the message states both, so "missing"
        # can be checked where the check was made (carried over from item 9).
        p = self._psp_query(
            'savefile_directory = "/var/data/gone"\n'
            'sort_savefiles_by_content_enable = "false"\nsort_savefiles_enable = "false"\n'
        )
        rejected = [c for c in p.caveats if c.code == atlas.CAVEAT_INVALID_SAVE_DIRECTORY]
        assert rejected
        assert rejected[0].data["configured"] == "/var/data/gone"
        assert f"{HOME}/.var/app/net.retrodeck.retrodeck/data/gone" in rejected[0].message

    def test_a_host_side_rejection_does_not_repeat_the_same_path(self):
        p = self._psp_query(
            'savefile_directory = "/mnt/sd/gone"\n'
            'sort_savefiles_by_content_enable = "false"\nsort_savefiles_enable = "false"\n'
        )
        rejected = [c for c in p.caveats if c.code == atlas.CAVEAT_INVALID_SAVE_DIRECTORY]
        assert rejected
        assert "host spelling" not in rejected[0].message

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
        p = placed(
            rd.savefile_location(
                content_path="/mnt/sd/retrodeck/roms/3do/Game.chd", core_so="opera_libretro.so"
            )
        )
        assert p.granularity is not None
        assert p.granularity.mode == "shared"

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
        # BareRetroArchNative writes its cfg outside any sandbox: /var/config there
        # is a real host path, and substituting one would answer with a
        # directory this RetroArch never touches.
        machine = FixtureMachine(
            {
                f"{HOME}/.config/retroarch/retroarch.cfg": (
                    'savefile_directory = "/var/config/saves"\n'
                    'sort_savefiles_by_content_enable = "false"\nsort_savefiles_enable = "false"\n'
                ),
                "/var/config/saves/.keep": "",
                f"{HOME}/roms/gba/Game.zip": "",
            }
        )
        p = placed(atlas.BareRetroArchNative(HOME, machine).savefile_location(content_path=f"{HOME}/roms/gba/Game.zip"))
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
        return placed(
            rd.savefile_location(
                content_path="/mnt/sd/retrodeck/roms/psp/Game.iso", core_so="ppsspp_libretro.so"
            )
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
        return placed(
            rd.savefile_location(
                content_path="/mnt/sd/retrodeck/roms/psp/Game.iso", core_so="ppsspp_libretro.so"
            )
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

    # The two directory keys read a blank value in opposite directions, and the
    # pair below is the reason: rgui_config_directory is a handled path setting
    # (configuration.c:1736) that the generic loop writes untested (:6536-6537),
    # savefile_directory is not (:1709, skipped at :6534-6535) and reaches only
    # the block that demands path_is_directory (:6914-6933). Neither is a bug —
    # asserting them side by side is what keeps a later reader from ruling that
    # one of them is.

    def test_blank_rgui_config_directory_clears_it_and_moves_the_tree(self):
        beside = f"{HOME}/.var/app/net.retrodeck.retrodeck/config/retroarch"
        p = self._psp_query(
            self.GLOBAL_CFG + 'rgui_config_directory = ""\n',
            files={
                f"{beside}/PPSSPP/PPSSPP.cfg": 'sort_savefiles_by_content_enable = "true"',
                f"{RETRODECK_OVERRIDES}/PPSSPP/PPSSPP.cfg": 'savefile_directory = "/mnt/sd/wrong"',
                "/mnt/sd/retrodeck/saves/psp/.keep": "",
            },
        )
        # The tree moved: the override beside retroarch.cfg is the one read.
        assert p.dir == "/mnt/sd/retrodeck/saves/psp"

    def test_blank_savefile_directory_in_an_override_changes_nothing(self):
        p = self._psp_query(
            self.GLOBAL_CFG,
            files={f"{RETRODECK_OVERRIDES}/PPSSPP/PPSSPP.cfg": 'savefile_directory = ""'},
        )
        assert p.dir == "/mnt/sd/retrodeck/saves"  # not the platform default
        assert [c.data for c in p.caveats if c.code == atlas.CAVEAT_INVALID_SAVE_DIRECTORY] == [
            {
                "layer": "core override config/PPSSPP/PPSSPP.cfg",
                "configured": "",
                "effective": "/mnt/sd/retrodeck/saves",
            }
        ]

    def test_a_refused_global_root_can_be_rescued_by_an_override(self):
        # The one direction where the standing root is set AFTER the refusal:
        # the global cfg is refused, the override then supplies a usable root.
        # The message must not claim that root predates the rejected file.
        p = self._psp_query(
            'savefile_directory = "/run/media/gone/saves"\n'
            'sort_savefiles_by_content_enable = "false"\nsort_savefiles_enable = "false"\n'
            'libretro_directory = "/app/cores"\n',
            files={
                f"{RETRODECK_OVERRIDES}/PPSSPP/PPSSPP.cfg": 'savefile_directory = "/mnt/sd/other"',
                "/mnt/sd/other/.keep": "",
            },
        )
        assert p.dir == "/mnt/sd/other"
        invalid = [c for c in p.caveats if c.code == atlas.CAVEAT_INVALID_SAVE_DIRECTORY]
        assert [c.data for c in invalid] == [
            {
                "layer": "retroarch.cfg",
                "configured": "/run/media/gone/saves",
                "effective": "/mnt/sd/other",
            }
        ]
        assert "a later file in the chain set" in invalid[0].message
        assert "stood before this file" not in invalid[0].message

    def test_a_refusal_the_chain_never_got_past_names_the_earlier_root(self):
        p = self._psp_query(
            self.GLOBAL_CFG,
            files={
                f"{RETRODECK_OVERRIDES}/PPSSPP/PPSSPP.cfg": 'savefile_directory = "/run/media/gone/saves"'
            },
        )
        invalid = [c for c in p.caveats if c.code == atlas.CAVEAT_INVALID_SAVE_DIRECTORY]
        assert "stood before this file" in invalid[0].message
        assert "a later file in the chain set" not in invalid[0].message

    def test_a_shadowed_override_is_not_the_fallback_for_an_unusable_one(self):
        # The merged config holds the GAME override's root; the core override's
        # was overwritten before any getter saw it, so the refusal falls back to
        # the global cfg's root, not to /mnt/sd/other.
        p = self._psp_query(
            self.GLOBAL_CFG,
            files={
                f"{RETRODECK_OVERRIDES}/PPSSPP/PPSSPP.cfg": 'savefile_directory = "/mnt/sd/other"',
                f"{RETRODECK_OVERRIDES}/PPSSPP/Game.cfg": 'savefile_directory = "/run/media/gone/saves"',
                "/mnt/sd/other/.keep": "",
            },
        )
        assert p.dir == "/mnt/sd/retrodeck/saves"


class TestOstreeHomeIsHostSide:
    """Fedora Silverblue and Bazzite — both ship RetroDECK — make ``/home`` a
    symlink to ``/var/home``, so real home directories live under ``/var``.
    Nothing scopes atlas to SteamOS: ``home`` is whatever the caller passes."""

    HOME = "/var/home/deck"
    CFG = f"{HOME}/.var/app/net.retrodeck.retrodeck/config/retroarch/retroarch.cfg"
    JSON = f"{HOME}/.var/app/net.retrodeck.retrodeck/config/retrodeck/retrodeck.json"
    MARKER = '{"paths": {"rd_home_path": "/var/home/deck/retrodeck", "saves_path": "/var/home/deck/retrodeck/saves"}}'

    def _retrodeck_at(self, home, cfg_body, files=None, cores=None):
        machine = FixtureMachine(
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
        p = placed(rd.savefile_location(content_path=f"{self.HOME}/retrodeck/roms/gba/Game.zip"))
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
        machine = FixtureMachine(
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
        p = placed(
            rd.savefile_location(
                content_path=f"{self.HOME}/retrodeck/roms/gba/Game.zip", core_so="mgba_libretro.so"
            )
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


class _CountingMachine:
    """A machine that records every ``read_text``, so a query's reads can be counted.

    Only ``read_text`` is counted: it is the operation that turns a file's
    *content* into an answer, so it is what "every governing source is read
    exactly once" (DESIGN, consistency model) is about. ``path_kind`` and
    ``glob`` are repeatable probes that carry no revision of a file with them.

    Reads are keyed on the **file**, not on the spelling that reached it:
    ``resolve_links`` follows the seam's own ``readlink`` the way the
    resolver does. Two spellings of one file are one source — the deployed
    ``es_systems.xml`` really is reached through ``current/active``, a symlink
    — so a counter keyed on the spelling would guard "one spelling per source"
    and let a second read through a second spelling walk past it.
    """

    def __init__(self, files, **kwargs):
        self._inner = FixtureMachine(files, **kwargs)
        self.reads: Counter[str] = Counter()

    def read_text(self, path):
        # An unresolvable chain (ELOOP) has no file to key on; the spelling is
        # then the most specific thing there is.
        self.reads[resolve_links(self._inner, path) or path] += 1
        return self._inner.read_text(path)

    def read_appimage_text(self, path, inner_path):
        return self._inner.read_appimage_text(path, inner_path)

    def glob(self, pattern):
        return self._inner.glob(pattern)

    def path_kind(self, path):
        return self._inner.path_kind(path)

    def readlink(self, path):
        return self._inner.readlink(path)

    def query_core(self, so_path):
        return self._inner.query_core(so_path)

    def file_size(self, path):
        return self._inner.file_size(path)

    def file_digest(self, path, algorithm):
        return self._inner.file_digest(path, algorithm)

    def repeats(self) -> dict[str, int]:
        return {path: count for path, count in self.reads.items() if count > 1}


class TestOneReadPerSourcePerQuery:
    """DESIGN's consistency model, enforced: one read per source per query.

    A source read twice inside one answer can be edited between the two reads,
    and the answer then mixes two revisions of the machine — the whole reason
    the handles hold no state. ``firmware_for_system`` did exactly that: it
    read ``retrodeck.json`` and both ``es_systems.xml`` layers, then called
    ``emulators_for``, which read all three again.
    """

    DEPLOY_ESDE = (
        "/var/lib/flatpak/app/net.retrodeck.retrodeck/current/active/files/retrodeck/components"
        "/es-de/share/es-de/resources/systems/linux/es_systems.xml"
    )
    ES_SYSTEMS = (
        '<?xml version="1.0"?>\n<systemList>\n  <system><name>n64</name>\n'
        # A real catalogue always declares <path>; without one there is no ROM
        # directory for a gamelist entry to hang off, so the anchor never
        # resolves and this fixture would never reach the settings read it is
        # here to count.
        "    <path>%ROMPATH%/n64</path>\n"
        '    <command label="Mupen64Plus-Next">retroarch -L '
        "/app/cores/mupen64plus_next_libretro.so %ROM%</command>\n"
        '    <command label="ParaLLEl N64">retroarch -L '
        "/app/cores/parallel_n64_libretro.so %ROM%</command>\n"
        "  </system>\n</systemList>\n"
    )
    GAMELIST = (
        '<?xml version="1.0"?>\n<gameList>\n\t<game>\n\t\t<path>./Game.z64</path>\n'
        "\t\t<altemulator>ParaLLEl N64</altemulator>\n\t</game>\n</gameList>\n"
    )
    FILES = {
        RETRODECK_JSON: RD_JSON,
        RETRODECK_CFG: (
            'savefile_directory = "/mnt/sd/retrodeck/saves"\n'
            'system_directory = "/mnt/sd/retrodeck/bios"\n'
            'libretro_info_path = "/app/cores"\nlibretro_directory = "/app/cores"\n'
            'sort_savefiles_by_content_enable = "false"\nsort_savefiles_enable = "false"\n'
        ),
        DEPLOY_ESDE: ES_SYSTEMS,
        "/mnt/sd/retrodeck/ES-DE/custom_systems/es_systems.xml": '<?xml version="1.0"?>\n<systemList />\n',
        "/mnt/sd/retrodeck/ES-DE/gamelists/n64/gamelist.xml": GAMELIST,
        f"{RD_DEPLOY_CORES}/mupen64plus_next_libretro.info": (
            'display_name = "Mupen64Plus-Next"\nsystemname = "Nintendo 64"\n'
        ),
        f"{RD_DEPLOY_CORES}/parallel_n64_libretro.info": (
            'display_name = "ParaLLEl N64"\nsystemname = "Nintendo 64"\n'
            'firmware_count = "1"\nfirmware0_path = "pifdata.bin"\n'
        ),
        "/mnt/sd/retrodeck/bios/pifdata.bin": "rom",
        "/mnt/sd/retrodeck/saves/.keep": "",
        "/mnt/sd/retrodeck/roms/n64/Game.z64": "",
        ESDE_SETTINGS: (
            '<?xml version="1.0"?>\n<string name="ROMDirectory" value="/mnt/sd/retrodeck/roms" />\n'
        ),
    }
    CORES = {
        f"{RD_DEPLOY_CORES}/mupen64plus_next_libretro.so": {"library_name": "Mupen64Plus-Next"},
        f"{RD_DEPLOY_CORES}/parallel_n64_libretro.so": {"library_name": "ParaLLEl N64"},
    }
    CONTENT = "/mnt/sd/retrodeck/roms/n64/Game.z64"

    INFO = f"{RD_DEPLOY_CORES}/parallel_n64_libretro.info"

    def _query(self, ask):
        machine = _CountingMachine(self.FILES, cores=self.CORES)
        ask(atlas.RetroDeck(HOME, machine))
        # An empty read log would make every assertion below vacuously true.
        assert machine.reads
        return machine

    def test_savefile_location_reads_each_source_once(self):
        machine = self._query(
            lambda rd: placed(rd.savefile_location(content_path=self.CONTENT, core_so="parallel_n64_libretro.so"))
        )
        assert machine.repeats() == {}

    def test_emulators_for_reads_each_source_once(self):
        machine = self._query(lambda rd: rd.emulators_for("n64", content_path=self.CONTENT))
        assert machine.repeats() == {}
        # The anchor's own source, read once like the rest — naming content is
        # what makes this query resolve where ES-DE would launch from.
        assert ESDE_SETTINGS in machine.reads

    def test_emulators_for_without_content_never_opens_the_settings(self):
        # Nothing to anchor, so nothing to anchor against: the query that
        # cannot match a per-game entry does not pay for the directory it
        # would have matched in.
        machine = self._query(lambda rd: rd.emulators_for("n64"))
        assert machine.repeats() == {}
        assert ESDE_SETTINGS not in machine.reads

    def test_firmware_for_system_never_opens_the_settings(self):
        # This route names no content either, and gaining a read it cannot use
        # is exactly the cost the anchor rework had to avoid.
        machine = self._query(lambda rd: rd.firmware_for_system(system="n64"))
        assert ESDE_SETTINGS not in machine.reads

    def test_entry_savefile_location_reads_each_source_once(self):
        # The catalogue ask that produced the entry is its own query; the
        # invariant is per query, so only the entry's own reads are counted.
        machine = _CountingMachine(self.FILES, cores=self.CORES)
        entry = atlas.RetroDeck(HOME, machine).emulators_for("n64").entries[0]
        machine.reads.clear()
        placed(entry.savefile_location(content_path=self.CONTENT))
        assert machine.repeats() == {}
        # This route gained the catalogue and the settings when the anchor
        # moved off the marker: the per-game check needs the system's <path>,
        # and only the catalogue declares one. Once each, like everything else.
        assert self.DEPLOY_ESDE in machine.reads
        assert ESDE_SETTINGS in machine.reads

    def test_savestate_location_reads_each_source_once(self):
        machine = self._query(
            lambda rd: state_placed(rd.savestate_location(content_path=self.CONTENT, core_so="parallel_n64_libretro.so"))
        )
        assert machine.repeats() == {}
        # The support declaration is a real read, and the only source this
        # question has that its savefile twin does not.
        assert self.INFO in machine.reads

    def test_entry_savestate_location_reads_each_source_once(self):
        # Same shape as the entry save route: the catalogue ask that produced
        # the entry is its own query, so only the entry's own reads count.
        machine = _CountingMachine(self.FILES, cores=self.CORES)
        entry = atlas.RetroDeck(HOME, machine).emulators_for("n64").entries[0]
        machine.reads.clear()
        state_placed(entry.savestate_location(content_path=self.CONTENT))
        assert machine.repeats() == {}
        assert self.DEPLOY_ESDE in machine.reads
        assert ESDE_SETTINGS in machine.reads

    def test_firmware_for_core_reads_each_source_once(self):
        machine = self._query(lambda rd: rd.firmware_for_core(core_so="parallel_n64_libretro.so"))
        assert machine.repeats() == {}
        assert self.INFO in machine.reads  # the declarations were really read

    def test_firmware_for_system_reads_each_source_once(self):
        machine = self._query(lambda rd: rd.firmware_for_system(system="n64"))
        assert machine.repeats() == {}
        # The sources the two halves of this query share, actually read.
        assert RETRODECK_JSON in machine.reads
        assert self.DEPLOY_ESDE in machine.reads

    def test_firmware_inventory_reads_each_source_once(self):
        machine = self._query(lambda rd: rd.firmware_inventory())
        assert machine.repeats() == {}
        assert self.INFO in machine.reads

    def test_identify_firmware_reads_each_source_once(self):
        machine = self._query(lambda rd: rd.identify_firmware(md5="0" * 32))
        assert machine.repeats() == {}
        assert self.INFO in machine.reads

    def test_rom_location_reads_each_source_once(self):
        # This one reaches furthest: the marker, both catalogue layers, and
        # ES-DE's settings.
        machine = self._query(lambda rd: rd.rom_location("n64"))
        assert machine.repeats() == {}
        assert self.DEPLOY_ESDE in machine.reads

    def test_systems_reads_each_source_once(self):
        # The listing reads the marker for its root and again, in effect, for
        # the health findings it carries — one read has to serve both.
        machine = self._query(lambda rd: rd.systems())
        assert machine.repeats() == {}
        assert RETRODECK_JSON in machine.reads

    def test_an_emudeck_query_reads_each_source_once(self):
        # The other handle family, where the companion cfg is what two things
        # want: the context reads its text, the health finding its status.
        machine = _CountingMachine(
            {
                EMUDECK_SETTINGS: 'romsPath="$HOME/Emulation/roms"\nsavesPath="$HOME/Emulation/saves"\n',
                STANDALONE_CFG: 'system_directory = "/home/deck/Emulation/bios"\n',
            }
        )
        atlas.EmuDeck(HOME, machine).firmware_inventory()
        assert machine.reads
        assert machine.repeats() == {}
        assert STANDALONE_CFG in machine.reads

    def test_an_emudeck_catalogue_query_reads_each_source_once(self):
        machine = _CountingMachine(
            {EMUDECK_SETTINGS: 'savesPath="$HOME/Emulation/saves"\n', STANDALONE_CFG: ""}
        )
        atlas.EmuDeck(HOME, machine).systems()
        assert machine.reads
        assert machine.repeats() == {}

    def test_the_counter_would_see_a_repeat(self):
        # The guard above proves nothing unless a second read is visible.
        machine = self._query(lambda rd: (rd.root(), rd.saves_root()))
        assert machine.repeats() == {RETRODECK_JSON: 2}

    def test_the_counter_sees_one_file_under_two_spellings(self):
        # …and nothing unless it counts the file rather than the spelling: the
        # deployed catalogue is reached through `current/active`, a symlink, so
        # a second read spelled the other way must not read as a first read.
        deployed = "/var/lib/flatpak/app/x/1.0/files/es_systems.xml"
        machine = _CountingMachine(
            {deployed: "<systemList />"},
            symlinks={"/var/lib/flatpak/app/x/current": "/var/lib/flatpak/app/x/1.0"},
        )
        machine.read_text(deployed)
        machine.read_text("/var/lib/flatpak/app/x/current/files/es_systems.xml")
        assert machine.repeats() == {deployed: 2}

    def test_firmware_for_system_answers_what_the_catalogue_answers(self):
        # Threading the snapshot must not change the enumeration it produces.
        machine = FixtureMachine(self.FILES, cores=self.CORES)
        rd = atlas.RetroDeck(HOME, machine)
        answer = rd.firmware_for_system(system="n64")
        assert [c.core_so for c in answer.cores] == [
            e.core_so for e in rd.emulators_for("n64").entries
        ]

    # The four Flatpak overrides files, in the order _override_files composes
    # them for a system-deployed app — flatpak's own merge order: the global
    # file before the per-app one within an installation, the system
    # installation before the user one (flatpak-dir.c:1518-1567).
    OVERRIDE_FILES = (
        "/var/lib/flatpak/overrides/global",
        "/var/lib/flatpak/overrides/net.retrodeck.retrodeck",
        f"{HOME}/.local/share/flatpak/overrides/global",
        f"{HOME}/.local/share/flatpak/overrides/net.retrodeck.retrodeck",
    )

    def test_the_cfg_reading_questions_read_the_overrides_files_once(self):
        # Since the XDG pin made the config home unmovable, the override
        # files decide exactly one thing on this handle: what a ~ in a cfg
        # value expands against (the sandbox's HOME). So the questions that
        # read retroarch.cfg compose them — once each per query, which is
        # what repeats() == {} adds on top of the membership check. This
        # fixture deploys the app in the system installation, so all four
        # files apply.
        content, core = self.CONTENT, "parallel_n64_libretro.so"
        questions = {
            "savefile_location": lambda rd: placed(rd.savefile_location(content_path=content, core_so=core)),
            "savestate_location": lambda rd: state_placed(rd.savestate_location(content_path=content, core_so=core)),
            "firmware_for_core": lambda rd: rd.firmware_for_core(core_so=core),
            "firmware_for_system": lambda rd: rd.firmware_for_system(system="n64"),
            "firmware_inventory": lambda rd: rd.firmware_inventory(),
            "identify_firmware": lambda rd: rd.identify_firmware(md5="0" * 32),
        }
        for question, ask in questions.items():
            machine = self._query(ask)
            assert machine.repeats() == {}, question
            for path in self.OVERRIDE_FILES:
                assert path in machine.reads, f"{question} did not read {path}"

    def test_the_catalogue_questions_do_not_read_the_overrides_files(self):
        # The other half of the same fact: ES-DE's --home is the pinned
        # XDG_CONFIG_HOME, no cfg is read, and an environment that cannot
        # move anything these answers rest on is not a source of theirs.
        content = self.CONTENT
        questions = {
            "systems": lambda rd: rd.systems(),
            "emulators_for": lambda rd: rd.emulators_for("n64"),
            "emulators_for_content": lambda rd: rd.emulators_for("n64", content_path=content),
            "rom_location": lambda rd: rd.rom_location("n64"),
            "roms_dir": lambda rd: rd.roms_dir(),
        }
        for question, ask in questions.items():
            machine = self._query(ask)
            assert machine.repeats() == {}, question
            for path in self.OVERRIDE_FILES:
                assert path not in machine.reads, f"{question} read {path}"

    def test_the_entry_routes_read_the_overrides_files_once(self):
        # The composed path: the entry's placement query reads the cfg, so it
        # composes the override files — once, however many resolutions the
        # answer assembles.
        for ask in (
            lambda entry: placed(entry.savefile_location(content_path=self.CONTENT)),
            lambda entry: state_placed(entry.savestate_location(content_path=self.CONTENT)),
        ):
            machine = _CountingMachine(self.FILES, cores=self.CORES)
            entry = atlas.RetroDeck(HOME, machine).emulators_for("n64").entries[0]
            machine.reads.clear()
            ask(entry)
            assert machine.repeats() == {}
            for path in self.OVERRIDE_FILES:
                assert path in machine.reads, f"the entry route did not read {path}"


class TestCfgDirectorySpelling:
    """A cfg names its directories however whoever wrote the line spelled them.

    ``some/dir`` and ``some/dir/`` are one directory to the machine, and the
    keys travel straight from the cfg into ``path_kind`` (``_cfg_directory``),
    so an answer that depended on the trailing slash would report an unresolved
    core catalogue for an installation that is entirely fine.
    """

    INFO = f"{RD_DEPLOY_CORES}/pcsx2_libretro.info"

    def _machine(self, slash):
        return FixtureMachine(
            {
                RETRODECK_JSON: RD_JSON,
                RETRODECK_CFG: (
                    f'system_directory = "/mnt/sd/retrodeck/bios{slash}"\n'
                    f'libretro_info_path = "/app/cores{slash}"\n'
                    f'libretro_directory = "/app/cores{slash}"\n'
                    f'savefile_directory = "/mnt/sd/retrodeck/saves{slash}"\n'
                ),
                self.INFO: 'display_name = "LRPS2"\nfirmware_count = "1"\nfirmware0_path = "ps2/bios/rom1.bin"\n',
                "/mnt/sd/retrodeck/bios/ps2/bios/rom1.bin": "rom",
                "/mnt/sd/retrodeck/saves/.keep": "",
            },
            cores={f"{RD_DEPLOY_CORES}/pcsx2_libretro.so": {"library_name": "LRPS2"}},
        )

    def test_a_trailing_slash_on_a_cfg_directory_changes_no_answer(self):
        plain = atlas.RetroDeck(HOME, self._machine("")).firmware_inventory()
        slashed = atlas.RetroDeck(HOME, self._machine("/")).firmware_inventory()
        assert [c.core_so for c in slashed.cores] == [c.core_so for c in plain.cores]
        assert [c.core_so for c in plain.cores] == ["pcsx2_libretro.so"]
        assert [c.code for c in slashed.caveats] == [c.code for c in plain.caveats]
        assert [r.found for c in slashed.cores for r in c.requirements] == ["file"]


class TestBareRetroArch:
    def test_native_upstream_default_sorts_by_core(self):
        # config.def.h:982 — upstream defaults to sort-by-core.
        machine = FixtureMachine(
            {
                f"{HOME}/.config/retroarch/retroarch.cfg": 'savefile_directory = "~/saves"\n',
                f"{HOME}/saves/.keep": "",
                f"{HOME}/roms/gba/Game.zip": "",
            }
        )
        inst = atlas.BareRetroArchNative(HOME, machine)
        p = placed(inst.savefile_location(content_path=f"{HOME}/roms/gba/Game.zip"))
        assert p.dir == f"{HOME}/saves/<library_name>"
        assert p.needs == ("library_name",)


class TestEveryHandleAnswersTheCatalogueQuestion:
    """The question is about an arrangement, so no handle may decline to answer it.

    Before, only RetroDECK had the method and a consumer had to narrow with
    ``isinstance`` — which meant deciding, without help, what the other
    arrangements would have said. They say it themselves now, and the two
    reasons for having nothing to say are different claims that must not
    collapse: one is a fact about the machine, the other is about atlas.
    """

    RA_FILES = {
        f"{HOME}/.config/retroarch/retroarch.cfg": 'savefile_directory = "~/saves"\n',
        f"{HOME}/saves/.keep": "",
    }
    EMUDECK_FILES = {
        EMUDECK_SETTINGS: 'romsPath="$HOME/Emulation/roms"\nsavesPath="$HOME/Emulation/saves"\n',
        STANDALONE_CFG: 'savefile_directory = "~/Emulation/saves"\n',
    }
    DEPLOY_ESDE = (
        "/var/lib/flatpak/app/net.retrodeck.retrodeck/current/active/files/retrodeck/components"
        "/es-de/share/es-de/resources/systems/linux/es_systems.xml"
    )

    def _only(self, files, **kwargs):
        return atlas.detect(HOME, FixtureMachine(files, **kwargs))[0]

    def test_a_bare_retroarch_states_the_arrangement_has_none(self):
        # A settled fact: RetroArch ships no frontend catalogue, and no
        # evidence work would change that. The evidence caveat beside it is a
        # different claim — no bare install has been observed live — and every
        # answer from an unverified arrangement carries it (atlas.evidence).
        answer = self._only(self.RA_FILES).emulators_for("n64")
        assert answer.entries == ()
        assert [c.code for c in answer.caveats] == [
            atlas.CAVEAT_EMULATOR_CATALOGUE_UNAVAILABLE,
            atlas.CAVEAT_ARRANGEMENT_UNVERIFIED,
        ]

    def test_an_emudeck_arrangement_states_that_atlas_has_not_established_it(self):
        # EmuDeck installs a frontend; which one, and where it keeps its
        # catalogue, is what nobody has established. Saying "none" here would
        # be atlas reporting its own gap as a property of the machine.
        answer = self._only(self.EMUDECK_FILES, dirs=[f"{HOME}/Emulation/saves"]).emulators_for("n64")
        assert answer.entries == ()
        assert [c.code for c in answer.caveats] == [
            atlas.CAVEAT_EMULATOR_CATALOGUE_UNESTABLISHED,
        ]

    def test_the_two_refusals_are_different_codes(self):
        # The whole point of two: a client rendering "no emulators" may act on
        # the first and must not act on the second.
        bare = self._only(self.RA_FILES).emulators_for("n64")
        emudeck = self._only(self.EMUDECK_FILES, dirs=[f"{HOME}/Emulation/saves"]).emulators_for("n64")
        assert bare.caveats[0].code != emudeck.caveats[0].code

    def test_the_systems_question_answers_the_same_way(self):
        answer = self._only(self.RA_FILES).systems()
        assert answer.systems == ()
        assert [c.code for c in answer.caveats] == [
            atlas.CAVEAT_EMULATOR_CATALOGUE_UNAVAILABLE,
            atlas.CAVEAT_ARRANGEMENT_UNVERIFIED,
        ]

    def test_an_unreadable_retrodeck_catalogue_is_not_an_empty_one(self):
        """The defect this answer object exists for, on the handle that has a catalogue.

        The read flag was computed and thrown away, so an ``es_systems.xml``
        that could not be read produced the same empty tuple as a catalogue
        that was read and declares nothing — and the caller could not tell that
        atlas had never looked.
        """
        machine = FixtureMachine(
            {RETRODECK_JSON: RD_JSON, self.DEPLOY_ESDE: {"status": "unreadable"}},
            dirs=["/mnt/sd/retrodeck/saves"],
        )
        rd = atlas.RetroDeck(HOME, machine)
        for answer in (rd.emulators_for("n64"), rd.systems()):
            assert [c.code for c in answer.caveats] == [atlas.CAVEAT_EMULATOR_CATALOGUE_UNREADABLE]
        assert rd.emulators_for("n64").entries == ()
        assert rd.systems().systems == ()

    def test_a_read_catalogue_that_knows_no_emulator_is_silent_on_a_healthy_installation(self):
        # The one empty answer a client may act on: read, and the frontend
        # genuinely knows nothing for this system. The empty caveat list is
        # this fixture's health talking too — on a broken installation the
        # findings sit here, which is why the client's test is the three
        # emulator-catalogue-* codes rather than `not answer.caveats`.
        machine = FixtureMachine(
            {
                RETRODECK_JSON: RD_JSON,
                self.DEPLOY_ESDE: '<?xml version="1.0"?>\n<systemList>\n  <system><name>n64</name>'
                '<path>%ROMPATH%/n64</path>\n    <command label="Mupen64Plus-Next">'
                "%EMULATOR_RETROARCH% -L %CORE_RETROARCH%/mupen64plus_next_libretro.so %ROM%"
                "</command>\n  </system>\n</systemList>\n",
            },
            dirs=["/mnt/sd/retrodeck/saves"],
        )
        answer = atlas.RetroDeck(HOME, machine).emulators_for("dreamcast")
        assert answer.entries == ()
        assert answer.caveats == ()


class TestTheRomDirectorySettingIsReadOrRefused:
    """Which empty an ``es_settings.xml`` is decides whether the default may apply.

    ES-DE falls back on its own ``<home>/ROMs`` only where the setting is not
    set. A file that exists and could not be read says nothing about the
    setting, so answering the default there would state a directory belonging
    to a configuration nobody established — the collapse this suite guards.
    """

    DEPLOY_ESDE = (
        "/var/lib/flatpak/app/net.retrodeck.retrodeck/current/active/files/retrodeck/components"
        "/es-de/share/es-de/resources/systems/linux/es_systems.xml"
    )
    ES_SYSTEMS = (
        '<?xml version="1.0"?>\n<systemList>\n  <system><name>n64</name>\n'
        "    <path>%ROMPATH%/n64</path>\n    <extension>.z64 .Z64</extension>\n"
        '    <command label="Mupen64Plus-Next">retroarch -L '
        "/app/cores/mupen64plus_next_libretro.so %ROM%</command>\n  </system>\n</systemList>\n"
    )
    SETTINGS = ESDE_SETTINGS
    CONFIG_HOME = f"{HOME}/.var/app/net.retrodeck.retrodeck/config"
    DEFAULT_DIR = f"{CONFIG_HOME}/ROMs/n64"
    UNREADABLE = {"status": "unreadable"}
    NOT_ABSOLUTE = "Emulation/roms"
    NOT_ABSOLUTE_SETTINGS = f'<string name="ROMDirectory" value="{NOT_ABSOLUTE}" />'
    TILDE_SETTINGS = '<string name="ROMDirectory" value="~/Emulation/roms" />'

    def _placement(self, settings=None, system="n64"):
        files = {RETRODECK_JSON: RD_JSON, self.DEPLOY_ESDE: self.ES_SYSTEMS}
        if settings is not None:
            files[self.SETTINGS] = settings
        machine = FixtureMachine(files, dirs=["/mnt/sd/retrodeck/saves"])
        return atlas.RetroDeck(HOME, machine).rom_location(system)

    @staticmethod
    def _cites_the_settings(placement) -> bool:
        """Whether the answer claims to have read ES-DE's settings file."""
        return any("es_settings.xml" in source for source in placement.sources)

    def test_no_settings_file_at_all_resolves_the_frontends_own_default(self):
        # Missing is a reading: there is no file, so there is no configured
        # value, and the frontend's home-relative default is what applies.
        placement = self._placement()
        assert placement.dir == self.DEFAULT_DIR
        assert placement.caveats == ()

    def test_a_file_that_sets_the_key_empty_resolves_the_default_too(self):
        placement = self._placement('<string name="ROMDirectory" value="" />')
        assert placement.dir == self.DEFAULT_DIR
        assert placement.caveats == ()

    def test_a_file_that_names_no_such_key_resolves_the_default_too(self):
        placement = self._placement('<string name="MediaDirectory" value="/media" />')
        assert placement.dir == self.DEFAULT_DIR
        assert placement.caveats == ()

    def test_settings_that_cannot_be_read_state_no_directory(self):
        placement = self._placement(self.UNREADABLE)
        assert placement.dir is None
        assert [c.code for c in placement.caveats] == [atlas.CAVEAT_FRONTEND_SETTINGS_UNREADABLE]

    def test_the_unreadable_caveat_names_the_file_and_the_read_status(self):
        caveat = self._placement(self.UNREADABLE).caveats[0]
        assert caveat.data == {"system": "n64", "path": self.SETTINGS, "status": "unreadable"}

    def test_settings_whose_bytes_are_not_text_state_no_directory(self):
        placement = self._placement({"status": "invalid-text"})
        assert [c.code for c in placement.caveats] == [atlas.CAVEAT_FRONTEND_SETTINGS_UNREADABLE]
        assert placement.caveats[0].data["status"] == "invalid-text"

    def test_settings_that_do_not_parse_state_no_directory(self):
        placement = self._placement("this is not xml <<<")
        assert [c.code for c in placement.caveats] == [atlas.CAVEAT_FRONTEND_SETTINGS_UNREADABLE]
        assert placement.caveats[0].data["status"] == "unparseable"

    def test_an_unreadable_file_is_not_the_default_case(self):
        # The collapse itself: the two must not answer the same directory.
        assert self._placement(self.UNREADABLE).dir != self._placement().dir

    def test_the_extensions_survive_settings_nobody_could_read(self):
        # Which files launch is declared in the same element and does not
        # depend on where they sit.
        assert self._placement(self.UNREADABLE).extensions == (".z64", ".Z64")

    def test_a_resolving_answer_cites_the_settings_it_read(self):
        # The counterpart the two assertions below would be vacuous without.
        assert self._cites_the_settings(self._placement('<string name="ROMDirectory" value="/r" />'))

    def test_an_unreadable_settings_file_is_not_cited_as_a_source(self):
        # A source names a reading the answer rests on, and this one rests on
        # the failure — which the caveat states instead.
        assert not self._cites_the_settings(self._placement(self.UNREADABLE))

    def test_a_relative_setting_is_refused_rather_than_guessed(self):
        # Relative resolves against the ES-DE process's working directory,
        # which atlas has not established. ~ is not this case: the frontend
        # expands it against a home this handle reads (below).
        placement = self._placement(self.NOT_ABSOLUTE_SETTINGS)
        assert placement.dir is None
        assert [c.code for c in placement.caveats] == [atlas.CAVEAT_ROM_PATH_UNRESOLVED]

    def test_a_tilde_setting_expands_against_the_frontends_own_home(self):
        # ES-DE expands every ~ in the setting (FileData.cpp:289 via
        # expandHomePath), and this frontend's home is the launcher's
        # --home "${XDG_CONFIG_HOME}" — the per-app config home, not the
        # user's: the same home the empty-setting default derives from.
        placement = self._placement(self.TILDE_SETTINGS)
        assert placement.dir == f"{self.CONFIG_HOME}/Emulation/roms/n64"
        assert placement.caveats == ()

    def test_the_expansion_home_is_not_the_users(self):
        # The discriminating direction: a naive expansion against $HOME names
        # a real directory ES-DE never launches from.
        assert self._placement(self.TILDE_SETTINGS).dir != f"{HOME}/Emulation/roms/n64"

    def test_a_tilde_anywhere_is_replaced_as_text(self):
        # Not a shell's tilde grammar: expandHomePath is a plain replace of
        # every ~, so ~roms is the home with "roms" glued on — and that
        # directory, odd or not, is the one the frontend launches from.
        placement = self._placement('<string name="ROMDirectory" value="~roms" />')
        assert placement.dir == f"{self.CONFIG_HOME}roms/n64"

    def test_a_refusal_names_the_raw_text_even_when_a_tilde_expanded(self):
        # Emulation/~/roms expands and is still relative: the refusal must
        # carry the setting's own text — the value whose remedy is an edit —
        # never the half-expanded string.
        placement = self._placement('<string name="ROMDirectory" value="Emulation/~/roms" />')
        assert placement.dir is None
        caveat = placement.caveats[0]
        assert caveat.code == atlas.CAVEAT_ROM_PATH_UNRESOLVED
        assert caveat.data["configured"] == "Emulation/~/roms"

    def test_a_tilde_setting_expands_the_same_under_an_xdg_override(self):
        # A Flatpak override redefining XDG_CONFIG_HOME never reaches the
        # app: flatpak force-pins the XDG_*_HOME variables to the per-app
        # directories after applying every override (flatpak-context.c:
        # 3158-3187 via flatpak-run.c:3574, at 1.16.6), so the frontend's
        # --home — and with it both the unset default and this expansion —
        # stays exactly where atlas read it.
        machine = FixtureMachine(
            {
                RETRODECK_JSON: RD_JSON,
                self.DEPLOY_ESDE: self.ES_SYSTEMS,
                self.SETTINGS: self.TILDE_SETTINGS,
                f"{HOME}/.local/share/flatpak/overrides/net.retrodeck.retrodeck": (
                    "[Environment]\nXDG_CONFIG_HOME=/mnt/elsewhere/config\n"
                ),
            },
            dirs=["/mnt/sd/retrodeck/saves"],
        )
        placement = atlas.RetroDeck(HOME, machine).rom_location("n64")
        assert placement.dir == f"{self.CONFIG_HOME}/Emulation/roms/n64"
        assert placement.caveats == ()

    def test_the_refusal_names_the_declaration_and_the_configured_value(self):
        caveat = self._placement(self.NOT_ABSOLUTE_SETTINGS).caveats[0]
        assert caveat.data == {
            "system": "n64",
            "declared": "%ROMPATH%/n64",
            "configured": self.NOT_ABSOLUTE,
        }

    def test_the_refusal_says_which_value_it_would_not_resolve_against(self):
        # The message is prose and non-contractual, but a reason that never
        # names the offending value leaves nothing to act on.
        caveat = self._placement(self.NOT_ABSOLUTE_SETTINGS).caveats[0]
        assert self.NOT_ABSOLUTE in caveat.message
        assert "not an absolute path" in caveat.message

    def test_roms_dir_answers_the_root_es_de_substitutes(self):
        # The root, not a system's directory — no <path> is applied to it.
        machine = FixtureMachine(
            {RETRODECK_JSON: RD_JSON, ESDE_SETTINGS: '<string name="ROMDirectory" value="/r" />'},
            dirs=["/mnt/sd/retrodeck/saves"],
        )
        assert atlas.RetroDeck(HOME, machine).roms_dir() == "/r"

    def test_roms_dir_no_longer_follows_the_markers_roms_path(self):
        machine = FixtureMachine(
            {
                RETRODECK_JSON: (
                    '{"paths": {"rd_home_path": "/mnt/sd/retrodeck", '
                    '"saves_path": "/mnt/sd/retrodeck/saves", "roms_path": "/mnt/sd/games"}}'
                ),
                ESDE_SETTINGS: '<string name="ROMDirectory" value="/mnt/sd/es-de-roms" />',
            },
            dirs=["/mnt/sd/retrodeck/saves"],
        )
        assert atlas.RetroDeck(HOME, machine).roms_dir() == "/mnt/sd/es-de-roms"

    def test_roms_dir_refuses_rather_than_inventing_a_root(self):
        # A bare string cannot carry which of the three ways it refused, so it
        # answers None and the caveated route is rom_location(system).
        machine = FixtureMachine(
            {RETRODECK_JSON: RD_JSON, ESDE_SETTINGS: {"status": "unreadable"}},
            dirs=["/mnt/sd/retrodeck/saves"],
        )
        assert atlas.RetroDeck(HOME, machine).roms_dir() is None

    def test_the_undeclared_branch_never_opens_the_settings_file(self):
        # Fixed with the sources: this answer returns before reading them, so
        # citing ROMDirectory would claim a reading that did not happen.
        placement = self._placement(system="dreamcast")
        assert [c.code for c in placement.caveats] == [atlas.CAVEAT_ROM_PATH_UNDECLARED]
        assert not self._cites_the_settings(placement)


class TestEveryAnswerStatesTheInstallationsHealth:
    """A broken installation says so on every answer, not only on placements.

    The rule is blanket on purpose: a finding is a true statement about the
    installation whatever was asked, while a map of which finding affects which
    answer would have to be maintained and could rot silently. What broke is in
    the ``data``; judging relevance is the client's.

    The findings come from the reads each route already makes — never from a
    second ``health()`` call inside a query — so the one-read-per-source
    invariant above covers this wiring too.
    """

    ESDE = (
        "/var/lib/flatpak/app/net.retrodeck.retrodeck/current/active/files/retrodeck/components"
        "/es-de/share/es-de/resources/systems/linux/es_systems.xml"
    )
    ES_SYSTEMS = (
        '<?xml version="1.0"?>\n<systemList>\n  <system><name>n64</name>\n'
        '    <command label="Mupen64Plus-Next">retroarch -L '
        "/app/cores/mupen64plus_next_libretro.so %ROM%</command>\n  </system>\n</systemList>\n"
    )
    # A marker whose `saves_path` is not a string: present, parseable, and
    # unusable — so the roots fall back to defaults that do not exist either.
    BROKEN = {RETRODECK_JSON: '{"paths": {"rd_home_path": "/mnt/sd/retrodeck", "saves_path": 7}}'}
    HEALTHY = {RETRODECK_JSON: RD_JSON, "/mnt/sd/retrodeck/roms/systeminfo.txt": ""}
    HEALTHY_DIRS = ["/mnt/sd/retrodeck/saves"]
    CORE_SO = "mgba_libretro.so"

    def _answers(self, rd) -> dict[str, tuple[atlas.Caveat, ...]]:
        return {
            "savefile_location": placed(rd.savefile_location(core_so=self.CORE_SO)).caveats,
            "systems": rd.systems().caveats,
            "emulators_for": rd.emulators_for("n64").caveats,
            "firmware_for_core": rd.firmware_for_core(core_so=self.CORE_SO).caveats,
            "firmware_for_system": rd.firmware_for_system(system="n64").caveats,
            "firmware_inventory": rd.firmware_inventory().caveats,
            "identify_firmware": rd.identify_firmware(md5="deadbeef").caveats,
        }

    def test_the_fixture_is_broken_in_three_ways(self):
        assert _retrodeck(self.BROKEN).health().codes == (
            atlas.HEALTH_ISSUE_MARKER_INVALID,
            atlas.HEALTH_ISSUE_ROOT_MISSING,
            atlas.HEALTH_ISSUE_SAVES_ROOT_MISSING,
        )

    def test_every_answer_leads_with_the_findings(self):
        rd = _retrodeck(self.BROKEN)
        findings = list(rd.health().issues)
        led = {q: list(c[: len(findings)]) for q, c in self._answers(rd).items()}
        assert led == {question: findings for question in led}

    def test_each_finding_arrives_exactly_once(self):
        rd = _retrodeck(self.BROKEN)
        codes = rd.health().codes
        repeated = {
            question: sorted(c for c in codes if [x.code for x in caveats].count(c) != 1)
            for question, caveats in self._answers(rd).items()
        }
        assert {q: r for q, r in repeated.items() if r} == {}

    def test_a_finding_keeps_its_own_message(self):
        # The re-wrap tell: a category prefix or a rebuilt message shows here.
        rd = _retrodeck(self.BROKEN)
        finding = rd.health().issues[0]
        carried = rd.firmware_inventory().caveats[0]
        assert (carried.code, carried.message, dict(carried.data)) == (
            finding.code,
            finding.message,
            dict(finding.data),
        )

    def test_a_healthy_installation_adds_nothing(self):
        rd = _retrodeck(self.HEALTHY, dirs=self.HEALTHY_DIRS)
        health_codes = {
            atlas.HEALTH_ISSUE_MARKER_INVALID,
            atlas.HEALTH_ISSUE_ROOT_MISSING,
            atlas.HEALTH_ISSUE_SAVES_ROOT_MISSING,
        }
        stated = {
            question: [c.code for c in caveats if c.code in health_codes]
            for question, caveats in self._answers(rd).items()
        }
        assert {q: codes for q, codes in stated.items() if codes} == {}

    def test_the_entry_route_carries_them_too(self):
        # The composed path — a catalogue entry answering for its own core —
        # goes through the placement seam, so it should come free. Checked.
        rd = _retrodeck({**self.BROKEN, self.ESDE: self.ES_SYSTEMS})
        entry = rd.emulators_for("n64").entries[0]
        placement = placed(entry.savefile_location())
        assert not isinstance(placement, atlas.Unresolved)
        carried = [c.code for c in placement.caveats]
        assert carried[: len(rd.health().codes)] == list(rd.health().codes)


class TestAFlatpakOverrideCannotMoveTheConfigHome:
    """An XDG override is inert: the config home a RetroDECK answer reads is the one in force.

    flatpak force-pins the ``XDG_*_HOME`` variables to the per-app
    directories AFTER applying every override and ``--env``
    (flatpak-context.c:3158-3187 applied via flatpak-run.c:3574, against the
    override env applied at :3352, both with overwrite; flatpak 1.16.6;
    flatpak-run(1) documents the pin, flatpak/flatpak#4529 closed the request
    to lift it). So an ``[Environment]`` override naming ``XDG_CONFIG_HOME``
    never reaches the app, the tree atlas reads is exactly the tree the
    frontend and its emulators use, and no answer carries doubt about it —
    the old relocation refusals and riders were stating doubt the pinned
    source refutes.
    """

    DEPLOY_ESDE = (
        "/var/lib/flatpak/app/net.retrodeck.retrodeck/current/active/files/retrodeck/components"
        "/es-de/share/es-de/resources/systems/linux/es_systems.xml"
    )
    ES_SYSTEMS = (
        '<?xml version="1.0"?>\n<systemList>\n  <system><name>n64</name>\n'
        "    <path>%ROMPATH%/n64</path>\n    <extension>.z64 .Z64</extension>\n"
        '    <command label="Mupen64Plus-Next">retroarch -L '
        "/app/cores/mupen64plus_next_libretro.so %ROM%</command>\n  </system>\n</systemList>\n"
    )
    FILES = {
        RETRODECK_JSON: RD_JSON,
        RETRODECK_CFG: (
            'savefile_directory = "/mnt/sd/retrodeck/saves"\n'
            'system_directory = "/mnt/sd/retrodeck/bios"\n'
            'libretro_info_path = "/app/cores"\nlibretro_directory = "/app/cores"\n'
            'sort_savefiles_by_content_enable = "false"\nsort_savefiles_enable = "false"\n'
        ),
        DEPLOY_ESDE: ES_SYSTEMS,
        f"{RD_DEPLOY_CORES}/mupen64plus_next_libretro.info": 'display_name = "Mupen64Plus-Next"\n',
        "/mnt/sd/retrodeck/saves/.keep": "",
        "/mnt/sd/retrodeck/bios/.keep": "",
        "/mnt/sd/retrodeck/roms/n64/Game.z64": "",
        ESDE_SETTINGS: '<string name="ROMDirectory" value="/mnt/sd/retrodeck/roms" />',
    }
    CORES = {f"{RD_DEPLOY_CORES}/mupen64plus_next_libretro.so": {"library_name": "Mupen64Plus-Next"}}
    CORE = "mupen64plus_next_libretro.so"
    CONTENT = "/mnt/sd/retrodeck/roms/n64/Game.z64"
    OVERRIDE = f"{HOME}/.local/share/flatpak/overrides/net.retrodeck.retrodeck"
    MOVED = "[Context]\nfilesystems=host;\n\n[Environment]\nXDG_CONFIG_HOME=/mnt/elsewhere/config\n"
    # The settings file with ROMDirectory unset: the state the home-derived
    # default applies to, which is the state the ROM refusal guards.
    UNSET_SETTINGS = '<string name="ROMDirectory" value="" />'

    # The pinned default config home — where the XDG pin keeps every read.
    CONFIG_HOME = f"{HOME}/.var/app/net.retrodeck.retrodeck/config"

    def _rd(self, extra=None):
        return _retrodeck({**self.FILES, **(extra or {})}, cores=self.CORES)

    def _relocated(self, extra=None):
        return self._rd({self.OVERRIDE: self.MOVED, **(extra or {})})

    def _questions(self, rd):
        return {
            "savefile_location": placed(rd.savefile_location(content_path=self.CONTENT, core_so=self.CORE)),
            "savestate_location": state_placed(rd.savestate_location(content_path=self.CONTENT, core_so=self.CORE)),
            "systems": rd.systems(),
            "emulators_for": rd.emulators_for("n64"),
            "emulators_for_content": rd.emulators_for("n64", content_path=self.CONTENT),
            "rom_location": rd.rom_location("n64"),
            "firmware_for_core": rd.firmware_for_core(core_so=self.CORE),
            "firmware_for_system": rd.firmware_for_system(system="n64"),
            "firmware_inventory": rd.firmware_inventory(),
            "identify_firmware": rd.identify_firmware(md5="0" * 32),
        }

    def _answers(self, rd):
        """Contract serializations plus the placements' sources — entry objects hold their handle, so raw equality cannot compare two handles' answers."""
        from atlas.contract import (
            catalogue_contract,
            firmware_contract,
            identification_contract,
            rom_placement_contract,
            savefile_placement_contract,
            savestate_placement_contract,
            systems_contract,
        )

        serializers = {
            "savefile_location": savefile_placement_contract,
            "savestate_location": savestate_placement_contract,
            "systems": systems_contract,
            "emulators_for": catalogue_contract,
            "emulators_for_content": catalogue_contract,
            "rom_location": rom_placement_contract,
            "firmware_for_core": firmware_contract,
            "firmware_for_system": firmware_contract,
            "firmware_inventory": firmware_contract,
            "identify_firmware": identification_contract,
        }
        return {
            question: (serializers[question](answer), answer.sources)
            for question, answer in self._questions(rd).items()
        }

    def test_an_xdg_override_changes_no_answer_at_all(self):
        # Not merely "no caveat": every answer, sources and all, is the one
        # the unrelocated machine gives — the override never reaches the app,
        # so nothing about the machine's behavior differs.
        assert self._answers(self._relocated()) == self._answers(self._rd())

    def test_an_xdg_unset_changes_no_answer_either(self):
        # unset-environment=XDG_CONFIG_HOME is applied and then overwritten by
        # the same pin (flatpak_run_apply_env_vars runs before
        # flatpak_context_apply_env_appid) — inert the same way.
        unset = "[Context]\nunset-environment=XDG_CONFIG_HOME;\n"
        assert self._answers(self._rd({self.OVERRIDE: unset})) == self._answers(self._rd())

    def test_no_answer_carries_the_relocation_code(self):
        rd = self._relocated({ESDE_SETTINGS: self.UNSET_SETTINGS})
        for question, answer in self._questions(rd).items():
            assert atlas.CAVEAT_CONFIG_HOME_RELOCATED not in [c.code for c in answer.caveats], question

    def test_the_unset_default_resolves_under_an_override(self):
        # ROMDirectory unset used to be the refusal branch: the default
        # derives from ES-DE's --home "${XDG_CONFIG_HOME}", which the pin
        # keeps exactly where atlas resolves it.
        placement = self._relocated({ESDE_SETTINGS: self.UNSET_SETTINGS}).rom_location("n64")
        assert placement.dir == f"{self.CONFIG_HOME}/ROMs/n64"
        assert placement.caveats == ()

    def test_roms_dir_resolves_the_default_under_an_override(self):
        assert self._relocated({ESDE_SETTINGS: self.UNSET_SETTINGS}).roms_dir() == f"{self.CONFIG_HOME}/ROMs"
        assert self._relocated().roms_dir() == "/mnt/sd/retrodeck/roms"

    def test_health_is_untouched(self):
        assert self._relocated().health().ok


class TestAHomeOverrideMovesOnlyTheCfgTildeBase:
    """The one consequence a Flatpak override still has: what a cfg ``~`` expands against.

    ``HOME`` does reach the app — the host value passes into the sandbox and
    an override lands on top of it with nothing reapplied after
    (flatpak-run.c:3055, :3352) — and the only file this handle reads that
    resolves anything against it is ``retroarch.cfg``: RetroArch substitutes
    ``getenv("HOME")`` for a leading ``~`` (file_path.c:1066-1101,
    :1457-1468 @ a79435a). Everything else is keyed off the pinned
    ``XDG_CONFIG_HOME`` (``all_vars.sh:4``, ``component_functions.sh:3``,
    ``component_launcher.sh:10``), ES-DE's own ``~`` expansion included. The
    override value is literal — flatpak expands no ``$`` — and a value that
    leaves the expansion non-absolute or sandbox-only earns exactly the
    statements those shapes always earned.
    """

    DEPLOY_ESDE = TestAFlatpakOverrideCannotMoveTheConfigHome.DEPLOY_ESDE
    ES_SYSTEMS = TestAFlatpakOverrideCannotMoveTheConfigHome.ES_SYSTEMS
    CORES = TestAFlatpakOverrideCannotMoveTheConfigHome.CORES
    CORE = TestAFlatpakOverrideCannotMoveTheConfigHome.CORE
    CONTENT = TestAFlatpakOverrideCannotMoveTheConfigHome.CONTENT
    USER_APP = f"{HOME}/.local/share/flatpak/overrides/net.retrodeck.retrodeck"
    USER_GLOBAL = f"{HOME}/.local/share/flatpak/overrides/global"
    SYSTEM_GLOBAL = "/var/lib/flatpak/overrides/global"
    PLATFORM_DEFAULT = f"{HOME}/.var/app/net.retrodeck.retrodeck/config/retroarch/saves"
    TILDE_CFG = (
        'savefile_directory = "~/saves"\n'
        'system_directory = "/mnt/sd/retrodeck/bios"\n'
        'libretro_info_path = "/app/cores"\nlibretro_directory = "/app/cores"\n'
        'sort_savefiles_by_content_enable = "false"\nsort_savefiles_enable = "false"\n'
    )

    def _files(self, overrides=None):
        base = dict(TestAFlatpakOverrideCannotMoveTheConfigHome.FILES)
        base[RETRODECK_CFG] = self.TILDE_CFG
        base.update(overrides or {})
        return base

    def _savefile(self, overrides=None, dirs=None):
        machine = FixtureMachine(self._files(overrides), cores=self.CORES, dirs=dirs)
        return placed(
            atlas.RetroDeck(HOME, machine).savefile_location(
                content_path=self.CONTENT, core_so=self.CORE
            )
        )

    HOME_DIRS = ["/mnt/elsewhere/saves", f"{HOME}/saves"]

    def test_an_absolute_home_override_moves_the_expansion(self):
        placement = self._savefile(
            {self.USER_APP: "[Environment]\nHOME=/mnt/elsewhere\n"}, dirs=self.HOME_DIRS
        )
        assert placement.dir == "/mnt/elsewhere/saves"
        assert any("Flatpak overrides read live" in source for source in placement.sources)

    def test_without_an_override_the_tilde_expands_against_the_machine_home(self):
        # The discriminating direction of the test above.
        placement = self._savefile(dirs=self.HOME_DIRS)
        assert placement.dir == f"{HOME}/saves"
        assert not any("Flatpak overrides read live" in source for source in placement.sources)

    def test_the_last_applicable_file_wins_per_key(self):
        # flatpak's merge order: system-global → system-app → user-global →
        # user-app, later files overwriting per key (flatpak-dir.c:1518-1567).
        placement = self._savefile(
            {
                self.SYSTEM_GLOBAL: "[Environment]\nHOME=/mnt/sysglobal\n",
                self.USER_GLOBAL: "[Environment]\nHOME=/mnt/userglobal\n",
                self.USER_APP: "[Environment]\nHOME=/mnt/elsewhere\n",
            },
            dirs=["/mnt/sysglobal/saves", "/mnt/userglobal/saves", *self.HOME_DIRS],
        )
        assert placement.dir == "/mnt/elsewhere/saves"

    def test_an_unset_home_leaves_the_tilde_verbatim(self):
        # getenv("HOME") NULL: fill_pathname_home_dir leaves the buffer empty
        # and the substitution block is skipped — the value stays "~/saves",
        # which RetroArch's own directory test then refuses, and the root that
        # stood before it stands (the platform default here).
        placement = self._savefile(
            {self.USER_APP: "[Context]\nunset-environment=HOME;\n"}, dirs=self.HOME_DIRS
        )
        assert placement.dir == self.PLATFORM_DEFAULT
        assert atlas.CAVEAT_INVALID_SAVE_DIRECTORY in [c.code for c in placement.caveats]

    def test_an_empty_home_behaves_like_an_unset_one(self):
        # HOME= (the empty string) is set-but-empty; fill_pathname_home_dir
        # copies it and the substitution block is skipped just the same.
        placement = self._savefile(
            {self.USER_APP: "[Environment]\nHOME=\n"}, dirs=self.HOME_DIRS
        )
        assert placement.dir == self.PLATFORM_DEFAULT

    def test_a_dollar_carrying_home_is_literal_and_refused(self):
        # flatpak expands no $VAR — the value is the literal string, the
        # expansion is not absolute, and RetroArch's directory test refuses it.
        placement = self._savefile(
            {self.USER_APP: "[Environment]\nHOME=$HOME/elsewhere\n"}, dirs=self.HOME_DIRS
        )
        assert placement.dir == self.PLATFORM_DEFAULT
        assert atlas.CAVEAT_INVALID_SAVE_DIRECTORY in [c.code for c in placement.caveats]

    def test_a_sandbox_only_home_earns_the_untranslated_statement(self):
        # /var/elsewhere exists only inside the sandbox; the expansion is
        # where the emulator writes, in the only namespace that names it, and
        # the caveat states that atlas cannot follow it there — the way that
        # shape is always stated.
        placement = self._savefile(
            {self.USER_APP: "[Environment]\nHOME=/var/elsewhere\n"}, dirs=self.HOME_DIRS
        )
        assert placement.dir == "/var/elsewhere/saves"
        assert atlas.CAVEAT_SANDBOX_PATH_UNTRANSLATED in [c.code for c in placement.caveats]

    def test_esdes_own_tilde_is_not_moved(self):
        # ES-DE expands against its --home "${XDG_CONFIG_HOME}" — pinned —
        # not against the sandbox's HOME, so the same override that moves the
        # cfg tilde moves nothing here.
        files = self._files({self.USER_APP: "[Environment]\nHOME=/mnt/elsewhere\n"})
        files[ESDE_SETTINGS] = '<string name="ROMDirectory" value="~/Emulation/roms" />'
        machine = FixtureMachine(files, cores=self.CORES, dirs=self.HOME_DIRS)
        placement = atlas.RetroDeck(HOME, machine).rom_location("n64")
        config_home = TestAFlatpakOverrideCannotMoveTheConfigHome.CONFIG_HOME
        assert placement.dir == f"{config_home}/Emulation/roms/n64"

    def test_a_system_file_does_not_speak_for_a_user_deployed_app(self):
        # The deploy search finds the user installation first
        # (flatpak-dir-utils.c:300-316), and system overrides load only for a
        # system deploy (flatpak-dir.c:3053-3059) — so the same file that
        # moves the expansion on a system deploy moves nothing here.
        user_deploy = self.DEPLOY_ESDE.replace(
            "/var/lib/flatpak/app", f"{HOME}/.local/share/flatpak/app"
        )
        files = {
            key: value for key, value in self._files().items() if key != self.DEPLOY_ESDE
        }
        files[user_deploy] = self.ES_SYSTEMS
        files[self.SYSTEM_GLOBAL] = "[Environment]\nHOME=/mnt/elsewhere\n"
        # Both installations carry the app — FILES leaves the core's .info in
        # the system tree — which is the machine this scoping is about: with no
        # system deploy at all the system files would fall away for the wrong
        # reason. The core itself is deployed in the user tree alone, because
        # that is the tree the running deploy's own "/app" reads come out of
        # (the fixture used to hold it in both while that was unsettled).
        cores = {
            so.replace("/var/lib/flatpak/app", f"{HOME}/.local/share/flatpak/app"): answer
            for so, answer in self.CORES.items()
        }
        machine = FixtureMachine(files, cores=cores, dirs=self.HOME_DIRS)
        placement = placed(
            atlas.RetroDeck(HOME, machine).savefile_location(
                content_path=self.CONTENT, core_so=self.CORE
            )
        )
        assert placement.dir == f"{HOME}/saves"

    def test_the_same_file_speaks_for_the_system_deployed_app(self):
        # The counterpart that keeps the scoping test honest.
        placement = self._savefile(
            {self.SYSTEM_GLOBAL: "[Environment]\nHOME=/mnt/elsewhere\n"}, dirs=self.HOME_DIRS
        )
        assert placement.dir == "/mnt/elsewhere/saves"


class TestTheBareFlatpaksOverridesAreReadTheSameWay:
    """Issue #101: the ``org.libretro.RetroArch`` overrides speak for the bare app and for EmuDeck's.

    The composition is the one RetroDECK's handle already reads its own files
    with — user files always, system files only for a system-deployed app —
    so these tests pin the *wiring*, not the merge machinery: which handle
    reads which app's files, and that a native install reads none at all.
    """

    TILDE_CFG = (
        'savefile_directory = "~/saves"\n'
        'sort_savefiles_by_content_enable = "false"\nsort_savefiles_enable = "false"\n'
    )
    USER_APP = f"{HOME}/.local/share/flatpak/overrides/org.libretro.RetroArch"
    SYSTEM_APP = "/var/lib/flatpak/overrides/org.libretro.RetroArch"
    SYSTEM_DEPLOY = "/var/lib/flatpak/app/org.libretro.RetroArch/current/active"
    USER_DEPLOY = f"{HOME}/.local/share/flatpak/app/org.libretro.RetroArch/current/active"
    HOME_DIRS = ["/mnt/elsewhere/saves", f"{HOME}/saves"]
    MOVED = "[Environment]\nHOME=/mnt/elsewhere\n"

    def _bare(self, files=None, dirs=()):
        machine = FixtureMachine(
            {STANDALONE_CFG: self.TILDE_CFG, **(files or {})}, dirs=[*self.HOME_DIRS, *dirs]
        )
        return placed(atlas.BareRetroArchFlatpak(HOME, machine).savefile_location())

    def test_a_user_override_moves_the_bare_flatpaks_tilde_base(self):
        placement = self._bare({self.USER_APP: self.MOVED})
        assert placement.dir == "/mnt/elsewhere/saves"
        assert any("Flatpak overrides read live" in source for source in placement.sources)

    def test_without_an_override_the_tilde_expands_against_the_machine_home(self):
        placement = self._bare()
        assert placement.dir == f"{HOME}/saves"
        assert not any("Flatpak overrides read live" in source for source in placement.sources)

    def test_a_system_override_needs_a_system_deploy_to_speak(self):
        # No deploy anywhere: only the always-loaded user files are read
        # (flatpak-dir.c:3053-3083), so the system file says nothing.
        placement = self._bare({self.SYSTEM_APP: self.MOVED})
        assert placement.dir == f"{HOME}/saves"

    def test_a_system_override_speaks_for_the_system_deployed_app(self):
        placement = self._bare({self.SYSTEM_APP: self.MOVED}, dirs=[self.SYSTEM_DEPLOY])
        assert placement.dir == "/mnt/elsewhere/saves"

    def test_a_user_deploy_silences_the_system_file(self):
        # Both installations carry the app; the user one runs (the #93
        # resolution), and flatpak loads system overrides only for an app the
        # system installation runs.
        placement = self._bare(
            {self.SYSTEM_APP: self.MOVED}, dirs=[self.SYSTEM_DEPLOY, self.USER_DEPLOY]
        )
        assert placement.dir == f"{HOME}/saves"

    def test_emudecks_retroarch_reads_the_same_apps_files(self):
        machine = FixtureMachine(
            {
                EMUDECK_SETTINGS: 'savesPath="$HOME/Emulation/saves"\n',
                STANDALONE_CFG: self.TILDE_CFG,
                self.USER_APP: self.MOVED,
            },
            dirs=self.HOME_DIRS,
        )
        placement = placed(atlas.EmuDeck(HOME, machine).savefile_location())
        assert placement.dir == "/mnt/elsewhere/saves"
        assert any("Flatpak overrides read live" in source for source in placement.sources)

    def test_a_native_install_reads_no_override_files(self):
        # Nothing sandboxes a native RetroArch, so nothing can hand it another
        # HOME — the file is another app's business.
        machine = FixtureMachine(
            {
                f"{HOME}/.config/retroarch/retroarch.cfg": self.TILDE_CFG,
                self.USER_APP: self.MOVED,
                f"{HOME}/.local/share/flatpak/overrides/global": self.MOVED,
            },
            dirs=self.HOME_DIRS,
        )
        placement = placed(atlas.BareRetroArchNative(HOME, machine).savefile_location())
        assert placement.dir == f"{HOME}/saves"
        assert not any("Flatpak overrides read live" in source for source in placement.sources)


class TestTheRunningDeployIsTheOneFlatpakWouldStart:
    """Both installations can carry the same app; the reads come from the one that runs.

    Flatpak resolves the deploy by searching the installations with the user
    one inserted at the front of the list and stopping at the first that has
    the app (``flatpak_find_deploy_for_ref``, flatpak-dir-utils.c:300-316 @
    1.16.6, the loop at :278-285). So on a machine deploying RetroDECK both
    ways, every ``/app/...`` a cfg or a handle names is a file in the *user*
    tree, and the system tree's copy of it is one nothing here opens — the
    same resolution that decides whose overrides files speak for the app.
    """

    SYSTEM_DEPLOY = "/var/lib/flatpak/app/net.retrodeck.retrodeck/current/active/files"
    USER_DEPLOY = f"{HOME}/.local/share/flatpak/app/net.retrodeck.retrodeck/current/active/files"
    ESDE_SUFFIX = "retrodeck/components/es-de/share/es-de/resources/systems/linux/es_systems.xml"
    CFG = (
        'savefile_directory = "/mnt/sd/retrodeck/saves"\n'
        'libretro_directory = "/app/cores"\n'
        'sort_savefiles_by_content_enable = "false"\nsort_savefiles_enable = "false"\n'
    )
    CONTENT = "/mnt/sd/retrodeck/roms/n64/Game.z64"
    CORE = "mupen64plus_next_libretro.so"
    OTHER_CORE = "mgba_libretro.so"

    def _catalogue(self, system):
        return (
            f'<?xml version="1.0"?>\n<systemList>\n  <system><name>{system}</name>\n'
            f"    <path>%ROMPATH%/{system}</path>\n    <extension>.z64 .Z64</extension>\n"
            '    <command label="Libretro">retroarch -L '
            f"/app/cores/{self.CORE} %ROM%</command>\n  </system>\n</systemList>\n"
        )

    def _rd(self, files=None, cores=None):
        base = {
            RETRODECK_JSON: RD_JSON,
            RETRODECK_CFG: self.CFG,
            "/mnt/sd/retrodeck/saves/.keep": "",
            self.CONTENT: "",
        }
        base.update(files or {})
        return _retrodeck(base, cores=cores)

    def _cores(self, *deploys):
        return {f"{deploy}/cores/{self.CORE}": {"library_name": "Mupen64Plus-Next"} for deploy in deploys}

    def test_the_user_deploys_catalogue_is_the_one_read(self):
        # Both trees ship an es_systems.xml and they declare different
        # systems, so the answer names which tree was read.
        rd = self._rd(
            {
                f"{self.SYSTEM_DEPLOY}/{self.ESDE_SUFFIX}": self._catalogue("psx"),
                f"{self.USER_DEPLOY}/{self.ESDE_SUFFIX}": self._catalogue("n64"),
            }
        )
        assert rd.systems().systems == ("n64",)

    def test_a_system_only_deploy_is_still_the_one_read(self):
        # The direction that keeps the rule falsifiable: with no user deploy
        # the search reaches the system installation, and that tree answers.
        rd = self._rd({f"{self.SYSTEM_DEPLOY}/{self.ESDE_SUFFIX}": self._catalogue("psx")})
        assert rd.systems().systems == ("psx",)

    def test_the_running_deploy_does_not_borrow_the_other_trees_core(self):
        # The cfg names "/app/cores"; the user deploy runs, so its cores
        # directory is the one that decides. It holds another core — absence
        # established — and the system tree's copy of the asked-for one is not
        # what a run of this app would load.
        rd = self._rd(
            {f"{self.USER_DEPLOY}/{self.ESDE_SUFFIX}": self._catalogue("n64")},
            cores={
                **self._cores(self.SYSTEM_DEPLOY),
                f"{self.USER_DEPLOY}/cores/{self.OTHER_CORE}": {"library_name": "mGBA"},
            },
        )
        outcome = rd.savefile_location(content_path=self.CONTENT, core_so=self.CORE)
        assert isinstance(outcome, atlas.Unresolved)
        assert outcome.code == atlas.UNRESOLVED_CORE_NOT_INSTALLED

    def test_the_same_machine_resolves_when_the_running_deploy_carries_the_core(self):
        # The counterpart: only the tree the core sits in differs, and the
        # placement comes back.
        rd = self._rd(
            {f"{self.USER_DEPLOY}/{self.ESDE_SUFFIX}": self._catalogue("n64")},
            cores=self._cores(self.USER_DEPLOY),
        )
        placement = placed(rd.savefile_location(content_path=self.CONTENT, core_so=self.CORE))
        assert placement.dir == "/mnt/sd/retrodeck/saves"

    def test_a_system_only_deploy_resolves_its_own_core(self):
        # And the system tree keeps answering where it is the only deploy —
        # the machine every other fixture describes.
        rd = self._rd(
            {f"{self.SYSTEM_DEPLOY}/{self.ESDE_SUFFIX}": self._catalogue("n64")},
            cores=self._cores(self.SYSTEM_DEPLOY),
        )
        placement = placed(rd.savefile_location(content_path=self.CONTENT, core_so=self.CORE))
        assert placement.dir == "/mnt/sd/retrodeck/saves"


class TestTheOverridesFileReadsTheWayGKeyFileDoes:
    """The parser's value semantics are g_key_file_get_string's, because flatpak's are."""

    def _env(self, text):
        from atlas.installations import (
            _environment_overrides,  # pyright: ignore[reportPrivateUsage] - the parser is the unit under test
        )

        return _environment_overrides(text)

    def test_escapes_decode(self):
        env = self._env("[Environment]\nHOME=/with\\sspace\\ttab\\\\slash\n")
        assert env == {"HOME": "/with space\ttab\\slash"}

    def test_an_invalid_escape_is_an_unset(self):
        # flatpak reads the value with a NULL GError and hands NULL on, and a
        # NULL value unsets (flatpak-context.c:1944-1946, flatpak-run.c:752-755).
        assert self._env("[Environment]\nHOME=/bad\\qescape\n") == {"HOME": None}

    def test_a_trailing_backslash_is_an_unset_too(self):
        assert self._env("[Environment]\nHOME=/bad\\\n") == {"HOME": None}

    def test_unset_environment_beats_the_environment_group_in_one_file(self):
        env = self._env("[Environment]\nHOME=/kept\n\n[Context]\nunset-environment=HOME;\n")
        assert env == {"HOME": None}

    def test_the_unset_list_splits_on_semicolons_and_drops_the_trailing_one(self):
        env = self._env("[Context]\nunset-environment=HOME;XDG_DATA_HOME;\n")
        assert env == {"HOME": None, "XDG_DATA_HOME": None}

    def test_group_names_match_exactly(self):
        # GKeyFile does not trim inside the brackets: "[ Environment ]" names
        # a different group, and flatpak reads nothing out of it.
        assert self._env("[ Environment ]\nHOME=/elsewhere\n") == {}

    def test_value_leading_whitespace_is_skipped_and_trailing_kept(self):
        assert self._env("[Environment]\nHOME=  /padded  \n") == {"HOME": "/padded  "}


class TestFirmwareResolvesTheSystemDirectoryLikeTheCardRoute:
    """Item 14's rule, on the other route: absent resolves, cleared refuses.

    The firmware route used to refuse both spellings with one code, so a
    machine whose config simply never names ``system_directory`` — the ordinary
    case for a stock RetroArch — was told there was no root to check against,
    while the card route had already resolved that same machine to RetroArch's
    own default.
    """

    CFG = f"{HOME}/.config/retroarch/retroarch.cfg"
    CONFIG_TREE = f"{HOME}/.config/retroarch"
    CORES = f"{HOME}/.config/retroarch/cores"
    INFO = 'display_name = "mGBA"\nsystemname = "GBA"\n'
    DIRS = 'libretro_info_path = "~/.config/retroarch/cores"\n'

    def _inventory(self, cfg: str, **kwargs):
        machine = FixtureMachine(
            {
                self.CFG: cfg,
                f"{self.CORES}/mgba_libretro.info": self.INFO,
                **kwargs.pop("files", {}),
            },
            **kwargs,
        )
        return atlas.BareRetroArchNative(HOME, machine).firmware_inventory()

    def test_an_absent_key_resolves_to_the_platform_default(self):
        answer = self._inventory(self.DIRS, dirs=[f"{self.CONFIG_TREE}/system"])
        assert answer.root == f"{self.CONFIG_TREE}/system"

    def test_an_absent_key_refuses_nothing(self):
        answer = self._inventory(self.DIRS, dirs=[f"{self.CONFIG_TREE}/system"])
        assert atlas.CAVEAT_SYSTEM_DIRECTORY_CLEARED not in [c.code for c in answer.caveats]

    def test_a_resolved_default_that_is_not_there_is_stated_like_any_root(self):
        # Resolving is not asserting: the default directory may not exist, and
        # that is the same fact a configured missing root states.
        answer = self._inventory(self.DIRS)
        assert atlas.CAVEAT_FIRMWARE_ROOT_MISSING in [c.code for c in answer.caveats]

    def test_a_cleared_key_refuses_with_its_own_code(self):
        answer = self._inventory(f'system_directory = ""\n{self.DIRS}')
        assert answer.root is None
        assert atlas.CAVEAT_SYSTEM_DIRECTORY_CLEARED in [c.code for c in answer.caveats]

    def test_the_cleared_refusal_carries_the_value(self):
        answer = self._inventory(f'system_directory = "default"\n{self.DIRS}')
        cleared = next(c for c in answer.caveats if c.code == atlas.CAVEAT_SYSTEM_DIRECTORY_CLEARED)
        assert cleared.data == {"value": "default"}

    def test_a_configured_key_is_unchanged(self):
        answer = self._inventory(
            f'system_directory = "/bios"\n{self.DIRS}', dirs=["/bios"]
        )
        assert answer.root == "/bios"
        assert atlas.CAVEAT_SYSTEM_DIRECTORY_CLEARED not in [c.code for c in answer.caveats]

    def test_both_routes_resolve_an_absent_key_to_the_same_directory(self):
        # The point of sharing the helper: one directory, two routes.
        #
        # Every extra line of this fixture is load-bearing, and none of it is
        # scenery to be tidied away. The card route reaches the system directory
        # only when a card applies, and since #81 a card applies only to a core
        # that answered — so the core has to be deployed, it has to register the
        # option that confirms its generation, and `libretro_directory` has to
        # name the directory it sits in, or nothing resolves the .so at all.
        # Take any of the three away and the query lands on the standard save
        # root instead. The fixture used to declare no cores whatsoever, which
        # was green only because an unread core still got its card.
        machine = FixtureMachine(
            {
                self.CFG: f'libretro_directory = "{self.CORES}"\n{self.DIRS}',
                f"{self.CORES}/mgba_libretro.info": self.INFO,
            },
            dirs=[f"{self.CONFIG_TREE}/system"],
            cores={
                f"{self.CORES}/flycast_libretro.so": {
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
        handle = atlas.BareRetroArchNative(HOME, machine)
        placement = placed(handle.savefile_location(core_so="flycast_libretro.so"))
        assert handle.firmware_inventory().root == f"{self.CONFIG_TREE}/system"
        assert placement.dir.startswith(f"{self.CONFIG_TREE}/system")

    def test_a_dropped_line_is_stated_beside_the_resolved_default(self):
        # The silence this split would otherwise create: the key ends up absent
        # because RetroArch refused the line, so the answer resolves — and says
        # which line set nothing, or the configured path vanishes without trace.
        answer = self._inventory(
            f'system_directory "/bios"\n{self.DIRS}', dirs=[f"{self.CONFIG_TREE}/system"]
        )
        assert answer.root == f"{self.CONFIG_TREE}/system"
        dropped = next(c for c in answer.caveats if c.code == atlas.CAVEAT_CFG_LINE_DROPPED)
        assert dropped.data == {"key": "system_directory", "line": 'system_directory "/bios"'}

    def test_a_dropped_line_for_another_key_is_not_this_routes_business(self):
        # Only the key this route resolves is stated here; the save-layout keys
        # are the card route's to report, and stating them twice would double
        # every dropped line on a machine that asks both questions.
        answer = self._inventory(
            f'savefile_directory "/saves"\n{self.DIRS}', dirs=[f"{self.CONFIG_TREE}/system"]
        )
        assert atlas.CAVEAT_CFG_LINE_DROPPED not in [c.code for c in answer.caveats]


class TestSimpleIniKeyMatching:
    """The mirror of SimpleIni's comparator is the unit under test (#225)."""

    def test_a_section_spelled_in_another_case_still_matches(self):
        from atlas.installations import (
            _simpleini_value,  # pyright: ignore[reportPrivateUsage] - the mirror is the unit under test
        )

        assert _simpleini_value({("folders", "savestates"): "/x"}, "Folders", "Savestates") == (
            "/x",
            "savestates",
        )

    def test_duplicate_case_spellings_collapse_with_the_last_occurrence_winning(self):
        # AddEntry assigns into the found (case-equal) key, so the last line in
        # file order speaks (SimpleIni.h:2042-2150 at PCSX2 v2.6.3).
        from atlas.installations import (
            _simpleini_value,  # pyright: ignore[reportPrivateUsage] - the mirror is the unit under test
        )

        values = {("Folders", "Savestates"): "/a", ("Folders", "SaveStates"): "/b"}
        assert _simpleini_value(values, "Folders", "Savestates") == ("/b", "SaveStates")

    def test_a_present_but_empty_value_is_not_an_absent_key(self):
        from atlas.installations import (
            _simpleini_value,  # pyright: ignore[reportPrivateUsage] - the mirror is the unit under test
        )

        assert _simpleini_value({("Folders", "Savestates"): ""}, "Folders", "Savestates") == (
            "",
            "Savestates",
        )
        assert _simpleini_value({}, "Folders", "Savestates") == (None, "Savestates")

    def test_the_folding_is_ascii_only_never_pythons(self):
        # SI_GenericNoCase lowers A-Z and nothing else (SimpleIni.h:2916-2931).
        # str.lower() and str.casefold() both fold 'İ' to 'i̇' and casefold
        # folds 'ß' to 'ss' — a mirror built on either would match keys the
        # emulator keeps apart.
        from atlas.installations import (
            _ascii_locase,  # pyright: ignore[reportPrivateUsage] - the mirror is the unit under test
            _simpleini_value,  # pyright: ignore[reportPrivateUsage] - the mirror is the unit under test
        )

        assert _ascii_locase("SaveStates") == "savestates"
        assert _ascii_locase("İß") == "İß"
        assert _simpleini_value({("S", "İd"): "/x"}, "S", "i̇d") == (None, "i̇d")
        assert _simpleini_value({("S", "Straße"): "/x"}, "S", "STRASSE") == (None, "STRASSE")


class TestPcsx2EmptyFolderKeys:
    """A present-but-empty [Folders] line moves the directory to the DataRoot.

    GetStringValue falls to the compiled default only when the lookup fails
    (SettingsInterface.h:83-89 at v2.6.3); the empty value survives, and
    Path::Combine(DataRoot, "") is the DataRoot itself (FileSystem.cpp:847-862)
    — one fact, pinned once per question that reads a [Folders] directory.
    """

    DATA_ROOT = f"{HOME}/.var/app/net.retrodeck.retrodeck/config/PCSX2"
    BASE = {
        RETRODECK_JSON: RD_JSON,
        RETRODECK_CFG: 'savefile_directory = "/mnt/sd/retrodeck/saves"\n'
        'libretro_directory = "/app/cores"\n',
        DOLPHIN_ESDE: TRIO_ESDE,
    }

    def _entry(self, ini):
        rd = _retrodeck(
            {**self.BASE, PCSX2_INI_PATH: ini}, dirs=["/mnt/sd/retrodeck/saves"]
        )
        return rd.emulators_for("ps2").entries[0]

    def test_an_empty_savestates_line_lands_the_states_on_the_dataroot(self):
        p = self._entry("[Folders]\nSaveStates =\n").savestate_location()
        assert isinstance(p, atlas.SavestatePlacement)
        assert p.dir == self.DATA_ROOT

    def test_an_absent_savestates_key_keeps_the_compiled_default(self):
        p = self._entry("[Folders]\nMemoryCards = /mnt/sd/cards\n").savestate_location()
        assert isinstance(p, atlas.SavestatePlacement)
        assert p.dir == f"{self.DATA_ROOT}/sstates"

    def test_an_empty_memorycards_line_lands_the_cards_on_the_dataroot(self):
        p = self._entry(
            "[Folders]\nMemoryCards =\n[MemoryCards]\nSlot1_Enable = true\nSlot2_Enable = false\n"
        ).savefile_location()
        assert not isinstance(p, atlas.Unresolved)
        assert p.file_set.groups[0].dir == self.DATA_ROOT
        assert p.granularity is not None
        reading = next(r for r in p.granularity.readings if r.key == "MemoryCards")
        # The reading's value is the empty string the file carries — not the
        # None of an absent key.
        assert reading.value == ""

    def test_an_empty_textures_line_roots_the_packs_on_the_dataroot(self):
        answer = self._entry("[Folders]\nTextures =\n").texture_pack_location()
        assert not isinstance(answer, atlas.Unresolved)
        assert answer.dir == f"{self.DATA_ROOT}/<save_id>/replacements"

    def test_an_empty_gamesettings_line_globs_the_dataroot_for_the_layer(self):
        # The fourth [Folders] reader: the per-game settings layer's directory
        # is read through the same LoadPathFromSettings (Pcsx2Config.cpp:2290),
        # so an empty line puts the layer at the DataRoot — the caveat's
        # contractual dir must say so, not the compiled gamesettings.
        rd = _retrodeck(
            {
                **self.BASE,
                PCSX2_INI_PATH: (
                    "[Folders]\nGameSettings =\n"
                    "[EmuCore/GS]\nLoadTextureReplacements = false\n"
                ),
                f"{self.DATA_ROOT}/Game.ini": "",
            },
            dirs=["/mnt/sd/retrodeck/saves"],
        )
        answer = rd.emulators_for("ps2").entries[0].texture_pack_location()
        assert not isinstance(answer, atlas.Unresolved)
        stated = next(
            c for c in answer.caveats if c.code == atlas.CAVEAT_PER_GAME_OVERRIDES_PRESENT
        )
        assert stated.data["dir"] == self.DATA_ROOT
        assert stated.data["count"] == "1"
