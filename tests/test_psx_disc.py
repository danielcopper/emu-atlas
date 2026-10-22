"""Tests for atlas.psx_disc — the disc identity read against hand-built images.

Every image here is assembled sector by sector in the test: raw 2352-byte Mode 2
sectors (sync, header, subheader, 2048 data bytes, the EDC Beetle checks), an ISO
9660 Primary Volume Descriptor at sector 16, a root directory, and the files a
region rule reads. Nothing is borrowed from a real disc, so what the reader is
proven against is the layout the emulators walk and the checksum Beetle computes
(edc_crc32.c:122-130, recover-raw.c:55-73 at libretro/beetle-psx-libretro@d6383bf).
The seam classes prove both machines map every outcome to the same words.
"""

from __future__ import annotations

import struct
from pathlib import Path

import pytest

from atlas import psx_disc
from atlas.machine import (
    DISC_FORMAT_NOT_READ,
    DISC_MISSING,
    DISC_NOT_A_DISC,
    DISC_OK,
    DISC_UNREADABLE,
    DiscIdentityResult,
    FixtureMachine,
    RealMachine,
)

US = b"          Licensed  by          Sony Computer Entertainment Amer  ica "
SYSTEM_CNF = b"BOOT = cdrom:\\SLUS_000.01;1\r\nTCB = 4\r\nEVENT = 10\r\nSTACK = 801FFFF0\r\n"
ROOT_LBA = 18
SECTORS = 24
CUE = 'FILE "Game.bin" BINARY\n  TRACK 01 MODE2/2352\n    INDEX 01 00:00:00\n'


def raw_sector(lba: int, data: bytes, *, mode: int = 2) -> bytes:
    """A raw Mode 2 Form 1 sector: sync, header, subheader, data, and the EDC over subheader and data."""
    sync = b"\x00" + b"\xff" * 10 + b"\x00"
    header = bytes((0, 2, lba % 75, mode))
    subheader = b"\x00\x00\x08\x00" * 2
    body = subheader + data.ljust(psx_disc.USER_DATA_SIZE, b"\x00")
    edc = struct.pack("<I", psx_disc.edc(body))
    sector = sync + header + body + edc
    return sector.ljust(psx_disc.RAW_SECTOR_SIZE, b"\x00")


def record(name: bytes, lba: int, size: int, *, directory: bool = False) -> bytes:
    """One ISO 9660 directory record (ECMA-119 9.1), padded to an even length."""
    body = (
        bytes((0,))
        + struct.pack("<I", lba)
        + struct.pack(">I", lba)
        + struct.pack("<I", size)
        + struct.pack(">I", size)
        + bytes(7)
        + bytes((2 if directory else 0, 0, 0))
        + struct.pack("<H", 1)
        + struct.pack(">H", 1)
        + bytes((len(name),))
        + name
    )
    length = 1 + len(body)
    if length % 2:
        body += b"\x00"
        length += 1
    return bytes((length,)) + body


def pvd(root: bytes) -> bytes:
    data = bytearray(psx_disc.USER_DATA_SIZE)
    data[0:6] = b"\x01CD001"
    data[156 : 156 + len(root)] = root
    return bytes(data)


def disc(
    *,
    license: bytes = US,
    files: dict[bytes, bytes] | None = None,
    root_records: list[bytes] | None = None,
    corrupt: int | None = None,
    mode_at: dict[int, int] | None = None,
) -> bytes:
    """A track 1 image: the licence at sector 4, the PVD at 16, a root directory at 18, files from 20 on."""
    files = {b"SYSTEM.CNF;1": SYSTEM_CNF, b"SLUS_000.01;1": b"PS-X EXE"} if files is None else files
    sectors: dict[int, bytes] = {4: license}
    records = [record(b"\x00", ROOT_LBA, 2048, directory=True), record(b"\x01", ROOT_LBA, 2048, directory=True)]
    lba = 20
    for name, content in files.items():
        records.append(record(name, lba, len(content)))
        sectors[lba] = content
        lba += 1
    records.extend(root_records or [])
    sectors[ROOT_LBA] = b"".join(records)
    sectors[16] = pvd(record(b"\x00", ROOT_LBA, 2048, directory=True))
    sectors[17] = b"\xffCD001"
    image = bytearray()
    for index in range(SECTORS):
        image += raw_sector(index, sectors.get(index, b""), mode=(mode_at or {}).get(index, 2))
    if corrupt is not None:
        image[corrupt * psx_disc.RAW_SECTOR_SIZE + 100] ^= 0xFF
    return bytes(image)


def write_game(tmp_path: Path, image: bytes, cue: str = CUE) -> Path:
    (tmp_path / "Game.bin").write_bytes(image)
    path = tmp_path / "Game.cue"
    path.write_text(cue)
    return path


def read(path: Path) -> psx_disc.DiscIdentity:
    return psx_disc.read_identity(str(path), lambda name: open(name, "rb"))


class TestTheEdc:
    def test_the_checksum_is_the_reflected_polynomial_beetle_ships(self):
        # One byte from a zero register is that byte's table entry, and
        # edc_crc32.c's table begins 0x00000000, 0x90910101, 0x91210201.
        assert [psx_disc.edc(bytes((byte,))) for byte in range(3)] == [0x00000000, 0x90910101, 0x91210201]


class TestAnImageIsRead:
    def test_a_cue_sheet_yields_the_licence_and_system_cnf(self, tmp_path):
        identity = read(write_game(tmp_path, disc()))
        assert identity.license == US.ljust(psx_disc.LICENSE_LENGTH, b"\x00")
        assert identity.system_cnf == SYSTEM_CNF
        assert identity.system_cnf_sector == SYSTEM_CNF.ljust(psx_disc.USER_DATA_SIZE, b"\x00")
        assert identity.boot_found is True
        assert identity.edc_valid is True

    def test_a_bare_bin_is_one_raw_track_from_its_first_byte(self, tmp_path):
        path = tmp_path / "Game.BIN"
        path.write_bytes(disc())
        assert read(path).system_cnf == SYSTEM_CNF

    def test_an_executable_the_disc_does_not_hold_is_not_found(self, tmp_path):
        identity = read(write_game(tmp_path, disc(files={b"SYSTEM.CNF;1": SYSTEM_CNF})))
        assert identity.boot_found is False

    def test_without_system_cnf_the_fallback_executable_is_looked_up(self, tmp_path):
        identity = read(write_game(tmp_path, disc(files={b"PSX.EXE;1": b"PS-X EXE"})))
        assert identity.system_cnf is None
        assert identity.system_cnf_sector is None
        assert identity.boot_found is True

    def test_an_executable_in_a_subdirectory_is_found_through_it(self, tmp_path):
        cnf = b"BOOT = cdrom:\\GAME\\SLUS_000.01;1\r\n"
        directory = [
            record(b"\x00", 21, 2048, directory=True),
            record(b"\x01", ROOT_LBA, 2048, directory=True),
            record(b"SLUS_000.01;1", 22, 8),
        ]
        image = bytearray(disc(files={b"SYSTEM.CNF;1": cnf}, root_records=[record(b"GAME", 21, 2048, directory=True)]))
        for lba, data in ((21, b"".join(directory)), (22, b"PS-X EXE")):
            image[lba * psx_disc.RAW_SECTOR_SIZE : (lba + 1) * psx_disc.RAW_SECTOR_SIZE] = raw_sector(lba, data)
        assert read(write_game(tmp_path, bytes(image))).boot_found is True

    def test_a_sector_beetle_reads_that_fails_its_checksum_is_noted(self, tmp_path):
        assert read(write_game(tmp_path, disc(corrupt=4))).edc_valid is False

    def test_a_sector_beetle_never_reads_does_not_count(self, tmp_path):
        # Sector 23 holds nothing any rule reads.
        assert read(write_game(tmp_path, disc(corrupt=23))).edc_valid is True


class TestALayoutTheEmulatorsReadApartIsRefused:
    def test_a_sector_that_is_not_mode_2(self, tmp_path):
        path = write_game(tmp_path, disc(mode_at={4: 1}))
        with pytest.raises(psx_disc.FormatNotRead, match="not a Mode 2 sector"):
            read(path)

    def test_a_cooked_iso_read_as_raw_sectors(self, tmp_path):
        path = tmp_path / "Game.iso"
        path.write_bytes(bytes(psx_disc.USER_DATA_SIZE * 40))
        with pytest.raises(psx_disc.FormatNotRead):
            read(path)

    @pytest.mark.parametrize(
        "cue",
        [
            'FILE "Game.bin" BINARY\nTRACK 01 MODE1/2048\nINDEX 01 00:00:00\n',
            'FILE "Game.bin" BINARY\nTRACK 01 MODE2/2352\nINDEX 00 00:00:00\nINDEX 01 00:02:00\n',
            'FILE "Game.bin" BINARY\nTRACK 01 MODE2/2352\nPREGAP 00:02:00\nINDEX 01 00:00:00\n',
            'FILE "Game.bin" BINARY\nTRACK 01 MODE2/2352\nINDEX 01 00:00:01\n',
        ],
        ids=["cooked-track", "index-0", "pregap", "index-1-late"],
    )
    def test_a_track_1_that_is_not_raw_mode_2_at_the_head_of_its_file(self, tmp_path, cue):
        path = write_game(tmp_path, disc(), cue)
        with pytest.raises(psx_disc.FormatNotRead, match="not raw Mode 2"):
            read(path)

    def test_no_primary_volume_descriptor_at_sector_16(self, tmp_path):
        image = bytearray(disc())
        image[16 * psx_disc.RAW_SECTOR_SIZE : 17 * psx_disc.RAW_SECTOR_SIZE] = raw_sector(16, b"")
        path = write_game(tmp_path, bytes(image))
        with pytest.raises(psx_disc.FormatNotRead, match="Primary Volume Descriptor"):
            read(path)

    def test_a_system_cnf_only_some_lookups_find(self, tmp_path):
        # Lower case: DuckStation and SwanStation compare without case, Beetle exactly.
        path = write_game(tmp_path, disc(files={b"system.cnf;1": SYSTEM_CNF}))
        with pytest.raises(psx_disc.FormatNotRead, match="different records"):
            read(path)

    def test_a_system_cnf_without_its_version(self, tmp_path):
        # DuckStation strips the version it does not find; SwanStation requires a ';'.
        path = write_game(tmp_path, disc(files={b"SYSTEM.CNF": SYSTEM_CNF}))
        with pytest.raises(psx_disc.FormatNotRead, match="different records"):
            read(path)

    def test_a_root_directory_beetle_will_not_read(self, tmp_path):
        # A root of 10 MiB or more ends Beetle's SYSTEM.CNF route
        # (libretro.cpp:1450-1455) while DuckStation and SwanStation read on.
        sectors = 18 + psx_disc.BEETLE_ROOT_LIMIT // psx_disc.USER_DATA_SIZE + 1
        image = bytearray(disc())
        image += b"".join(raw_sector(lba, b"") for lba in range(SECTORS, sectors))
        big_root = record(b"\x00", ROOT_LBA, psx_disc.BEETLE_ROOT_LIMIT, directory=True)
        image[16 * psx_disc.RAW_SECTOR_SIZE : 17 * psx_disc.RAW_SECTOR_SIZE] = raw_sector(16, pvd(big_root))
        path = write_game(tmp_path, bytes(image))
        with pytest.raises(psx_disc.FormatNotRead, match="different records"):
            read(path)

    def test_a_suffix_no_reader_opens(self, tmp_path):
        path = tmp_path / "Game.chd"
        path.write_bytes(b"MComprHD")
        with pytest.raises(psx_disc.FormatNotRead, match="no reader here opens"):
            read(path)

    def test_an_image_ending_before_sector_4(self, tmp_path):
        path = write_game(tmp_path, disc()[: 3 * psx_disc.RAW_SECTOR_SIZE])
        with pytest.raises(psx_disc.FormatNotRead, match="past the end of track 1"):
            read(path)


class TestACueSheetThatOpensNoDisc:
    @pytest.mark.parametrize(
        "cue",
        [
            'FILE "Game.bin" BINARY\nTRACK 01 MODE2/2352\nINDEX 01 00:00:00\nNONSENSE\n',
            'FILE "Game.bin" WAVE\nTRACK 01 MODE2/2352\nINDEX 01 00:00:00\n',
            'TRACK 01 MODE2/2352\nINDEX 01 00:00:00\n',
            'FILE "Game.bin" BINARY\nTRACK 01 MODE2/2352\n',
            'FILE "Game.bin" BINARY\nTRACK 02 MODE2/2352\nINDEX 01 00:00:00\n',
            'FILE "Game.bin" BINARY\nTRACK 01 MODE3/2352\nINDEX 01 00:00:00\n',
            'FILE "Game.bin" BINARY\nTRACK 01 MODE2/2352\nINDEX 01 00:61:00\n',
        ],
        ids=["unknown-command", "not-binary", "track-before-file", "no-index-1", "no-track-1", "bad-mode", "bad-msf"],
    )
    def test_the_parser_refuses_it(self, tmp_path, cue):
        path = write_game(tmp_path, disc(), cue)
        with pytest.raises(psx_disc.NotADisc):
            read(path)

    def test_a_number_of_any_length_is_refused_not_raised(self, tmp_path):
        cue = f'FILE "Game.bin" BINARY\nTRACK 01 MODE2/2352\nINDEX {"9" * 5000} 00:00:00\n'
        path = write_game(tmp_path, disc(), cue)
        with pytest.raises(psx_disc.NotADisc):
            read(path)
        location = f'FILE "Game.bin" BINARY\nTRACK 01 MODE2/2352\nINDEX 01 {"9" * 5000}:00:00\n'
        assert RealMachine().read_disc_identity(str(write_game(tmp_path, disc(), location))) == DiscIdentityResult(
            DISC_NOT_A_DISC
        )

    def test_a_number_past_the_conversion_limit_is_refused_not_raised(self):
        # A cue line is at most 1023 bytes, so no token reaches Python's digit
        # limit through the parser; the conversion holds on its own anyway.
        digits = b"9" * 5000
        assert psx_disc._s32(digits) is None  # pyright: ignore[reportPrivateUsage] - the conversion's own bound

    def test_a_lone_carriage_return_ends_no_line(self, tmp_path):
        # fgets ends a line at \n alone, so TRACK is read as a FILE argument
        # and the sheet declares no track.
        cue = 'FILE "Game.bin" BINARY\rTRACK 01 MODE2/2352\n    INDEX 01 00:00:00\n'
        path = write_game(tmp_path, disc(), cue)
        with pytest.raises(psx_disc.NotADisc):
            read(path)

    def test_a_line_ends_at_its_first_nul(self, tmp_path):
        cue = 'FILE "Game.bin" BINARY\n  TRACK 01 MODE2/2352\x00 garbage\n    INDEX 01 00:00:00\n'
        assert read(write_game(tmp_path, disc(), cue)).system_cnf == SYSTEM_CNF

    def test_an_audio_track_1(self, tmp_path):
        path = write_game(tmp_path, disc(), 'FILE "Game.bin" BINARY\nTRACK 01 AUDIO\nINDEX 01 00:00:00\n')
        with pytest.raises(psx_disc.NotADisc, match="audio"):
            read(path)

    def test_comments_flags_and_quoted_names_are_read_past(self, tmp_path):
        cue = 'REM a comment\nCATALOG 0000000000000\nFILE "Game.bin" BINARY\n  TRACK 01 MODE2/2352\n    FLAGS DCP\n    INDEX 01 00:00:00\n'
        assert read(write_game(tmp_path, disc(), cue)).system_cnf == SYSTEM_CNF

    def test_every_file_the_sheet_names_must_open(self, tmp_path):
        cue = CUE + 'FILE "Game (Track 2).bin" BINARY\n  TRACK 02 AUDIO\n    INDEX 01 00:00:00\n'
        path = write_game(tmp_path, disc(), cue)
        with pytest.raises(FileNotFoundError):
            read(path)


class TestBootValue:
    @pytest.mark.parametrize(
        ("text", "value"),
        [
            (b"BOOT = cdrom:\\SLUS_000.01;1\r\n", b"cdrom:\\SLUS_000.01;1"),
            (b"boot=cdrom:\\SLES_123.45;1", b"cdrom:\\SLES_123.45;1"),
            (b"B O O T = cd rom:\\X\n", b"cdrom:\\X"),
            (b"TCB = 4\r\n", None),
            (b"BOOT2 = cdrom0:\\SLUS_200.00;1\n", None),
        ],
    )
    def test_the_tokeniser_drops_whitespace_and_matches_the_key_without_case(self, text, value):
        assert psx_disc.boot_value(text) == value

    @pytest.mark.parametrize(
        ("text", "path"),
        [
            (None, b"PSX.EXE"),
            (b"TCB = 4\n", b"PSX.EXE"),
            (b"BOOT = cdrom:\\SLUS_000.01;1\n", b"SLUS_000.01"),
            (b"BOOT = cdrom:\\\\GAME\\MAIN.EXE;1\n", b"GAME\\MAIN.EXE"),
            (b"BOOT = MAIN.EXE\n", b"MAIN.EXE"),
        ],
    )
    def test_duckstation_s_executable_path(self, text, path):
        assert psx_disc.duckstation_executable(text) == path


class TestTheSeamMapsEveryOutcome:
    """RealMachine.read_disc_identity — one status per distinct outcome, never collapsed."""

    def test_a_disc_is_ok_with_its_identity(self, tmp_path):
        result = RealMachine().read_disc_identity(str(write_game(tmp_path, disc())))
        assert result.status == DISC_OK
        assert result.identity is not None
        assert result.identity.system_cnf == SYSTEM_CNF

    def test_an_absent_image_is_missing(self, tmp_path):
        assert RealMachine().read_disc_identity(str(tmp_path / "Gone.cue")) == DiscIdentityResult(DISC_MISSING)

    def test_a_data_file_the_sheet_names_and_is_gone_is_missing(self, tmp_path):
        path = tmp_path / "Game.cue"
        path.write_text(CUE)
        assert RealMachine().read_disc_identity(str(path)) == DiscIdentityResult(DISC_MISSING)

    def test_a_directory_is_unreadable(self, tmp_path):
        (tmp_path / "Game.cue").mkdir()
        assert RealMachine().read_disc_identity(str(tmp_path / "Game.cue")) == DiscIdentityResult(DISC_UNREADABLE)

    def test_a_data_file_that_is_a_directory_is_unreadable(self, tmp_path):
        (tmp_path / "Game.bin").mkdir()
        path = tmp_path / "Game.cue"
        path.write_text(CUE)
        assert RealMachine().read_disc_identity(str(path)) == DiscIdentityResult(DISC_UNREADABLE)

    def test_a_layout_not_read_is_format_not_read(self, tmp_path):
        path = write_game(tmp_path, disc(mode_at={4: 1}))
        assert RealMachine().read_disc_identity(str(path)) == DiscIdentityResult(DISC_FORMAT_NOT_READ)

    def test_a_suffix_no_reader_opens_is_format_not_read(self, tmp_path):
        path = tmp_path / "Game.chd"
        path.write_bytes(b"MComprHD")
        assert RealMachine().read_disc_identity(str(path)) == DiscIdentityResult(DISC_FORMAT_NOT_READ)

    def test_a_refused_cue_sheet_is_not_a_disc(self, tmp_path):
        path = write_game(tmp_path, disc(), "NONSENSE\n")
        assert RealMachine().read_disc_identity(str(path)) == DiscIdentityResult(DISC_NOT_A_DISC)

    def test_the_result_holds_an_identity_exactly_when_ok(self, tmp_path):
        identity = read(write_game(tmp_path, disc()))
        with pytest.raises(ValueError, match="exactly when status is 'ok'"):
            DiscIdentityResult(DISC_OK)
        with pytest.raises(ValueError, match="exactly when status is 'ok'"):
            DiscIdentityResult(DISC_MISSING, identity)


GAME = "/roms/psx/Game.cue"
FACTS = {"license": US.decode("latin-1"), "system_cnf": SYSTEM_CNF.decode("latin-1"), "boot_found": True, "edc_valid": True}


class TestTheFixtureModelsTheAnswer:
    """FixtureMachine.read_disc_identity — the four facts in, the same outcomes out."""

    def _machine(self, **kwargs) -> FixtureMachine:
        files = {GAME: CUE, "/roms/psx/Game.chd": "x", "/roms/psx/Locked.cue": {"status": "unreadable"}}
        return FixtureMachine(files, **kwargs)

    def test_a_declared_identity_answers_what_the_reader_would_hold(self, tmp_path):
        result = self._machine(disc_identities={GAME: FACTS}).read_disc_identity(GAME)
        assert result == DiscIdentityResult(DISC_OK, read(write_game(tmp_path, disc())))

    def test_a_declared_state_answers_that_state(self):
        machine = self._machine(disc_identities={GAME: "not-a-disc"})
        assert machine.read_disc_identity(GAME) == DiscIdentityResult(DISC_NOT_A_DISC)

    def test_a_declared_image_with_no_identity_answers_missing(self):
        assert self._machine().read_disc_identity(GAME) == DiscIdentityResult(DISC_MISSING)

    def test_a_suffix_no_reader_opens_answers_format_not_read(self):
        assert self._machine().read_disc_identity("/roms/psx/Game.chd") == DiscIdentityResult(DISC_FORMAT_NOT_READ)

    def test_an_absent_file_a_directory_and_an_unreadable_file(self):
        machine = self._machine()
        assert machine.read_disc_identity("/roms/psx/Gone.cue") == DiscIdentityResult(DISC_MISSING)
        assert machine.read_disc_identity("/roms/psx") == DiscIdentityResult(DISC_UNREADABLE)
        assert machine.read_disc_identity("/roms/psx/Locked.cue") == DiscIdentityResult(DISC_UNREADABLE)

    def test_an_inaccessible_path_answers_unreadable(self):
        machine = self._machine(inaccessible=["/roms"], disc_identities={GAME: FACTS})
        assert machine.read_disc_identity(GAME) == DiscIdentityResult(DISC_UNREADABLE)

    @pytest.mark.parametrize(
        ("identities", "message"),
        [
            ({"/roms/psx/Nope.cue": FACTS}, "no file is declared there"),
            ({"/roms/psx/Locked.cue": FACTS}, "an unreadable file states no identity"),
            ({"/roms/psx/Game.chd": FACTS}, "no reader opens this suffix"),
            ({GAME: "format-not-read"}, "a state must be one of"),
            ({GAME: {**FACTS, "serial": "x"}}, "exactly"),
            ({GAME: {**FACTS, "license": "x" * 73}}, "at most"),
            ({GAME: {**FACTS, "boot_found": "yes"}}, "must be a boolean"),
            ({GAME: {**FACTS, "system_cnf": "BOOT = €"}}, "one byte per character"),
        ],
        ids=["undeclared", "unreadable", "suffix", "state", "fields", "licence-length", "boolean", "latin-1"],
    )
    def test_a_declaration_no_real_read_could_make_is_refused(self, identities, message):
        with pytest.raises(ValueError, match=message):
            self._machine(disc_identities=identities)
