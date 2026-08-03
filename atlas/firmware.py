"""Firmware — which files the installed cores declare, and what state each is in.

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

The state model (:data:`FIRMWARE_STATES`) keeps the two apart. Four states
answer "what is the state of a file that was declared" — ``verified`` (present,
hash known, matches), ``mismatch`` (present, hash known, does **not** match),
``present`` (present, identity unverified — either no hash is known or none was
asked for), ``missing`` (declared, not on disk) — and one answers "what else is
lying around": ``undeclared``.

*Unknown* is deliberately **not** a state. Having no declaration to check
against is a property of the answer, not of a file, so it is a
:class:`~atlas.placement.Caveat` (:data:`CAVEAT_NO_FIRMWARE_DECLARATION`)
attached to a report whose file list is **empty**. A caller that ignores
caveats then gets an empty list rather than a satisfied one: empty is honest,
"nothing missing" would be a lie.
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
from atlas.machine import DIGEST_MD5, KIND_FILE, Machine
from atlas.placement import Caveat

FirmwareState = Literal["verified", "mismatch", "present", "missing", "undeclared"]

STATE_VERIFIED: FirmwareState = "verified"
STATE_MISMATCH: FirmwareState = "mismatch"
STATE_PRESENT: FirmwareState = "present"
STATE_MISSING: FirmwareState = "missing"
STATE_UNDECLARED: FirmwareState = "undeclared"

FIRMWARE_STATES = ("verified", "mismatch", "present", "missing", "undeclared")

# Caveat codes — stable identifiers, like the placement ones.
CAVEAT_NO_FIRMWARE_DECLARATION = "no-firmware-declaration"
CAVEAT_INFO_PATH_UNRESOLVED = "info-path-unresolved"
CAVEAT_CORE_DIR_UNRESOLVED = "core-dir-unresolved"
CAVEAT_FIRMWARE_ROOT_MISSING = "firmware-root-missing"

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


class FirmwareHashes:
    """Read-only view over the packaged hash table — world knowledge, keyed as upstream keys it."""

    def __init__(self, files: dict[str, FirmwareHash], meta: dict[str, Any]) -> None:
        self._files = files
        self._meta = meta

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

    def for_path(self, path: str) -> FirmwareHash | None:
        """The identity of a declared path — matched by the path, then by base name.

        Upstream keys 91 of its entries by a relative path and the rest by a
        bare file name, with no base name shared between the two forms, so
        trying both is unambiguous. ``None`` is a normal, expected answer:
        ``System.dat`` covers only part of the firmware universe.
        """
        return self._files.get(path) or self._files.get(os.path.basename(path))


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
    comes from the machine instead (:func:`read_declarations`).
    """
    if text is None:
        text = importlib.resources.files("atlas").joinpath("data", "firmware_hashes.json").read_text(encoding="utf-8")
    data = json.loads(text)
    raw_files: dict[str, dict[str, Any]] = data.get("files", {})
    files = {name: _hash_from_raw(name, raw) for name, raw in raw_files.items()}
    return FirmwareHashes(files, data.get("_meta", {}))


# Libretro ``systemname`` strings mapped to atlas platform slugs. World
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

# Per-file-name platform overrides for multi-system cores. A core covering
# several systems carries one ``systemname``, but its firmware belongs to
# different platforms — mGBA declares the Game Boy boot ROMs under "Game
# Boy/Game Boy Color/Game Boy Advance". World knowledge, same as the map above.
FIRMWARE_PLATFORM_OVERRIDE: Mapping[str, str] = {
    # Game Boy family (mGBA, VBA-M, Mesen-S, Gambatte, …)
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


def platform_for(file_name: str, systemname: str) -> str:
    """The atlas platform slug a declared file belongs to.

    The per-file override wins (multi-system cores), then the ``systemname``
    map. A ``systemname`` the map does not know is *slugified* rather than
    dropped — a mechanical normalization of a string read off the machine, so
    every declaration stays reachable by some slug — and an empty
    ``systemname`` yields ``_unknown``, matching the packaged registry's own
    catch-all.
    """
    override = FIRMWARE_PLATFORM_OVERRIDE.get(file_name)
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
    ``required`` is ``firmwareN_opt`` inverted: libretro treats a missing
    ``_opt`` as *not optional*, so an absent flag means required.
    """

    path: str
    file_name: str
    description: str
    required: bool
    core: str
    systemname: str
    platform: str


def _declarations_in(text: str, core: str) -> tuple[FirmwareDeclaration, ...]:
    """Parse one ``.info`` file's firmware block into declarations."""
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
                required=not optional,
                core=core,
                systemname=systemname,
                platform=platform_for(file_name, systemname),
            )
        )
    return tuple(declarations)


def read_declarations(
    machine: Machine, info_dir: str, *, core_dir: str | None = None
) -> tuple[FirmwareDeclaration, ...]:
    """Read every firmware declaration the *installed* cores make, live.

    Globs ``info_dir`` for ``.info`` files and parses each one. When *core_dir*
    is given, a declaration counts only if the core's ``.so`` is actually
    there: an ``.info`` set routinely covers more cores than an installation
    ships (RetroDECK: 292 ``.info`` against 211 ``.so``), and firmware demanded
    by a core that cannot run is exactly the noise a shipped table produced.
    With *core_dir* ``None`` nothing is filtered — the caller states that gap
    as a caveat rather than having it silently narrow the answer.

    libretro's two template ``.info`` files are dropped by name: they declare
    the literal placeholder ``filename.ext``.
    """
    declarations: list[FirmwareDeclaration] = []
    for info_path in machine.glob(os.path.join(_glob_escape(info_dir), "*.info")):
        stem = os.path.basename(info_path)[: -len(".info")]
        if stem in TEMPLATE_INFO_STEMS:
            continue
        if core_dir is not None and machine.path_kind(os.path.join(core_dir, f"{stem}.so")) != KIND_FILE:
            continue
        text = machine.read_text(info_path).text
        if text is None:
            continue
        declarations.extend(_declarations_in(text, stem))
    return tuple(declarations)


@dataclass(frozen=True, slots=True)
class FirmwareFile:
    """One firmware file's state under the installation's system directory.

    ``path`` is relative to the report's ``root``. ``required`` is the OR over
    every installed core that declares this path — one core calling a file
    optional does not make it optional for another. ``hash_known`` says whether
    the packaged table can identify the file at all: it is the difference
    between "not checked" and "not checkable", which ``present`` alone cannot
    carry. An ``undeclared`` file has no declaring core, is never required, and
    its description is empty.
    """

    path: str
    file_name: str
    description: str
    required: bool
    state: FirmwareState
    hash_known: bool
    cores: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.state not in FIRMWARE_STATES:
            raise ValueError(f"FirmwareFile: state must be one of {FIRMWARE_STATES}, got {self.state!r}")
        if self.state == STATE_UNDECLARED and (self.required or self.cores):
            raise ValueError("FirmwareFile: an undeclared file has no declaring core and is never required")


@dataclass(frozen=True, slots=True)
class FirmwareReport:
    """What the installed cores want, where it goes, and what is actually there.

    ``root`` is the resolved system directory the files are relative to, or
    ``None`` when the configs do not state one — then ``files`` is empty and a
    caveat says why, because nothing can be checked without it. ``hash_checked``
    records whether identity verification ran at all, so a list of ``present``
    files can never be mistaken for a list of verified ones. ``sources`` is the
    provenance trail; ``caveats`` states every degradation explicitly.
    """

    root: str | None
    files: tuple[FirmwareFile, ...]
    hash_checked: bool
    sources: tuple[str, ...]
    caveats: tuple[Caveat, ...]


def _observed_state(
    machine: Machine, full_path: str, identity: FirmwareHash | None, *, verify: bool
) -> FirmwareState:
    """The state of a declared file, given what the machine and the table say."""
    if machine.path_kind(full_path) != KIND_FILE:
        return STATE_MISSING
    if not verify or identity is None:
        return STATE_PRESENT
    # Size is a free pre-filter: a wrong size settles the question without
    # reading a byte of the file.
    size = machine.file_size(full_path)
    if size is not None and size != identity.size:
        return STATE_MISMATCH
    digest = machine.file_digest(full_path, DIGEST_MD5)
    if digest is None:
        # Present but unreadable: identity stays unverified, never assumed.
        return STATE_PRESENT
    return STATE_VERIFIED if digest.lower() == identity.md5.lower() else STATE_MISMATCH


def _undeclared_files(
    machine: Machine, root: str, declared_paths: set[str], hashes: FirmwareHashes
) -> list[FirmwareFile]:
    """Files sitting where a declaration points, that no installed core declares.

    The scan is bounded to the directories declarations actually reference —
    the system directory itself plus every subdirectory named in a
    ``firmwareN_path``. An unbounded walk would mark thousands of files (whole
    core data trees live under the same root) and drown the signal; this bound
    needs no exclusion list and grows on its own as cores declare new paths.
    """
    directories = {""} | {os.path.dirname(p) for p in declared_paths if os.path.dirname(p)}
    found: list[FirmwareFile] = []
    for relative_dir in sorted(directories):
        directory = os.path.join(root, relative_dir) if relative_dir else root
        for entry in machine.glob(os.path.join(_glob_escape(directory), "*")):
            if machine.path_kind(entry) != KIND_FILE:
                continue
            relative = os.path.join(relative_dir, os.path.basename(entry)) if relative_dir else os.path.basename(entry)
            if relative in declared_paths:
                continue
            file_name = os.path.basename(entry)
            found.append(
                FirmwareFile(
                    path=relative,
                    file_name=file_name,
                    description="",
                    required=False,
                    state=STATE_UNDECLARED,
                    hash_known=hashes.for_path(relative) is not None,
                    cores=(),
                )
            )
    return found


def resolve_firmware(
    machine: Machine,
    *,
    root: str | None,
    declarations: tuple[FirmwareDeclaration, ...],
    hashes: FirmwareHashes,
    platform: str | None = None,
    verify: bool = False,
    sources: tuple[str, ...] = (),
    caveats: tuple[Caveat, ...] = (),
) -> FirmwareReport:
    """Compose the report: live declarations + packaged identities + what is on disk.

    *platform* filters the declarations to one platform slug; ``None`` asks the
    whole-installation question and is the only form that reports
    ``undeclared`` files — "what else is lying around" is a property of the
    system directory, not of a platform. A selection that ends up empty yields
    an **empty file list** plus :data:`CAVEAT_NO_FIRMWARE_DECLARATION`; it
    never yields a satisfied-looking answer.
    """
    all_caveats = list(caveats)
    if root is None:
        return FirmwareReport(
            root=None,
            files=(),
            hash_checked=False,
            sources=tuple(sources),
            caveats=tuple(all_caveats),
        )

    selected = [d for d in declarations if platform is None or d.platform == platform]
    by_path: dict[str, list[FirmwareDeclaration]] = {}
    for declaration in selected:
        by_path.setdefault(declaration.path, []).append(declaration)

    files: list[FirmwareFile] = []
    for path, group in by_path.items():
        identity = hashes.for_path(path)
        # The longest description is the most informative one; cores describe
        # the same file with varying detail.
        description = max((d.description for d in group), key=len, default="")
        files.append(
            FirmwareFile(
                path=path,
                file_name=group[0].file_name,
                description=description,
                required=any(d.required for d in group),
                state=_observed_state(machine, os.path.join(root, path), identity, verify=verify),
                hash_known=identity is not None,
                cores=tuple(sorted({d.core for d in group})),
            )
        )

    if platform is None:
        files.extend(_undeclared_files(machine, root, {d.path for d in declarations}, hashes))

    if not by_path:
        subject = f"firmware for platform {platform!r}" if platform is not None else "any firmware"
        all_caveats.append(
            Caveat(
                CAVEAT_NO_FIRMWARE_DECLARATION,
                f"no installed core declares {subject} — there is no declaration to check against, so "
                "nothing here is a required-and-satisfied file; anything listed is undeclared",
                {"platform": platform} if platform is not None else {},
            )
        )

    return FirmwareReport(
        root=root,
        files=tuple(sorted(files, key=lambda f: f.path)),
        hash_checked=verify,
        sources=tuple(sources),
        caveats=tuple(all_caveats),
    )
