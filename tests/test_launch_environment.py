"""What a RetroDECK component launcher reads from RetroDECK's start (#574): the packaged table and the live reads.

The answers are held by ``vectors/machines/launch-command.json``; the deployed
files by ``tests/test_launch_environment_tripwire.py``.
"""

from __future__ import annotations

import json
import re
import time

import pytest

from atlas import DistributionVariable
from atlas.launch_environment import (
    SOURCE_LINE,
    SOURCE_PATHS,
    StartVariable,
    environment_rules,
    is_search_list,
    line_variable,
    load_environment_rules,
    paths_variable,
    shell_references,
    shell_value,
    start_environment,
    start_line,
)

HASH = "a" * 40
ALL_VARS = "/app/libexec/all_vars.sh"
OTHER_FUNCTIONS = "/app/libexec/other_functions.sh"
XROAR_FUNCTIONS = "/app/retrodeck/components/xroar/component_functions.sh"
# A launcher's path in the table: a component directory under the deploy's tree.
LAUNCHER_PATH = re.compile(r"^/app/retrodeck/components/[a-z0-9-]+/component_launcher\.sh$")


def table(**changes: object) -> dict[str, object]:
    """The smallest table the loader takes, with *changes* applied to its one launcher."""
    launcher: dict[str, object] = {
        "launcher": "/app/retrodeck/components/x/component_launcher.sh",
        "blob": HASH,
        "reads": [{"name": "rd_shared_libs", "citation": "components/x/component_launcher.sh:7"}],
    }
    launcher.update(changes)
    return {
        "schema": 1,
        "spec": "s",
        "retrodeck": {"version": "0.10.10b", "start": "s"},
        "start_files": {ALL_VARS: HASH, OTHER_FUNCTIONS: "b" * 40},
        "variables": {
            "rd_shared_libs": {"defined_in": ALL_VARS, "from": "line"},
            "bios_path": {"defined_in": OTHER_FUNCTIONS, "from": "paths", "line": 156},
        },
        "launchers": [launcher],
    }


def _blob_of(component: str) -> str:
    rules = environment_rules()
    return next(rule.blob for rule in rules.by_blob.values() if rule.launcher.split("/")[-2] == component)


class TestThePackagedTable:
    def test_it_pins_every_launcher_of_the_release_it_was_read_at(self):
        rules = environment_rules()
        assert rules.version == "0.10.10b"
        assert len(rules.by_blob) == 23
        assert all(LAUNCHER_PATH.match(rule.launcher) for rule in rules.by_blob.values())
        assert len({rule.launcher for rule in rules.by_blob.values()}) == 23

    def test_the_start_provides_seven_variables_from_three_files(self):
        variables = environment_rules().variables
        assert {name: (v.defined_in, v.source) for name, v in variables.items()} == {
            "rd_shared_libs": (ALL_VARS, SOURCE_LINE),
            "DEFAULT_LD_LIBRARY_PATH": (ALL_VARS, SOURCE_LINE),
            "bios_path": (OTHER_FUNCTIONS, SOURCE_PATHS),
            "screenshots_path": (OTHER_FUNCTIONS, SOURCE_PATHS),
            "saves_path": (OTHER_FUNCTIONS, SOURCE_PATHS),
            "roms_path": (OTHER_FUNCTIONS, SOURCE_PATHS),
            "xroar_config": (XROAR_FUNCTIONS, SOURCE_LINE),
        }

    @pytest.mark.parametrize(
        ("component", "reads", "pins"),
        [
            ("dolphin", ["rd_shared_libs", "DEFAULT_LD_LIBRARY_PATH"], [ALL_VARS]),
            ("retroarch", ["DEFAULT_LD_LIBRARY_PATH"], [ALL_VARS]),
            ("pico-8", ["bios_path", "screenshots_path"], [OTHER_FUNCTIONS]),
            ("portmaster", ["rd_shared_libs", "roms_path"], [ALL_VARS, OTHER_FUNCTIONS]),
            ("xroar", ["rd_shared_libs", "DEFAULT_LD_LIBRARY_PATH", "xroar_config"], [ALL_VARS, XROAR_FUNCTIONS]),
            ("pcsx2", [], []),
        ],
    )
    def test_a_launcher_pins_the_start_file_of_each_variable_it_reads(
        self, component: str, reads: list[str], pins: list[str]
    ):
        rules = environment_rules()
        rule = rules.by_blob[_blob_of(component)]
        assert [read.name for read in rule.reads] == reads
        assert list(rules.pins(rule)) == pins


class TestTheLoaderRefusesWhatItCannotRead:
    @pytest.mark.parametrize(
        ("changes", "message"),
        [
            ({"blob": "abc"}, "full blob hash"),
            ({"launcher": "/opt/x/component_launcher.sh"}, "is an /app/ path"),
            ({"launcher": "/app/../x.sh"}, "is an /app/ path"),
            ({"reads": "rd_shared_libs"}, "reads is a list"),
            ({"reads": [{"name": "rd_shared_libs"}]}, "exactly name and citation"),
            ({"reads": [{"name": "ffmpeg_path", "citation": "c"}]}, "none of the variables the start defines"),
            ({"reads": [{"name": "rd_shared_libs", "citation": " "}]}, "carries a citation"),
            (
                {"reads": [{"name": "rd_shared_libs", "citation": "c"}, {"name": "rd_shared_libs", "citation": "c"}]},
                "read once",
            ),
            ({"extra": True}, "a launcher names exactly"),
        ],
    )
    def test_a_malformed_launcher_is_refused(self, changes: dict[str, object], message: str):
        text = json.dumps(table(**changes))
        with pytest.raises(ValueError, match=message):
            load_environment_rules(text)

    @pytest.mark.parametrize(
        ("key", "value", "message"),
        [
            ("schema", 2, "unsupported schema"),
            ("extra", 1, "unexpected top-level keys"),
            ("retrodeck", {"version": "0.10.10b"}, "names the version read and cites the start"),
            ("start_files", {}, "expected an object of path"),
            ("start_files", {"/etc/x.sh": HASH}, "is an /app/ path pinned by a full blob hash"),
            ("start_files", {ALL_VARS: "abc"}, "is an /app/ path pinned by a full blob hash"),
            ("variables", [], "expected an object of name"),
            ("variables", {"x": {"defined_in": ALL_VARS, "from": "env"}}, "'from' is one of"),
            ("variables", {"x": {"defined_in": ALL_VARS, "from": "line", "line": 3}}, "a line variable names exactly"),
            ("variables", {"x": {"defined_in": ALL_VARS, "from": "paths"}}, "a paths variable names exactly"),
            ("variables", {"x": {"defined_in": "/app/y.sh", "from": "line"}}, "one of the pinned start_files"),
            ("variables", {"x": {"defined_in": ALL_VARS, "from": "paths", "line": 0}}, "line is the start file's line"),
            ("variables", {"x": {"defined_in": ALL_VARS, "from": "paths", "line": True}}, "line is the start file's"),
            ("launchers", [], "non-empty 'launchers' list"),
        ],
    )
    def test_a_malformed_table_is_refused(self, key: str, value: object, message: str):
        document = table()
        document[key] = value
        text = json.dumps(document)
        with pytest.raises(ValueError, match=message):
            load_environment_rules(text)

    def test_two_launchers_under_one_blob_are_refused(self):
        document = table()
        launchers = document["launchers"]
        assert isinstance(launchers, list)
        document["launchers"] = [*launchers, *launchers]
        text = json.dumps(document)
        with pytest.raises(ValueError, match="two launchers pinned by one blob"):
            load_environment_rules(text)


class TestTheStartLineIsReadAsTheShellReadsIt:
    @pytest.mark.parametrize(
        ("text", "found"),
        [
            ('x="/app/x"\n', (1, "/app/x")),
            ("x='/app/$x'\n", (1, "/app/$x")),
            ("x=/app/x\n", (1, "/app/x")),
            ('x="/app/x"   # a comment\n', (1, "/app/x")),
            ('x="/app/x" \n', (1, "/app/x")),
            ("x=\n", (1, "")),
            ("x=${LD_LIBRARY_PATH}\n", (1, None)),
            ('x="$HOME/x"\n', (1, None)),
            ('x="$(cat f)"\n', (1, None)),
            ('x="a\\"b"\n', (1, None)),
            ("x=a b\n", (1, None)),
            ('# x="/app/x"\ny="/app/y"\n', None),
            ('  x="/app/x"\n', None),
            ('xx="/app/x"\n', None),
            ('x="/app/one"\nx="/app/two"\n', (2, "/app/two")),
        ],
    )
    def test_a_line(self, text: str, found: tuple[int, str | None] | None):
        assert start_line(text, "x") == found

    def test_a_reference_to_a_known_value_is_expanded_on_the_line(self):
        assert start_line("x=${LD_LIBRARY_PATH}\n", "x", {"LD_LIBRARY_PATH": ""}) == (1, "")


KNOWN = {"LD_LIBRARY_PATH": "/lib/a", "XDG_CONFIG_HOME": "/home/u/.var/app/a/config", "EMPTY": ""}


class TestTheExpansionReadsOnlyWhatAtlasReads:
    @pytest.mark.parametrize(
        ("rest", "value"),
        [
            ("${LD_LIBRARY_PATH}", "/lib/a"),
            ("$LD_LIBRARY_PATH", "/lib/a"),
            ("${EMPTY}", ""),
            ('"$XDG_CONFIG_HOME/xroar/xroar.conf"', "/home/u/.var/app/a/config/xroar/xroar.conf"),
            ('"${XDG_CONFIG_HOME}/x"', "/home/u/.var/app/a/config/x"),
            ("$XDG_CONFIG_HOME/x", "/home/u/.var/app/a/config/x"),
            ("/app/lib:${LD_LIBRARY_PATH}", "/app/lib:/lib/a"),
            ("\"a\"'$b'c", "a$bc"),
            ("${LD_LIBRARY_PATH}   # the default", "/lib/a"),
            ("abc#def", "abc#def"),
            ("a=b", "a=b"),
            ("", ""),
            ("  # only a comment", ""),
        ],
    )
    def test_a_value_atlas_reads(self, rest: str, value: str):
        assert shell_value(rest, KNOWN) == value

    @pytest.mark.parametrize(
        "rest",
        [
            "${HOME}",
            '"$HOME/x"',
            "$1",
            "$$",
            '"$"',
            "$(cat f)",
            "`cat f`",
            "$((1 + 2))",
            "${LD_LIBRARY_PATH:-/lib}",
            "${#LD_LIBRARY_PATH}",
            "${LD_LIBRARY_PATH",
            '"a\\"b"',
            "a\\ b",
            "~/x",
            "/lib/*",
            '"unterminated',
            "'unterminated",
            "a b",
            "a;b",
            "${LD_LIBRARY_PATH}${HOME}",
            '"$LD_LIBRARY_PATH$"',
            '"$$LD_LIBRARY_PATH"',
            '"${LD_LIBRARY_PATH:-/lib}"',
        ],
    )
    def test_anything_else_is_not_read(self, rest: str):
        assert shell_value(rest, KNOWN) is None

    @pytest.mark.parametrize(
        "rest",
        [
            '"$A' + "0$A" * 10000,
            '"$A' + "0$A" * 10000 + '"',
            '"$A' + "0$A" * 10000 + '"$(x)',
            "$A" + "0$A" * 10000 + " x",
            '"' + "${A}" * 10000 + "$",
        ],
    )
    def test_a_long_adversarial_line_is_answered_at_once(self, rest: str):
        started = time.perf_counter()
        shell_value(rest, {"A": "x"})
        shell_references(rest)
        is_search_list(rest)
        assert time.perf_counter() - started < 0.5

    @pytest.mark.parametrize(
        ("rest", "names"),
        [
            ("${LD_LIBRARY_PATH}", {"LD_LIBRARY_PATH"}),
            ('"$XDG_CONFIG_HOME/x:${HOME}"', {"XDG_CONFIG_HOME", "HOME"}),
            ("'$HOME'", set()),
            ('"/app/x"', set()),
            ("$(cat f)$HOME", set()),
            ('"$(cat f) $HOME"', set()),
        ],
    )
    def test_the_references_a_value_reads(self, rest: str, names: set[str]):
        assert shell_references(rest) == names


class TestASearchListIsAWholeElementOfTheValue:
    @pytest.mark.parametrize(
        "rest",
        [
            "${LD_LIBRARY_PATH}",
            "$LD_LIBRARY_PATH",
            '"${LD_LIBRARY_PATH}"',
            "/app/lib:${LD_LIBRARY_PATH}",
            '"$a:${LD_LIBRARY_PATH}:/x"',
            "${LD_LIBRARY_PATH}:/app/lib",
            "${LD_LIBRARY_PATH}   # the default",
        ],
    )
    def test_a_search_list(self, rest: str):
        assert is_search_list(rest)

    @pytest.mark.parametrize(
        "rest",
        [
            '"${LD_LIBRARY_PATH}/foo"',
            "/x${LD_LIBRARY_PATH}",
            "${XDG_CONFIG_HOME}${LD_LIBRARY_PATH}",
            "'${LD_LIBRARY_PATH}'",
            '"$XDG_CONFIG_HOME/x"',
            "/app/lib",
        ],
    )
    def test_no_search_list(self, rest: str):
        assert not is_search_list(rest)


class TestTheStartValuesStandUntilConfReadReplacesThem:
    START = {"XDG_CONFIG_HOME": "/home/u/.var/app/a/config", "LD_LIBRARY_PATH": ""}

    def test_a_start_file_before_conf_read_keeps_them_whatever_the_settings_hold(self):
        settings = {"paths": {"LD_LIBRARY_PATH": "/x"}, "options": {"XDG_CONFIG_HOME": "/y"}}
        assert start_environment(ALL_VARS, self.START, settings) == self.START

    @pytest.mark.parametrize("section", ["paths", "options"])
    def test_a_settings_key_of_the_same_name_replaces_one_after_conf_read(self, section: str):
        settings = {section: {"XDG_CONFIG_HOME": "/y", "bios_path": "/b"}}
        assert start_environment(XROAR_FUNCTIONS, self.START, settings) == {"LD_LIBRARY_PATH": ""}

    def test_settings_naming_neither_keep_them(self):
        settings = {"paths": {"bios_path": "/b"}, "options": {"cheevos": "true"}}
        assert start_environment(XROAR_FUNCTIONS, self.START, settings) == self.START

    @pytest.mark.parametrize("settings", [{"paths": []}, {"options": "x"}])
    def test_a_section_conf_read_does_not_read_key_by_key_replaces_them_all(self, settings: dict[str, object]):
        assert start_environment(XROAR_FUNCTIONS, self.START, settings) == {}

    def test_conf_read_exports_the_version_too(self):
        assert start_environment(XROAR_FUNCTIONS, {"version": "1"}, {}) == {}


def _exists(*paths: str):
    return lambda path: path in paths


class TestALineVariable:
    TEXT = 'a="/app/x"\nb=${LD_LIBRARY_PATH}\nc="word"\n'

    def test_a_folder_that_is_there_is_provided(self):
        assert line_variable("a", ALL_VARS, self.TEXT, _exists("/app/x")) == DistributionVariable(
            "a", "/app/x", f"{ALL_VARS}:1", True
        )

    def test_a_folder_that_is_not_there_is_not_provided(self):
        assert line_variable("a", ALL_VARS, self.TEXT, _exists()) == DistributionVariable(
            "a", "/app/x", f"{ALL_VARS}:1", False
        )

    def test_a_computed_value_is_not_stated_and_the_line_provides_it(self):
        assert line_variable("b", ALL_VARS, self.TEXT, _exists()) == DistributionVariable(
            "b", None, f"{ALL_VARS}:2", True
        )

    def test_a_value_composed_of_known_values_is_stated_and_provided_by_the_line(self):
        assert line_variable("b", ALL_VARS, self.TEXT, _exists(), {"LD_LIBRARY_PATH": ""}) == DistributionVariable(
            "b", "", f"{ALL_VARS}:2", True
        )

    def test_a_library_search_list_names_no_path_and_is_provided_by_the_line(self):
        assert line_variable("b", ALL_VARS, self.TEXT, _exists(), {"LD_LIBRARY_PATH": "/usr/lib/extra"}) == (
            DistributionVariable("b", "/usr/lib/extra", f"{ALL_VARS}:2", True)
        )

    def test_a_path_composed_from_a_search_list_is_checked_as_a_path(self):
        text = 'x="${LD_LIBRARY_PATH}/foo"\n'
        known = {"LD_LIBRARY_PATH": "/usr/lib"}
        assert line_variable("x", ALL_VARS, text, _exists(), known) == DistributionVariable(
            "x", "/usr/lib/foo", f"{ALL_VARS}:1", False
        )

    def test_an_expanded_path_is_provided_where_it_is_there(self):
        text = 'x="$XDG_CONFIG_HOME/x.conf"\n'
        known = {"XDG_CONFIG_HOME": "/home/u/config"}
        assert line_variable("x", XROAR_FUNCTIONS, text, _exists(), known) == DistributionVariable(
            "x", "/home/u/config/x.conf", f"{XROAR_FUNCTIONS}:1", False
        )
        assert line_variable("x", XROAR_FUNCTIONS, text, _exists("/home/u/config/x.conf"), known).provided

    def test_a_value_naming_no_path_is_provided_by_the_line(self):
        assert line_variable("c", ALL_VARS, self.TEXT, _exists()) == DistributionVariable(
            "c", "word", f"{ALL_VARS}:3", True
        )

    @pytest.mark.parametrize("text", [None, 'z="/app/x"\n'])
    def test_a_start_file_without_the_line_provides_nothing(self, text: str | None):
        assert line_variable("a", ALL_VARS, text, _exists("/app/x")) == DistributionVariable(
            "a", None, ALL_VARS, False
        )


class TestAPathsVariable:
    VARIABLE = StartVariable(OTHER_FUNCTIONS, SOURCE_PATHS, 156)
    SET_BY = f"{OTHER_FUNCTIONS}:156"

    def test_a_folder_that_is_there_is_provided(self):
        paths = {"bios_path": "/home/u/retrodeck/bios"}
        assert paths_variable("bios_path", self.VARIABLE, paths, _exists("/home/u/retrodeck/bios")) == (
            DistributionVariable("bios_path", "/home/u/retrodeck/bios", self.SET_BY, True)
        )

    def test_a_folder_that_is_not_there_is_stated_and_not_provided(self):
        paths = {"bios_path": "/home/u/retrodeck/bios"}
        assert paths_variable("bios_path", self.VARIABLE, paths, _exists()) == (
            DistributionVariable("bios_path", "/home/u/retrodeck/bios", self.SET_BY, False)
        )

    def test_an_empty_value_is_stated_and_not_provided(self):
        assert paths_variable("bios_path", self.VARIABLE, {"bios_path": ""}, _exists("")) == (
            DistributionVariable("bios_path", "", self.SET_BY, False)
        )

    @pytest.mark.parametrize("paths", [{}, {"bios_path": 3}, {"bios_path": None}, [], None])
    def test_no_string_under_the_key_is_no_value_and_not_provided(self, paths: object):
        assert paths_variable("bios_path", self.VARIABLE, paths, _exists()) == (
            DistributionVariable("bios_path", None, self.SET_BY, False)
        )
