"""The strtoll tripwire: a Dolphin-family build must call the strtoll atlas reads slots with.

Dolphin reads a slot device id through ``TryParse``, which hands the value to
``std::strtoll(str, &end, 0)`` (StringUtil.h:63-100 at dolphin 2603a). Which C
function that call reaches is not in the source: glibc 2.38 and later compile
``strtoll`` into ``__isoc23_strtoll`` under ``_GNU_SOURCE`` — which g++ always
defines — and that one reads a ``0b`` binary prefix in base 0 where the older
one stops at the ``b``. So whether ``SlotA = 0b1001`` is the GBA cartridge
adapter or slot A's default is a fact of the build, and atlas answers the
former (:func:`atlas.installations._dolphin_literal_base`) because every
Dolphin-family build it describes imports ``__isoc23_strtoll`` and no plain
``strtoll``.

So where such a build is deployed, this reads the binary's dynamic symbol
table — the ELF's own ``.dynsym`` and the string table it links, parsed here
with ``struct`` rather than through ``nm``, nothing run — and holds it to that
import. A build linked against an older glibc, or one that also imports the
plain function (so that which one ``TryParse`` calls is no longer settled by
the table), fails here rather than being read with a prefix it does not know.

**What this cannot see.** The table says which ``strtoll`` the binary can
call, not which call site calls which; it is conclusive only because there is
exactly one ``strtoll`` in it. A binary without a section header table (a
stripped-to-the-bone build) cannot be read this way and is refused by name.

Skipped where no Dolphin-family build is deployed, like the tripwires beside
it; the builds are the four :data:`atlas.installations._DOLPHIN_GAME_LAYERS`
states lines for, and a row added there without a binary here fails.
"""

from __future__ import annotations

import struct
from collections.abc import Mapping
from pathlib import Path

import pytest

from atlas.installations import (
    _DOLPHIN_GAME_LAYERS,  # pyright: ignore[reportPrivateUsage] - the build rows this holds to a binary
)

COMPONENTS = Path(
    "/var/lib/flatpak/app/net.retrodeck.retrodeck/current/active/files/retrodeck/components"
)
FLATPAK_ROOTS = (
    Path.home() / ".local" / "share" / "flatpak" / "app",
    Path("/var/lib/flatpak/app"),
)

C23_STRTOLL = "__isoc23_strtoll"
PLAIN_STRTOLL = "strtoll"

# ELF64 constants (the System V gABI): the section type of the dynamic symbol
# table, and the section index an undefined — imported — symbol carries.
_SHT_DYNSYM = 11
_SHN_UNDEF = 0
_ELF_HEADER = struct.Struct("<16sHHIQQQIHHHHHH")
_SECTION_HEADER = struct.Struct("<IIQQQQIIQQ")
_SYMBOL = struct.Struct("<IBBHQQ")


def _flatpak_binary(app_id: str, path: str) -> Path:
    """Where a build living in an app of its own sits — user install before system."""
    for root in FLATPAK_ROOTS:
        candidate = root / app_id / "current" / "active" / "files" / path
        if candidate.is_file():
            return candidate
    return FLATPAK_ROOTS[0] / app_id / "current" / "active" / "files" / path


# Keyed the way the build rows are: the token, and the flatpak app id where the
# build is an app of its own (``None`` for the arrangement's bundled build).
# The component paths are the settings table's own; the Flathub apps install
# their emulator as bin/dolphin-emu (observed in both deployed apps).
BUILD_BINARIES: Mapping[tuple[str, str | None], tuple[str | None, str]] = {
    ("DOLPHIN", None): (None, "dolphin/bin/dolphin-emu"),
    ("DOLPHIN", "org.DolphinEmu.dolphin-emu"): ("org.DolphinEmu.dolphin-emu", "bin/dolphin-emu"),
    ("PRIMEHACK", None): (None, "primehack/bin/primehack"),
    ("PRIMEHACK", "io.github.shiiion.primehack"): ("io.github.shiiion.primehack", "bin/dolphin-emu"),
}


def _binary_of(app_id: str | None, path: str) -> Path:
    return COMPONENTS / path if app_id is None else _flatpak_binary(app_id, path)


def undefined_dynamic_symbols(blob: bytes) -> frozenset[str]:
    """The names a little-endian ELF64 imports: its ``.dynsym`` entries with no section."""
    if len(blob) < _ELF_HEADER.size or blob[:4] != b"\x7fELF":
        raise ValueError("not an ELF file")
    if blob[4] != 2 or blob[5] != 1:
        raise ValueError("not a little-endian 64-bit ELF, the only shape this reader parses")
    header = _ELF_HEADER.unpack_from(blob)
    shoff, shentsize, shnum = header[6], header[11], header[12]
    if shoff == 0 or shnum == 0:
        raise ValueError("the ELF carries no section header table to find .dynsym by")
    sections = [_SECTION_HEADER.unpack_from(blob, shoff + i * shentsize) for i in range(shnum)]
    tables = [section for section in sections if section[1] == _SHT_DYNSYM]
    if not tables:
        raise ValueError("the ELF carries no dynamic symbol table")
    names: set[str] = set()
    for _, _, _, _, offset, size, link, _, _, entsize in tables:
        strings = sections[link][4]
        for at in range(offset + entsize, offset + size, entsize):
            name, _, _, index, _, _ = _SYMBOL.unpack_from(blob, at)
            if index == _SHN_UNDEF and name:
                start = strings + name
                names.add(blob[start : blob.index(b"\x00", start)].decode("ascii"))
    return frozenset(names)


def import_disagreements(imports: Mapping[str, frozenset[str]]) -> list[str]:
    """The builds whose imports do not settle ``strtoll`` as the C23 one.

    *imports* maps a build's label to what its binary imports. A build that is
    not deployed is absent and not judged. Factored out of the assertion so
    the rule can be watched failing without a deployed build.
    """
    wrong: list[str] = []
    for label, names in imports.items():
        if C23_STRTOLL not in names:
            wrong.append(f"{label}: imports no {C23_STRTOLL}")
        if PLAIN_STRTOLL in names:
            wrong.append(f"{label}: imports the plain {PLAIN_STRTOLL}, which reads no 0b prefix")
    return wrong


def deployed_imports() -> dict[str, frozenset[str]]:
    """What each Dolphin-family build deployed here imports, by binary path."""
    imports: dict[str, frozenset[str]] = {}
    for app_id, path in BUILD_BINARIES.values():
        binary = _binary_of(app_id, path)
        if binary.is_file():
            imports[str(binary)] = undefined_dynamic_symbols(binary.read_bytes())
    return imports


def _imports_or_skip() -> dict[str, frozenset[str]]:
    imports = deployed_imports()
    if not imports:
        pytest.skip("no Dolphin-family build is deployed here")
    return imports


def _synthetic_elf(undefined: tuple[str, ...], defined: tuple[str, ...]) -> bytes:
    """A minimal ELF64: a null section, a ``.dynsym`` and the string table it links."""
    strings = b"\x00"
    symbols = _SYMBOL.pack(0, 0, 0, 0, 0, 0)
    for names, index in ((undefined, _SHN_UNDEF), (defined, 7)):
        for name in names:
            symbols += _SYMBOL.pack(len(strings), 0x12, 0, index, 0, 0)
            strings += name.encode("ascii") + b"\x00"
    symbols_at = _ELF_HEADER.size
    strings_at = symbols_at + len(symbols)
    sections_at = strings_at + len(strings)
    header = _ELF_HEADER.pack(
        b"\x7fELF\x02\x01\x01" + b"\x00" * 9, 3, 62, 1, 0, 0, sections_at, 0,
        _ELF_HEADER.size, 0, 0, _SECTION_HEADER.size, 3, 0,
    )
    sections = (
        _SECTION_HEADER.pack(0, 0, 0, 0, 0, 0, 0, 0, 0, 0)
        + _SECTION_HEADER.pack(0, _SHT_DYNSYM, 0, 0, symbols_at, len(symbols), 2, 1, 8, _SYMBOL.size)
        + _SECTION_HEADER.pack(0, 3, 0, 0, strings_at, len(strings), 0, 0, 1, 0)
    )
    return header + symbols + strings + sections


class TestTheReader:
    """The ELF reader, over binaries written here — no machine involved."""

    def test_the_imports_are_the_undefined_symbols_only(self):
        blob = _synthetic_elf((C23_STRTOLL, "strtol"), ("main",))
        assert undefined_dynamic_symbols(blob) == {C23_STRTOLL, "strtol"}

    def test_a_file_that_is_not_elf_is_refused(self):
        with pytest.raises(ValueError, match="not an ELF"):
            undefined_dynamic_symbols(b"#!/bin/sh\n")

    def test_a_32_bit_elf_is_refused(self):
        blob = bytearray(_synthetic_elf((C23_STRTOLL,), ()))
        blob[4] = 1
        with pytest.raises(ValueError, match="64-bit"):
            undefined_dynamic_symbols(bytes(blob))


class TestTheCheckItself:
    """The rule, over import sets written here — no machine involved."""

    def test_a_build_importing_only_the_c23_strtoll_passes(self):
        assert import_disagreements({"demo": frozenset({C23_STRTOLL, "strtol"})}) == []

    def test_a_build_importing_only_the_plain_strtoll_is_named_twice(self):
        named = import_disagreements({"demo": frozenset({PLAIN_STRTOLL})})
        assert [entry.split(":")[0] for entry in named] == ["demo", "demo"]

    def test_a_build_importing_both_is_named(self):
        named = import_disagreements({"demo": frozenset({C23_STRTOLL, PLAIN_STRTOLL})})
        assert named == [f"demo: imports the plain {PLAIN_STRTOLL}, which reads no 0b prefix"]

    def test_a_build_that_is_not_deployed_is_not_judged(self):
        assert import_disagreements({}) == []


class TestTheBuildTable:
    def test_every_build_row_has_a_binary(self):
        # The build rows are what the answer states lines for; a row this file
        # has no binary for would be a build read with a prefix nothing holds.
        assert set(BUILD_BINARIES) == set(_DOLPHIN_GAME_LAYERS)


class TestEveryDeployedBuildCallsTheC23Strtoll:
    """Machine-bound: what the binaries here actually import."""

    def test_every_deployed_build_imports_only_the_c23_strtoll(self):
        # Not vacuous where the reader reads nothing: an empty import set is
        # a build importing no __isoc23_strtoll, and is named.
        assert import_disagreements(_imports_or_skip()) == []
