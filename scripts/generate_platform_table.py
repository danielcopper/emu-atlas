"""Generate ``atlas/data/platform_table.json`` from its pinned sources — issue #785.

The table maps the keys of every included source onto one another (DESIGN.md,
Vocabulary). A *row* is one system at the finest distinction any source makes
(#784 D5, D30); a *mapping* places one ``(source, key)`` on one row, with the
relation the key's row set implies, the rule that placed it (``via``) and the
citation that rule stands on. Rows are internal to the loader (D4): their keys
are working names for review and never reach an answer.

Everything here is mechanical: read off the pinned revisions the ``Upstream``
constants below name. ``atlas/data/SOURCES.md`` records each upstream's pin,
licence and decision, and this script refuses to run unless every block has a
licence and a decision line and states the revision its constant pins (D22). The hand
judgements sit in ``atlas/data/platform_overrides.json`` and are applied by the
loader over this file, never here (D2).

The joins, each named by its ``via``:

- ``own-key`` — ES-DE's system names found the rows; each name is exact on its row.
- ``esde-platform-tag`` — an ES-DE platform id maps to every ES-DE system whose
  live ``<platform>`` list carries it.
- ``same-name (ES-DE deployment)`` — a RetroDECK or EmuDeck key spelled as an
  ES-DE system name is that system: both deploy ES-DE's own catalogue.
- ``same-name+platform-list`` — a Batocera, KNULLI or ROCKNIX key spelled as an
  ES-DE system name joins it when the display names agree (every word of the
  shorter name, casefolded and stripped of punctuation, occurs in the longer)
  and its platform list reaches no other ES-DE system.
- ``platform-list-broad`` — such a key whose platform list reaches further
  ES-DE systems maps onto exactly those, so Batocera's ``megadrive``
  (``genesis, megadrive``) covers genesis, megadrive and megadrivejp. ES-DE's
  family tags ``arcade``, ``pc`` and ``pcwindows`` are skipped: they say how a
  key scrapes, not what it holds.
- ``same-name+platform-list (batocera)`` — a KNULLI or ROCKNIX key no ES-DE
  system is spelled as, spelled as a Batocera key whose display name agrees
  and whose platform list is the same, shares that key's rows: both are
  Batocera's catalogue lineage.
- ``romm-resolve`` — RomM's ``resolve_platform_slug`` (``platform_aliases.py``):
  a folder that is a UPS value is that slug, an alias key is its alias. A slug
  maps onto the rows of every folder that resolves to it. RomM's comment on
  the table calls its keys folder names Batocera, RetroBat and ES-DE use;
  RetroBat is no source here, and RetroDECK, EmuDeck, KNULLI and ROCKNIX use
  the same folder names, so an alias key is read as the ES-DE, RetroDECK,
  EmuDeck, Batocera, KNULLI or ROCKNIX folder of that spelling; a folder that
  is a UPS value is the ES-DE-deployment folder of that spelling only (Batocera's ``pico`` is the Sega Pico, RomM's ``pico`` is PICO-8).
- ``core-database (every core)`` — an ES-DE system's live RetroArch commands
  name cores; a core's ``.info`` at the pinned core-info revision lists
  ``database`` names. A name maps to a system when **every** core of that
  system that has a ``database`` key declares it.
- ``esde-scraper-map`` — ES-DE's ScreenScraper and TheGamesDB maps give each
  platform id an id; it maps onto every ES-DE system carrying the platform id.
- ``romm-igdb-list`` / ``romm-ss-list`` — RomM's ``IGDB_PLATFORM_LIST`` gives a
  slug its numeric IGDB id and the IGDB slug, two keys on the same rows (the
  id is the stable one; IGDB re-spells slugs under it), and ``SCREENSAVER_PLATFORM_LIST``
  its ScreenScraper id; each maps onto the slug's rows. The ids are only ever
  these republishers' (#784 D21, changed 2026-11-08); a Wikidata statement
  carrying the same value (P5795 against the slug) is added as a second
  citation, and an id no statement carries is listed as a finding.
- ``own-row`` — a key no join places founds a row of its own (D5).

Every source key is listed under ``keys`` with the citation of where the source
states it, mapped or not, so the release check can hold each one to
mapped-or-excluded (D17). Every entry is open-ended at its source's pinned
version (D15): ``valid_from`` is that version, ``valid_to`` is ``null``.

A maintainer tool, not part of the library: it fetches the pinned revisions
from the network (``--cache DIR`` keeps them between runs) and rewrites the
data file. stdlib only, like everything else in the tree.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import io
import json
import os
import re
import sys
import tarfile
import tempfile
import time
import urllib.parse
import urllib.request
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from xml.parsers import expat

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# The catalogue is read the way the runtime reads it — the same systems, the
# same tokens, the same core a command loads — so a key the table places is
# the key a question on the machine asks with.
from atlas.esde import core_so, parse_es_systems, platform_tokens  # noqa: E402
from atlas.platforms import runs_from  # noqa: E402

OUTPUT = ROOT / "atlas" / "data" / "platform_table.json"
SOURCES_MD = ROOT / "atlas" / "data" / "SOURCES.md"

TABLE_SCHEMA = 1
# The table's version (D12); atlas/data/README.md says when it changes.
TABLE_VERSION = 1

USER_AGENT = "emu-atlas-platform-table/1 (https://github.com/danielcopper/emu-atlas)"


@dataclass(frozen=True)
class Upstream:
    """One pinned upstream: what SOURCES.md names it, and where its files are."""

    block: str
    version: str
    revision: str
    raw: str


ESDE = Upstream(
    "es-de",
    "v3.5.0",
    "50e4b600ae533d772bae3ff880d11a09b05dbe84",
    "https://gitlab.com/es-de/emulationstation-de/-/raw/50e4b600ae533d772bae3ff880d11a09b05dbe84",
)
RETRODECK = Upstream(
    "retrodeck-es-de",
    "retrodeck-main-20260926-172324",
    "15e1b236eafd2864bd99ffcc9853d2efa2a3916c",
    "https://raw.githubusercontent.com/RetroDECK/ES-DE/15e1b236eafd2864bd99ffcc9853d2efa2a3916c",
)
EMUDECK = Upstream(
    "emudeck",
    "dddb3022",
    "dddb30229adbbd54d5ff7283cfd70e11c4322c26",
    "https://raw.githubusercontent.com/dragoonDorise/EmuDeck/dddb30229adbbd54d5ff7283cfd70e11c4322c26",
)
ROMM = Upstream(
    "romm",
    "5.4.1",
    "9c74d0143fbb876f2f62260b91c6fd0b3f590226",
    "https://raw.githubusercontent.com/rommapp/romm/9c74d0143fbb876f2f62260b91c6fd0b3f590226",
)
CORE_INFO = Upstream(
    "libretro-core-info",
    "eac3a278",
    "eac3a2784d428444aa2bd472039b5c5540141af1",
    "https://codeload.github.com/libretro/libretro-core-info/tar.gz/eac3a2784d428444aa2bd472039b5c5540141af1",
)
BATOCERA = Upstream(
    "batocera",
    "batocera-43.1",
    "155c2d8d304cbb53db52e9479dcf683392821d5c",
    "https://raw.githubusercontent.com/batocera-linux/batocera.linux/155c2d8d304cbb53db52e9479dcf683392821d5c",
)
KNULLI = Upstream(
    "knulli",
    "20260511",
    "c1c7de479e56691c6665723046d9f25500e5ee74",
    "https://raw.githubusercontent.com/knulli-cfw/knulli-linux/c1c7de479e56691c6665723046d9f25500e5ee74",
)
ROCKNIX = Upstream(
    "rocknix",
    "20261101",
    "cefc14339a4ae18c02bb69888f27835e50664b60",
    "https://raw.githubusercontent.com/ROCKNIX/distribution/cefc14339a4ae18c02bb69888f27835e50664b60",
)
# Wikidata is read at pinned item revisions, not live: the pin is the file of
# item revisions, its date the version and its digest the revision SOURCES.md
# must record. --repin-wikidata rewrites the file from Wikidata's current state.
WIKIDATA_PINS = ROOT / "scripts" / "wikidata_revisions.json"


def _wikidata_pin() -> tuple[str, str]:
    text = WIKIDATA_PINS.read_text(encoding="utf-8")
    return json.loads(text)["as_of"], hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


WIKIDATA = Upstream("wikidata", *_wikidata_pin(), "https://query.wikidata.org/sparql")

UPSTREAMS = (ESDE, RETRODECK, EMUDECK, ROMM, CORE_INFO, BATOCERA, KNULLI, ROCKNIX, WIKIDATA)

# Column → the upstream blocks it is read from, one per project (#784 D42).
# The column names are the vocabulary names a question takes.
COLUMNS: dict[str, tuple[Upstream, ...]] = {
    "esde": (ESDE,),
    "esde-platform": (ESDE,),
    "retrodeck": (RETRODECK,),
    "emudeck": (EMUDECK,),
    "romm": (ROMM,),
    "romm-folder": (ROMM,),
    "libretro": (CORE_INFO,),
    "batocera": (BATOCERA,),
    "knulli": (KNULLI,),
    "rocknix": (ROCKNIX,),
    "igdb": (ROMM,),
    # Two republishers fill this column: ES-DE's scraper map and RomM's table.
    "screenscraper": (ESDE, ROMM),
    "thegamesdb": (ESDE,),
}


def column_runs_from(column: str) -> str:
    """The version a column's mappings run from, as the loader reads it back (:func:`atlas.platforms.runs_from`)."""
    return runs_from(tuple((u.block, u.version) for u in COLUMNS[column]))

ESDE_SYSTEMS_PATH = "resources/systems/linux/es_systems.xml"
ESDE_PLATFORM_CPP = "es-app/src/PlatformId.cpp"
ESDE_PLATFORM_H = "es-app/src/PlatformId.h"
ESDE_SS_CPP = "es-app/src/scrapers/ScreenScraper.cpp"
ESDE_TGDB_CPP = "es-app/src/scrapers/GamesDBJSONScraper.cpp"
EMUDECK_SYSTEMS_TXT = "roms/systems.txt"
EMUDECK_ROMS_API = (
    "https://api.github.com/repos/dragoonDorise/EmuDeck/contents/roms?ref=" + EMUDECK.revision
)
ROMM_SLUGS = "backend/utils/platform_slugs.py"
ROMM_ALIASES = "backend/utils/platform_aliases.py"
BATOCERA_YML = "package/batocera/emulationstation/batocera-es-system/es_systems.yml"
KNULLI_YML = "package/emulationstation/knulli-es-system/es_systems.yml"
ROCKNIX_DIR = "config/emulators"
ROCKNIX_API = (
    "https://api.github.com/repos/ROCKNIX/distribution/contents/config/emulators?ref="
    + ROCKNIX.revision
)

# PlatformId.cpp's non-platform entries ("Nothing set", "Do not allow
# scraping", the PLATFORM_COUNT placeholder).
ESDE_SENTINELS = frozenset({"unknown", "ignore", "invalid"})
# ES-DE platform ids that group systems for scraping rather than name one:
# a platform-list token among them says how a key scrapes, not what it holds.
FAMILY_TAGS = frozenset({"arcade", "pc", "pcwindows"})
BATOCERA_FAMILY = ("batocera", "knulli", "rocknix")

WIKIDATA_PROPERTIES = {"igdb": "P5795", "screenscraper": "P7757", "thegamesdb": "P7623"}
ROMM_IGDB = "backend/adapters/services/igdb.py"
ROMM_SS = "backend/handler/metadata/ss_handler.py"
WIKIDATA_QUERY = (
    "SELECT DISTINCT ?item WHERE { { ?item wdt:P5795 [] } UNION { ?item wdt:P7757 [] } "
    "UNION { ?item wdt:P7623 [] } }"
)

# How the catalogues that state a kind (D16) spell it, onto the table's closed
# kind vocabulary (atlas.platforms.PLATFORM_KINDS). Batocera's and KNULLI's
# `hardware`, ROCKNIX's SYSTEM_HARDWARE.
HARDWARE_KINDS = {
    "console": "console",
    "portable": "handheld",
    "computer": "computer",
    "arcade": "arcade-board",
    "port": "engine-port",
    "game engine": "engine-port",
    "pinball": "engine-port",
    "system": "launcher",
    "tool": "launcher",
    "library": "launcher",
    "emulator": "launcher",
}


# --- SOURCES.md -------------------------------------------------------------


def source_blocks(text: str) -> dict[str, dict[str, str]]:
    """``## <block> — <name>`` sections of SOURCES.md, each a dict of its ``- field:`` lines."""
    blocks: dict[str, dict[str, str]] = {}
    current: dict[str, str] | None = None
    for line in text.splitlines():
        heading = re.match(r"^## `([a-z0-9-]+)` — (.+)$", line)
        if heading:
            current = {"name": heading.group(2).strip()}
            blocks[heading.group(1)] = current
            continue
        item = re.match(r"^- \*\*([a-z ]+):\*\* (.+)$", line)
        if item and current is not None:
            current[item.group(1)] = item.group(2).strip()
    return blocks


def check_sources(text: str) -> dict[str, dict[str, str]]:
    """Refuse to run without a licence and a decision line for every upstream (D22).

    The pins themselves are the ``Upstream`` constants; SOURCES.md records them
    for the reader, so a block whose revision line does not state the pinned
    revision stops the run too — the record and the pin cannot drift apart.
    """
    blocks = source_blocks(text)
    for upstream in UPSTREAMS:
        block = blocks.get(upstream.block)
        if block is None:
            raise SystemExit(f"SOURCES.md has no block for {upstream.block!r}")
        for needed in ("licence", "decision"):
            if not block.get(needed):
                raise SystemExit(f"SOURCES.md block {upstream.block!r} has no {needed} line")
        if upstream.revision and upstream.revision not in block.get("revision", ""):
            raise SystemExit(f"SOURCES.md block {upstream.block!r} does not record the pinned revision {upstream.revision}")
    return blocks


# --- fetching ---------------------------------------------------------------


class Fetcher:
    """Fetch pinned URLs once, optionally keeping them under a cache directory."""

    def __init__(self, cache: Path | None) -> None:
        self.cache = cache

    def bytes(self, url: str) -> bytes:
        # The URL's digest names the cache file: unique for any URL length, and
        # the file appears whole or not at all (written aside, then renamed).
        path = None
        if self.cache is not None:
            path = self.cache / hashlib.sha256(url.encode("utf-8")).hexdigest()
            if path.exists():
                return path.read_bytes()
        request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(request, timeout=120) as response:  # noqa: S310 — pinned https URLs
            data = response.read()
        if path is not None:
            path.parent.mkdir(parents=True, exist_ok=True)
            partial = path.with_suffix(".partial")
            partial.write_bytes(data)
            os.replace(partial, path)
        return data

    def text(self, url: str) -> str:
        return self.bytes(url).decode("utf-8")

    def json(self, url: str) -> Any:
        return json.loads(self.bytes(url))


def _must(pattern: str, text: str, what: str, flags: int = 0) -> re.Match[str]:
    found = re.search(pattern, text, flags)
    if found is None:
        raise SystemExit(f"pinned source no longer matches: {what}")
    return found


def _line_of(text: str, offset: int) -> int:
    return text.count("\n", 0, offset) + 1


# --- reading the sources ----------------------------------------------------


@dataclass
class SourceKey:
    """One key as its source states it."""

    key: str
    citation: str
    name: str = ""
    platforms: tuple[str, ...] = ()
    hardware: str = ""
    commands: tuple[tuple[str, int], ...] = ()
    platform_line: int = 0


class _SystemsParser:
    """The lines the ``<system>`` blocks of an es_systems.xml sit on — only the lines.

    What a system is (its name, ``<platform>`` tokens and the cores its
    commands load) is :mod:`atlas.esde`'s reading; this pass adds where in the
    file each element stands, which a citation needs and the runtime does not.
    """

    def __init__(self) -> None:
        self.systems: list[dict[str, Any]] = []
        self._current: dict[str, Any] | None = None
        self._text: list[str] = []
        self._line = 0
        self.parser = expat.ParserCreate()
        self.parser.StartElementHandler = self._start
        self.parser.EndElementHandler = self._end
        self.parser.CharacterDataHandler = self._text.append

    def _start(self, tag: str, _attrs: dict[str, str]) -> None:
        self._text.clear()
        self._line = self.parser.CurrentLineNumber
        if tag == "system":
            self._current = {"commands": []}
        elif self._current is not None:
            self._current[tag + "_line"] = self._line

    def _end(self, tag: str) -> None:
        if self._current is None:
            return
        value = "".join(self._text).strip()
        if tag == "command":
            self._current["commands"].append((value, self._line))
        elif tag == "system":
            self.systems.append(self._current)
            self._current = None
        else:
            self._current[tag] = value

    def parse(self, text: str) -> list[dict[str, Any]]:
        self.parser.Parse(text, True)
        return self.systems


def esde_systems(text: str, cite: str) -> list[SourceKey]:
    """Every live system of an es_systems.xml, as :func:`atlas.esde.parse_es_systems` reads it.

    Each is cited to the line of its ``<name>``, and carries its commands with
    the line each sits on.
    """
    located = {(system.get("name") or "").strip(): system for system in _SystemsParser().parse(text)}
    keys: list[SourceKey] = []
    for name, declared in parse_es_systems(text, provenance=cite).systems.items():
        system = located[name]
        keys.append(
            SourceKey(
                key=name,
                citation=f"{cite}:{system['name_line']}",
                name=system.get("fullname", ""),
                platforms=declared.platforms,
                commands=tuple(system["commands"]),
                platform_line=system.get("platform_line", 0),
            )
        )
    return keys


def commented_systems(text: str, cite: str) -> list[SourceKey]:
    """Whole ``<system>`` blocks inside XML comments — present in the file, switched off.

    A known gap: :func:`atlas.esde.commented_out_systems` skips comment blocks
    that do not parse, such as RetroDECK's ``&dash;&dash;`` ones; the table
    keeps them, so retrodeck/ngage, symbian and zxnext are keys here while the
    machine reports those systems absent until atlas.esde reads such blocks.
    """
    keys: list[SourceKey] = []
    for comment in re.finditer(r"<!--(.*?)-->", text, re.S):
        for block in re.finditer(r"<system>(.*?)</system>", comment.group(1), re.S):
            inner = block.group(1)
            name = re.search(r"<name>([^<]+)</name>", inner)
            if name is None:
                continue
            line = _line_of(text, comment.start(1) + block.start(1) + name.start())
            fullname = re.search(r"<fullname>([^<]+)</fullname>", inner)
            platform = re.search(r"<platform>([^<]*)</platform>", inner)
            keys.append(
                SourceKey(
                    key=name.group(1).strip(),
                    citation=f"{cite}:{line} (inside an XML comment)",
                    name=fullname.group(1).strip() if fullname else "",
                    platforms=platform_tokens(platform.group(1)) if platform else (),
                )
            )
    return keys


def esde_platform_ids(cpp: str, header: str) -> tuple[list[tuple[str, int]], dict[str, str]]:
    """``platformNames`` with their lines, and the enum symbol → name map."""
    start = _must(r"platformNames \{", cpp, "platformNames").end()
    end = cpp.index("};", start)
    first = _line_of(cpp, start)
    names = [
        (name, first + offset)
        for offset, line in enumerate(cpp[start:end].splitlines())
        for name in re.findall(r'"([^"]*)"', line)
    ]
    body = _must(r"enum PlatformId : unsigned int \{(.*?)\};", header, "PlatformId enum", re.S)
    symbols = [
        token.group(1)
        for line in body.group(1).splitlines()
        if (token := re.match(r"\s*([A-Z][A-Z0-9_]*)", line))
    ]
    if len(symbols) != len(names):
        raise SystemExit(f"PlatformId: {len(symbols)} enum symbols, {len(names)} names")
    return names, {symbol: name for symbol, (name, _) in zip(symbols, names, strict=True)}


def scraper_map(cpp: str, map_name: str, symbols: dict[str, str]) -> dict[str, tuple[str, int]]:
    """An ES-DE scraper map: platform id → (provider id, line)."""
    start = _must(re.escape(map_name) + r" \{", cpp, map_name).end()
    end = cpp.index("};", start)
    first = _line_of(cpp, start)
    found: dict[str, tuple[str, int]] = {}
    for offset, line in enumerate(cpp[start:end].splitlines()):
        for symbol, number in re.findall(r'\{\s*([A-Z][A-Z0-9_]*)\s*,\s*"?(\d+)"?\s*\}', line):
            found[symbols[symbol]] = (number, first + offset)
    return found


def romm_slugs(text: str, cite: str) -> list[SourceKey]:
    """RomM's ``UniversalPlatformSlug`` members, read with ``ast``."""
    module = ast.parse(text)
    for node in module.body:
        if isinstance(node, ast.ClassDef) and node.name == "UniversalPlatformSlug":
            return [
                SourceKey(key=str(item.value.value), citation=f"{cite}:{item.lineno}")
                for item in node.body
                if isinstance(item, ast.Assign) and isinstance(item.value, ast.Constant)
            ]
    raise SystemExit("pinned source no longer matches: UniversalPlatformSlug")


def romm_aliases(text: str, ups_by_member: dict[str, str]) -> dict[str, tuple[str, int]]:
    """``PLATFORM_FS_ALIASES``: folder → (slug, line)."""
    module = ast.parse(text)
    for node in module.body:
        if (
            isinstance(node, ast.AnnAssign)
            and isinstance(node.target, ast.Name)
            and node.target.id == "PLATFORM_FS_ALIASES"
            and isinstance(node.value, ast.Dict)
        ):
            aliases: dict[str, tuple[str, int]] = {}
            for key, value in zip(node.value.keys, node.value.values, strict=True):
                assert isinstance(key, ast.Constant) and isinstance(value, ast.Attribute)
                aliases[str(key.value)] = (ups_by_member[value.attr], key.lineno)
            return aliases
    raise SystemExit("pinned source no longer matches: PLATFORM_FS_ALIASES")


def romm_members(text: str) -> dict[str, str]:
    module = ast.parse(text)
    for node in module.body:
        if isinstance(node, ast.ClassDef) and node.name == "UniversalPlatformSlug":
            return {
                item.targets[0].id: str(item.value.value)
                for item in node.body
                if isinstance(item, ast.Assign)
                and isinstance(item.targets[0], ast.Name)
                and isinstance(item.value, ast.Constant)
            }
    raise SystemExit("pinned source no longer matches: UniversalPlatformSlug")


def _module_ints(text: str) -> dict[str, int]:
    """Module-level ``NAME: Final = 6`` constants, which RomM's tables refer to by name."""
    return {m.group(1): int(m.group(2)) for m in re.finditer(r"^([A-Z][A-Z0-9_]*): Final = (\d+)\s*$", text, re.M)}


def _romm_table(text: str, name: str) -> list[tuple[str, dict[str, ast.expr]]]:
    """The ``UPS.MEMBER: {...}`` entries of one of RomM's module-level provider tables.

    Only the assignment is parsed, padded to its own line numbers: the module
    around it may use syntax newer than the interpreter running this script.
    """
    start = _must(r"^" + name + r"\b[^=\n]*=\s*\{", text, name, re.M)
    end = text.index("\n}", start.start()) + 2
    snippet = "\n" * (_line_of(text, start.start()) - 1) + text[start.start() : end]
    node = ast.parse(snippet).body[0]
    assert isinstance(node, ast.AnnAssign) and isinstance(node.value, ast.Dict)
    entries = []
    for key, value in zip(node.value.keys, node.value.values, strict=True):
        assert isinstance(key, ast.Attribute) and isinstance(value, ast.Dict)
        fields = {k.value: v for k, v in zip(value.keys, value.values, strict=True) if isinstance(k, ast.Constant)}
        entries.append((key.attr, fields))
    return entries


def _int_of(node: ast.expr, constants: dict[str, int]) -> int:
    if isinstance(node, ast.Constant) and isinstance(node.value, int):
        return node.value
    if isinstance(node, ast.Name) and node.id in constants:
        return constants[node.id]
    raise SystemExit(f"pinned source no longer matches: an id that is not a number ({ast.dump(node)})")


def romm_igdb(text: str, members: dict[str, str]) -> dict[str, tuple[str, str, int]]:
    """RomM's ``IGDB_PLATFORM_LIST``: slug → (numeric id, IGDB slug, line of the id)."""
    constants = _module_ints(text)
    found: dict[str, tuple[str, str, int]] = {}
    for member, fields in _romm_table(text, "IGDB_PLATFORM_LIST"):
        slug = fields["slug"]
        assert isinstance(slug, ast.Constant)
        found[members[member]] = (str(_int_of(fields["id"], constants)), str(slug.value), fields["id"].lineno)
    return found


def romm_screenscraper(text: str, members: dict[str, str]) -> dict[str, tuple[str, int]]:
    """RomM's ``SCREENSAVER_PLATFORM_LIST`` (its ScreenScraper table): slug → (id, line of the id)."""
    constants = _module_ints(text)
    return {
        members[member]: (str(_int_of(fields["id"], constants)), fields["id"].lineno)
        for member, fields in _romm_table(text, "SCREENSAVER_PLATFORM_LIST")
    }


_YAML_FIELDS = ("name", "manufacturer", "hardware", "platform")


def yaml_systems(text: str, cite: str) -> list[SourceKey]:
    """The top-level system keys of a Batocera-style es_systems.yml and the fields read here.

    Only the flat fields at two spaces of indent are read (``name``, ``hardware``,
    ``platform``); everything nested under a key is skipped. ``platform`` defaults
    to the key, as the catalogue generator defaults it (``es_systems.py:82`` at
    batocera-43.1).
    """
    keys: list[SourceKey] = []
    fields: dict[str, str] = {}
    for number, line in enumerate(text.splitlines(), start=1):
        top = re.match(r"^([^\s#][^:]*):\s*$", line)
        if top:
            fields = {}
            keys.append(SourceKey(key=top.group(1).strip(), citation=f"{cite}:{number}"))
            continue
        item = re.match(r"^  (" + "|".join(_YAML_FIELDS) + r"):\s*(.*?)\s*$", line)
        if item and keys:
            fields[item.group(1)] = item.group(2).strip().strip("\"'")
            _apply_yaml_fields(keys[-1], fields)
    for key in keys:
        if not key.platforms:
            key.platforms = (key.key,)
    return keys


def _apply_yaml_fields(key: SourceKey, fields: dict[str, str]) -> None:
    key.name = fields.get("name", "")
    key.hardware = fields.get("hardware", "")
    if "platform" in fields:
        key.platforms = platform_tokens(fields["platform"])


def rocknix_systems(files: dict[str, str], cite: str) -> list[SourceKey]:
    """One key per ``config/emulators/<key>.conf``: ``SYSTEM_NAME`` and its fields."""
    keys: list[SourceKey] = []
    for filename in sorted(files):
        values: dict[str, tuple[str, int]] = {}
        for number, line in enumerate(files[filename].splitlines(), start=1):
            match = re.match(r'^SYSTEM_([A-Z_]+)="(.*)"\s*$', line)
            if match:
                values[match.group(1)] = (match.group(2), number)
        name, line = values["NAME"]
        keys.append(
            SourceKey(
                key=name,
                citation=f"{cite}/{filename}:{line}",
                name=values.get("FULLNAME", ("", 0))[0],
                platforms=platform_tokens(values.get("PLATFORM", ("", 0))[0]),
                hardware=values.get("HARDWARE", ("", 0))[0].lower(),
            )
        )
    return keys


def core_databases(archive: bytes) -> dict[str, tuple[tuple[str, ...], int]]:
    """Core short name → (its ``.info`` ``database`` names, the line), over every ``.info``.

    A core whose ``.info`` declares no database is listed too, with no names,
    so a core missing from the result has no ``.info`` at the pin.
    """
    found: dict[str, tuple[tuple[str, ...], int]] = {}
    with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
        for member in tar.getmembers():
            if not member.name.endswith("_libretro.info"):
                continue
            handle = tar.extractfile(member)
            assert handle is not None
            core = Path(member.name).name[: -len("_libretro.info")]
            found[core] = ((), 0)
            for number, line in enumerate(handle.read().decode("utf-8", "replace").splitlines(), 1):
                match = re.match(r'^\s*database\s*=\s*"(.*)"\s*$', line)
                if match:
                    names = tuple(n.strip() for n in match.group(1).split("|") if n.strip())
                    found[core] = (names, number)
    return found


@dataclass
class WikidataItem:
    qid: str
    revision: int
    values: dict[str, tuple[str, ...]]


def wikidata_items(fetcher: Fetcher, pins: dict[str, int]) -> dict[str, WikidataItem]:
    """Every pinned item, read at its pinned revision.

    Only statements of normal or preferred rank count — a deprecated statement is
    Wikidata's own "this value is wrong".
    """
    if not pins:
        raise SystemExit(f"{WIKIDATA_PINS.name} pins no Wikidata item; the second citations would vanish")
    by_revision = {revision: qid for qid, revision in pins.items()}
    revisions = sorted(by_revision)
    items: dict[str, WikidataItem] = {}
    for start in range(0, len(revisions), 50):
        url = "https://www.wikidata.org/w/api.php?" + urllib.parse.urlencode(
            {
                "action": "query",
                "prop": "revisions",
                "revids": "|".join(str(r) for r in revisions[start : start + 50]),
                "rvprop": "ids|content",
                "rvslots": "main",
                "format": "json",
                "formatversion": "2",
            }
        )
        for page in fetcher.json(url)["query"]["pages"]:
            for revision in page["revisions"]:
                qid = by_revision[revision["revid"]]
                if page["title"] != qid:
                    raise SystemExit(f"Wikidata revision {revision['revid']} belongs to {page['title']}, not {qid}")
                entity = json.loads(revision["slots"]["main"]["content"])
                items[qid] = WikidataItem(qid, revision["revid"], _ranked_values(entity))
    missing = sorted(set(pins) - set(items))
    if missing:
        raise SystemExit(f"Wikidata returned no content for the pinned revisions of {', '.join(missing)}")
    return items


def repin_wikidata(fetcher: Fetcher) -> dict[str, Any]:
    """Every item carrying one of the three platform-id properties, at its current revision — a new pin."""
    query = WIKIDATA.raw + "?" + urllib.parse.urlencode({"query": WIKIDATA_QUERY, "format": "json"})
    bindings = fetcher.json(query)["results"]["bindings"]
    if not bindings:
        raise SystemExit("the Wikidata query answered no item; refusing to pin an empty set")
    qids = sorted({b["item"]["value"].rsplit("/", 1)[1] for b in bindings})
    pins: dict[str, int] = {}
    for start in range(0, len(qids), 50):
        url = "https://www.wikidata.org/w/api.php?" + urllib.parse.urlencode(
            {"action": "wbgetentities", "ids": "|".join(qids[start : start + 50]), "props": "info", "format": "json"}
        )
        for qid, entity in fetcher.json(url)["entities"].items():
            pins[qid] = entity["lastrevid"]
        time.sleep(0.5)
    spec = json.loads(WIKIDATA_PINS.read_text(encoding="utf-8"))["spec"]
    return {"spec": spec, "as_of": time.strftime("%Y-%m-%d", time.gmtime()), "items": dict(sorted(pins.items()))}


def _ranked_values(entity: dict[str, Any]) -> dict[str, tuple[str, ...]]:
    values: dict[str, tuple[str, ...]] = {}
    for column, prop in WIKIDATA_PROPERTIES.items():
        values[column] = tuple(
            statement["mainsnak"]["datavalue"]["value"]
            for statement in entity.get("claims", {}).get(prop, [])
            if statement["rank"] != "deprecated" and "datavalue" in statement["mainsnak"]
        )
    return values


# --- building the table -----------------------------------------------------


@dataclass
class Placement:
    row: str
    via: str
    citation: str


@dataclass
class Table:
    keys: dict[str, dict[str, str]] = field(default_factory=lambda: defaultdict(dict))
    rows: set[str] = field(default_factory=set)
    kinds: dict[str, dict[str, dict[str, str]]] = field(default_factory=lambda: defaultdict(dict))
    placements: dict[tuple[str, str], list[Placement]] = field(
        default_factory=lambda: defaultdict(list)
    )
    findings: dict[str, list[str]] = field(default_factory=lambda: defaultdict(list))

    def add_key(self, column: str, key: SourceKey) -> None:
        self.keys[column].setdefault(key.key, key.citation)

    def place(self, column: str, key: str, row: str, via: str, citation: str) -> None:
        if all(p.row != row for p in self.placements[(column, key)]):
            self.placements[(column, key)].append(Placement(row, via, citation))

    def own_row(self, column: str, key: SourceKey) -> str:
        row = f"{column}:{key.key}"
        self.rows.add(row)
        self.place(column, key.key, row, "own-row", key.citation)
        return row

    def rows_of(self, column: str, key: str) -> list[str]:
        return [p.row for p in self.placements.get((column, key), [])]


def _words(name: str) -> set[str]:
    return {w for w in re.split(r"[^0-9a-z]+", name.casefold()) if w}


def names_agree(one: str, other: str) -> bool:
    """Every word of the shorter display name occurs in the longer one."""
    a, b = _words(one), _words(other)
    if not a or not b:
        return False
    shorter, longer = (a, b) if len(a) <= len(b) else (b, a)
    return shorter <= longer


def build_esde(table: Table, systems: list[SourceKey], platform_ids: list[tuple[str, int]], cite: str) -> dict[str, set[str]]:
    """The ES-DE rows, the system column and the platform-id column; returns tag → systems."""
    tagged: dict[str, set[str]] = defaultdict(set)
    for system in systems:
        table.add_key("esde", system)
        row = f"esde:{system.key}"
        table.rows.add(row)
        table.place("esde", system.key, row, "own-key", system.citation)
        for tag in system.platforms:
            tagged[tag].add(system.key)
    lines = {s.key: s.platform_line for s in systems}
    for name, line in platform_ids:
        if name in ESDE_SENTINELS:
            continue
        table.add_key("esde-platform", SourceKey(name, f"{cite}:{ESDE_PLATFORM_CPP}:{line}"))
        for system in sorted(tagged.get(name, ())):
            table.place(
                "esde-platform",
                name,
                f"esde:{system}",
                "esde-platform-tag",
                f"es-de:{ESDE_SYSTEMS_PATH}:{lines[system]}",
            )
    return tagged


def build_deployment(table: Table, column: str, keys: list[SourceKey], esde: dict[str, SourceKey]) -> None:
    """RetroDECK and EmuDeck: an ES-DE spelling is the ES-DE system, anything else its own row."""
    for key in keys:
        table.add_key(column, key)
        if key.key in esde:
            table.place(
                column,
                key.key,
                f"esde:{key.key}",
                "same-name (ES-DE deployment)",
                f"{key.citation}; {esde[key.key].citation}",
            )
        else:
            table.own_row(column, key)


def _reach(tokens: tuple[str, ...], tagged: dict[str, set[str]]) -> set[str]:
    reached: set[str] = set()
    for token in tokens:
        if token not in FAMILY_TAGS:
            reached |= tagged.get(token, set())
    return reached


def build_batocera_family(
    table: Table,
    column: str,
    keys: list[SourceKey],
    esde: dict[str, SourceKey],
    tagged: dict[str, set[str]],
    batocera: dict[str, SourceKey],
) -> None:
    for key in keys:
        table.add_key(column, key)
        if key.key in esde:
            _join_esde_spelling(table, column, key, esde[key.key], tagged)
        elif column != "batocera" and key.key in batocera:
            _join_batocera_spelling(table, column, key, batocera[key.key])
        else:
            table.own_row(column, key)


def _join_esde_spelling(
    table: Table, column: str, key: SourceKey, system: SourceKey, tagged: dict[str, set[str]]
) -> None:
    if not names_agree(key.name, system.name):
        table.findings["names-disagree"].append(
            f"{column} {key.key!r} ({key.name!r}) vs esde {system.key!r} ({system.name!r})"
        )
        table.own_row(column, key)
        return
    reached = _reach(key.platforms, tagged) - {system.key}
    via = "platform-list-broad" if reached else "same-name+platform-list"
    for target in sorted({system.key} | reached):
        table.place(column, key.key, f"esde:{target}", via, f"{key.citation}; {system.citation}")


def _join_batocera_spelling(table: Table, column: str, key: SourceKey, other: SourceKey) -> None:
    if not names_agree(key.name, other.name) or set(key.platforms) != set(other.platforms):
        table.findings["names-or-platforms-disagree (batocera)"].append(
            f"{column} {key.key!r} ({key.name!r}, {key.platforms}) vs batocera "
            f"({other.name!r}, {other.platforms})"
        )
        table.own_row(column, key)
        return
    for row in table.rows_of("batocera", other.key):
        table.place(
            column,
            key.key,
            row,
            "same-name+platform-list (batocera)",
            f"{key.citation}; {other.citation}",
        )


def build_romm(
    table: Table,
    slugs: list[SourceKey],
    aliases: dict[str, tuple[str, int]],
    cite: str,
) -> None:
    """The slug and folder columns through RomM's own ``resolve_platform_slug``."""
    folder_rows: dict[str, tuple[list[str], str]] = {}
    for slug in slugs:
        folder_rows[slug.key] = (_first_rows(table, slug.key, DEPLOYMENTS), slug.citation)
    for folder, (target, line) in aliases.items():
        folder_rows[folder] = (_first_rows(table, folder, (*DEPLOYMENTS, *BATOCERA_FAMILY)), f"{cite}:{ROMM_ALIASES}:{line}")
    resolves = {folder: aliases[folder][0] if folder in aliases else folder for folder in folder_rows}
    for slug in slugs:
        table.add_key("romm", slug)
        reached = [
            (row, folder_rows[folder][1])
            for folder, target in sorted(resolves.items())
            if target == slug.key
            for row in folder_rows[folder][0]
        ]
        if not reached:
            table.own_row("romm", slug)
            continue
        for row, citation in reached:
            # A folder spelled as its slug cites the slug's own line: once, not twice.
            table.place("romm", slug.key, row, "romm-resolve", "; ".join(dict.fromkeys((slug.citation, citation))))
    for folder, (rows, citation) in sorted(folder_rows.items()):
        table.add_key("romm-folder", SourceKey(folder, citation))
        target = resolves[folder]
        if not rows:
            rows = table.rows_of("romm", target)
        for row in rows:
            table.place("romm-folder", folder, row, "romm-resolve", citation)


DEPLOYMENTS = ("esde", "retrodeck", "emudeck")


def _first_rows(table: Table, folder: str, columns: tuple[str, ...]) -> list[str]:
    """The rows of the first of *columns* that holds *folder* as a key."""
    for column in columns:
        rows = table.rows_of(column, folder)
        if rows:
            return rows
    return []


def build_libretro(
    table: Table,
    systems: list[SourceKey],
    databases: dict[str, tuple[tuple[str, ...], int]],
) -> None:
    """``database`` names onto the ES-DE systems whose every core declares them."""
    for core, (names, line) in sorted(databases.items()):
        for name in names:
            table.add_key("libretro", SourceKey(name, f"libretro-core-info:{core}_libretro.info:{line}"))
    for system in systems:
        named = [
            (so.removesuffix("_libretro.so"), line) for command, line in system.commands if (so := core_so(command))
        ]
        for core, line in named:
            if core not in databases:
                table.findings["core-without-info"].append(
                    f"{system.key}: {core}_libretro.so (es-de:{ESDE_SYSTEMS_PATH}:{line}) has no .info at the pin"
                )
            elif not databases[core][0]:
                table.findings["core-declares-no-database"].append(
                    f"{system.key}: {core}_libretro.info declares no database (es-de:{ESDE_SYSTEMS_PATH}:{line})"
                )
        cores = [(core, line) for core, line in named if core in databases]
        declaring = [(core, line) for core, line in cores if databases[core][0]]
        if not declaring:
            continue
        shared = set.intersection(*(set(databases[core][0]) for core, _ in declaring))
        for name in sorted(shared):
            citation = "; ".join(
                f"es-de:{ESDE_SYSTEMS_PATH}:{line} -> libretro-core-info:{core}_libretro.info:"
                f"{databases[core][1]}"
                for core, line in declaring
            )
            table.place("libretro", name, f"esde:{system.key}", "core-database (every core)", citation)


def corroborating_items(items: dict[str, WikidataItem]) -> dict[tuple[str, str], list[WikidataItem]]:
    """``(column, value)`` → the Wikidata items that carry the value, the second citation."""
    found: dict[tuple[str, str], list[WikidataItem]] = defaultdict(list)
    for item in items.values():
        for column in WIKIDATA_PROPERTIES:
            for value in item.values[column]:
                found[(column, value)].append(item)
    return found


def _with_wikidata(
    table: Table, citation: str, column: str, value: str, corroboration: dict[tuple[str, str], list[WikidataItem]]
) -> str:
    """*citation* with the Wikidata statements that carry the same value, or a finding that none does."""
    carriers = corroboration.get((column, value), [])
    if not carriers:
        table.findings[f"not-on-wikidata ({column})"].append(f"{value}: {citation}")
        return citation
    return "; ".join([citation, *(_statement(item, column, value) for item in carriers)])


def build_esde_ids(
    table: Table,
    tagged: dict[str, set[str]],
    maps: dict[str, dict[str, tuple[str, int]]],
    corroboration: dict[tuple[str, str], list[WikidataItem]],
) -> None:
    """ScreenScraper and TheGamesDB ids from ES-DE's scraper maps, onto the tag's systems (D21)."""
    paths = {"screenscraper": ESDE_SS_CPP, "thegamesdb": ESDE_TGDB_CPP}
    for column, mapping in maps.items():
        for tag, (value, line) in sorted(mapping.items()):
            citation = f"es-de:{paths[column]}:{line} ({tag})"
            table.keys[column].setdefault(value, citation)
            cited = _with_wikidata(table, citation, column, value, corroboration)
            for system in sorted(tagged.get(tag, ())):
                table.place(column, value, f"esde:{system}", "esde-scraper-map", cited)


def build_romm_ids(
    table: Table,
    igdb: dict[str, tuple[str, str, int]],
    screenscraper: dict[str, tuple[str, int]],
    corroboration: dict[tuple[str, str], list[WikidataItem]],
) -> None:
    """IGDB ids (and RomM's ScreenScraper ids) from RomM's provider tables, onto the slug's rows (D21).

    An IGDB record gives two keys on the same rows: the numeric id — the stable
    one, IGDB re-spells slugs under an unchanged id — and the slug the record
    states at the pin. Wikidata's P5795 holds slugs, so the slug is what it is
    checked against.
    """
    for slug, (number, igdb_slug, line) in sorted(igdb.items()):
        citation = f"romm:{ROMM_IGDB}:{line} (id {number}, slug {igdb_slug})"
        cited = _with_wikidata(table, citation, "igdb", igdb_slug, corroboration)
        for key in (number, igdb_slug):
            table.keys["igdb"].setdefault(key, citation)
            for row in table.rows_of("romm", slug):
                table.place("igdb", key, row, "romm-igdb-list", cited)
    for slug, (number, line) in sorted(screenscraper.items()):
        citation = f"romm:{ROMM_SS}:{line} ({slug})"
        table.keys["screenscraper"].setdefault(number, citation)
        cited = _with_wikidata(table, citation, "screenscraper", number, corroboration)
        for row in table.rows_of("romm", slug):
            table.place("screenscraper", number, row, "romm-ss-list", cited)


def _statement(item: WikidataItem, column: str, value: str) -> str:
    return f"wikidata:{item.qid} {WIKIDATA_PROPERTIES[column]}={value} (revision {item.revision})"


def unplaced_rows(table: Table) -> None:
    """A row of its own for every key no join placed (D5)."""
    for column in ("esde-platform", "libretro", "igdb", "screenscraper", "thegamesdb"):
        for key, citation in sorted(table.keys[column].items()):
            if not table.rows_of(column, key):
                table.own_row(column, SourceKey(key, citation))


def assign_kinds(table: Table, family: dict[str, list[SourceKey]]) -> None:
    """Every Batocera-family key's stated kind, kept per key for the loader (D16).

    A kind is a fact a catalogue states about its own key; the loader gives a
    row the kind of the keys that end up on it, whatever join or override put
    them there, so the statement is kept with the key rather than the row.
    """
    for column in BATOCERA_FAMILY:
        for key in family[column]:
            kind = HARDWARE_KINDS.get(key.hardware)
            if kind is not None:
                table.kinds[column][key.key] = {
                    "kind": kind,
                    "citation": f"{key.citation} (hardware: {key.hardware})",
                }


# --- output -----------------------------------------------------------------


def mappings(table: Table) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for (column, key), placements in sorted(table.placements.items()):
        relation = "exactMatch" if len(placements) == 1 else "broadMatch"
        version = column_runs_from(column)
        for placement in sorted(placements, key=lambda p: p.row):
            out.append(
                {
                    "source": column,
                    "key": key,
                    "row": placement.row,
                    "relation": relation,
                    "via": placement.via,
                    "citation": placement.citation,
                    "valid_from": version,
                    "valid_to": None,
                }
            )
    return out


def payload(table: Table) -> dict[str, Any]:
    used = {row for (_, _), ps in table.placements.items() for p in ps for row in [p.row]}
    return {
        "schema": TABLE_SCHEMA,
        "spec": (
            "DESIGN.md — Vocabulary; issue #784 (D1–D30), cut #785. Rows are internal "
            "working names (D4) and never leave the loader; the overrides in "
            "platform_overrides.json are applied over this file by the loader (D2). "
            "Generated by scripts/generate_platform_table.py from the pins below; "
            "licences and decisions per upstream in SOURCES.md."
        ),
        "table_version": TABLE_VERSION,
        "sources": {
            column: [
                {"upstream": upstream.block, "version": upstream.version, "revision": upstream.revision}
                for upstream in upstreams
            ]
            for column, upstreams in COLUMNS.items()
        },
        "rows": sorted(row for row in table.rows if row in used),
        "kinds": {column: dict(sorted(table.kinds[column].items())) for column in BATOCERA_FAMILY},
        "keys": {column: dict(sorted(table.keys[column].items())) for column in COLUMNS},
        "mappings": mappings(table),
    }


def build(fetcher: Fetcher) -> Table:
    table = Table()
    esde_text = fetcher.text(f"{ESDE.raw}/{ESDE_SYSTEMS_PATH}")
    systems = esde_systems(esde_text, f"es-de:{ESDE_SYSTEMS_PATH}")
    names, symbols = esde_platform_ids(
        fetcher.text(f"{ESDE.raw}/{ESDE_PLATFORM_CPP}"), fetcher.text(f"{ESDE.raw}/{ESDE_PLATFORM_H}")
    )
    tagged = build_esde(table, systems, names, "es-de")
    esde_by_name = {s.key: s for s in systems}

    rd_text = fetcher.text(f"{RETRODECK.raw}/{ESDE_SYSTEMS_PATH}")
    rd_cite = f"retrodeck-es-de:{ESDE_SYSTEMS_PATH}"
    build_deployment(
        table,
        "retrodeck",
        esde_systems(rd_text, rd_cite) + commented_systems(rd_text, rd_cite),
        esde_by_name,
    )
    build_deployment(table, "emudeck", emudeck_folders(fetcher), esde_by_name)

    family = {
        "batocera": yaml_systems(fetcher.text(f"{BATOCERA.raw}/{BATOCERA_YML}"), f"batocera:{BATOCERA_YML}"),
        "knulli": yaml_systems(fetcher.text(f"{KNULLI.raw}/{KNULLI_YML}"), f"knulli:{KNULLI_YML}"),
        "rocknix": rocknix_systems(rocknix_files(fetcher), f"rocknix:{ROCKNIX_DIR}"),
    }
    batocera_by_key = {k.key: k for k in family["batocera"]}
    for column in BATOCERA_FAMILY:
        build_batocera_family(table, column, family[column], esde_by_name, tagged, batocera_by_key)

    slugs_text = fetcher.text(f"{ROMM.raw}/{ROMM_SLUGS}")
    aliases = romm_aliases(fetcher.text(f"{ROMM.raw}/{ROMM_ALIASES}"), romm_members(slugs_text))
    build_romm(table, romm_slugs(slugs_text, f"romm:{ROMM_SLUGS}"), aliases, "romm")

    build_libretro(table, systems, core_databases(fetcher.bytes(CORE_INFO.raw)))

    maps = {
        "screenscraper": scraper_map(
            fetcher.text(f"{ESDE.raw}/{ESDE_SS_CPP}"), "screenscraper_platformid_map", symbols
        ),
        "thegamesdb": scraper_map(
            fetcher.text(f"{ESDE.raw}/{ESDE_TGDB_CPP}"), "gamesdb_new_platformid_map", symbols
        ),
    }
    pins = json.loads(WIKIDATA_PINS.read_text(encoding="utf-8"))["items"]
    corroboration = corroborating_items(wikidata_items(fetcher, pins))
    build_esde_ids(table, tagged, maps, corroboration)
    members = romm_members(slugs_text)
    build_romm_ids(
        table,
        romm_igdb(fetcher.text(f"{ROMM.raw}/{ROMM_IGDB}"), members),
        romm_screenscraper(fetcher.text(f"{ROMM.raw}/{ROMM_SS}"), members),
        corroboration,
    )
    unplaced_rows(table)
    assign_kinds(table, family)
    return table


def emudeck_folders(fetcher: Fetcher) -> list[SourceKey]:
    """The ``roms/`` template folders, each cited to its folder and to its ``systems.txt`` line where it has one."""
    listing = fetcher.json(EMUDECK_ROMS_API)
    lines: dict[str, tuple[str, int]] = {}
    text = fetcher.text(f"{EMUDECK.raw}/{EMUDECK_SYSTEMS_TXT}")
    for number, line in enumerate(text.splitlines(), start=1):
        folder, colon, name = line.partition(":")
        if colon and folder:
            lines[folder.strip()] = (name.strip(), number)
    keys: list[SourceKey] = []
    for entry in sorted(listing, key=lambda e: e["name"]):
        if entry["type"] != "dir":
            continue
        name, number = lines.get(entry["name"], ("", 0))
        citation = f"emudeck:roms/{entry['name']}/"
        if number:
            citation += f"; emudeck:{EMUDECK_SYSTEMS_TXT}:{number}"
        keys.append(SourceKey(entry["name"], citation, name=name))
    return keys


def rocknix_files(fetcher: Fetcher) -> dict[str, str]:
    listing = fetcher.json(ROCKNIX_API)
    return {
        entry["name"]: fetcher.text(entry["download_url"])
        for entry in listing
        if entry["name"].endswith(".conf")
    }


def resolve_path(raw: str, what: str) -> Path:
    """*raw* resolved, symlinks followed, and refused unless it lies under one of three roots.

    The generator writes the cache and the findings where the command line
    says, so both are bounded to three roots: the repository, the user's home
    directory, and the system's temporary directory as ``tempfile`` names it
    (``TMPDIR`` moves it). A mistyped argument cannot write anywhere else.
    Raises ``ValueError`` for the caller to report as an argument error.
    """
    path = Path(raw).resolve()
    roots = (ROOT.resolve(), Path.home().resolve(), Path(tempfile.gettempdir()).resolve())
    if not any(path.is_relative_to(root) for root in roots):
        raise ValueError(f"{what} {path} lies outside the repository, the home directory and the temporary directory")
    return path


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument(
        "--cache",
        help="keep fetched files here between runs (inside the repository, the home or the temporary directory)",
    )
    parser.add_argument(
        "--findings",
        help="write the join findings here (inside the repository, the home or the temporary directory)",
    )
    parser.add_argument(
        "--repin-wikidata", action="store_true", help="pin Wikidata's current item revisions, then stop"
    )
    args = parser.parse_args(argv)
    # Resolved before anything runs, in every mode: a path is refused whether or not this run writes to it.
    try:
        cache = resolve_path(args.cache, "--cache") if args.cache else None
        findings = resolve_path(args.findings, "--findings") if args.findings else None
    except ValueError as error:
        parser.error(str(error))
    if args.repin_wikidata:
        text = json.dumps(repin_wikidata(Fetcher(None)), indent=1) + "\n"
        WIKIDATA_PINS.write_text(text, encoding="utf-8")
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]
        print(f"pinned {WIKIDATA_PINS.name}; record revision {digest} in SOURCES.md's wikidata block")
        return
    check_sources(SOURCES_MD.read_text(encoding="utf-8"))
    table = build(Fetcher(cache))
    data = payload(table)
    OUTPUT.write_text(json.dumps(data, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    if findings is not None:
        findings.write_text(json.dumps(table.findings, indent=1, ensure_ascii=False) + "\n")
    print(
        f"wrote {OUTPUT.name}: {len(data['rows'])} rows, {len(data['mappings'])} mappings, "
        + ", ".join(f"{c} {len(k)}" for c, k in data["keys"].items())
    )
    for line in finding_counts(table.findings):
        print(line)


def finding_counts(findings: dict[str, list[str]]) -> list[str]:
    """One line per kind of finding with its count — printed on every run; ``--findings`` writes the detail."""
    return [f"finding {kind}: {len(entries)}" for kind, entries in sorted(findings.items())]


if __name__ == "__main__":
    main()
