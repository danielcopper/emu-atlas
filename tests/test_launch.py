"""ES-DE's launch lookup mirrored over a view of the frontend's filesystem (``FileData.cpp`` @ v3.4.1).

The view here is a plain mapping of what the frontend would see, so every rule
of the lookup is held without a machine: the RetroDECK sandbox view itself is
held in ``test_installations.py`` and by the vectors.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pytest

import atlas
from atlas.find_rules import EmulatorRules, FindRules, merge_find_rules
from atlas.launch import (
    PROBE_HIT,
    PROBE_MISS,
    PROBE_UNKNOWN,
    LaunchLookup,
    LayeredFindRules,
    Probe,
    generic_path,
)

HOME = "/home/deck/.var/app/net.retrodeck.retrodeck/config"
APP = "net.retrodeck.retrodeck"
EMULATOR_TOKEN = "emulator_token"
CORE_TOKEN = "core_token"
LAUNCHER = "/app/retrodeck/components/dolphin/component_launcher.sh"


@dataclass
class View:
    """The frontend's filesystem as data: files (with links), directories, and what cannot be told."""

    files: set[str] = field(default_factory=set)
    links: set[str] = field(default_factory=set)
    dirs: dict[str, tuple[str, ...]] = field(default_factory=dict)
    unknown: set[str] = field(default_factory=set)
    rom: str | None = "/roms/"
    app_id: str | None = APP
    home: str = HOME
    search_path: tuple[str, ...] = ("/app/bin", "/usr/bin")
    es_path: str = "/app/retrodeck/components/es-de/bin"
    asked: list[str] = field(default_factory=list)

    def rom_directory(self) -> str | None:
        return self.rom

    def found(self, path: str) -> Probe:
        self.asked.append(path)
        if path in self.unknown:
            return PROBE_UNKNOWN
        return PROBE_HIT if path in self.files or path in self.links else PROBE_MISS

    def executable(self, path: str) -> Probe:
        if path in self.unknown:
            return PROBE_UNKNOWN
        return PROBE_HIT if path in self.files else PROBE_MISS

    def listing(self, directory: str) -> tuple[str, ...] | None:
        if directory in self.unknown:
            return None
        return self.dirs.get(directory, ())


def layered(
    emulators: dict[str, EmulatorRules],
    cores: dict[str, tuple[str, ...]] | None = None,
    *,
    shipped: FindRules | None = None,
    custom: FindRules | None = None,
    bundled_read: bool = True,
    custom_unreadable: str | None = None,
) -> LayeredFindRules:
    bundled = FindRules(emulators=emulators, cores=cores or {})
    custom = custom or FindRules(emulators={}, cores={})
    return LayeredFindRules(
        merge_find_rules((custom, bundled if bundled_read else FindRules(emulators={}, cores={}))),
        frozenset(custom.emulators),
        frozenset(custom.cores),
        bundled_read,
        custom_unreadable,
        shipped,
    )


def resolve(rules: LayeredFindRules, view: View, command: str, *, loads_core: bool = False):
    return LaunchLookup(rules, view).resolve(command, loads_core=loads_core)


def codes(answer) -> list[str]:
    return [c.code for c in answer.caveats]


DOLPHIN = {"DOLPHIN": EmulatorRules(("dolphin-emu",), ("~/Applications/Dolphin*.AppImage", LAUNCHER))}
STANDALONE = "%EMULATOR_DOLPHIN% -b -e %ROM%"


class TestTheEmulatorIsFoundTheWayESDEFindsIt:
    def test_a_staticpath_hit_is_the_launcher_in_the_frontends_spelling(self):
        answer = resolve(layered(DOLPHIN), View(files={LAUNCHER}), STANDALONE)
        assert answer.availability == atlas.AVAILABILITY_STARTABLE
        assert answer.launcher == atlas.Launcher(LAUNCHER, None, APP, "staticpath", LAUNCHER)
        assert answer.core_path is None
        assert answer.caveats == ()

    def test_every_systempath_entry_is_tried_before_any_staticpath_entry(self):
        view = View(files={LAUNCHER, "/usr/bin/dolphin-emu"})
        answer = resolve(layered(DOLPHIN), view, STANDALONE)
        assert answer.launcher is not None
        assert (answer.launcher.path, answer.launcher.rule, answer.launcher.entry) == (
            "/usr/bin/dolphin-emu",
            "systempath",
            "dolphin-emu",
        )
        assert view.asked[:2] == ["/app/bin/dolphin-emu", "/usr/bin/dolphin-emu"]

    def test_tilde_is_the_frontends_home_and_a_glob_takes_the_first_sorted_match(self):
        directory = f"{HOME}/Applications"
        view = View(
            files={f"{directory}/Dolphin-b.AppImage", f"{directory}/Dolphin-a.AppImage"},
            dirs={directory: ("Dolphin-b.AppImage", "Dolphin-a.AppImage", "Other.AppImage")},
        )
        answer = resolve(layered(DOLPHIN), view, STANDALONE)
        assert answer.launcher is not None
        assert answer.launcher.path == f"{directory}/Dolphin-a.AppImage"
        assert answer.launcher.entry == "~/Applications/Dolphin*.AppImage"

    def test_a_glob_whose_first_match_is_no_file_is_a_miss_even_with_a_later_file(self):
        directory = f"{HOME}/Applications"
        view = View(files={f"{directory}/Dolphin-b.AppImage"}, dirs={directory: ("Dolphin-a.AppImage", "Dolphin-b.AppImage")})
        answer = resolve(layered(DOLPHIN), view, STANDALONE)
        assert answer.availability == atlas.AVAILABILITY_NOT_INSTALLED

    def test_a_dot_in_a_glob_is_a_regular_expression_wildcard(self):
        rules = layered({"X": EmulatorRules(static_paths=("/opt/x.y*",))})
        view = View(files={"/opt/xzy-1"}, dirs={"/opt": ("xzy-1",)})
        answer = resolve(rules, view, "%EMULATOR_X% %ROM%")
        assert answer.launcher is not None
        assert answer.launcher.path == "/opt/xzy-1"

    def test_a_wildcard_above_the_last_component_matches_nothing(self):
        # The parent spelled with a literal '*' lists a file the last
        # component's wildcard would match — ES-DE never lists it.
        rules = layered({"X": EmulatorRules(static_paths=("/opt/*/x*",))})
        view = View(files={"/opt/*/xy"}, dirs={"/opt/*": ("xy",)})
        assert resolve(rules, view, "%EMULATOR_X% %ROM%").availability == atlas.AVAILABILITY_NOT_INSTALLED

    def test_parentheses_and_brackets_in_a_glob_are_literal(self):
        rules = layered({"X": EmulatorRules(static_paths=("/opt/x (1)[a]*",))})
        view = View(files={"/opt/x (1)[a]-z"}, dirs={"/opt": ("x (1)[a]-z",)})
        answer = resolve(rules, view, "%EMULATOR_X% %ROM%")
        assert answer.launcher is not None
        assert answer.launcher.path == "/opt/x (1)[a]-z"

    def test_a_symlink_counts_whatever_it_points_at(self):
        view = View(links={LAUNCHER})
        assert resolve(layered(DOLPHIN), view, STANDALONE).availability == atlas.AVAILABILITY_STARTABLE

    def test_a_pipe_entry_carries_its_replacement_and_the_file_that_selected_it(self):
        entry = "/var/lib/flatpak/exports/bin/net.sf.VICE|flatpak run --command=xvic net.sf.VICE"
        rules = layered({"VICE": EmulatorRules(static_paths=(entry,))})
        view = View(files={"/var/lib/flatpak/exports/bin/net.sf.VICE"})
        answer = resolve(rules, view, "%EMULATOR_VICE% %ROM%")
        assert answer.launcher == atlas.Launcher(
            "/var/lib/flatpak/exports/bin/net.sf.VICE",
            "flatpak run --command=xvic net.sf.VICE",
            APP,
            "staticpath",
            entry,
        )

    def test_a_pipe_entry_with_nothing_after_it_runs_the_file_it_found(self):
        # FileData.cpp:2581-2583: an empty replacement is no replacement, and
        # ES-DE substitutes the found path, escaped, as without a |.
        entry = "/x/My Emu|"
        rules = layered({"X": EmulatorRules(static_paths=(entry,))})
        view = View(files={"/x/My Emu"})
        lookup = LaunchLookup(rules, view)
        answer = lookup.resolve("%EMULATOR_X% %ROM%", loads_core=False)
        assert answer.launcher == atlas.Launcher("/x/My Emu", None, APP, "staticpath", entry)
        parts = lookup.command_parts("%EMULATOR_X% %ROM%", loads_core=False)
        assert parts is not None
        assert parts.emulator.substituted == "/x/My\\ Emu"

    def test_espath_and_rompath_expand_and_the_path_is_normalized(self):
        rules = layered({"X": EmulatorRules(static_paths=("%ESPATH%/x", "%ROMPATH%//tools/y/"))})
        view = View(files={"/roms///tools/y/"}, rom="/roms/")
        answer = resolve(rules, view, "%EMULATOR_X% %ROM%")
        assert view.asked[0] == "/app/retrodeck/components/es-de/bin/x"
        assert answer.launcher is not None
        assert answer.launcher.path == "/roms/tools/y"

    def test_a_rompath_nobody_can_resolve_stops_the_walk(self):
        rules = layered({"X": EmulatorRules(static_paths=("%ROMPATH%/x", "/later"))})
        answer = resolve(rules, View(files={"/later"}, rom=None), "%EMULATOR_X% %ROM%")
        assert answer.availability == atlas.AVAILABILITY_UNESTABLISHED
        assert codes(answer) == [atlas.CAVEAT_LAUNCH_PATH_UNESTABLISHED]


class TestEveryOtherVerdictCarriesExactlyOneReason:
    def test_nothing_found_is_not_installed_with_the_entries_searched(self):
        answer = resolve(layered(DOLPHIN), View(), STANDALONE)
        assert answer.availability == atlas.AVAILABILITY_NOT_INSTALLED
        assert answer.launcher is None
        (reason,) = answer.caveats
        assert reason.code == atlas.CAVEAT_EMULATOR_NOT_FOUND
        assert reason.data == {
            EMULATOR_TOKEN: "DOLPHIN",
            "searched": ("dolphin-emu", "~/Applications/Dolphin*.AppImage", LAUNCHER),
        }

    def test_no_rules_for_the_token_is_unestablished(self):
        answer = resolve(layered(DOLPHIN), View(), "%EMULATOR_CEMU% %ROM%")
        assert answer.availability == atlas.AVAILABILITY_UNESTABLISHED
        assert [(c.code, dict(c.data)) for c in answer.caveats] == [
            (atlas.CAVEAT_EMULATOR_RULES_MISSING, {EMULATOR_TOKEN: "CEMU"})
        ]

    def test_an_empty_definition_is_no_rules(self):
        answer = resolve(layered({"DOLPHIN": EmulatorRules()}), View(), STANDALONE)
        assert codes(answer) == [atlas.CAVEAT_EMULATOR_RULES_MISSING]

    def test_a_probe_that_cannot_tell_stops_the_walk_before_a_later_hit(self):
        view = View(files={LAUNCHER}, unknown={"/usr/bin/dolphin-emu"})
        answer = resolve(layered(DOLPHIN), view, STANDALONE)
        assert answer.availability == atlas.AVAILABILITY_UNESTABLISHED
        assert [(c.code, dict(c.data)) for c in answer.caveats] == [
            (atlas.CAVEAT_LAUNCH_PATH_UNESTABLISHED, {"path": "/usr/bin/dolphin-emu"})
        ]

    def test_a_listing_that_cannot_be_told_stops_the_walk(self):
        view = View(files={LAUNCHER}, unknown={f"{HOME}/Applications"})
        answer = resolve(layered(DOLPHIN), view, STANDALONE)
        assert [(c.code, dict(c.data)) for c in answer.caveats] == [
            (atlas.CAVEAT_LAUNCH_PATH_UNESTABLISHED, {"path": f"{HOME}/Applications"})
        ]

    def test_an_unread_bundled_layer_leaves_a_name_only_the_custom_layer_can_answer(self):
        custom = FindRules(emulators={"X": EmulatorRules(static_paths=("/x",))}, cores={})
        rules = layered({}, custom=custom, bundled_read=False)
        assert resolve(rules, View(files={"/x"}), "%EMULATOR_X% %ROM%").availability == atlas.AVAILABILITY_STARTABLE
        unread = resolve(rules, View(), STANDALONE)
        assert unread.availability == atlas.AVAILABILITY_UNESTABLISHED
        assert [(c.code, dict(c.data)) for c in unread.caveats] == [
            (atlas.CAVEAT_FIND_RULES_UNREADABLE, {"layer": atlas.LAYER_BUNDLED})
        ]

    def test_a_skipped_custom_layer_is_a_note_beside_any_verdict(self):
        rules = layered(DOLPHIN, custom_unreadable=f"{HOME}/ES-DE/custom_systems/es_find_rules.xml")
        found = resolve(rules, View(files={LAUNCHER}), STANDALONE)
        missing = resolve(rules, View(), STANDALONE)
        assert found.availability == atlas.AVAILABILITY_STARTABLE
        assert [(c.code, dict(c.data)) for c in found.caveats] == [
            (atlas.CAVEAT_FIND_RULES_UNREADABLE, {"layer": atlas.LAYER_CUSTOM})
        ]
        assert codes(missing) == [atlas.CAVEAT_EMULATOR_NOT_FOUND, atlas.CAVEAT_FIND_RULES_UNREADABLE]

    def test_a_command_without_an_emulator_token_is_not_evaluated(self):
        for command in ("/usr/bin/foo %ROM%", "%EMULATOR_% %ROM%", "%EMULATOR_X -b"):
            answer = resolve(layered(DOLPHIN), View(), command)
            assert [(c.code, dict(c.data)) for c in answer.caveats] == [
                (atlas.CAVEAT_LAUNCH_RESOLUTION_UNSUPPORTED, {})
            ], command

    def test_a_command_with_emupath_is_not_evaluated(self):
        answer = resolve(layered(DOLPHIN), View(files={LAUNCHER}), "%EMULATOR_DOLPHIN% %EMUPATH%/x %ROM%")
        assert codes(answer) == [atlas.CAVEAT_LAUNCH_RESOLUTION_UNSUPPORTED]

    def test_a_precommand_that_is_not_found_refuses_like_the_emulator(self):
        rules = layered({**DOLPHIN, "GAMESCOPE": EmulatorRules(("gamescope",))})
        answer = resolve(rules, View(files={LAUNCHER}), "%PRECOMMAND_GAMESCOPE% %EMULATOR_DOLPHIN% %ROM%")
        assert [(c.code, c.data[EMULATOR_TOKEN]) for c in answer.caveats] == [
            (atlas.CAVEAT_EMULATOR_NOT_FOUND, "GAMESCOPE")
        ]


RETROARCH = {"RETROARCH": EmulatorRules(static_paths=("/app/retrodeck/components/retroarch/component_launcher.sh",))}
RA_FILE = "/app/retrodeck/components/retroarch/component_launcher.sh"
LIBRETRO = "%EMULATOR_RETROARCH% -L %CORE_RETROARCH%/dolphin_libretro.so %ROM%"
CORES = {"RETROARCH": ("/var/config/retroarch/cores", "~/.config/retroarch/cores")}


class TestTheCoreIsFoundThroughTheCorepathRules:
    def test_the_first_corepath_holding_the_file_is_the_core(self):
        view = View(files={RA_FILE, f"{HOME}/.config/retroarch/cores/dolphin_libretro.so"})
        answer = resolve(layered(RETROARCH, CORES), view, LIBRETRO, loads_core=True)
        assert answer.availability == atlas.AVAILABILITY_STARTABLE
        assert answer.core_path == f"{HOME}/.config/retroarch/cores/dolphin_libretro.so"

    def test_a_quoted_core_reference_ends_at_the_quote(self):
        command = '%EMULATOR_RETROARCH% -L "%CORE_RETROARCH%/dolphin_libretro.so" %ROM%'
        view = View(files={RA_FILE, "/var/config/retroarch/cores/dolphin_libretro.so"})
        answer = resolve(layered(RETROARCH, CORES), view, command, loads_core=True)
        assert answer.core_path == "/var/config/retroarch/cores/dolphin_libretro.so"

    def test_no_corepath_holding_it_is_not_installed(self):
        answer = resolve(layered(RETROARCH, CORES), View(files={RA_FILE}), LIBRETRO, loads_core=True)
        assert answer.availability == atlas.AVAILABILITY_NOT_INSTALLED
        assert answer.launcher is None
        assert answer.core_path is None
        assert [(c.code, dict(c.data)) for c in answer.caveats] == [
            (
                atlas.CAVEAT_CORE_NOT_INSTALLED,
                {"core_so": "dolphin_libretro.so", "searched": CORES["RETROARCH"]},
            )
        ]

    def test_no_corepath_rules_for_the_core_token_is_unestablished(self):
        custom = FindRules(emulators={}, cores={"RETROARCH": ()})
        answer = resolve(layered(RETROARCH, CORES, custom=custom), View(files={RA_FILE}), LIBRETRO, loads_core=True)
        assert answer.availability == atlas.AVAILABILITY_UNESTABLISHED
        assert [(c.code, dict(c.data)) for c in answer.caveats] == [
            (atlas.CAVEAT_CORE_RULES_MISSING, {CORE_TOKEN: "RETROARCH"})
        ]

    def test_the_emulator_is_looked_for_first(self):
        answer = resolve(layered(RETROARCH, CORES), View(), LIBRETRO, loads_core=True)
        assert codes(answer) == [atlas.CAVEAT_EMULATOR_NOT_FOUND]

    def test_emupath_in_a_corepath_is_the_launchers_directory(self):
        rules = layered(RETROARCH, {"RETROARCH": ("%EMUPATH%/cores",)})
        view = View(files={RA_FILE, "/app/retrodeck/components/retroarch/cores/dolphin_libretro.so"})
        assert resolve(rules, view, LIBRETRO, loads_core=True).core_path == (
            "/app/retrodeck/components/retroarch/cores/dolphin_libretro.so"
        )

    def test_a_libretro_command_naming_its_core_by_path_is_not_evaluated(self):
        command = "%EMULATOR_RETROARCH% -L /cores/dolphin_libretro.so %ROM%"
        answer = resolve(layered(RETROARCH, CORES), View(files={RA_FILE}), command, loads_core=True)
        assert codes(answer) == [atlas.CAVEAT_LAUNCH_RESOLUTION_UNSUPPORTED]


class TestRunGameIsComparedNotFollowed:
    def shipped(self, rules: dict[str, EmulatorRules], repeated: frozenset[str] = frozenset()) -> FindRules:
        return FindRules(emulators=rules, cores={}, repeated_emulators=repeated)

    def note(self, answer) -> list[object]:
        return [c.data["run_game_path"] for c in answer.caveats if c.code == atlas.CAVEAT_RUN_GAME_DIFFERS]

    def test_the_same_pick_states_nothing(self):
        rules = layered(DOLPHIN, shipped=self.shipped(DOLPHIN))
        assert self.note(resolve(rules, View(files={LAUNCHER}), STANDALONE)) == []

    def test_a_tilde_entry_es_de_takes_is_one_run_game_never_reads(self):
        directory = f"{HOME}/Applications"
        view = View(files={LAUNCHER, f"{directory}/Dolphin.AppImage"}, dirs={directory: ("Dolphin.AppImage",)})
        answer = resolve(layered(DOLPHIN, shipped=self.shipped(DOLPHIN)), view, STANDALONE)
        assert answer.availability == atlas.AVAILABILITY_STARTABLE
        assert self.note(answer) == [LAUNCHER]

    def test_run_game_never_expands_tilde(self):
        rules = {"DOLPHIN": EmulatorRules(static_paths=("~/bin/dolphin", LAUNCHER))}
        view = View(files={LAUNCHER, f"{HOME}/bin/dolphin"})
        answer = resolve(layered(rules, shipped=self.shipped(rules)), view, STANDALONE)
        assert answer.launcher is not None
        assert answer.launcher.path == f"{HOME}/bin/dolphin"
        assert self.note(answer) == [LAUNCHER]

    def test_a_custom_definition_run_game_never_reads(self):
        custom = FindRules(emulators={"DOLPHIN": EmulatorRules(static_paths=("/custom/dolphin",))}, cores={})
        rules = layered(DOLPHIN, custom=custom, shipped=self.shipped(DOLPHIN))
        assert self.note(resolve(rules, View(files={LAUNCHER, "/custom/dolphin"}), STANDALONE)) == [LAUNCHER]

    def test_a_pipe_entry_always_differs(self):
        entry = "/var/lib/flatpak/exports/bin/x|flatpak run x"
        rules = {"X": EmulatorRules(static_paths=(entry,))}
        answer = resolve(layered(rules, shipped=self.shipped(rules)), View(files={"/var/lib/flatpak/exports/bin/x"}), "%EMULATOR_X% %ROM%")
        assert self.note(answer) == [""]

    def test_a_token_outside_run_games_pattern_finds_nothing_there(self):
        rules = {"DOSBOX-X": EmulatorRules(static_paths=("/x",))}
        answer = resolve(layered(rules, shipped=self.shipped(rules)), View(files={"/x"}), "%EMULATOR_DOSBOX-X% %ROM%")
        assert self.note(answer) == [""]

    def test_os_shell_is_substituted_without_a_search(self):
        rules = {"OS-SHELL": EmulatorRules(("bash",))}
        answer = resolve(layered(rules, shipped=self.shipped(rules)), View(files={"/usr/bin/bash"}), "%EMULATOR_OS-SHELL% %ROM%")
        assert self.note(answer) == ["/bin/sh"]

    def test_a_repeated_name_is_one_run_games_xmllint_refuses(self):
        rules = layered(DOLPHIN, shipped=self.shipped(DOLPHIN, frozenset({"DOLPHIN"})))
        assert self.note(resolve(rules, View(files={LAUNCHER}), STANDALONE)) == [""]

    def test_an_entry_over_several_lines_is_one_run_game_cannot_read(self):
        # Even where a file of that very name exists: run_game.sh reads the
        # entry line by line and never sees it whole.
        spread = "/app/x\n"
        shipped = {"DOLPHIN": EmulatorRules(static_paths=(spread,))}
        rules = layered(DOLPHIN, shipped=self.shipped(shipped))
        assert self.note(resolve(rules, View(files={LAUNCHER, spread}), STANDALONE)) == [""]

    def test_a_systempath_entry_is_resolved_on_path_for_the_comparison(self):
        rules = {"X": EmulatorRules(("x",))}
        answer = resolve(layered(rules, shipped=self.shipped(rules)), View(files={"/usr/bin/x"}), "%EMULATOR_X% %ROM%")
        assert self.note(answer) == []

    def test_a_probe_run_game_cannot_be_told_about_states_nothing(self):
        directory = f"{HOME}/Applications"
        view = View(files={f"{directory}/Dolphin.AppImage"}, dirs={directory: ("Dolphin.AppImage",)}, unknown={LAUNCHER})
        view.files.add(LAUNCHER)
        answer = resolve(layered(DOLPHIN, shipped=self.shipped(DOLPHIN)), view, STANDALONE)
        assert self.note(answer) == []


class TestTheSpellingsAreESDEs:
    def test_generic_path_collapses_and_trims(self):
        assert generic_path("/a//b///c/") == "/a/b/c"
        assert generic_path("/") == "/"

    def test_one_search_per_token_per_answer(self):
        view = View(files={RA_FILE, "/var/config/retroarch/cores/a_libretro.so", "/var/config/retroarch/cores/b_libretro.so"})
        lookup = LaunchLookup(layered(RETROARCH, CORES), view)
        for core in ("a", "b"):
            command = f"%EMULATOR_RETROARCH% -L %CORE_RETROARCH%/{core}_libretro.so %ROM%"
            assert lookup.resolve(command, loads_core=True).availability == atlas.AVAILABILITY_STARTABLE
        assert view.asked.count(RA_FILE) == 1


class TestTheAnswerTypesRefuseWhatTheyCannotMean:
    def test_a_launcher_rule_outside_the_vocabulary_is_refused(self):
        with pytest.raises(ValueError, match="rule must be one of"):
            atlas.Launcher("/x", None, None, "corepath", "/x")  # pyright: ignore[reportArgumentType] - the refused word

    def test_a_verdict_outside_the_vocabulary_or_a_launcher_beside_another_verdict_is_refused(self):
        from atlas.launch import LaunchResolution

        with pytest.raises(ValueError, match="availability must be one of"):
            LaunchResolution("needs-setup")  # pyright: ignore[reportArgumentType] - the refused word
        with pytest.raises(ValueError, match="exactly where the verdict is startable"):
            LaunchResolution(atlas.AVAILABILITY_STARTABLE)
        launcher = atlas.Launcher("/x", None, None, "staticpath", "/x")
        with pytest.raises(ValueError, match="exactly where the verdict is startable"):
            LaunchResolution(atlas.AVAILABILITY_NOT_INSTALLED, launcher)
