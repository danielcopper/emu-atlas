"""What a RetroDECK component launcher reads from RetroDECK's start (#574): the packaged table and the live reads.

The answers are held by ``vectors/machines/launch-command.json``; the deployed
files by ``tests/test_launch_environment_tripwire.py``.
"""

from __future__ import annotations

import json
import re

import pytest

from atlas import DistributionVariable
from atlas.launch_environment import (
    SOURCE_LINE,
    SOURCE_PATHS,
    StartVariable,
    environment_rules,
    line_variable,
    load_environment_rules,
    paths_variable,
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
