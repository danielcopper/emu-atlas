"""Regenerate the LhA fixture the archive reader and the slave reader are tested against.

``whdload-sample.lha`` is a WHDLoad install archive in miniature, in exactly
the shape a real one has: a drawer icon ``TestGame.info`` at the root and a
drawer ``TestGame/`` holding ``TestGame.slave`` beside one other file
(``ReadMe``). That shape is what the core's boot script resolves — the slave named
after its own directory — so one fixture carries the container, the ``-lh5-``
stream, the AmigaDOS hunk file and the ``WHDLoadSlave`` structure at once.

Nothing here is redistributed: the slave is a hunk file this script builds,
and its ``ws_name`` is invented. A real install archive cannot be committed —
whdload.de states no distribution terms — so the reader's check against real
Amiga bytes stays a local one.

There is no LhA *compressor* on this machine to make the fixture with (LHa
for UNIX is not in Homebrew, and Lhasa, which is, only decompresses), so this
script carries a small ``-lh5-`` encoder: greedy LZSS over the format's 8 KiB
window, then one static-Huffman block, which is what the format is. The
output is checked against an independent implementation rather than against
the reader it feeds:

    lha t tests/data/whdload-sample.lha     # Lhasa 0.6.0, CRC of every member
    lha l tests/data/whdload-sample.lha     # the member list

Run from the repository root:

    python tests/data/make_lha_fixtures.py
"""

from __future__ import annotations

import heapq
import struct
from pathlib import Path

HERE = Path(__file__).resolve().parent
FIXTURE = HERE / "whdload-sample.lha"

# The stream's own numbers, spelled here as the encoder's mirror of
# ``atlas.lha``: 256 literals, the shortest match, the three alphabets and
# the widths their counts go out in.
LITERALS = 256
THRESHOLD = 3
MAX_MATCH = 256
WINDOW = 1 << 13
CODES = 510
CODE_COUNT_BITS = 9
PRE_CODES = 19
PRE_COUNT_BITS = 5
PRE_SKIP_AFTER = 3
PRE_ESCAPE = 7
POSITIONS = 14
POSITION_COUNT_BITS = 4
MAX_CODE_LENGTH = 16
RUN_SHORT_BITS = 4
RUN_SHORT_BASE = 3
RUN_SHORT_MAX = RUN_SHORT_BASE + (1 << RUN_SHORT_BITS) - 1
RUN_LONG_BASE = 20
RUN_LONG_MAX = RUN_LONG_BASE + (1 << CODE_COUNT_BITS) - 1

CRC16_POLYNOMIAL = 0xA001


def crc16(data: bytes) -> int:
    value = 0
    for byte in data:
        value ^= byte
        for _ in range(8):
            value = (value >> 1) ^ CRC16_POLYNOMIAL if value & 1 else value >> 1
    return value


class BitWriter:
    """Bits most-significant first — the order the format reads them back in."""

    def __init__(self) -> None:
        self.bits: list[int] = []

    def put(self, value: int, count: int) -> None:
        for shift in range(count - 1, -1, -1):
            self.bits.append((value >> shift) & 1)

    def bytes(self) -> bytes:
        padded = self.bits + [0] * (-len(self.bits) % 8)
        return bytes(
            int("".join(str(bit) for bit in padded[at : at + 8]), 2) for at in range(0, len(padded), 8)
        )


def huffman_lengths(frequencies: dict[int, int], alphabet: int) -> list[int]:
    """Canonical code lengths for the symbols used, zero for the rest.

    A single used symbol cannot make a complete code on its own, so a second
    one is given a length too — the format has no shorter way to say it,
    short of the stated-count-of-zero form the fixtures do not need.
    """
    frequencies = dict(frequencies)
    for filler in range(alphabet):
        if len(frequencies) >= 2:
            break
        frequencies.setdefault(filler, 1)
    used = sorted(frequencies)
    heap = [(frequencies[symbol], index, [symbol]) for index, symbol in enumerate(used)]
    heapq.heapify(heap)
    lengths = dict.fromkeys(used, 0)
    counter = len(used)
    while len(heap) > 1:
        left_weight, _, left = heapq.heappop(heap)
        right_weight, _, right = heapq.heappop(heap)
        for symbol in left + right:
            lengths[symbol] += 1
        heapq.heappush(heap, (left_weight + right_weight, counter, left + right))
        counter += 1
    if max(lengths.values()) > MAX_CODE_LENGTH:
        raise ValueError("the fixture's content needs a code longer than the format allows")
    return [lengths.get(symbol, 0) for symbol in range(alphabet)]


def canonical(lengths: list[int]) -> dict[int, tuple[int, int]]:
    """Symbol -> (code, length), assigned the way ``atlas.lha`` reads them back."""
    counts = [0] * (MAX_CODE_LENGTH + 1)
    for length in lengths:
        counts[length] += 1
    cursor = [0] * (MAX_CODE_LENGTH + 2)
    for length in range(1, MAX_CODE_LENGTH + 1):
        cursor[length + 1] = cursor[length] + (counts[length] << (MAX_CODE_LENGTH - length))
    if cursor[MAX_CODE_LENGTH + 1] != 1 << MAX_CODE_LENGTH:
        raise ValueError("the code lengths do not spend the format's code space exactly")
    codes: dict[int, tuple[int, int]] = {}
    for symbol, length in enumerate(lengths):
        if length:
            codes[symbol] = (cursor[length] >> (MAX_CODE_LENGTH - length), length)
            cursor[length] += 1 << (MAX_CODE_LENGTH - length)
    return codes


def compress_symbols(data: bytes) -> list[tuple[int, int]]:
    """Greedy LZSS: a list of (code symbol, position value) pairs.

    ``position value`` is the distance minus one for a match and -1 for a
    literal, which is what the position code carries.
    """
    out: list[tuple[int, int]] = []
    at = 0
    while at < len(data):
        length, distance = longest_match(data, at)
        if length >= THRESHOLD:
            out.append((LITERALS + length - THRESHOLD, distance - 1))
            at += length
        else:
            out.append((data[at], -1))
            at += 1
    return out


def longest_match(data: bytes, at: int) -> tuple[int, int]:
    """The longest earlier repeat of the bytes at *at*, within the format's window."""
    best_length = 0
    best_distance = 0
    limit = min(len(data) - at, MAX_MATCH)
    for start in range(max(0, at - WINDOW), at):
        length = 0
        while length < limit and data[start + length] == data[at + length]:
            length += 1
        if length > best_length:
            best_length, best_distance = length, at - start
    return best_length, best_distance


def pre_symbols(lengths: list[int], stated: int) -> list[tuple[int, int, int]]:
    """The code-length table as (pre symbol, extra value, extra bits) triples."""
    out: list[tuple[int, int, int]] = []
    index = 0
    while index < stated:
        if lengths[index]:
            out.append((lengths[index] + 2, 0, 0))
            index += 1
            continue
        run = 0
        while index + run < stated and not lengths[index + run]:
            run += 1
        index += run
        out.extend(zero_runs(run))
    return out


def zero_runs(run: int) -> list[tuple[int, int, int]]:
    """One run of unused entries, spelled with the three codes the format has."""
    out: list[tuple[int, int, int]] = []
    while run:
        if run <= 2:
            out.append((0, 0, 0))
            run -= 1
        elif run <= RUN_SHORT_MAX:
            out.append((1, run - RUN_SHORT_BASE, RUN_SHORT_BITS))
            run = 0
        elif run < RUN_LONG_BASE:
            out.append((0, 0, 0))
            run -= 1
        else:
            taken = min(run, RUN_LONG_MAX)
            out.append((2, taken - RUN_LONG_BASE, CODE_COUNT_BITS))
            run -= taken
    return out


def write_pre_lengths(writer: BitWriter, lengths: list[int], count_bits: int, skip_after: int) -> None:
    """A table whose lengths go out literally, with the skip the format demands."""
    stated = last_used(lengths)
    writer.put(stated, count_bits)
    index = 0
    while index < stated:
        write_pre_length(writer, lengths[index])
        index += 1
        if index == skip_after:
            skip = 0
            while skip < 3 and index + skip < len(lengths) and not lengths[index + skip]:
                skip += 1
            writer.put(skip, 2)
            index += skip


def write_pre_length(writer: BitWriter, length: int) -> None:
    if length < PRE_ESCAPE:
        writer.put(length, PRE_SKIP_AFTER)
        return
    writer.put((1 << (length - PRE_SKIP_AFTER)) - 2, length - PRE_SKIP_AFTER)


def last_used(lengths: list[int]) -> int:
    for index in range(len(lengths) - 1, -1, -1):
        if lengths[index]:
            return index + 1
    return 0


def position_value(value: int) -> tuple[int, int, int]:
    """A match distance as (position symbol, extra value, extra bits)."""
    if value == 0:
        return 0, 0, 0
    symbol = value.bit_length()
    return symbol, value - (1 << (symbol - 1)), symbol - 1


def compress_lh5(data: bytes) -> bytes:
    """One static-Huffman block over the whole member."""
    stream = compress_symbols(data)
    code_frequencies: dict[int, int] = {}
    position_frequencies: dict[int, int] = {}
    for symbol, value in stream:
        code_frequencies[symbol] = code_frequencies.get(symbol, 0) + 1
        if value >= 0:
            position = position_value(value)[0]
            position_frequencies[position] = position_frequencies.get(position, 0) + 1
    code_lengths = huffman_lengths(code_frequencies, CODES)
    position_lengths = huffman_lengths(position_frequencies, POSITIONS)
    pre = pre_symbols(code_lengths, last_used(code_lengths))
    pre_frequencies: dict[int, int] = {}
    for symbol, _, _ in pre:
        pre_frequencies[symbol] = pre_frequencies.get(symbol, 0) + 1
    pre_lengths = huffman_lengths(pre_frequencies, PRE_CODES)

    writer = BitWriter()
    writer.put(len(stream), MAX_CODE_LENGTH)
    write_pre_lengths(writer, pre_lengths, PRE_COUNT_BITS, PRE_SKIP_AFTER)
    write_code_lengths(writer, pre, canonical(pre_lengths), last_used(code_lengths))
    write_pre_lengths(writer, position_lengths, POSITION_COUNT_BITS, -1)
    write_stream(writer, stream, canonical(code_lengths), canonical(position_lengths))
    return writer.bytes()


def write_code_lengths(
    writer: BitWriter, pre: list[tuple[int, int, int]], codes: dict[int, tuple[int, int]], stated: int
) -> None:
    writer.put(stated, CODE_COUNT_BITS)
    for symbol, extra, extra_bits in pre:
        code, length = codes[symbol]
        writer.put(code, length)
        writer.put(extra, extra_bits)


def write_stream(
    writer: BitWriter,
    stream: list[tuple[int, int]],
    codes: dict[int, tuple[int, int]],
    positions: dict[int, tuple[int, int]],
) -> None:
    for symbol, value in stream:
        code, length = codes[symbol]
        writer.put(code, length)
        if value < 0:
            continue
        position, extra, extra_bits = position_value(value)
        code, length = positions[position]
        writer.put(code, length)
        writer.put(extra, extra_bits)


def level1_header(name: str, directory: str, packed: bytes, original: bytes) -> bytes:
    """One level-1 header: the base header, a dirname extension, and the chain's end."""
    encoded = name.encode("latin-1")
    extension = b""
    if directory:
        payload = b"\x02" + directory.encode("latin-1").replace(b"/", b"\xff") + b"\xff"
        extension = struct.pack("<H", len(payload) + 2) + payload
    extension += struct.pack("<H", 0)
    # The first two bytes of the extension block are the base header's own
    # trailing next-header size, so the chain starts there.
    body = (
        b"-lh5-"
        + struct.pack("<I", len(packed) + len(extension) - 2)
        + struct.pack("<I", len(original))
        + struct.pack("<HH", 0x5000, 0x5000)
        + b"\x20\x01"
        + bytes([len(encoded)])
        + encoded
        + struct.pack("<H", crc16(original))
        + b"A"
    )
    body += extension[:2]
    return bytes([len(body), sum(body) & 0xFF]) + body + extension[2:]


def member(path: str, content: bytes) -> bytes:
    directory, _, name = path.rpartition("/")
    packed = compress_lh5(content)
    return level1_header(name, directory, packed, content) + packed


SLAVE_NAME = "Test Game"
SLAVE_COPYRIGHT = "1993 Nobody"


def slave_hunk() -> bytes:
    """A one-hunk AmigaDOS executable carrying a WHDLoadSlave structure.

    The structure is the autodoc's, big-endian: the ``moveq #-1,d0 / rts``
    guard, the identifier, a version past the one that introduced ``ws_name``,
    and structure-relative pointers to the two strings. The filler after it
    stands in for a real slave's code and gives the compressor something to
    find repeats in.
    """
    strings = b"\x00" + SLAVE_NAME.encode("latin-1") + b"\x00" + SLAVE_COPYRIGHT.encode("latin-1") + b"\x00"
    filler = b"".join(b"\x4e\x71\x60\x00" + bytes([index % 7]) * 4 for index in range(64))
    body = (
        struct.pack(">HH", 0x70FF, 0x4E75)
        + b"WHDLOADS"
        + struct.pack(">HH", 17, 0)
        + struct.pack(">II", 0x00080000, 0)
        + struct.pack(">HHH", 0, 0, 0)
        + b"\x00\x00"
        + struct.pack(">I", 0)
    )
    name_pointer = len(body) + 3 * 2 + len(filler) + 1
    body += struct.pack(">HHH", name_pointer, name_pointer + len(SLAVE_NAME) + 1, 0)
    body += filler + strings
    body += b"\x00" * (-len(body) % 4)
    return (
        struct.pack(">IIIII", 0x3F3, 0, 1, 0, 0)
        + struct.pack(">I", len(body) // 4)
        + struct.pack(">II", 0x3E9, len(body) // 4)
        + body
    )


ICON = bytes(range(78)) + b"WHDLoad drawer icon, not a real Workbench icon\x00"
README = ("Test Game\n" + "installed for the atlas fixture, nothing real.\n" * 8).encode("latin-1")


def main() -> None:
    archive = (
        member("TestGame.info", ICON)
        + member("TestGame/TestGame.slave", slave_hunk())
        + member("TestGame/ReadMe", README)
        + b"\x00"
    )
    FIXTURE.write_bytes(archive)
    print(f"wrote {FIXTURE} ({len(archive)} bytes)")


if __name__ == "__main__":
    main()
