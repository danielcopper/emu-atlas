"""Tests for atlas.detect — marker ordering, identity overlap, coexistence, own machine."""

from __future__ import annotations

import subprocess
from types import SimpleNamespace

import atlas
import atlas.machine
from atlas.installations import RETRODECK_JSON_SUFFIX
from atlas.machine import FixtureMachine, RealMachine

RETRODECK_JSON = "/home/deck/.var/app/net.retrodeck.retrodeck/config/retrodeck/retrodeck.json"
EMUDECK_SETTINGS = "/home/deck/.config/EmuDeck/settings.sh"
STANDALONE_CFG = "/home/deck/.var/app/org.libretro.RetroArch/config/retroarch/retroarch.cfg"
NATIVE_CFG = "/home/deck/.config/retroarch/retroarch.cfg"

HOME = "/home/deck"


def _detect(files, **kwargs):
    return atlas.detect(HOME, FixtureMachine(files, **kwargs))


class TestMarkers:
    def test_empty_machine_detects_nothing(self):
        assert _detect({}) == []

    def test_retrodeck_by_json(self):
        installs = _detect({RETRODECK_JSON: '{"paths": {"rd_home_path": "/mnt/sd/retrodeck"}}'})
        assert [i.kind for i in installs] == ["retrodeck"]

    def test_standalone_flatpak_by_cfg(self):
        installs = _detect({STANDALONE_CFG: ""})
        assert [i.kind for i in installs] == ["bare_retroarch_flatpak"]

    def test_native_by_cfg(self):
        installs = _detect({NATIVE_CFG: ""})
        assert [i.kind for i in installs] == ["bare_retroarch_native"]

    def test_emudeck_by_settings(self):
        installs = _detect({EMUDECK_SETTINGS: 'savesPath="$HOME/Emulation/saves"\n'})
        assert [i.kind for i in installs] == ["emudeck"]


class TestIdentityOverlap:
    def test_emudeck_claims_the_standalone_flatpak(self):
        # EmuDeck IS a configured org.libretro.RetroArch — one handle, both
        # descriptions, never a second handle for the same installation.
        installs = _detect(
            {
                EMUDECK_SETTINGS: 'savesPath="$HOME/Emulation/saves"\n',
                STANDALONE_CFG: 'savefile_directory = "/home/deck/Emulation/saves/retroarch/saves"\n',
            }
        )
        assert [i.kind for i in installs] == ["emudeck"]
        assert installs[0].kinds == ("emudeck", "bare_retroarch_flatpak")

    def test_unclaimed_flatpak_is_its_own_installation(self):
        installs = _detect({STANDALONE_CFG: ""})
        assert installs[0].kinds == ("bare_retroarch_flatpak",)


class TestCoexistence:
    def test_all_four_markers_priority_order(self):
        installs = _detect(
            {
                RETRODECK_JSON: '{"paths": {"rd_home_path": "/mnt/sd/retrodeck"}}',
                EMUDECK_SETTINGS: 'savesPath="$HOME/Emulation/saves"\n',
                STANDALONE_CFG: "",
                NATIVE_CFG: "",
            }
        )
        # EmuDeck claims the flatpak; RetroDECK first, native last.
        assert [i.kind for i in installs] == ["retrodeck", "emudeck", "bare_retroarch_native"]

    def test_retrodeck_and_native(self):
        installs = _detect(
            {
                RETRODECK_JSON: '{"paths": {"rd_home_path": "/mnt/sd/retrodeck"}}',
                NATIVE_CFG: "",
            }
        )
        assert [i.kind for i in installs] == ["retrodeck", "bare_retroarch_native"]


class TestHealth:
    def test_retrodeck_root_missing(self):
        installs = _detect({RETRODECK_JSON: '{"paths": {"rd_home_path": "/run/media/gone/retrodeck"}}'})
        health = installs[0].health()
        assert not health.ok
        assert atlas.HEALTH_ISSUE_ROOT_MISSING in health.codes

    def test_retrodeck_healthy(self):
        installs = _detect(
            {
                RETRODECK_JSON: '{"paths": {"rd_home_path": "/mnt/sd/retrodeck"}}',
                "/mnt/sd/retrodeck/roms/systeminfo.txt": "",
            },
            dirs=["/mnt/sd/retrodeck/saves"],
        )
        assert installs[0].health() == atlas.Health()

    def test_retrodeck_missing_saves_root_is_an_issue(self):
        installs = _detect(
            {
                RETRODECK_JSON: '{"paths": {"rd_home_path": "/mnt/sd/retrodeck"}}',
                "/mnt/sd/retrodeck/roms/systeminfo.txt": "",
            }
        )
        assert installs[0].health().codes == (atlas.HEALTH_ISSUE_SAVES_ROOT_MISSING,)

    def test_retrodeck_unparseable_json(self):
        installs = _detect({RETRODECK_JSON: "not json{{"})
        assert atlas.HEALTH_ISSUE_MARKER_INVALID in installs[0].health().codes

    def test_retrodeck_unreadable_marker_is_detected_and_reported(self):
        # An unreadable marker is a PRESENT, broken RetroDECK — it must not
        # disappear from detection (REVIEW H10).
        installs = _detect({RETRODECK_JSON: {"status": "unreadable"}})
        assert [i.kind for i in installs] == ["retrodeck"]
        assert atlas.HEALTH_ISSUE_MARKER_UNREADABLE in installs[0].health().codes

    def test_emudeck_saves_root_missing(self):
        installs = _detect({EMUDECK_SETTINGS: 'savesPath="/gone/Emulation/saves"\n'})
        assert atlas.HEALTH_ISSUE_SAVES_ROOT_MISSING in installs[0].health().codes

    def test_emudeck_stale_marker_reports_missing_companion(self):
        # settings.sh survives while the Flatpak is gone: the handle stays,
        # and the companion issue makes the staleness visible (REVIEW H10).
        installs = _detect(
            {
                EMUDECK_SETTINGS: 'romsPath="$HOME/Emulation/roms"\nsavesPath="$HOME/Emulation/saves"\n',
                "/home/deck/Emulation/roms/gba/game.zip": "",
            },
            dirs=["/home/deck/Emulation/saves"],
        )
        assert installs[0].health().codes == (atlas.HEALTH_ISSUE_COMPANION_CONFIG_MISSING,)

    def test_bare_flatpak_unreadable_cfg(self):
        installs = _detect({STANDALONE_CFG: {"status": "unreadable"}})
        assert [i.kind for i in installs] == ["bare_retroarch_flatpak"]
        assert installs[0].health().codes == (atlas.HEALTH_ISSUE_CONFIG_UNREADABLE,)


class TestTheMachineDetectBuildsForItself:
    """Handed no machine, ``detect`` builds one — a new one on every call.

    That is the second half of the guidance ``RealMachine`` publishes: a core
    probe is remembered only as far as the machine that ran it, so a caller
    that re-detects for every question shares nothing between the answers and
    pays a hanging core's full timeout every time. Every other test in this
    file hands in a fixture, so this is the one that enters the branch.
    """

    def _home_with_a_retrodeck_marker(self, tmp_path):
        marker = tmp_path / RETRODECK_JSON_SUFFIX
        marker.parent.mkdir(parents=True)
        marker.write_text('{"paths": {"rd_home_path": "/mnt/sd/retrodeck"}}')
        return str(tmp_path)

    def _machines_built(self, monkeypatch):
        """Every ``RealMachine`` built from here on, in the order they were built."""
        built: list[RealMachine] = []
        constructor = RealMachine.__init__

        def recording_init(machine: RealMachine) -> None:
            constructor(machine)
            built.append(machine)

        monkeypatch.setattr(RealMachine, "__init__", recording_init)
        return built

    def _hanging_probe(self, monkeypatch):
        """Every core probe hangs; returns the spawn list."""
        spawns: list[list[str]] = []

        def fake_run(argv, **kwargs):
            spawns.append(argv)
            raise subprocess.TimeoutExpired(cmd=argv, timeout=15)

        monkeypatch.setattr(
            atlas.machine,
            "subprocess",
            SimpleNamespace(run=fake_run, TimeoutExpired=subprocess.TimeoutExpired),
        )
        return spawns

    def test_two_calls_share_no_probe_memory(self, tmp_path, monkeypatch):
        home = self._home_with_a_retrodeck_marker(tmp_path)
        so = tmp_path / "mgba_libretro.so"
        so.write_bytes(b"\x7fELF")
        spawns = self._hanging_probe(monkeypatch)
        built = self._machines_built(monkeypatch)

        assert [i.kind for i in atlas.detect(home)] == ["retrodeck"]
        assert [i.kind for i in atlas.detect(home)] == ["retrodeck"]

        # One machine per call, and neither knows what the other asked: the
        # same hanging core costs a spawn on each. A machine built once and
        # reused would answer the second question from the first one's memory.
        assert len(built) == 2
        first, second = built
        assert first is not second
        assert first.query_core(str(so)) is None
        assert first.query_core(str(so)) is None
        assert len(spawns) == 1
        assert second.query_core(str(so)) is None
        assert len(spawns) == 2
