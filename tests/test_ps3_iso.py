"""Tests for atlas.ps3_iso — RPCS3's two magic blocks, read out of hand-built ISO 9660 images.

Every image here is laid out sector by sector the way ``iso_archive`` reads one
(``rpcs3/Loader/ISO.cpp:1216-1311`` at d5213de6): volume descriptors from
sector 16 with the root directory record at byte 156, a terminator, then
directory extents of 33-byte-header records and file data. Nothing is borrowed
from a real disc.
"""

from __future__ import annotations

import io
import struct

import pytest

from atlas import ps3_file, ps3_iso

SECTOR = ps3_iso.SECTOR
CLEAR_LICENCE = b"PS3LICDA" + b"\x00" * 24
CLEAR_EBOOT = b"SCE\x00" + b"\x00" * 28
SCRAMBLED = bytes(range(32))


def record(name: bytes, sector: int, size: int, *, directory: bool, length: int | None = None) -> bytes:
    """One directory record: length, both-endian sector and size, the flags, the name — *length* overrides the
    length byte, for a record RPCS3 refuses."""
    body = (
        bytes([0])
        + struct.pack("<I", sector) + struct.pack(">I", sector)
        + struct.pack("<I", size) + struct.pack(">I", size)
        + bytes(7)
        + bytes([0b10 if directory else 0, 0, 0])
        + struct.pack("<H", 1) + struct.pack(">H", 1)
        + bytes([len(name)])
        + name
    )  # fmt: skip
    total = 1 + len(body)
    total += total % 2
    return (bytes([total if length is None else length]) + body).ljust(total, b"\x00")


class Image:
    """Sectors laid out in order: descriptors, then whatever the test places."""

    def __init__(self) -> None:
        self.sectors: dict[int, bytes] = {}
        self.next = 20

    def place(self, data: bytes) -> tuple[int, int]:
        """*data* at the next free sectors — its first sector and its size."""
        start = self.next
        count = max(1, -(-len(data) // SECTOR))
        for index in range(count):
            self.sectors[start + index] = data[index * SECTOR : (index + 1) * SECTOR]
        self.next += count
        return start, len(data)

    def directory(self, entries: list[bytes], parent: tuple[int, int] | None = None) -> tuple[int, int]:
        """A directory extent holding ``.``, ``..`` and *entries*, padded to whole sectors."""
        start = self.next
        own = record(b"\x00", start, SECTOR, directory=True)
        up = record(b"\x01", *(parent or (start, SECTOR)), directory=True)
        data = own + up + b"".join(entries)
        return self.place(data.ljust(-(-len(data) // SECTOR) * SECTOR, b"\x00"))

    def descriptor(self, sector: int, kind: int, root: tuple[int, int] | None = None) -> None:
        data = bytearray(SECTOR)
        data[0] = kind
        data[1:6] = b"CD001"
        if root is not None:
            root_record = record(b"\x00", root[0], root[1], directory=True)
            data[156 : 156 + len(root_record)] = root_record
        self.sectors[sector] = bytes(data)

    def bytes(self, regions: int = 2) -> bytes:
        last = max(self.sectors)
        out = bytearray((last + 1) * SECTOR)
        out[0:4] = regions.to_bytes(4, "big")
        for index, data in self.sectors.items():
            out[index * SECTOR : index * SECTOR + len(data)] = data
        return bytes(out)


def game(image: Image, licence: bytes | None, eboot: bytes | None, *, name=lambda n: n) -> tuple[int, int]:
    """PS3_GAME/LICDIR/LIC.DAT and PS3_GAME/USRDIR/EBOOT.BIN, each where given — the root's extent."""
    usrdir_entries = []
    if eboot is not None:
        usrdir_entries.append(record(name(b"EBOOT.BIN;1"), *image.place(eboot), directory=False))
    licdir_entries = []
    if licence is not None:
        licdir_entries.append(record(name(b"LIC.DAT;1"), *image.place(licence), directory=False))
    usrdir = image.directory(usrdir_entries)
    licdir = image.directory(licdir_entries)
    ps3_game = image.directory(
        [record(name(b"LICDIR"), *licdir, directory=True), record(name(b"USRDIR"), *usrdir, directory=True)]
    )
    return image.directory([record(name(b"PS3_GAME"), *ps3_game, directory=True)])


def standard(licence: bytes | None, eboot: bytes | None) -> bytes:
    image = Image()
    root = game(image, licence, eboot)
    image.descriptor(16, 1, root)
    image.descriptor(17, 255)
    return image.bytes()


def blocks(data: bytes) -> ps3_iso.Blocks | None:
    return ps3_iso.magic_blocks(io.BytesIO(data), len(data))


class TestTheTwoBlocks:
    def test_both_in_the_clear_is_clear(self):
        assert blocks(standard(CLEAR_LICENCE, CLEAR_EBOOT)) == ps3_iso.BLOCKS_CLEAR

    def test_one_scrambled_block_is_encrypted(self):
        assert blocks(standard(CLEAR_LICENCE, SCRAMBLED)) == ps3_iso.BLOCKS_ENCRYPTED
        assert blocks(standard(SCRAMBLED, CLEAR_EBOOT)) == ps3_iso.BLOCKS_ENCRYPTED

    def test_a_missing_file_is_skipped(self):
        assert blocks(standard(None, CLEAR_EBOOT)) == ps3_iso.BLOCKS_CLEAR
        assert blocks(standard(SCRAMBLED, None)) == ps3_iso.BLOCKS_ENCRYPTED

    def test_neither_file_is_no_block(self):
        assert blocks(standard(None, None)) == ps3_iso.BLOCKS_NONE

    def test_a_short_block_ends_the_read_with_no_verdict(self):
        """``NOT_ISO`` on the first short read, whatever the other block says (ISO.cpp:270-273)."""
        assert blocks(standard(b"PS3LICDA", SCRAMBLED)) == ps3_iso.BLOCKS_NONE


class TestTheTreeIsReadAsRpcs3ReadsIt:
    def test_names_lose_their_version_and_a_trailing_dot(self):
        image = Image()
        root = game(image, CLEAR_LICENCE, SCRAMBLED, name=lambda n: n if n.endswith(b";1") else n + b".")
        image.descriptor(16, 1, root)
        image.descriptor(17, 255)
        assert blocks(image.bytes()) == ps3_iso.BLOCKS_ENCRYPTED

    def test_names_compare_exactly(self):
        image = Image()
        root = game(image, CLEAR_LICENCE, SCRAMBLED, name=lambda n: n.lower())
        image.descriptor(16, 1, root)
        image.descriptor(17, 255)
        assert blocks(image.bytes()) == ps3_iso.BLOCKS_NONE

    def test_the_last_descriptor_naming_a_root_is_the_tree_read_in_ucs2(self):
        image = Image()
        primary = game(image, None, None)
        joliet = game(image, CLEAR_LICENCE, SCRAMBLED, name=lambda n: n.decode().encode("utf-16-be"))
        image.descriptor(16, 1, primary)
        image.descriptor(17, 2, joliet)
        image.descriptor(18, 255)
        assert blocks(image.bytes()) == ps3_iso.BLOCKS_ENCRYPTED

    def test_padding_skips_to_the_next_sector(self):
        image = Image()
        eboot = image.place(SCRAMBLED)
        # One sector of padding after the dot entries, the EBOOT record in the sector after it.
        start = image.next
        own = record(b"\x00", start, 2 * SECTOR, directory=True)
        data = (own + record(b"\x01", start, SECTOR, directory=True)).ljust(SECTOR, b"\x00")
        data += record(b"EBOOT.BIN;1", *eboot, directory=False).ljust(SECTOR, b"\x00")
        usrdir = image.place(data)
        ps3_game = image.directory([record(b"USRDIR", *usrdir, directory=True)])
        root = image.directory([record(b"PS3_GAME", *ps3_game, directory=True)])
        image.descriptor(16, 1, root)
        image.descriptor(17, 255)
        assert blocks(image.bytes()) == ps3_iso.BLOCKS_ENCRYPTED


class TestTheWalkIsBounded:
    """The image comes with downloaded content: what it names is followed only so far."""

    def test_no_terminator_within_the_limit_is_no_verdict(self):
        image = Image()
        image.next = 16 + ps3_iso.DESCRIPTOR_LIMIT + 4
        root = game(image, CLEAR_LICENCE, CLEAR_EBOOT)
        for sector in range(16, 16 + ps3_iso.DESCRIPTOR_LIMIT):
            image.descriptor(sector, 1, root)
        image.descriptor(16 + ps3_iso.DESCRIPTOR_LIMIT, 255)
        assert blocks(image.bytes()) is None

    def test_a_directory_beyond_the_limit_is_no_verdict(self):
        image = Image()
        root = game(image, CLEAR_LICENCE, CLEAR_EBOOT)
        image.descriptor(16, 1, (root[0], ps3_iso.DIRECTORY_LIMIT + SECTOR))
        image.descriptor(17, 255)
        data = image.bytes() + bytes(ps3_iso.DIRECTORY_LIMIT + SECTOR)
        assert blocks(data) is None

    def test_a_directory_past_the_image_is_no_verdict(self):
        image = Image()
        image.descriptor(16, 1, (10_000, SECTOR))
        image.descriptor(17, 255)
        assert blocks(image.bytes()) is None

    def test_a_record_rpcs3_refuses_is_no_verdict(self):
        image = Image()
        broken = image.place(record(b"PS3_GAME", 30, SECTOR, directory=True, length=20).ljust(SECTOR, b"\x00"))
        image.descriptor(16, 1, broken)
        image.descriptor(17, 255)
        assert blocks(image.bytes()) is None

    def test_a_root_that_is_no_directory_is_no_verdict(self):
        image = Image()
        image.descriptor(16, 1, None)
        data = bytearray(image.sectors[16])
        root = record(b"\x00", 20, SECTOR, directory=False)
        data[156 : 156 + len(root)] = root
        image.sectors[16] = bytes(data)
        image.descriptor(17, 255)
        image.place(bytes(SECTOR))
        assert blocks(image.bytes()) is None


class TestTheFactsCarryTheBlocks:
    def test_an_image_declaring_encrypted_regions_states_its_blocks(self, tmp_path):
        path = tmp_path / "Game.iso"
        path.write_bytes(standard(CLEAR_LICENCE, SCRAMBLED))
        with path.open("rb") as handle:
            facts = ps3_file.read_facts(handle)
        assert facts.iso == ps3_file.Ps3IsoHeader(2, None, ps3_iso.BLOCKS_ENCRYPTED)

    @pytest.mark.parametrize("regions", [1, 128])
    def test_an_image_whose_regions_rule_it_out_reads_no_block(self, tmp_path, regions):
        path = tmp_path / "Game.iso"
        image = Image()
        root = game(image, CLEAR_LICENCE, SCRAMBLED)
        image.descriptor(16, 1, root)
        image.descriptor(17, 255)
        path.write_bytes(image.bytes(regions))
        with path.open("rb") as handle:
            facts = ps3_file.read_facts(handle)
        assert facts.iso == ps3_file.Ps3IsoHeader(regions, None, None)
