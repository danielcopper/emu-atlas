"""The projection a platform translation is asserted in by both vector families.

``vectors/README.md`` defines it and says why; this module computes it from a
serialized translation (:func:`atlas.contract.platform_translation_contract`),
as a port computes it from its own answer.
"""

from __future__ import annotations

from typing import Any


def _sources(answers: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "source": answer["source"],
            "status": answer["status"],
            "keys": [{"key": k["key"], "relation": k["relation"]} for k in answer["keys"]],
        }
        for answer in answers
    ]


def translation_projection(contract: dict[str, Any]) -> dict[str, Any]:
    """*contract* (a serialized translation) without its citations and its source versions."""
    return {
        "table_version": contract["table_version"],
        "pairs": [{**pair, "sources": _sources(pair["sources"])} for pair in contract["pairs"]],
        "intersection_status": contract["intersection_status"],
        "intersection": _sources(contract["intersection"]),
        "conflict": contract["conflict"],
        "caveats": contract["caveats"],
    }


def with_projected_translation(answer: dict[str, Any]) -> dict[str, Any]:
    """A serialized machine answer whose ``translation`` field is projected."""
    return {**answer, "translation": translation_projection(answer["translation"])}
