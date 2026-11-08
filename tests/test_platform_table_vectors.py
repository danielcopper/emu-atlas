"""Run every 'platform-table' vector: pairs in, the projected translation out, exact equality.

The family is data only — no machine — so it pins the packaged table itself:
a client vendoring the table runs the same vectors against its own reading of
it (#784 D27). The projection a translation is asserted in lives in
``tests/platform_projection.py`` and is stated in ``vectors/README.md``.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from atlas.contract import platform_translation_contract
from atlas.platforms import translate
from tests.platform_projection import translation_projection, with_projected_translation

_VECTOR_DIR = Path(__file__).resolve().parents[1] / "vectors" / "platform-table"


def _vectors():
    files = sorted(_VECTOR_DIR.glob("*.json"))
    assert files, f"no vector files found in {_VECTOR_DIR}"
    for path in files:
        for vector in json.loads(path.read_text(encoding="utf-8"))["vectors"]:
            yield pytest.param(vector, id=f"{path.stem}:{vector['name']}")


@pytest.mark.parametrize("vector", list(_vectors()))
def test_platform_table_vector(vector):
    pairs = [tuple(pair) for pair in vector["input"]["pairs"]]
    answer = translation_projection(platform_translation_contract(translate(pairs)))
    assert answer == vector["expected"], vector.get("rationale", vector["name"])


CONTRACT = {
    "table_version": 2,
    "source_versions": [{"source": "esde", "version": "v1"}, {"source": "romm", "version": "m1"}],
    "pairs": [
        {
            "source": "romm",
            "key": "genesis",
            "version": None,
            "status": "known",
            "sources": [
                {"source": "esde", "status": "keys", "keys": [{"key": "genesis", "relation": "narrowMatch", "citations": ["c"]}]},
                {"source": "romm", "status": "none", "keys": []},
            ],
        }
    ],
    "intersection_status": "agreed",
    "intersection": [
        {"source": "esde", "status": "keys", "keys": [{"key": "genesis", "relation": "narrowMatch", "citations": ["c"]}]}
    ],
    "conflict": [],
    "caveats": [{"code": "x", "data": {}}],
}


class TestTheProjection:
    def test_it_is_the_contract_without_citations_and_source_versions(self):
        assert translation_projection(CONTRACT) == {
            "table_version": 2,
            "pairs": [
                {
                    "source": "romm",
                    "key": "genesis",
                    "version": None,
                    "status": "known",
                    "sources": [
                        {"source": "esde", "status": "keys", "keys": [{"key": "genesis", "relation": "narrowMatch"}]},
                        {"source": "romm", "status": "none", "keys": []},
                    ],
                }
            ],
            "intersection_status": "agreed",
            "intersection": [{"source": "esde", "status": "keys", "keys": [{"key": "genesis", "relation": "narrowMatch"}]}],
            "conflict": [],
            "caveats": [{"code": "x", "data": {}}],
        }

    def test_a_machine_answer_keeps_everything_but_its_translation(self):
        answer = {"vocabulary": "romm", "value": "genesis", "translation": CONTRACT, "matches": [], "caveats": []}
        projected = with_projected_translation(answer)
        assert {k: v for k, v in projected.items() if k != "translation"} == {
            k: v for k, v in answer.items() if k != "translation"
        }
        assert projected["translation"] == translation_projection(CONTRACT)

class TestTheValidatorHoldsTheFamilysShape:
    def _vector(self, **changes):
        from scripts.validate_vectors import validate_platform_table_vector

        vector = {
            "name": "v",
            "input": {"pairs": [["romm", "genesis"]]},
            "expected": translation_projection(platform_translation_contract(translate([("romm", "genesis")]))),
            **changes,
        }
        validate_platform_table_vector(vector)

    def test_a_real_answer_validates(self):
        self._vector()

    @pytest.mark.parametrize(
        "changes, message",
        [
            ({"input": {"pairs": [["romm"]]}}, "each input pair is"),
            ({"input": {"pairs": [["romm", "sega32"]]}}, "must echo the asked pairs"),
            ({"input": {"pairs": [["romm", "genesis"]], "extra": 1}}, "exactly 'pairs'"),
        ],
    )
    def test_a_malformed_vector_is_refused(self, changes, message):
        from scripts.validate_vectors import VectorError

        with pytest.raises(VectorError, match=message):
            self._vector(**changes)
