"""EmuDeck's side of the launch lookup (#84 part 3): the host view, the build stamp, and the start's rewrite.

The lookup itself is held in ``test_launch.py`` and the answers by the
``launch`` vectors; what is held here is what only EmuDeck's handle does — the
view it hands the lookup, the ES-DE build it reads off the AppImage, and the
notes it builds out of the rewrite its launcher runs before every start.
"""

from __future__ import annotations

import os
import re
import shutil
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest

import atlas
from atlas.installations import _HostLaunchView  # pyright: ignore[reportPrivateUsage] - the view the handle builds
from atlas.esde import EmulatorSpec
from atlas.launch import PROBE_HIT, PROBE_MISS, PROBE_UNKNOWN, LaunchResolution
from atlas.machine import FixtureFileSpec, FixtureMachine, RealMachine

HOME = "/home/deck"
APPIMAGE = f"{HOME}/Applications/ES-DE.AppImage"
BINARY = "usr/bin/es-de"
CUSTOM = f"{HOME}/ES-DE/custom_systems/es_find_rules.xml"
TOOLS = f"{HOME}/Emulation/tools"
SETTINGS = f"{HOME}/.config/EmuDeck/settings.sh"
DATA = Path(__file__).parent / "data"


def view(machine: FixtureMachine) -> _HostLaunchView:
    return _HostLaunchView(machine=machine, home=HOME, search_path=("/usr/bin",), rom_root=lambda: None)


class TestTheHostViewIsTheHostsOwn:
    def test_a_file_and_any_symlink_are_hits(self):
        machine = FixtureMachine(
            {"/usr/bin/retroarch": "", "/opt/real": ""},
            symlinks={"/usr/bin/live": "/opt/real", "/usr/bin/dead": "/nowhere"},
        )
        host = view(machine)
        assert [host.found(p) for p in ("/usr/bin/retroarch", "/usr/bin/live", "/usr/bin/dead")] == [PROBE_HIT] * 3

    def test_a_directory_and_nothing_are_misses(self):
        host = view(FixtureMachine({"/usr/bin/retroarch": ""}))
        assert [host.found(p) for p in ("/usr/bin", "/usr/bin/none", "")] == [PROBE_MISS] * 3

    def test_what_cannot_be_stat_ed_cannot_be_told(self):
        host = view(FixtureMachine({"/usr/bin/retroarch": ""}, inaccessible=["/mnt/card"]))
        assert host.found("/mnt/card/RetroArch.AppImage") == PROBE_UNKNOWN

    def test_a_relative_path_and_one_inside_the_image_cannot_be_told(self):
        host = view(FixtureMachine({"/usr/bin/retroarch": ""}))
        assert host.found("retroarch") == PROBE_UNKNOWN
        assert host.found("%ESPATH%/emulators/xemu") == PROBE_UNKNOWN
        # An absolute spelling around it is still inside the image's mount once ES-DE substitutes it.
        assert host.found(f"{HOME}/%ESPATH%/emulators/xemu") == PROBE_UNKNOWN
        assert host.listing("%ESPATH%/emulators") is None
        assert host.es_path == "%ESPATH%"
        assert host.app_id is None

    def test_a_listing_holds_every_name_and_states_what_it_cannot_read(self):
        machine = FixtureMachine(
            {f"{HOME}/Applications/b.AppImage": "", f"{HOME}/Applications/.a.AppImage": "", "/f": ""},
            unlistable=["/locked"],
            inaccessible=["/gone"],
        )
        host = view(machine)
        assert sorted(host.listing(f"{HOME}/Applications") or ()) == [".a.AppImage", "b.AppImage"]
        assert host.listing("/f") == ()
        assert host.listing("/locked") is None
        assert host.listing("/gone") is None

    def test_run_game_s_test_is_never_a_hit(self):
        assert view(FixtureMachine({"/usr/bin/retroarch": ""})).executable("/usr/bin/retroarch") == PROBE_MISS


def emudeck(
    *, appimage: Mapping[str, object] | str, stamps: dict[str, object] | None = None
) -> atlas.EmuDeck:
    machine = FixtureMachine(
        {SETTINGS: "doInstallESDE=true\n", APPIMAGE: {"status": "invalid-text"}},
        appimages={APPIMAGE: appimage},
        appimage_stamps=stamps,
    )
    return atlas.EmuDeck(HOME, machine)


def build(handle: atlas.EmuDeck) -> str | None:
    return handle._deployed_esde_build()  # pyright: ignore[reportPrivateUsage] - the gate #573 reads


class TestTheBuildIsReadOffTheAppImage:
    IMAGE = {BINARY: {"status": "invalid-text"}}

    def test_the_stamp_is_the_build(self):
        assert build(emudeck(appimage=self.IMAGE, stamps={APPIMAGE: {BINARY: "3.4.1"}})) == "3.4.1"

    @pytest.mark.parametrize("state", ["no-stamp", "ambiguous"])
    def test_a_stamp_the_read_refuses_is_no_build(self, state: str):
        assert build(emudeck(appimage=self.IMAGE, stamps={APPIMAGE: {BINARY: state}})) is None

    def test_a_declared_entry_with_no_stamp_is_no_build(self):
        assert build(emudeck(appimage=self.IMAGE)) is None

    def test_an_image_this_runtime_cannot_open_is_no_build(self):
        assert build(emudeck(appimage="capability-missing")) is None

    def test_the_pattern_is_the_version_line_s_two_literals(self):
        # The layout of the 3.4.1 binary: --version's "ES-DE " then
        # PROGRAM_VERSION_STRING, each NUL-ended, among other version-shaped
        # strings that are not the build.
        binary = b"--help\x00ES-DE \x003.4.1\x00)\n\x00-h\x00\x0010.2.0\x00\x00\x001.3.2\x00GLIBCXX_3.4.1\x00"
        assert atlas.EmuDeck.ESDE_VERSION_STAMP.findall(binary) == [b"3.4.1"]
        assert re.compile(rb"\x00(\d+\.\d+\.\d+)\x00").findall(binary) != [b"3.4.1"]


class _CountingMachine(RealMachine):
    def __init__(self) -> None:
        super().__init__()
        self.stamp_reads = 0

    def read_appimage_stamp(self, path: str, inner_path: str, pattern: re.Pattern[bytes]):
        self.stamp_reads += 1
        return super().read_appimage_stamp(path, inner_path, pattern)


class TestTheBuildIsKeptUnderTheImagesStamp:
    """On a real machine the read is made once per AppImage revision, as the find-rules parses are."""

    @pytest.fixture
    def handle(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[atlas.EmuDeck, _CountingMachine, Path]:
        image = tmp_path / "Applications" / "ES-DE.AppImage"
        image.parent.mkdir()
        shutil.copyfile(DATA / "esde-like.gzip.appimage", image)
        # The fixture image carries no frontend binary: its catalogue entry and
        # a pattern that matches it once stand in for the binary and the stamp.
        catalogue = "usr/share/es-de/resources/systems/linux/es_systems.xml"
        monkeypatch.setattr(atlas.EmuDeck, "ESDE_BINARY_ENTRY", catalogue)
        monkeypatch.setattr(atlas.EmuDeck, "ESDE_VERSION_STAMP", re.compile(rb"<name>([a-z]+)</name>"))
        machine = _CountingMachine()
        return atlas.EmuDeck(str(tmp_path), machine), machine, image

    def test_an_unchanged_image_is_read_once(self, handle):
        emu, machine, _ = handle
        assert build(emu) == "gba"
        assert build(emu) == "gba"
        assert machine.stamp_reads == 1

    def test_a_replaced_image_is_read_again(self, handle):
        emu, machine, image = handle
        assert build(emu) == "gba"
        before = image.stat()
        os.utime(image, ns=(before.st_atime_ns, before.st_mtime_ns + 1_000_000))
        assert build(emu) == "gba"
        assert machine.stamp_reads == 2


SET = {"toolsPath": TOOLS}


def notes(custom: str | dict[str, str] | None, settings: dict[str, str] = SET) -> dict[str, dict[str, object]]:
    """What the handle's next-start reading states per token, read off a machine with that custom file."""
    files: dict[str, str | dict[str, str]] = {SETTINGS: "doInstallESDE=true\n"}
    if custom is not None:
        files[CUSTOM] = custom
    handle = atlas.EmuDeck(HOME, FixtureMachine(files))
    text = handle._find_rules_files.read(CUSTOM).text  # pyright: ignore[reportPrivateUsage] - the lookup's one read
    made = handle._start_rewrite_notes(text, settings)  # pyright: ignore[reportPrivateUsage] - the rule under test
    return {token: dict(note.data) for token, note in made.items()}


def rules(*blocks: str) -> str:
    return '<?xml version="1.0"?>\n<ruleList>\n' + "".join(blocks) + "</ruleList>\n"


def block(name: str, entry: str) -> str:
    return (
        f'    <emulator name="{name}">\n        <rule type="staticpath">\n'
        f"            <entry>{entry}</entry>\n        </rule>\n    </emulator>\n"
    )


class TestTheNextStartsRewriteIsStated:
    """``esde_launch_fixes`` at dragoonDorise/EmuDeck@863ab69, emuDeckESDE.sh:524-553 and :569-575."""

    ALL = rules(
        block("DOLPHIN", f"{TOOLS}/launchers/dolphin-emu.sh"),
        block("CEMU", f"{TOOLS}/launchers/cemu.sh"),
        block("RYUJINX", f"{TOOLS}/launchers/ryujinx.sh"),
    )

    def test_a_file_that_carries_every_entry_is_left_alone(self):
        assert notes(self.ALL) == {}

    def test_no_custom_file_is_no_rewrite(self):
        assert notes(None) == {}

    def test_an_entry_the_file_does_not_carry_is_the_one_the_start_writes(self):
        custom = rules(block("CEMU", "/old/cemu.sh"), block("RYUJINX", "/old/ryujinx.sh"))
        assert notes(custom) == {
            "DOLPHIN": {"emulator_token": "DOLPHIN", "entry": f"{TOOLS}/launchers/dolphin-emu.sh"},
            "CEMU": {"emulator_token": "CEMU", "entry": f"{TOOLS}/launchers/cemu.sh"},
        }

    def test_ryujinx_is_added_only_where_the_file_names_none(self):
        dolphin_and_cemu = (
            block("DOLPHIN", f"{TOOLS}/launchers/dolphin-emu.sh"),
            block("CEMU", f"{TOOLS}/launchers/cemu.sh"),
        )
        assert notes(rules(block("RYUJINX", "/somewhere/else.sh"), *dolphin_and_cemu)) == {}
        assert notes(rules(*dolphin_and_cemu)) == {
            "RYUJINX": {"emulator_token": "RYUJINX", "entry": f"{TOOLS}/launchers/ryujinx.sh"}
        }

    def test_a_custom_file_atlas_could_not_read_states_nothing(self):
        assert notes({"status": "unreadable"}) == {}

    def test_an_unset_tools_path_is_emudeck_s_default(self):
        made = notes(rules(), settings={})
        assert made["DOLPHIN"]["entry"] == f"{HOME}/Emulation/tools/launchers/dolphin-emu.sh"


SYSTEMS = f"{HOME}/ES-DE/custom_systems/es_systems.xml"
BASH = "/bin/bash"
LITERAL = "/run/media/mmcblk0p1/Emulation"
SCRIPT = f"{TOOLS}/launchers/bigpemu.sh"


def catalogue(*commands: str) -> str:
    rows = "".join(f'    <command label="row{index}">{command}</command>\n' for index, command in enumerate(commands))
    return (
        '<?xml version="1.0"?>\n<systemList>\n  <system><name>x</name><path>%ROMPATH%/x</path>'
        f"<extension>.iso</extension>\n{rows}  </system>\n</systemList>\n"
    )


def entries(
    *commands: str,
    settings: str = "doInstallESDE=true\n",
    files: Mapping[str, FixtureFileSpec] | None = None,
    bash: bool = True,
    **machine: Any,
) -> list[atlas.EmulatorEntry]:
    present: dict[str, FixtureFileSpec] = {SETTINGS: settings, SYSTEMS: catalogue(*commands)}
    if bash:
        present[BASH] = ""
    present.update(files or {})
    handle = atlas.EmuDeck(HOME, FixtureMachine(present, **machine))
    return list(handle.emulators_for("x").entries)


def launch(entry: atlas.EmulatorEntry) -> tuple[str, list[tuple[str, dict[str, object]]]]:
    return entry.availability, [(c.code, dict(c.data)) for c in entry.caveats]


class TestTheLauncherScriptIsWhatTheStartTurnsOn:
    """D11-D14 of #84: ``/bin/bash <script>`` — ES-DE finds bash, and bash opens the script."""

    def test_a_present_script_leaves_bash_startable_without_a_path_note(self):
        [entry] = entries(f"{BASH} {SCRIPT} %ROM%", files={SCRIPT: "#!/bin/bash\n"})
        assert launch(entry) == (atlas.AVAILABILITY_STARTABLE, [])
        assert entry.launcher == atlas.Launcher(BASH, None, None, atlas.RULE_COMMAND, BASH)

    def test_no_file_a_dead_link_or_a_directory_is_a_missing_script(self):
        dead = f"{TOOLS}/launchers/dead.sh"
        folder = f"{TOOLS}/launchers/folder.sh"
        rows = [f"{BASH} {path} %ROM%" for path in (SCRIPT, dead, folder)]
        answers = entries(*rows, symlinks={dead: "/nowhere"}, dirs=[folder])
        assert [launch(entry) for entry in answers] == [
            (atlas.AVAILABILITY_NOT_INSTALLED, [(atlas.CAVEAT_LAUNCHER_SCRIPT_MISSING, {"path": path})])
            for path in (SCRIPT, dead, folder)
        ]
        assert [entry.launcher for entry in answers] == [None] * 3

    def test_a_link_to_a_file_is_present(self):
        linked = f"{TOOLS}/launchers/linked.sh"
        [entry] = entries(f"{BASH} {linked} %ROM%", files={SCRIPT: ""}, symlinks={linked: SCRIPT})
        assert entry.availability == atlas.AVAILABILITY_STARTABLE

    def test_a_script_atlas_cannot_read_as_a_path_is_not_established(self):
        words = (SCRIPT, "%ROM%", "run.sh", f"'{SCRIPT}'", f"{HOME}/$GAME.sh", f"{HOME}/%BASENAME%.sh")
        answers = entries(*(f"{BASH} {word} %ROM%" for word in words), inaccessible=[f"{TOOLS}/launchers"])
        assert [launch(entry)[1] for entry in answers] == [
            [(atlas.CAVEAT_LAUNCH_PATH_UNESTABLISHED, {"path": word})] for word in words
        ]

    def test_a_glob_or_an_operator_in_the_script_word_is_not_established(self):
        words = (f"{TOOLS}/launchers/big*.sh", f"{TOOLS}/launchers/bigpemu.sh;true")
        answers = entries(*(f"{BASH} {word} %ROM%" for word in words), files={SCRIPT: ""})
        assert [launch(entry)[1] for entry in answers] == [
            [(atlas.CAVEAT_LAUNCH_PATH_UNESTABLISHED, {"path": word})] for word in words
        ]

    def test_the_script_is_read_after_the_flags_es_de_removes_first(self):
        [entry] = entries(f"%RUNINBACKGROUND% {BASH} /missing.sh %ROM%")
        assert launch(entry) == (
            atlas.AVAILABILITY_NOT_INSTALLED,
            [(atlas.CAVEAT_LAUNCHER_SCRIPT_MISSING, {"path": "/missing.sh"})],
        )

    def test_a_token_in_the_script_s_place_is_the_find_rules_answer(self):
        launcher = f"{TOOLS}/launchers/dolphin-emu.sh"
        custom = rules(block("DOLPHIN", launcher))
        [entry] = entries(f"{BASH} %EMULATOR_DOLPHIN% %ROM%", files={CUSTOM: custom, launcher: ""})
        assert launch(entry) == (atlas.AVAILABILITY_STARTABLE, [])
        assert entry.launcher is not None
        assert (entry.launcher.rule, entry.launcher.path) == ("staticpath", launcher)

    def test_tilde_in_the_script_is_es_de_s_home(self):
        [entry] = entries(f"{BASH} ~/run.sh %ROM%", files={f"{HOME}/run.sh": ""})
        assert entry.availability == atlas.AVAILABILITY_STARTABLE

    def test_bash_not_found_is_the_answer_and_the_script_is_not_asked(self):
        [entry] = entries(f"{BASH} {SCRIPT} %ROM%", bash=False)
        availability, caveats = launch(entry)
        assert availability == atlas.AVAILABILITY_NOT_INSTALLED
        assert [code for code, _ in caveats] == [atlas.CAVEAT_PROGRAM_NOT_FOUND, atlas.CAVEAT_LAUNCH_PATH_ASSUMED]

    def test_another_first_word_gets_the_second_method_alone(self):
        [entry] = entries(f"/usr/bin/bash {SCRIPT} %ROM%", files={"/usr/bin/bash": ""})
        assert entry.availability == atlas.AVAILABILITY_STARTABLE


class TestTheCommandTheStartRewritesIsStated:
    """D13 of #84: ``ESDE_ensureSystemsPath``, emuDeckESDE.sh:555-560 at 863ab69."""

    ROW = f"{BASH} {LITERAL}/tools/launchers/bigpemu.sh %ROM%"

    def test_the_literal_and_another_emulation_path_is_the_note_beside_the_verdict(self):
        [entry] = entries(self.ROW, settings=f'emulationPath="{HOME}/Emulation"\ndoInstallESDE=true\n')
        script = f"{LITERAL}/tools/launchers/bigpemu.sh"
        assert launch(entry) == (
            atlas.AVAILABILITY_NOT_INSTALLED,
            [
                (atlas.CAVEAT_LAUNCHER_SCRIPT_MISSING, {"path": script}),
                (atlas.CAVEAT_COMMAND_REWRITTEN_AT_START, {"path": script, "rewritten": SCRIPT}),
            ],
        )

    def test_an_emulation_path_equal_to_the_literal_is_no_rewrite(self):
        [entry] = entries(self.ROW, settings=f'emulationPath="{LITERAL}"\ndoInstallESDE=true\n')
        assert [code for code, _ in launch(entry)[1]] == [atlas.CAVEAT_LAUNCHER_SCRIPT_MISSING]

    def test_a_command_without_the_literal_is_no_rewrite(self):
        [entry] = entries(f"{BASH} {SCRIPT} %ROM%", settings='emulationPath="/x"\ndoInstallESDE=true\n')
        assert [code for code, _ in launch(entry)[1]] == [atlas.CAVEAT_LAUNCHER_SCRIPT_MISSING]

    def test_an_unset_emulation_path_is_emudeck_s_default(self):
        [entry] = entries(self.ROW)
        assert entry.caveats[-1].data["rewritten"] == SCRIPT

    def test_a_row_from_another_layer_is_never_rewritten(self):
        handle = atlas.EmuDeck(HOME, FixtureMachine({SETTINGS: ""}))
        spec = EmulatorSpec("x", "row", "standalone", None, self.ROW, "es_systems.xml (AppImage-embedded)")
        note = handle._systems_path_note(spec, LITERAL, {})  # pyright: ignore[reportPrivateUsage] - the rule under test
        assert note is None


class TestTheNotesKeepTheirOrder:
    def test_a_note_goes_before_a_skipped_custom_layer(self):
        from atlas.installations import _with_note_before_custom  # pyright: ignore[reportPrivateUsage]

        reason = atlas.Caveat(atlas.CAVEAT_LAUNCHER_SCRIPT_MISSING, "", {"path": "/x"})
        assumed = atlas.Caveat(atlas.CAVEAT_LAUNCH_PATH_ASSUMED, "", {"search_path": ()})
        custom = atlas.Caveat(atlas.CAVEAT_FIND_RULES_UNREADABLE, "", {"layer": atlas.LAYER_CUSTOM})
        note = atlas.Caveat(atlas.CAVEAT_COMMAND_REWRITTEN_AT_START, "", {"path": "/x", "rewritten": "/y"})
        answer = LaunchResolution(atlas.AVAILABILITY_NOT_INSTALLED, caveats=(reason, assumed, custom))
        assert _with_note_before_custom(answer, note).caveats == (reason, assumed, note, custom)
        alone = LaunchResolution(atlas.AVAILABILITY_NOT_INSTALLED, caveats=(reason,))
        assert _with_note_before_custom(alone, note).caveats == (reason, note)


class TestTheHostViewOpensWhatBashOpens:
    """D12 of #84: a regular file once links are followed; no mode bits."""

    def test_a_file_and_a_link_to_one_are_present(self):
        host = view(FixtureMachine({"/opt/a.sh": ""}, symlinks={"/opt/b.sh": "/opt/a.sh"}))
        assert [host.regular_file(p) for p in ("/opt/a.sh", "/opt/b.sh")] == [PROBE_HIT] * 2

    def test_a_dead_link_a_directory_and_nothing_are_missing(self):
        host = view(FixtureMachine({"/opt/a.sh": ""}, symlinks={"/opt/dead.sh": "/nowhere"}, dirs=["/opt/dir"]))
        assert [host.regular_file(p) for p in ("/opt/dead.sh", "/opt/dir", "/opt/none.sh")] == [PROBE_MISS] * 3

    def test_what_cannot_be_stat_ed_relative_or_inside_the_image_cannot_be_told(self):
        host = view(FixtureMachine({}, inaccessible=["/mnt/card"]))
        paths = ("/mnt/card/a.sh", "a.sh", "%ESPATH%/a.sh")
        assert [host.regular_file(p) for p in paths] == [PROBE_UNKNOWN] * 3

    def test_emudeck_s_frontend_runs_in_the_backend_directory(self):
        handle = atlas.EmuDeck(HOME, FixtureMachine({SETTINGS: ""}))
        _, host = handle._lookup(handle._esde_snapshot("x"))  # pyright: ignore[reportPrivateUsage] - the view it builds
        assert host.working_directory == f"{HOME}/.config/EmuDeck/backend"
