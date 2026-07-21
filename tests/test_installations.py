"""Tests for atlas.installations — the handles' roots and save placements."""

from __future__ import annotations

import json
import os

from atlas.installations import (
    NATIVE_CFG_SUFFIX,
    RETRODECK_CFG_SUFFIX,
    STANDALONE_FLATPAK_CFG_SUFFIX,
    NativeRetroArch,
    RetroDeck,
    StandaloneRetroArchFlatpak,
)
from atlas.reader import FixtureReader

HOME = "/home/deck"
RD_CFG = os.path.join(HOME, RETRODECK_CFG_SUFFIX)
SA_CFG = os.path.join(HOME, STANDALONE_FLATPAK_CFG_SUFFIX)
NA_CFG = os.path.join(HOME, NATIVE_CFG_SUFFIX)

CUSTOM_JSON = json.dumps(
    {
        "paths": {
            "rd_home_path": "/mnt/sd/retrodeck",
            "saves_path": "/mnt/sd/retrodeck/saves",
            "roms_path": "/mnt/sd/retrodeck/roms",
            "bios_path": "/mnt/sd/retrodeck/bios",
        }
    }
)


class TestRetroDeckPaths:
    def test_custom_paths_from_config(self):
        rd = RetroDeck(HOME, FixtureReader({}), CUSTOM_JSON)
        assert rd.root() == "/mnt/sd/retrodeck"
        assert rd.saves_root() == "/mnt/sd/retrodeck/saves"
        assert rd.roms_dir() == "/mnt/sd/retrodeck/roms"
        assert rd.bios_dir() == "/mnt/sd/retrodeck/bios"

    def test_fallback_paths_when_config_empty(self):
        rd = RetroDeck(HOME, FixtureReader({}), "{}")
        assert rd.root() == os.path.join(HOME, "retrodeck")
        assert rd.saves_root() == os.path.join(HOME, "retrodeck", "saves")
        assert rd.roms_dir() == os.path.join(HOME, "retrodeck", "roms")
        assert rd.bios_dir() == os.path.join(HOME, "retrodeck", "bios")

    def test_fallback_paths_when_config_missing(self):
        rd = RetroDeck(HOME, FixtureReader({}), None)
        assert rd.saves_root() == os.path.join(HOME, "retrodeck", "saves")

    def test_partial_config_mixes_configured_and_fallback(self):
        rd = RetroDeck(HOME, FixtureReader({}), json.dumps({"paths": {"saves_path": "/custom/saves"}}))
        assert rd.saves_root() == "/custom/saves"
        assert rd.bios_dir() == os.path.join(HOME, "retrodeck", "bios")


class TestRetroDeckSavePlacement:
    def test_reads_retrodeck_retroarch_cfg(self):
        reader = FixtureReader({RD_CFG: 'sort_savefiles_by_content_enable = "true"\n'})
        rd = RetroDeck(HOME, reader, CUSTOM_JSON)
        placement = rd.save_placement("gba")
        assert placement.dir == "/mnt/sd/retrodeck/saves/<content_dir>"
        assert placement.filename == "<rom_stem>.srm"
        assert placement.needs == ("content_dir", "rom_stem")

    def test_no_cfg_uses_defaults(self):
        rd = RetroDeck(HOME, FixtureReader({}), CUSTOM_JSON)
        placement = rd.save_placement("gba")
        assert placement.dir == "/mnt/sd/retrodeck/saves/<content_dir>"
        assert any("RetroDECK default" in s for s in placement.sources)

    def test_saves_root_provenance_in_sources(self):
        rd = RetroDeck(HOME, FixtureReader({}), CUSTOM_JSON)
        placement = rd.save_placement("gba")
        assert any("retrodeck.json: paths.saves_path" in s for s in placement.sources)


class TestBareRetroArchInstalls:
    def test_standalone_flatpak_root_is_config_dir(self):
        sa = StandaloneRetroArchFlatpak(HOME, FixtureReader({}))
        assert sa.kind == "standalone_retroarch_flatpak"
        assert sa.root() == os.path.dirname(SA_CFG)

    def test_native_root_is_config_dir(self):
        na = NativeRetroArch(HOME, FixtureReader({}))
        assert na.kind == "native_retroarch"
        assert na.root() == os.path.dirname(NA_CFG)

    def test_standalone_savefile_directory_becomes_saves_root(self):
        reader = FixtureReader({SA_CFG: 'savefile_directory = "~/saves"\nsort_savefiles_by_content_enable = "true"\n'})
        placement = StandaloneRetroArchFlatpak(HOME, reader).save_placement("gba")
        assert placement.dir == "/home/deck/saves/<content_dir>"
        assert placement.needs == ("content_dir", "rom_stem")

    def test_native_absent_savefile_directory_is_hole(self):
        reader = FixtureReader({NA_CFG: 'sort_savefiles_by_content_enable = "true"\n'})
        placement = NativeRetroArch(HOME, reader).save_placement("gba")
        assert placement.dir == "<savefile_directory>/<content_dir>"
        assert placement.needs == ("savefile_directory", "content_dir", "rom_stem")
