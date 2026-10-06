"""Generate ``atlas/data/platform_ids_by_system.json`` from its pinned sources — issue #584.

The crosswalk (``generate_platform_crosswalk.py``) answers which public ids
belong to an ES-DE *platform*. Some public ids stand for one ES-DE *system*
whose ``<platform>`` tag it shares with others: ES-DE v3.4.1 tags ``cps1``,
``cps2``, ``cps3``, ``model2`` and ``stv`` ``arcade`` like every other
arcade system, and ``doom`` ``pc, pcwindows``. A platform-keyed row
cannot carry such an id without answering every system under the tag, so
this table is keyed by system instead. ``scummvm`` is the one row whose tag
is its own: its libretro name sits here because the crosswalk's ``scummvm``
join is a decided null — RomM's ScummVM record carries an IGDB keyword id,
not a platform id — and so copies no libretro name from that record. Its
sources:

- **the systems and their tags** — ES-DE v3.4.1,
  ``resources/systems/linux/es_systems.xml``. Each row cites the line of the
  system's ``<name>`` and of its ``<platform>``: the shared tag is the reason
  the row exists.
- **screenscraper** — RomM tag 5.3.1 (commit 95599dad),
  ``SCREENSAVER_PLATFORM_LIST`` in ``backend/handler/metadata/ss_handler.py``
  (RomM's spelling of its ScreenScraper table). ScreenScraper's own system
  list needs API credentials, so RomM's table at a tag is the cited source;
  an id written through a named constant cites the constant's line as well.
- **libretro** — RomM tag 5.3.1, ``LIBRETRO_PLATFORM_LIST`` in
  ``backend/handler/metadata/libretro_handler.py``, held against the primary
  authority: the database name must exist as ``rdb/<name>.rdb`` in
  libretro/libretro-database at the pinned commit.

The join between an ES-DE system and RomM's ``UniversalPlatformSlug`` member
is **hand-curated** in ``HAND_JOIN`` below, as the crosswalk's is: no upstream
publishes it. The generator refuses a join it cannot place — a member with no
row in the named table, a system ES-DE does not declare exactly once, a
libretro name the database does not carry, and an id two systems would share.

A maintainer tool, not part of the library: it fetches the pinned revisions
from the network and rewrites the data file; the library only ever reads the
result. stdlib only, like everything else in the tree.
"""

from __future__ import annotations

import json
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

ESDE_SYSTEMS = "https://gitlab.com/es-de/emulationstation-de/-/raw/v3.4.1/resources/systems/linux/es_systems.xml"
ROMM_REVISION = "95599dadbe93c8f7b8a8647148fafbe293df3167"
ROMM_RAW = f"https://raw.githubusercontent.com/rommapp/romm/{ROMM_REVISION}/backend/handler/metadata"
LIBRETRO_DATABASE_REVISION = "fbeefcb46c2e1b20a7e2945f34a694a41b2d6f90"
LIBRETRO_DATABASE_RAW = (
    f"https://raw.githubusercontent.com/libretro/libretro-database/{LIBRETRO_DATABASE_REVISION}"
)

ESDE_CITE = "ES-DE v3.4.1 resources/systems/linux/es_systems.xml"
ROMM_CITE = f"rommapp/romm@{ROMM_REVISION[:8]} (tag 5.3.1) backend/handler/metadata"
LIBRETRO_DATABASE_CITE = f"libretro/libretro-database@{LIBRETRO_DATABASE_REVISION[:8]}"

OUTPUT = Path(__file__).resolve().parents[1] / "atlas" / "data" / "platform_ids_by_system.json"

# ES-DE system → vocabulary → the RomM UniversalPlatformSlug member whose id
# in that vocabulary stands for this system alone. ScummVM's ScreenScraper id
# (123) is not here: ES-DE tags scummvm with its own platform, so the
# crosswalk already answers it. RomM's IGDB record for ScummVM (50501) is not
# here either: RomM's own note calls it an IGDB keyword id, not a platform id
# (backend/adapters/services/igdb.py:2017 at the same tag). Model 3's
# ScreenScraper id (55) is not here because model3 is not an atlas system id:
# the vocabulary list (system_ids.json, RetroDECK 0.10.9b's catalogue) has no
# model3, RetroDECK's shipped es_systems.xml carries it only commented out,
# and the loader refuses a row naming a system the vocabulary lacks.
HAND_JOIN: dict[str, dict[str, str]] = {
    "cps1": {"screenscraper": "CPS1"},
    "cps2": {"screenscraper": "CPS2"},
    "cps3": {"screenscraper": "CPS3"},
    "doom": {"screenscraper": "DOOM", "libretro": "DOOM"},
    "model2": {"screenscraper": "MODEL2"},
    "scummvm": {"libretro": "SCUMMVM"},
    "stv": {"screenscraper": "STV"},
}


def _fetch(url: str) -> str:
    with urllib.request.urlopen(url) as response:  # noqa: S310 — pinned https URLs above
        return response.read().decode("utf-8")


def _exists(url: str) -> bool:
    request = urllib.request.Request(url, method="HEAD")
    try:
        with urllib.request.urlopen(request) as response:  # noqa: S310 — pinned https URL
            return response.status == 200
    except urllib.error.HTTPError as error:
        if error.code == 404:
            return False
        raise


def _line_of(lines: list[str], pattern: str, what: str) -> tuple[int, re.Match[str]]:
    """The one 1-based line matching *pattern*, and its match — refusing none or several."""
    found = [(number, match) for number, line in enumerate(lines, 1) if (match := re.search(pattern, line))]
    if len(found) != 1:
        raise SystemExit(f"pinned source no longer matches exactly once ({len(found)} times): {what}")
    return found[0]


def _esde_system(lines: list[str], system: str) -> dict[str, str]:
    """A system's fullname and its name/platform citation, read line-wise for the line numbers."""
    name_line, _ = _line_of(lines, rf"<name>{re.escape(system)}</name>", f"es_systems.xml <name>{system}</name>")
    fullname = platform = None
    for number, line in enumerate(lines[name_line:], name_line + 1):
        if "</system>" in line:
            break
        if fullname is None and (match := re.search(r"<fullname>([^<]+)</fullname>", line)):
            fullname = match.group(1)
        if platform is None and (match := re.search(r"<platform>([^<]+)</platform>", line)):
            platform = (number, match.group(1))
    if fullname is None or platform is None:
        raise SystemExit(f"es_systems.xml system {system!r} carries no <fullname> or <platform>")
    return {
        "comment": fullname,
        "system": (
            f"{ESDE_CITE}:{name_line} <name>{system}</name>, "
            f":{platform[0]} <platform>{platform[1]}</platform>"
        ),
    }


def _screenscraper_id(lines: list[str], member: str) -> dict[str, str]:
    row_line, row = _line_of(
        lines, rf'^\s*UPS\.{member}:\s*\{{"id":\s*([A-Z0-9_]+)', f"ss_handler.py UPS.{member}"
    )
    value = row.group(1)
    source = f"{ROMM_CITE}/ss_handler.py:{row_line} UPS.{member}"
    if not value.isdigit():
        constant_line, constant = _line_of(
            lines, rf"^{value}:\s*Final\s*=\s*(\d+)\s*$", f"ss_handler.py constant {value}"
        )
        source += f" → :{constant_line} {value} = {constant.group(1)}"
        value = constant.group(1)
    return {"vocabulary": "screenscraper", "value": value, "source": source}


def _libretro_name(lines: list[str], member: str) -> dict[str, str]:
    row_line, row = _line_of(
        lines, rf'^\s*UPS\.{member}:\s*"([^"]+)"', f"libretro_handler.py UPS.{member}"
    )
    name = row.group(1)
    if not _exists(f"{LIBRETRO_DATABASE_RAW}/rdb/{name}.rdb"):
        raise SystemExit(f"libretro-database carries no rdb/{name}.rdb at the pinned commit")
    return {
        "vocabulary": "libretro",
        "value": name,
        "source": f"{ROMM_CITE}/libretro_handler.py:{row_line} UPS.{member}; {LIBRETRO_DATABASE_CITE} rdb/{name}.rdb",
    }


def main() -> int:
    esde = _fetch(ESDE_SYSTEMS).splitlines()
    readers = {
        "screenscraper": (_fetch(f"{ROMM_RAW}/ss_handler.py").splitlines(), _screenscraper_id),
        "libretro": (_fetch(f"{ROMM_RAW}/libretro_handler.py").splitlines(), _libretro_name),
    }

    systems: dict[str, dict[str, Any]] = {}
    owners: dict[tuple[str, str], str] = {}
    for system, join in sorted(HAND_JOIN.items()):
        ids = []
        for vocabulary, member in sorted(join.items(), key=lambda item: item[0]):
            lines, read = readers[vocabulary]
            entry = read(lines, member)
            key = (vocabulary, entry["value"])
            if key in owners:
                raise SystemExit(f"{vocabulary} {entry['value']!r} would stand for both {owners[key]!r} and {system!r}")
            owners[key] = system
            ids.append(entry)
        systems[system] = {**_esde_system(esde, system), "ids": ids}

    payload = {
        "schema": 1,
        "spec": (
            "DESIGN.md — Vocabulary. The public platform ids that stand for exactly one ES-DE "
            "system. Most of these systems share their <platform> tag with others (arcade; pc, "
            "pcwindows), so the platform-keyed crosswalk (platform_ids_crosswalk.json) cannot "
            "carry their ids without answering every system under the tag. World knowledge under CLAUDE.md's boundary rule — nothing here is "
            "read off a machine, so it is versioned and source-cited, and the loader refuses a "
            "malformed table rather than answering out of one. An id listed here wins over the "
            "crosswalk."
        ),
        "description": (
            "systems: ES-DE system name → the public ids that stand for it alone. Each id names "
            "its vocabulary (one of the crosswalk's four), its value as the string a question "
            "asks with (a numeric id as its decimal digits, a libretro database name verbatim) "
            "and its source at the pinned revision. The system field cites the system's <name> "
            "and <platform> lines in ES-DE's catalogue — a shared tag is why most rows exist. "
            "The comment field is that catalogue's <fullname> — prose, non-contractual. One "
            "vocabulary/value pair stands for one system, never two (the hand-curated join "
            "lives in scripts/generate_platform_ids_by_system.py, HAND_JOIN)."
        ),
        "sources": {
            "systems": f"{ESDE_CITE} (<name>, <fullname>, <platform>)",
            "screenscraper": (
                f"{ROMM_CITE}/ss_handler.py SCREENSAVER_PLATFORM_LIST — ScreenScraper's own "
                "system list needs API credentials"
            ),
            "libretro": (
                f"{ROMM_CITE}/libretro_handler.py LIBRETRO_PLATFORM_LIST, each name held "
                f"against {LIBRETRO_DATABASE_CITE} rdb/<name>.rdb"
            ),
            "generator": "scripts/generate_platform_ids_by_system.py",
        },
        "systems": systems,
    }

    OUTPUT.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"wrote {OUTPUT.name}: {len(systems)} systems, {len(owners)} ids")
    return 0


if __name__ == "__main__":
    sys.exit(main())
