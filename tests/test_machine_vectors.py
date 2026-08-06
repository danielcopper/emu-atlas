"""Run every 'machines' vector through the real detect() + resolver routes.

The vectors are the artifact; this is atlas's conformance run for the machines
family (schema 3). Each vector is a whole fixture machine — files, dirs,
symlinks, core answers — and detect() must find exactly the expected
installations. Expectations are the canonical contract serializations
(atlas.contract) asserted with EXACT equality: every stable field, including
caveat codes and data, granularity identity, fallback/physical directories,
and health issue codes. Prose (sources, messages) is deliberately outside the
contract. A port that passes these reads the machine the way the reference
does.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import atlas
from scripts import validate_vectors
from atlas.contract import (
    catalogue_contract,
    systems_contract,
    firmware_contract,
    identification_contract,
    installation_contract,
    placement_contract,
    unresolved_contract,
)

_REPO_ROOT = Path(__file__).resolve().parents[1]
_VECTOR_DIR = _REPO_ROOT / "vectors" / "machines"


def _load_vectors():
    files = sorted(_VECTOR_DIR.glob("*.json"))
    assert files, f"no vector files found in {_VECTOR_DIR}"
    for path in files:
        data = json.loads(path.read_text())
        assert data["family"] == "machines"
        assert data["schema"] == 3, f"{path}: runner speaks vector schema 3"
        for vector in data["vectors"]:
            yield pytest.param(vector, id=f"{path.stem}:{vector['name']}")


def _machine(inp) -> atlas.FixtureMachine:
    return atlas.FixtureMachine(
        inp["files"],
        symlinks=inp.get("symlinks"),
        cores=inp.get("cores"),
        dirs=inp.get("dirs"),
        inaccessible=inp.get("inaccessible"),
        unlistable=inp.get("unlistable"),
    )


def _select(installs, selector: str | None, context: str):
    if selector is None:
        assert installs, f"{context}: no installation detected to answer the query"
        return installs[0]
    for install in installs:
        if install.kind == selector:
            return install
    raise AssertionError(f"{context}: no detected installation of kind {selector!r}")


@pytest.mark.parametrize("vector", list(_load_vectors()))
def test_machine_vector(vector):
    inp = vector["input"]
    expected = vector["expected"]
    rationale = vector.get("rationale", vector["name"])
    machine = _machine(inp)

    installs = atlas.detect(inp["home"], machine)
    assert [installation_contract(i) for i in installs] == expected["installations"], rationale

    if "catalogue" in expected:
        query = inp["catalogue_query"]
        # Any handle, selected the way every other query selects one: the
        # catalogue question is on the protocol now, so a vector can ask it of
        # an arrangement that answers with a refusal.
        install = _select(installs, query.get("installation"), vector["name"])
        answer = install.emulators_for(query["system"], content_path=query.get("content_path"))
        assert catalogue_contract(answer) == expected["catalogue"], rationale

    if "systems" in expected:
        install = _select(installs, inp.get("systems_query", {}).get("installation"), vector["name"])
        assert systems_contract(install.systems()) == expected["systems"], rationale

    if "save_location" in expected:
        query = inp["query"]
        install = _select(installs, query.get("installation"), vector["name"])
        placement = install.save_location(
            content_path=query.get("content_path"), core_so=query.get("core_so")
        )
        assert placement_contract(placement) == expected["save_location"], rationale

    if "firmware" in expected:
        firmware_query = inp["firmware_query"]
        install = _select(installs, firmware_query.get("installation"), vector["name"])
        verify = firmware_query.get("verify", False)
        kind = firmware_query["kind"]
        if kind == "core":
            answer = install.firmware_for_core(core_so=firmware_query["core_so"], verify=verify)
        elif kind == "system":
            answer = install.firmware_for_system(system=firmware_query["system"], verify=verify)
        else:
            answer = install.firmware_inventory(verify=verify)
        assert firmware_contract(answer) == expected["firmware"], rationale

    if "identification" in expected:
        identify_query = inp["identify_query"]
        install = _select(installs, identify_query.get("installation"), vector["name"])
        identified = install.identify_firmware(
            md5=identify_query.get("md5"),
            sha1=identify_query.get("sha1"),
            size=identify_query.get("size"),
        )
        assert identification_contract(identified) == expected["identification"], rationale

    if "entry_save_location" in expected:
        entry_query = inp["entry_query"]
        install = _select(installs, entry_query.get("installation"), vector["name"])
        entries = install.emulators_for(
            entry_query["system"], content_path=entry_query.get("content_path")
        ).entries
        if "label" in entry_query:
            entry = next(e for e in entries if e.label == entry_query["label"])
        else:
            entry = entries[0]
        outcome = entry.save_location(content_path=entry_query.get("content_path"))
        if isinstance(outcome, atlas.Unresolved):
            got = unresolved_contract(outcome)
        else:
            got = placement_contract(outcome)
        assert got == expected["entry_save_location"], rationale


class TestTheGrammarRefusesContradictions:
    """A rule nothing else can catch: no vector contradicts itself today.

    The validator running clean over the corpus proves the corpus is clean, not
    that the rule is still there — delete the check and the gate stays green.
    So the refusal is asserted directly, through the same per-vector entry
    point the gate drives.
    """

    _INSTALLATION = {
        "kind": "retrodeck",
        "kinds": ["retrodeck"],
        "root": "/mnt/sd/retrodeck",
        "health": [],
    }
    _UNREAD = [{"code": "emulator-catalogue-unreadable", "data": {}}]

    def _vector(self, expected=None, **input_keys):
        return {
            "name": "synthetic",
            "input": {"home": "/home/deck", "files": {"/home/deck/x": ""}, **input_keys},
            "expected": {"installations": []} if expected is None else expected,
        }

    def test_a_path_in_both_unreadable_lists_is_refused(self):
        # The tempting wrong reading is that both together spell mode 000. They
        # do not: such a directory answers *directory* about itself, so it is
        # 'unlistable', and 'inaccessible' would have the resolver refuse it a
        # step earlier than the machine does.
        vector = self._vector(inaccessible=["/saves"], unlistable=["/saves"])
        with pytest.raises(validate_vectors.VectorError, match="both"):
            validate_vectors.validate_machines_vector(vector)

    def test_the_two_lists_are_fine_apart(self):
        vector = self._vector(inaccessible=["/mnt/card"], unlistable=["/saves"])
        validate_vectors.validate_machines_vector(vector)

    def test_a_systems_query_the_runner_cannot_read_is_refused(self):
        # Not an object is not a query: the runner asks it for a handle
        # selector, so a bare string reaches the resolver as an AttributeError
        # instead of a stated defect in the vector.
        vector = self._vector(systems_query="nonsense")
        with pytest.raises(validate_vectors.VectorError, match="systems_query keys"):
            validate_vectors.validate_machines_vector(vector)

    def test_a_systems_query_without_an_expectation_is_refused(self):
        # A question with nothing to compare against is never asked, and a
        # vector that silently asks nothing proves nothing.
        vector = self._vector(systems_query={})
        with pytest.raises(validate_vectors.VectorError, match="systems_query and systems"):
            validate_vectors.validate_machines_vector(vector)

    def test_a_systems_expectation_without_a_query_is_refused(self):
        vector = self._vector(
            expected={"installations": [], "systems": {"systems": [], "caveats": []}}
        )
        with pytest.raises(validate_vectors.VectorError, match="systems_query and systems"):
            validate_vectors.validate_machines_vector(vector)

    def test_systems_stated_beside_a_no_catalogue_code_are_refused(self):
        vector = self._vector(
            systems_query={},
            expected={
                "installations": [self._INSTALLATION],
                "systems": {"systems": ["n64"], "caveats": self._UNREAD},
            },
        )
        with pytest.raises(validate_vectors.VectorError, match="states systems"):
            validate_vectors.validate_machines_vector(vector)

    def test_entries_stated_beside_a_no_catalogue_code_are_refused(self):
        # The same contradiction one level down: a catalogue nobody read
        # declares no entry, so naming one alongside the code that says it was
        # never read locks in an answer no arrangement can produce.
        vector = self._vector(
            catalogue_query={"system": "n64"},
            expected={
                "installations": [self._INSTALLATION],
                "catalogue": {
                    "entries": [
                        {
                            "label": "ParaLLEl N64",
                            "kind": "libretro",
                            "core_so": "parallel_n64_libretro.so",
                            "selection": None,
                            "caveats": [],
                        }
                    ],
                    "caveats": self._UNREAD,
                },
            },
        )
        with pytest.raises(validate_vectors.VectorError, match="states entries"):
            validate_vectors.validate_machines_vector(vector)
