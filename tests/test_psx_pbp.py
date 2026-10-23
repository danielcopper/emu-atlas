"""Tests for atlas.psx_pbp — the PBP read against images written in the test.

Every image is built by tests/pbp_writer.py around the same hand-made track 1
the bin tests read (tests/test_psx_disc.py), so the identity a PBP yields is
held against the identity that track yields as a bin. The two readers are
proven apart: what DuckStation's and SwanStation's open refuses over the
PARAM.SFO or an encryption, what Beetle's open decrypts, and where the two
read one file apart. A seeded mutation run proves that no damaged PBP ends in
anything but the reader's documented answers.
"""

from __future__ import annotations

import io
import random
import struct
from pathlib import Path

import pytest

from atlas import psx_disc, psx_pbp
from atlas.machine import (
    DISC_CODEC_UNAVAILABLE,
    DISC_FORMAT_NOT_READ,
    DISC_NOT_A_DISC,
    DISC_OK,
    DISC_UNREADABLE,
    DiscIdentityResult,
    FixtureMachine,
    RealMachine,
)
from tests.pbp_writer import BLOCK, DEFAULT_SFO, Disc, Pbp, bcd, deflate, msf, sfo, toc_entry
from tests.test_psx_disc import SYSTEM_CNF, US, disc

JP = b"          Licensed  by          Sony Computer Entertainment Inc."
TRACK = disc()
BIN_IDENTITY = psx_disc.identity_of(lambda lba: TRACK[lba * 2352 : (lba + 1) * 2352] or None)
OPENS = psx_pbp.PbpFacts(duckstation_refuses=False, encrypted=False)
DS_REFUSES = psx_pbp.PbpFacts(duckstation_refuses=True, encrypted=False)


def opener_of(image: bytes):
    return lambda _path: io.BytesIO(image)


def facts(pbp: Pbp) -> psx_pbp.PbpFacts:
    return psx_pbp.read_facts("Game.pbp", opener_of(pbp.encode()))


def read(pbp: Pbp | bytes) -> psx_disc.DiscIdentity:
    image = pbp if isinstance(pbp, bytes) else pbp.encode()
    return psx_pbp.read_identity("Game.pbp", opener_of(image))


def with_sfo(entries: dict[bytes, bytes | int], **kwargs) -> Pbp:
    return Pbp([Disc(TRACK)], param_sfo=sfo(entries, **kwargs))


class TestAPbpIsRead:
    def test_deflated_blocks_yield_the_bins_identity(self):
        assert read(Pbp([Disc(TRACK)])) == BIN_IDENTITY
        assert facts(Pbp([Disc(TRACK)])) == OPENS

    def test_a_block_whose_size_is_a_whole_block_is_stored(self):
        assert read(Pbp([Disc(TRACK, stored=True)])) == BIN_IDENTITY

    def test_the_first_disc_of_several_decides(self):
        other = disc(license=JP)
        assert read(Pbp([Disc(TRACK), Disc(other)], title=True)) == BIN_IDENTITY
        assert read(Pbp([Disc(other), Disc(TRACK)], title=True)).license.startswith(JP)


class TestDuckStationRefusesOverTheSfo:
    """cd_image_pbp.cpp:246-402: the refusals before any disc, which Beetle never makes."""

    @pytest.mark.parametrize(
        "entries",
        [
            {b"CATEGORY": b"ME"},
            {b"BOOTABLE": 0, b"CATEGORY": b"ME"},
            {b"BOOTABLE": b"1", b"CATEGORY": b"ME"},
            {b"BOOTABLE": 1},
            {b"BOOTABLE": 1, b"CATEGORY": b"MG"},
            {b"BOOTABLE": 1, b"CATEGORY": 1},
        ],
        ids=["no-bootable", "bootable-0", "bootable-a-string", "no-category", "category-mg", "category-a-number"],
    )
    def test_bootable_1_and_category_me_are_required(self, entries):
        pbp = with_sfo(entries)
        assert facts(pbp) == DS_REFUSES
        # Beetle reads no PARAM.SFO, so its read is the identity.
        assert read(pbp) == BIN_IDENTITY

    def test_a_value_of_a_type_the_reader_does_not_handle(self):
        assert facts(with_sfo(DEFAULT_SFO, types={b"TITLE": 0x0004})) == DS_REFUSES
        assert facts(with_sfo(DEFAULT_SFO, types={b"TITLE": 0x0104})) == DS_REFUSES

    def test_a_string_stated_as_no_bytes_is_refused(self):
        # fgets with a count of 0 returns nothing (cd_image_pbp.cpp:325-333).
        assert facts(with_sfo(DEFAULT_SFO, sizes={b"TITLE": 0})) == DS_REFUSES

    def test_a_string_read_through_fgets_stops_at_its_stated_size(self):
        # A count of 3 reads two bytes, "ME" — CATEGORY stated one byte short of its NUL still matches.
        assert facts(with_sfo(DEFAULT_SFO, sizes={b"CATEGORY": 3})) == OPENS
        assert facts(with_sfo(DEFAULT_SFO, sizes={b"CATEGORY": 2})) == DS_REFUSES

    def test_the_first_entry_of_a_key_wins(self):
        # A second BOOTABLE entry after the first: std::map::emplace keeps the first (cd_image_pbp.cpp:334, :345).
        later = sfo({b"BOOTABLE": 1, b"CATEGORY": b"ME", b"BOOTABLF": 0}).replace(b"BOOTABLF", b"BOOTABLE")
        assert facts(Pbp([Disc(TRACK)], param_sfo=later)) == OPENS
        earlier = sfo({b"BOOTABLF": 0, b"BOOTABLE": 1, b"CATEGORY": b"ME"}).replace(b"BOOTABLF", b"BOOTABLE")
        assert facts(Pbp([Disc(TRACK)], param_sfo=earlier)) == DS_REFUSES

    def test_only_the_two_values_judged_are_kept(self):
        image = sfo({b"TITLE": b"Game", b"BOOTABLE": 1, b"DISC_ID": b"SLUS00001", b"CATEGORY": b"ME"})
        table = psx_pbp._sfo_table(  # pyright: ignore[reportPrivateUsage] - what the walk keeps
            psx_pbp._File(io.BytesIO(image)), 0  # pyright: ignore[reportPrivateUsage]
        )
        assert table == {b"BOOTABLE": 1, b"CATEGORY": b"ME"}

    def test_the_sfo_magic_is_tested_on_its_first_byte_only(self):
        # strncmp against "\0PSF" stops at the literal's NUL (cd_image_pbp.cpp:254).
        quirky = b"\x00XYZ" + sfo(DEFAULT_SFO)[4:]
        assert facts(Pbp([Disc(TRACK)], param_sfo=quirky)) == OPENS
        assert facts(Pbp([Disc(TRACK)], param_sfo=b"\x01PSF" + sfo(DEFAULT_SFO)[4:])) == DS_REFUSES

    def test_an_sfo_index_past_the_end_of_the_file(self):
        header = bytearray(sfo(DEFAULT_SFO))
        header[16:20] = struct.pack("<I", 0x00FF_FFFF)
        pbp = Pbp([Disc(TRACK)], param_sfo=bytes(header))
        with pytest.raises(psx_disc.Unreadable, match="PARAM.SFO of"):
            read(pbp)


class TestEncryption:
    def test_an_encrypted_image_refuses_duckstation_and_is_decrypted_by_beetle(self):
        pbp = Pbp([Disc(TRACK, encrypted=True)])
        assert facts(pbp) == psx_pbp.PbpFacts(duckstation_refuses=True, encrypted=True)
        with pytest.raises(psx_disc.FormatNotRead, match="no reader"):
            read(pbp)

    def test_an_encrypted_disc_table(self):
        pbp = Pbp([Disc(TRACK)], title=True, title_encrypted=True)
        assert facts(pbp) == psx_pbp.PbpFacts(duckstation_refuses=True, encrypted=True)

    def test_beetle_alone_reaches_the_disc_of_a_pbp_duckstation_refuses(self):
        pbp = with_sfo({b"BOOTABLE": 1, b"CATEGORY": b"MG"})
        assert read(pbp) == BIN_IDENTITY


class TestTheReadersReadApart:
    def test_a_header_magic_only_duckstation_takes(self):
        # DuckStation tests the first byte (cd_image_pbp.cpp:233); Beetle all four (Beetle :118-122).
        subject = Pbp([Disc(TRACK)], magic=b"\x00XYZ")
        with pytest.raises(psx_disc.FormatNotRead, match="finds none"):
            read(subject)

    def test_a_title_magic_of_twelve_bytes(self):
        # Beetle compares 12 bytes and reads the disc table; DuckStation's 16 differ and it finds no ISO header.
        subject = Pbp([Disc(TRACK)], title=True, title_magic=b"PSTITLEIMG00XXXX")
        with pytest.raises(psx_disc.FormatNotRead, match="finds none"):
            read(subject)

    def test_an_image_magic_only_beetle_ignores(self):
        pbp = Pbp([Disc(TRACK, magic=b"PSISOIMGXXXX")])
        with pytest.raises(psx_disc.FormatNotRead, match="finds none"):
            read(pbp)
        # Where DuckStation refuses over the SFO anyway, Beetle's read is the identity.
        alone = Pbp([Disc(TRACK, magic=b"PSISOIMGXXXX")], param_sfo=sfo({b"BOOTABLE": 0, b"CATEGORY": b"ME"}))
        assert read(alone) == BIN_IDENTITY

    def test_toc_points_duckstation_checks(self):
        toc = Disc(TRACK).default_toc()
        toc[0] = toc_entry(point=0xA3, userdata=bytes((bcd(1), 0, 0)))
        subject = Pbp([Disc(TRACK, toc=toc)])
        with pytest.raises(psx_disc.FormatNotRead, match="finds none"):
            read(subject)

    def test_a_track_1_pregap_kept_in_the_image(self):
        # DuckStation starts track 1 at its pregap_start (cd_image_pbp.cpp:687-689); Beetle at sector 0.
        shifted = bytes(2352 * 2) + TRACK
        toc = Disc(shifted).default_toc()
        toc[3] = toc_entry(point=0x01, pregap=msf(2), userdata=msf(152))
        subject = Pbp([Disc(shifted, toc=toc)])
        with pytest.raises(psx_disc.FormatNotRead, match="apart"):
            read(subject)

    def test_a_block_base_beetle_does_not_take(self):
        # Beetle reads the blocks from +0x100000 whatever +0xBFC states (Beetle :470).
        subject = Pbp([Disc(TRACK, base=0x110000)])
        with pytest.raises(psx_disc.Unreadable):
            read(subject)


class TestWhatOpensAsNoDisc:
    def test_an_audio_track_1(self):
        toc = Disc(TRACK).default_toc()
        toc[3] = toc_entry(kind=0x01, point=0x01, pregap=msf(0), userdata=msf(150))
        subject = Pbp([Disc(TRACK, toc=toc)])
        with pytest.raises(psx_disc.NotADisc, match="audio"):
            read(subject)

    def test_a_multi_disc_table_naming_no_disc(self):
        pbp = Pbp([Disc(TRACK)], title=True)
        image = bytearray(pbp.encode())
        psar = struct.unpack_from("<I", image, 8 + 4 * 7)[0]
        image[psar + 0x200 : psar + 0x204] = bytes(4)
        # DuckStation refuses it (cd_image_pbp.cpp:493-497); Beetle reads its TOC from the title header, which
        # names no track (Beetle :170-174, :506-511).
        subject = bytes(image)
        with pytest.raises(psx_disc.NotADisc, match="names no disc"):
            read(subject)

    def test_a_file_cut_short_before_its_block_table(self):
        image = Pbp([Disc(TRACK)]).encode()
        subject = image[:0x9000]
        with pytest.raises(psx_disc.Unreadable):
            read(subject)


class TestWhatDoesNotComeBack:
    def test_the_last_block_without_a_closing_entry(self):
        # Beetle sizes a block by the next entry's offset (Beetle :617-630); with none it reads memory it never set.
        subject = Pbp([Disc(TRACK, closing=False)])
        with pytest.raises(psx_disc.Unreadable, match="Beetle refuses"):
            read(subject)

    def test_a_block_that_does_not_inflate(self):
        subject = Pbp([Disc(TRACK, blocks={0: b"\xff" * 100})])
        with pytest.raises(psx_disc.Unreadable, match="does not decode"):
            read(subject)

    def test_a_stream_that_runs_past_a_block(self):
        # inflate(Z_FINISH) into one block's room must reach the stream's end (cd_image_pbp.cpp:810-815; Beetle :354-362).
        longer = deflate((TRACK + bytes(BLOCK))[:BLOCK] + bytes(100))
        subject = Pbp([Disc(TRACK, blocks={0: longer})])
        with pytest.raises(psx_disc.Unreadable, match="does not end within one block"):
            read(subject)

    def test_duckstation_reads_a_short_stream_only_as_far_as_it_reaches(self):
        # A stream ending short leaves DuckStation's buffer holding the block before past its end (:785-818).
        half = deflate(TRACK[: BLOCK // 2])
        image = Pbp([Disc(TRACK, blocks={0: half})]).encode()
        sectors = psx_pbp._duckstation_sectors(  # pyright: ignore[reportPrivateUsage] - the one reader alone
            psx_pbp._File(io.BytesIO(image)), psx_pbp._Work()  # pyright: ignore[reportPrivateUsage]
        )
        assert sectors(4) == TRACK[4 * 2352 : 5 * 2352]
        with pytest.raises(psx_disc.Unreadable, match="buffer kept"):
            sectors(8)

    def test_a_stream_that_ends_short_of_a_block(self):
        # DuckStation keeps a short stream and reads its buffer's leftovers past it; Beetle demands a whole block.
        subject = Pbp([Disc(TRACK, blocks={0: deflate(TRACK[:BLOCK // 2])})])
        with pytest.raises(psx_disc.Unreadable):
            read(subject)

    def test_the_blocks_one_read_decodes_are_bounded(self, monkeypatch):
        monkeypatch.setattr(psx_pbp, "_MAX_DECODES", 2)
        subject = Pbp([Disc(TRACK)])
        with pytest.raises(psx_disc.Unreadable, match="decoding more than"):
            read(subject)

    def test_what_the_sfo_asks_to_be_read_is_bounded_in_total(self):
        subject = with_sfo(DEFAULT_SFO, sizes={b"TITLE": 1 << 20})
        with pytest.raises(psx_disc.Unreadable, match="asking more than"):
            read(subject)
        # Many keys each asking a little add up the same way.
        many = {**DEFAULT_SFO, **{f"K{number:04}".encode(): b"x" for number in range(2048)}}
        subject = with_sfo(many, sizes={f"K{number:04}".encode(): 1 << 10 for number in range(2048)})
        with pytest.raises(psx_disc.Unreadable, match="asking more than"):
            read(subject)

    def test_zlib_missing(self, monkeypatch):
        monkeypatch.setattr(psx_pbp, "zlib", None)
        subject = Pbp([Disc(TRACK)])
        with pytest.raises(psx_disc.CodecUnavailable):
            read(subject)
        # A stored image needs no codec.
        assert read(Pbp([Disc(TRACK, stored=True)])) == BIN_IDENTITY


class TestDamagedImages:
    def test_no_mutation_ends_in_anything_but_the_documented_answers(self):
        rng = random.Random(691)
        sources = [Pbp([Disc(TRACK)]).encode(), Pbp([Disc(TRACK), Disc(TRACK)], title=True).encode()]
        expected = (psx_disc.NotADisc, psx_disc.FormatNotRead, psx_disc.Unreadable)
        for round_number in range(300):
            image = bytearray(sources[round_number % len(sources)])
            for _ in range(rng.randint(1, 3)):
                low, high = rng.choice([(0, 0x200), (0x400, 0x4400), (0x4400, len(image))])
                image[rng.randrange(low, min(high, len(image)))] = rng.randrange(256)
            pbp = bytes(image)
            psx_pbp.read_facts("Game.pbp", opener_of(pbp))
            try:
                read(pbp)
            except expected:
                pass


def write(tmp_path: Path, pbp: Pbp, name: str = "Game.pbp") -> str:
    path = tmp_path / name
    path.write_bytes(pbp.encode())
    return str(path)


class TestTheSeam:
    """RealMachine.read_disc_identity over a PBP — both opens' facts beside every outcome."""

    def test_a_pbp_is_ok_with_its_facts(self, tmp_path):
        result = RealMachine().read_disc_identity(write(tmp_path, Pbp([Disc(TRACK)])))
        assert result == DiscIdentityResult(DISC_OK, BIN_IDENTITY, pbp=OPENS)

    def test_each_outcome_keeps_its_word(self, tmp_path, monkeypatch):
        encrypted = psx_pbp.PbpFacts(duckstation_refuses=True, encrypted=True)
        machine = RealMachine()
        assert machine.read_disc_identity(write(tmp_path, Pbp([Disc(TRACK, encrypted=True)]), "E.pbp")) == (
            DiscIdentityResult(DISC_FORMAT_NOT_READ, pbp=encrypted)
        )
        toc = Disc(TRACK).default_toc()
        toc[3] = toc_entry(kind=0x01, point=0x01, pregap=msf(0), userdata=msf(150))
        assert machine.read_disc_identity(write(tmp_path, Pbp([Disc(TRACK, toc=toc)]), "A.pbp")) == (
            DiscIdentityResult(DISC_NOT_A_DISC, pbp=OPENS)
        )
        assert machine.read_disc_identity(write(tmp_path, Pbp([Disc(TRACK, closing=False)]), "U.pbp")) == (
            DiscIdentityResult(DISC_UNREADABLE, pbp=OPENS)
        )
        monkeypatch.setattr(psx_pbp, "zlib", None)
        assert machine.read_disc_identity(write(tmp_path, Pbp([Disc(TRACK)]), "C.pbp")) == (
            DiscIdentityResult(DISC_CODEC_UNAVAILABLE, pbp=OPENS)
        )


GAME = "/roms/psx/Game.pbp"
FACTS = {"license": US.decode("latin-1"), "system_cnf": SYSTEM_CNF.decode("latin-1"), "boot_found": True, "edc_valid": True}


class TestTheFixtureModelsTheAnswer:
    """FixtureMachine.read_disc_identity over a PBP — both opens' facts and the read's outcome, as declared."""

    def _machine(self, spec) -> FixtureMachine:
        return FixtureMachine({GAME: "x"}, disc_identities={GAME: spec})

    def test_the_facts_and_the_identity(self):
        result = self._machine({"duckstation_refuses": True, "encrypted": False, "identity": FACTS}).read_disc_identity(
            GAME
        )
        assert result.status == DISC_OK
        assert result.pbp == DS_REFUSES

    def test_a_read_state_beside_the_facts(self):
        spec = {"duckstation_refuses": False, "encrypted": True, "identity": "format-not-read"}
        assert self._machine(spec).read_disc_identity(GAME) == DiscIdentityResult(
            DISC_FORMAT_NOT_READ, pbp=psx_pbp.PbpFacts(False, True)
        )

    def test_unreadable_alone(self):
        assert self._machine("unreadable").read_disc_identity(GAME) == DiscIdentityResult(DISC_UNREADABLE)

    @pytest.mark.parametrize(
        ("spec", "message"),
        [
            ("missing", "a state must be one of"),
            ({"duckstation_refuses": False, "identity": FACTS}, "exactly"),
            ({"duckstation_refuses": "no", "encrypted": False, "identity": FACTS}, "booleans"),
            ({"duckstation_refuses": False, "encrypted": False, "identity": "parent-not-found"}, "a state must be one of"),
        ],
        ids=["missing", "fields", "not-boolean", "read-state"],
    )
    def test_a_declaration_no_real_read_could_make_is_refused(self, spec, message):
        with pytest.raises(ValueError, match=message):
            self._machine(spec)
