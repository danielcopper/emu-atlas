"""Firmware — which emulator wants which file, where it goes, and what lies there.

Firmware knowledge splits exactly along atlas's boundary rule, and this module
is where the split is made visible:

- **Which files a core wants is on the machine.** RetroArch ships a ``.info``
  file next to every core, and it declares that core's firmware
  (``firmwareN_path`` / ``firmwareN_desc`` / ``firmwareN_opt``) and its
  ``systemname``. So the declarations are *read*, live, from the installation's
  own ``libretro_info_path`` — never from a shipped table that drifts against
  the cores actually installed.
- **What a correct file's bytes are is written nowhere on the machine.** The
  ``md5``/``sha1``/``size`` triple comes from libretro-database's
  ``System.dat``, and stays a packaged, versioned, source-cited lookup
  (``data/firmware_hashes.json``). "No hash known" is a normal state, not an
  edge case: ``System.dat`` covers only part of the firmware universe.

The model is **emulator-centric**, because a firmware requirement is a property
of an emulator and of nothing else::

    System            snes, dreamcast, gb
      └─ Emulator     mgba, sameboy, flycast   ← decides BOTH the demand AND the place
           ├─ need         required | optional
           ├─ path         absolute destination + expected file name
           └─ identity     expected bytes — judges what is actually lying there

The hash is not a level above the name: the *emulator* decides the name and the
path, the *hash* decides whether what sits there is the right thing. Two cores
on one machine routinely want the same bytes under different names — gambatte
asks for ``gb_bios.bin``, SameBoy for ``dmg_boot.bin``, and the packaged table
carries both under one md5. A byte-identical file under the *other* name does
**not** satisfy the requirement (SameBoy opens ``dmg_boot.bin`` and nothing
else), so ``missing`` stays ``missing`` — what the shared identity buys is a
better instruction: copy what you already have instead of downloading it.

Two axes, kept apart on purpose (:class:`FirmwareRequirement`):

- ``need`` — ``required`` or ``optional``, straight from ``firmwareN_opt``.
  It says what an emulator asks for, never what is on disk.
- ``present`` / ``checked`` — what the machine answers. ``checked`` keeps
  **four** values: ``verified`` and ``mismatch`` are results, ``unchecked``
  means the identity is known but verification was not asked for, and
  ``unknown`` means it cannot be established at all. "We did not look" and "we
  looked and cannot tell" must never collapse into one value.

The invariant that ties the two together: ``checked is None`` exactly when
``present is not True`` — nothing at the destination, or a look that did not
happen, leaves nothing to check.

*Unknown* is deliberately not a ``need``. Having no declaration to check
against is a property of the answer, not of a file, so it is a
:class:`~atlas.placement.Caveat` attached to an answer whose requirement list
is **empty**. A caller that ignores caveats then gets an empty list rather than
a satisfied one: empty is honest, "nothing missing" would be a lie. Which
caveat says *which kind* of empty this is, and there are three
(:func:`_empty_answer_caveat`): :data:`CAVEAT_NO_FIRMWARE_DECLARATION` —
nothing declares it and that was read; :data:`CAVEAT_NO_FIRMWARE_REQUIREMENT` —
declarations exist and none became a requirement;
:data:`CAVEAT_FIRMWARE_DECLARATION_UNKNOWN` — what is declared could not be
established at all.
"""

from __future__ import annotations

import importlib.resources
import json
import os
import re
from dataclasses import dataclass
from glob import escape as _glob_escape
from typing import Any, Literal, Mapping

from atlas.core_info import FirmwareSlot, enumerate_firmware, parse_core_info
from atlas.esde import KIND_LIBRETRO
from atlas.machine import (
    DIGEST_MD5,
    DIGEST_SHA1,
    KIND_DIRECTORY,
    KIND_FILE,
    KIND_INACCESSIBLE,
    KIND_MISSING,
    READ_OK,
    SYMLINK_HOPS,
    Machine,
    PathKind,
    ReadStatus,
)
from atlas.oddities import SaveMode, load_oddities
from atlas.placement import ROOT_SYSTEM_DIRECTORY, UNRESOLVED_STANDALONE, Caveat
from atlas.retroarch_cfg import cfg_uint

FirmwareNeed = Literal["required", "optional"]

NEED_REQUIRED: FirmwareNeed = "required"
NEED_OPTIONAL: FirmwareNeed = "optional"

FIRMWARE_NEEDS = ("required", "optional")

FirmwareChecked = Literal["verified", "mismatch", "unchecked", "unknown"]

CHECKED_VERIFIED: FirmwareChecked = "verified"
CHECKED_MISMATCH: FirmwareChecked = "mismatch"
CHECKED_UNCHECKED: FirmwareChecked = "unchecked"
CHECKED_UNKNOWN: FirmwareChecked = "unknown"

FIRMWARE_CHECKED = ("verified", "mismatch", "unchecked", "unknown")

# Caveat codes — stable identifiers, like the placement ones.
# The three below are the answer-level vocabulary for an empty requirement
# list, and they are three because an empty answer has three reasons: nothing
# is declared, something is declared that never became a requirement, or atlas
# could not establish what is declared at all. One code for all three would let
# a read that did not happen read as "nothing needed" (:func:`_empty_answer_caveat`).
CAVEAT_NO_FIRMWARE_DECLARATION = "no-firmware-declaration"
CAVEAT_NO_FIRMWARE_REQUIREMENT = "no-firmware-requirement"
CAVEAT_FIRMWARE_DECLARATION_UNKNOWN = "firmware-declaration-unknown"
CAVEAT_INFO_PATH_UNRESOLVED = "info-path-unresolved"
CAVEAT_CORE_DIR_UNRESOLVED = "core-dir-unresolved"
CAVEAT_FIRMWARE_ROOT_MISSING = "firmware-root-missing"
CAVEAT_CORE_NOT_INSTALLED = "core-not-installed"
# One fact, one code on both routes: the placement route answers a standalone
# emulator with the typed Unresolved outcome, the firmware route with this
# caveat, and a client that learned the word on one route reads the other.
CAVEAT_STANDALONE_UNSUPPORTED = UNRESOLVED_STANDALONE
CAVEAT_EMULATOR_CATALOGUE_UNAVAILABLE = "emulator-catalogue-unavailable"
CAVEAT_FIRMWARE_UNREADABLE = "firmware-unreadable"
CAVEAT_FIRMWARE_CONTENT_UNIDENTIFIED = "firmware-content-unidentified"
CAVEAT_SYSTEM_UNKNOWN = "system-unknown"
# The marked-word code: a requirement's ``system`` is one of atlas's own
# spellings (:data:`SYSTEMS_WITHOUT_CATALOGUE_ID`) because no ES-DE build
# declares an id for that machine. The word is exported anyway — refusing to
# answer would hide real firmware — and this code is what keeps it from being
# read as a catalogue id. Same form as ``_unknown``: a token no catalogue
# declares, plus a structured caveat saying why.
CAVEAT_SYSTEM_NOT_IN_CATALOGUE = "system-not-in-catalogue"
CAVEAT_SYSTEM_ASSIGNMENT_DERIVED = "system-assignment-derived"
CAVEAT_CORE_WITHOUT_SYSTEMNAME = "core-without-systemname"
CAVEAT_SYSTEM_ASSIGNMENT_MAY_HIDE_CORES = "system-assignment-may-hide-cores"
CAVEAT_CORE_INFO_UNREADABLE = "core-info-unreadable"
CAVEAT_EMULATOR_CATALOGUE_UNREADABLE = "emulator-catalogue-unreadable"
# Four ways to answer from no full catalogue, and they are four different
# claims: the arrangement has none (unavailable), it has one that could not be
# read (unreadable), atlas has not established where this arrangement keeps
# one (unestablished), and part of its catalogue sits where atlas does not
# open — EmuDeck's ES-DE bundles its es_systems.xml inside the AppImage
# (ES-DE INSTALL.md v3.4.1:1470), so only the on-disk layers were read and
# the enumeration is incomplete (sealed). Only unavailable and unreadable say
# anything about the machine; the other two are about atlas, and a client
# must not read either as an absence. Sealed is also the one of the four that
# may accompany real entries: what the readable layers declare is stated, and
# the caveat says the frontend may declare more.
CAVEAT_EMULATOR_CATALOGUE_UNESTABLISHED = "emulator-catalogue-unestablished"
CAVEAT_EMULATOR_CATALOGUE_SEALED = "emulator-catalogue-sealed"
CAVEAT_FIRMWARE_PATH_OBSTRUCTED = "firmware-path-obstructed"
CAVEAT_FIRMWARE_PATH_INACCESSIBLE = "firmware-path-inaccessible"
CAVEAT_FIRMWARE_SCAN_INCOMPLETE = "firmware-scan-incomplete"
CAVEAT_CORE_ENUMERATION_INCOMPLETE = "core-enumeration-incomplete"
CAVEAT_FIRMWARE_PATH_ESCAPES_ROOT = "firmware-path-escapes-root"
CAVEAT_FIRMWARE_PATH_UNRESOLVABLE = "firmware-path-unresolvable"
CAVEAT_FIRMWARE_PATH_NAMES_NO_FILE = "firmware-path-names-no-file"
# Distinct from CAVEAT_FIRMWARE_ROOT_MISSING, which says the root is not there:
# this one says the configured value cannot name a place at all, so no
# declaration can be resolved against it however well the file exists.
CAVEAT_FIRMWARE_ROOT_UNUSABLE = "firmware-root-unusable"
CAVEAT_FIRMWARE_DECLARATION_UNREAD = "firmware-declaration-unread"
CAVEAT_FIRMWARE_CONTENT_CONTRADICTORY = "firmware-content-contradictory"
# The third member of the identification family: the table does not know this
# content (unidentified), the fields describe no single file (contradictory),
# or — this one — the request never named content at all.
CAVEAT_FIRMWARE_CONTENT_UNSTATED = "firmware-content-unstated"

# The two ``.info`` files libretro ships as templates rather than as cores:
# both declare firmware0_path = "filename.ext" with opt = "true/false". The
# offline generator dropped them implicitly (no core ever matched); a live
# reader has to say so.
TEMPLATE_INFO_STEMS = ("00_example_libretro", "puzzlescript_libretro")


@dataclass(frozen=True, slots=True)
class FirmwareHash:
    """One firmware file's identity, as libretro-database's ``System.dat`` states it.

    ``name`` is the key ``System.dat`` uses, verbatim: usually a bare file name
    (``scph5501.bin``) but sometimes a relative path (``dc/dc_boot.bin``) — the
    upstream data mixes both, so the table does too rather than normalizing
    away information it does not own.
    """

    name: str
    md5: str
    sha1: str
    size: int


@dataclass(frozen=True, slots=True)
class FirmwareIdentity:
    """What one firmware content *is*: its bytes, and every name it goes by.

    ``known_as`` is the alias set — every key in the packaged table carrying
    exactly these bytes, the queried name included. 18 of the table's 369
    distinct contents are known under more than one name (``dmg_boot.bin`` ≡
    ``gb_bios.bin``, ``dc/boot.bin`` ≡ ``dc/dc_boot.bin``, …), which is what
    makes "you already have these bytes, under another name" a statable answer.
    """

    md5: str
    sha1: str
    size: int
    known_as: tuple[str, ...] = ()


class FirmwareHashes:
    """Read-only view over the packaged hash table — by name and by content."""

    def __init__(self, files: dict[str, FirmwareHash], meta: dict[str, Any]) -> None:
        self._files = files
        self._meta = meta
        contents: dict[tuple[str, str, int], list[str]] = {}
        for name, entry in files.items():
            contents.setdefault(_content_key(entry.md5, entry.sha1, entry.size), []).append(name)
        self._contents = {key: tuple(sorted(names)) for key, names in contents.items()}

    @property
    def meta(self) -> dict[str, Any]:
        """The table's ``_meta`` block (upstream source, version, generation date)."""
        return dict(self._meta)

    def names(self) -> tuple[str, ...]:
        """Every name the table has an identity for, sorted."""
        return tuple(sorted(self._files))

    def get(self, name: str) -> FirmwareHash | None:
        """The identity stored under *name* exactly, or ``None``."""
        return self._files.get(name)

    def _identity(self, entry: FirmwareHash) -> FirmwareIdentity:
        return FirmwareIdentity(
            md5=entry.md5,
            sha1=entry.sha1,
            size=entry.size,
            known_as=self._contents[_content_key(entry.md5, entry.sha1, entry.size)],
        )

    def for_path(self, path: str) -> FirmwareIdentity | None:
        """The identity expected at a declared path — matched by path, then base name.

        Upstream keys 91 of its entries by a relative path and the rest by a
        bare file name, with no base name shared between the two forms, so
        trying both is unambiguous. ``None`` is a normal, expected answer:
        ``System.dat`` covers only part of the firmware universe.
        """
        entry = self._files.get(path) or self._files.get(os.path.basename(path))
        return None if entry is None else self._identity(entry)

    def for_content(
        self, *, md5: str | None = None, sha1: str | None = None, size: int | None = None
    ) -> FirmwareIdentity | None:
        """The identity of *content*, matched by every field the caller supplies.

        Matching is by bytes, not by name, so it answers the download flow:
        whatever the file is called where it came from, its content says what it
        is and :attr:`FirmwareIdentity.known_as` says what to call it here.
        Every supplied field must agree — a caller that knows both digests gets
        no match when they disagree, rather than a coin-flip.
        """
        if md5 is None and sha1 is None:
            raise ValueError("for_content: at least one of md5/sha1 is needed — size alone is not an identity")
        for (entry_md5, entry_sha1, entry_size), names in self._contents.items():
            if md5 is not None and entry_md5 != md5.lower():
                continue
            if sha1 is not None and entry_sha1 != sha1.lower():
                continue
            if size is not None and entry_size != size:
                continue
            return self._identity(self._files[names[0]])
        return None

    def contradicts_itself(
        self, *, md5: str | None = None, sha1: str | None = None, size: int | None = None
    ) -> bool:
        """Did :meth:`for_content` miss because the *fields* disagree, not the content?

        A caller passing an md5 and a sha1 from two different files gets no
        match — and reporting that as "unknown content" points them at the
        table when the problem is the request.

        The question is asked of the **digests**, because they alone identify:
        when the table knows a supplied md5 or sha1 and the full request still
        matches nothing, the entry that digest names carries a different value
        for something else the caller supplied. That is the caller's request
        disagreeing with itself, whether the field that disagrees is the other
        digest or the size — a size the table pairs with no entry at all is
        exactly the case, and asking "does any entry have this size" instead
        answered ``False`` there and blamed the table for an md5 it knows
        perfectly.

        Unknown digests are not a contradiction: content the table does not
        cover is a normal answer, and a size that happens to match some entry
        says nothing about it.
        """
        if sum(value is not None for value in (md5, sha1, size)) < 2:
            return False
        if md5 is not None and self.for_content(md5=md5) is not None:
            return True
        return sha1 is not None and self.for_content(sha1=sha1) is not None


def _content_key(md5: str, sha1: str, size: int) -> tuple[str, str, int]:
    return (md5.lower(), sha1.lower(), size)


def _hash_from_raw(name: str, raw: dict[str, Any]) -> FirmwareHash:
    md5, sha1, size = raw.get("md5"), raw.get("sha1"), raw.get("size")
    if not isinstance(md5, str) or not isinstance(sha1, str) or not isinstance(size, int):
        raise ValueError(f"{name}: an entry must carry string 'md5'/'sha1' and integer 'size'")
    return FirmwareHash(name=name, md5=md5, sha1=sha1, size=size)


def load_hashes(text: str | None = None) -> FirmwareHashes:
    """Load the packaged hash table (or *text* when supplied, for tests).

    With no argument the bundled ``data/firmware_hashes.json`` is read from the
    installed package. This is the one firmware read that does **not** go
    through the machine seam, and deliberately so: it is the library reading
    its own bundled world knowledge. Everything about *which* files are wanted
    comes from the machine instead (:func:`read_core_declarations`).
    """
    if text is None:
        text = importlib.resources.files("atlas").joinpath("data", "firmware_hashes.json").read_text(encoding="utf-8")
    data = json.loads(text)
    raw_files: dict[str, dict[str, Any]] = data.get("files", {})
    files = {name: _hash_from_raw(name, raw) for name, raw in raw_files.items()}
    return FirmwareHashes(files, data.get("_meta", {}))


# Libretro ``systemname`` strings mapped into atlas's one system vocabulary —
# ES-DE's ids, the same names every other question takes (``atlas.systems``,
# pinned to RetroDECK 0.10.9b's linux ``es_systems.xml``). World knowledge: the
# strings are read live from the machine, but what they *mean* is written
# nowhere on it, so the table is versioned and source-cited like the packaged
# data files it stands beside. Multiple variants map to one id because
# libretro's naming changed over the years.
#
# Two guards hold the table to its target: every value is an ES-DE id or a
# declared entry of :data:`SYSTEMS_WITHOUT_CATALOGUE_ID`, and the evidence
# joins recorded for the re-pointed entries are re-run against the deployed
# ``es_systems.xml`` (tests/test_firmware.py). Citations below name that file
# by line and the shipped ``.info`` files under the Flatpak's
# ``retroarch/rd_extras/cores/``.
#
# One thing this table never claims is a launch: a core's ``.info`` firmware
# list is display knowledge — at the RetroArch pin (``a79435a``, see
# docs/research/retrodeck-save-placement.md) it is evaluated only by display
# surfaces (``menu_displaylist.c:880`` and ``ui_qt.cpp:1238``, of which the
# Qt-less binary RetroDECK ships reaches only the first), and
# ``task_content.c``/``runloop.c`` reference none of it — so filing a file
# under an id says which system's firmware it is, never that some launch
# checks for it.
SYSTEMNAME_MAP_VERSION = "2"  # "1" was the unversioned libretro-slug generation
SYSTEMNAME_MAP_REVIEWED = "2026-08-09"

SYSTEMNAME_TO_SLUG: Mapping[str, str] = {
    # PlayStation
    "Sony - PlayStation": "psx",
    "PlayStation": "psx",
    "Sony PlayStation 2": "ps2",
    "PSP": "psp",
    # Sega. es_systems.xml:616 "dreamcast" (Sega Dreamcast) launches
    # flycast_libretro (:620); flycast_gles2 and retrodream carry the same
    # systemname without a catalogue launch under it.
    "Sega - Dreamcast": "dreamcast",
    "Sega Dreamcast": "dreamcast",
    "Sega - Saturn": "saturn",
    "Saturn": "saturn",
    "Sega - Mega-CD - Sega CD": "segacd",
    # es_systems.xml:1102 "mastersystem" (Sega Master System) launches
    # gearsystem_libretro and smsplus_libretro; emux_sms carries "Sega Master
    # System" too, without a catalogue launch under it. Ruled over "mark3"
    # (:1085), the same hardware's JP id — one id for the hardware, the J
    # BIOS dump included.
    "Sega - Master System - Mark III": "mastersystem",
    "Sega Master System": "mastersystem",
    "Sega 8-bit": "mastersystem",
    "Sega 8-bit (MS/GG/SG-1000)": "mastersystem",
    # es_systems.xml:810 "gamegear" (Sega Game Gear); its command :814
    # launches genesis_plus_gx — the core whose bios.gg the override files
    # here — alongside gearsystem (:816) and smsplus (:817).
    "Sega - Game Gear": "gamegear",
    "Sega - Mega Drive - Genesis": "genesis",
    "Sega Genesis": "genesis",
    "Sega 8/16-bit (Various)": "genesis",
    "Sega 8/16-bit + 32X (Various)": "genesis",
    # Nintendo
    "Nintendo - Game Boy": "gb",
    "Nintendo - Game Boy Color": "gbc",
    "Game Boy/Game Boy Color": "gb",
    "Nintendo - Game Boy Advance": "gba",
    "Game Boy Advance": "gba",
    "Game Boy/Game Boy Color/Game Boy Advance": "gba",
    "Nintendo - Nintendo DS": "nds",
    "Nintendo DS": "nds",
    "Nintendo - Famicom Disk System": "fds",
    "Nintendo - Nintendo Entertainment System": "nes",
    "Nintendo Entertainment System": "nes",
    "Nintendo 64": "n64",
    "Super Nintendo Entertainment System": "snes",
    "Super Nintendo Entertainment System / Game Boy / Game Boy Color": "snes",
    "GameCube / Wii": "gc",
    # Atari. es_systems.xml:301 "atarilynx" (Atari Lynx) launches all three
    # shipped carriers of "Lynx" (handy, holani, mednafen_lynx).
    "Atari - Lynx": "atarilynx",
    "Lynx": "atarilynx",
    "Atari - 5200": "atari5200",
    "Atari 5200": "atari5200",
    "Atari - 7800": "atari7800",
    "Atari 7800": "atari7800",
    "Atari - 8-bit": "atari800",
    "Atari 8-bit Family": "atari800",
    "Atari - ST": "atarist",
    "Atari ST/STE/TT/Falcon": "atarist",
    # NEC. Every carrier of these systemnames that declares firmware declares
    # exactly the CD system cards (syscard*.pce, gexpress.pce — the
    # mednafen_pce, mednafen_pce_fast and mednafen_supergrafx .info files;
    # geargrafx carries "PC Engine/SuperGrafx" and declares none), so the
    # ruled id is what the files are for: es_systems.xml:1600 "pcenginecd"
    # (NEC PC Engine CD), launching mednafen_pce and mednafen_pce_fast.
    # supergrafx launches mednafen_supergrafx (:2071), mednafen_pce (:2072)
    # and geargrafx (:2073); tg16 launches mednafen_pce (:2142),
    # mednafen_pce_fast (:2143) and mednafen_supergrafx (:2144).
    "NEC - PC Engine - TurboGrafx 16": "pcenginecd",
    "PC Engine/PCE-CD": "pcenginecd",
    "PC Engine SuperGrafx": "pcenginecd",
    "PC Engine/SuperGrafx": "pcenginecd",
    "PC Engine/SuperGrafx/CD": "pcenginecd",
    "PC-FX": "pcfx",
    "PC-98": "pc98",
    # SNK
    "SNK - Neo Geo": "neogeo",
    "Neo Geo": "neogeo",
    "SNK Neo Geo CD": "neogeocd",
    # Commodore
    "Commodore - Amiga": "amiga",
    "Amiga": "amiga",
    "C64": "c64",
    "C64 SuperCPU": "c64",
    "C64DTV": "c64",
    # No ES-DE build declares a c128 id; the catalogue files the C128 under
    # c64 itself — es_systems.xml:354 "c64" launches vice_x128 (:362) — and
    # vice_x128's own database says "Commodore - 64".
    "C128": "c64",
    # NOT the Commodore: ep128emu_core's systemname, and its firmware descs
    # say what it is ("exos21.rom (Enterprise 128 Expandible OS 2.1)", …).
    # No catalogue id exists — SYSTEMS_WITHOUT_CATALOGUE_ID entry "ep128".
    "128": "ep128",
    # Other
    "Coleco - ColecoVision": "colecovision",
    "ColecoVision": "colecovision",
    "ColecoVision/CreatiVision/My Vision": "colecovision",
    "Microsoft - MSX": "msx",
    "MSX": "msx",
    "MSX/SVI/ColecoVision/SG-1000": "msx",
    "Amstrad - CPC": "amstradcpc",
    "The 3DO Company - 3DO": "3do",
    "3DO": "3do",
    "Magnavox - Odyssey2": "odyssey2",
    "Magnavox Odyssey2 / Philips Videopac+": "odyssey2",
    # es_systems.xml:368 "cdimono1" (Philips CD-i) launches both shipped
    # carriers, same_cdi (:372) and cdi2015 (:373) — whose required file is
    # literally cdimono1.zip.
    "CD-i": "cdimono1",
    "CDi": "cdimono1",
    "Intellivision": "intellivision",
    "DOS": "dos",
    "PC-8000 / PC-8800 series": "pc88",
    "Sharp X1": "x1",
    "Sharp X68000": "x68000",
    "Pokemon Mini": "pokemini",
    # [D] — the one semantic join: es_systems.xml:1032 "macintosh" (Apple
    # Macintosh) launches standalone MAME macse/macplus, not minivmac, but
    # both sides are the 68k Macintosh line (minivmac_libretro.info requires
    # MacII.ROM). Only plausible id; no launching-core witness.
    "Mac68k": "macintosh",
    # Elektronika BK-0010/0011M — no catalogue id exists;
    # SYSTEMS_WITHOUT_CATALOGUE_ID entry "bk" (bk_libretro.info).
    "BK-0010/BK-0011(M)": "bk",
    # TI-83 calculators (numero_libretro.info: ti83.rom/ti83plus.rom/
    # ti83se.rom) — no catalogue id exists; ES-DE's "ti99" is the TI-99/4A
    # home computer, a different machine. SYSTEMS_WITHOUT_CATALOGUE_ID "ti83".
    "TI83": "ti83",
    "Super Cassette Vision": "scv",
    "FreeChaF": "channelf",
    "Vircon32": "vircon32",
    # es_systems.xml:1526 "palm" (Palm OS) launches mu_libretro (:1530), the
    # sole shipped carrier.
    "Palm OS": "palm",
    "CP System I/II": "cps",
    # Multi-system / game engines. es_systems.xml:155 "arcade" launches the
    # carriers of "Arcade (various)" (fbneo, the mame variants, dice) — the
    # umbrella id the catalogue itself files these cores under; per-board
    # refinements are per-file override work.
    "Arcade (various)": "arcade",
    # "Game engine" is scummvm_libretro's systemname and nothing else's;
    # es_systems.xml:1848 "scummvm" launches it (:1852).
    "Game engine": "scummvm",
    # es_systems.xml:1653 "ports" launches ecwolf_libretro (:1659) — the
    # catalogue's own filing, bucket though it is.
    "Wolfenstein 3D Game Engine": "ports",
    # "RPG Maker XP/VX/VX Ace Game Engine" is deliberately unmapped: no
    # shipped .info carries it, so it files mechanically like any other
    # systemname this table does not know.
    "J2ME": "j2me",
    "Java ME": "j2me",
    "ZX Spectrum (various)": "zxspectrum",
}

# Systems that are real on the machine and absent from the catalogue: the
# pinned ES-DE build declares no id for them (guard-tested against the
# deployed es_systems.xml — nothing there spells them, commented blocks
# included). atlas answers with its own spelling rather than hiding real
# firmware, and every answer that uses one carries
# :data:`CAVEAT_SYSTEM_NOT_IN_CATALOGUE` so the word cannot be read as a
# catalogue id. Keys are the spellings; values name the machine for the
# caveat's prose. Same output form as ``_unknown``: a token no catalogue
# declares, plus a structured caveat saying why — only the fact differs
# (here the catalogue lacks the word; there the core states no systemname).
SYSTEMS_WITHOUT_CATALOGUE_ID: Mapping[str, str] = {
    # bk_libretro.info: systemname "BK-0010/BK-0011(M)", eight bk/*.ROM dumps.
    "bk": "Elektronika BK-0010/0011M",
    # numero_libretro.info: ti83.rom / ti83plus.rom / ti83se.rom.
    "ti83": "Texas Instruments TI-83 calculators",
    # ep128emu_core_libretro.info: systemname "128", firmware descs
    # "Enterprise 128 Expandible OS", "Enterprise 64 BASIC", … — the spelling
    # follows the emulator family's own name (ep128emu).
    "ep128": "Enterprise 64/128",
}

# Per-file system overrides for multi-system cores. A core covering several
# systems carries one ``systemname``, but its firmware belongs to different
# systems — mGBA declares the Game Boy boot ROMs under "Game Boy/Game Boy
# Color/Game Boy Advance".
#
# Evidence level [D], derived — **not** [V]. Provenance: each entry is atlas's
# reading of which machine a dump belongs to, cross-read against the platform
# keys in RomM's ``backend/models/fixtures/known_bios_files.json``. There is no
# upstream source that states this per file: libretro's ``.info`` carries one
# ``systemname`` for the whole core and nothing per firmware entry, and
# ``System.dat`` keys identities by name without a system. The disagreements are
# real and this table takes a side: the Super Game Boy dumps (``SGB1.sfc``,
# ``sgb_bios.bin``, …) are filed under ``snes`` here because the cartridge runs
# in an SNES, while RomM files the same bytes under ``super-gb``.
#
# Deliberately incomplete, and not to be completed by hand — the set of boot-ROM
# variants grows with every core release (SkyEmu alone adds ``dmg_rom.bin``,
# ``dmg0_rom.bin``, ``cgb0_boot.bin``, ``cgb_agb_boot.bin``). A declaration this
# table does not cover falls back to the core's ``systemname``, and where that
# fallback can be wrong the answer says so — see
# :func:`system_assignment_caveats`. Visible beats silent.
#
# Entries are added only where the file's machine is not in question, and each
# addition shrinks how often that caveat has to fire. Versioned like the
# packaged data files it stands beside.
FIRMWARE_SYSTEM_OVERRIDE_VERSION = "3"
FIRMWARE_SYSTEM_OVERRIDE_REVIEWED = "2026-08-09"

FIRMWARE_SYSTEM_OVERRIDE: Mapping[str, str] = {
    # Game Boy family (mGBA, VBA-M, Mesen-S, Gambatte, SameBoy, …)
    "gb_bios.bin": "gb",
    "dmg_boot.bin": "gb",
    "gbc_bios.bin": "gbc",
    "cgb_boot.bin": "gbc",
    "sgb_bios.bin": "snes",
    "sgb_boot.bin": "snes",
    "sgb2_boot.bin": "snes",
    "SGB1.sfc": "snes",
    "SGB2.sfc": "snes",
    # Sega CD (Genesis Plus GX, PicoDrive)
    "bios_CD_E.bin": "segacd",
    "bios_CD_U.bin": "segacd",
    "bios_CD_J.bin": "segacd",
    # Master System (Genesis Plus GX). One id for the hardware, the J dump
    # included — ruled mastersystem, not mark3.
    "bios_E.sms": "mastersystem",
    "bios_U.sms": "mastersystem",
    "bios_J.sms": "mastersystem",
    # Game Gear (Genesis Plus GX): "bios.gg (GameGear BIOS)" by its own desc.
    "bios.gg": "gamegear",
    # ColecoVision BIOS declared by smsplus under systemname "Sega 8-bit"
    # (smsplus_libretro.info firmware1: "BIOS.col (Colecovision BIOS)") —
    # without this rule it files under mastersystem.
    "BIOS.col": "colecovision",
    # Flycast's arcade boards. The .info files all of these under systemname
    # "Sega Dreamcast", but each desc names its board (flycast_libretro.info
    # firmware1-7), and the catalogue launches Flycast under the boards' own
    # ids (es_systems.xml:1349 naomi, :1360 naomi2, :333 atomiswave).
    "naomi.zip": "naomi",  # "dc/naomi.zip (Naomi Bios from MAME)"
    "naomi2.zip": "naomi2",  # "dc/naomi2.zip (Naomi2 Bios from MAME)"
    "hod2bios.zip": "naomi",  # "(Naomi The House of the Dead 2 Bios from MAME)"
    "f355dlx.zip": "naomi",  # "(Naomi Ferrari F355 Challenge deluxe Bios from MAME)"
    "f355bios.zip": "naomi",  # "(Naomi Ferrari F355 Challenge twin/deluxe Bios from MAME)"
    "airlbios.zip": "naomi",  # "(Naomi Airline Pilots deluxe Bios from MAME)"
    "awbios.zip": "atomiswave",  # "dc/awbios.zip (Atomiswave BIOS from MAME)"
    # SNES enhancement chips, declared by every bsnes variant and by Snes9x.
    # These are cartridge coprocessor ROMs — they exist only inside SNES
    # cartridges, so the machine is not in question. Ten installed bsnes
    # variants used to carry a derived-assignment caveat for these alone.
    "dsp1.data.rom": "snes",
    "dsp1.program.rom": "snes",
    "dsp1b.data.rom": "snes",
    "dsp1b.program.rom": "snes",
    "dsp2.data.rom": "snes",
    "dsp2.program.rom": "snes",
    "dsp3.data.rom": "snes",
    "dsp3.program.rom": "snes",
    "dsp4.data.rom": "snes",
    "dsp4.program.rom": "snes",
    "cx4.data.rom": "snes",
    "st010.data.rom": "snes",
    "st010.program.rom": "snes",
    "st011.data.rom": "snes",
    "st011.program.rom": "snes",
    "st018.data.rom": "snes",
    "st018.program.rom": "snes",
    # The Satellaview BIOS, an SNES add-on — same vocabulary choice as the
    # Super Game Boy dumps above, as are bsnes's names for the SGB dumps.
    "BS-X.bin": "snes",
    "sgb.boot.rom": "snes",
    "sgb1.boot.rom": "snes",
    "sgb2.boot.rom": "snes",
    "sgb1.program.rom": "snes",
    "sgb2.program.rom": "snes",
    # Famicom Disk System, declared by the NES cores under systemname
    # "Nintendo Entertainment System" — a genuinely wrong assignment without
    # this rule, and the same class of error as 5200.rom below.
    "disksys.rom": "fds",
    # Nintendo DS / DSi, declared by melonDS, DeSmuME and NooDS. The names are
    # DS-specific; the generic "firmware.bin" several of them also declare is
    # deliberately not listed, because a bare name that generic cannot be
    # claimed for one system.
    "bios7.bin": "nds",
    "bios9.bin": "nds",
    "dsi_bios7.bin": "nds",
    "dsi_bios9.bin": "nds",
    "dsi_firmware.bin": "nds",
    "dsi_nand.bin": "nds",
    # Atari 5200 (atari800, whose systemname is "Atari 8-bit Family"). Without
    # this the 5200 BIOS is filed under atari800 and a query for atari5200
    # cannot reach it at all.
    "5200.rom": "atari5200",
}

_NON_SLUG = re.compile(r"[^a-z0-9]+")

# A ``systemname`` that names several machines at once. libretro writes these
# as a slash list, with or without spaces: "Game Boy/Game Boy Color",
# "GameCube / Wii", "Atari ST/STE/TT/Falcon" — the slash carries the whole
# signal, so it is looked for literally; the spacing around it says nothing.
_SEVERAL_SYSTEMS_SEPARATOR = "/"

SystemSource = Literal["override", "systemname", "slug", "none"]

SOURCE_OVERRIDE: SystemSource = "override"
SOURCE_SYSTEMNAME: SystemSource = "systemname"
SOURCE_SLUG: SystemSource = "slug"
SOURCE_NONE: SystemSource = "none"


def system_decision(file_name: str, systemname: str) -> tuple[str, SystemSource]:
    """The system a declared file belongs to, *and how it was arrived at*.

    Four ways, in order: the per-file override (the only one that knows which
    machine a dump belongs to), the ``systemname`` map — both speaking ES-DE
    ids, or a declared own spelling where no id exists — then a mechanical
    slug of an unmapped ``systemname`` (so every declaration stays reachable
    by some word), and — when the ``.info`` states no ``systemname`` at all —
    ``_unknown``.

    The *source* is the point. Everything but ``override`` assigns a file by
    what its whole core is called, which is only sound while the core covers one
    system. :func:`system_assignment_caveats` turns that into a stated caveat
    instead of a silent guess.
    """
    override = FIRMWARE_SYSTEM_OVERRIDE.get(file_name)
    if override is not None:
        return override, SOURCE_OVERRIDE
    known = SYSTEMNAME_TO_SLUG.get(systemname)
    if known is not None:
        return known, SOURCE_SYSTEMNAME
    slug = _NON_SLUG.sub("-", systemname.lower()).strip("-")
    return (slug, SOURCE_SLUG) if slug else ("_unknown", SOURCE_NONE)


def system_for(file_name: str, systemname: str) -> str:
    """The system a declared file belongs to, in atlas's vocabulary."""
    return system_decision(file_name, systemname)[0]


@dataclass(frozen=True, slots=True)
class FirmwareDeclaration:
    """One firmware path, as one installed core's ``.info`` file declares it.

    ``path`` is verbatim from ``firmwareN_path`` — by convention relative to the
    emulator's system directory and possibly carrying subdirectories
    (``dc/dc_boot.bin``); an absolute one is composed with that directory just
    the same (:func:`_join_under`).
    ``need`` is ``firmwareN_opt`` inverted: RetroArch only ever writes that
    flag when it reads as one of its four booleans, over a slot that starts out
    ``false``, so an absent *and* an unreadable flag both mean required
    (:func:`atlas.core_info.enumerate_firmware`).
    """

    path: str
    file_name: str
    description: str
    need: FirmwareNeed
    system: str
    system_source: SystemSource


@dataclass(frozen=True, slots=True)
class CoreDeclarations:
    """One installed core's ``.info`` file, as far as firmware is concerned.

    Every installed core gets one of these — including the ones that declare
    nothing. That is the whole point: "this core is here and wants no firmware"
    is an answer, and it is a different answer from "atlas does not know this
    core", which has no :class:`CoreDeclarations` at all.

    ``database`` is the ``.info`` field of that name, split on ``|``: the
    libretro-database names the core covers. It is read purely as a *signal* —
    it is deliberately not used to assign a system, because it is a different
    vocabulary (``Sinclair - ZX 81`` where ``systemname`` says ``ZX81``), and on
    a real installation 88 of 117 single-entry database names are unknown to the
    ``systemname`` map; leaning on it would mean maintaining a second table of
    the same size.

    ``info_status`` is the read status of the ``.info`` itself. A core whose
    ``.so`` is on disk but whose ``.info`` is missing, unreadable, or not UTF-8
    is still *here* — atlas simply does not know what it wants, which is a state
    of its own and never a silent deletion from the inventory.

    ``firmware_count`` is that field as the ``.info`` spells it and ``unread``
    the ``firmwareN_path`` keys its enumeration left out — together the reason
    ``firmware`` can be shorter than the file looks
    (:func:`atlas.core_info.enumerate_firmware`).
    """

    core_so: str
    stem: str
    systemname: str
    system: str
    firmware: tuple[FirmwareDeclaration, ...]
    database: tuple[str, ...] = ()
    info_status: ReadStatus = READ_OK
    firmware_count: str = ""
    unread: tuple[str, ...] = ()

    @property
    def serves_several_systems(self) -> bool:
        """Does the machine say this core covers more than one system?

        Three independent readings, any of which counts, because each one is
        evidence and missing it means staying silent about a wrong assignment:

        - ``database`` names several systems (mGBA, Genesis Plus GX);
        - ``systemname`` itself names several (``ColecoVision/CreatiVision/My
          Vision``, ``GameCube / Wii``) — the same separator
          :data:`SYSTEMNAME_TO_SLUG` is already keyed on;
        - the two disagree where both are mappable: one source contradicting
          the other is exactly a reason not to trust either blindly. No
          shipped core trips this today — vice_x128 was the case (systemname
          ``C128``, database ``Commodore - 64``) until map version 2 filed the
          C128 under ``c64`` the way the catalogue itself does, turning the
          disagreement into agreement — but a reading that exists is kept:
          the next info set can disagree again.

        Over-firing says "this could be derived" when it is not; staying silent
        says nothing when it is. The first is recoverable, so the reading is
        deliberately generous. A core with a ``systemname`` and no ``database``
        has only one source and is taken at its word — stated here because it is
        an assumption, not a reading.
        """
        if len(self.database) > 1:
            return True
        if _SEVERAL_SYSTEMS_SEPARATOR in self.systemname:
            return True
        if len(self.database) == 1:
            from_database = SYSTEMNAME_TO_SLUG.get(self.database[0])
            from_systemname = SYSTEMNAME_TO_SLUG.get(self.systemname)
            if from_database is not None and from_systemname is not None:
                return from_database != from_systemname
        return False


@dataclass(frozen=True, slots=True)
class _Info:
    """One ``.info`` as far as firmware is concerned — the parse, before the machine."""

    systemname: str
    database: tuple[str, ...]
    firmware: tuple[FirmwareDeclaration, ...]
    firmware_count: str
    unread: tuple[str, ...]


# What a core whose ``.info`` could not be read declares: nothing, and not
# because it wants nothing — ``info_status`` carries that difference.
_UNREADABLE_INFO = _Info("", (), (), "", ())


def _declaration_of(slot: FirmwareSlot, systemname: str) -> FirmwareDeclaration:
    """One enumerated slot as atlas files it — by the file its path names."""
    file_name = os.path.basename(slot.path)
    system, source = system_decision(file_name, systemname)
    return FirmwareDeclaration(
        path=slot.path,
        file_name=file_name,
        description=slot.description,
        need=NEED_OPTIONAL if slot.optional else NEED_REQUIRED,
        system=system,
        system_source=source,
    )


def _declarations_in(text: str) -> _Info:
    """Read one ``.info``: its ``systemname``, its ``database``, and its firmware."""
    fields = parse_core_info(text)
    systemname = fields.get("systemname", "")
    enumeration = enumerate_firmware(fields)
    return _Info(
        systemname=systemname,
        database=tuple(entry for entry in fields.get("database", "").split("|") if entry),
        firmware=tuple(_declaration_of(slot, systemname) for slot in enumeration.slots),
        firmware_count=enumeration.count,
        unread=enumeration.unread,
    )


@dataclass(frozen=True, slots=True)
class CoreEnumeration:
    """The installed cores, and the directory that names them if it could not be read.

    Two fields because an empty ``cores`` is ambiguous on its own: an
    installation that ships none, and one whose core directory could not be
    listed, are opposite facts about the machine and only the first licenses
    saying anything about it. ``unreadable`` is empty exactly when the
    enumeration is trustworthy — the same shape as
    :class:`~atlas.machine.GlobResult`, which is where it comes from.
    """

    cores: tuple[CoreDeclarations, ...] = ()
    unreadable: tuple[str, ...] = ()

    @property
    def listed(self) -> bool:
        """Did the enumeration actually happen?"""
        return not self.unreadable


def read_core_declarations(
    machine: Machine, info_dir: str, *, core_dir: str | None = None
) -> CoreEnumeration:
    """Read what every *installed* core declares, live, from its ``.info`` file.

    When *core_dir* is given the **cores** are the enumeration: every ``.so``
    there is an installed core, and its ``.info`` is what atlas reads *about*
    it. That way a core whose ``.info`` is missing, unreadable, or not UTF-8
    still appears — carrying its read status instead of vanishing from the
    inventory. The other direction matters too: an ``.info`` set routinely
    covers more cores than an installation ships (RetroDECK: 292 ``.info``
    against 211 ``.so``), and firmware demanded by a core that cannot run is
    exactly the noise a shipped table produced.

    With *core_dir* ``None`` there is no core enumeration to be had, so the
    ``.info`` files are the enumeration and nothing is filtered — the caller
    states that gap as a caveat rather than having it silently narrow the
    answer.

    libretro's two template ``.info`` files are dropped by name: they declare
    the literal placeholder ``filename.ext``.

    The answer carries whether the directory that *names* the cores could be
    read, because an empty list means two opposite things: this installation
    ships no cores, or the place they would be named could not be listed. Only
    the first licenses a statement about the machine, and the caller cannot
    tell them apart from the list alone — which is what the old "empty means
    nobody looked" reading got wrong in the other direction, treating a
    genuinely empty directory as a failure.
    """
    directory = info_dir if core_dir is None else core_dir
    suffix = ".info" if core_dir is None else ".so"
    listing = machine.glob(os.path.join(_glob_escape(directory), f"*{suffix}"))
    stems = [os.path.basename(path)[: -len(suffix)] for path in listing.matches]
    cores: list[CoreDeclarations] = []
    for stem in stems:
        if stem in TEMPLATE_INFO_STEMS:
            continue
        result = machine.read_text(os.path.join(info_dir, f"{stem}.info"))
        info = _UNREADABLE_INFO if result.text is None else _declarations_in(result.text)
        cores.append(
            CoreDeclarations(
                core_so=f"{stem}.so",
                stem=stem,
                systemname=info.systemname,
                system=system_for("", info.systemname),
                firmware=info.firmware,
                database=info.database,
                info_status=result.status,
                firmware_count=info.firmware_count,
                unread=info.unread,
            )
        )
    return CoreEnumeration(tuple(sorted(cores, key=lambda c: c.core_so)), listing.unreadable)


def system_assignment_caveats(core: CoreDeclarations) -> tuple[Caveat, ...]:
    """State it when a core's firmware was filed by what the *core* is called.

    Only the per-file override knows which machine a dump belongs to. Every
    other route assigns a file by its core's ``systemname``, which is sound
    exactly while the core covers one system — and the ``.info`` says when it
    does not: ``database`` names every system the core serves.

    Two distinct cases, never one bucket:

    - **No ``systemname`` at all.** SkyEmu ships none, only a ``database``
      naming three systems, so eight of its ten declarations land on
      ``_unknown``. That is not a fallback that might be wrong, it is no
      assignment at all, and it gets its own code.
    - **Fallback on a multi-system core.** The core covers several systems by
      any of the readings in :attr:`CoreDeclarations.serves_several_systems`,
      and at least one file was filed by its single ``systemname``. mGBA's
      ``gba_bios.bin`` goes this way.

    A core whose declarations are all override-assigned states nothing — there
    is nothing uncertain to state. Neither does a core that declares no
    firmware, nor one whose only derived declaration names no file at all
    (``dc/``): that is refused before it is a requirement, and naming the empty
    string here would put a gap in the middle of a list of files.
    """
    caveats: list[Caveat] = []
    derived = tuple(
        d.file_name for d in core.firmware if d.system_source != SOURCE_OVERRIDE and d.file_name
    )
    if derived:
        files = ", ".join(sorted(set(derived)))
        if not core.systemname:
            covers = (
                f"its database field names {len(core.database)} systems ({'|'.join(core.database)})"
                if core.database
                else "and its .info names no database either, so nothing on the machine says what it covers"
            )
            caveats.append(
                Caveat(
                    CAVEAT_CORE_WITHOUT_SYSTEMNAME,
                    f"{core.core_so} states no systemname in its .info, so nothing says which of its systems "
                    f"these files belong to and they are filed as _unknown: {files} — {covers}",
                    {
                        "core_so": core.core_so,
                        "files": files,
                        "database": "|".join(core.database),
                        "table_version": FIRMWARE_SYSTEM_OVERRIDE_VERSION,
                    },
                )
            )
        elif core.serves_several_systems:
            caveats.append(
                Caveat(
                    CAVEAT_SYSTEM_ASSIGNMENT_DERIVED,
                    f"these files' system is inherited from {core.core_so}'s own systemname "
                    f"({core.systemname!r}); the core covers more than one system, and no per-file "
                    f"source is established yet, so the filing may be wrong: {files}",
                    {
                        "core_so": core.core_so,
                        "systemname": core.systemname,
                        "files": files,
                        "database": "|".join(core.database),
                        "table_version": FIRMWARE_SYSTEM_OVERRIDE_VERSION,
                    },
                )
            )
    return tuple(caveats)


def catalogue_vocabulary_caveats(core: CoreDeclarations) -> tuple[Caveat, ...]:
    """State it when a core's firmware is filed under a word no catalogue declares.

    A handful of systems exist on the machines atlas supports and in no ES-DE
    build (:data:`SYSTEMS_WITHOUT_CATALOGUE_ID`). Refusing to answer would
    hide real firmware, so the answer keeps atlas's own spelling — and this
    caveat marks every use of one, so a consumer validating identifiers
    against ``known_systems()`` reads a marked word rather than a membership
    mistake. One caveat per spelling used, naming the files filed under it.
    """
    caveats: list[Caveat] = []
    filed: dict[str, list[str]] = {}
    for declaration in core.firmware:
        if declaration.system in SYSTEMS_WITHOUT_CATALOGUE_ID and declaration.file_name:
            filed.setdefault(declaration.system, []).append(declaration.file_name)
    for system in sorted(filed):
        files = ", ".join(sorted(set(filed[system])))
        caveats.append(
            Caveat(
                CAVEAT_SYSTEM_NOT_IN_CATALOGUE,
                f"no ES-DE system id exists for this system "
                f"({SYSTEMS_WITHOUT_CATALOGUE_ID[system]}); {system!r} is atlas's own spelling — "
                f"{core.core_so} files under it: {files}",
                {
                    "core_so": core.core_so,
                    "system": system,
                    "files": files,
                    # Deliberately not "table_version": the assignment caveats
                    # cite the override table under that key, and this one
                    # cites the systemname map — one key per governing table,
                    # so a consumer reading the number knows what it versions.
                    "map_version": SYSTEMNAME_MAP_VERSION,
                },
            )
        )
    return tuple(caveats)


@dataclass(frozen=True, slots=True)
class FirmwareRequirement:
    """One (core, declared file) pair — the atom of the whole model.

    ``path`` is the **absolute, resolved** destination — where the file really
    lands once the kernel has followed every symlink on the way. It is stated
    whether or not a file is there, because "where does this go" is the question
    a download flow asks, and resolving it means two declarations of the same
    file are one place rather than two (LRPS2's ``pcsx2/bios`` *is* the firmware
    root on RetroDECK, so a placing client would otherwise write two copies).
    ``declared`` keeps the string the core spelled, which is the name it will
    open — nothing is lost by resolving.

    ``need`` says what the core asks for; ``found`` and ``checked`` say what the
    machine holds. ``found`` is the path kind read at the destination and keeps
    all four apart — a directory in the way is not a missing file, and a path
    that could not be looked at is neither. ``checked`` is ``None`` exactly when
    there is nothing at the destination to check, and otherwise keeps its four
    values apart: ``unchecked`` (identity known, verification not asked for) is
    not ``unknown`` (it could not be established), and neither is a verdict.
    """

    core_so: str
    system: str
    system_source: SystemSource
    need: FirmwareNeed
    file_name: str
    path: str
    declared: str
    description: str
    identity: FirmwareIdentity | None
    found: PathKind
    checked: FirmwareChecked | None

    def __post_init__(self) -> None:
        if self.need not in FIRMWARE_NEEDS:
            raise ValueError(f"FirmwareRequirement: need must be one of {FIRMWARE_NEEDS}, got {self.need!r}")
        if self.found not in (KIND_FILE, KIND_DIRECTORY, KIND_MISSING, KIND_INACCESSIBLE):
            raise ValueError(f"FirmwareRequirement: found must be a path kind, got {self.found!r}")
        if self.found in (KIND_FILE, KIND_DIRECTORY):
            if self.checked not in FIRMWARE_CHECKED:
                raise ValueError(
                    f"FirmwareRequirement: something is there, so checked must be one of {FIRMWARE_CHECKED}, "
                    f"got {self.checked!r}"
                )
        elif self.checked is not None:
            raise ValueError("FirmwareRequirement: nothing is there to check, so checked must be None")

    @property
    def present(self) -> bool | None:
        """Is anything at the destination? ``None`` when atlas could not look.

        Derived from :attr:`found`, and deliberately three-valued: "could not
        look" is not "not there".
        """
        if self.found == KIND_INACCESSIBLE:
            return None
        return self.found in (KIND_FILE, KIND_DIRECTORY)

    @property
    def satisfied(self) -> bool | None:
        """Is the right file where this core will look for it?

        ``True`` only when a file is there and atlas *established* that it is
        the right one. ``False`` when nothing is there, or when the bytes are
        known to be wrong — a present file can absolutely fail this, which is
        the whole reason the identity table exists. ``None`` for everything atlas
        did not establish:

        - the path could not be looked at;
        - a directory sits there (something is present, nothing is confirmed);
        - the identity is known and could not be read (unreadable bytes);
        - the identity is known and verification was **not asked for**.

        That last one is the load-bearing case. Without hashing, "a file with
        the right name is there" is all atlas knows, and calling it satisfied
        would be an all-clear it did not earn. It is affordable to say so: a
        verified single-core answer costs 0.03 s and the whole tree 0.8 s on the
        reference machine, so a caller who wants the green light can ask for it.

        A file whose identity the table does not cover stays ``True``: nothing
        further can ever be established about it, so withholding the answer
        would withhold it forever.
        """
        if self.found == KIND_INACCESSIBLE:
            return None
        if self.found == KIND_MISSING:
            return False
        if self.found == KIND_DIRECTORY:
            return None
        if self.checked == CHECKED_MISMATCH:
            return False
        if self.checked == CHECKED_UNCHECKED:
            return None
        if self.checked == CHECKED_UNKNOWN and self.identity is not None:
            return None
        return True


@dataclass(frozen=True, slots=True)
class RefusedDeclaration:
    """A declaration atlas would not follow — it leaves the root, or will not resolve.

    It is not a requirement — there is no destination to state — but it is a
    firmware file this core asked for, so it has to stay visible on the core.
    Otherwise ``unmet`` and ``undetermined`` together would look like the whole
    story while a required file had been quietly dropped.

    ``reason`` is the caveat code that says *why*: leaving the root and being
    unresolvable are different facts about the machine.
    """

    declared: str
    need: FirmwareNeed
    reason: str


CoreDeclarationState = Literal["read", "unreadable", "absent", "unsupported"]

DECLARATION_READ: CoreDeclarationState = "read"
DECLARATION_UNREADABLE: CoreDeclarationState = "unreadable"
DECLARATION_ABSENT: CoreDeclarationState = "absent"
# Not a state of the machine but of atlas's coverage: the emulator is here and
# atlas has no source for what it wants. Spelled the way the placement route
# spells the same fact, because it *is* the same fact asked twice.
DECLARATION_UNSUPPORTED: CoreDeclarationState = "unsupported"

CORE_DECLARATION_STATES = ("read", "unreadable", "absent", "unsupported")


@dataclass(frozen=True, slots=True)
class CoreFirmware:
    """What one emulator wants, resolved against the live firmware root.

    ``declaration`` is the load-bearing field, and it has four values because
    an empty ``requirements`` list has four meanings:

    - ``read`` — atlas read this core's own ``.info``, so an empty list is the
      answer "this core needs no firmware".
    - ``unreadable`` — the core is here, its declaration is not (missing,
      unreadable, or not UTF-8). What it wants is unknown.
    - ``absent`` — no such core here at all. Exactly that, and nothing else:
      a claim about what is installed on this machine.
    - ``unsupported`` — the emulator is here and atlas has no source for what
      it wants, which today means a standalone emulator: it ships no ``.info``,
      and its own rules are outside the resolver's coverage. A claim about
      atlas, not about the machine, and the same one the placement route makes
      with :data:`~atlas.placement.UNRESOLVED_STANDALONE`.

    Only the first makes an empty list mean *complete*; the other three make it
    mean *unknown*, and each carries a caveat saying which. ``unsupported`` and
    the evidence caveats are different axes and never substitute for each
    other: ``arrangement-unverified`` says a reading was never confirmed live,
    while this says there was no reading to confirm.
    """

    core_so: str | None
    label: str | None
    declaration: CoreDeclarationState
    requirements: tuple[FirmwareRequirement, ...]
    caveats: tuple[Caveat, ...]
    refused: tuple[RefusedDeclaration, ...] = ()
    # The ``.info`` keys RetroArch's own enumeration never reaches (declared
    # without a count, or past it). A field rather than a caveat read back out
    # of the answer: whether this core declares something nobody asks for is a
    # fact about the core, and the identification route needs it as data, not
    # as the presence of a message. It stays out of the contract — the caveat
    # that states it already carries the keys, and one fact serialized twice is
    # one fact that can disagree with itself.
    unread: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.declaration not in CORE_DECLARATION_STATES:
            raise ValueError(
                f"CoreFirmware: declaration must be one of {CORE_DECLARATION_STATES}, got {self.declaration!r}"
            )
        if self.declaration != DECLARATION_READ and self.requirements:
            raise ValueError("CoreFirmware: requirements can only come from a declaration that was read")
        if self.declaration != DECLARATION_READ and not self.caveats:
            raise ValueError("CoreFirmware: an unread declaration must state why, or its empty list lies")
        if self.refused and not self.caveats:
            raise ValueError("CoreFirmware: a refused declaration must state why, or it vanishes")

    @property
    def unmet(self) -> tuple[FirmwareRequirement, ...]:
        """Required files that are demonstrably not usable — absent or wrong."""
        return tuple(r for r in self.requirements if r.need == NEED_REQUIRED and r.satisfied is False)

    @property
    def undetermined(self) -> tuple[FirmwareRequirement, ...]:
        """Required files atlas could not judge — unverified, unreadable, or unlookable."""
        return tuple(r for r in self.requirements if r.need == NEED_REQUIRED and r.satisfied is None)

    @property
    def requirements_met(self) -> bool | None:
        """Are all *required* files in place and right? ``None`` when atlas cannot say.

        The tri-state is the point, and it is the one number a client renders:
        ``None`` when the declaration could not be read, when a required file
        could not be judged — including one that was simply never verified — or
        when a required declaration was refused for leaving the firmware root.
        ``True`` is never reached out of ignorance, and never with a required
        file whose bytes are known to be wrong.

        Note what follows for ``verify=False``: a core whose required files have
        known identities answers ``None``, not ``True``. Presence alone is not
        the question this field asks.
        """
        if self.declaration != DECLARATION_READ:
            return None
        if self.unmet:
            return False
        if self.undetermined:
            return None
        return None if any(r.need == NEED_REQUIRED for r in self.refused) else True


@dataclass(frozen=True, slots=True)
class UnclaimedFile:
    """A file in the firmware tree that no installed core asks for.

    ``identity`` is matched by **content**, not by name — the name is exactly
    what is not to be trusted about an unclaimed file. It is therefore only
    available when the caller asked for verification; without it atlas states
    the file and says nothing about what it is.
    """

    path: str
    identity: FirmwareIdentity | None

    @property
    def known_as(self) -> tuple[str, ...]:
        """The canonical names this content is known under, when recognised."""
        return () if self.identity is None else self.identity.known_as


@dataclass(frozen=True, slots=True)
class FirmwareAnswer:
    """One firmware answer: per emulator what it wants, plus what nobody wants.

    ``root`` is the resolved system directory — the directory RetroArch hands
    its cores — or ``None`` when the configs do not state one; then there is
    nothing to resolve against and ``cores`` is empty with a caveat saying so.
    That caveat is an invariant, not a habit: an answer with no root and no
    caveat is the one shape this whole module exists to prevent — an empty
    list that says nothing, which a caller can only read as "nothing needed".
    ``hash_checked`` records whether identity verification ran at all, so a
    list of present files can never be mistaken for a list of verified ones.
    """

    root: str | None
    cores: tuple[CoreFirmware, ...]
    unclaimed: tuple[UnclaimedFile, ...]
    hash_checked: bool
    sources: tuple[str, ...]
    caveats: tuple[Caveat, ...]

    def __post_init__(self) -> None:
        if self.root is None and not self.caveats:
            raise ValueError(
                "FirmwareAnswer: an answer with no root must state why there is none — without it the "
                "empty answer reads as 'this machine needs no firmware'"
            )

    @property
    def requirements(self) -> tuple[FirmwareRequirement, ...]:
        """Every requirement in the answer, flattened and sorted by destination."""
        return _requirements_of(self.cores)


@dataclass(frozen=True, slots=True)
class FirmwareIdentification:
    """What a piece of content is, and every place on this machine that wants it.

    ``identity`` is ``None`` when the packaged table does not recognise the
    content — a normal answer, and one that must not be read as "this file is
    junk": the table covers only what ``System.dat`` covers.
    """

    identity: FirmwareIdentity | None
    requirements: tuple[FirmwareRequirement, ...]
    sources: tuple[str, ...]
    caveats: tuple[Caveat, ...]

    @property
    def known_as(self) -> tuple[str, ...]:
        """The canonical names this content is known under, when recognised."""
        return () if self.identity is None else self.identity.known_as


@dataclass(frozen=True, slots=True)
class FirmwareContext:
    """One live read of everything a firmware answer is derived from.

    Assembled once per query by the installation handle, then shared by every
    entry point below, so a single answer can never mix two revisions of the
    configs it was derived from.

    ``cores_read`` says whether the core enumeration happened at all. An empty
    ``cores`` means two different things — this installation ships no cores, or
    atlas never got to look — and only the first licenses a statement about what
    the machine has.

    ``arrangement_version`` is the version the arrangement states about itself
    in that same read, or ``None`` where it states none. It rides here rather
    than being fetched where it is used, so the evidence an answer states and
    the configs it was derived from come from one snapshot — and so a handle
    that assembles a context cannot leave it out by forgetting.
    """

    root: str | None
    cores: tuple[CoreDeclarations, ...]
    hashes: FirmwareHashes
    cores_read: bool = True
    sources: tuple[str, ...] = ()
    caveats: tuple[Caveat, ...] = ()
    arrangement_version: str | None = None


@dataclass(frozen=True, slots=True)
class CatalogueEntry:
    """One emulator a frontend catalogue declares for a system."""

    label: str
    kind: str
    core_so: str | None


@dataclass(frozen=True, slots=True)
class Catalogue:
    """A frontend's emulator enumeration for one system — and whether it was read.

    The distinction is the whole point: an enumeration that came back empty says
    the frontend knows no emulator for that system, while one that could not be
    read says nothing at all. Collapsing them turns a read failure into a claim
    about the machine.
    """

    entries: tuple[CatalogueEntry, ...]
    read: bool = True


_SAVE_ARTIFACTS: frozenset[str] | None = None


def _mode_save_files(mode: SaveMode) -> set[str]:
    """The concrete files one save mode claims, relative to the firmware root.

    A file name still carrying a template hole names no concrete file, so it
    claims nothing here.
    """
    return {
        f"{mode.subdir}/{name}" if mode.subdir else name
        for name in (*(mode.files or ()), *(mode.observe or ()))
        if "<" not in name
    }


def save_artifact_paths() -> frozenset[str]:
    """Paths under the firmware root that the save rule cards claim as *save* data.

    Some cores write saves into the system directory — Flycast keeps its VMUs
    in ``dc/``, LRPS2 its memory cards under ``pcsx2/memcards``. Those files sit
    in the firmware tree but reporting them as firmware-anything is a category
    error, and atlas already knows better: the cards in
    ``data/core_oddities.json`` name the directory and the files. Every mode
    rooted in the system directory contributes, whatever the core's option is
    set to right now — a leftover VMU from the other setting is still a save.
    """
    global _SAVE_ARTIFACTS
    if _SAVE_ARTIFACTS is None:
        paths: set[str] = set()
        for card in load_oddities():
            for mode in card.modes.values():
                if mode.root == ROOT_SYSTEM_DIRECTORY:
                    paths.update(_mode_save_files(mode))
        _SAVE_ARTIFACTS = frozenset(paths)
    return _SAVE_ARTIFACTS


def _observe(
    machine: Machine, path: str, identity: FirmwareIdentity | None, *, verify: bool
) -> tuple[PathKind, FirmwareChecked | None, Caveat | None]:
    """What the machine says about one destination: what is there, and how sure we are.

    All four path kinds are distinct answers, because the caller acts on each
    differently. A directory sitting at the destination is not "missing" in any
    useful sense — nothing can be placed there — and an inaccessible path is not
    an absent file, it is a look that did not happen.
    """
    kind = machine.path_kind(path)
    if kind == KIND_INACCESSIBLE:
        return (
            kind,
            None,
            Caveat(
                CAVEAT_FIRMWARE_PATH_INACCESSIBLE,
                f"{path} cannot be looked at (permissions or an I/O failure), so whether anything is there "
                "is unknown — this is not an absent file",
                {"path": path},
            ),
        )
    if kind == KIND_DIRECTORY:
        # Not necessarily wrong: LRPS2 declares "pcsx2/bios" and means the
        # folder, and RetroDECK links it to the firmware root. Atlas states what
        # is there and establishes nothing — telling the caller to clear the
        # path would break exactly that arrangement.
        return (
            kind,
            CHECKED_UNKNOWN,
            Caveat(
                CAVEAT_FIRMWARE_PATH_OBSTRUCTED,
                f"a directory is at {path}, where this core's declaration names a file — some cores declare "
                "a folder instead (LRPS2 does), so this may be correct; either way nothing about it can be "
                "established, and it is not a missing file",
                {"path": path},
            ),
        )
    if kind != KIND_FILE:
        return kind, None, None
    if identity is None:
        # Nothing to check against — and that is not the same as "not checked".
        return KIND_FILE, CHECKED_UNKNOWN, None
    if not verify:
        return KIND_FILE, CHECKED_UNCHECKED, None
    # Size is a free pre-filter: a wrong size settles the question without
    # reading a byte of the file.
    size = machine.file_size(path)
    if size is not None and size != identity.size:
        return KIND_FILE, CHECKED_MISMATCH, None
    digest = machine.file_digest(path, DIGEST_MD5)
    if digest is None:
        return (
            KIND_FILE,
            CHECKED_UNKNOWN,
            Caveat(
                CAVEAT_FIRMWARE_UNREADABLE,
                f"{path} is there but its bytes cannot be read, so its identity stays unestablished — "
                "this is a read failure, not a verdict on the file",
                {"path": path},
            ),
        )
    matches = digest.lower() == identity.md5.lower()
    return KIND_FILE, CHECKED_VERIFIED if matches else CHECKED_MISMATCH, None


def resolve_links(machine: Machine, path: str) -> str | None:
    """Follow symlinks segment by segment, the way the kernel would.

    ``None`` when the chain does not settle within
    :data:`~atlas.machine.SYMLINK_HOPS` hops — a loop, or a chain longer than
    the kernel would follow, both of which are ``ELOOP``. Goes through the
    seam's ``readlink`` and uses the seam's hop limit, so a fixture machine
    resolves exactly like the real one; a limit that differed anywhere would
    make vectors built on it prove nothing.

    ``..`` is applied to the *resolved* path, not to the spelling. That is the
    whole point: the kernel resolves a component and then walks up from where
    it landed, so ``link/..`` leaves the link's target directory, not the
    directory the link sits in.

    One of three kernel walks in atlas, next to ``FixtureMachine._resolve``
    (which additionally refuses to step through a non-directory, because it
    answers for paths rather than resolving them) and
    ``atlas.installations._resolve_symlink_chain`` (which also collects the
    links traversed). A fidelity finding about symlinks, ``..`` or the hop
    limit belongs in all three; the limit itself is shared, never copied.
    """
    parts = [p for p in path.split("/") if p and p != "."]
    resolved = "/"
    hops = SYMLINK_HOPS
    while parts:
        segment = parts.pop(0)
        if segment == "..":
            resolved = os.path.dirname(resolved) or "/"
            continue
        candidate = os.path.join(resolved, segment)
        target = machine.readlink(candidate)
        if target is None:
            resolved = candidate
            continue
        hops -= 1
        if hops < 0:
            return None
        if os.path.isabs(target):
            resolved = "/"
        # A relative target is relative to the directory holding the link,
        # which is exactly where `resolved` already stands.
        parts = [p for p in target.split("/") if p and p != "."] + parts
    return resolved


def _stays_under(root: str, path: str) -> bool:
    """Is *path* the firmware *root* itself, or something inside it?

    Both arguments are **resolved** paths — the check is the last step of a
    resolution, never a substitute for one. The root counts as inside on
    purpose: RetroDECK links ``bios/pcsx2/bios`` back to the firmware root so
    LRPS2 finds its folder, and that declaration resolves to the root exactly.

    Every place atlas decides to read, hash, or report something is bounded by
    this one predicate, so the bound cannot drift between the declaration side
    and the scan side.
    """
    prefix = root if root.endswith("/") else f"{root}/"
    return path == root or path.startswith(prefix)


@dataclass(frozen=True, slots=True)
class Destination:
    """Where a declared path actually lands — or why atlas would not follow it.

    Exactly one of ``path`` and ``refusal`` is set. ``refusal`` is a caveat
    code, and there are four of them on purpose: "this leaves the firmware
    root", "this could not be resolved at all", "this names no file" and
    "there is no root to resolve against" are different facts, and a consumer
    branching on a code it can trust must not be handed the wrong one. The
    last one is also of a different *scope*: the first three are about the
    declaration in hand, while an unusable root refuses every declaration of
    every core alike.
    """

    path: str | None = None
    refusal: str | None = None

    def __post_init__(self) -> None:
        if (self.path is None) == (self.refusal is None):
            raise ValueError(
                "Destination: exactly one of path and refusal is set — a landing place without a refusal, "
                f"or a refusal without one, got path={self.path!r} refusal={self.refusal!r}"
            )


def _join_under(root: str, declared: str) -> str:
    """``fill_pathname_join`` — the composition RetroArch performs.

    Directory, exactly one separator, then the declared path **verbatim**
    (``file_path.c:983-993`` with ``fill_pathname_slash``, ``:395-410``). There
    is no special case for an absolute declaration, which is the whole point:
    ``/etc/passwd`` composes to ``<root>//etc/passwd`` and names a file inside
    the system directory, not the one it reads like. That composed name is what
    ``path_is_valid`` is asked about (``core_info.c:2381-2383``) and what the
    core is handed, so it is what atlas answers.
    """
    if not root:
        return declared
    return f"{root}{declared}" if root.endswith("/") else f"{root}/{declared}"


def destination_under(machine: Machine, root: str, declared: str) -> Destination:
    """Where a declared path lands under *root* — resolved, in the kernel's order.

    ``firmwareN_path`` is a relative path by contract, and it is read from a
    config file a user (or anything writing that file) can edit, so every read
    atlas then does — presence, size, digest, and the directories the unclaimed
    scan walks — is bounded by the root. An absolute declaration is not the way
    out of that bound it looks like: RetroArch composes it with the system
    directory like any other (:func:`_join_under`), and so does atlas. What
    still leaves the root is a climb (``../``), and that is refused.

    Everything else is decided on **resolved** paths, and nothing is normalized
    first. Collapsing ``..`` lexically before resolving would eat the component
    in front of it even when that component is a symlink, and the kernel does
    the opposite: it resolves the component and applies ``..`` to where it
    landed. With ``pcsx2/bios`` linked to the firmware root,
    ``pcsx2/bios/../x.bin`` is ``<root>/../x.bin`` to the kernel — outside — and
    ``<root>/pcsx2/x.bin`` to a lexical reading. The kernel opens the file, so
    the kernel's reading is the one that counts.

    The answer is the resolved path. Two declarations that land on the same
    file then look like one place, which is what keeps a placing client from
    writing two copies: LRPS2's ``pcsx2/bios`` *is* the firmware root here.

    A declaration whose last component is ``.``, ``..`` or empty is refused
    before any of that: those name a directory step, not a file. They resolve
    to a perfectly legal directory — for ``sub/..`` the firmware root itself,
    for ``dc/`` the subdirectory — and answering that as a destination would
    state a requirement for a file the core never named, on a path nothing can
    ever be placed at.

    A *root* that is not an absolute path is refused first of all, because it
    is not a bound: the resolver builds every path from ``/``, so a relative
    root resolves to one that contains everything below it and the containment
    check then passes on anything, including a declaration that names a file
    nowhere near the firmware tree.

    A cfg reaches this: ``system_directory = "system"`` is not blank and not
    ``default``, so it survives ``expand_home`` as written, and the sandbox
    translation only rewrites absolute spellings — the relative value arrives
    here unchanged. RetroArch resolves such a root against the working
    directory of the running process, which is not a fact on disk, so atlas
    has no destination to state and says so instead of promoting the value to
    an absolute path it invented. That the root itself is unusable is stated
    separately, by ``firmware-root-missing``.
    """
    if not os.path.isabs(root):
        return Destination(refusal=CAVEAT_FIRMWARE_ROOT_UNUSABLE)
    if os.path.basename(declared) in ("", ".", ".."):
        return Destination(refusal=CAVEAT_FIRMWARE_PATH_NAMES_NO_FILE)
    resolved_root = resolve_links(machine, root)
    resolved = resolve_links(machine, _join_under(root, declared))
    if resolved_root is None or resolved is None:
        return Destination(refusal=CAVEAT_FIRMWARE_PATH_UNRESOLVABLE)
    if _stays_under(resolved_root, resolved):
        return Destination(path=resolved)
    return Destination(refusal=CAVEAT_FIRMWARE_PATH_ESCAPES_ROOT)


def _why_refused(refusal: str, root: str) -> str:
    """The prose behind one refusal code — one sentence fragment per fact."""
    if refusal == CAVEAT_FIRMWARE_PATH_ESCAPES_ROOT:
        return f"does not stay under the firmware root {root} once symlinks are resolved"
    if refusal == CAVEAT_FIRMWARE_PATH_NAMES_NO_FILE:
        return "ends in a directory step ('.', '..', or nothing at all) and so names no file"
    return "cannot be resolved at all — a symlink loop, or a chain longer than the kernel follows"


def _refusal_caveat(core: CoreDeclarations, declaration: FirmwareDeclaration, refusal: str, root: str) -> Caveat:
    """The caveat behind one refused declaration — with the right subject.

    Three of the four refusals are about the declaration, so the core and the
    path it spelled lead the sentence. An unusable root is not: it holds for
    every declaration of every core alike, and saying "this core declares X,
    which cannot be resolved" would blame a file that is perfectly well
    formed. The data is the same either way — a consumer branches on the code.
    """
    if refusal == CAVEAT_FIRMWARE_ROOT_UNUSABLE:
        message = (
            f"the firmware root {root!r} is not an absolute path, so nothing resolves against it and no "
            f"destination exists to answer — {core.core_so}'s {declaration.need} declaration "
            f"{declaration.path!r} is refused for that reason, not for anything about the file"
        )
    else:
        message = (
            f"{core.core_so} declares {declaration.path!r}, which {_why_refused(refusal, root)} — atlas "
            f"will not read or place a file it cannot vouch for, so this {declaration.need} file is "
            "refused rather than answered"
        )
    return Caveat(
        refusal,
        message,
        {
            "core_so": core.core_so,
            "declared": declaration.path,
            "need": declaration.need,
            "root": root,
        },
    )


def _requirements_for(
    machine: Machine,
    context: FirmwareContext,
    core: CoreDeclarations,
    *,
    verify: bool,
) -> tuple[tuple[FirmwareRequirement, ...], tuple[RefusedDeclaration, ...], tuple[Caveat, ...], list[Caveat]]:
    """Resolve one core's declarations: what it wants, what was refused, and why.

    Returns ``(requirements, refused, core_caveats, answer_caveats)``. The
    refusal caveat belongs to the **core** — it is a fact about that core's
    declaration, and if it only travelled on the answer, a caller reading one
    emulator's entry would see a requirement list that silently lost a file.
    """
    root = context.root
    assert root is not None  # callers resolve the empty-root answer before getting here
    requirements: list[FirmwareRequirement] = []
    refused: list[RefusedDeclaration] = []
    core_caveats: list[Caveat] = []
    answer_caveats: list[Caveat] = []
    for declaration in core.firmware:
        destination = destination_under(machine, root, declaration.path)
        if destination.path is None:
            refusal = destination.refusal or CAVEAT_FIRMWARE_PATH_ESCAPES_ROOT
            refused.append(
                RefusedDeclaration(declared=declaration.path, need=declaration.need, reason=refusal)
            )
            core_caveats.append(_refusal_caveat(core, declaration, refusal, root))
            continue
        path = destination.path
        identity = context.hashes.for_path(declaration.path)
        found, checked, caveat = _observe(machine, path, identity, verify=verify)
        if caveat is not None:
            answer_caveats.append(caveat)
        requirements.append(
            FirmwareRequirement(
                core_so=core.core_so,
                system=declaration.system,
                system_source=declaration.system_source,
                need=declaration.need,
                file_name=declaration.file_name,
                path=path,
                declared=declaration.path,
                description=declaration.description,
                identity=identity,
                found=found,
                checked=checked,
            )
        )
    return (
        tuple(sorted(requirements, key=lambda r: r.path)),
        tuple(refused),
        tuple(core_caveats),
        answer_caveats,
    )


def _requirements_of(cores: tuple[CoreFirmware, ...]) -> tuple[FirmwareRequirement, ...]:
    """Every requirement across *cores*, flattened and sorted by destination.

    One ordering for every answer that hands requirements back, so an
    identification and an inventory list the same files in the same order.
    """
    return tuple(sorted((r for c in cores for r in c.requirements), key=lambda r: (r.path, r.core_so)))


def _empty_answer(context: FirmwareContext, extra: tuple[Caveat, ...] = ()) -> FirmwareAnswer:
    return FirmwareAnswer(
        root=None,
        cores=(),
        unclaimed=(),
        hash_checked=False,
        sources=context.sources,
        caveats=(*context.caveats, *extra),
    )


def _no_declaration(subject: str, data: dict[str, str]) -> Caveat:
    """Nothing declares it, and that was established — the empty list is the answer."""
    return Caveat(
        CAVEAT_NO_FIRMWARE_DECLARATION,
        f"no installed core declares {subject} — every emulator in this answer was read and none names "
        "one, so there is nothing here to check against; this is an absence that was established, not a gap",
        data,
    )


def _no_requirement(subject: str, data: dict[str, str]) -> Caveat:
    """Declared, and still not a requirement — refused, or never enumerated by its core."""
    return Caveat(
        CAVEAT_NO_FIRMWARE_REQUIREMENT,
        f"nothing that declares {subject} produced a requirement: every declaration was either refused "
        "(it names no place inside the firmware root) or sits outside the enumeration its own core "
        "performs — the emulators below say which, one file at a time; an empty list means unresolved, "
        "not complete",
        data,
    )


def _declaration_unknown(subject: str, data: dict[str, str]) -> Caveat:
    """A read that did not happen — the one thing that may never become a claim."""
    return Caveat(
        CAVEAT_FIRMWARE_DECLARATION_UNKNOWN,
        f"whether anything here declares {subject} could not be established — the enumeration this "
        "answer would derive it from did not happen, or nothing in it could be read; the empty list "
        "below is a look that failed, never 'nothing needed'",
        data,
    )


def _declared_without_requiring(cores: tuple[CoreFirmware, ...]) -> bool:
    """Was firmware declared here that never became a requirement?

    Both halves are fields: a declaration atlas would not follow (``refused``)
    and one the emulator never asks for (``unread``). Reading them back out of
    the caveat list would make a message the load-bearing part of an answer.
    """
    return any(core.refused or core.unread for core in cores)


def _empty_answer_caveat(
    cores: tuple[CoreFirmware, ...],
    *,
    enumerated: bool,
    declared: bool,
    subject: str,
    data: dict[str, str],
) -> Caveat:
    """Which kind of empty this answer is — one code per kind, never one for three.

    An answer with no requirement is empty for one of three reasons, and they
    are different instructions to a client: nothing is declared (read, and the
    absence is the answer), something is declared that never became a
    requirement (the machine says what it wants and atlas would not follow it),
    or what is declared could not be established at all (a read that did not
    happen). A single code covering all three reads as the mildest of them,
    which is how a failed enumeration becomes "this machine needs nothing" —
    so each kind carries its own code and a consumer branches on that alone.

    *enumerated* is whether the enumeration this answer rests on happened;
    *declared* whether the subject was declared without becoming a requirement.
    Order matters: a subject where something could not be read may not be
    reported as "declared but unresolved", because that claims atlas saw the
    declarations.

    "Nothing is declared" needs **every** emulator in the answer to have been
    read, not merely one: an absence is a claim about all of them, and one
    readable core alongside five unreadable ones establishes nothing about
    what those five want. The reference machine reads 206 of its 211 cores,
    so the weaker reading would answer "nothing needed" over five unknowns on
    every query it touches.
    """
    if not enumerated or not all(core.declaration == DECLARATION_READ for core in cores):
        return _declaration_unknown(subject, data)
    if declared:
        return _no_requirement(subject, data)
    return _no_declaration(subject, data)


def _why_unread(raw_count: str) -> str:
    """Why an enumeration left declared paths out — what its ``firmware_count`` said."""
    bound = cfg_uint(raw_count)
    if not raw_count:
        return "its .info states no firmware_count, and without one it enumerates no firmware at all"
    if bound is None:
        return (
            f"its firmware_count is {raw_count!r}, which RetroArch does not read as a number, "
            "so it enumerates no firmware at all"
        )
    if bound == 0:
        return "its firmware_count is 0, so it enumerates no firmware"
    return f"its firmware_count is {bound}, so it reads firmware0_ up to firmware{bound - 1}_ and no further"


def _unread_declaration_caveats(core: CoreDeclarations) -> tuple[Caveat, ...]:
    """State the firmware a core's ``.info`` declares outside its own enumeration.

    RetroArch reads firmware through ``firmware_count`` slots and nothing else
    (:func:`atlas.core_info.enumerate_firmware`), so a path declared without a
    count, or past it, is a file it never asks for. atlas answers what the
    emulator reads — and says so here, because the answer on its own looks
    exactly like a core that simply wants less than its file lists.
    """
    if not core.unread:
        return ()
    keys = ", ".join(core.unread)
    return (
        Caveat(
            CAVEAT_FIRMWARE_DECLARATION_UNREAD,
            f"{core.core_so} declares {keys}, which RetroArch never reads: {_why_unread(core.firmware_count)} "
            "— those files are not requirements here because the emulator will not ask for them",
            {
                "core_so": core.core_so,
                "declared": keys,
                "firmware_count": core.firmware_count,
            },
        ),
    )


def _core_caveats(core: CoreDeclarations, refusals: tuple[Caveat, ...]) -> tuple[Caveat, ...]:
    """Everything one resolved core states — the same set on every route.

    The two routes that resolve a read declaration (the inventory/system-by-
    systemname one and the catalogue one) must state the same facts about the
    same core, or the answer a consumer gets depends on which question it
    asked.
    """
    return (
        *refusals,
        *_unread_declaration_caveats(core),
        *system_assignment_caveats(core),
        *catalogue_vocabulary_caveats(core),
    )


def _read_core(
    machine: Machine,
    context: FirmwareContext,
    core: CoreDeclarations,
    label: str | None,
    *,
    verify: bool,
) -> tuple[CoreFirmware, list[Caveat]]:
    """One core whose ``.info`` was read: the answer for it, and what it observed.

    Both routes that reach a read declaration — the per-core/system one and the
    catalogue one — build it here, because they differ only in where the label
    comes from. A field carried at one site and forgotten at the other is
    invisible: the answer still type-checks, the caveat that mentions the fact
    still appears on the core, and the suite stays green. ``unread`` was
    exactly that. It reached the catalogue route empty, so
    ``_declared_without_requiring`` saw nothing declared and an empty
    ``firmware_for_system`` answer stopped saying which kind of empty it was.
    """
    requirements, refused, core_caveats, observed = _requirements_for(
        machine, context, core, verify=verify
    )
    return (
        CoreFirmware(
            core_so=core.core_so,
            label=label,
            declaration=DECLARATION_READ,
            requirements=requirements,
            caveats=_core_caveats(core, core_caveats),
            refused=refused,
            unread=core.unread,
        ),
        observed,
    )


def _resolve_cores(
    machine: Machine,
    context: FirmwareContext,
    cores: tuple[CoreDeclarations, ...],
    *,
    verify: bool,
    labels: Mapping[str, str] | None = None,
) -> tuple[tuple[CoreFirmware, ...], list[Caveat]]:
    resolved: list[CoreFirmware] = []
    answer_caveats: list[Caveat] = []
    for core in cores:
        label = None if labels is None else labels.get(core.core_so)
        if core.info_status != READ_OK:
            resolved.append(_undeclarable_core(core, label))
            continue
        answer, observed = _read_core(machine, context, core, label, verify=verify)
        answer_caveats.extend(observed)
        resolved.append(answer)
    return tuple(resolved), answer_caveats


def _cores_a_derived_assignment_may_hide(
    all_cores: tuple[CoreDeclarations, ...], selected: tuple[CoreDeclarations, ...], system: str
) -> tuple[CoreDeclarations, ...]:
    """Installed cores this system query could not reach *because* of a derived slug.

    Without a frontend catalogue the selection is keyed on the cores' own
    ``systemname``. A core whose firmware was filed by a system that was derived
    rather than ruled can therefore sit under the wrong slug — and then it is not
    selected, so the caveat that would have said so never gets attached either.

    A candidate has to be one: the core must have a derived assignment **and**
    its own ``database`` must name the queried system. That is on-machine
    evidence that this core covers it, so atari800 shows up for ``atari5200``
    (its database names ``Atari - 5200`` while its systemname says ``Atari
    8-bit Family``) while thirty unrelated cores do not. Listing every derived
    core instead would put atari800 and blueMSX under a PlayStation query and
    train the reader to skip the line.
    """
    chosen = {core.core_so for core in selected}
    return tuple(
        core
        for core in all_cores
        if core.core_so not in chosen
        and any(SYSTEMNAME_TO_SLUG.get(name) == system for name in core.database)
        and system_assignment_caveats(core)
    )


def _undeclarable_core(core: CoreDeclarations, label: str | None) -> CoreFirmware:
    """A core that is here, whose ``.info`` is not — present, and unexplained."""
    return CoreFirmware(
        core_so=core.core_so,
        label=label,
        declaration=DECLARATION_UNREADABLE,
        requirements=(),
        caveats=(
            Caveat(
                CAVEAT_CORE_INFO_UNREADABLE,
                f"{core.core_so} is installed, but its .info could not be read ({core.info_status}) — what "
                "this core wants is unknown, so the empty list below is not 'needs nothing'",
                {"core_so": core.core_so, "status": core.info_status},
            ),
        ),
    )


def firmware_for_core(
    machine: Machine, context: FirmwareContext, *, core_so: str, verify: bool = False
) -> FirmwareAnswer:
    """Does *core_so* need firmware, and where does each file go?

    *core_so* is the core's ``.so`` name (``"mgba_libretro.so"``), its bare
    stem, or a full path — all three name the same core. An installed core that
    declares nothing answers ``declaration="read"`` with an empty requirement
    list: that is the honest "no, it needs nothing". A core whose ``.info``
    could not be read answers ``"unreadable"``, and one this installation does
    not ship answers ``"absent"`` — in both, the empty list means unknown.
    """
    stem = os.path.basename(core_so)
    if stem.endswith(".so"):
        stem = stem[: -len(".so")]
    if context.root is None:
        return _empty_answer(context)
    match = next((c for c in context.cores if c.stem == stem), None)
    if match is None:
        # The core-level twin of an unknown system: the caller named something
        # this installation does not have. Saying "no firmware declared" here
        # would read as "needs nothing" for a core that may declare plenty —
        # and when the cores were never enumerated, atlas cannot even claim
        # absence, so it says only that nothing could be read.
        reason = (
            Caveat(
                CAVEAT_CORE_NOT_INSTALLED,
                f"{stem}.so is not installed here (it is not among the cores atlas enumerated) — there is no "
                "declaration for it, so the empty list means unknown, not 'needs nothing'",
                {"core_so": f"{stem}.so"},
            )
            if context.cores_read
            else _declaration_unknown(f"firmware for {stem}.so", {"core_so": f"{stem}.so"})
        )
        return FirmwareAnswer(
            root=context.root,
            cores=(
                CoreFirmware(
                    core_so=f"{stem}.so",
                    label=None,
                    declaration=DECLARATION_ABSENT,
                    requirements=(),
                    caveats=(reason,),
                ),
            ),
            unclaimed=(),
            hash_checked=verify,
            sources=context.sources,
            caveats=(*context.caveats, reason),
        )
    cores, caveats = _resolve_cores(machine, context, (match,), verify=verify)
    return FirmwareAnswer(
        root=context.root,
        cores=cores,
        unclaimed=(),
        hash_checked=verify,
        sources=context.sources,
        caveats=(*context.caveats, *caveats),
    )


def _derived_enumeration(
    machine: Machine, context: FirmwareContext, system: str, *, verify: bool
) -> tuple[list[CoreFirmware], list[Caveat]]:
    """The emulators filed under *system* by the installed cores' own declarations.

    The selection every catalogue-less answer shares: a core answers for
    *system* when the map files the core itself there, or any one of its
    declared files (the per-file overrides put Flycast under ``naomi``).
    Observation caveats and the may-hide statement ride along. What does
    *not* ride along is the lead caveat framing the list — *why* the
    enumeration is derived differs between the two callers (an arrangement
    with no catalogue at all, and a word no catalogue can declare), and each
    states its own reason.
    """
    caveats: list[Caveat] = []
    selected = tuple(
        core
        for core in context.cores
        if core.system == system or any(d.system == system for d in core.firmware)
    )
    cores, observation_caveats = _resolve_cores(machine, context, selected, verify=verify)
    caveats.extend(observation_caveats)
    hidden = _cores_a_derived_assignment_may_hide(context.cores, selected, system)
    if hidden:
        names = ", ".join(sorted(c.core_so for c in hidden))
        caveats.append(
            Caveat(
                CAVEAT_SYSTEM_ASSIGNMENT_MAY_HIDE_CORES,
                f"this list is keyed on the cores' own systemname, and {len(hidden)} installed core(s) "
                f"name {system!r} in their database while filing firmware by a system that was derived "
                f"rather than ruled — an emulator missing from this list is one of these: {names}",
                {
                    "count": str(len(hidden)),
                    "cores": names,
                    "system": system,
                    "table_version": FIRMWARE_SYSTEM_OVERRIDE_VERSION,
                },
            )
        )
    return list(cores), caveats


def _cores_by_systemname(
    machine: Machine, context: FirmwareContext, system: str, *, verify: bool
) -> tuple[list[CoreFirmware], list[Caveat]]:
    """The emulators for *system* where no frontend catalogue exists.

    The installed cores' own ``systemname`` is then the only enumeration there
    is. The identifier is the same vocabulary as everywhere else — ES-DE ids,
    which the systemname map speaks since its version 2 — but *which emulators
    carry it* was derived from the cores rather than read from a frontend, and
    the caveat states that: an enumeration a catalogue would disagree with is
    a real possibility, an identifier mismatch is not.
    """
    cores, caveats = _derived_enumeration(machine, context, system, verify=verify)
    lead = Caveat(
        CAVEAT_EMULATOR_CATALOGUE_UNAVAILABLE,
        "this installation ships no emulator catalogue, so the emulators for this system are derived "
        "from the installed cores' own systemname — the identifier is atlas's one system vocabulary "
        "either way; what a catalogue would have said about the emulator list is unknown",
        {"system": system},
    )
    return cores, [lead, *caveats]


def _uncatalogued_word_caveat(system: str) -> Caveat:
    """The answer-level mark on a question asked in one of atlas's own spellings.

    Not :data:`CAVEAT_EMULATOR_CATALOGUE_UNAVAILABLE`, however similar the
    consequence: that code claims the *machine* ships no catalogue, which is
    false on a catalogued arrangement. The true fact is about the word — no
    build declares it, so no catalogue anywhere could enumerate or deny its
    emulators — and it holds identically on every arrangement, which is what
    makes the own spellings one vocabulary rather than a per-route dialect.
    """
    return Caveat(
        CAVEAT_SYSTEM_NOT_IN_CATALOGUE,
        f"no ES-DE system id exists for this system ({SYSTEMS_WITHOUT_CATALOGUE_ID[system]}); "
        f"{system!r} is atlas's own spelling — no frontend catalogue can enumerate its emulators, "
        "so this list is derived from the installed cores' own systemname on every arrangement",
        {"system": system, "map_version": SYSTEMNAME_MAP_VERSION},
    )


def _catalogue_entry_core(
    machine: Machine,
    context: FirmwareContext,
    entry: CatalogueEntry,
    by_stem: Mapping[str, CoreDeclarations],
    *,
    verify: bool,
) -> tuple[CoreFirmware, list[Caveat]]:
    """One catalogue entry resolved, plus the observations it produced.

    Four states, kept apart: a standalone emulator (here, and outside atlas's
    coverage), a core the catalogue names that is not installed (not here), an
    installed core whose ``.info`` could not be read, and one that was read.
    """
    if entry.kind != KIND_LIBRETRO or entry.core_so is None:
        return (
            CoreFirmware(
                core_so=entry.core_so,
                label=entry.label,
                declaration=DECLARATION_UNSUPPORTED,
                requirements=(),
                caveats=(
                    Caveat(
                        CAVEAT_STANDALONE_UNSUPPORTED,
                        f"{entry.label} is a standalone emulator — its firmware rules are outside "
                        "the resolver's current coverage (ROADMAP.md), so the empty list means "
                        "unknown; the emulator is here, atlas's source for it is not",
                        {"label": entry.label},
                    ),
                ),
            ),
            [],
        )
    core = by_stem.get(entry.core_so[: -len(".so")] if entry.core_so.endswith(".so") else entry.core_so)
    if core is None:
        # Same guard as the per-core route: absence is a claim, and it
        # needs the core enumeration to have happened.
        reason = (
            Caveat(
                CAVEAT_CORE_NOT_INSTALLED,
                f"the catalogue declares {entry.label} on {entry.core_so}, but that core is "
                "not installed here — atlas has no declaration for it, so the empty list "
                "means unknown, not 'needs nothing'",
                {"core_so": entry.core_so, "label": entry.label},
            )
            if context.cores_read
            else _declaration_unknown(
                f"firmware for {entry.core_so}", {"core_so": entry.core_so, "label": entry.label}
            )
        )
        return (
            CoreFirmware(
                core_so=entry.core_so,
                label=entry.label,
                declaration=DECLARATION_ABSENT,
                requirements=(),
                caveats=(reason,),
            ),
            [],
        )
    if core.info_status != READ_OK:
        return _undeclarable_core(core, entry.label), []
    return _read_core(machine, context, core, entry.label, verify=verify)


def firmware_for_system(
    machine: Machine,
    context: FirmwareContext,
    *,
    system: str,
    catalogue: Catalogue | None = None,
    verify: bool = False,
) -> FirmwareAnswer:
    """Which emulators can run *system*, and what each of them wants.

    *system* is one vocabulary on every route: ES-DE's system names, the same
    ids every other question takes (``atlas.systems``), plus the published own
    spellings (:data:`SYSTEMS_WITHOUT_CATALOGUE_ID`) for the systems no ES-DE
    build declares. With a frontend *catalogue* (ES-DE, on the installs that
    ship it) an id's emulator list is the frontend's — including entries whose
    core is not installed and standalone emulators, both stated as such rather
    than dropped. Without one the list is derived from the installed cores'
    own ``systemname`` through the packaged map;
    :data:`CAVEAT_EMULATOR_CATALOGUE_UNAVAILABLE` states that the
    *enumeration* is derived — the identifier means the same thing either way.

    An own spelling is answered identically on **every** arrangement: no
    catalogue can enumerate or deny a word no build declares, so the cores'
    own declarations are the only enumeration there is, catalogued or not,
    and the answer carries :data:`CAVEAT_SYSTEM_NOT_IN_CATALOGUE` — at answer
    level for the word asked about, and on each core for the files filed
    under one. An own spelling nothing files under answers **empty, marked
    and established**: the word is vocabulary, so the emptiness is a machine
    fact — the mark plus :data:`CAVEAT_NO_FIRMWARE_DECLARATION`, the same
    established absence an id's declaration-less emulators state per entry,
    said at answer level here because there is no entry to say it. And
    :data:`CAVEAT_SYSTEM_UNKNOWN` fires only for words in neither vocabulary.
    (``_unknown`` is deliberately not such a word: it marks cores that state
    no systemname at all, a fact about those cores rather than about a
    system, and it keeps its route-dependent behavior.)

    Each emulator answers with its **whole** declaration set, every requirement
    carrying its own ``system``: a multi-system core (mGBA declares Game Boy
    boot ROMs) wants what it wants regardless of which of its systems was
    asked about, and silently dropping entries would turn "needs firmware" into
    a wrong answer.
    """
    if context.root is None:
        return _empty_answer(context)

    resolved: list[CoreFirmware] = []
    caveats: list[Caveat] = []
    # Whether the enumeration happened at all decides whether this answer may
    # say anything about the machine when it comes back empty.
    enumerated = context.cores_read if catalogue is None else catalogue.read

    if system in SYSTEMS_WITHOUT_CATALOGUE_ID:
        # A word no build declares is answered from the cores on every
        # arrangement — a catalogue can neither enumerate nor deny it, so
        # consulting one would only ever manufacture "unknown identifier"
        # out of a word this module itself publishes. The cores are the
        # enumeration here even when a catalogue exists, so whether *they*
        # were read is what an empty answer hangs on.
        enumerated = context.cores_read
        resolved, caveats = _derived_enumeration(machine, context, system, verify=verify)
        caveats.insert(0, _uncatalogued_word_caveat(system))
    elif catalogue is None:
        resolved, caveats = _cores_by_systemname(machine, context, system, verify=verify)
    elif not catalogue.read:
        caveats.append(
            Caveat(
                CAVEAT_EMULATOR_CATALOGUE_UNREADABLE,
                "the frontend's emulator catalogue could not be read, so which emulators run this system is "
                "unknown — this answer is empty because atlas could not look, not because nothing is there",
                {"system": system},
            )
        )
    else:
        by_stem = {core.stem: core for core in context.cores}
        for entry in catalogue.entries:
            core, observed = _catalogue_entry_core(machine, context, entry, by_stem, verify=verify)
            resolved.append(core)
            caveats.extend(observed)

    if not resolved and enumerated and system not in SYSTEMS_WITHOUT_CATALOGUE_ID:
        # Nothing here covers that identifier — a different answer from "nobody
        # declares firmware for it", and a different thing for a client to do.
        # A consumer working in RomM slugs that forgets to translate ("dc" for
        # ES-DE's "dreamcast") lands exactly here, and must not read it as
        # "nothing needed". A published own spelling is exempt: the word IS
        # vocabulary, so nothing filing under it is a machine fact, not an
        # identifier mistake — it answers empty carrying only its mark, the
        # same shape an id whose emulators declare nothing answers with.
        # system-unknown fires only for words in neither vocabulary.
        caveats.append(
            Caveat(
                CAVEAT_SYSTEM_UNKNOWN,
                f"no emulator on this machine covers the system {system!r} — nothing was resolved, so this "
                "empty answer says the identifier is unknown here, not that nothing is needed; check the "
                "vocabulary before reading it as complete",
                {"system": system},
            )
        )
    elif not any(c.requirements for c in resolved):
        cores = tuple(resolved)
        empty = _empty_answer_caveat(
            cores,
            enumerated=enumerated,
            declared=_declared_without_requiring(cores),
            subject=f"firmware for system {system!r}",
            data={"system": system},
        )
        # A system whose emulators were read and declare nothing says so per
        # emulator — ``declaration="read"`` with an empty list is the answer,
        # exactly as the per-core route gives it, and an answer-level line
        # would add nothing while reading as a degradation. What the entries
        # cannot say is the other two: that firmware was declared here and
        # never became a requirement, or that nothing could be read at all.
        # And only entries that exist can say anything: an own spelling
        # nothing files under reaches here with no cores at all (its
        # ``system-unknown`` is deliberately suppressed above), so the
        # established absence is stated here or nowhere.
        if empty.code != CAVEAT_NO_FIRMWARE_DECLARATION or not cores:
            caveats.append(empty)

    return FirmwareAnswer(
        root=context.root,
        cores=tuple(resolved),
        unclaimed=(),
        hash_checked=verify,
        sources=context.sources,
        caveats=(*context.caveats, *caveats),
    )


def _unclaimed_identity(
    machine: Machine, context: FirmwareContext, path: str, *, verify: bool
) -> tuple[FirmwareIdentity | None, Caveat | None]:
    """What an unclaimed file *is*, by content — nothing at all without ``verify``.

    The name is exactly what is not to be trusted about a file nobody declared,
    so without hashing atlas states the file and says nothing about it.
    """
    if not verify:
        return None, None
    digest = machine.file_digest(path, DIGEST_MD5)
    sha1 = machine.file_digest(path, DIGEST_SHA1)
    if digest is None or sha1 is None:
        return None, Caveat(
            CAVEAT_FIRMWARE_UNREADABLE,
            f"{path} is there but its bytes cannot be read, so what it is stays unknown",
            {"path": path},
        )
    return context.hashes.for_content(md5=digest, sha1=sha1), None


def _unclaimed_in(
    machine: Machine,
    context: FirmwareContext,
    directory: str,
    claimed: set[str],
    artifacts: set[str],
    *,
    verify: bool,
) -> tuple[list[UnclaimedFile], list[Caveat]]:
    """The files in one directory that no installed core asks for.

    An entry that cannot be looked at is stated, not dropped: whether it is a
    file nobody declared is then unknown, and an unknown is the one thing this
    list may not silently contain — it would arrive as an
    :class:`UnclaimedFile` whose path is real and whose identity was invented.

    Only for entries that survive the exclusions, though. Whether a path is
    *this scan's* subject is settled before anything is said about it: a
    declared destination that cannot be looked at is already stated by
    :func:`_observe`, and saying it again here would put the same fact in the
    answer twice; a save the rule cards claim is never this scan's business at
    all, and a caveat calling an unreadable memory card a possibly-undeclared
    firmware file is the category error the artifact exclusion exists to
    prevent.

    A directory the scan could not list at all is the same fact one level up,
    and it is stated the same way: once, naming the directory. Without it an
    unreadable firmware tree reports as a tree with nothing unclaimed in it,
    which is the reassuring answer and the wrong one.
    """
    found: list[UnclaimedFile] = []
    caveats: list[Caveat] = []
    listing = machine.glob(os.path.join(_glob_escape(directory), "*"))
    caveats.extend(
        Caveat(
            CAVEAT_FIRMWARE_SCAN_INCOMPLETE,
            f"{unreadable} lies in the firmware tree and could not be listed (permissions or an I/O "
            "failure) — whether anything undeclared sits in it is unknown, so the list below is what "
            "atlas could see and not the whole tree",
            {"path": unreadable},
        )
        for unreadable in listing.unreadable
    )
    for entry in listing.matches:
        kind = machine.path_kind(entry)
        if kind not in (KIND_FILE, KIND_INACCESSIBLE):
            continue
        resolved_entry = resolve_links(machine, entry) or entry
        if resolved_entry in claimed or resolved_entry in artifacts:
            continue
        if kind == KIND_INACCESSIBLE:
            caveats.append(
                Caveat(
                    CAVEAT_FIRMWARE_PATH_INACCESSIBLE,
                    f"{entry} lies in the firmware tree, is claimed by no core, and cannot be looked at "
                    "(permissions or an I/O failure) — so whether it is a file nobody declared is unknown; "
                    "it is stated here instead of appearing below as a file atlas never saw",
                    {"path": entry},
                )
            )
            continue
        identity, caveat = _unclaimed_identity(machine, context, entry, verify=verify)
        if caveat is not None:
            caveats.append(caveat)
        found.append(UnclaimedFile(path=resolved_entry, identity=identity))
    return found, caveats


def _unclaimed_files(
    machine: Machine,
    context: FirmwareContext,
    claimed: set[str],
    *,
    verify: bool,
) -> tuple[tuple[UnclaimedFile, ...], list[Caveat]]:
    """Files sitting where a declaration points, that no installed core asks for.

    The scan is bounded to the directories declarations actually reference —
    the system directory itself plus every subdirectory named in a
    ``firmwareN_path``. An unbounded walk would mark thousands of files (whole
    core data trees live under the same root) and drown the signal; this bound
    needs no exclusion list and grows on its own as cores declare new paths.
    Save data the rule cards claim is excluded outright
    (:func:`save_artifact_paths`).

    Every scanned directory is clamped to the root's subtree, and the root
    itself is the only one that may equal it. A claimed path is resolved and
    inside the root, but that is not enough: a folder declaration is allowed to
    land on the root exactly (LRPS2's ``pcsx2/bios``, which RetroDECK links
    back to it), and the *parent* of that landing is one level above the
    firmware tree. Without the clamp a stock RetroDECK scans it, and whatever
    sits there is reported — and with ``verify`` hashed — as unclaimed
    firmware.

    Names beginning with a dot never appear in this list, and that is a
    decision rather than an oversight: a wildcard in the seam's glob does not
    match a leading dot (:mod:`atlas.machine` states the rule normatively, and
    a real ``glob`` behaves the same), and what sits under a dot in a firmware
    tree is tooling residue — a file manager's ``.directory``, a sync tool's
    bookkeeping — not firmware anyone placed there. It narrows *this list*
    only: a declared path is resolved, never globbed, so a core that declares a
    dotted file still gets its requirement answered.
    """
    root = context.root
    assert root is not None
    resolved_root = resolve_links(machine, root) or root
    # Resolved, because that is what the entries below are compared against:
    # RetroDECK's dir_prep links whole firmware subdirectories elsewhere, and a
    # VMU reached through ``dc -> dreamcast`` is the same save file under both
    # spellings — matched on the unresolved name it would be reported as
    # firmware nobody asked for.
    artifacts = {
        resolve_links(machine, path) or path
        for path in (os.path.join(resolved_root, name) for name in save_artifact_paths())
    }
    directories = {resolved_root} | {
        parent
        for parent in (os.path.dirname(p) for p in claimed)
        if _stays_under(resolved_root, parent)
    }
    found: list[UnclaimedFile] = []
    caveats: list[Caveat] = []
    for directory in sorted(directories):
        in_directory, unreadable = _unclaimed_in(
            machine, context, directory, claimed, artifacts, verify=verify
        )
        found.extend(in_directory)
        caveats.extend(unreadable)
    return tuple(sorted(found, key=lambda f: f.path)), caveats


def firmware_inventory(machine: Machine, context: FirmwareContext, *, verify: bool = False) -> FirmwareAnswer:
    """Every installed core's firmware, plus what is lying around unclaimed.

    The aggregate view: how much is in place, how much of that is
    hash-correct, what is missing, and what sits in the firmware tree that
    nothing asks for. Unclaimed files are identified by **content**, so
    ``verify`` is what turns their identity on — a name proves nothing about a
    file nobody declared.
    """
    if context.root is None:
        return _empty_answer(context)
    cores, caveats = _resolve_cores(machine, context, context.cores, verify=verify)
    # Only declarations that stay under the root define the scan, so a config
    # pointing outside it can never widen where atlas reads or hashes.
    claimed: set[str] = set()
    for core in context.cores:
        for declaration in core.firmware:
            landing = destination_under(machine, context.root, declaration.path).path
            if landing is not None:
                claimed.add(landing)
    unclaimed, unclaimed_caveats = _unclaimed_files(machine, context, claimed, verify=verify)
    caveats.extend(unclaimed_caveats)
    if not claimed:
        caveats.append(
            _empty_answer_caveat(
                cores,
                enumerated=context.cores_read,
                declared=_declared_without_requiring(cores),
                subject="any firmware",
                data={},
            )
        )
    return FirmwareAnswer(
        root=context.root,
        cores=cores,
        unclaimed=unclaimed,
        hash_checked=verify,
        sources=context.sources,
        caveats=(*context.caveats, *caveats),
    )


def _stated_content(md5: str | None, sha1: str | None, size: int | None) -> dict[str, str]:
    """What the caller stated about the content — every field, so none is dropped.

    A caveat about a request that matched nothing has to carry the whole
    request: told only the digests, a caller whose *size* is the field the
    table disagrees with cannot see which of its values was rejected.
    """
    stated: dict[str, str] = {}
    if md5 is not None:
        stated["md5"] = md5
    if sha1 is not None:
        stated["sha1"] = sha1
    if size is not None:
        stated["size"] = str(size)
    return stated


def _declared_beyond_requirements(
    cores: tuple[CoreFirmware, ...], hashes: FirmwareHashes, identity: FirmwareIdentity
) -> bool:
    """Could a declaration this answer never resolved have been about *this* content?

    Two kinds of declaration never become a requirement and so can never be
    matched by content. A **refused** one still names the path it wanted, so it
    is answered precisely: the packaged table says what belongs there. An
    **unread** one is only known by the key it was declared under
    (``firmware3_path``) and not by the path it named
    (:class:`CoreDeclarations`), so it cannot be tied to an identity at all —
    and it is counted anyway, because the alternative is to answer that an
    absence was established while a core's ``.info`` may name exactly this
    file. Over-reporting "unresolved" costs a caller one look at the core
    entries; the other direction is the claim this module exists to refuse.
    """
    for core in cores:
        if core.unread:
            return True
        for refusal in core.refused:
            expected = hashes.for_path(refusal.declared)
            if expected is not None and expected.md5.lower() == identity.md5.lower():
                return True
    return False


def identify_firmware(
    machine: Machine,
    context: FirmwareContext,
    *,
    md5: str | None = None,
    sha1: str | None = None,
    size: int | None = None,
) -> FirmwareIdentification:
    """What is this content, and which requirements on this machine does it satisfy?

    The download flow, answered by bytes: a file arrives under whatever name
    its source gave it, and the question is where it goes, under what name, and
    whether it is even the right thing. Every requirement whose expected
    identity is this content comes back — across cores and across systems,
    because one identity is routinely wanted in several places under several
    names — each carrying its own absolute destination and expected file name.

    Matching a requirement is by identity, never by the name the caller's file
    happens to carry. A requirement whose file name the packaged table does not
    cover can never be matched: atlas will not claim that unknown bytes belong
    somewhere.

    A request that names no content at all — a bare ``size``, which two
    different files routinely share — is answered, not raised: it is a state of
    this domain like any other, and it comes back as an empty identification
    carrying :data:`CAVEAT_FIRMWARE_CONTENT_UNSTATED`.
    """
    caveats: list[Caveat] = list(context.caveats)
    stated = _stated_content(md5, sha1, size)
    if md5 is None and sha1 is None:
        caveats.append(
            Caveat(
                CAVEAT_FIRMWARE_CONTENT_UNSTATED,
                "this request names no content: a size alone is not an identity — files of one size are "
                "not one file — so there is nothing to look up and nothing this answer could be about",
                stated,
            )
        )
        return FirmwareIdentification(
            identity=None, requirements=(), sources=context.sources, caveats=tuple(caveats)
        )
    identity = context.hashes.for_content(md5=md5, sha1=sha1, size=size)
    if identity is None:
        if context.hashes.contradicts_itself(md5=md5, sha1=sha1, size=size):
            # The table knows these fields, just not together. Blaming the table
            # would send the caller looking in the wrong place.
            caveats.append(
                Caveat(
                    CAVEAT_FIRMWARE_CONTENT_CONTRADICTORY,
                    "the fields given describe no single file: the table knows a digest among them, and the "
                    "entry it names carries a different value for something else stated here — so the "
                    "request contradicts itself rather than the content being unknown",
                    stated,
                )
            )
        else:
            caveats.append(
                Caveat(
                    CAVEAT_FIRMWARE_CONTENT_UNIDENTIFIED,
                    "the packaged identity table does not recognise this content — that is a normal answer "
                    "(the table covers only what System.dat covers), so it says nothing about the file's worth",
                    stated,
                )
            )
        return FirmwareIdentification(
            identity=None, requirements=(), sources=context.sources, caveats=tuple(caveats)
        )
    if context.root is None:
        return FirmwareIdentification(
            identity=identity, requirements=(), sources=context.sources, caveats=tuple(caveats)
        )
    # What this answer is made of is the declarations plus what sits at each
    # destination — the unclaimed scan answers a different question entirely
    # (files nobody declared), and none of its result reaches here. Running it
    # would glob and stat every directory a declaration references for a lookup
    # the caller already has the bytes for.
    cores, _observations = _resolve_cores(machine, context, context.cores, verify=False)
    wanted = tuple(
        r
        for r in _requirements_of(cores)
        if r.identity is not None and r.identity.md5.lower() == identity.md5.lower()
    )
    if not wanted:
        caveats.append(
            _empty_answer_caveat(
                cores,
                enumerated=context.cores_read,
                declared=_declared_beyond_requirements(cores, context.hashes, identity),
                subject=f"a file with this identity (known as {', '.join(identity.known_as)})",
                data={"md5": identity.md5},
            )
        )
    # An identification hands back requirements without their emulator, so a
    # caveat about how one of them got its system has to travel with it or it is
    # lost. Only about *these* requirements, though: a core's caveat names the
    # files it is about, and attaching it because some other file of the same
    # core was derived puts warnings about files that are not in this answer.
    derived_in_answer = {r.core_so for r in wanted if r.system_source != SOURCE_OVERRIDE}
    for core in cores:
        if core.core_so in derived_in_answer:
            caveats.extend(core.caveats)
    return FirmwareIdentification(
        identity=identity, requirements=wanted, sources=context.sources, caveats=tuple(caveats)
    )
