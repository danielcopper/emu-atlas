"""Walking the conformance corpus for the caveat blocks inside it — once, in one place.

Several checks read the same thing out of ``vectors/machines/*.json``: that
every exported code appears somewhere, that one ``(code, key)`` carries one
JSON type, that every enumerated value comes from its vocabulary, that the
generator's two corpus gates fire, and — in the mutation probes — that a
deliberately broken corpus is caught. Each grew its own recursive walker over
the nested answer shapes, and walkers that must agree about what a caveat *is*
are that many chances to disagree. This is that walker.

One of them recognised a caveat by its ``code`` alone rather than by the
``{code, data}`` pair. Measured over the shipped corpus the two recognitions
find the same 124 codes, so folding it in changed nothing — but "the same
today" is not a definition, and one definition is what keeps the checks from
drifting apart.

It yields the ``data`` mapping itself rather than a copy, so a probe that needs
to break one value can assign through it.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterator

_VECTOR_DIR = Path(__file__).resolve().parents[1] / "vectors" / "machines"


def caveat_blocks(node: Any) -> Iterator[tuple[str, dict[str, Any]]]:
    """Every ``{code, data}`` block under *node*, as ``(code, the data mapping)``.

    A caveat is recognised by its shape rather than by where it sits: findings,
    refusals and the caveat lists on every answer all serialize the same pair,
    and a walker keyed on position would miss whichever the corpus rearranges
    next.
    """
    if isinstance(node, dict):
        code = node.get("code")
        data = node.get("data")
        if isinstance(code, str) and isinstance(data, dict):
            yield code, data
        for value in node.values():
            yield from caveat_blocks(value)
    elif isinstance(node, list):
        for value in node:
            yield from caveat_blocks(value)


def vector_files(directory: Path | None = None) -> list[Path]:
    """The corpus files, sorted — the default is the shipped corpus."""
    return sorted((directory or _VECTOR_DIR).glob("*.json"))


def expected_blocks(directory: Path | None = None) -> Iterator[tuple[str, Any]]:
    """``(where, the expected block)`` per vector, for a walk that reports a place."""
    for path in vector_files(directory):
        for vector in json.loads(path.read_text(encoding="utf-8"))["vectors"]:
            yield f"{path.name}:{vector['name']}", vector["expected"]
