"""Tests for the archive half of the disc read — RealMachine over zips written in the test, and the fixture's model.

A zip RetroArch extracts for a libretro core is listed the way its zip backend
walks it, and the entry it extracts is read out of the zip as if written where
RetroArch writes it: the entry's own bytes at that path, everything it names
off the disk. Each is held against the same reads made of loose files.
"""

from __future__ import annotations

import random
import struct
import zipfile
from pathlib import Path
from typing import get_args

import pytest

from atlas import machine as machine_module
from atlas import psx_disc, psx_region
from atlas.machine import (
    ARCHIVE_MISSING,
    ARCHIVE_NOT_ARCHIVE,
    ARCHIVE_OK,
    ARCHIVE_UNREADABLE,
    DISC_FORMAT_NOT_READ,
    DISC_MISSING,
    DISC_OK,
    DISC_UNREADABLE,
    READ_MISSING,
    READ_OK,
    READ_UNREADABLE,
    ArchiveListResult,
    ExtractedEntry,
    FixtureMachine,
    RealMachine,
)
from atlas.placement import REASON_CONTENT_UNOPENABLE
from atlas.psx_region import EMULATOR_BEETLE, EMULATOR_SWANSTATION, DiscRegionReading
from tests.pbp_writer import Disc, Pbp
from tests.test_psx_disc import CUE, disc

TRACK = disc()
BIN_IDENTITY = psx_disc.identity_of(lambda lba: TRACK[lba * 2352 : (lba + 1) * 2352] or None)
BEETLE = psx_region.ArchiveHands(claims=frozenset({"exe", "cue", "toc", "ccd", "m3u", "pbp", "chd"}))
SWAN = psx_region.ArchiveHands(claims=frozenset({"cue", "bin", "img", "iso", "chd", "pbp", "m3u"}))


def zip_bytes(path: Path, members: dict[str, bytes], method: int) -> bytes:
    return Path(write_zip(path, members, method)).read_bytes()


def write_zip(path: Path, members: dict[str, bytes], method: int = zipfile.ZIP_DEFLATED) -> str:
    with zipfile.ZipFile(path, "w", compression=method) as archive:
        for name, data in members.items():
            archive.writestr(name, data)
    return str(path)


class TestTheListing:
    def test_every_entry_in_the_central_directory_s_order(self, tmp_path):
        path = write_zip(tmp_path / "Game.zip", {"b.cue": b"", "dir/": b"", "a.bin": b""})
        assert RealMachine().list_disc_archive(path) == ArchiveListResult(ARCHIVE_OK, ("b.cue", "dir/", "a.bin"))

    def test_a_name_keeps_the_bytes_the_archive_stores(self, tmp_path):
        path = tmp_path / "Game.zip"
        with zipfile.ZipFile(path, "w") as archive:
            info = zipfile.ZipInfo("Jé.cue")
            archive.writestr(info, b"")
        # Written as UTF-8 with the flag set; a CP437 name is re-encoded to its own bytes either way.
        assert RealMachine().list_disc_archive(str(path)).members == ("Jé.cue",)

    def test_a_name_in_cp437_keeps_its_bytes(self, tmp_path):
        path = tmp_path / "Game.zip"
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr("x.cue", b"")
        # "x.cue" -> "\x82.cue" (CP437 for é) in both headers, the UTF-8 flag left clear: the name's byte is 0x82.
        path.write_bytes(path.read_bytes().replace(b"x.cue", b"\x82.cue"))
        assert RealMachine().list_disc_archive(str(path)).members == (b"\x82.cue".decode("utf-8", "surrogateescape"),)

    def test_what_does_not_list(self, tmp_path):
        machine = RealMachine()
        assert machine.list_disc_archive(str(tmp_path / "None.zip")) == ArchiveListResult(ARCHIVE_MISSING)
        (tmp_path / "Bad.zip").write_bytes(b"not a zip")
        assert machine.list_disc_archive(str(tmp_path / "Bad.zip")) == ArchiveListResult(ARCHIVE_NOT_ARCHIVE)
        (tmp_path / "Dir.zip").mkdir()
        assert machine.list_disc_archive(str(tmp_path / "Dir.zip")) == ArchiveListResult(ARCHIVE_UNREADABLE)


def write_undecodable_name(path: Path) -> str:
    """A zip whose one name is flagged UTF-8 and is not — bytes RetroArch reads as they stand, and zipfile refuses."""
    write_zip(path, {"aaaa.cue": CUE.encode()}, zipfile.ZIP_STORED)
    raw = bytearray(path.read_bytes())
    central = raw.find(b"PK\x01\x02")
    raw[central + 8 : central + 10] = (0x800).to_bytes(2, "little")
    name = raw.find(b"aaaa.cue", central)
    raw[name : name + 2] = b"\xff\xfe"
    path.write_bytes(bytes(raw))
    return str(path)


def write_corrupt_stream(path: Path, name: str, data: bytes) -> str:
    """A zip whose one deflated entry's stream is overwritten with bytes that do not decode."""
    write_zip(path, {name: data})
    raw = bytearray(path.read_bytes())
    start = raw.find(name.encode()) + len(name)
    raw[start + 2 : start + 60] = b"\xff" * 58
    path.write_bytes(bytes(raw))
    return str(path)


class TestWhatZipfileCannotRead:
    def test_a_name_flagged_utf8_that_is_not_lists_as_unreadable(self, tmp_path):
        path = write_undecodable_name(tmp_path / "Game.zip")
        assert RealMachine().list_disc_archive(path) == ArchiveListResult(ARCHIVE_UNREADABLE)
        # PUAE's walk of the same zip answers the same word.
        assert RealMachine().list_archive(path) == ArchiveListResult(ARCHIVE_UNREADABLE)

    def test_an_entry_of_such_a_zip_is_unreadable(self, tmp_path):
        path = write_undecodable_name(tmp_path / "Game.zip")
        result = RealMachine().read_disc_identity(str(tmp_path / "Game.cue"), extracted=ExtractedEntry(path, "x.cue"))
        assert result.status == DISC_UNREADABLE

    def test_a_deflate_stream_that_does_not_decode(self, tmp_path):
        path = write_corrupt_stream(tmp_path / "Game.zip", "Game.bin", TRACK)
        machine = RealMachine()
        result = machine.read_disc_identity(str(tmp_path / "Game.bin"), extracted=ExtractedEntry(path, "Game.bin"))
        assert result.status == DISC_UNREADABLE
        assert machine.read_archive_text(path, "Game.bin", 1 << 20) == machine_module.ReadResult(READ_UNREADABLE)


def with_central(path: Path, at: int, value: bytes) -> str:
    """The zip at *path* with *value* written *at* bytes into its first central directory header."""
    raw = bytearray(path.read_bytes())
    central = raw.find(b"PK\x01\x02")
    raw[central + at : central + at + len(value)] = value
    path.write_bytes(bytes(raw))
    return str(path)


class TestWhatZipfileWillNotTake:
    """One boundary for what zipfile raises: each case ends in the documented word."""

    def test_a_version_needed_zipfile_does_not_implement(self, tmp_path):
        # The "version needed to extract" field: zipfile refuses past 6.3 with NotImplementedError.
        path = with_central(Path(write_zip(tmp_path / "Game.zip", {"Game.cue": CUE.encode()})), 6, b"\xff")
        machine = RealMachine()
        assert machine.list_disc_archive(path) == ArchiveListResult(ARCHIVE_UNREADABLE)
        assert machine.list_archive(path) == ArchiveListResult(ARCHIVE_UNREADABLE)
        result = machine.read_disc_identity(str(tmp_path / "Game.cue"), extracted=ExtractedEntry(path, "Game.cue"))
        assert result.status == DISC_UNREADABLE
        assert machine.read_archive_text(path, "Game.cue", 1 << 20) == machine_module.ReadResult(READ_UNREADABLE)

    def test_a_local_header_flagged_utf8_over_bytes_that_are_not(self, tmp_path):
        path = tmp_path / "Game.zip"
        write_zip(path, {"aaaa.bin": TRACK}, zipfile.ZIP_STORED)
        raw = bytearray(path.read_bytes().replace(b"aaaa", b"\xff\xfeaa"))
        raw[6:8] = (0x800).to_bytes(2, "little")  # the local header's flags only
        path.write_bytes(bytes(raw))
        machine = RealMachine()
        (entry,) = machine.list_disc_archive(str(path)).members
        result = machine.read_disc_identity(str(tmp_path / "x.bin"), extracted=ExtractedEntry(str(path), entry))
        assert result.status == DISC_UNREADABLE
        assert machine.read_archive_text(str(path), entry, 1 << 22) == machine_module.ReadResult(READ_UNREADABLE)

    @pytest.mark.parametrize("flag", [0x20, 0x40], ids=["patched", "strong-encryption"])
    def test_an_entry_flagged_as_one_atlas_does_not_read(self, tmp_path, flag):
        path = with_central(Path(write_zip(tmp_path / "Game.zip", {"Game.bin": TRACK})), 8, flag.to_bytes(2, "little"))
        result = RealMachine().read_disc_identity(str(tmp_path / "Game.bin"), extracted=ExtractedEntry(path, "Game.bin"))
        assert result.status == DISC_FORMAT_NOT_READ

    def test_a_read_of_no_size_reads_the_rest_in_charged_chunks(self, tmp_path, monkeypatch):
        path = write_zip(tmp_path / "Game.zip", {"Game.bin": TRACK})
        with machine_module._open_entry(path, "Game.bin") as handle:  # pyright: ignore[reportPrivateUsage]
            handle.seek(4)
            assert handle.read() == TRACK[4:]
        monkeypatch.setattr(machine_module, "_ENTRY_CHUNK", 1 << 12)
        monkeypatch.setattr(machine_module, "_MAX_ENTRY_INFLATE", 1 << 14)
        with machine_module._open_entry(path, "Game.bin") as handle:  # pyright: ignore[reportPrivateUsage]
            with pytest.raises(psx_disc.Unreadable, match="decompressing more than"):
                handle.read()

    def test_an_unsized_read_is_decompressed_in_charged_chunks(self, tmp_path, monkeypatch):
        path = write_zip(tmp_path / "Game.zip", {"Game.m3u": bytes(1 << 20)})
        asked: list[int] = []
        real_read = zipfile.ZipExtFile.read

        def spy(self: zipfile.ZipExtFile, n: int | None = -1) -> bytes:
            asked.append(-1 if n is None else n)
            return real_read(self, n)

        monkeypatch.setattr(zipfile.ZipExtFile, "read", spy)
        monkeypatch.setattr(machine_module, "_ENTRY_CHUNK", 1 << 16)
        monkeypatch.setattr(machine_module, "_MAX_ENTRY_INFLATE", 1 << 18)
        assert RealMachine().read_archive_text(path, "Game.m3u", 1 << 21) == machine_module.ReadResult(READ_UNREADABLE)
        assert asked
        assert max(asked) <= 1 << 16
        assert min(asked) >= 0
        assert sum(asked) <= 1 << 18


class TestWhatIsNoZipAtAll:
    """``not-archive`` only where RetroArch's backend finds no end record; a zip zipfile refuses is unread."""

    def test_a_central_extra_field_zipfile_refuses_is_a_zip_unread(self, tmp_path):
        path = tmp_path / "Game.zip"
        with zipfile.ZipFile(path, "w", zipfile.ZIP_STORED) as archive:
            info = zipfile.ZipInfo("Game.bin", (2020, 1, 1, 0, 0, 0))
            info.extra = struct.pack("<HH", 0xCAFE, 8) + bytes(4)  # states 8 bytes and holds 4
            archive.writestr(info, TRACK)
        machine = RealMachine()
        assert machine.list_disc_archive(str(path)) == ArchiveListResult(ARCHIVE_UNREADABLE)
        assert machine.list_archive(str(path)) == ArchiveListResult(ARCHIVE_UNREADABLE)

    def test_an_end_record_must_end_the_file_with_its_comment(self, tmp_path):
        # A signature whose stated comment does not reach the file's end is no record (archive_file_zlib.c:610-614).
        stray = tmp_path / "Stray.zip"
        stray.write_bytes(b"x" * 100 + b"PK\x05\x06" + bytes(16) + (5).to_bytes(2, "little"))
        machine = RealMachine()
        # zipfile reads it as an empty zip; RetroArch's backend finds no record.
        assert machine.list_disc_archive(str(stray)) == ArchiveListResult(ARCHIVE_NOT_ARCHIVE)
        # The same record with its five comment bytes is a zip zipfile reads as empty.
        whole = tmp_path / "Whole.zip"
        whole.write_bytes(b"PK\x05\x06" + bytes(16) + (5).to_bytes(2, "little") + b"note!")
        assert machine.list_disc_archive(str(whole)) == ArchiveListResult(ARCHIVE_OK, ())

    def test_bytes_that_hold_a_record_and_no_directory_it_names_are_unread(self, tmp_path):
        path = tmp_path / "Broken.zip"
        # An end record naming one entry in a directory past the file's start.
        path.write_bytes(b"PK\x05\x06" + struct.pack("<HHHHIIH", 0, 0, 1, 1, 46, 5000, 0))
        assert RealMachine().list_disc_archive(str(path)) == ArchiveListResult(ARCHIVE_UNREADABLE)


class TestDamagedZips:
    def test_no_mutation_ends_in_anything_but_the_documented_answers(self, tmp_path):
        # Seeded: header bytes — the local header, the central directory and
        # the end record — are where one byte decides what zipfile does.
        rng = random.Random(691)
        pbp = Pbp([Disc(TRACK)]).encode()
        bases = [
            (zip_bytes(tmp_path / "a.zip", {"Game.pbp": pbp}, zipfile.ZIP_DEFLATED), "Game.pbp"),
            (zip_bytes(tmp_path / "b.zip", {"Game.pbp": pbp}, zipfile.ZIP_STORED), "Game.pbp"),
            (zip_bytes(tmp_path / "c.zip", {"Game.cue": CUE.encode(), "Game.bin": TRACK}, zipfile.ZIP_DEFLATED), "Game.cue"),
        ]
        listed = {ARCHIVE_OK, ARCHIVE_UNREADABLE, ARCHIVE_NOT_ARCHIVE}
        read = {READ_OK, READ_MISSING, READ_UNREADABLE, machine_module.READ_INVALID_TEXT}
        path = tmp_path / "Mutated.zip"
        machine = RealMachine()
        for _ in range(150):
            base, entry = rng.choice(bases)
            image = bytearray(base)
            hot = [image.find(b"PK\x03\x04"), image.rfind(b"PK\x01\x02"), image.rfind(b"PK\x05\x06")]
            for _ in range(rng.randint(1, 6)):
                at = rng.choice(hot) + rng.randint(0, 46) if rng.random() < 0.7 else rng.randrange(len(image))
                if 0 <= at < len(image):
                    image[at] = rng.randrange(256)
            path.write_bytes(bytes(image))
            assert machine.list_disc_archive(str(path)).status in listed
            assert machine.list_archive(str(path)).status in listed
            result = machine.read_disc_identity(str(tmp_path / entry), extracted=ExtractedEntry(str(path), entry))
            assert result.status in get_args(machine_module.DiscIdentityStatus)
            text = machine.read_archive_text(str(path), entry, 1 << 22)
            assert text is None or text.status in read


class TestTheExtractedEntry:
    @pytest.mark.parametrize("method", [zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED], ids=["stored", "deflated"])
    def test_a_pbp_entry_reads_as_the_pbp(self, tmp_path, method):
        path = write_zip(tmp_path / "Game.zip", {"Game.pbp": Pbp([Disc(TRACK)]).encode()}, method)
        result = RealMachine().read_disc_identity(str(tmp_path / "Game.pbp"), extracted=ExtractedEntry(path, "Game.pbp"))
        assert (result.status, result.identity) == (DISC_OK, BIN_IDENTITY)

    def test_a_cue_entry_names_its_bin_beside_where_it_is_written(self, tmp_path):
        path = write_zip(tmp_path / "Game.zip", {"Game.cue": CUE.encode(), "Game.bin": TRACK})
        extracted = ExtractedEntry(path, "Game.cue")
        written = str(tmp_path / "Game.cue")
        assert RealMachine().read_disc_identity(written, extracted=extracted).status == DISC_MISSING
        (tmp_path / "Game.bin").write_bytes(TRACK)
        assert RealMachine().read_disc_identity(written, extracted=extracted).identity == BIN_IDENTITY

    def test_the_written_path_is_the_entry_whatever_lies_there(self, tmp_path):
        path = write_zip(tmp_path / "Game.zip", {"Game.bin": TRACK})
        (tmp_path / "Game.bin").write_bytes(b"a loose file RetroArch overwrites")
        result = RealMachine().read_disc_identity(str(tmp_path / "Game.bin"), extracted=ExtractedEntry(path, "Game.bin"))
        assert result.identity == BIN_IDENTITY

    def test_a_method_atlas_does_not_read(self, tmp_path):
        path = write_zip(tmp_path / "Game.zip", {"Game.bin": TRACK}, zipfile.ZIP_BZIP2)
        result = RealMachine().read_disc_identity(str(tmp_path / "Game.bin"), extracted=ExtractedEntry(path, "Game.bin"))
        assert result.status == DISC_FORMAT_NOT_READ

    def test_an_archive_gone_or_an_entry_it_does_not_hold(self, tmp_path):
        path = write_zip(tmp_path / "Game.zip", {"Game.bin": TRACK})
        machine = RealMachine()
        written = str(tmp_path / "Game.bin")
        assert machine.read_disc_identity(written, extracted=ExtractedEntry(path, "Other.bin")).status == DISC_MISSING
        gone = ExtractedEntry(str(tmp_path / "None.zip"), "Game.bin")
        assert machine.read_disc_identity(written, extracted=gone).status == DISC_MISSING

    def test_a_deflated_entry_s_decompression_is_bounded(self, tmp_path, monkeypatch):
        path = write_zip(tmp_path / "Game.zip", {"Game.bin": TRACK})
        monkeypatch.setattr(machine_module, "_MAX_ENTRY_INFLATE", 2352 * 5)
        result = RealMachine().read_disc_identity(str(tmp_path / "Game.bin"), extracted=ExtractedEntry(path, "Game.bin"))
        assert result.status == DISC_UNREADABLE

    def test_a_stored_entry_s_reads_are_not_charged(self, tmp_path, monkeypatch):
        path = write_zip(tmp_path / "Game.zip", {"Game.bin": TRACK}, zipfile.ZIP_STORED)
        monkeypatch.setattr(machine_module, "_MAX_ENTRY_INFLATE", 1)
        result = RealMachine().read_disc_identity(str(tmp_path / "Game.bin"), extracted=ExtractedEntry(path, "Game.bin"))
        assert result.identity == BIN_IDENTITY


class TestAnEntryAsText:
    def test_the_text_within_its_bound(self, tmp_path):
        path = write_zip(tmp_path / "Game.zip", {"Game.m3u": b"a.cue\r\nb.cue\n"})
        machine = RealMachine()
        read = machine.read_archive_text(path, "Game.m3u", 64)
        assert read is not None
        assert (read.status, read.text) == (READ_OK, "a.cue\r\nb.cue\n")
        assert machine.read_archive_text(path, "Game.m3u", 4) is None

    def test_what_does_not_come_back(self, tmp_path):
        path = write_zip(tmp_path / "Game.zip", {"Game.m3u": b"x"}, zipfile.ZIP_BZIP2)
        machine = RealMachine()
        assert machine.read_archive_text(path, "Other.m3u", 64) == machine_module.ReadResult(READ_MISSING)
        assert machine.read_archive_text(str(tmp_path / "None.zip"), "Game.m3u", 64) == machine_module.ReadResult(
            READ_MISSING
        )
        assert machine.read_archive_text(path, "Game.m3u", 64) == machine_module.ReadResult(READ_UNREADABLE)


class TestTheWholeRoute:
    """psx_region over RealMachine and real zips: the entry RetroArch extracts, read where it is written."""

    def test_beetle_extracts_the_cue_alone_and_cannot_open_it(self, tmp_path):
        path = write_zip(tmp_path / "Game.zip", {"Game.cue": CUE.encode(), "Game.bin": TRACK})
        reading = psx_region.read_disc_region(RealMachine(), EMULATOR_BEETLE, path, BEETLE)
        assert reading == DiscRegionReading(reason=REASON_CONTENT_UNOPENABLE, disc=f"{path}#Game.cue", extension="cue")

    def test_swanstation_extracts_the_bin_and_reads_it_bare(self, tmp_path):
        path = write_zip(tmp_path / "Game.zip", {"Game.cue": CUE.encode(), "Game.bin": TRACK})
        assert psx_region.read_disc_region(RealMachine(), EMULATOR_SWANSTATION, path, SWAN).region == "ntsc-u"

    def test_a_cache_directory_is_where_the_entry_is_written(self, tmp_path):
        cache = tmp_path / "cache"
        cache.mkdir()
        (cache / "Game.bin").write_bytes(TRACK)
        path = write_zip(tmp_path / "Game.zip", {"Game.cue": CUE.encode()})
        hands = psx_region.ArchiveHands(claims=BEETLE.claims, cache_directory=str(cache))
        assert psx_region.read_disc_region(RealMachine(), EMULATOR_BEETLE, path, hands).region == "ntsc-u"

    def test_a_playlist_extracted_alone(self, tmp_path):
        path = write_zip(tmp_path / "Game.zip", {"Game.m3u": b"Game.pbp\n", "Game.pbp": Pbp([Disc(TRACK)]).encode()})
        machine = RealMachine()
        reading = psx_region.read_disc_region(machine, EMULATOR_BEETLE, path, BEETLE)
        assert reading == DiscRegionReading(reason=REASON_CONTENT_UNOPENABLE, disc=f"{path}#Game.m3u", extension="m3u")
        (tmp_path / "Game.pbp").write_bytes(Pbp([Disc(TRACK)]).encode())
        assert psx_region.read_disc_region(machine, EMULATOR_BEETLE, path, BEETLE).region == "ntsc-u"


class TestTheFixtureModelsTheAnswer:
    ZIP = "/roms/Game.zip"

    def test_an_entry_identity_needs_a_listed_entry(self):
        with pytest.raises(ValueError, match="lists in that archive"):
            FixtureMachine({self.ZIP: "x"}, archives={self.ZIP: ["Other.cue"]}, disc_identities={f"{self.ZIP}#Game.cue": "missing"})
        with pytest.raises(ValueError, match="lists in that archive"):
            FixtureMachine({self.ZIP: "x"}, disc_identities={f"{self.ZIP}#Game.cue": "missing"})

    def test_an_entry_text_needs_a_listed_entry_and_a_string(self):
        with pytest.raises(ValueError, match="lists in that archive"):
            FixtureMachine({self.ZIP: "x"}, archives={self.ZIP: []}, archive_texts={f"{self.ZIP}#Game.m3u": ""})
        with pytest.raises(ValueError, match="must be a string"):
            FixtureMachine({self.ZIP: "x"}, archives={self.ZIP: ["Game.m3u"]}, archive_texts={f"{self.ZIP}#Game.m3u": 1})
        with pytest.raises(ValueError, match="names no archive entry"):
            FixtureMachine({self.ZIP: "x"}, archives={self.ZIP: ["Game.m3u"]}, archive_texts={"/roms/Game.m3u": ""})

    def test_the_listing_is_judged_as_the_real_one(self):
        machine = FixtureMachine({self.ZIP: "x", "/roms/Plain.zip": "x"}, dirs=["/roms/Dir.zip"], archives={self.ZIP: ["a.cue"]})
        assert machine.list_disc_archive(self.ZIP) == ArchiveListResult(ARCHIVE_OK, ("a.cue",))
        assert machine.list_disc_archive("/roms/Plain.zip") == ArchiveListResult(ARCHIVE_NOT_ARCHIVE)
        assert machine.list_disc_archive("/roms/Dir.zip") == ArchiveListResult(ARCHIVE_UNREADABLE)
        assert machine.list_disc_archive("/roms/None.zip") == ArchiveListResult(ARCHIVE_MISSING)

    def test_an_entry_s_text_within_its_bound(self):
        machine = FixtureMachine(
            {self.ZIP: "x"}, archives={self.ZIP: ["Game.m3u"]}, archive_texts={f"{self.ZIP}#Game.m3u": "a.cue\n"}
        )
        read = machine.read_archive_text(self.ZIP, "Game.m3u", 64)
        assert read is not None
        assert read.text == "a.cue\n"
        assert machine.read_archive_text(self.ZIP, "Game.m3u", 2) is None
        assert machine.read_archive_text("/roms/None.zip", "Game.m3u", 64) == machine_module.ReadResult(READ_MISSING)
