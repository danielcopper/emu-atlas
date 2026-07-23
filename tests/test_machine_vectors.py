"""Run every 'machines' vector through the real detect() + save_location().

The vectors are the artifact; this is atlas's conformance run for the machines
family. Each vector is a whole fixture machine — files, symlinks, core answers —
and detect() must find exactly the expected installations (kind + root + health,
in order). When the vector carries a query, the highest-priority install must
resolve the expected SavePlacement: directory, root kind, remaining holes, and
the observed-or-unknown file set. A port that passes these reads the machine the
way the reference does.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import atlas

_REPO_ROOT = Path(__file__).resolve().parents[1]
_VECTOR_DIR = _REPO_ROOT / "vectors" / "machines"


def _load_vectors():
    files = sorted(_VECTOR_DIR.glob("*.json"))
    assert files, f"no vector files found in {_VECTOR_DIR}"
    for path in files:
        data = json.loads(path.read_text())
        assert data["family"] == "machines"
        for vector in data["vectors"]:
            yield pytest.param(vector, id=f"{path.stem}:{vector['name']}")


@pytest.mark.parametrize("vector", list(_load_vectors()))
def test_machine_vector(vector):
    inp = vector["input"]
    expected = vector["expected"]
    machine = atlas.FixtureMachine(
        inp["files"], symlinks=inp.get("symlinks"), cores=inp.get("cores")
    )

    installs = atlas.detect(inp["home"], machine)
    got_installations = [
        {"kind": i.kind, "root": i.root(), "health": i.health()} for i in installs
    ]
    assert got_installations == expected["installations"], vector.get("rationale", vector["name"])

    if "emulators" in expected:
        catalogue_inst = installs[0]
        assert isinstance(catalogue_inst, atlas.RetroDeck), "catalogue vectors target RetroDECK"
        entries = catalogue_inst.emulators_for(inp["catalogue_query"]["system"])
        got_emulators = []
        for exp, entry in zip(expected["emulators"], entries):
            got_emulators.append({key: getattr(entry, key) for key in exp})
        assert len(entries) == len(expected["emulators"]) and got_emulators == expected["emulators"], vector["name"]

    if "save_location" in expected:
        query = inp["query"]
        placement = installs[0].save_location(
            content_path=query.get("content_path"), core_so=query.get("core_so")
        )
        got_placement = {
            "dir": placement.dir,
            "root_kind": placement.root_kind,
            "needs": list(placement.needs),
            "file_set": {
                "state": placement.file_set.state,
                "files": list(placement.file_set.files),
            },
        }
        if "caveats" in expected["save_location"]:
            got_placement["caveats"] = [c.code for c in placement.caveats]
        expected_gran = expected["save_location"].get("granularity")
        if expected_gran is not None:
            assert placement.granularity is not None, vector["name"]
            got_placement["granularity"] = {
                key: getattr(placement.granularity, key) for key in expected_gran
            }
        assert got_placement == expected["save_location"], vector.get("rationale", vector["name"])
