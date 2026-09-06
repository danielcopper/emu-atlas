"""Tests for atlas.lha — the header walk on built archives, ``-lh5-`` on a generated one.

Every level-0, level-1 and level-2 archive here is assembled byte by byte
from ``header.doc``'s own field table, so what the walk is proven against is
the layout and not a copy of some archive's quirks. The compressed half
cannot be built that way — a static-Huffman stream is not a struct — so it is
proven against ``tests/data/whdload-sample.lha``, an archive
``tests/data/make_lha_fixtures.py`` generates with an encoder of its own and
which Lhasa, an independent LhA implementation, lists and CRC-tests clean.
"""

from __future__ import annotations

import struct
from pathlib import Path

import pytest

from atlas import lha

DATA = Path(__file__).parent / "data"
SAMPLE = DATA / "whdload-sample.lha"

# The two names the fixture generator gives the slave it builds.
SLAVE_MEMBER = "TestGame/TestGame.slave"
ICON_MEMBER = "TestGame.info"
README_MEMBER = "TestGame/ReadMe"

END = b"\x00"
# The two-byte end marker: a zero size and a zero checksum.
END_MARKER = b"\x00\x00"
# Two header fields this file reaches into by offset to break them, from
# header.doc's own table: the level byte, and level 0's packed size.
LEVEL_BYTE = 20
PACKED_SIZE_FIELD = 7
ORIGINAL_SIZE_FIELD = 11
NAME_LENGTH_FIELD = 21


def bits_to_bytes(bits: str) -> bytes:
    """A written-out bit string as the bytes a most-significant-first reader sees."""
    padded = bits + "0" * (-len(bits) % 8)
    return bytes(int(padded[at : at + 8], 2) for at in range(0, len(padded), 8))


def level0(name: bytes, content: bytes, *, method: bytes = lha.STORED, crc: int | None = None) -> bytes:
    """A level-0 header and its data — the whole path lives in the base header."""
    checksum = lha.crc16(content) if crc is None else crc
    body = (
        method
        + struct.pack("<II", len(content), len(content))
        + struct.pack("<HH", 0, 0)
        + b"\x20\x00"
        + bytes([len(name)])
        + name
        + struct.pack("<H", checksum)
    )
    return bytes([len(body), sum(body) & 0xFF]) + body + content


def extensions(payloads: list[bytes]) -> tuple[int, bytes]:
    """An extended-header chain: the first header's size, and the blob after the base one."""
    blob = b""
    following = 0
    for payload in reversed(payloads):
        blob = payload + struct.pack("<H", following) + blob
        following = len(payload) + 2
    return following, blob


def level1(name: bytes, content: bytes, *, payloads: list[bytes] | None = None) -> bytes:
    """A level-1 header: a base header, extended headers, then the data."""
    first, blob = extensions(payloads or [])
    body = (
        lha.STORED
        + struct.pack("<II", len(content) + len(blob), len(content))
        + struct.pack("<HH", 0, 0)
        + b"\x20\x01"
        + bytes([len(name)])
        + name
        + struct.pack("<H", lha.crc16(content))
        + b"A"
        + struct.pack("<H", first)
    )
    return bytes([len(body), sum(body) & 0xFF]) + body + blob + content


def level2(content: bytes, *, payloads: list[bytes]) -> bytes:
    """A level-2 header: a 16-bit total size, and a name only the extensions carry."""
    first, blob = extensions(payloads)
    base = (
        struct.pack("<H", 26 + len(blob))
        + lha.STORED
        + struct.pack("<II", len(content), len(content))
        + struct.pack("<I", 0)
        + b"\x00\x02"
        + struct.pack("<H", lha.crc16(content))
        + b"A"
        + struct.pack("<H", first)
    )
    return base + blob + content


def test_crc16_is_the_arc_checksum() -> None:
    # The CRC-16/ARC check value: "123456789" hashes to 0xBB3D.
    assert lha.crc16(b"123456789") == 0xBB3D


def test_level0_carries_the_whole_path_and_both_separators() -> None:
    archive = level0(b"Game\xffData\\file.txt", b"hello") + END

    (member,) = lha.members(archive)

    assert member.name == "Game/Data/file.txt"
    assert member.method == lha.STORED
    assert lha.extract(archive, member) == b"hello"


def test_level1_takes_its_directory_from_an_extension_header() -> None:
    archive = level1(b"AlienBreed.slave", b"body", payloads=[b"\x02Game\xffsource\xff"]) + END

    (member,) = lha.members(archive)

    assert member.name == "Game/source/AlienBreed.slave"
    assert lha.extract(archive, member) == b"body"


def test_a_filename_extension_header_overrides_the_base_header_name() -> None:
    archive = level1(b"short", b"x", payloads=[b"\x01a-much-longer-name.slave"]) + END

    (member,) = lha.members(archive)

    assert member.name == "a-much-longer-name.slave"


def test_level2_names_a_member_from_its_extension_headers_alone() -> None:
    archive = level2(b"content", payloads=[b"\x01ReadMe", b"\x02Game\xff"]) + END

    (member,) = lha.members(archive)

    assert member.name == "Game/ReadMe"
    assert lha.extract(archive, member) == b"content"


def test_the_walk_reaches_every_member_and_stops_at_the_marker() -> None:
    archive = level0(b"one", b"1") + level1(b"two", b"22") + level2(b"3", payloads=[b"\x01three"]) + END

    assert [member.name for member in lha.members(archive)] == ["one", "two", "three"]


def test_an_archive_may_end_at_the_end_of_the_file() -> None:
    assert [member.name for member in lha.members(level0(b"only", b"x"))] == ["only"]


def test_a_two_byte_marker_is_what_ends_the_walk() -> None:
    # A level-2 header whose total size is a multiple of 256 opens with a zero
    # byte; only the checksum byte beside it tells the two apart.
    archive = level0(b"a", b"x") + END_MARKER + level0(b"unreachable", b"y")

    assert [member.name for member in lha.members(archive)] == ["a"]


@pytest.mark.parametrize(
    "archive",
    [
        pytest.param(b"", id="empty"),
        pytest.param(b"\x00", id="nothing-but-the-marker"),
        pytest.param(b"PK\x03\x04" + bytes(40), id="some-other-archive"),
        pytest.param(level0(b"cut", b"content")[:12], id="header-cut-short"),
    ],
)
def test_bytes_that_are_not_an_archive_are_refused(archive: bytes) -> None:
    with pytest.raises(lha.NotAnLha):
        lha.members(archive)


def test_an_unknown_header_level_is_refused() -> None:
    archive = bytearray(level0(b"x", b"y"))
    archive[LEVEL_BYTE] = 9

    with pytest.raises(lha.NotAnLha):
        lha.members(bytes(archive))


def test_an_extension_header_that_overruns_the_file_is_refused() -> None:
    archive = level1(b"x", b"y", payloads=[b"\x02dir\xff"])
    broken = bytearray(archive)
    # The base header's last two bytes are the first extension's size.
    broken[len(archive) - len(b"\x02dir\xff") - 4 - 1] = 0xFF

    with pytest.raises(lha.NotAnLha):
        lha.members(bytes(broken))


def test_a_member_reaching_past_the_archive_is_refused() -> None:
    archive = bytearray(level0(b"x", b"content"))
    struct.pack_into("<I", archive, PACKED_SIZE_FIELD, 1 << 20)

    with pytest.raises(lha.NotAnLha):
        lha.members(bytes(archive))


def test_a_member_whose_bytes_fail_its_own_crc_is_refused() -> None:
    archive = level0(b"x", b"content", crc=0) + END
    (member,) = lha.members(archive)

    with pytest.raises(lha.CorruptMember, match="CRC-16"):
        lha.extract(archive, member)


def test_an_unimplemented_method_names_itself() -> None:
    archive = level0(b"x", b"content", method=b"-lh7-") + END
    (member,) = lha.members(archive)

    with pytest.raises(lha.UnsupportedMethod) as raised:
        lha.extract(archive, member)

    assert raised.value.method == "-lh7-"


def test_a_stream_whose_code_lengths_spend_nothing_is_refused() -> None:
    # One block, a stated pre-code count of 1, and a code length of zero: the
    # lengths leave the whole code space unspent, so no symbol can be read.
    packed = bits_to_bytes("0" * 15 + "1" + "00001" + "000")
    archive = level0(b"x", packed, method=lha.LH5, crc=0) + END
    (member,) = lha.members(archive)

    with pytest.raises(lha.CorruptMember, match="complete code"):
        lha.extract(archive, member)


def test_the_sample_archive_lists_the_members_the_generator_wrote() -> None:
    data = SAMPLE.read_bytes()

    assert [member.name for member in lha.members(data)] == [ICON_MEMBER, SLAVE_MEMBER, README_MEMBER]
    assert {member.method for member in lha.members(data)} == {lha.LH5}


def test_every_lh5_member_of_the_sample_decodes_to_its_stated_size_and_crc() -> None:
    data = SAMPLE.read_bytes()

    for member in lha.members(data):
        content = lha.extract(data, member)
        assert len(content) == member.original_size
        assert lha.crc16(content) == member.crc


def test_an_lh5_member_decodes_to_the_text_the_generator_compressed() -> None:
    data = SAMPLE.read_bytes()
    member = next(m for m in lha.members(data) if m.name == README_MEMBER)

    content = lha.extract(data, member).decode("latin-1")

    assert content.startswith("Test Game\n")
    # The repeated line is what the encoder's matches are made of.
    assert content.count("installed for the atlas fixture, nothing real.\n") == 8


def test_a_block_whose_single_symbol_is_outside_its_alphabet_is_refused() -> None:
    # The stated-count-of-zero form carries the symbol in a field wider than
    # any alphabet here, so a corrupt stream can name one that is not a symbol.
    packed = bits_to_bytes("0" * 15 + "1" + "00000" + "11111")
    archive = level0(b"x", packed, method=lha.LH5, crc=0) + END
    (member,) = lha.members(archive)

    with pytest.raises(lha.CorruptMember, match="single symbol"):
        lha.extract(archive, member)


def test_a_member_whose_stated_size_outruns_its_bytes_is_refused() -> None:
    # The size in the header is not a promise the bytes keep: past the stream
    # the reader would invent one literal per turn, forever.
    data = bytearray(SAMPLE.read_bytes())
    struct.pack_into("<I", data, 2 + ORIGINAL_SIZE_FIELD, 4_000_000_000)
    archive = bytes(data)
    member = lha.members(archive)[0]

    with pytest.raises(lha.CorruptMember, match="do not describe the same member"):
        lha.extract(archive, member)


def test_a_member_whose_compressed_bytes_run_out_early_is_refused() -> None:
    data = SAMPLE.read_bytes()
    member = lha.members(data)[0]
    cut = lha.Member(
        member.name, member.method, member.offset, member.packed_size // 4,
        member.original_size, member.crc,
    )

    with pytest.raises(lha.CorruptMember):
        lha.extract(data, cut)


def test_a_nameless_member_is_no_member_at_all() -> None:
    # A zip under an .lha suffix walks this far: a header whose name length is
    # zero would otherwise be listed as a member nothing can name.
    archive = bytearray(level0(b"x", b"y") + END)
    archive[NAME_LENGTH_FIELD] = 0

    with pytest.raises(lha.NotAnLha, match="states no name"):
        lha.members(bytes(archive))
