"""Tests for atlas.melonds — the config read chain, mirrored from the emulator.

The vectors carry the answers; these carry the reading's corners: which file
``Config::Load`` steps to, how the legacy scan treats a line, and what each
accessor does with a value of the wrong type.
"""

from atlas import melonds
from atlas.machine import FixtureMachine

HOME = "/home/deck"
CONFIG_HOME = f"{HOME}/.config"
TOML = f"{CONFIG_HOME}/melonDS/melonDS.toml"
INI = f"{CONFIG_HOME}/melonDS/melonDS.ini"


def _read(files=None, **kwargs):
    return melonds.read_config(FixtureMachine(files or {}, **kwargs), CONFIG_HOME)


class TestWhichFileSpeaks:
    def test_the_toml_outranks_the_legacy_file_beside_it(self):
        read = _read(
            {
                TOML: '[Instance0]\nSaveFilePath = "/from/toml"\n',
                INI: "SaveFilePath=/from/ini\n",
            }
        )
        assert read.config is not None
        assert read.config.source == melonds.SOURCE_TOML
        assert melonds.get_string(read.config, "Instance0.SaveFilePath") == "/from/toml"

    def test_an_unparseable_toml_is_factory_defaults_not_the_legacy_file(self):
        # Config::Load catches the syntax error and keeps an empty table
        # (Config.cpp:796-803) — it never falls back, so a legacy value beside
        # a broken TOML must not govern.
        read = _read({TOML: "[Instance0\nnot toml", INI: "SaveFilePath=/from/ini\n"})
        assert read.config is not None
        assert read.config.source == melonds.SOURCE_TOML_INVALID
        assert melonds.get_string(read.config, "Instance0.SaveFilePath") == ""

    def test_a_missing_toml_reaches_the_legacy_file(self):
        read = _read({INI: "SaveFilePath=/from/ini\n"})
        assert read.config is not None
        assert read.config.source == melonds.SOURCE_LEGACY
        assert read.config.stated_file == INI
        assert melonds.get_string(read.config, "Instance0.SaveFilePath") == "/from/ini"

    def test_neither_file_is_the_compiled_defaults(self):
        read = _read()
        assert read.config is not None
        assert read.config.source == melonds.SOURCE_DEFAULTS
        assert read.config.stated_file is None

    def test_an_unreadable_file_names_itself(self):
        assert _read({TOML: {"status": "unreadable"}}).unreadable == TOML
        assert _read({INI: {"status": "unreadable"}}).unreadable == INI


class TestTheLegacyScan:
    def test_a_later_line_overwrites_an_earlier_one(self):
        read = _read({INI: "SaveFilePath=/first\nSaveFilePath=/second\n"})
        assert read.config is not None
        assert melonds.get_string(read.config, "Instance0.SaveFilePath") == "/second"

    def test_an_empty_value_leaves_the_entry_untouched(self):
        # The sscanf needs at least one byte after the '=' (Config.cpp:709),
        # so an empty assignment does not clear a value set above it.
        read = _read({INI: "SaveFilePath=/kept\nSaveFilePath=\n"})
        assert read.config is not None
        assert melonds.get_string(read.config, "Instance0.SaveFilePath") == "/kept"

    def test_a_tab_ends_the_value(self):
        read = _read({INI: "SaveFilePath=/saves\ttrailing\n"})
        assert read.config is not None
        assert melonds.get_string(read.config, "Instance0.SaveFilePath") == "/saves"

    def test_an_unknown_key_is_ignored(self):
        read = _read({INI: "NotAKey=1\nExternalBIOSEnable=1\n"})
        assert read.config is not None
        assert melonds.get_bool(read.config, "Emu.ExternalBIOSEnable") is True

    def test_a_bool_is_strtol_not_a_word(self):
        # The legacy table types the key as bool and LoadLegacyFile converts
        # with strtol (Config.cpp:751-753): "true" parses to 0, so it is false.
        read = _read({INI: "ExternalBIOSEnable=true\n"})
        assert read.config is not None
        assert melonds.get_bool(read.config, "Emu.ExternalBIOSEnable") is False
        read = _read({INI: "ExternalBIOSEnable= 1abc\n"})
        assert read.config is not None
        assert melonds.get_bool(read.config, "Emu.ExternalBIOSEnable") is True


class TestTheAccessors:
    def test_a_value_of_the_wrong_type_falls_to_the_default(self):
        read = _read({TOML: "[Instance0]\nSaveFilePath = 7\n[Emu]\nExternalBIOSEnable = 1\n"})
        assert read.config is not None
        assert melonds.get_string(read.config, "Instance0.SaveFilePath") == ""
        assert melonds.get_bool(read.config, "Emu.ExternalBIOSEnable") is False

    def test_the_console_type_is_clamped_to_its_declared_range(self):
        # IntRanges caps Emu.ConsoleType at {0, 1} (Config.cpp:79), so an
        # out-of-range value reads as the nearest bound, never as a refusal.
        for stated, expected in ((7, 1), (-3, 0), (1, 1), (0, 0)):
            read = _read({TOML: f"[Emu]\nConsoleType = {stated}\n"})
            assert read.config is not None
            assert melonds.console_type(read.config) == expected, stated

    def test_a_boolean_console_type_is_not_an_integer(self):
        read = _read({TOML: "[Emu]\nConsoleType = true\n"})
        assert read.config is not None
        assert melonds.console_type(read.config) == 0


class TestLocalFilePath:
    def test_an_absolute_value_stays(self):
        assert melonds.local_file_path(CONFIG_HOME, "/bios/bios9.bin") == "/bios/bios9.bin"

    def test_a_relative_value_joins_the_config_directory(self):
        assert (
            melonds.local_file_path(CONFIG_HOME, "bios9.bin")
            == f"{CONFIG_HOME}/melonDS/bios9.bin"
        )

    def test_an_empty_value_is_the_config_directory_itself(self):
        assert melonds.local_file_path(CONFIG_HOME, "") == f"{CONFIG_HOME}/melonDS"


class TestTheProbeSet:
    """``verifySetup``'s two switches, and what each combination probes."""

    def test_ds_mode_without_external_bios_probes_nothing(self):
        assert melonds.probed_firmware_keys(False, 0) == ()

    def test_ds_mode_with_external_bios_probes_the_pair_and_the_firmware(self):
        assert melonds.probed_firmware_keys(True, 0) == (
            "DS.BIOS9Path",
            "DS.BIOS7Path",
            "DS.FirmwarePath",
        )

    def test_dsi_mode_probes_its_bios_pair_and_nand_either_way(self):
        assert melonds.probed_firmware_keys(False, 1) == (
            "DSi.BIOS9Path",
            "DSi.BIOS7Path",
            "DSi.NANDPath",
        )

    def test_dsi_mode_with_external_bios_adds_both_firmwares(self):
        assert melonds.probed_firmware_keys(True, 1) == (
            "DS.BIOS9Path",
            "DS.BIOS7Path",
            "DSi.BIOS9Path",
            "DSi.BIOS7Path",
            "DSi.FirmwarePath",
            "DSi.NANDPath",
        )
