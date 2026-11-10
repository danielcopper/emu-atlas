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


def single_key_entry(projection: dict[str, Any]) -> dict[str, Any]:
    """One single-key answer's projection as the snapshot records it, losing nothing.

    The table version is recorded once for the whole snapshot; a source without
    a key for the pair is left out (every source is listed, so it is ``none``);
    and a single pair's intersection is its own sources or nothing, so it is not
    repeated. Both are asserted here rather than assumed.
    """
    (pair,) = projection["pairs"]
    agreed = projection["intersection_status"] == "agreed"
    assert projection["intersection"] == (pair["sources"] if agreed else []), "one pair's intersection is its own"
    assert projection["conflict"] == [] and agreed == (pair["status"] == "known"), "one pair is never a conflict"
    entry: dict[str, Any] = {"status": pair["status"]}
    keys = {s["source"]: {k["key"]: k["relation"] for k in s["keys"]} for s in pair["sources"] if s["keys"]}
    if keys:
        entry["keys"] = keys
    if projection["caveats"]:
        entry["caveats"] = projection["caveats"]
    return entry
