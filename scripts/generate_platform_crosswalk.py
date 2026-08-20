"""Generate ``atlas/data/platform_ids_crosswalk.json`` from its pinned sources — issue #68.

The crosswalk answers "which public platform identities correspond to this
ES-DE platform id". Its four columns come from three pinned upstreams:

- **the platform vocabulary** — ES-DE v3.4.1, ``es-app/src/PlatformId.cpp``
  (``platformNames``) with ``es-app/src/PlatformId.h`` supplying the enum
  symbols in the same declaration order. This is the vocabulary the
  ``<platform>`` tag of every ``es_systems.xml`` system speaks
  (``SystemData.cpp:1074-1091``: lowercased, split by ``readList``, matched
  exactly by ``getPlatformId``).
- **screenscraper / thegamesdb** — ES-DE's own scraper maps at the same
  revision (``scrapers/ScreenScraper.cpp`` ``screenscraper_platformid_map``,
  ``scrapers/GamesDBJSONScraper.cpp`` ``gamesdb_new_platformid_map``). Purely
  mechanical: parsed, never curated here.
- **igdb / libretro** — identity records copied from RomM's maintained tables
  at rommapp/romm tag 5.1.0 (commit 06cafd4b): ``IGDB_PLATFORM_LIST``
  (``backend/adapters/services/igdb.py``) and ``LIBRETRO_PLATFORM_LIST``
  (``backend/handler/metadata/libretro_handler.py``), both keyed by RomM's
  ``UniversalPlatformSlug`` enum (``backend/handler/metadata/base_handler.py``).
  IGDB records keep the **numeric id as the stable key**: IGDB slugs drift
  under a stable id (platform 117 renamed ``philips-cd-i`` → ``philips-cdi``
  between 2024 and 2025), so the slug is a convenience column only.

The join between the ES-DE vocabulary and RomM's enum is **hand-curated** in
``HAND_JOIN`` below — no upstream publishes it, and the two namespaces are
not transformable by rule (ES-DE ``cdimono1`` vs RomM ``philips-cd-i``,
``dreamcast`` vs ``dc``). Semantics of a join entry: the listed identities
are the public platforms whose games belong on this ES-DE platform,
family-canonical only. An empty list is a decided "no public identity"
(game engines, fantasy consoles, hardware IGDB does not carry), never a gap:
the generator refuses a platform it cannot place.

A maintainer tool, not part of the library: it fetches the pinned revisions
from the network and rewrites the data file; the library only ever reads the
result. stdlib only, like everything else in the tree.
"""

from __future__ import annotations

import json
import re
import sys
import urllib.request
from pathlib import Path
from typing import Any

ESDE_RAW = "https://gitlab.com/es-de/emulationstation-de/-/raw/v3.4.1/es-app/src"
ROMM_RAW = "https://raw.githubusercontent.com/rommapp/romm/06cafd4b100e9a883faf31c15a5c8724881cf35a/backend"

SOURCES = {
    "platform_id_h": f"{ESDE_RAW}/PlatformId.h",
    "platform_id_cpp": f"{ESDE_RAW}/PlatformId.cpp",
    "screenscraper_cpp": f"{ESDE_RAW}/scrapers/ScreenScraper.cpp",
    "gamesdb_cpp": f"{ESDE_RAW}/scrapers/GamesDBJSONScraper.cpp",
    "romm_ups": f"{ROMM_RAW}/handler/metadata/base_handler.py",
    "romm_igdb": f"{ROMM_RAW}/adapters/services/igdb.py",
    "romm_libretro": f"{ROMM_RAW}/handler/metadata/libretro_handler.py",
}

OUTPUT = Path(__file__).resolve().parents[1] / "atlas" / "data" / "platform_ids_crosswalk.json"

# The vocabulary's non-platform sentinels (PlatformId.cpp: "Nothing set",
# "Do not allow scraping", and the PLATFORM_COUNT placeholder).
SENTINELS = ("unknown", "ignore", "invalid")

# Upstream records read but not copied: RomM 5.1.0's PC_8800_SERIES libretro
# entry names the PC Engine playlist ("NEC - PC Engine - TurboGrafx 16",
# libretro_handler.py:215) — a data error at the pinned revision, and a known
# wrong value is not world knowledge.
LIBRETRO_EXCLUDES = frozenset({"pc-8800-series"})

# ES-DE platform id → RomM UniversalPlatformSlug values carrying its public
# identities. Platforms absent here join by their own name. An empty list is
# a decided null — reviewed against the IGDB platform list at the pinned
# revision, not a fallthrough.
HAND_JOIN: dict[str, list[str]] = {
    # different spellings of the same platform
    "amigacd32": ["amiga-cd32"],
    "amstradcpc": ["acpc"],
    "apple2": ["appleii"],
    "apple2gs": ["apple-iigs"],
    "arcadia": ["arcadia-2001"],
    "archimedes": ["acorn-archimedes"],
    "astrocde": ["astrocade"],
    "atarijaguar": ["jaguar"],
    "atarijaguarcd": ["atari-jaguar-cd"],
    "atarilynx": ["lynx"],
    "atarist": ["atari-st"],
    "cdimono1": ["philips-cd-i"],
    "cdtv": ["commodore-cdtv"],
    "channelf": ["fairchild-channel-f"],
    "coco": ["trs-80-color-computer"],
    "dragon32": ["dragon-32-slash-64"],
    "dreamcast": ["dc"],
    "electron": ["acorn-electron"],
    "fm7": ["fm-7"],
    "fmtowns": ["fm-towns"],
    "gameandwatch": ["g-and-w"],
    "gamecom": ["game-dot-com"],
    "gc": ["ngc"],
    "gx4000": ["amstrad-gx4000"],
    "lcdgames": ["handheld-electronic-lcd"],
    "macintosh": ["mac"],
    "mastersystem": ["sms"],
    "megadrive": ["genesis"],
    "megaduck": ["mega-duck-slash-cougar-boy"],
    "n3ds": ["3ds"],
    "neogeocd": ["neo-geo-cd"],
    "ngp": ["neo-geo-pocket"],
    "ngpc": ["neo-geo-pocket-color"],
    "odyssey2": ["odyssey-2"],
    "palm": ["palm-os"],
    "pc88": ["pc-8800-series"],
    "pc98": ["pc-9800-series"],
    "pcengine": ["tg16"],
    "pcenginecd": ["turbografx-cd"],
    "pcfx": ["pc-fx"],
    "pcwindows": ["win"],
    "plus4": ["c-plus-4"],
    "pokemini": ["pokemon-mini"],
    "scv": ["epoch-super-cassette-vision"],
    "sega32x": ["sega32"],
    "segapico": ["sega-pico"],
    "sg-1000": ["sg1000"],
    "supracan": ["super-acan"],
    "ti99": ["ti-99"],
    "vic20": ["vic-20"],
    "wonderswancolor": ["wonderswan-color", "swancrystal"],
    "x68000": ["sharp-x68000"],
    "zxspectrum": ["zxs"],
    # a family platform whose IGDB coverage is split or wider than one entry
    "atari800": ["atari8bit"],
    "atarixe": ["atari8bit"],
    "neogeo": ["neogeoaes", "neogeomvs"],
    "snes": ["snes", "sfam"],
    "sufami": ["sfam"],
    "n64": ["n64", "64dd"],
    "moto": ["thomson-mo5"],
    "pc": ["dos", "win"],
    "windows3x": ["win"],
    "sgb": ["gb"],
    "snes-msu1": ["snes"],
    "flash": ["browser"],
    "j2me": ["mobile"],
    # arcade hardware IGDB files under its one Arcade platform
    "atomiswave": ["arcade"],
    "daphne": ["arcade"],
    "naomi": ["arcade"],
    # decided nulls — no IGDB platform and no libretro database name at the
    # pinned revisions (game engines, fantasy consoles, uncarried hardware);
    # scummvm's RomM record is excluded on RomM's own comment: its id 50501
    # is an IGDB keyword id, not a platform id.
    "adam": [],
    "crvision": [],
    "easyrpg": [],
    "fpinball": [],
    "gmaster": [],
    "love": [],
    "lowresnx": [],
    "lutro": [],
    "mess": [],
    "msxturbor": [],
    "mugen": [],
    "openbor": [],
    "oric": [],
    "pico8": [],
    "pv1000": [],
    "residualvm": [],
    "samcoupe": [],
    "scummvm": [],
    "solarus": [],
    "spectravideo": [],
    "steam": [],
    "tic80": [],
    "vircon32": [],
    "vpinball": [],
    "wasm4": [],
    "zmachine": [],
    "zxnext": [],
}


def _fetch(url: str) -> str:
    with urllib.request.urlopen(url) as response:  # noqa: S310 — pinned https URLs above
        return response.read().decode("utf-8")


def _must(pattern: str, text: str, what: str, flags: int = 0) -> str:
    found = re.search(pattern, text, flags)
    if found is None:
        raise SystemExit(f"pinned source no longer matches: {what}")
    return found.group(1)


def _enum_symbols(header: str) -> list[str]:
    """The PlatformId enum symbols in declaration order, sentinel included."""
    body = _must(r"enum PlatformId : unsigned int \{(.*?)\};", header, "PlatformId enum", re.S)
    symbols = ["PLATFORM_UNKNOWN"]
    for line in body.splitlines():
        token = re.match(r"\s*([A-Z][A-Z0-9_]*)\s*[,}]?", line)
        if token and token.group(1) != "PLATFORM_UNKNOWN":
            symbols.append(token.group(1))
    return symbols


def _platform_names(cpp: str) -> tuple[list[str], dict[str, str]]:
    """``platformNames`` in order, and each name's source comment."""
    body = _must(r"platformNames \{(.*?)\};", cpp, "platformNames", re.S)
    names = re.findall(r'"([^"]*)"', body)
    comments = dict(re.findall(r'"([^"]+)",\s*//\s*(.+?)\s*$', body, re.M))
    return names, comments


def _scraper_map(cpp: str, map_name: str, quoted: bool) -> dict[str, int]:
    body = _must(map_name + r" \{(.*?)\};", cpp, map_name, re.S)
    pattern = r'\{\s*([A-Z][A-Z0-9_]*)\s*,\s*"(\d+)"\s*\}' if quoted else r"\{\s*([A-Z][A-Z0-9_]*)\s*,\s*(\d+)\s*\}"
    return {symbol: int(number) for symbol, number in re.findall(pattern, body, re.S)}


def _ups_values(base_handler: str) -> dict[str, str]:
    """RomM's UniversalPlatformSlug enum: member name → slug value."""
    body = _must(
        r"class UniversalPlatformSlug\(enum\.StrEnum\):(.*?)(?:\nclass |\Z)",
        base_handler,
        "UniversalPlatformSlug",
        re.S,
    )
    # A long member wraps its value into parentheses on the next line.
    return dict(re.findall(r'([A-Z_][A-Z0-9_]*)\s*=\s*\(?\s*"([^"]+)"', body, re.S))


def _igdb_records(igdb_py: str, ups: dict[str, str]) -> dict[str, dict[str, Any]]:
    """RomM's IGDB identities keyed by UPS slug: {id, slug, name} per platform."""
    body = _must(r"IGDB_PLATFORM_LIST[^=]*=\s*\{(.*?)\n\}", igdb_py, "IGDB_PLATFORM_LIST", re.S)
    records: dict[str, dict[str, Any]] = {}
    # The lookahead ends an entry at the next member or at the dict's end — the
    # last entry carries no trailing newline inside the captured body.
    for member, block in re.findall(r"UPS\.([A-Z0-9_]+):\s*\{(.*?)\},?\s*(?=UPS\.|\Z)", body, re.S):
        record_id = re.search(r'"id":\s*(\d+)', block)
        slug = re.search(r'"slug":\s*"([^"]+)"', block)
        name = re.search(r'"name":\s*"([^"]+)"', block)
        if record_id and slug and name:
            records[ups[member]] = {
                "id": int(record_id.group(1)),
                "slug": slug.group(1),
                "name": name.group(1),
            }
    return records


def _libretro_names(libretro_py: str, ups: dict[str, str]) -> dict[str, str]:
    body = _must(
        r"LIBRETRO_PLATFORM_LIST[^=]*=\s*\{(.*?)\n\}", libretro_py, "LIBRETRO_PLATFORM_LIST", re.S
    )
    return {ups[member]: value for member, value in re.findall(r'UPS\.([A-Z0-9_]+):\s*"([^"]+)"', body)}


def main() -> int:
    texts = {key: _fetch(url) for key, url in SOURCES.items()}

    symbols = _enum_symbols(texts["platform_id_h"])
    names, comments = _platform_names(texts["platform_id_cpp"])
    if len(symbols) != len(names):
        raise SystemExit(f"enum/name count mismatch: {len(symbols)} symbols, {len(names)} names")
    symbol_to_name = dict(zip(symbols, names))

    screenscraper = {
        symbol_to_name[symbol]: number
        for symbol, number in _scraper_map(texts["screenscraper_cpp"], "screenscraper_platformid_map", quoted=False).items()
    }
    thegamesdb = {
        symbol_to_name[symbol]: number
        for symbol, number in _scraper_map(texts["gamesdb_cpp"], "gamesdb_new_platformid_map", quoted=True).items()
    }

    ups = _ups_values(texts["romm_ups"])
    igdb = _igdb_records(texts["romm_igdb"], ups)
    libretro = _libretro_names(texts["romm_libretro"], ups)

    platforms: dict[str, dict[str, Any]] = {}
    for name in names:
        if name in SENTINELS:
            continue
        join = HAND_JOIN.get(name, [name])
        for key in join:
            if key not in igdb and key not in libretro:
                raise SystemExit(f"join key {key!r} for platform {name!r} matches no pinned record")
        if name not in HAND_JOIN and name not in igdb and name not in libretro:
            raise SystemExit(f"platform {name!r} is neither hand-joined nor same-named in a pinned table")
        libretro_names = []
        for key in join:
            value = libretro.get(key)
            if key not in LIBRETRO_EXCLUDES and value is not None and value not in libretro_names:
                libretro_names.append(value)
        platforms[name] = {
            "comment": comments.get(name, ""),
            "igdb": [igdb[key] for key in join if key in igdb],
            "libretro": libretro_names,
            "screenscraper": screenscraper.get(name),
            "thegamesdb": thegamesdb.get(name),
        }

    payload = {
        "schema": 1,
        "spec": (
            "DESIGN.md — Vocabulary. The public identities of each ES-DE platform id: which "
            "IGDB platforms, libretro database names and scraper ids correspond to the "
            "<platform> tag a catalogue system declares. World knowledge under CLAUDE.md's "
            "boundary rule — nothing here is read off a machine, so it is versioned and "
            "source-cited, and the loader refuses a malformed table rather than answering "
            "out of one. The machine-read half (which systems declare which platform, and "
            "whether they are declared here) is never tabled."
        ),
        "description": (
            "platforms: ES-DE platform id → its public identities. igdb entries carry the "
            "numeric id as the stable key (slugs drift under a stable id: platform 117 "
            "renamed philips-cd-i → philips-cdi), the slug and name ride along as recorded "
            "at the pinned revision. libretro entries are database/playlist names. "
            "screenscraper/thegamesdb are the numeric ids ES-DE's own scrapers use. An "
            "empty list or null is a decided absence at the pinned revisions, never a gap. "
            "The comment field is the source's own annotation — prose, non-contractual. "
            "Semantics of the identity lists: the public platforms whose games belong on "
            "this ES-DE platform, family-canonical only (the hand-curated join lives in "
            "scripts/generate_platform_crosswalk.py, HAND_JOIN)."
        ),
        "sources": {
            "vocabulary": "ES-DE v3.4.1 es-app/src/PlatformId.cpp (platformNames) + PlatformId.h (enum order)",
            "screenscraper": "ES-DE v3.4.1 es-app/src/scrapers/ScreenScraper.cpp screenscraper_platformid_map",
            "thegamesdb": "ES-DE v3.4.1 es-app/src/scrapers/GamesDBJSONScraper.cpp gamesdb_new_platformid_map",
            "igdb": "rommapp/romm@06cafd4b (tag 5.1.0) backend/adapters/services/igdb.py IGDB_PLATFORM_LIST",
            "libretro": "rommapp/romm@06cafd4b (tag 5.1.0) backend/handler/metadata/libretro_handler.py LIBRETRO_PLATFORM_LIST",
            "generator": "scripts/generate_platform_crosswalk.py",
        },
        "platforms": platforms,
    }

    OUTPUT.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    with_igdb = sum(1 for p in platforms.values() if p["igdb"])
    print(
        f"wrote {OUTPUT.name}: {len(platforms)} platforms, {with_igdb} with an IGDB identity, "
        f"{sum(1 for p in platforms.values() if p['libretro'])} with libretro names, "
        f"{sum(1 for p in platforms.values() if p['screenscraper'] is not None)} screenscraper, "
        f"{sum(1 for p in platforms.values() if p['thegamesdb'] is not None)} thegamesdb"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
