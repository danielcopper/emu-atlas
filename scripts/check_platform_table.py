"""The platform table's release check (issue #784 D17) — run by CI and by the test suite.

The loader (``atlas.platforms.load_platform_table``) already refuses a table
that does not add up as a file: uncited mappings, unlisted keys, a relation
outside the SKOS four or other than its key's row count implies, an override
naming a key its source does not have at the pin. This check holds the table
to what a release promises on top:

- every key of every source is placed on a row once the overrides are
  applied, or listed in ``platform_excluded.json`` with a reason (the loader
  holds that list to keys that exist, one entry each, each with a reason);
- no row a key reaches still carries the placeholder kind ``unclassified``
  (D16), and no row is classified by a pick between catalogues that state
  different kinds for it — such a conflict is decided by a kind override
  with its reason (D17).

Exit status 0 with nothing printed when the table passes; 1 with one line per
problem otherwise.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from atlas.platforms import UNCLASSIFIED_KIND, load_platform_table  # noqa: E402

DATA = ROOT / "atlas" / "data"


def check(table_text: str, overrides_text: str, excluded_text: str) -> list[str]:
    """Every release problem of the three files, one line each; empty when the table passes."""
    table = load_platform_table(table_text, overrides_text, excluded_text)
    problems: list[str] = []
    for source, keys in table.keys.items():
        for key in sorted(keys):
            if (source, key) not in table.placements and (source, key) not in table.excluded:
                problems.append(f"{source}/{key}: neither placed on a row nor excluded with a reason")
    for row, kinds in sorted(table.kind_conflicts.items()):
        keys = ", ".join(f"{s}/{k}" for s, ks in table.by_row[row].items() for k in ks)
        problems.append(f"catalogues state different kinds ({', '.join(kinds)}) and no override decides ({keys})")
    for row, kind in sorted(table.kinds.items()):
        if kind == UNCLASSIFIED_KIND:
            keys = ", ".join(f"{s}/{k}" for s, ks in table.by_row[row].items() for k in ks)
            problems.append(f"a row carries no kind ({keys})")
    return problems


def main() -> int:
    problems = check(
        (DATA / "platform_table.json").read_text(encoding="utf-8"),
        (DATA / "platform_overrides.json").read_text(encoding="utf-8"),
        (DATA / "platform_excluded.json").read_text(encoding="utf-8"),
    )
    for problem in problems:
        print(problem)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
