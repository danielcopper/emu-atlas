"""Tests for atlas.esde — catalogue parsing, merge semantics, entry handles."""

from __future__ import annotations

import pytest

import atlas
from atlas.esde import merge_layers, parse_es_systems

HOME = "/home/deck"
RETRODECK_JSON = f"{HOME}/.var/app/net.retrodeck.retrodeck/config/retrodeck/retrodeck.json"
RETRODECK_CFG = f"{HOME}/.var/app/net.retrodeck.retrodeck/config/retroarch/retroarch.cfg"
OPTIONS_CFG = f"{HOME}/.var/app/net.retrodeck.retrodeck/config/retroarch/retroarch-core-options.cfg"

RD_JSON = '{"paths": {"rd_home_path": "/mnt/sd/retrodeck", "saves_path": "/mnt/sd/retrodeck/saves"}}'
DEPLOY = "/var/lib/flatpak/app/net.retrodeck.retrodeck/current/active/files"
BUNDLED_ESDE = f"{DEPLOY}/retrodeck/components/es-de/share/es-de/resources/systems/linux/es_systems.xml"
CUSTOM_ESDE = "/mnt/sd/retrodeck/ES-DE/custom_systems/es_systems.xml"

BUNDLED_XML = """<?xml version="1.0"?>
<systemList>
  <system>
    <name>dreamcast</name>
    <path>%ROMPATH%/dreamcast</path>
    <command label="Flycast">%EMULATOR_RETROARCH% -L %CORE_RETROARCH%/flycast_libretro.so %ROM%</command>
  </system>
  <system>
    <name>n64</name>
    <path>%ROMPATH%/n64</path>
    <command label="Mupen64Plus-Next">%EMULATOR_RETROARCH% -L %CORE_RETROARCH%/mupen64plus_next_libretro.so %ROM%</command>
    <command label="ParaLLEl N64">%EMULATOR_RETROARCH% -L %CORE_RETROARCH%/parallel_n64_libretro.so %ROM%</command>
  </system>
  <system>
    <name>ps3</name>
    <path>%ROMPATH%/ps3</path>
    <command label="RPCS3 Directory (Standalone)">%EMULATOR_RPCS3% --no-gui %ROM%</command>
  </system>
</systemList>
"""


class TestParse:
    def test_systems_and_order(self):
        parsed = parse_es_systems(BUNDLED_XML, source="test")
        assert set(parsed) == {"dreamcast", "n64", "ps3"}
        assert [e.label for e in parsed["n64"]] == ["Mupen64Plus-Next", "ParaLLEl N64"]

    def test_libretro_classification_extracts_core_so(self):
        parsed = parse_es_systems(BUNDLED_XML, source="test")
        entry = parsed["dreamcast"][0]
        assert entry.kind == atlas.KIND_LIBRETRO
        assert entry.core_so == "flycast_libretro.so"

    def test_standalone_classification(self):
        parsed = parse_es_systems(BUNDLED_XML, source="test")
        entry = parsed["ps3"][0]
        assert entry.kind == atlas.KIND_STANDALONE
        assert entry.core_so is None

    def test_malformed_xml_is_skipped_layer(self):
        assert parse_es_systems("<systemList><system>", source="test") == {}

    def test_commented_out_systems_yield_nothing(self):
        # RetroDECK ships a custom_systems overlay that is entirely commented out.
        text = '<?xml version="1.0"?>\n<systemList>\n<!-- <system><name>x</name></system> -->\n</systemList>'
        assert parse_es_systems(text, source="test") == {}


class TestMerge:
    def test_custom_replaces_bundled_system(self):
        bundled = parse_es_systems(BUNDLED_XML, source="bundled")
        custom = parse_es_systems(
            '<systemList><system><name>n64</name>'
            '<command label="ParaLLEl N64">%EMULATOR_RETROARCH% -L %CORE_RETROARCH%/parallel_n64_libretro.so %ROM%</command>'
            "</system></systemList>",
            source="custom",
        )
        merged = merge_layers(bundled, custom)
        assert [e.label for e in merged["n64"]] == ["ParaLLEl N64"]
        assert merged["n64"][0].source == "custom"
        assert "dreamcast" in merged  # untouched systems stay

    def test_custom_adds_new_system(self):
        merged = merge_layers(
            parse_es_systems(BUNDLED_XML, source="bundled"),
            parse_es_systems(
                '<systemList><system><name>mysystem</name>'
                "<command>%EMULATOR_SOMETHING% %ROM%</command></system></systemList>",
                source="custom",
            ),
        )
        assert "mysystem" in merged


def _retrodeck(files, **kwargs):
    machine = atlas.FixtureMachine(files, **kwargs)
    return atlas.RetroDeck(HOME, machine, files.get(RETRODECK_JSON))


def _catalogue_fixture(extra_files=None, **kwargs):
    files = {RETRODECK_JSON: RD_JSON, BUNDLED_ESDE: BUNDLED_XML}
    files.update(extra_files or {})
    return _retrodeck(files, **kwargs)


class TestRetroDeckCatalogue:
    def test_emulators_for_declared_order(self):
        rd = _catalogue_fixture()
        entries = rd.emulators_for("n64")
        assert [e.label for e in entries] == ["Mupen64Plus-Next", "ParaLLEl N64"]
        assert entries[0].core_so == "mupen64plus_next_libretro.so"

    def test_unknown_system_is_empty(self):
        assert _catalogue_fixture().emulators_for("does-not-exist") == ()

    def test_no_esde_at_all_is_empty(self):
        rd = _retrodeck({RETRODECK_JSON: RD_JSON})
        assert rd.emulators_for("n64") == ()
        assert rd.systems() == ()

    def test_systems_listing(self):
        assert _catalogue_fixture().systems() == ("dreamcast", "n64", "ps3")

    def test_custom_overlay_overrides(self):
        rd = _catalogue_fixture(
            {
                CUSTOM_ESDE: '<systemList><system><name>dreamcast</name>'
                '<command label="Custom Flycast">%EMULATOR_RETROARCH% -L %CORE_RETROARCH%/flycast_libretro.so %ROM%</command>'
                "</system></systemList>"
            }
        )
        entries = rd.emulators_for("dreamcast")
        assert [e.label for e in entries] == ["Custom Flycast"]
        assert entries[0].source == "es_systems.xml (custom_systems overlay)"


class TestEntrySaveLocation:
    def test_full_circle_dreamcast_entry_hits_the_rule_card(self):
        # catalogue -> default entry -> save_location: core known, card applies,
        # the no-core caveat class does not exist on this path.
        rd = _catalogue_fixture(
            {
                RETRODECK_CFG: (
                    'savefile_directory = "/mnt/sd/retrodeck/saves"\n'
                    'system_directory = "/mnt/sd/retrodeck/bios"\n'
                    'global_core_options = "true"\n'
                    'libretro_directory = "/app/cores"\n'
                ),
                OPTIONS_CFG: 'reicast_per_content_vmus = "disabled"\n',
                "/mnt/sd/retrodeck/roms/dreamcast/Shenmue (Europe).gdi": "",
                "/mnt/sd/retrodeck/bios/dc/vmu_save_A1.bin": "v",
            },
            cores={f"{DEPLOY}/cores/flycast_libretro.so": {"library_name": "Flycast"}},
        )
        entry = rd.emulators_for("dreamcast")[0]
        p = entry.save_location(content_path="/mnt/sd/retrodeck/roms/dreamcast/Shenmue (Europe).gdi")
        assert p.dir == "/mnt/sd/retrodeck/bios/dc"
        assert p.root_kind == atlas.ROOT_SYSTEM_DIRECTORY
        assert not any(c.code == atlas.CAVEAT_NO_CORE for c in p.caveats)
        assert p.granularity is not None and p.granularity.value == "shared-card"

    def test_standalone_entry_refuses_instead_of_guessing(self):
        rd = _catalogue_fixture()
        entry = rd.emulators_for("ps3")[0]
        with pytest.raises(NotImplementedError):
            entry.save_location(content_path="/mnt/sd/retrodeck/roms/ps3/game")
