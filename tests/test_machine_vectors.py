"""Run every 'machines' vector through the real detect() + resolver routes.

The vectors are the artifact; this is atlas's conformance run for the machines
family (schema 2). Each vector is a whole fixture machine — files, dirs,
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
from atlas.contract import (
    emulator_contract,
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
        assert data["schema"] == 2, f"{path}: runner speaks vector schema 2"
        for vector in data["vectors"]:
            yield pytest.param(vector, id=f"{path.stem}:{vector['name']}")


def _machine(inp) -> atlas.FixtureMachine:
    return atlas.FixtureMachine(
        inp["files"],
        symlinks=inp.get("symlinks"),
        cores=inp.get("cores"),
        dirs=inp.get("dirs"),
        inaccessible=inp.get("inaccessible"),
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
