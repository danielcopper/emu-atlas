"""A launch command taken apart: ES-DE's ``launchGame`` assembly and the shell's reading of it (#573).

The view is a plain mapping of what the frontend would see, as in
``test_launch.py``; the RetroDECK sandbox and the answer's shape are held by
``vectors/machines/launch-command.json``.
"""

from __future__ import annotations

import json
import random
from dataclasses import dataclass, field
from pathlib import Path

import pytest

import atlas
from atlas.cli import run
from atlas.find_rules import RULE_STATICPATH, RULE_SYSTEMPATH
from atlas.launch import PROBE_HIT, PROBE_MISS, PROBE_UNKNOWN, CommandParts, Found, Launcher, Probe, escaped_path
from atlas.launch_command import (
    COMMAND_LENGTH_LIMIT,
    INJECT_FILE_SIZE_LIMIT,
    INJECTION_LIMIT,
    RESCAN_LIMIT,
    SUBSTITUTION_LIMIT,
    INJECTION_ABSENT,
    INJECTION_EMPTY,
    INJECTION_INJECTED,
    INJECTION_OVERSIZED,
    CommandInputs,
    EnvironmentVariable,
    LaunchProgram,
    TOO_LARGE,
    TakenApart,
    TooLarge,
    _environment,  # pyright: ignore[reportPrivateUsage] - the guard is held directly
    take_apart,
)
from atlas.machine import READ_INVALID_TEXT, READ_MISSING, READ_OK, READ_UNREADABLE, ReadResult
from tests.test_machine_vectors import fixture_machine

HOME = "/home/deck/.var/app/net.retrodeck.retrodeck/config"
APP = "net.retrodeck.retrodeck"
LAUNCHER = "/app/retrodeck/components/retroarch/component_launcher.sh"
CORE = "/var/config/retroarch/cores/mgba_libretro.so"
ROMS = "/roms"
GAME = ROMS + "/gba/Game.gba"


@dataclass
class View:
    """The frontend's filesystem as data: files with their text, directories, and what cannot be told."""

    files: dict[str, ReadResult] = field(default_factory=dict)
    dirs: dict[str, tuple[str, ...]] = field(default_factory=dict)
    unknown: set[str] = field(default_factory=set)
    unplaced: set[str] = field(default_factory=set)
    sizes: dict[str, int] = field(default_factory=dict)
    reads: list[str] = field(default_factory=list)
    app_id: str | None = APP
    home: str = HOME
    search_path: tuple[str, ...] = ("/app/bin", "/usr/bin")
    es_path: str = "/app/retrodeck/components/es-de/bin"

    def rom_directory(self) -> str | None:
        return ROMS + "/"

    def found(self, path: str) -> Probe:
        if path in self.unknown:
            return PROBE_UNKNOWN
        return PROBE_HIT if path in self.files else PROBE_MISS

    def executable(self, path: str) -> Probe:
        return self.found(path)

    def listing(self, directory: str) -> tuple[str, ...] | None:
        if directory in self.unknown:
            return None
        return self.dirs.get(directory, ())

    def is_directory(self, path: str) -> Probe:
        if path in self.unknown:
            return PROBE_UNKNOWN
        return PROBE_HIT if path in self.dirs else PROBE_MISS

    def host_path(self, path: str) -> str | None:
        return None if path in self.unplaced else path

    def read_text_within(self, path: str, limit: int) -> ReadResult | TooLarge | None:
        self.reads.append(path)
        if path in self.unknown:
            return None
        held = self.files.get(path, ReadResult(READ_MISSING))
        size = self.sizes.get(path, None if held.text is None else len(held.text.encode("utf-8")))
        return TOO_LARGE if size is not None and size > limit else held


def text(content: str) -> ReadResult:
    return ReadResult(READ_OK, content)


BASH = Found(Launcher("/usr/bin/bash", None, APP, RULE_SYSTEMPATH, "bash"), "/usr/bin/bash")


def static(path: str = LAUNCHER) -> Found:
    return Found(Launcher(path, None, APP, RULE_STATICPATH, path), escaped_path(path))


def parts(found: Found | None = None, core: str | None = CORE) -> CommandParts:
    return CommandParts(found or static(), None, core)


def apart(
    command: str,
    content: str = GAME,
    view: View | None = None,
    *,
    found: Found | None = None,
    core: str | None = CORE,
    rom_directory: str | None = ROMS + "/",
) -> TakenApart:
    view = view or View()
    inputs = CommandInputs(content, parts(found, core), HOME, view.es_path, rom_directory)
    return take_apart(command, inputs, view)


def arguments(
    command: str,
    content: str = GAME,
    view: View | None = None,
    *,
    found: Found | None = None,
    core: str | None = CORE,
) -> tuple[str, ...]:
    answer = apart(command, content, view, found=found, core=core)
    assert answer.command is not None, answer.caveats
    return answer.command.arguments


def codes(answer: TakenApart) -> list[tuple[str, dict[str, object]]]:
    return [(caveat.code, dict(caveat.data)) for caveat in answer.caveats]


class TestPlainWords:
    def test_a_libretro_command_is_the_launcher_the_core_and_the_file(self):
        answer = apart("%EMULATOR_RETROARCH% -L %CORE_RETROARCH%/mgba_libretro.so %ROM%")
        assert answer.command is not None
        assert answer.command.program == LaunchProgram("flatpak", app_id=APP, command=LAUNCHER)
        assert answer.command.arguments == ("-L", CORE, GAME)
        assert answer.caveats == ()

    def test_escaped_characters_and_quotes_read_as_the_shell_reads_them(self):
        command = "%EMULATOR_X% a\\;b 'c d' \"e\\\"f\" \"g\\n\" \"\" %ROM%"
        assert arguments(command, ROMS + "/gba/My Game (USA).gba", core=None) == (
            "a;b",
            "c d",
            'e"f',
            "g\\n",
            "",
            ROMS + "/gba/My Game (USA).gba",
        )

    def test_a_quoted_core_reference_loses_its_quotes(self):
        assert arguments('%EMULATOR_X% -L "%CORE_RETROARCH%/mgba_libretro.so" %ROM%') == ("-L", CORE, GAME)

    def test_a_core_path_holding_a_space_is_escaped_and_read_back(self):
        core = "/var/config/retro arch/mgba_libretro.so"
        assert arguments("%EMULATOR_X% -L %CORE_RETROARCH%/mgba_libretro.so %ROM%", core=core) == ("-L", core, GAME)

    def test_a_systempath_hit_goes_in_unescaped(self):
        found = Found(Launcher("/usr/bin/bash", None, APP, RULE_SYSTEMPATH, "bash"), "/usr/bin/bash")
        answer = apart("%EMULATOR_OS-SHELL% %ROM%", found=found, core=None)
        assert answer.command is not None
        assert answer.command.program.command == "/usr/bin/bash"

    def test_a_replacement_command_is_command_text_split_into_words(self):
        replacement = "flatpak run --command=x net.x"
        found = Found(Launcher("/x/flag", replacement, APP, RULE_STATICPATH, "/x/flag|…"), replacement)
        answer = apart("%EMULATOR_X% %ROM%", found=found, core=None)
        assert answer.command is not None
        assert answer.command.program.command == "flatpak"
        assert answer.command.arguments == ("run", "--command=x", "net.x", GAME)

    def test_a_frontend_on_the_host_runs_a_native_program(self):
        answer = apart("%EMULATOR_X% %ROM%", view=View(app_id=None), core=None)
        assert answer.command is not None
        assert answer.command.program == LaunchProgram("native", path=LAUNCHER)

    def test_runinbackground_is_removed_with_the_leading_blanks(self):
        assert arguments("%RUNINBACKGROUND%   %EMULATOR_X% %ROM%", core=None) == (GAME,)

    def test_the_frontend_values_are_its_own_paths(self):
        command = "%EMULATOR_X% %ESPATH% %EMUDIR% %ROMPATH%/gba %GAMEDIR% %GAMEDIRRAW% %ROMRAWWIN% ~/x"
        assert arguments(command, core=None) == (
            "/app/retrodeck/components/es-de/bin",
            "/app/retrodeck/components/retroarch",
            ROMS + "/gba",
            ROMS + "/gba",
            ROMS + "/gba",
            "\\roms\\gba\\Game.gba",
            HOME + "/x",
        )

    def test_basename_and_filename_follow_es_de(self):
        assert arguments("%EMULATOR_X% %BASENAME% %FILENAME% %ROMRAW%", ROMS + "/gba/A.b.gba", core=None) == (
            "A.b",
            "A.b.gba",
            ROMS + "/gba/A.b.gba",
        )


class TestTheShellsGrammarIsRefused:
    @pytest.mark.parametrize(
        ("tail", "construct"),
        [
            ("| tee log", "pipe"),
            ("&& true", "and-or"),
            ("|| true", "and-or"),
            ("; true", "list"),
            ("& true", "background"),
            ("> log", "redirection"),
            ("< in", "redirection"),
            ("(x)", "subshell"),
            ("$HOME", "expansion"),
            ('"$HOME"', "expansion"),
            ("`id`", "substitution"),
            ("*.cfg", "glob"),
            ("a?", "glob"),
            ("[ab]", "glob"),
            ("{a,b}", "brace"),
            ("#comment", "comment"),
            ("~user", "tilde"),
            ("'open", "unterminated-quote"),
            ("end\\", "trailing-escape"),
        ],
    )
    def test_a_construct_beyond_plain_words_is_refused_with_its_name(self, tail, construct):
        # ``~`` in the command itself is ES-DE's home; a tilde reaches the
        # shell only through text spliced in later, as an injection does.
        if construct == "tilde":
            view = View(files={ROMS + "/gba/tail": text(tail)})
            answer = apart("%EMULATOR_X% %INJECT%=tail", view=view, core=None)
        else:
            answer = apart(f"%EMULATOR_X% %ROM% {tail}", core=None)
        assert answer.command is None
        assert codes(answer) == [("launch-command-shell-syntax", {"construct": construct})]

    @pytest.mark.parametrize("tail", ["$\\\nHOME", '"$\\\nHOME"', "$\\\n\\\n{x}"])
    def test_a_dollar_before_a_line_continuation_is_read_past_it(self, tail):
        answer = apart(f"%EMULATOR_X% {tail}", core=None)
        assert codes(answer) == [("launch-command-shell-syntax", {"construct": "expansion"})]

    @pytest.mark.parametrize(
        "tail", ["$", "a$", "$]", "$~", "$.", '"a$"', '"$\'x\'"', "{Beta}", "a{b}c", "{", "}", "$\\{a,b}", "'{a,b}'"]
    )
    def test_what_bash_reads_as_an_ordinary_character_stays_a_plain_word(self, tail):
        answer = apart(f"%EMULATOR_X% {tail}", core=None)
        assert answer.command is not None, answer.caveats

    @pytest.mark.parametrize(
        ("tail", "construct"),
        [
            ("$1", "expansion"),
            ("$_", "expansion"),
            ("$@", "expansion"),
            ("$$", "expansion"),
            ("${x}", "expansion"),
            ("$(x)", "expansion"),
            ("$[1+1]", "expansion"),
            ("$'x'", "expansion"),
            ('$"x"', "expansion"),
            ("{a..b}", "brace"),
            ("x{a,b}y", "brace"),
            ("{{a},b}", "brace"),
        ],
    )
    def test_what_bash_expands_after_a_dollar_or_in_braces_is_refused(self, tail, construct):
        answer = apart(f"%EMULATOR_X% {tail}", core=None)
        assert codes(answer) == [("launch-command-shell-syntax", {"construct": construct})]

    @pytest.mark.parametrize("first", ["Aé=1", "é=1", "A-B=1"])
    def test_a_first_word_with_no_ascii_name_before_its_equals_sign_is_a_plain_word(self, first):
        # bash's names are ASCII letters, digits and underscores; a word like
        # these is a command name, not an assignment.
        answer = apart(f"{first} %EMULATOR_X% %ROM%", core=None)
        assert answer.command is not None, answer.caveats
        assert answer.command.program.command == first

    @pytest.mark.parametrize(("first", "construct"), [("A=1", "assignment"), ("if", "reserved-word")])
    def test_a_first_word_the_shell_reads_as_grammar_is_refused(self, first, construct):
        answer = apart(f"{first} %EMULATOR_X% %ROM%", core=None)
        assert codes(answer) == [("launch-command-shell-syntax", {"construct": construct})]

    def test_a_quoted_assignment_or_reserved_word_is_a_plain_word(self):
        found = Found(Launcher("/x", "'A=1' if", APP, RULE_STATICPATH, "/x|…"), "'A=1' if")
        answer = apart("%EMULATOR_X%", found=found, core=None)
        assert answer.command is not None
        assert answer.command.program.command == "A=1"


class TestTheEnvironment:
    def test_env_assignments_are_the_environment_in_order(self):
        answer = apart("env B=2 A=1 %EMULATOR_X% %ROM%", core=None)
        assert answer.command is not None
        assert answer.command.environment == (EnvironmentVariable("B", "2"), EnvironmentVariable("A", "1"))
        assert answer.command.program.command == LAUNCHER
        assert answer.command.arguments == (GAME,)

    @pytest.mark.parametrize("option", ["-i", "--unset=X", "-"])
    def test_an_env_option_is_refused(self, option):
        answer = apart(f"env {option} %EMULATOR_X% %ROM%", core=None)
        assert codes(answer) == [("launch-command-env-option", {"option": option})]

    def test_an_assignment_without_a_name_is_refused(self):
        answer = apart("env =x %EMULATOR_X% %ROM%", core=None)
        assert codes(answer) == [("launch-command-env-option", {"option": "=x"})]

    def test_a_command_that_reads_as_no_words_is_refused(self):
        found = Found(Launcher("/x", "   ", APP, RULE_STATICPATH, "/x|   "), "   ")
        answer = apart("%EMULATOR_X%", found=found, core=None)
        assert codes(answer) == [("launch-command-no-program", {})]

    @pytest.mark.parametrize("words", [[], ["env"]])
    def test_too_few_words_hold_no_environment(self, words):
        assert _environment(words) == ((), 0)

    @pytest.mark.parametrize(
        "command", ['"" %EMULATOR_X% %ROM%', "'' %EMULATOR_X% %ROM%", 'env A=1 "" %EMULATOR_X%']
    )
    def test_an_empty_program_word_is_no_program(self, command):
        answer = apart(command, core=None)
        assert codes(answer) == [("launch-command-no-program", {})]

    def test_an_empty_program_word_an_injection_brings_is_no_program(self):
        view = View(files={ROMS + "/gba/one": text('""')})
        answer = apart("%INJECT%=one %EMULATOR_X% %ROM%", view=view, core=None)
        assert codes(answer) == [("launch-command-no-program", {})]

    def test_env_with_no_program_after_it_runs_env_itself(self):
        found = Found(Launcher("/x", "A=1", APP, RULE_STATICPATH, "/x|A=1"), "A=1")
        answer = apart("env %EMULATOR_X%", found=found, core=None)
        assert answer.command is not None
        assert answer.command.program.command == "env"
        assert answer.command.environment == ()
        assert answer.command.arguments == ("A=1",)


class TestAValueESDEBreaksIsAnsweredAsMeant:
    @pytest.mark.parametrize("name", ["a|b.gba", "a`b.gba", "a\tb.gba", "a\nb.gba"])
    def test_a_character_getescapedpath_leaves_alone_breaks_rom(self, name):
        answer = apart("%EMULATOR_X% %ROM% --fast", ROMS + "/gba/" + name, core=None)
        assert answer.command is not None
        assert answer.command.arguments == (ROMS + "/gba/" + name, "--fast")
        assert codes(answer) == [("launch-command-argument-broken", {"placeholder": "%ROM%"})]

    def test_a_backslash_in_a_file_name_is_turned_into_a_separator_by_es_de(self):
        content = ROMS + "/gba/a\\b.gba"
        answer = apart("%EMULATOR_X% %ROM%", content, core=None)
        assert answer.command is not None
        assert answer.command.arguments == (content,)
        assert codes(answer) == [("launch-command-argument-broken", {"placeholder": "%ROM%"})]

    def test_a_raw_value_with_a_space_splits_unquoted(self):
        content = ROMS + "/gba/My Game.gba"
        answer = apart("%EMULATOR_X% %ROMRAW% --fast", content, core=None)
        assert answer.command is not None
        assert answer.command.arguments == (content, "--fast")
        assert codes(answer) == [("launch-command-argument-broken", {"placeholder": "%ROMRAW%"})]

    def test_a_raw_value_with_a_dollar_breaks_inside_double_quotes(self):
        content = ROMS + "/gba/$x.gba"
        answer = apart('%EMULATOR_X% "%ROMRAW%"', content, core=None)
        assert answer.command is not None
        assert answer.command.arguments == (content,)
        assert codes(answer) == [("launch-command-argument-broken", {"placeholder": "%ROMRAW%"})]



    def test_filename_read_off_the_escaped_path_breaks_where_the_extension_holds_an_escaped_character(self):
        answer = apart("%EMULATOR_X% %FILENAME%", ROMS + "/gba/Game.z!p", core=None)
        assert answer.command is not None
        assert answer.command.arguments == ("Game.z!p",)
        assert codes(answer) == [("launch-command-argument-broken", {"placeholder": "%FILENAME%"})]

    def test_a_value_es_de_substitutes_as_nothing_is_answered_as_meant(self):
        # getStem reads the name through getGenericPath, which turns the
        # backslash into a separator and leaves an empty stem.
        answer = apart("%EMULATOR_X% %BASENAME% --x", ROMS + "/gba/a\\.gba", core=None)
        assert answer.command is not None
        assert answer.command.arguments == ("a\\", "--x")
        assert codes(answer) == [("launch-command-argument-broken", {"placeholder": "%BASENAME%"})]

    def test_a_value_es_de_substitutes_as_nothing_in_a_command_string_is_refused(self):
        answer = apart("%EMULATOR_SH% -c './%BASENAME%_run'", ROMS + "/ports/a\\.sh", found=BASH, core=None)
        assert codes(answer) == [("launch-command-second-shell-unsafe", {"placeholder": "%BASENAME%"})]

    def test_a_placeholder_spelled_in_a_file_name_is_replaced_by_es_de(self):
        content = ROMS + "/gba/%ROMRAW%.gba"
        answer = apart("%EMULATOR_X% %ROM%", content, core=None)
        assert answer.command is not None
        assert answer.command.arguments == (content,)
        assert codes(answer) == [("launch-command-argument-broken", {"placeholder": "%ROM%"})]


class TestASecondShell:
    @pytest.mark.parametrize("command", ['%EMULATOR_X% -c "%ROM%"', "%EMULATOR_X% -c '%ROM%'"])
    def test_an_escaped_path_a_second_shell_reads_as_itself_keeps_es_des_backslashes(self, command):
        content = ROMS + "/ports/My Game (1); [x].sh"
        answer = apart(command, content, core=None)
        assert answer.command is not None
        assert answer.command.arguments == ("-c", ROMS + "/ports/My\\ Game\\ \\(1\\)\\;\\ \\[x\\].sh")
        assert answer.caveats == ()

    @pytest.mark.parametrize(
        ("command", "name"),
        [
            *(
                ('%EMULATOR_X% -c "%ROM%"', name)
                for name in ("a|b.sh", "a$b.sh", 'a"b.sh', "a\nb.sh", "a`b`.sh", "a\tb.sh")
            ),
            *(("%EMULATOR_X% -c '%ROM%'", name) for name in ("a|b.sh", "a'b.sh", "a\nb.sh", "a`b`.sh", "a\tb.sh")),
        ],
    )
    def test_a_path_the_second_shell_would_not_read_as_itself_refuses_the_command(self, command, name):
        answer = apart(command, ROMS + "/ports/" + name, core=None)
        assert codes(answer) == [("launch-command-second-shell-unsafe", {"placeholder": "%ROM%"})]

    @pytest.mark.parametrize(
        ("command", "name"), [('%EMULATOR_X% -c "%ROM%"', "a'b.sh"), ("%EMULATOR_X% -c '%ROM%'", 'a$"b.sh')]
    )
    def test_a_quote_or_dollar_es_de_escapes_survives_the_other_quoting(self, command, name):
        # Inside double quotes \' keeps its backslash, inside single quotes \$
        # and \" do: the second shell then reads the character as itself.
        answer = apart(command, ROMS + "/ports/" + name, core=None)
        assert answer.command is not None
        assert answer.caveats == ()

    def test_ordinary_dollars_and_braces_in_a_name_reach_the_second_shell_as_themselves(self):
        content = ROMS + "/ports/Game {Beta} [!] $ (1).sh"
        answer = apart('%EMULATOR_SH% -c "%ROM%"', content, found=BASH, core=None)
        assert answer.command is not None
        assert answer.caveats == ()

    def test_a_raw_value_inside_quotes_for_a_program_that_is_no_shell_keeps_the_first_reading(self):
        content = ROMS + "/ports/a|b c.sh"
        assert arguments('%EMULATOR_X% -c "%ROMRAW%"', content, core=None) == ("-c", content)

    @pytest.mark.parametrize("name", ["a|b.sh", "My Game.sh", "a;b.sh", "a$b.sh"])
    @pytest.mark.parametrize("command", ['%EMULATOR_SH% -c "%ROMRAW%"', '%EMULATOR_SH% -e -c "%ROMRAW%" x'])
    def test_a_raw_value_a_shells_command_string_would_not_read_as_itself_refuses_the_command(self, command, name):
        answer = apart(command, ROMS + "/ports/" + name, found=BASH, core=None)
        assert codes(answer) == [("launch-command-second-shell-unsafe", {"placeholder": "%ROMRAW%"})]

    @pytest.mark.parametrize("name", ["a;b.sh", "a|b.sh", "My Game.sh", "a$b.sh"])
    @pytest.mark.parametrize(
        ("command", "placeholder"),
        [("%EMULATOR_SH% -c %ROM%", "%ROM%"), ("%EMULATOR_SH% -e -c %ROMRAW%", "%ROMRAW%")],
    )
    def test_an_unquoted_value_in_a_shells_command_string_is_read_by_the_second_shell_too(
        self, command, placeholder, name
    ):
        # Unquoted, the first shell takes ES-DE's backslashes off (a\;b
        # yields a;b), and the second shell reads what is left.
        answer = apart(command, ROMS + "/ports/" + name, found=BASH, core=None)
        assert codes(answer) == [("launch-command-second-shell-unsafe", {"placeholder": placeholder})]

    @pytest.mark.parametrize("command", ["%EMULATOR_SH% -c %ROM%", "%EMULATOR_SH% -e -c %ROMRAW%"])
    def test_an_unquoted_value_the_second_shell_reads_as_itself_is_answered(self, command):
        answer = apart(command, ROMS + "/ports/Game-1.sh", found=BASH, core=None)
        assert answer.command is not None
        assert answer.command.arguments[-1] == ROMS + "/ports/Game-1.sh"
        assert answer.caveats == ()

    def test_an_unquoted_escaped_value_outside_a_shells_command_string_keeps_the_first_reading(self):
        content = ROMS + "/ports/My Game;1.sh"
        assert arguments("%EMULATOR_SH% -c true %ROM%", content, found=BASH, core=None) == ("-c", "true", content)

    @pytest.mark.parametrize(
        "options",
        [
            "-ec",
            "-xc",
            "-c -e",
            "-c --",
            "-c -",
            "-O extglob -c",
            "-o posix -c",
            "+e -c",
            "-eo pipefail -c",
            "--norc -c",
            "--rcfile x -c",
            "-ce -o posix",
        ],
    )
    def test_the_shells_options_are_walked_to_the_command_string(self, options):
        answer = apart(f"%EMULATOR_SH% {options} %ROMRAW%", ROMS + "/ports/a;reboot.sh", found=BASH, core=None)
        assert codes(answer) == [("launch-command-second-shell-unsafe", {"placeholder": "%ROMRAW%"})]

    @pytest.mark.parametrize("options", ["-e", "-O extglob", "-- -c", "run.sh -c", "-o -c"])
    def test_a_value_where_no_command_string_stands_keeps_the_first_reading(self, options):
        # No -c, -c after the operands, or -c as the argument -o takes.
        content = ROMS + "/ports/a;reboot.sh"
        answer = apart(f"%EMULATOR_SH% {options} %ROMRAW%", content, found=BASH, core=None)
        assert answer.command is not None, answer.caveats
        assert answer.command.arguments[-1] == content

    @pytest.mark.parametrize("option", ["-1", "-e=x", "-é"])
    def test_an_option_the_walk_cannot_read_makes_every_word_a_command_string(self, option):
        answer = apart(f"%EMULATOR_SH% {option} %ROMRAW%", ROMS + "/ports/a;b.sh", found=BASH, core=None)
        assert codes(answer) == [("launch-command-second-shell-unsafe", {"placeholder": "%ROMRAW%"})]

    @pytest.mark.parametrize(
        ("command", "name"),
        [
            ("%EMULATOR_SH% -c './%BASENAME%_run'", "x$.sh"),
            ('%EMULATOR_SH% -c "cd %GAMEDIR% && ./%BASENAME%_run"', "x$.sh"),
            ("%EMULATOR_SH% -c '%BASENAME%'", "~root.sh"),
            ("%EMULATOR_SH% -c 'run>%BASENAME%'", "x.sh"),
            ("%EMULATOR_SH% -c '%BASENAME%*'", "x.sh"),
            ("%EMULATOR_SH% -c '%BASENAME%;true'", "x.sh"),
        ],
    )
    def test_a_value_the_command_string_reads_together_with_its_own_text_is_refused(self, command, name):
        answer = apart(command, ROMS + "/ports/" + name, found=BASH, core=None)
        assert codes(answer) == [("launch-command-second-shell-unsafe", {"placeholder": "%BASENAME%"})]

    @pytest.mark.parametrize(
        "command",
        [
            "%EMULATOR_SH% -c './%BASENAME%_run --x'",
            '%EMULATOR_SH% -c "cd %GAMEDIR% && ./%BASENAME%_run"',
            "%EMULATOR_SH% -c 'exec \"%ROMRAW%\"'",
        ],
    )
    def test_a_value_the_command_string_reads_literally_beside_its_own_text_is_answered(self, command):
        answer = apart(command, ROMS + "/ports/Game-1.sh", found=BASH, core=None)
        assert answer.command is not None, answer.caveats
        assert answer.caveats == ()

    @pytest.mark.parametrize("stem", ["A=b", "if", "!", "time"])
    @pytest.mark.parametrize("separator", [";", "&&", "||", "|", "&", "\\n", "("])
    def test_a_value_where_the_second_shell_expects_a_command_again_is_read_as_one(self, separator, stem):
        command = f'%EMULATOR_SH% -c "true{separator} %BASENAME% x"'.replace("\\n", "\n")
        answer = apart(command, ROMS + "/ports/" + stem + ".sh", found=BASH, core=None)
        assert codes(answer) == [("launch-command-second-shell-unsafe", {"placeholder": "%BASENAME%"})]

    @pytest.mark.parametrize(
        ("command", "stem"),
        [
            ('%EMULATOR_SH% -c "run OPT=%BASENAME%"', "~"),
            ('%EMULATOR_SH% -c "run OPT=%BASENAME%"', "~+"),
            ('%EMULATOR_SH% -c "run OPT=%BASENAME%"', "x:~"),
            ('%EMULATOR_SH% -c "run %BASENAME%"', "OPT=~"),
            ('%EMULATOR_SH% -c "run OPT=a:%BASENAME%"', "~x"),
            ('%EMULATOR_SH% -c "run OPT=%GAMEDIR%:%BASENAME%"', "~x"),
        ],
    )
    def test_bash_outside_posix_mode_tilde_expands_an_assignment_shaped_argument(self, command, stem):
        # A ~ the command itself writes is ES-DE's home before the shell sees
        # it (FileData.cpp:1164); only a value can bring one.
        answer = apart(command, ROMS + "/ports/" + stem + ".sh", found=BASH, core=None)
        assert codes(answer) == [("launch-command-second-shell-unsafe", {"placeholder": "%BASENAME%"})]

    @pytest.mark.parametrize(
        "command",
        [
            '%EMULATOR_SH% --posix -c "run OPT=%BASENAME%"',
            '%EMULATOR_SH% -o posix -c "run OPT=%BASENAME%"',
        ],
    )
    def test_a_posix_shell_leaves_an_assignment_shaped_argument_alone(self, command):
        answer = apart(command, ROMS + "/ports/~.sh", found=BASH, core=None)
        assert answer.command is not None, answer.caveats

    @pytest.mark.parametrize("stem", ["A=b", "if"])
    @pytest.mark.parametrize(
        "template",
        [
            "if true; then {} x; fi",
            "if {} x; then true; fi",
            "while true; do {} x; done",
            "if false; then true; else {} x; fi",
            "! {} x",
            "time {} x",
            "{{ {} x; }}",
            "case a in a) {} x;; esac",
            "(true) {} x",
        ],
    )
    def test_a_value_after_a_word_that_opens_a_command_list_is_read_as_a_command(self, template, stem):
        command = '%EMULATOR_SH% -c "' + template.format("%BASENAME%") + '"'
        answer = apart(command, ROMS + "/ports/" + stem + ".sh", found=BASH, core=None)
        assert codes(answer) == [("launch-command-second-shell-unsafe", {"placeholder": "%BASENAME%"})]

    @pytest.mark.parametrize(
        "template",
        # A value right against an operator is refused on its own (``{};``),
        # so the clean cases leave a blank between them.
        [
            "if true; then run {} x; fi",
            "! run {} x",
            "time run {}",
            "{{ run {} ; }}",
            "case a in a) run {} ;; esac",
            "( run {} )",
            "(true) ; run {}",
        ],
    )
    def test_a_clean_value_in_those_command_lists_is_answered(self, template):
        command = '%EMULATOR_SH% -c "' + template.format("%BASENAME%") + '"'
        answer = apart(command, ROMS + "/ports/Game-1.sh", found=BASH, core=None)
        assert answer.command is not None, answer.caveats

    @pytest.mark.parametrize(
        "command",
        [
            '%EMULATOR_SH% --posix +o posix -c "run OPT=%BASENAME%"',
            '%EMULATOR_SH% -o posix +o posix -c "run OPT=%BASENAME%"',
            '%EMULATOR_SH% --posix -c "set +o posix; run OPT=%BASENAME%"',
        ],
    )
    def test_posix_mode_switched_off_brings_the_tilde_back(self, command):
        answer = apart(command, ROMS + "/ports/~.sh", found=BASH, core=None)
        assert codes(answer) == [("launch-command-second-shell-unsafe", {"placeholder": "%BASENAME%"})]

    def test_sh_switched_out_of_posix_mode_expands_the_tilde_too(self):
        sh = Found(Launcher("/usr/bin/sh", None, APP, RULE_SYSTEMPATH, "sh"), "/usr/bin/sh")
        answer = apart('%EMULATOR_SH% +o posix -c "run OPT=%BASENAME%"', ROMS + "/ports/~.sh", found=sh, core=None)
        assert codes(answer) == [("launch-command-second-shell-unsafe", {"placeholder": "%BASENAME%"})]

    def test_sh_by_name_is_a_posix_shell(self):
        sh = Found(Launcher("/usr/bin/sh", None, APP, RULE_SYSTEMPATH, "sh"), "/usr/bin/sh")
        answer = apart('%EMULATOR_SH% -c "run OPT=%BASENAME%"', ROMS + "/ports/~.sh", found=sh, core=None)
        assert answer.command is not None, answer.caveats

    @pytest.mark.parametrize("command", ["%EMULATOR_SH% -c \"''\"", "%EMULATOR_SH% -c \"'' %ROM%\""])
    def test_an_empty_word_the_command_string_spells_itself_is_no_matter_of_its_values(self, command):
        # The second shell runs an empty command name; that is the command's
        # own text, ES-DE runs it so, and the path in it stays one literal word.
        answer = apart(command, ROMS + "/ports/Game.sh", found=BASH, core=None)
        assert answer.command is not None, answer.caveats

    def test_an_empty_word_a_value_makes_in_the_command_string_is_refused(self):
        answer = apart('%EMULATOR_SH% -c "%BASENAME%"', ROMS + "/ports/\t\t ;'.sh", found=BASH, core=None)
        assert codes(answer) == [("launch-command-second-shell-unsafe", {"placeholder": "%BASENAME%"})]

    def test_the_operating_system_shell_token_counts_as_a_shell_whatever_its_name(self):
        found = Found(Launcher("/app/bin/rd-shell", None, APP, RULE_SYSTEMPATH, "rd-shell"), "/app/bin/rd-shell")
        answer = apart('%EMULATOR_OS-SHELL% -c "%ROMRAW%"', ROMS + "/ports/a|b.sh", found=found, core=None)
        assert codes(answer) == [("launch-command-second-shell-unsafe", {"placeholder": "%ROMRAW%"})]

    @pytest.mark.parametrize(
        "command",
        [
            '%EMULATOR_SH% -c "%ROMRAW%"',
            '%EMULATOR_SH% -c true "%ROMRAW%"',
            '%EMULATOR_SH% "%ROMRAW%"',
            '%EMULATOR_SH% run.sh -c "%ROMRAW%"',
        ],
    )
    def test_a_raw_value_a_shell_reads_as_itself_or_does_not_read_is_answered(self, command):
        # The first is a command string that reads as itself; the others put
        # the path where no shell's -c reads it — after the command string, as
        # a script, or after a script whose own argument -c is.
        content = ROMS + "/ports/Game.sh" if command == '%EMULATOR_SH% -c "%ROMRAW%"' else ROMS + "/ports/a|b c.sh"
        answer = apart(command, content, found=BASH, core=None)
        assert answer.command is not None
        assert answer.command.arguments[-1] == content
        assert answer.caveats == ()


class TestTheWorkingFolder:
    def test_a_quoted_startdir_may_hold_spaces(self):
        answer = apart('%STARTDIR%="~/my dir" %EMULATOR_X% %ROM%', core=None)
        assert answer.command is not None
        folder = answer.command.working_folder
        assert folder is not None
        assert (folder.frontend_path, folder.host_path, folder.created_if_missing) == (
            HOME + "/my dir",
            HOME + "/my dir",
            True,
        )
        assert answer.command.arguments == (GAME,)

    def test_startdir_gameentrydir_is_the_file_itself(self):
        answer = apart("%STARTDIR%=%GAMEENTRYDIR% %EMULATOR_X% %ROM%", core=None)
        assert answer.command is not None
        assert answer.command.working_folder is not None
        assert answer.command.working_folder.frontend_path == GAME

    def test_an_empty_startdir_sets_no_folder(self):
        answer = apart("%STARTDIR%= %EMULATOR_X% %ROM%", core=None)
        assert answer.command is not None
        assert answer.command.working_folder is None
        assert answer.command.arguments == (GAME,)

    @pytest.mark.parametrize("command", ["%STARTDIR% %EMULATOR_X% %ROM%", '%EMULATOR_X% %ROM% %STARTDIR%="x'])
    def test_an_entry_es_de_cannot_read_is_refused(self, command):
        answer = apart(command, core=None)
        assert codes(answer) == [("launch-command-entry-invalid", {"placeholder": "%STARTDIR%"})]

    def test_a_folder_the_host_cannot_place_is_stated_with_its_path(self):
        answer = apart("%STARTDIR%=/etc/x %EMULATOR_X% %ROM%", view=View(unplaced={"/etc/x"}), core=None)
        assert answer.command is not None
        assert answer.command.working_folder is not None
        assert answer.command.working_folder.host_path is None
        assert codes(answer) == [("launch-path-unestablished", {"path": "/etc/x"})]

    def test_a_folder_es_des_escaping_breaks_is_stated_as_meant(self):
        answer = apart("%STARTDIR%=%GAMEDIR% %EMULATOR_X% %ROMRAW%", ROMS + "/a|b/Game.gba", core=None)
        assert answer.command is not None
        assert answer.command.working_folder is not None
        assert answer.command.working_folder.frontend_path == ROMS + "/a|b"
        assert ("launch-command-argument-broken", {"placeholder": "%STARTDIR%"}) in codes(answer)


class TestInjection:
    def test_lines_are_joined_without_a_separator_and_carriage_returns_dropped(self):
        view = View(files={ROMS + "/gba/Game.args": text("-a\r\n-b \n-c\n")})
        answer = apart("%EMULATOR_X% %INJECT%=%BASENAME%.args %ROM%", view=view, core=None)
        assert answer.command is not None
        assert answer.command.arguments == ("-a-b", "-c", GAME)
        assert [(i.file, i.outcome) for i in answer.command.injections] == [
            (ROMS + "/gba/Game.args", INJECTION_INJECTED)
        ]

    def test_a_quoted_absolute_entry_and_the_rom_itself(self):
        view = View(files={"/etc x/args": text("-a"), GAME: text("-r")})
        answer = apart('%EMULATOR_X% %INJECT%="/etc x/args" %INJECT%=%ROM%', view=view, core=None)
        assert answer.command is not None
        assert answer.command.arguments == ("-a", "-r")

    def test_more_than_4096_bytes_is_skipped(self):
        view = View(files={ROMS + "/gba/big": text("é" * 2049)})
        answer = apart("%EMULATOR_X% %INJECT%=big", view=view, core=None)
        assert answer.command is not None
        assert answer.command.injections[0].outcome == INJECTION_OVERSIZED
        assert answer.command.arguments == ()

    def test_exactly_4096_bytes_is_injected(self):
        view = View(files={ROMS + "/gba/big": text("é" * 2048)})
        answer = apart("%EMULATOR_X% %INJECT%=big", view=view, core=None)
        assert answer.command is not None
        assert answer.command.injections[0].outcome == INJECTION_INJECTED

    @pytest.mark.parametrize(
        ("held", "outcome"),
        [(text(""), INJECTION_EMPTY), (text("\n\r\n"), INJECTION_EMPTY), (ReadResult(READ_MISSING), INJECTION_EMPTY)],
    )
    def test_a_file_that_yields_no_text_injects_nothing(self, held, outcome):
        # A link that leads nowhere is found (ES-DE's isSymlink) and reads
        # nothing, which is the missing read.
        view = View(files={ROMS + "/gba/one": held})
        answer = apart("%EMULATOR_X% %INJECT%=one", view=view, core=None)
        assert answer.command is not None
        assert answer.command.injections[0].outcome == outcome

    def test_an_absent_file_is_named_absent(self):
        answer = apart("%EMULATOR_X% %INJECT%=one", core=None)
        assert answer.command is not None
        assert [(i.file, i.outcome) for i in answer.command.injections] == [(ROMS + "/gba/one", INJECTION_ABSENT)]

    @pytest.mark.parametrize("status", [READ_UNREADABLE, READ_INVALID_TEXT])
    def test_a_file_atlas_cannot_read_is_refused(self, status):
        view = View(files={ROMS + "/gba/one": ReadResult(status)})
        answer = apart("%EMULATOR_X% %INJECT%=one", view=view, core=None)
        assert codes(answer) == [("launch-command-inject-unreadable", {"file": ROMS + "/gba/one"})]

    def test_a_file_the_view_cannot_tell_is_unestablished(self):
        answer = apart("%EMULATOR_X% %INJECT%=one", view=View(unknown={ROMS + "/gba/one"}), core=None)
        assert codes(answer) == [("launch-path-unestablished", {"path": ROMS + "/gba/one"})]

    def test_an_injection_may_inject_another_file(self):
        view = View(files={ROMS + "/gba/one": text("%INJECT%=two"), ROMS + "/gba/two": text("-x")})
        answer = apart("%EMULATOR_X% %INJECT%=one", view=view, core=None)
        assert answer.command is not None
        assert answer.command.arguments == ("-x",)
        assert [i.outcome for i in answer.command.injections] == [INJECTION_INJECTED, INJECTION_INJECTED]

    def test_the_same_file_twice_from_the_command_is_no_loop(self):
        view = View(files={ROMS + "/gba/one": text("-x")})
        answer = apart("%EMULATOR_X% %INJECT%=one %INJECT%=one", view=view, core=None)
        assert answer.command is not None
        assert answer.command.arguments == ("-x", "-x")

    def test_a_cycle_through_two_files_is_refused(self):
        view = View(files={ROMS + "/gba/one": text("%INJECT%=two"), ROMS + "/gba/two": text("%INJECT%=one")})
        answer = apart("%EMULATOR_X% %INJECT%=one", view=view, core=None)
        assert codes(answer) == [("launch-command-inject-loop", {"file": ROMS + "/gba/one"})]

    def test_a_nul_the_injected_text_carries_is_refused(self):
        view = View(files={ROMS + "/gba/one": text("-a\0; reboot")})
        answer = apart("%EMULATOR_X% %INJECT%=one", view=view, core=None)
        assert codes(answer) == [("launch-command-shell-syntax", {"construct": "nul"})]

    def test_an_injection_is_read_by_the_shell_like_the_command(self):
        view = View(files={ROMS + "/gba/one": text("| tee x")})
        answer = apart("%EMULATOR_X% %INJECT%=one", view=view, core=None)
        assert codes(answer) == [("launch-command-shell-syntax", {"construct": "pipe"})]


class TestTheFile:
    def test_a_directory_without_a_file_of_its_name_is_launched_as_the_directory(self):
        content = ROMS + "/ps3/Game.ps3"
        view = View(dirs={content: ("PS3_GAME",)})
        assert arguments("%EMULATOR_X% %ROM% %BASENAME% %FILENAME%", content, view, core=None) == (
            content,
            "Game.ps3",
            "Game.ps3.ps3",
        )

    def test_a_directory_the_view_cannot_tell_is_unestablished(self):
        answer = apart("%EMULATOR_X% %ROM%", view=View(unknown={GAME}), core=None)
        assert codes(answer) == [("launch-path-unestablished", {"path": GAME})]

    def test_a_desktop_file_without_enableshortcuts_is_an_ordinary_file(self):
        assert arguments("%EMULATOR_X% %ROM%", ROMS + "/d/a.desktop", core=None) == (ROMS + "/d/a.desktop",)

    def test_a_desktop_file_with_enableshortcuts_is_refused(self):
        answer = apart("%ENABLESHORTCUTS% %EMULATOR_X% %ROM%", ROMS + "/d/a.desktop", core=None)
        assert codes(answer) == [("launch-command-desktop-file", {"path": ROMS + "/d/a.desktop"})]

    def test_rompath_without_a_rom_root_is_unestablished(self):
        answer = apart("%EMULATOR_X% %ROMPATH%", core=None, rom_directory=None)
        assert codes(answer) == [("launch-command-rom-root-unestablished", {})]

    def test_an_unknown_placeholder_stays_and_is_named_once(self):
        answer = apart("%EMULATOR_X% %HIDEWINDOW% %HIDEWINDOW% %EMULATOR_Y%", core=None)
        assert answer.command is not None
        assert answer.command.arguments == ("%HIDEWINDOW%", "%HIDEWINDOW%", "%EMULATOR_Y%")
        assert codes(answer) == [
            ("launch-command-placeholder-unknown", {"placeholder": "%HIDEWINDOW%"}),
            ("launch-command-placeholder-unknown", {"placeholder": "%EMULATOR_Y%"}),
        ]


class TestTheLimits:
    """What atlas follows of one command, and that it stops there rather than read or splice without end."""

    def test_an_injection_file_larger_than_atlas_reads_is_refused_unread(self):
        view = View(files={ROMS + "/gba/huge": text("-x")}, sizes={ROMS + "/gba/huge": 4 << 30})
        answer = apart("%EMULATOR_X% %INJECT%=huge", view=view, core=None)
        assert codes(answer) == [("launch-command-beyond-limits", {"limit": "inject-file-size"})]

    def test_an_injection_file_at_the_limit_is_read(self):
        view = View(files={ROMS + "/gba/big": text("-x")}, sizes={ROMS + "/gba/big": INJECT_FILE_SIZE_LIMIT})
        assert arguments("%EMULATOR_X% %INJECT%=big", view=view, core=None) == ("-x",)

    @pytest.mark.parametrize(("entries", "refused"), [(INJECTION_LIMIT - 1, False), (INJECTION_LIMIT, True)])
    def test_injections_are_followed_up_to_the_limit(self, entries, refused):
        # The command's own entry and those its injected text brings count alike.
        view = View(files={ROMS + "/gba/many": text("%INJECT%=one " * entries), ROMS + "/gba/one": text("-x")})
        answer = apart("%EMULATOR_X% %INJECT%=many", view=view, core=None)
        if refused:
            assert codes(answer) == [("launch-command-beyond-limits", {"limit": "injections"})]
        else:
            assert answer.command is not None
            assert answer.command.arguments == ("-x",) * entries

    def test_injections_that_fan_out_stop_at_the_limit(self):
        files = {ROMS + f"/gba/f{depth}": text(f"%INJECT%=f{depth + 1} " * 3) for depth in range(6)}
        answer = apart("%EMULATOR_X% %INJECT%=f0", view=View(files=files), core=None)
        assert codes(answer) == [("launch-command-beyond-limits", {"limit": "injections"})]

    def test_a_command_longer_than_atlas_reads_is_refused(self):
        answer = apart("%EMULATOR_X% " + "x" * COMMAND_LENGTH_LIMIT, core=None)
        assert codes(answer) == [("launch-command-beyond-limits", {"limit": "command-length"})]

    def test_a_splice_that_makes_the_command_too_long_is_refused(self):
        content = ROMS + "/gba/" + "g" * 4000 + ".gba"
        answer = apart("%EMULATOR_X% %ROM% %ROM% %ROM% %ROM% %ROM%", content, core=None)
        assert codes(answer) == [("launch-command-beyond-limits", {"limit": "command-length"})]

    @pytest.mark.parametrize(("count", "refused"), [(SUBSTITUTION_LIMIT - 1, False), (SUBSTITUTION_LIMIT, True)])
    def test_substitutions_are_followed_up_to_the_limit(self, count, refused):
        # The emulator's own substitution is one of them.
        answer = apart("%EMULATOR_X% " + "%ROM% " * count, core=None)
        assert (answer.command is None) == refused
        if refused:
            assert codes(answer) == [("launch-command-beyond-limits", {"limit": "substitutions"})]

    @pytest.mark.parametrize(("depth", "refused"), [(RESCAN_LIMIT - 1, False), (RESCAN_LIMIT, True)])
    def test_a_replacement_rescans_up_to_the_limit(self, depth, refused):
        # Each pass removing the flag joins the text around it into the flag
        # again, so the nesting depth is the number of passes ES-DE makes.
        nested = "%RUNIN" * depth + "%RUNINBACKGROUND%" + "BACKGROUND%" * depth
        answer = apart(nested + " %EMULATOR_X% %ROM%", core=None)
        if refused:
            assert codes(answer) == [("launch-command-beyond-limits", {"limit": "rescans"})]
        else:
            assert answer.command is not None
            assert answer.command.arguments == (GAME,)

    def test_the_quote_before_a_core_reference_is_dropped_as_often_as_es_de_rescans(self):
        # Both quotes go (the replace rescans), and with no closing quote the
        # core's replacement takes the space after it too (FileData.cpp:1535-1536).
        assert arguments('%EMULATOR_X% -L ""%CORE_RETROARCH%/mgba_libretro.so %ROM%') == ("-L", CORE + GAME)


class TestNoExceptionEscapes:
    """Every command and every name is answered or refused — taking a command apart never raises."""

    _TOKENS = (
        "%EMULATOR_X%", "%ROM%", "%ROMRAW%", "%BASENAME%", "%FILENAME%", "%GAMEDIR%", "%GAMEDIRRAW%", "%ROMPATH%",
        "%ESPATH%", "%EMUDIR%", "%STARTDIR%=", "%INJECT%=", "%INJECT%=inj", "%CORE_RETROARCH%/m.so", '"%CORE_',
        "%RUNINBACKGROUND%", "%ENABLESHORTCUTS%", "env", "A=1", "-c", "-ec", "-o", "--", "-", "~", '"', "'", "\\",
        " ", "\t", "\n", ";", "|", "&", "(", ")", "<", ">", "$", "${", "`", "*", "?", "[", "{", "}", ",", "..", "#",
        "=", ":", "x", "", "\0", "%", "%X%",
    )
    _NAME = "a b;|&$`'\"\\\t\n*?[]{}~=:#!()<>%.é"

    def test_random_commands_and_names_are_answered_or_refused(self):
        rng = random.Random(573)
        programs = (None, BASH, Found(Launcher("/x", "   ", APP, RULE_STATICPATH, "/x|   "), "   "))
        for _ in range(3000):
            command = rng.choice(("", "%EMULATOR_X% ", "%EMULATOR_SH% -c ")) + "".join(
                rng.choice(self._TOKENS) for _ in range(rng.randint(0, 12))
            )
            name = "".join(rng.choice(self._NAME) for _ in range(rng.randint(1, 6)))
            injected = "".join(rng.choice(self._TOKENS) for _ in range(rng.randint(0, 6)))
            answer = apart(
                command,
                ROMS + "/gba/" + name + rng.choice((".gba", "", ".desktop")),
                View(files={ROMS + "/gba/inj": text(injected)}),
                found=rng.choice(programs),
                core=rng.choice((None, "/c/m.so", "/c d/m.so")),
                rom_directory=rng.choice((ROMS + "/", None)),
            )
            assert answer.command is not None or answer.caveats, (command, name)


class TestTheProgram:
    @pytest.mark.parametrize(
        "kwargs",
        [
            {"variant": "flatpak", "app_id": APP},
            {"variant": "flatpak", "app_id": APP, "command": "x", "path": "/x"},
            {"variant": "native"},
            {"variant": "appimage", "path": "/x", "app_id": APP},
            {"variant": "snap", "path": "/x"},
        ],
    )
    def test_a_program_carries_exactly_its_variants_keys(self, kwargs):
        with pytest.raises(ValueError, match="LaunchProgram"):
            LaunchProgram(**kwargs)


def _vector_machine():
    """The first launch-command vector's machine, detected — a RetroDECK whose gba entry is startable."""
    document = json.loads((Path(__file__).resolve().parents[1] / "vectors/machines/launch-command.json").read_text())
    inp = document["vectors"][0]["input"]
    return inp, fixture_machine(inp)


class TestTheQuestion:
    def test_a_relative_content_path_raises_on_the_handle_and_the_aggregate(self):
        inp, machine = _vector_machine()
        retrodeck = atlas.detect(inp["home"], machine)[0]
        everywhere = atlas.EveryInstallation(())
        with pytest.raises(ValueError, match="content_path must be an absolute path"):
            retrodeck.launch_command("gba", "roms/Game.gba")
        with pytest.raises(ValueError, match="content_path must be an absolute path"):
            everywhere.launch_command("gba", "roms/Game.gba")

    def test_the_cli_refuses_a_relative_content_path_as_a_usage_error(self, capsys):
        inp, machine = _vector_machine()
        with pytest.raises(SystemExit) as stopped:
            run(["launch-command", "--system", "gba", "--content", "roms/Game.gba"], home=inp["home"], machine=machine)
        assert stopped.value.code == 2
        assert "must be an absolute path" in capsys.readouterr().err


def _mame_startdir_vectors():
    """The launch-command vectors whose answer runs a MAME command from a working folder."""
    document = json.loads((Path(__file__).resolve().parents[1] / "vectors/machines/launch-command.json").read_text())
    return [
        vector
        for vector in document["vectors"]
        if (answer := vector["expected"].get("launch_command")) is not None
        and answer["command"] is not None
        and answer["command"]["working_folder"] is not None
        and answer["entry"]["emulator"] == "MAME"
    ]


class TestTheWorkingFolderIsTheStatesStartFolder:
    """The folder the command runs in and the one MAME's states answer opens a relative root from are one (#607).

    Both are the frontend's ``%STARTDIR%`` with its ``~`` expanded against the
    frontend's home; held on the same machines, so the two readings cannot
    drift apart again.
    """

    def test_there_are_machines_to_hold_it_on(self):
        assert _mame_startdir_vectors()

    @pytest.mark.parametrize("vector", _mame_startdir_vectors(), ids=lambda vector: vector["name"])
    def test_the_states_open_below_the_working_folder(self, vector):
        inp = vector["input"]
        query = inp["launch_command_query"]
        install = atlas.detect(inp["home"], fixture_machine(inp))[0]
        answer = install.launch_command(query["system"], query["content_path"], label=query.get("label"))
        assert answer.command is not None
        assert answer.command.working_folder is not None
        assert answer.entry is not None
        folder = answer.command.working_folder.host_path
        placement = answer.entry.savestate_location(content_path=query["content_path"])
        assert isinstance(placement, atlas.SavestatePlacement)
        # No mame.ini on these machines: the compiled relative 'sta' governs,
        # below the folder the launch changes into.
        assert placement.dir == f"{folder}/sta/{placement.dir.rsplit('/', 1)[1]}"
