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

    System            snes, dc, gb
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

*Unknown* is deliberately not a ``need``. Having no declaration to check
against is a property of the answer, not of a file, so it is a
:class:`~atlas.placement.Caveat` (:data:`CAVEAT_NO_FIRMWARE_DECLARATION`)
attached to an answer whose requirement list is **empty**. A caller that
ignores caveats then gets an empty list rather than a satisfied one: empty is
honest, "nothing missing" would be a lie.
"""

from __future__ import annotations

import importlib.resources
import json
import os
import re
from dataclasses import dataclass
from glob import escape as _glob_escape
from typing import Any, Literal, Mapping

from atlas.core_info import parse_core_info
from atlas.esde import KIND_LIBRETRO
from atlas.machine import DIGEST_MD5, DIGEST_SHA1, KIND_FILE, Machine
from atlas.oddities import load_oddities
from atlas.placement import ROOT_SYSTEM_DIRECTORY, Caveat

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
CAVEAT_NO_FIRMWARE_DECLARATION = "no-firmware-declaration"
CAVEAT_INFO_PATH_UNRESOLVED = "info-path-unresolved"
CAVEAT_CORE_DIR_UNRESOLVED = "core-dir-unresolved"
CAVEAT_FIRMWARE_ROOT_MISSING = "firmware-root-missing"
CAVEAT_CORE_NOT_INSTALLED = "core-not-installed"
CAVEAT_STANDALONE_EMULATOR = "standalone-emulator"
CAVEAT_CATALOGUE_UNAVAILABLE = "emulator-catalogue-unavailable"
CAVEAT_FIRMWARE_UNREADABLE = "firmware-unreadable"
CAVEAT_CONTENT_UNIDENTIFIED = "firmware-content-unidentified"

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


# Libretro ``systemname`` strings mapped to atlas system slugs. World
# knowledge: the strings are read live from the machine, but what they *mean*
# in atlas's vocabulary is not written anywhere on it. Multiple variants map to
# one slug because libretro's naming changed over the years.
SYSTEMNAME_TO_SLUG: Mapping[str, str] = {
    # PlayStation
    "Sony - PlayStation": "psx",
    "PlayStation": "psx",
    "Sony PlayStation 2": "ps2",
    "PSP": "psp",
    # Sega
    "Sega - Dreamcast": "dc",
    "Sega Dreamcast": "dc",
    "Sega - Saturn": "saturn",
    "Saturn": "saturn",
    "Sega - Mega-CD - Sega CD": "segacd",
    "Sega - Master System - Mark III": "sms",
    "Sega Master System": "sms",
    "Sega 8-bit": "sms",
    "Sega 8-bit (MS/GG/SG-1000)": "sms",
    "Sega - Game Gear": "gg",
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
    # Atari
    "Atari - Lynx": "lynx",
    "Lynx": "lynx",
    "Atari - 5200": "atari5200",
    "Atari 5200": "atari5200",
    "Atari - 7800": "atari7800",
    "Atari 7800": "atari7800",
    "Atari - 8-bit": "atari800",
    "Atari 8-bit Family": "atari800",
    "Atari - ST": "atarist",
    "Atari ST/STE/TT/Falcon": "atarist",
    # NEC
    "NEC - PC Engine - TurboGrafx 16": "pce",
    "PC Engine/PCE-CD": "pce",
    "PC Engine SuperGrafx": "pce",
    "PC Engine/SuperGrafx": "pce",
    "PC Engine/SuperGrafx/CD": "pce",
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
    "C128": "c128",
    "128": "c128",
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
    "CD-i": "cdi",
    "CDi": "cdi",
    "Intellivision": "intellivision",
    "DOS": "dos",
    "PC-8000 / PC-8800 series": "pc88",
    "Sharp X1": "x1",
    "Sharp X68000": "x68000",
    "Pokemon Mini": "pokemini",
    "Mac68k": "mac68k",
    "BK-0010/BK-0011(M)": "bk",
    "TI83": "ti83",
    "Super Cassette Vision": "scv",
    "FreeChaF": "channelf",
    "Vircon32": "vircon32",
    "Palm OS": "palmos",
    "CP System I/II": "cps",
    # Multi-system / game engines
    "Arcade (various)": "_arcade",
    "Game engine": "_engine",
    "RPG Maker XP/VX/VX Ace Game Engine": "_engine",
    "Wolfenstein 3D Game Engine": "_engine",
    "J2ME": "j2me",
    "Java ME": "j2me",
    "ZX Spectrum (various)": "zxspectrum",
}

# Per-file system overrides for multi-system cores. A core covering several
# systems carries one ``systemname``, but its firmware belongs to different
# systems — mGBA declares the Game Boy boot ROMs under "Game Boy/Game Boy
# Color/Game Boy Advance". World knowledge, same as the map above.
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
    # Master System (Genesis Plus GX)
    "bios_E.sms": "sms",
    "bios_U.sms": "sms",
    "bios_J.sms": "sms",
    # Game Gear (Genesis Plus GX)
    "bios.gg": "gg",
}

_NON_SLUG = re.compile(r"[^a-z0-9]+")


def system_for(file_name: str, systemname: str) -> str:
    """The atlas system slug a declared file belongs to.

    The per-file override wins (multi-system cores), then the ``systemname``
    map. A ``systemname`` the map does not know is *slugified* rather than
    dropped — a mechanical normalization of a string read off the machine, so
    every declaration stays reachable by some slug — and an empty
    ``systemname`` yields ``_unknown``.
    """
    override = FIRMWARE_SYSTEM_OVERRIDE.get(file_name)
    if override is not None:
        return override
    known = SYSTEMNAME_TO_SLUG.get(systemname)
    if known is not None:
        return known
    slug = _NON_SLUG.sub("-", systemname.lower()).strip("-")
    return slug or "_unknown"


@dataclass(frozen=True, slots=True)
class FirmwareDeclaration:
    """One firmware path, as one installed core's ``.info`` file declares it.

    ``path`` is verbatim from ``firmwareN_path`` — relative to the emulator's
    system directory and possibly carrying subdirectories (``dc/dc_boot.bin``).
    ``need`` is ``firmwareN_opt`` inverted: libretro treats a missing ``_opt``
    as *not optional*, so an absent flag means required.
    """

    path: str
    file_name: str
    description: str
    need: FirmwareNeed
    system: str


@dataclass(frozen=True, slots=True)
class CoreDeclarations:
    """One installed core's ``.info`` file, as far as firmware is concerned.

    Every installed core gets one of these — including the ones that declare
    nothing. That is the whole point: "this core is here and wants no firmware"
    is an answer, and it is a different answer from "atlas does not know this
    core", which has no :class:`CoreDeclarations` at all.
    """

    core_so: str
    stem: str
    systemname: str
    system: str
    firmware: tuple[FirmwareDeclaration, ...]


def _declarations_in(text: str) -> tuple[str, tuple[FirmwareDeclaration, ...]]:
    """Parse one ``.info`` file into its ``systemname`` and firmware block."""
    fields = parse_core_info(text)
    systemname = fields.get("systemname", "")
    declarations: list[FirmwareDeclaration] = []
    for key, path in fields.items():
        if not key.startswith("firmware") or not key.endswith("_path") or not path:
            continue
        index = key[len("firmware") : -len("_path")]
        if not index.isdigit():
            continue
        file_name = os.path.basename(path)
        if not file_name:
            continue
        # A missing _opt means required (libretro's own reading).
        optional = fields.get(f"firmware{index}_opt", "false").strip().lower() == "true"
        declarations.append(
            FirmwareDeclaration(
                path=path,
                file_name=file_name,
                description=fields.get(f"firmware{index}_desc", ""),
                need=NEED_OPTIONAL if optional else NEED_REQUIRED,
                system=system_for(file_name, systemname),
            )
        )
    return systemname, tuple(declarations)


def read_core_declarations(
    machine: Machine, info_dir: str, *, core_dir: str | None = None
) -> tuple[CoreDeclarations, ...]:
    """Read what every *installed* core declares, live, from its ``.info`` file.

    Globs ``info_dir`` for ``.info`` files and parses each one. When *core_dir*
    is given, a core counts only if its ``.so`` is actually there: an ``.info``
    set routinely covers more cores than an installation ships (RetroDECK: 292
    ``.info`` against 211 ``.so``), and firmware demanded by a core that cannot
    run is exactly the noise a shipped table produced. With *core_dir* ``None``
    nothing is filtered — the caller states that gap as a caveat rather than
    having it silently narrow the answer.

    libretro's two template ``.info`` files are dropped by name: they declare
    the literal placeholder ``filename.ext``.
    """
    cores: list[CoreDeclarations] = []
    for info_path in machine.glob(os.path.join(_glob_escape(info_dir), "*.info")):
        stem = os.path.basename(info_path)[: -len(".info")]
        if stem in TEMPLATE_INFO_STEMS:
            continue
        if core_dir is not None and machine.path_kind(os.path.join(core_dir, f"{stem}.so")) != KIND_FILE:
            continue
        text = machine.read_text(info_path).text
        if text is None:
            continue
        systemname, declarations = _declarations_in(text)
        cores.append(
            CoreDeclarations(
                core_so=f"{stem}.so",
                stem=stem,
                systemname=systemname,
                system=system_for("", systemname),
                firmware=declarations,
            )
        )
    return tuple(sorted(cores, key=lambda c: c.core_so))


@dataclass(frozen=True, slots=True)
class FirmwareRequirement:
    """One (core, declared file) pair — the atom of the whole model.

    ``path`` is the **absolute** destination: the installation's live system
    directory plus the declared relative path, subdirectory included. It is
    stated whether or not a file is there, because "where does this go" is the
    question a download flow asks.

    ``need`` says what the core asks for; ``present`` and ``checked`` say what
    the machine holds. ``checked`` is ``None`` exactly when nothing is there to
    check, and otherwise keeps its four values apart: ``unchecked`` (identity
    known, verification not asked for) is not ``unknown`` (no identity known,
    so it cannot be established), and neither is a verdict.
    """

    core_so: str
    system: str
    need: FirmwareNeed
    file_name: str
    path: str
    description: str
    identity: FirmwareIdentity | None
    present: bool
    checked: FirmwareChecked | None

    def __post_init__(self) -> None:
        if self.need not in FIRMWARE_NEEDS:
            raise ValueError(f"FirmwareRequirement: need must be one of {FIRMWARE_NEEDS}, got {self.need!r}")
        if self.present:
            if self.checked not in FIRMWARE_CHECKED:
                raise ValueError(
                    f"FirmwareRequirement: a present file must state one of {FIRMWARE_CHECKED}, got {self.checked!r}"
                )
        elif self.checked is not None:
            raise ValueError("FirmwareRequirement: nothing is there to check, so checked must be None")


@dataclass(frozen=True, slots=True)
class CoreFirmware:
    """What one emulator wants, resolved against the live firmware root.

    ``installed`` is the load-bearing flag. ``True`` means atlas read this
    core's own declaration off the machine, so an empty ``requirements`` is the
    answer "this core needs no firmware". ``False`` means there was nothing to
    read — the empty list then means *unknown*, never *nothing needed*, and a
    caveat says which flavor of nothing it was.
    """

    core_so: str | None
    label: str | None
    installed: bool
    requirements: tuple[FirmwareRequirement, ...]
    caveats: tuple[Caveat, ...]

    @property
    def unmet(self) -> tuple[FirmwareRequirement, ...]:
        """Required files that are not where this core will look for them."""
        return tuple(r for r in self.requirements if r.need == NEED_REQUIRED and not r.present)

    @property
    def requirements_met(self) -> bool | None:
        """Are all *required* files in place? ``None`` when atlas cannot say.

        The tri-state is the point: a core whose declaration could not be read
        answers ``None``, so "can I launch this right now" can never be
        answered ``True`` out of ignorance.
        """
        return None if not self.installed else not self.unmet


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
    ``hash_checked`` records whether identity verification ran at all, so a
    list of present files can never be mistaken for a list of verified ones.
    """

    root: str | None
    cores: tuple[CoreFirmware, ...]
    unclaimed: tuple[UnclaimedFile, ...]
    hash_checked: bool
    sources: tuple[str, ...]
    caveats: tuple[Caveat, ...]

    @property
    def requirements(self) -> tuple[FirmwareRequirement, ...]:
        """Every requirement in the answer, flattened and sorted by destination."""
        return tuple(sorted((r for c in self.cores for r in c.requirements), key=lambda r: (r.path, r.core_so)))


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
    """

    root: str | None
    cores: tuple[CoreDeclarations, ...]
    hashes: FirmwareHashes
    sources: tuple[str, ...] = ()
    caveats: tuple[Caveat, ...] = ()


@dataclass(frozen=True, slots=True)
class CatalogueEntry:
    """One emulator a frontend catalogue declares for a system."""

    label: str
    kind: str
    core_so: str | None


_SAVE_ARTIFACTS: frozenset[str] | None = None


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
                if mode.root != ROOT_SYSTEM_DIRECTORY:
                    continue
                for name in (*(mode.files or ()), *(mode.observe or ())):
                    if "<" in name:  # an unfilled template names no concrete file
                        continue
                    paths.add(f"{mode.subdir}/{name}" if mode.subdir else name)
        _SAVE_ARTIFACTS = frozenset(paths)
    return _SAVE_ARTIFACTS


def _observe(
    machine: Machine, path: str, identity: FirmwareIdentity | None, *, verify: bool
) -> tuple[bool, FirmwareChecked | None, Caveat | None]:
    """What the machine says about one destination: present, and how sure we are."""
    if machine.path_kind(path) != KIND_FILE:
        return False, None, None
    if identity is None:
        # Nothing to check against — and that is not the same as "not checked".
        return True, CHECKED_UNKNOWN, None
    if not verify:
        return True, CHECKED_UNCHECKED, None
    # Size is a free pre-filter: a wrong size settles the question without
    # reading a byte of the file.
    size = machine.file_size(path)
    if size is not None and size != identity.size:
        return True, CHECKED_MISMATCH, None
    digest = machine.file_digest(path, DIGEST_MD5)
    if digest is None:
        return (
            True,
            CHECKED_UNKNOWN,
            Caveat(
                CAVEAT_FIRMWARE_UNREADABLE,
                f"{path} is there but its bytes cannot be read, so its identity stays unestablished — "
                "this is a read failure, not a verdict on the file",
                {"path": path},
            ),
        )
    matches = digest.lower() == identity.md5.lower()
    return True, CHECKED_VERIFIED if matches else CHECKED_MISMATCH, None


def _requirements_for(
    machine: Machine,
    context: FirmwareContext,
    core: CoreDeclarations,
    *,
    verify: bool,
) -> tuple[tuple[FirmwareRequirement, ...], tuple[Caveat, ...]]:
    root = context.root
    assert root is not None  # callers resolve the empty-root answer before getting here
    requirements: list[FirmwareRequirement] = []
    caveats: list[Caveat] = []
    for declaration in core.firmware:
        path = os.path.join(root, declaration.path)
        identity = context.hashes.for_path(declaration.path)
        present, checked, caveat = _observe(machine, path, identity, verify=verify)
        if caveat is not None:
            caveats.append(caveat)
        requirements.append(
            FirmwareRequirement(
                core_so=core.core_so,
                system=declaration.system,
                need=declaration.need,
                file_name=declaration.file_name,
                path=path,
                description=declaration.description,
                identity=identity,
                present=present,
                checked=checked,
            )
        )
    return tuple(sorted(requirements, key=lambda r: r.path)), tuple(caveats)


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
    return Caveat(
        CAVEAT_NO_FIRMWARE_DECLARATION,
        f"no installed core declares {subject} — there is no declaration to check against, so nothing "
        "here is a required-and-satisfied file; an empty list means unknown, not complete",
        data,
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
        requirements, caveats = _requirements_for(machine, context, core, verify=verify)
        answer_caveats.extend(caveats)
        resolved.append(
            CoreFirmware(
                core_so=core.core_so,
                label=None if labels is None else labels.get(core.core_so),
                installed=True,
                requirements=requirements,
                caveats=(),
            )
        )
    return tuple(resolved), answer_caveats


def firmware_for_core(
    machine: Machine, context: FirmwareContext, *, core_so: str, verify: bool = False
) -> FirmwareAnswer:
    """Does *core_so* need firmware, and where does each file go?

    *core_so* is the core's ``.so`` name (``"mgba_libretro.so"``), its bare
    stem, or a full path — all three name the same core. An installed core that
    declares nothing answers with ``installed`` true and an empty requirement
    list: that is the honest "no, it needs nothing". A core this installation
    does not ship answers ``installed`` false plus
    :data:`CAVEAT_CORE_NOT_INSTALLED`, and its empty list means unknown.
    """
    stem = os.path.basename(core_so)
    if stem.endswith(".so"):
        stem = stem[: -len(".so")]
    if context.root is None:
        return _empty_answer(context)
    match = next((c for c in context.cores if c.stem == stem), None)
    if match is None:
        return FirmwareAnswer(
            root=context.root,
            cores=(
                CoreFirmware(
                    core_so=f"{stem}.so",
                    label=None,
                    installed=False,
                    requirements=(),
                    caveats=(
                        Caveat(
                            CAVEAT_CORE_NOT_INSTALLED,
                            f"{stem}.so is not installed here (no .info of that name among the installed "
                            "cores) — atlas has no declaration for it, so the empty list below means "
                            "unknown, not 'needs nothing'",
                            {"core_so": f"{stem}.so"},
                        ),
                    ),
                ),
            ),
            unclaimed=(),
            hash_checked=verify,
            sources=context.sources,
            caveats=(*context.caveats, _no_declaration(f"firmware for core {stem}.so", {"core_so": f"{stem}.so"})),
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


def firmware_for_system(
    machine: Machine,
    context: FirmwareContext,
    *,
    system: str,
    catalogue: tuple[CatalogueEntry, ...] | None = None,
    verify: bool = False,
) -> FirmwareAnswer:
    """Which emulators can run *system*, and what each of them wants.

    With a frontend *catalogue* (ES-DE, on the installs that ship it) the
    emulator list is the frontend's — including entries whose core is not
    installed and standalone emulators, both stated as such rather than
    dropped, and *system* is the frontend's system name. Without one the list
    is derived from the installed cores' own ``systemname`` and *system* is an
    atlas slug; :data:`CAVEAT_CATALOGUE_UNAVAILABLE` states that switch,
    because the two are different vocabularies.

    Each emulator answers with its **whole** declaration set, every requirement
    carrying its own ``system``: a multi-system core (mGBA declares Game Boy
    boot ROMs) wants what it wants regardless of which of its systems was
    asked about, and silently dropping entries would turn "needs firmware" into
    a wrong answer.
    """
    if context.root is None:
        return _empty_answer(context)

    caveats: list[Caveat] = []
    by_stem = {core.stem: core for core in context.cores}
    resolved: list[CoreFirmware] = []

    if catalogue is None:
        caveats.append(
            Caveat(
                CAVEAT_CATALOGUE_UNAVAILABLE,
                "this installation ships no emulator catalogue, so the emulators for a system are derived "
                "from the installed cores' own systemname — the identifier is an atlas system slug, not a "
                "frontend system name",
                {"system": system},
            )
        )
        selected = tuple(
            core
            for core in context.cores
            if core.system == system or any(d.system == system for d in core.firmware)
        )
        cores, observation_caveats = _resolve_cores(machine, context, selected, verify=verify)
        resolved.extend(cores)
        caveats.extend(observation_caveats)
    else:
        for entry in catalogue:
            if entry.kind != KIND_LIBRETRO or entry.core_so is None:
                resolved.append(
                    CoreFirmware(
                        core_so=entry.core_so,
                        label=entry.label,
                        installed=False,
                        requirements=(),
                        caveats=(
                            Caveat(
                                CAVEAT_STANDALONE_EMULATOR,
                                f"{entry.label} is a standalone emulator — its firmware rules are not "
                                "resolvable yet (ROADMAP.md), so the empty list means unknown",
                                {"label": entry.label},
                            ),
                        ),
                    )
                )
                continue
            core = by_stem.get(entry.core_so[: -len(".so")] if entry.core_so.endswith(".so") else entry.core_so)
            if core is None:
                resolved.append(
                    CoreFirmware(
                        core_so=entry.core_so,
                        label=entry.label,
                        installed=False,
                        requirements=(),
                        caveats=(
                            Caveat(
                                CAVEAT_CORE_NOT_INSTALLED,
                                f"the catalogue declares {entry.label} on {entry.core_so}, but that core is "
                                "not installed here — atlas has no declaration for it, so the empty list "
                                "means unknown, not 'needs nothing'",
                                {"core_so": entry.core_so, "label": entry.label},
                            ),
                        ),
                    )
                )
                continue
            requirements, observation_caveats = _requirements_for(machine, context, core, verify=verify)
            caveats.extend(observation_caveats)
            resolved.append(
                CoreFirmware(
                    core_so=core.core_so,
                    label=entry.label,
                    installed=True,
                    requirements=requirements,
                    caveats=(),
                )
            )

    if not any(c.installed for c in resolved):
        caveats.append(_no_declaration(f"firmware for system {system!r}", {"system": system}))

    return FirmwareAnswer(
        root=context.root,
        cores=tuple(resolved),
        unclaimed=(),
        hash_checked=verify,
        sources=context.sources,
        caveats=(*context.caveats, *caveats),
    )


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
    """
    root = context.root
    assert root is not None
    artifacts = save_artifact_paths()
    directories = {""} | {os.path.dirname(p) for p in claimed if os.path.dirname(p)}
    found: list[UnclaimedFile] = []
    caveats: list[Caveat] = []
    for relative_dir in sorted(directories):
        directory = os.path.join(root, relative_dir) if relative_dir else root
        for entry in machine.glob(os.path.join(_glob_escape(directory), "*")):
            if machine.path_kind(entry) != KIND_FILE:
                continue
            name = os.path.basename(entry)
            relative = f"{relative_dir}/{name}" if relative_dir else name
            if relative in claimed or relative in artifacts:
                continue
            identity: FirmwareIdentity | None = None
            if verify:
                digest = machine.file_digest(entry, DIGEST_MD5)
                sha1 = machine.file_digest(entry, DIGEST_SHA1)
                if digest is None or sha1 is None:
                    caveats.append(
                        Caveat(
                            CAVEAT_FIRMWARE_UNREADABLE,
                            f"{entry} is there but its bytes cannot be read, so what it is stays unknown",
                            {"path": entry},
                        )
                    )
                else:
                    identity = context.hashes.for_content(md5=digest, sha1=sha1)
            found.append(UnclaimedFile(path=entry, identity=identity))
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
    claimed = {d.path for core in context.cores for d in core.firmware}
    unclaimed, unclaimed_caveats = _unclaimed_files(machine, context, claimed, verify=verify)
    caveats.extend(unclaimed_caveats)
    if not claimed:
        caveats.append(_no_declaration("any firmware", {}))
    return FirmwareAnswer(
        root=context.root,
        cores=cores,
        unclaimed=unclaimed,
        hash_checked=verify,
        sources=context.sources,
        caveats=(*context.caveats, *caveats),
    )


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
    """
    identity = context.hashes.for_content(md5=md5, sha1=sha1, size=size)
    caveats: list[Caveat] = list(context.caveats)
    if identity is None:
        caveats.append(
            Caveat(
                CAVEAT_CONTENT_UNIDENTIFIED,
                "the packaged identity table does not recognise this content — that is a normal answer "
                "(the table covers only what System.dat covers), so it says nothing about the file's worth",
                {k: v for k, v in (("md5", md5), ("sha1", sha1)) if v is not None},
            )
        )
        return FirmwareIdentification(
            identity=None, requirements=(), sources=context.sources, caveats=tuple(caveats)
        )
    if context.root is None:
        return FirmwareIdentification(
            identity=identity, requirements=(), sources=context.sources, caveats=tuple(caveats)
        )
    inventory = firmware_inventory(machine, context, verify=False)
    wanted = tuple(
        r
        for r in inventory.requirements
        if r.identity is not None and r.identity.md5.lower() == identity.md5.lower()
    )
    if not wanted:
        caveats.append(
            _no_declaration(
                f"a file with this identity (known as {', '.join(identity.known_as)})",
                {"md5": identity.md5},
            )
        )
    return FirmwareIdentification(
        identity=identity, requirements=wanted, sources=context.sources, caveats=tuple(caveats)
    )
