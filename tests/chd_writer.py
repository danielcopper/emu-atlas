"""Write small CHD v5 images for the reader's tests — the layout libchdr reads, built from first principles.

The writer is the reader's mirror and nothing more: a header, a Huffman-coded
map whose sixteen codes are all four bits long (the simplest complete code the
map's RLE tree import accepts, libchdr_huffman.c:181-237), the CD codecs with
the sector bytes and the subcode compressed apart, and the track metadata
chdman writes. A frame whose parity is intact has its sync and parity
stripped and its bit set in the hunk's ECC bitmap, as chdman strips them, so
the reader's rebuild is what the hunk's CRC checks. ``cdzs`` is written as a
zstd frame of raw blocks, which any zstd decoder reads; no zstd encoder is
needed. Nothing here is a real disc.
"""

from __future__ import annotations

import binascii
import lzma
import struct
import zlib
from dataclasses import dataclass, field

from atlas import psx_chd

FRAME = psx_chd.FRAME_SIZE
SECTOR = psx_chd.SECTOR_SIZE
SUBCODE = psx_chd.SUBCODE_SIZE
FRAMES_PER_HUNK = 8
HUNK_BYTES = FRAMES_PER_HUNK * FRAME
METADATA_V2 = "TRACK:{number} TYPE:{mode} SUBTYPE:{subtype} FRAMES:{frames} PREGAP:{pregap} PGTYPE:{pgtype} PGSUB:RW POSTGAP:0"

# Map types (libchdr_chd.c:116-149).
NONE = 4
SELF = 5
PARENT = 6


def tag(text: str) -> int:
    return int.from_bytes(text.encode("latin-1"), "big")


def crc16(data: bytes) -> int:
    return binascii.crc_hqx(data, 0xFFFF)


def with_parity(track: bytes) -> bytes:
    """Every Mode 2 sector of *track* with the P and Q parity a real disc carries, so a codec can strip it."""
    out = bytearray(track)
    for start in range(0, len(out) - SECTOR + 1, SECTOR):
        sector = bytearray(out[start : start + SECTOR])
        if sector[15] == 2:
            psx_chd._ecc_generate(sector)  # pyright: ignore[reportPrivateUsage] - the parity the codec strips
            out[start : start + SECTOR] = sector
    return bytes(out)


def raw_lzma(data: bytes, size: int) -> bytes:
    dictionary = psx_chd._lzma_dictionary(size)  # pyright: ignore[reportPrivateUsage] - the reader's own derivation
    filters = [{"id": lzma.FILTER_LZMA1, "dict_size": dictionary, "lc": 3, "lp": 0, "pb": 2}]
    return lzma.compress(data, format=lzma.FORMAT_RAW, filters=filters)


def raw_deflate(data: bytes, _size: int = 0) -> bytes:
    compressor = zlib.compressobj(9, zlib.DEFLATED, -15)
    return compressor.compress(data) + compressor.flush()


def _runs(data: bytes) -> list[tuple[bool, bytes]]:
    """*data* cut into runs of one byte 16 long or more (RLE) and the stretches between them (raw)."""
    pieces: list[tuple[bool, bytes]] = []
    start = index = 0
    while index < len(data):
        end = index
        while end < len(data) and data[end] == data[index] and end - index < 128 * 1024:
            end += 1
        if end - index >= 16:
            if start < index:
                pieces.append((False, data[start:index]))
            pieces.append((True, data[index:end]))
            start = end
        index = end if end - index >= 16 else index + 1
    if start < len(data) or not pieces:
        pieces.append((False, data[start:]))
    return pieces


def zstd_raw_frame(data: bytes, _size: int = 0) -> bytes:
    """A zstd frame of raw and RLE blocks (RFC 8878 3.1.1): single segment, a 4-byte content size, no checksum."""
    frame = struct.pack("<IB", 0xFD2FB528, 0xA0) + struct.pack("<I", len(data))
    blocks = [(rle, piece[i : i + 128 * 1024]) for rle, piece in _runs(data) for i in range(0, len(piece), 128 * 1024)]
    blocks = blocks or [(False, b"")]
    for index, (rle, block) in enumerate(blocks):
        header = int(index == len(blocks) - 1) | (int(rle) << 1) | (len(block) << 3)
        frame += header.to_bytes(3, "little") + (block[:1] if rle else block)
    return frame


def unzstd_raw_frames(data: bytes) -> bytes:
    """The decoder side of :func:`zstd_raw_frame` — a stand-in provider where the runtime has no zstd."""
    out, position = b"", 0
    while position < len(data):
        assert int.from_bytes(data[position : position + 4], "little") == 0xFD2FB528
        position += 9
        while True:
            header = int.from_bytes(data[position : position + 3], "little")
            position += 3
            size, kind = header >> 3, header >> 1 & 3
            assert kind in (0, 1), "only raw and RLE blocks"
            out += data[position : position + 1] * size if kind else data[position : position + size]
            position += 1 if kind else size
            if header & 1:
                break
    return out


BASE_CODECS = {"cdlz": raw_lzma, "cdzl": raw_deflate, "cdzs": zstd_raw_frame}
SUBCODE_CODECS = {"cdlz": raw_deflate, "cdzl": raw_deflate, "cdzs": zstd_raw_frame}


def cd_hunk(codec: str, hunk: bytes, *, strip: bool = True) -> bytes:
    """One hunk in a CD codec: the ECC bitmap, the base length, the sector bytes, then the subcode."""
    frames = len(hunk) // FRAME
    bitmap = bytearray((frames + 7) // 8)
    sectors, subcode = bytearray(), bytearray()
    for frame in range(frames):
        sector = bytearray(hunk[frame * FRAME : frame * FRAME + SECTOR])
        rebuilt = bytearray(sector)
        rebuilt[:12] = b"\x00" + b"\xff" * 10 + b"\x00"
        psx_chd._ecc_generate(rebuilt)  # pyright: ignore[reportPrivateUsage] - what the reader will rebuild
        if strip and rebuilt == sector:
            bitmap[frame // 8] |= 1 << (frame % 8)
            sector[:12] = bytes(12)
            sector[0x81C:SECTOR] = bytes(SECTOR - 0x81C)
        sectors += sector
        subcode += hunk[frame * FRAME + SECTOR : (frame + 1) * FRAME]
    base = BASE_CODECS[codec](bytes(sectors), frames * SECTOR)
    rest = SUBCODE_CODECS[codec](bytes(subcode), frames * SUBCODE)
    return bytes(bitmap) + len(base).to_bytes(2, "big") + base + rest


@dataclass
class Hunk:
    """One map entry: how the hunk is stored. ``codec`` is a CD codec, ``"none"`` for stored as is, ``"self"``
    and ``"parent"`` for a copy (``offset`` the hunk or unit it copies), or raw bytes standing for a codec the
    reader does not decode."""

    codec: str
    offset: int = 0
    payload: bytes | None = None


@dataclass
class Chd:
    """A CHD v5 image under construction: track 1 from raw sectors, each hunk stored as told."""

    track: bytes
    codecs: tuple[str, ...] = ("cdlz", "cdzl", "cdzs")
    hunks: dict[int, Hunk] = field(default_factory=dict)
    default: str = "cdlz"
    sha1: bytes = b"\x11" * 20
    parent_sha1: bytes = bytes(20)
    metadata: list[tuple[bytes, str]] | None = None
    hunk_bytes: int = HUNK_BYTES
    unit_bytes: int = FRAME
    version: int = 5
    strip: bool = True
    widths: tuple[int, int, int] = (24, 24, 24)

    def frames(self) -> int:
        return len(self.track) // SECTOR

    def logical(self) -> bytes:
        """The uncompressed hunks: each sector followed by zeroed subcode, padded to whole hunks."""
        data = bytearray()
        for index in range(self.frames()):
            data += self.track[index * SECTOR : (index + 1) * SECTOR] + bytes(SUBCODE)
        data += bytes(-len(data) % self.hunk_bytes)
        return bytes(data)

    def track_metadata(self) -> list[tuple[bytes, str]]:
        if self.metadata is not None:
            return self.metadata
        text = METADATA_V2.format(number=1, mode="MODE2_RAW", subtype="NONE", frames=self.frames(), pregap=0,
                                  pgtype="MODE2_RAW")
        return [(b"CHT2", text)]

    def build(self) -> bytes:
        logical = self.logical()
        count = len(logical) // self.hunk_bytes
        header_size = 124
        meta = self._metadata_block(header_size)
        data_start = header_size + len(meta)
        blobs, entries = bytearray(), []
        for number in range(count):
            hunk = self.hunks.get(number, Hunk(self.default))
            original = logical[number * self.hunk_bytes : (number + 1) * self.hunk_bytes]
            entries.append(self._entry(hunk, original, data_start + len(blobs), blobs))
        map_offset = data_start + len(blobs)
        header = self._header(len(logical), map_offset, header_size if meta else 0)
        return header + meta + bytes(blobs) + self._map(entries, data_start)

    def build_uncompressed(self) -> bytes:
        """An uncompressed CHD: every hunk stored whole on a hunk boundary, the map one hunk number each."""
        logical = self.logical()
        count = len(logical) // self.hunk_bytes
        meta = self._metadata_block(124)
        first = -(-(124 + len(meta) + count * 4) // self.hunk_bytes)
        map_bytes = b"".join(struct.pack(">I", first + number) for number in range(count))
        body = self._header(len(logical), 124 + len(meta), 124 if meta else 0, slots=[0, 0, 0, 0]) + meta + map_bytes
        return body + bytes(first * self.hunk_bytes - len(body)) + logical

    def _entry(self, hunk: Hunk, original: bytes, offset: int, blobs: bytearray) -> tuple[int, int, int, int]:
        """``(type, length, offset, crc)`` as the map's raw form holds them, the stored bytes appended."""
        if hunk.codec == "self":
            return SELF, 0, hunk.offset, 0
        if hunk.codec == "parent":
            return PARENT, 0, hunk.offset, 0
        if hunk.codec == "none":
            blobs += original
            return NONE, self.hunk_bytes, offset, crc16(original)
        stored = hunk.payload if hunk.payload is not None else cd_hunk(hunk.codec, original, strip=self.strip)
        blobs += stored
        return self.codecs.index(hunk.codec), len(stored), offset, crc16(original)

    def _metadata_block(self, start: int) -> bytes:
        block = bytearray()
        items = self.track_metadata()
        for index, (name, text) in enumerate(items):
            payload = text.encode("latin-1") + b"\x00"
            following = start + len(block) + 16 + len(payload) if index + 1 < len(items) else 0
            block += name + struct.pack(">I", len(payload)) + struct.pack(">Q", following) + payload
        return bytes(block)

    def _header(self, logical: int, map_offset: int, meta_offset: int, slots: list[int] | None = None) -> bytes:
        if slots is None:
            slots = [tag(codec) for codec in self.codecs] + [0] * (4 - len(self.codecs))
        return (
            b"MComprHD"
            + struct.pack(">II", 124, self.version)
            + struct.pack(">4I", *slots)
            + struct.pack(">QQQ", logical, map_offset, meta_offset)
            + struct.pack(">II", self.hunk_bytes, self.unit_bytes)
            + bytes(20)
            + self.sha1
            + self.parent_sha1
        )

    def _map(self, entries: list[tuple[int, int, int, int]], first_offset: int) -> bytes:
        """The Huffman-coded map: sixteen 4-bit code lengths, each type in four bits, then each entry's fields."""
        bits = _BitWriter()
        for _ in range(16):
            bits.write(4, 4)
        for kind, *_ in entries:
            bits.write(kind, 4)
        length_bits, self_bits, parent_bits = self.widths
        raw = b""
        for kind, length, offset, crc in entries:
            if kind <= 3:
                bits.write(length, length_bits)
                bits.write(crc, 16)
            elif kind == NONE:
                bits.write(crc, 16)
            elif kind == SELF:
                bits.write(offset, self_bits)
            elif kind == PARENT:
                bits.write(offset, parent_bits)
            raw += bytes((kind,)) + length.to_bytes(3, "big") + offset.to_bytes(6, "big") + crc.to_bytes(2, "big")
        compressed = bits.bytes()
        head = (
            struct.pack(">I", len(compressed))
            + first_offset.to_bytes(6, "big")
            + struct.pack(">H", crc16(raw))
            + bytes((length_bits, self_bits, parent_bits, 0))
        )
        return head + compressed


class _BitWriter:
    def __init__(self) -> None:
        self._value = 0
        self._count = 0

    def write(self, value: int, count: int) -> None:
        self._value = (self._value << count) | (value & ((1 << count) - 1))
        self._count += count

    def bytes(self) -> bytes:
        pad = -self._count % 8
        return (self._value << pad).to_bytes((self._count + pad) // 8, "big")
