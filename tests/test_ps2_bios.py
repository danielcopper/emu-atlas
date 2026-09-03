"""Tests for atlas.ps2_bios — the ROMDIR read against hand-built record sequences.

Every image here is built from ``struct.pack("<10sHI", ...)`` records, the
16-byte ``romdir`` the core reads (pcsx2/ps2/BiosTools.cpp:38-47 at 14d19f8),
so what the reader is proven against is the shape ``LoadBiosVersion`` walks
(:60-173) and nothing borrowed from a real dump. The expected description is
computed by hand from the core's format string (:147-155). The seam classes
prove the two machines map every outcome to the same words.
"""

from __future__ import annotations

import io
import struct

import pytest

from atlas import ps2_bios
from atlas.machine import (
    PS2_BIOS_MISSING,
    PS2_BIOS_NOT_A_BIOS,
    PS2_BIOS_OK,
    PS2_BIOS_UNREADABLE,
    FixtureMachine,
    Ps2BiosHeaderResult,
    RealMachine,
)

ROMVER = b"0200EC20040614"
SERIAL = b"0190022"
# "%-7s v%s.%s(%c%c/%c%c/%c%c%c%c)  %s %s" over Europe, 02, 00, 1, 4, 0, 6,
# 2, 0, 0, 4, Console, 0190022 — the zone padded to seven, two spaces before
# the console word.
DESCRIPTION = "Europe  v02.00(14/06/2004)  Console 0190022"
IMAGE_SIZE = 4 * 1024 * 1024
BIOS_PATH = "/bios/scph70004.bin"
JUNK_PATH = "/bios/junk.bin"


def record(name: bytes, size: int, ext_info_size: int = 0) -> bytes:
    """One ``romdir`` record as the core lays it out."""
    return struct.pack("<10sHI", name, ext_info_size, size)


def image(records: list[bytes], *, lead: bytes = b"", total: int = IMAGE_SIZE) -> bytes:
    """A file of *total* bytes: *lead*, then the table, zeros after — the strings are placed by the caller."""
    table = b"".join(records)
    body = bytearray(total)
    body[: len(lead)] = lead
    body[len(lead) : len(lead) + len(table)] = table
    return bytes(body)


def minimal_bios(*, lead: bytes = b"") -> bytes:
    """RESET, ROMDIR, EXTINFO (32 bytes), ROMVER (14 bytes), a terminator — the strings where the sizes put them.

    The walk sums sizes from offset 0 wherever ``RESET`` was found, so the
    ``ROMDIR`` record's size covers *lead* and the table's own 80 bytes, which
    places ``EXTINFO`` right after the table (the serial at +0x10) and
    ``ROMVER`` 32 bytes further on.
    """
    table_size = len(lead) + 5 * ps2_bios.RECORD_SIZE
    records = [
        record(b"RESET", 0),
        record(b"ROMDIR", table_size),
        record(b"EXTINFO", 32),
        record(b"ROMVER", 14),
        record(b"", 0),
    ]
    data = bytearray(image(records, lead=lead))
    data[table_size + 0x10 : table_size + 0x10 + len(SERIAL)] = SERIAL
    data[table_size + 32 : table_size + 32 + 14] = ROMVER
    return bytes(data)


class TestTheReaderWalksTheTable:
    def test_a_minimal_image_yields_the_fields_and_the_description(self):
        header = ps2_bios.read_header(io.BytesIO(minimal_bios()))
        assert header == ps2_bios.Ps2BiosHeader(
            zone="Europe",
            region=2,
            major="02",
            minor="00",
            year="2004",
            month="06",
            day="14",
            build="Console",
            serial="0190022",
            description=DESCRIPTION,
        )
        assert header.version == "02.00"
        assert header.date == "2004-06-14"

    def test_reset_need_not_be_the_first_record(self):
        """The loop reads past whatever precedes RESET (:63-70); an offset-0 peek would reject this file."""
        with_lead = minimal_bios(lead=record(b"JUNK", 16) * 3)
        header = ps2_bios.read_header(io.BytesIO(with_lead))
        assert header.description == DESCRIPTION

    def test_a_table_without_romver_is_not_a_bios(self):
        records = [record(b"RESET", 0), record(b"ROMDIR", 48), record(b"EXTINFO", 32), record(b"", 0)]
        data = io.BytesIO(image(records))
        with pytest.raises(ps2_bios.NotAPs2Bios, match="yielded no ROMVER string"):
            ps2_bios.read_header(data)

    def test_a_file_of_junk_is_not_a_bios(self):
        """No RESET anywhere: the file ends inside the bound, which is a short read (:65-66)."""
        data = io.BytesIO(b"\xff" * IMAGE_SIZE)
        with pytest.raises(ps2_bios.NotAPs2Bios, match="without a RESET record"):
            ps2_bios.read_header(data)

    def test_the_bound_is_the_cores_and_the_walk_then_starts_from_the_last_record(self):
        """524288 records without RESET exhaust the loop; the walk begins on the last one read (:72-79)."""
        # Exactly 8 MiB of empty-named records: the last record's name is
        # empty, the walk does not run, and no ROMVER makes it no BIOS.
        data = io.BytesIO(record(b"", 0) * ps2_bios.RESET_SCAN_BOUND)
        with pytest.raises(ps2_bios.NotAPs2Bios, match=f"no RESET record among the first {ps2_bios.RESET_SCAN_BOUND}"):
            ps2_bios.read_header(data)

    def test_a_romver_as_the_last_record_of_an_exhausted_bound_is_what_the_core_accepts(self):
        """Faithfulness over tidiness: the core walks from the last record read when the bound runs out."""
        data = bytearray(record(b"", 0) * (ps2_bios.RESET_SCAN_BOUND - 1) + record(b"ROMVER", 14))
        data[0:14] = ROMVER  # fileOffset is 0 when the walk starts, so ROMVER is read at 0
        header = ps2_bios.read_header(io.BytesIO(bytes(data)))
        assert header.major == "02"

    def test_an_empty_file_is_not_a_bios(self):
        data = io.BytesIO(b"")
        with pytest.raises(ps2_bios.NotAPs2Bios, match="ends after 0 complete ROMDIR records"):
            ps2_bios.read_header(data)

    def test_a_truncated_table_is_not_a_bios(self):
        data = io.BytesIO(record(b"RESET", 0) + b"ROMDIR")
        with pytest.raises(ps2_bios.NotAPs2Bios, match="yielded no ROMVER string"):
            ps2_bios.read_header(data)

    def test_a_qcow2_head_is_not_a_bios(self):
        """The other emulator's disk image a linked BIOS root holds: the first record is 'QFI\\xfb', never RESET."""
        data = io.BytesIO(b"QFI\xfb" + b"\x00" * (IMAGE_SIZE - 4))
        with pytest.raises(ps2_bios.NotAPs2Bios, match="without a RESET record"):
            ps2_bios.read_header(data)

    def test_a_romver_whose_bytes_fall_short_ends_the_walk_before_it_counts(self):
        """The read at :95 falling short breaks out (:96) with foundRomVer still false."""
        # ROMVER sits at 4096 and the file ends four bytes into it.
        records = [record(b"RESET", 0), record(b"ROMDIR", 4096), record(b"ROMVER", 14), record(b"", 0)]
        data = io.BytesIO(image(records, total=4096 + 4))
        with pytest.raises(ps2_bios.NotAPs2Bios, match="yielded no ROMVER string"):
            ps2_bios.read_header(data)

    def test_the_walk_stops_at_a_name_of_ten_bytes_or_none(self):
        """The loop condition (:79): an empty name ends the table, and so does one with no NUL in ten bytes."""
        records = [record(b"RESET", 0), record(b"ROMDIR", 48), record(b"0123456789", 14), record(b"ROMVER", 14)]
        data = io.BytesIO(image(records))
        with pytest.raises(ps2_bios.NotAPs2Bios):
            ps2_bios.read_header(data)

    def test_the_zone_letters_map_as_the_switch_does(self):
        cases = {
            b"J": ("Japan", 0),
            b"A": ("USA", 1),
            b"E": ("Europe", 2),
            b"H": ("Asia", 4),
            b"C": ("China", 6),
            b"X": ("Test", 9),
            b"P": ("Free", 10),
            b"Q": ("Q", 0),
        }
        for letter, (zone, region) in cases.items():
            header = ps2_bios.Ps2BiosHeader.from_strings(b"0100" + letter + b"D20000101", b"")
            assert (header.zone, header.region) == (zone, region), letter
            assert header.build == "Devel", letter

    def test_the_t_zone_reads_a_second_byte(self):
        assert ps2_bios.Ps2BiosHeader.from_strings(b"0100TZ20000101", b"").zone == "COH-H"
        assert ps2_bios.Ps2BiosHeader.from_strings(b"0100TC20000101", b"").zone == "T10K"

    def test_an_unknown_build_byte_prints_nothing_between_the_two_spaces(self):
        header = ps2_bios.Ps2BiosHeader.from_strings(b"0100J?20000101", b"")
        assert header.build == ""
        assert header.description == "Japan   v01.00(01/01/2000)   "

    def test_the_serial_is_the_c_string_of_fifteen_bytes(self):
        full = b"ABCDEFGHIJKLMNO"
        assert ps2_bios.Ps2BiosHeader.from_strings(ROMVER, full).serial == "ABCDEFGHIJKLMNO"

    def test_a_short_file_gets_the_percentage_suffix(self):
        """The file ends before the table's offsets reach (:165-170): the description says how much is there."""
        records = [record(b"RESET", 0), record(b"ROMDIR", 80), record(b"ROMVER", 14), record(b"BIG", 7904), record(b"", 0)]
        data = bytearray(image(records, total=4000))
        data[80:94] = ROMVER
        header = ps2_bios.read_header(io.BytesIO(bytes(data)))
        # fileOffset: 0 + 80 + 16 (14 rounded up) + 7904 = 8000, and the terminator's own padding of 16
        # is taken back (:110) → 7984; a 4000-byte file is 50% of that. No serial, so the format's last
        # "%s" prints nothing after the console word, and the suffix follows the trailing space.
        assert header.description == f"Europe  v02.00(14/06/2004)  Console  {4000 * 100 // 7984}%"


class TestTheSeamMapsEveryOutcome:
    """RealMachine.read_ps2_bios_header — one status per distinct outcome, never collapsed."""

    def test_a_bios_file_is_ok_with_the_header(self, tmp_path):
        path = tmp_path / "scph70004.bin"
        path.write_bytes(minimal_bios())
        result = RealMachine().read_ps2_bios_header(str(path))
        assert result.status == PS2_BIOS_OK
        assert result.header is not None
        assert result.header.description == DESCRIPTION

    def test_junk_is_not_a_bios(self, tmp_path):
        path = tmp_path / "junk.bin"
        path.write_bytes(b"\xff" * 4096)
        assert RealMachine().read_ps2_bios_header(str(path)) == Ps2BiosHeaderResult(PS2_BIOS_NOT_A_BIOS)

    def test_an_absent_file_is_missing(self, tmp_path):
        result = RealMachine().read_ps2_bios_header(str(tmp_path / "nope.bin"))
        assert result == Ps2BiosHeaderResult(PS2_BIOS_MISSING)

    def test_a_path_through_a_file_is_missing(self, tmp_path):
        (tmp_path / "f.bin").write_bytes(b"x")
        result = RealMachine().read_ps2_bios_header(str(tmp_path / "f.bin" / "inner.bin"))
        assert result == Ps2BiosHeaderResult(PS2_BIOS_MISSING)

    def test_a_directory_is_unreadable(self, tmp_path):
        assert RealMachine().read_ps2_bios_header(str(tmp_path)) == Ps2BiosHeaderResult(PS2_BIOS_UNREADABLE)

    def test_a_locked_file_is_unreadable(self, tmp_path):
        locked = tmp_path / "locked.bin"
        locked.write_bytes(minimal_bios())
        locked.chmod(0)
        try:
            result = RealMachine().read_ps2_bios_header(str(locked))
        finally:
            locked.chmod(0o600)
        assert result == Ps2BiosHeaderResult(PS2_BIOS_UNREADABLE)

    def test_the_result_holds_a_header_exactly_when_ok(self):
        header = ps2_bios.Ps2BiosHeader.from_strings(ROMVER, SERIAL)
        with pytest.raises(ValueError, match="exactly when status is 'ok'"):
            Ps2BiosHeaderResult(PS2_BIOS_OK)
        with pytest.raises(ValueError, match="exactly when status is 'ok'"):
            Ps2BiosHeaderResult(PS2_BIOS_NOT_A_BIOS, header)


class TestTheFixtureModelsTheAnswer:
    """FixtureMachine.read_ps2_bios_header — the two strings in, the same outcomes out."""

    def _machine(self, **kwargs) -> FixtureMachine:
        files = {BIOS_PATH: {"md5": "77" * 16, "size": IMAGE_SIZE}, JUNK_PATH: {"md5": "00" * 16, "size": IMAGE_SIZE}}
        return FixtureMachine(files, **kwargs)

    def test_a_declared_header_answers_the_fields_the_reader_builds(self):
        machine = self._machine(ps2_bios_headers={BIOS_PATH: {"romver": "0200EC20040614", "serial": "0190022"}})
        result = machine.read_ps2_bios_header(BIOS_PATH)
        assert result == Ps2BiosHeaderResult(PS2_BIOS_OK, ps2_bios.Ps2BiosHeader.from_strings(ROMVER, SERIAL))
        assert result.header is not None
        assert result.header.description == DESCRIPTION

    def test_a_declared_state_answers_that_state(self):
        machine = self._machine(ps2_bios_headers={JUNK_PATH: "not-a-bios", BIOS_PATH: "unreadable"})
        assert machine.read_ps2_bios_header(JUNK_PATH) == Ps2BiosHeaderResult(PS2_BIOS_NOT_A_BIOS)
        assert machine.read_ps2_bios_header(BIOS_PATH) == Ps2BiosHeaderResult(PS2_BIOS_UNREADABLE)

    def test_a_declared_file_with_no_header_answers_missing(self):
        """The fixture has no header to answer with, and says so rather than passing the file as no BIOS."""
        assert self._machine().read_ps2_bios_header(BIOS_PATH) == Ps2BiosHeaderResult(PS2_BIOS_MISSING)

    def test_an_absent_file_answers_missing(self):
        assert self._machine().read_ps2_bios_header("/bios/nope.bin") == Ps2BiosHeaderResult(PS2_BIOS_MISSING)

    def test_a_directory_answers_unreadable(self):
        assert self._machine().read_ps2_bios_header("/bios") == Ps2BiosHeaderResult(PS2_BIOS_UNREADABLE)

    def test_an_unreadable_file_answers_unreadable_before_any_test(self):
        machine = FixtureMachine({BIOS_PATH: {"status": "unreadable", "size": IMAGE_SIZE}})
        assert machine.read_ps2_bios_header(BIOS_PATH) == Ps2BiosHeaderResult(PS2_BIOS_UNREADABLE)

    def test_an_inaccessible_path_answers_unreadable(self):
        machine = self._machine(inaccessible=["/bios"])
        assert machine.read_ps2_bios_header(BIOS_PATH) == Ps2BiosHeaderResult(PS2_BIOS_UNREADABLE)

    def test_a_link_answers_its_targets_header(self):
        machine = self._machine(
            symlinks={"/bios/ps2.bin": BIOS_PATH},
            ps2_bios_headers={BIOS_PATH: {"romver": "0200EC20040614", "serial": "0190022"}},
        )
        assert machine.read_ps2_bios_header("/bios/ps2.bin").status == PS2_BIOS_OK

    def test_a_header_on_an_undeclared_file_is_refused(self):
        with pytest.raises(ValueError, match="no file is declared there"):
            self._machine(ps2_bios_headers={"/bios/nope.bin": "not-a-bios"})

    def test_a_header_on_an_unreadable_file_is_refused(self):
        files = {BIOS_PATH: {"status": "unreadable", "size": IMAGE_SIZE}}
        with pytest.raises(ValueError, match="an unreadable file states no header answer"):
            FixtureMachine(files, ps2_bios_headers={BIOS_PATH: "not-a-bios"})

    def test_an_unknown_state_is_refused(self):
        with pytest.raises(ValueError, match="a state must be one of"):
            self._machine(ps2_bios_headers={BIOS_PATH: "corrupt"})

    def test_a_header_object_needs_exactly_the_two_strings(self):
        with pytest.raises(ValueError, match="exactly"):
            self._machine(ps2_bios_headers={BIOS_PATH: {"romver": "0200EC20040614"}})

    def test_a_romver_is_fourteen_bytes(self):
        with pytest.raises(ValueError, match="romver is the 14 bytes"):
            self._machine(ps2_bios_headers={BIOS_PATH: {"romver": "0200E", "serial": ""}})

    def test_a_serial_is_at_most_fifteen_bytes_without_a_nul(self):
        with pytest.raises(ValueError, match="serial is at most 15 bytes"):
            self._machine(ps2_bios_headers={BIOS_PATH: {"romver": "0200EC20040614", "serial": "x" * 16}})

    def test_the_strings_are_one_byte_per_character(self):
        with pytest.raises(ValueError, match="one byte per character"):
            self._machine(ps2_bios_headers={BIOS_PATH: {"romver": "0200EC2004061€", "serial": ""}})

    def test_a_non_string_field_is_refused(self):
        with pytest.raises(ValueError, match="must be a string"):
            self._machine(ps2_bios_headers={BIOS_PATH: {"romver": 14, "serial": ""}})
