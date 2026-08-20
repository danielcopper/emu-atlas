"""Regenerate the AppImage fixtures the squashfs reader is tested against.

Two tiny images with identical content, one per codec the reader speaks:
``esde-like.gzip.appimage`` decompresses everywhere (zlib), and
``esde-like.zstd.appimage`` needs ``compression.zstd`` (Python >= 3.14) — the
pair is what lets the reader's *logic* be tested on every interpreter while
the codec gate is tested exactly where it applies.

Each image is a real mksquashfs output (block size 4 KiB, so ``big.bin``
spans data blocks while the small files land in a shared fragment) behind a
minimal 64-bit ELF header whose section-header fields place the image at
byte 4096 — the same ``e_shoff + e_shentsize * e_shnum`` arithmetic a real
AppImage runtime implies. The content covers what the resolver walk needs:
nested directories, a fragment-tail file, a multi-block file, and the three
symlink shapes (absolute, relative, ``..``-relative).

Run from the repository root; needs ``mksquashfs`` (squashfs-tools) on PATH:

    python tests/data/make_appimage_fixtures.py
"""

from __future__ import annotations

import os
import shutil
import struct
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent

CATALOGUE = """<?xml version="1.0"?>
<systemList>
  <system><name>gba</name><fullname>Game Boy Advance</fullname><path>%ROMPATH%/gba</path>
    <extension>.gba</extension>
    <command label="mGBA">%EMULATOR_RETROARCH% -L %CORE_RETROARCH%/mgba_libretro.so %ROM%</command>
    <platform>gba</platform>
  </system>
</systemList>
"""


def _tree(root: Path) -> None:
    app = root / "usr" / "share" / "es-de" / "resources" / "systems" / "linux"
    app.mkdir(parents=True)
    (app / "es_systems.xml").write_text(CATALOGUE, encoding="utf-8")
    # Deterministic multi-block content: 10 KiB spans three 4-KiB blocks.
    (root / "usr" / "share" / "big.bin").write_bytes(
        b"".join(bytes([i % 251]) * 64 for i in range(160))
    )
    (root / "not-text.bin").write_bytes(b"\xff\xfe\x00\x01binary")
    links = root / "usr" / "links"
    links.mkdir()
    os.symlink("/usr/share/es-de/resources/systems/linux/es_systems.xml", links / "absolute")
    os.symlink("../share/es-de/resources/systems/linux/es_systems.xml", links / "updir")
    os.symlink("share/es-de", root / "usr" / "esde-dir")


def _elf_prefix(image_offset: int) -> bytes:
    header = bytearray(64)
    header[:4] = b"\x7fELF"
    header[4] = 2  # 64-bit
    header[5] = 1  # little endian
    header[8:11] = b"AI\x02"  # AppImage type-2 marker, as the real tooling stamps
    struct.pack_into("<Q", header, 0x28, image_offset - 64)  # e_shoff
    struct.pack_into("<H", header, 0x3A, 64)  # e_shentsize
    struct.pack_into("<H", header, 0x3C, 1)  # e_shnum
    return bytes(header).ljust(image_offset, b"\x00")


def main() -> int:
    if shutil.which("mksquashfs") is None:
        raise SystemExit("mksquashfs (squashfs-tools) is required to regenerate the fixtures")
    for codec in ("gzip", "zstd"):
        with tempfile.TemporaryDirectory() as scratch:
            root = Path(scratch) / "root"
            root.mkdir()
            _tree(root)
            image = Path(scratch) / "image.squashfs"
            subprocess.run(
                [
                    "mksquashfs",
                    str(root),
                    str(image),
                    "-comp",
                    codec,
                    "-b",
                    "4096",
                    "-all-root",
                    "-no-xattrs",
                    "-noappend",
                    "-quiet",
                ],
                check=True,
            )
            target = HERE / f"esde-like.{codec}.appimage"
            target.write_bytes(_elf_prefix(4096) + image.read_bytes())
            print(f"wrote {target.name}: {target.stat().st_size} bytes")
    return 0


if __name__ == "__main__":
    sys.exit(main())
