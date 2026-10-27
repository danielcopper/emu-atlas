"""EmuDeck's pinned launcher scripts (#573): the packaged table, what decides each step, and how pins are hashed.

The answers are held by ``vectors/machines/launch-command.json``; the deployed
files by ``tests/test_launcher_script_tripwire.py``.
"""

from __future__ import annotations

import json

import pytest

import atlas
from atlas.installations import EmuDeck
from atlas.launcher_scripts import (
    RUNS_BY_SETTINGS,
    LauncherState,
    launcher_rules,
    load_launcher_rules,
    ryujinx_names_sdl2,
    step_runs,
)
from atlas.machine import RealMachine

HASH = "a" * 40


def table(**changes: object) -> dict[str, object]:
    """The smallest table the loader takes, with *changes* applied to its one script."""
    script: dict[str, object] = {
        "script": "x.sh",
        "blob": HASH,
        "emulator": "x",
        "variant": "flatpak",
        "variant_citation": "x.sh:4",
        "executes": {},
        "steps": [
            {"code": "backend-update", "runs": True, "citation": "c"},
            {"code": "cloud-sync-upload", "runs": "settings", "citation": "c"},
        ],
    }
    script.update(changes)
    return {
        "schema": 1,
        "spec": "s",
        "backend": {"repository": "r", "revision": HASH},
        "sources": {"$emudeckBackend/functions/all.sh": HASH},
        "scripts": [script],
    }


class TestThePackagedTable:
    def test_it_loads_and_pins_every_script_at_one_revision(self):
        rules = launcher_rules()
        assert len(rules.revision) == 40
        assert len(rules.sources) == 101
        assert {rule.script for rule in rules.by_blob.values()} == {
            "bigpemu.sh",
            "cemu.sh",
            "dolphin-emu.sh",
            "model-2-emulator.sh",
            "ryujinx.sh",
            "shadps4.sh",
            "xenia.sh",
        }

    def test_every_script_runs_the_steps_emulator_init_runs(self):
        common = [
            atlas.STEP_BACKEND_UPDATE,
            atlas.STEP_LAUNCH_FIXES,
            atlas.STEP_SERVICE_STOP,
            atlas.STEP_SAVES_MARKER_WRITE,
            atlas.STEP_CLOUD_SYNC_DOWNLOAD,
            atlas.STEP_SERVICE_START,
            atlas.STEP_CLOUD_SYNC_CHECK,
            atlas.STEP_CLOUD_SYNC_UPLOAD,
            atlas.STEP_SAVES_MARKER_REMOVAL,
        ]
        for rule in launcher_rules().by_blob.values():
            codes = [step.code for step in rule.steps]
            assert [code for code in codes if code in common] == common, rule.script


class TestTheLoaderRefusesWhatItCannotRead:
    @pytest.mark.parametrize(
        ("changes", "message"),
        [
            ({"blob": "abc"}, "full blob hash"),
            ({"variant": "snap"}, "variant must be one of"),
            ({"executes": {"/tmp/x": HASH}}, "starts with none of"),
            ({"steps": [{"code": "backend-update", "runs": "settings", "citation": "c"}]}, "'settings' where"),
            ({"steps": [{"code": "reboot", "runs": True, "citation": "c"}]}, "step code must be one of"),
            (
                {
                    "steps": [
                        {"code": "service-stop", "runs": True, "citation": "c"},
                        {"code": "backend-update", "runs": True, "citation": "c"},
                    ]
                },
                "in the order",
            ),
            ({"steps": [{"code": "backend-update", "runs": True, "citation": " "}]}, "carries a citation"),
        ],
    )
    def test_a_malformed_script_is_refused(self, changes: dict[str, object], message: str):
        text = json.dumps(table(**changes))
        with pytest.raises(ValueError, match=message):
            load_launcher_rules(text)

    def test_a_source_outside_the_backend_is_refused(self):
        document = table()
        document["sources"] = {"$toolsPath/x.sh": HASH}
        text = json.dumps(document)
        with pytest.raises(ValueError, match="starts with none of"):
            load_launcher_rules(text)

    def test_two_scripts_under_one_blob_are_refused(self):
        document = table()
        scripts = document["scripts"]
        assert isinstance(scripts, list)
        document["scripts"] = [*scripts, *scripts]
        text = json.dumps(document)
        with pytest.raises(ValueError, match="two scripts pinned by one blob"):
            load_launcher_rules(text)


def sync(rclone: bool | None = True, status: str = "true", provider: str = "Emudeck-GDrive", net_play: str = ""):
    return LauncherState(rclone=rclone, status=status, provider=provider, net_play=net_play)


def device(
    product: str | None = "Some PC",
    auto_map: str = "",
    gyro: bool | None = True,
    installed: bool | None = True,
    sdl2: bool | None = True,
) -> LauncherState:
    return LauncherState(
        rclone=False,
        status="",
        provider="",
        net_play="",
        auto_map=auto_map,
        product=product,
        gyro=gyro,
        ryujinx_installed=installed,
        ryujinx_sdl2=sdl2,
    )


class TestWhatTheSettingsDecide:
    """D22: true or false where a setting decides it, null where state at launch does."""

    @pytest.mark.parametrize(
        ("code", "state", "runs"),
        [
            (atlas.STEP_CLOUD_SYNC_DOWNLOAD, sync(), None),
            (atlas.STEP_CLOUD_SYNC_DOWNLOAD, sync(provider="gdrive"), True),
            (atlas.STEP_CLOUD_SYNC_DOWNLOAD, sync(rclone=False), False),
            (atlas.STEP_CLOUD_SYNC_DOWNLOAD, sync(rclone=None), None),
            (atlas.STEP_CLOUD_SYNC_DOWNLOAD, sync(status="false"), False),
            (atlas.STEP_SERVICE_START, sync(), None),
            (atlas.STEP_SERVICE_START, sync(provider="gdrive"), True),
            (atlas.STEP_SERVICE_START, sync(rclone=False), True),
            (atlas.STEP_SERVICE_START, sync(rclone=None), None),
            (atlas.STEP_SERVICE_START, sync(status=""), False),
            (atlas.STEP_CLOUD_SYNC_CHECK, sync(), True),
            (atlas.STEP_CLOUD_SYNC_CHECK, sync(net_play="true"), False),
            (atlas.STEP_CLOUD_SYNC_CHECK, sync(rclone=False), False),
            (atlas.STEP_CLOUD_SYNC_CHECK, sync(rclone=None), None),
            (atlas.STEP_CLOUD_SYNC_CHECK, sync(status="false"), False),
            (atlas.STEP_CLOUD_SYNC_UPLOAD, sync(), None),
            (atlas.STEP_CLOUD_SYNC_UPLOAD, sync(rclone=False), None),
            (atlas.STEP_CLOUD_SYNC_UPLOAD, sync(status="false"), False),
        ],
    )
    def test_a_settings_step(self, code: atlas.LauncherStepCode, state: LauncherState, runs: bool | None):
        assert step_runs(code, RUNS_BY_SETTINGS, state) is runs

    @pytest.mark.parametrize(
        ("emulator", "state", "runs"),
        [
            ("dolphin", device(), True),
            ("dolphin", device(product="Jupiter"), False),
            ("Cemu", device(product="Galileo"), False),
            ("Cemu", device(auto_map="false"), False),
            ("dolphin", device(product=None), None),
            ("dolphin", device(product=None, auto_map="false"), False),
            ("dolphin", device(product=""), True),
            ("ryujinx", device(), True),
            ("ryujinx", device(auto_map="false"), False),
            ("ryujinx", device(product="Jupiter", gyro=True), False),
            ("ryujinx", device(product="Jupiter", gyro=False), True),
            ("ryujinx", device(product="Galileo", gyro=None), None),
            ("ryujinx", device(product="Jupiter", gyro=False, auto_map="false"), True),
            ("ryujinx", device(product=None), None),
        ],
    )
    def test_a_launch_fix_is_decided_by_its_own_guards(self, emulator: str, state: LauncherState, runs: bool | None):
        assert step_runs(atlas.STEP_LAUNCH_FIXES, RUNS_BY_SETTINGS, state, emulator) is runs

    @pytest.mark.parametrize(
        ("state", "runs"),
        [
            (device(), True),
            (device(installed=False), False),
            (device(sdl2=False), False),
            (device(installed=None), None),
            (device(sdl2=None), None),
            (device(installed=None, sdl2=False), False),
        ],
    )
    def test_ryujinx_reinstalls_where_it_is_there_and_names_sdl2(self, state: LauncherState, runs: bool | None):
        assert step_runs(atlas.STEP_EMULATOR_INSTALL, RUNS_BY_SETTINGS, state, "ryujinx") is runs

    @pytest.mark.parametrize("runs", [True, False, None])
    def test_the_table_s_own_answer_stands(self, runs: bool | None):
        assert step_runs(atlas.STEP_BACKEND_UPDATE, runs, sync(status="false")) is runs


class TestTheReinstallConditionReadsAsJqReadsIt:
    @pytest.mark.parametrize(
        ("text", "names"),
        [
            ('{"input_config": [{"backend": "GamepadSDL2"}]}', True),
            ('{"input_config": [{"backend": "WindowKeyboard"}, {"backend": "GamepadSDL2Fork"}]}', True),
            ('{"input_config": [{"backend": "GamepadSDL3"}]}', False),
            ('{"input_config": [{"id": 1}, {"backend": null}, 3]}', False),
            ('{"input_config": [{"backend": false}, {"backend": "GamepadSDL2"}]}', True),
            ('{"input_config": {"a": {"backend": "GamepadSDL2"}}}', True),
            ('{"other": 1}', False),
            ("[]", False),
            ('{"input_config": [{"backend": 2}]}', None),
            ("{not json", None),
        ],
    )
    def test_a_configuration(self, text: str, names: bool | None):
        assert ryujinx_names_sdl2(text) is names


class _CountingMachine(RealMachine):
    def __init__(self) -> None:
        super().__init__()
        self.digests = 0

    def file_digest(self, path: str, algorithm: str, *, first_bytes: int | None = None) -> str | None:
        self.digests += 1
        return super().file_digest(path, algorithm, first_bytes=first_bytes)


class TestPinsAreHashedOncePerFile:
    def test_an_unchanged_file_is_hashed_once_and_a_changed_one_again(self, tmp_path):
        machine = _CountingMachine()
        hashes = EmuDeck(str(tmp_path), machine)._blob_hashes  # pyright: ignore[reportPrivateUsage] - the cache
        script = tmp_path / "x.sh"
        script.write_bytes(b"hello\n")
        assert hashes.hash(str(script)) == "ce013625030ba8dba906f756967f9e9ca394464a"
        assert hashes.hash(str(script)) == "ce013625030ba8dba906f756967f9e9ca394464a"
        assert machine.digests == 1
        script.write_bytes(b"hello again\n")
        assert hashes.hash(str(script)) != "ce013625030ba8dba906f756967f9e9ca394464a"
        assert machine.digests == 2
