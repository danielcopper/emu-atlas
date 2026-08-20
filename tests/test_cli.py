"""Hold the CLI to the vector corpus — its stdout is the contract, byte for byte.

The CLI promises no dialect of its own: what it prints for a question is the
same contract JSON the machines vectors pin. This runner proves it by driving
every vector through the CLI dispatch — argv built from the vector's query, a
fixture machine bound at the ``run()`` seam — and comparing the parsed stdout
against the vector's expected block with exact equality. A vector the library
runner asks and this runner cannot is an error, not a silent skip: the
library-only questions are named, so a question added tomorrow forces a CLI
decision instead of quietly never reaching the boundary.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from atlas.cli import run
from atlas.installations import RETRODECK_JSON_SUFFIX
from atlas.machine import FixtureMachine

_REPO_ROOT = Path(__file__).resolve().parents[1]
_VECTOR_DIR = _REPO_ROOT / "vectors" / "machines"

# expected key → the input key carrying its query (the library runner's own
# pairing, mirrored — tests/test_machine_vectors.py).
_QUERY_KEY = {
    "catalogue": "catalogue_query",
    "systems": "systems_query",
    "systems_for_platform": "platform_systems_query",
    "platform_ids": "platform_ids_query",
    "launchable": "launchable_query",
    "rom_location": "rom_location_query",
    "aggregate": "aggregate_query",
    "savefile_location": "savefile_query",
    "savestate_location": "savestate_query",
    "screenshot_location": "screenshot_query",
    "firmware": "firmware_query",
    "identification": "identify_query",
    "texture_pack_location": "texture_query",
    "soft_patch_candidates": "soft_patch_query",
    "mod_location": "mod_query",
}

# Questions the corpus asks that the CLI deliberately does not carry: the
# entry routes need a catalogue entry named on the command line, which is a
# design of its own (issue #196 scopes it out). Naming them here is the
# tripwire — an expected key in neither map fails the run below.
_LIBRARY_ONLY = {
    "entry_savefile_location",
    "entry_savestate_location",
    "entry_texture_pack_location",
    "entry_mod_location",
}


def _load_vectors():
    files = sorted(_VECTOR_DIR.glob("*.json"))
    assert files, f"no vector files found in {_VECTOR_DIR}"
    for path in files:
        data = json.loads(path.read_text())
        for vector in data["vectors"]:
            yield pytest.param(vector, id=f"{path.stem}:{vector['name']}")


def _machine(inp) -> FixtureMachine:
    return FixtureMachine(
        inp["files"],
        symlinks=inp.get("symlinks"),
        cores=inp.get("cores"),
        dirs=inp.get("dirs"),
        inaccessible=inp.get("inaccessible"),
        unlistable=inp.get("unlistable"),
        appimages=inp.get("appimages"),
    )


def _flag(name: str, value) -> list[str]:
    return [] if value is None else [name, str(value)]


def _question_argv(question: str, query) -> list[str]:
    """The CLI spelling of one question, selector not included."""
    if question in (
        "savefile_location",
        "savestate_location",
        "screenshot_location",
        "texture_pack_location",
        "mod_location",
    ):
        return (
            [question.replace("_", "-")]
            + _flag("--content", query.get("content_path"))
            + _flag("--core", query.get("core_so"))
        )
    if question == "soft_patch_candidates":
        return ["soft-patch-candidates", query["content_path"]] + _flag("--core", query.get("core_so"))
    if question == "systems":
        return ["systems"]
    if question == "systems_for_platform":
        return ["systems-for-platform", query["vocabulary"], query["value"]]
    if question == "platform_ids":
        return ["platform-ids", query["system"]]
    # "catalogue" is the expected block's name for the question, "emulators_for"
    # the aggregate query's — one spelling answers both.
    if question in ("emulators_for", "catalogue"):
        return ["emulators-for", query["system"]] + _flag("--content", query.get("content_path"))
    if question == "rom_location":
        return ["rom-location", query["system"]]
    if question == "launchable":
        return ["launchable", query["system"], query["content_path"]]
    if question == "firmware":
        verify = ["--verify"] if query.get("verify") else []
        kind = query["kind"]
        if kind == "core":
            return ["firmware-for-core", "--core", query["core_so"]] + verify
        if kind == "system":
            return ["firmware-for-system", "--system", query["system"]] + verify
        return ["firmware-inventory"] + verify
    if question == "identification":
        return (
            ["identify-firmware"]
            + _flag("--md5", query.get("md5"))
            + _flag("--sha1", query.get("sha1"))
            + _flag("--size", query.get("size"))
        )
    raise AssertionError(f"no CLI spelling for question {question!r}")


def _argv_for(key: str, query, expected, name: str) -> list[str]:
    """argv asking what the vector's expected block asks, selector included.

    The library runner's selector semantics carry over exactly: an explicit
    ``installation`` picks the first handle of that kind, no selector means
    the first detected handle — whose kind names the same handle, so the CLI's
    ``--installation`` reaches it. The ``aggregate`` family is the flagless
    form and stays without a selector.
    """
    if key == "aggregate":
        return _question_argv(query["question"], query)
    selector = query.get("installation")
    if selector is None:
        installations = expected["installations"]
        assert installations, f"{name}: expected.{key} needs a detected installation to answer"
        selector = installations[0]["kind"]
    return _question_argv(key, query) + ["--installation", selector]


@pytest.mark.parametrize("vector", list(_load_vectors()))
def test_the_cli_answers_every_vector_byte_for_byte(vector, capsys):
    inp = vector["input"]
    expected = vector["expected"]
    name = vector["name"]
    rationale = vector.get("rationale", name)
    machine = _machine(inp)

    exit_code = run(["detect"], home=inp["home"], machine=machine)
    assert exit_code == 0
    assert json.loads(capsys.readouterr().out) == expected["installations"], rationale

    for key, expectation in expected.items():
        if key == "installations" or key in _LIBRARY_ONLY:
            continue
        assert key in _QUERY_KEY, (
            f"{name}: the CLI conformance runner cannot ask expected.{key} — "
            f"give it a CLI spelling or name it library-only"
        )
        argv = _argv_for(key, inp[_QUERY_KEY[key]], expected, name)
        exit_code = run(argv, home=inp["home"], machine=machine)
        out = capsys.readouterr().out
        assert exit_code == 0, f"{name}: {argv} exited {exit_code}"
        assert json.loads(out) == expectation, rationale


class TestTheExitCodeSeparatesAnsweringFromAsking:
    """0 is an answer — unresolved and empty included; 2 is an unaskable question."""

    def test_an_empty_machine_still_answers(self, capsys):
        exit_code = run(["systems"], home="/home/deck", machine=FixtureMachine({}))
        assert exit_code == 0
        assert json.loads(capsys.readouterr().out) == []

    def test_a_kind_this_machine_does_not_have_cannot_be_asked(self, capsys):
        exit_code = run(
            ["systems", "--installation", "retrodeck"],
            home="/home/deck",
            machine=FixtureMachine({}),
        )
        captured = capsys.readouterr()
        assert exit_code == 2
        assert captured.out == ""
        assert "retrodeck" in captured.err

    def test_an_unknown_question_is_a_usage_error(self):
        machine = FixtureMachine({})
        with pytest.raises(SystemExit) as excinfo:
            run(["no-such-question"], home="/home/deck", machine=machine)
        assert excinfo.value.code == 2

    def test_the_home_flag_wins_over_the_binding(self, capsys):
        marker = {f"/elsewhere/{RETRODECK_JSON_SUFFIX}": "{}"}
        exit_code = run(
            ["detect", "--home", "/elsewhere"],
            home="/home/deck",
            machine=FixtureMachine(marker),
        )
        assert exit_code == 0
        detected = json.loads(capsys.readouterr().out)
        assert [installation["kind"] for installation in detected] == ["retrodeck"]


def test_python_dash_m_atlas_speaks_from_a_real_machine(tmp_path):
    """The ``python -m atlas`` wiring, end to end: real machine, empty home, [] answer."""
    completed = subprocess.run(
        [sys.executable, "-m", "atlas", "detect", "--home", str(tmp_path)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0
    assert json.loads(completed.stdout) == []
