"""Tests for atlas.ps3_file — RPCS3's reads of a file's bytes against hand-built files.

Every PARAM.SFO here is packed from the structures ``psf::load`` reads
(``rpcs3/Loader/PSF.cpp:85-101`` at d5213de6): a 20-byte little-endian header,
16-byte entries, a key table and a data table. Every image is a buffer with
``CD001`` placed behind the first volume descriptor's type byte at ``0x8000``,
the region count at offset 0 and a 3k3y mark at ``0xF70`` where one is wanted
(``rpcs3/Loader/ISO.cpp:63-118``, :514-551, :606-650). The seam classes prove
the two machines map every outcome to the same words.
"""

from __future__ import annotations

import struct

import pytest

from atlas import ps3_file
from atlas.machine import (
    PS3_FILE_MISSING,
    PS3_FILE_OK,
    PS3_FILE_UNREADABLE,
    FixtureMachine,
    Ps3FileResult,
    RealMachine,
)

STRING = 0x0204
ARRAY = 0x0004
INTEGER = 0x0404
UNKNOWN_FORMAT = 0x0104


def psf(entries: list[tuple[bytes, int, bytes]], *, version: int = 0x101, magic: bytes = b"\x00PSF") -> bytes:
    """A PARAM.SFO holding *entries* — each a key, a format and its value bytes — laid out the way the loader reads."""
    keys = b""
    data = b""
    table = b""
    for key, fmt, value in entries:
        length = 4 if fmt == INTEGER else len(value)
        table += struct.pack("<HHIII", len(keys), fmt, length, max(length, 4), len(data))
        keys += key + b"\x00"
        data += value.ljust(max(length, 4), b"\x00")
    off_keys = 20 + len(table)
    off_data = off_keys + len(keys)
    header = struct.pack("<4sIIII", magic, version, off_keys, off_data, len(entries))
    # Padding after the data table: the loader requires each entry's maximum to sit strictly inside the file.
    return header + table + keys + data + b"\x00" * 16


def read(tmp_path, data: bytes) -> ps3_file.Ps3FileFacts:
    path = tmp_path / "file"
    path.write_bytes(data)
    with path.open("rb") as handle:
        return ps3_file.read_facts(handle)


def image(regions: int, watermark: bytes = b"", size: int = 0x8006) -> bytes:
    data = bytearray(size)
    data[0:4] = regions.to_bytes(4, "big")
    data[0xF70 : 0xF70 + len(watermark)] = watermark
    data[0x8001:0x8006] = b"CD001"
    return bytes(data)


class TestTheParamSfoCategory:
    def test_a_string_category_the_loader_knows_is_kept(self, tmp_path):
        assert read(tmp_path, psf([(b"CATEGORY", STRING, b"HG\x00\x00")])).category == "HG"

    def test_an_array_category_is_kept_uncut(self, tmp_path):
        assert read(tmp_path, psf([(b"CATEGORY", ARRAY, b"DG")])).category == "DG"

    def test_an_unknown_category_clears_the_registry(self, tmp_path):
        assert read(tmp_path, psf([(b"CATEGORY", STRING, b"XX\x00\x00")])).category == ""

    def test_an_integer_category_is_no_string(self, tmp_path):
        assert read(tmp_path, psf([(b"CATEGORY", INTEGER, b"\x01\x00\x00\x00")])).category == ""

    def test_another_version_is_no_psf(self, tmp_path):
        assert read(tmp_path, psf([(b"CATEGORY", STRING, b"HG\x00\x00")], version=0x100)).category == ""

    def test_a_key_seen_twice_clears_the_registry(self, tmp_path):
        data = psf([(b"CATEGORY", STRING, b"HG\x00\x00"), (b"CATEGORY", STRING, b"DG\x00\x00")])
        assert read(tmp_path, data).category == ""

    def test_an_entry_of_an_unknown_format_is_skipped_rather_than_kept(self, tmp_path):
        data = psf([(b"CATEGORY", UNKNOWN_FORMAT, b"XX\x00\x00"), (b"CATEGORY", STRING, b"HG\x00\x00")])
        assert read(tmp_path, data).category == "HG"

    def test_a_table_longer_than_the_file_is_a_short_read(self, tmp_path):
        header = struct.pack("<4sIIII", b"\x00PSF", 0x101, 20, 20, 0xFFFFFFFF)
        assert read(tmp_path, header + b"\x00" * 16).category == ""

    def test_a_key_table_before_the_header_ends_is_corrupt(self, tmp_path):
        """The key offset moved four bytes into the header, the entry's key offset moved to match: every key still
        reads, and the loader refuses the file on the offset alone."""
        data = bytearray(psf([(b"CATEGORY", STRING, b"HG\x00\x00")]))
        data[8:12] = (16).to_bytes(4, "little")
        data[20:22] = (20).to_bytes(2, "little")
        assert read(tmp_path, bytes(data)).category == ""

    def test_a_file_cut_inside_its_data_table_clears_the_registry(self, tmp_path):
        data = psf([(b"CATEGORY", STRING, b"HG\x00\x00")])
        assert read(tmp_path, data[:-17]).category == ""

    def test_the_magic_alone_reads_as_a_psf(self, tmp_path):
        facts = read(tmp_path, psf([(b"CATEGORY", STRING, b"HG\x00\x00")]))
        assert facts.magic == ps3_file.MAGIC_PSF
        assert facts.iso is None


class TestTheParseIsBounded:
    """A PARAM.SFO comes with downloaded content: a crafted one must not make the parse costly."""

    @staticmethod
    def crafted(size: int) -> bytes:
        """A header naming as many entries as *size* holds, each pointing into one key table without a NUL."""
        count = (size - 20) // 32
        off_keys = 20 + count * 16
        entries = b"".join(struct.pack("<HHIII", index, UNKNOWN_FORMAT, 0, 0, 0) for index in range(count))
        # A data table of its own after the keys, so every entry passes the loader's size checks.
        body = struct.pack("<4sIIII", b"\x00PSF", 0x101, off_keys, size - 64, count) + entries
        return body + b"K" * (size - 64 - len(body)) + b"\x00" * 64

    def test_a_param_sfo_beyond_the_limit_is_not_parsed(self, tmp_path):
        path = tmp_path / "PARAM.SFO"
        path.write_bytes(self.crafted(ps3_file.PSF_SIZE_LIMIT + 1))
        with path.open("rb") as handle, pytest.raises(ps3_file.Ps3FileUnread):
            ps3_file.read_facts(handle)
        assert RealMachine().read_ps3_file(str(path)) == Ps3FileResult(PS3_FILE_UNREADABLE)

    def test_a_crafted_param_sfo_within_the_limit_reads_to_its_verdict(self, tmp_path):
        """Every key is a different suffix of one key table, every entry of a format the loader skips: nothing is
        kept, so no category."""
        assert read(tmp_path, self.crafted(ps3_file.PSF_SIZE_LIMIT)).category == ""


class TestTheImageHeader:
    def test_cd001_behind_the_first_descriptor_is_an_image(self, tmp_path):
        assert read(tmp_path, image(1)).iso == ps3_file.Ps3IsoHeader(1, None)

    def test_the_region_count_is_big_endian(self, tmp_path):
        iso = read(tmp_path, image(0x00000102)).iso
        assert iso is not None
        assert iso.regions == 0x102

    def test_one_byte_short_of_the_identifier_is_no_image(self, tmp_path):
        assert read(tmp_path, image(1)[:-1]).iso is None

    def test_no_identifier_is_no_image(self, tmp_path):
        data = bytearray(image(1))
        data[0x8001] = ord("X")
        assert read(tmp_path, bytes(data)).iso is None

    @pytest.mark.parametrize(
        ("mark", "watermark"),
        [(b"Encrypted 3K BLD", ps3_file.WATERMARK_ENCRYPTED), (b"Dncrypted 3K BLD", ps3_file.WATERMARK_DECRYPTED)],
    )
    def test_the_3k3y_mark_is_read(self, tmp_path, mark, watermark):
        iso = read(tmp_path, image(2, mark)).iso
        assert iso == ps3_file.Ps3IsoHeader(2, watermark)


class TestTheLeadingBytes:
    def test_a_short_file_yields_what_it_holds(self, tmp_path):
        assert read(tmp_path, b"SC").magic == "SC"

    @pytest.mark.parametrize("magic", [ps3_file.MAGIC_SELF, ps3_file.MAGIC_ELF])
    def test_a_self_and_an_elf_are_executables(self, tmp_path, magic):
        assert read(tmp_path, magic.encode("latin-1") + b"\x00" * 60).executable

    def test_an_sfb_is_no_executable(self, tmp_path):
        assert not read(tmp_path, b".SFB\x00\x01\x00\x00").executable


class TestTheFactsAreConsistent:
    def test_a_category_beside_another_magic_is_refused(self):
        with pytest.raises(ValueError, match="PARAM.SFO the loader keeps"):
            ps3_file.Ps3FileFacts(".SFB", None, "HG")

    def test_a_category_the_loader_does_not_keep_is_refused(self):
        with pytest.raises(ValueError, match="PARAM.SFO the loader keeps"):
            ps3_file.Ps3FileFacts(ps3_file.MAGIC_PSF, None, "XX")

    def test_more_than_four_leading_bytes_are_refused(self):
        with pytest.raises(ValueError, match="at most 4 bytes"):
            ps3_file.Ps3FileFacts("SCE\x00\x00")

    def test_a_watermark_outside_the_two_is_refused(self):
        with pytest.raises(ValueError, match="watermark must be one of"):
            ps3_file.Ps3IsoHeader(2, "maybe")  # type: ignore[arg-type]


class TestTheSeamMapsEveryOutcome:
    """RealMachine.read_ps3_file — every way the read can go, in the seam's words."""

    def test_a_file_is_ok_with_its_facts(self, tmp_path):
        path = tmp_path / "PS3_DISC.SFB"
        path.write_bytes(b".SFB\x00\x01")
        assert RealMachine().read_ps3_file(str(path)) == Ps3FileResult(PS3_FILE_OK, ps3_file.Ps3FileFacts(".SFB"))

    def test_an_absent_file_is_missing(self, tmp_path):
        assert RealMachine().read_ps3_file(str(tmp_path / "nope")) == Ps3FileResult(PS3_FILE_MISSING)

    def test_a_directory_is_missing(self, tmp_path):
        assert RealMachine().read_ps3_file(str(tmp_path)) == Ps3FileResult(PS3_FILE_MISSING)

    def test_a_locked_file_is_unreadable(self, tmp_path):
        locked = tmp_path / "PARAM.SFO"
        locked.write_bytes(b"\x00PSF")
        locked.chmod(0)
        try:
            result = RealMachine().read_ps3_file(str(locked))
        finally:
            locked.chmod(0o600)
        assert result == Ps3FileResult(PS3_FILE_UNREADABLE)

    def test_the_result_holds_facts_exactly_when_ok(self):
        with pytest.raises(ValueError, match="exactly when status is 'ok'"):
            Ps3FileResult(PS3_FILE_OK)
        facts = ps3_file.Ps3FileFacts("")
        with pytest.raises(ValueError, match="exactly when status is 'ok'"):
            Ps3FileResult(PS3_FILE_MISSING, facts)


SFO = "/roms/Game.ps3/PARAM.SFO"
ISO = "/roms/Game.iso"


class TestTheFixtureModelsTheAnswer:
    """FixtureMachine.read_ps3_file — the facts declared in, the same outcomes out."""

    def _machine(self, **kwargs) -> FixtureMachine:
        return FixtureMachine({SFO: {"md5": "77" * 16}, ISO: {"md5": "00" * 16}}, **kwargs)

    def test_declared_facts_answer_through_the_readers_types(self):
        machine = self._machine(
            ps3_files={
                SFO: {"magic": "\u0000PSF", "category": "HG"},
                ISO: {"magic": "\u0000\u0000\u0000\u0002", "iso": {"regions": 2, "watermark": "decrypted"}},
            }
        )
        assert machine.read_ps3_file(SFO) == Ps3FileResult(
            PS3_FILE_OK, ps3_file.Ps3FileFacts(ps3_file.MAGIC_PSF, None, "HG")
        )
        result = machine.read_ps3_file(ISO)
        assert result.facts is not None
        assert result.facts.iso == ps3_file.Ps3IsoHeader(2, ps3_file.WATERMARK_DECRYPTED)

    def test_a_declared_file_with_no_facts_answers_unreadable(self):
        """The fixture has no bytes to answer with, and says so rather than inventing a magic."""
        assert self._machine().read_ps3_file(SFO) == Ps3FileResult(PS3_FILE_UNREADABLE)

    def test_an_absent_file_and_a_directory_answer_missing(self):
        machine = self._machine()
        assert machine.read_ps3_file("/roms/nope") == Ps3FileResult(PS3_FILE_MISSING)
        assert machine.read_ps3_file("/roms/Game.ps3") == Ps3FileResult(PS3_FILE_MISSING)

    def test_an_inaccessible_path_answers_unreadable(self):
        assert self._machine(inaccessible=["/roms"]).read_ps3_file(SFO) == Ps3FileResult(PS3_FILE_UNREADABLE)

    @pytest.mark.parametrize(
        ("declared", "message"),
        [
            ({"/roms/other": {"magic": ".SFB"}}, "no file is declared there"),
            ({SFO: {"category": "HG"}}, "expected an object with magic"),
            ({SFO: {"magic": ".SFB", "category": "HG"}}, "PARAM.SFO the loader keeps"),
            ({SFO: {"magic": "Ā"}}, "one byte per character"),
            ({ISO: {"magic": "", "iso": {"regions": -1, "watermark": None}}}, "the u32 the header holds"),
            ({ISO: {"magic": "", "iso": {"regions": 1}}}, "regions and watermark, and optionally blocks"),
            ({ISO: {"magic": "", "iso": {"regions": 2, "watermark": None, "blocks": "maybe"}}}, "iso blocks must be"),
        ],
    )
    def test_a_declaration_no_read_could_answer_is_refused(self, declared, message):
        with pytest.raises(ValueError, match=message):
            self._machine(ps3_files=declared)

    def test_facts_on_an_unreadable_file_are_refused(self):
        with pytest.raises(ValueError, match="an unreadable file states no facts"):
            FixtureMachine({SFO: {"status": "unreadable"}}, ps3_files={SFO: {"magic": "\u0000PSF"}})
