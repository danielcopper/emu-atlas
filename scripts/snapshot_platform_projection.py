#!/usr/bin/env python3
"""Re-record the projection of every single-key answer of the packaged platform table.

Writes ``tests/data/platform_projection.json``: for every key of every source,
the answer :func:`atlas.translate` gives for that one pair, in the projection
the vectors assert (``tests/platform_projection.py``, no citations and no
pinned versions), the table version once for all. The test in
``tests/test_snapshot_platform_projection.py`` holds the packaged table against
it, so a regeneration that moves, adds or drops a mapping of any key fails
until this file is renewed in the same change (#784 D33)::

    mise run snapshot-platforms

Renewing is a statement that every moved key was meant to move: read the
test's list of moved keys before running this.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from atlas.contract import platform_translation_contract  # noqa: E402
from atlas.platforms import platform_table, translate  # noqa: E402
from tests.platform_projection import single_key_entry, translation_projection  # noqa: E402

SNAPSHOT_PATH = ROOT / "tests" / "data" / "platform_projection.json"

SNAPSHOT_SPEC = (
    "Test-side (#784 D33): the projection of every single-key answer of the packaged platform table, keyed "
    "'source/key' — per answer its status, every source's keys with their relation (a source left out answers "
    "none) and its caveats; the table version once. Written by scripts/snapshot_platform_projection.py; "
    "tests/test_snapshot_platform_projection.py holds the packaged table against it."
)


def snapshot() -> dict[str, object]:
    """The snapshot of the packaged table as it stands: every key of every source, in table order."""
    table = platform_table()
    answers = {
        f"{source}/{key}": single_key_entry(translation_projection(platform_translation_contract(translate([(source, key)]))))
        for source in table.sources
        for key in sorted(table.keys[source])
    }
    return {"spec": SNAPSHOT_SPEC, "table_version": table.table_version, "answers": answers}


def serialize(data: dict[str, object]) -> str:
    """The snapshot as text: one answer per line, so a moved key is one changed line in a diff."""
    answers = data["answers"]
    assert isinstance(answers, dict)
    lines = [
        f"  {json.dumps(name, ensure_ascii=False)}: {json.dumps(entry, ensure_ascii=False, sort_keys=True)}"
        for name, entry in answers.items()
    ]
    head = f'{{\n "spec": {json.dumps(data["spec"], ensure_ascii=False)},\n "table_version": {data["table_version"]},\n'
    return head + ' "answers": {\n' + ",\n".join(lines) + "\n }\n}\n"


def main() -> None:
    data = snapshot()
    SNAPSHOT_PATH.write_text(serialize(data), encoding="utf-8")
    print(f"wrote {SNAPSHOT_PATH.relative_to(ROOT)}: {len(data['answers'])} single-key answers")  # type: ignore[arg-type]


if __name__ == "__main__":
    main()
