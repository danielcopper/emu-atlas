"""Write small PBP images for the reader's tests — the layout PSX2PSP and pop-fe write, built from first principles.

The writer is the reader's mirror and nothing more: a PBP header, a
``PARAM.SFO`` of the entries asked for, an optional title header with its
table of disc offsets, and one ISO header per disc — the table of contents at
``+0x800``, the block base at ``+0xBFC``, the block table at ``+0x4000`` and
the blocks behind the base, each raw deflate or stored. The track is padded
with empty sectors to whole 16-sector blocks, and the entry after the last
block states where that block ends, so both readers find each block's size.
Nothing here is a real disc.
"""

from __future__ import annotations

import struct
import zlib
from dataclasses import dataclass, field

from atlas import psx_pbp

SECTOR = psx_pbp.SECTOR_SIZE
BLOCK = psx_pbp.BLOCK_SIZE
DATA_BASE = 0x100000
TITLE_MAGIC = b"PSTITLEIMG000000"
IMAGE_MAGIC = b"PSISOIMG0000"
PGD = b"\x00PGD"
SFO_STRING = 0x0204
SFO_U32 = 0x0404
DEFAULT_SFO: dict[bytes, bytes | int] = {b"BOOTABLE": 1, b"CATEGORY": b"ME", b"TITLE": b"Game"}


def bcd(value: int) -> int:
    return (value // 10) << 4 | value % 10


def msf(frames: int) -> bytes:
    return bytes((bcd(frames // 4500), bcd(frames // 75 % 60), bcd(frames % 75)))


def toc_entry(*, kind: int = 0x41, point: int, pregap: bytes = b"\x00\x00\x00", userdata: bytes = b"\x00\x00\x00") -> bytes:
    return bytes((kind, 0, point)) + pregap + b"\x00" + userdata


def deflate(data: bytes) -> bytes:
    compressor = zlib.compressobj(9, zlib.DEFLATED, -15)
    return compressor.compress(data) + compressor.flush()


def sfo(entries: dict[bytes, bytes | int], *, types: dict[bytes, int] | None = None, sizes: dict[bytes, int] | None = None) -> bytes:
    """A PARAM.SFO: header, index, keys, data — a string stored with its NUL and stated as that long."""
    keys = b""
    data = b""
    index = b""
    for key, value in entries.items():
        stored = struct.pack("<I", value) if isinstance(value, int) else value + b"\x00"
        kind = (types or {}).get(key, SFO_U32 if isinstance(value, int) else SFO_STRING)
        size = (sizes or {}).get(key, len(stored))
        index += struct.pack("<HHIII", len(keys), kind, size, len(stored), len(data))
        keys += key + b"\x00"
        data += stored
    key_table = 0x14 + len(index)
    data_table = key_table + len(keys)
    return b"\x00PSF" + struct.pack("<IIII", 0x101, key_table, data_table, len(entries)) + index + keys + data


@dataclass
class Disc:
    """One ISO header and its blocks: the track, how it is stored, and what its header states."""

    track: bytes
    stored: bool = False
    encrypted: bool = False
    magic: bytes = IMAGE_MAGIC
    base: int = DATA_BASE
    toc: list[bytes] | None = None
    # Each block's table entry, overriding the written one (offset, size).
    entries: dict[int, tuple[int, int]] = field(default_factory=dict)
    # Each block's bytes, overriding the encoded ones.
    blocks: dict[int, bytes] = field(default_factory=dict)
    closing: bool = True

    def sectors(self) -> int:
        return len(self.track) // SECTOR

    def default_toc(self) -> list[bytes]:
        frames = self.sectors()
        return [
            toc_entry(point=0xA0, userdata=bytes((bcd(1), 0x20, 0))),
            toc_entry(point=0xA1, userdata=bytes((bcd(1), 0, 0))),
            toc_entry(point=0xA2, userdata=msf(frames)),
            toc_entry(point=0x01, pregap=msf(0), userdata=msf(150)),
        ]

    def encode(self) -> bytes:
        """The ISO header and the blocks behind it."""
        padded = self.track + bytes(-len(self.track) % BLOCK)
        payloads = []
        for number in range(len(padded) // BLOCK):
            block = padded[number * BLOCK : (number + 1) * BLOCK]
            payloads.append(self.blocks.get(number, block if self.stored else deflate(block)))
        assert self.base >= 0x100000
        header = bytearray(self.base)
        header[0 : len(self.magic)] = self.magic
        if self.encrypted:
            header[0x400:0x404] = PGD
        toc = b"".join(self.toc if self.toc is not None else self.default_toc())
        header[0x800 : 0x800 + len(toc)] = toc
        header[0xBFC:0xC00] = struct.pack("<I", self.base)
        offset = 0
        for number, payload in enumerate(payloads):
            entry = self.entries.get(number, (offset, len(payload)))
            header[0x4000 + 32 * number : 0x4000 + 32 * number + 6] = struct.pack("<IH", *entry)
            offset += len(payload)
        if self.closing:
            at = 0x4000 + 32 * len(payloads)
            header[at : at + 4] = struct.pack("<I", offset)
        return bytes(header) + b"".join(payloads)


@dataclass
class Pbp:
    """A whole PBP: its magic, its PARAM.SFO, and one disc or a title header over several."""

    discs: list[Disc]
    magic: bytes = b"\x00PBP"
    param_sfo: bytes = field(default_factory=lambda: sfo(DEFAULT_SFO))
    title: bool = False
    title_magic: bytes = TITLE_MAGIC
    title_encrypted: bool = False

    def encode(self) -> bytes:
        sfo_at = 0x28
        psar = (sfo_at + len(self.param_sfo) + 0xFF) & ~0xFF
        header = self.magic + struct.pack("<I", 0x10000) + struct.pack("<8I", sfo_at, *([psar] * 7))
        body = bytearray(header + self.param_sfo)
        body += bytes(psar - len(body))
        if not self.title:
            body += self.discs[0].encode()
            return bytes(body)
        title = bytearray(0x400)
        title[0 : len(self.title_magic)] = self.title_magic
        images = [disc.encode() for disc in self.discs]
        at = len(title)
        offsets = []
        for image in images:
            offsets.append(at)
            at += len(image)
        table = PGD if self.title_encrypted else b"".join(struct.pack("<I", value) for value in offsets[:5])
        title[0x200 : 0x200 + len(table)] = table
        body += title + b"".join(images)
        return bytes(body)
