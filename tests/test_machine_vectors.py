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
    emulator_contract,
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


def _retrodeck(installs, context: str) -> atlas.RetroDeck:
    install = _select(installs, "retrodeck", context)
    assert isinstance(install, atlas.RetroDeck)
    return install


@pytest.mark.parametrize("vector", list(_load_vectors()))
def test_machine_vector(vector):
    inp = vector["input"]
    expected = vector["expected"]
    rationale = vector.get("rationale", vector["name"])
    machine = _machine(inp)

    installs = atlas.detect(inp["home"], machine)
    assert [installation_contract(i) for i in installs] == expected["installations"], rationale

    if "emulators" in expected:
        catalogue = _retrodeck(installs, vector["name"])
        entries = catalogue.emulators_for(
            inp["catalogue_query"]["system"],
            content_path=inp["catalogue_query"].get("content_path"),
        )
        assert [emulator_contract(e) for e in entries] == expected["emulators"], rationale

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
        catalogue = _retrodeck(installs, vector["name"])
        entries = catalogue.emulators_for(
            entry_query["system"], content_path=entry_query.get("content_path")
        )
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

    def _vector(self, **paths):
        return {
            "name": "synthetic",
            "input": {"home": "/home/deck", "files": {"/home/deck/x": ""}, **paths},
            "expected": {"installations": []},
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
