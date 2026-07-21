"""Run every 'machines' vector through the real detect() + save_placement().

The vectors are the artifact; this is atlas's conformance run for the machines
family. Each vector is a whole fixture machine: detect() must find exactly the
expected installations (kind + root, in order), and — when the vector carries a
query — the highest-priority install must return the expected SavePlacement.
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
    reader = atlas.FixtureReader(inp["files"])

    installs = atlas.detect(inp["home"], reader)
    got_installations = [{"kind": i.kind, "root": i.root()} for i in installs]
    assert got_installations == expected["installations"], vector.get("rationale", vector["name"])

    if "save_placement" in expected:
        query = inp["query"]
        placement = installs[0].save_placement(
            query["system"], query.get("core"), query.get("rom_dir_name")
        )
        got_placement = {
            "dir": placement.dir,
            "filename": placement.filename,
            "needs": list(placement.needs),
        }
        assert got_placement == expected["save_placement"], vector.get("rationale", vector["name"])
