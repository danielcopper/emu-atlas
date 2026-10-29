"""Tests for atlas.rpcs3_boot — which entries the reading covers, and the shape of what it states.

What RPCS3 boots from each kind of path is held by the machines vectors
(``vectors/machines/rpcs3-boot.json``), which run the reading end to end
through both arrangements; this module holds the parts a vector cannot
isolate: the exact command shapes the reading is applied to, and the
reading's own consistency rules.
"""

from __future__ import annotations

import pytest

from atlas import rpcs3_boot

DIRECTORY = "%EMULATOR_RPCS3% --no-gui %ROM%"
SERIAL = "%EMULATOR_RPCS3% --no-gui %RPCS3_GAMEID%:%INJECT%=%BASENAME%.ps3"
SHORTCUT = "%ENABLESHORTCUTS% %EMULATOR_OS-SHELL% %ROM%"


class TestTheReadingCoversTheShapesTheCatalogueShips:
    def test_the_directory_and_iso_command_hands_the_path(self):
        assert rpcs3_boot.hands_path(DIRECTORY)

    @pytest.mark.parametrize(
        "command",
        ["%EMULATOR_RPCS3% %ROM%", "%EMULATOR_RPCS3% --no-gui --fullscreen %ROM%", SERIAL, SHORTCUT],
    )
    def test_any_other_command_is_not_read_as_handing_the_path(self, command):
        assert not rpcs3_boot.hands_path(command)

    def test_the_game_serial_command_boots_by_serial(self):
        assert rpcs3_boot.boots_by_serial(SERIAL)
        assert not rpcs3_boot.boots_by_serial(DIRECTORY)
        assert not rpcs3_boot.boots_by_serial("%EMULATOR_OS-SHELL% %RPCS3_GAMEID%")

    @pytest.mark.parametrize(
        "command",
        [
            "%EMULATOR_RPCS3% --no-gui --fullscreen %RPCS3_GAMEID%:%INJECT%=%BASENAME%.ps3",
            "%EMULATOR_RPCS3% %RPCS3_GAMEID%:%INJECT%=%BASENAME%.ps3",
        ],
    )
    def test_a_serial_command_of_another_shape_is_not_read(self, command):
        assert not rpcs3_boot.boots_by_serial(command)

    def test_the_shortcut_command_runs_the_rom_through_the_shell(self):
        assert rpcs3_boot.shell_runs_rom(SHORTCUT)
        assert rpcs3_boot.shell_runs_rom("%EMULATOR_OS-SHELL% %ROM%")
        assert not rpcs3_boot.shell_runs_rom("%EMULATOR_OS-SHELL% -c %ROM%")

    def test_the_serial_entry_names_the_registry_it_does_not_read(self):
        reading = rpcs3_boot.by_serial("RPCS3 Game Serial (Standalone)")
        assert reading.outcome == rpcs3_boot.UNESTABLISHED
        assert [(c.code, c.data) for c in reading.caveats] == [
            (
                rpcs3_boot.CAVEAT_BOOT_REGISTRY_UNREAD,
                {"entry": "RPCS3 Game Serial (Standalone)", "registry": rpcs3_boot.GAME_REGISTRY},
            )
        ]


class TestAReadingStatesABootExactlyWhereItBoots:
    def test_a_boot_without_a_file_is_refused(self):
        with pytest.raises(ValueError, match="exactly where the outcome is boots"):
            rpcs3_boot.BootReading(rpcs3_boot.BOOTS)

    def test_a_file_beside_another_outcome_is_refused(self):
        with pytest.raises(ValueError, match="exactly where the outcome is boots"):
            rpcs3_boot.BootReading(rpcs3_boot.REFUSES, file="/roms/Game.ps3/EBOOT.BIN")

    def test_a_disc_root_without_a_file_is_refused(self):
        with pytest.raises(ValueError, match="a disc root stands only beside a boot file"):
            rpcs3_boot.BootReading(rpcs3_boot.UNESTABLISHED, disc_root="/roms/Game.ps3")
