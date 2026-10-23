"""Tests for atlas.psx_chd — the CHD read against images written in the test.

Every image is built by tests/chd_writer.py from the same hand-made track 1 the
bin tests read (tests/test_psx_disc.py), so the one thing proven over and over
is that a CHD yields exactly the identity its track yields as a bin: through
each CD codec, through stored, copied and parent hunks, and with the sync and
parity the codec strips rebuilt before the hunk's CRC is checked. The refusals
are proven one guard at a time, and a seeded mutation run proves that no
damaged image ends in anything but the reader's documented answers.
"""

from __future__ import annotations

import hashlib
import inspect
import os
import random
import struct
import sys
from pathlib import Path

import pytest

from atlas import psx_chd, psx_disc, squashfs
from atlas.machine import (
    DISC_CODEC_UNAVAILABLE,
    DISC_FORMAT_NOT_READ,
    DISC_NOT_A_DISC,
    DISC_OK,
    DISC_PARENT_NOT_FOUND,
    DISC_UNREADABLE,
    DiscIdentityResult,
    FixtureMachine,
    RealMachine,
)
from tests import chd_writer
from tests.chd_writer import Chd, Hunk
from tests.test_psx_disc import SYSTEM_CNF, US, disc, stated_longer

JP = b"          Licensed  by          Sony Computer Entertainment Inc."

TRACK = chd_writer.with_parity(disc())
BIN_IDENTITY = psx_disc.identity_of(lambda lba: TRACK[lba * 2352 : (lba + 1) * 2352] or None)
CHT2 = "TRACK:1 TYPE:{mode} SUBTYPE:{subtype} FRAMES:24 PREGAP:{pregap} PGTYPE:{pgtype} PGSUB:RW POSTGAP:0"


def opener(path: str):
    return open(path, "rb")


def lister(directory: str) -> list[str]:
    with os.scandir(directory) as entries:
        return sorted(entry.path for entry in entries if entry.is_file())


def write(tmp_path: Path, image: bytes, name: str = "Game.chd") -> str:
    path = tmp_path / name
    path.write_bytes(image)
    return str(path)


def read(path: str, files=lister) -> psx_disc.DiscIdentity:
    return psx_chd.read_identity(path, opener, files)


@pytest.fixture
def zstd_provider():
    """A zstd provider for the cdzs tests: the runtime's own where it has one, else a raw-block decoder."""
    if squashfs.zstd_codec() is not None:
        yield
        return
    squashfs.register_zstd_provider(type("RawBlocks", (), {"decompress": staticmethod(chd_writer.unzstd_raw_frames)}))
    yield
    squashfs.register_zstd_provider(None)


class TestTheFixtureTrack:
    def test_the_track_is_a_disc_the_bin_reader_reads(self):
        assert BIN_IDENTITY.license.startswith(US)
        assert BIN_IDENTITY.system_cnf == SYSTEM_CNF
        assert BIN_IDENTITY.edc_valid
        assert BIN_IDENTITY.boot_found


class TestEachCodec:
    @pytest.mark.parametrize("codec", ["cdlz", "cdzl"])
    def test_a_cd_codec_yields_the_bin_s_identity(self, tmp_path, codec):
        path = write(tmp_path, Chd(TRACK, default=codec).build())
        assert read(path) == BIN_IDENTITY

    def test_cdzs_through_the_zstd_provider(self, tmp_path, zstd_provider):
        path = write(tmp_path, Chd(TRACK, default="cdzs").build())
        assert read(path) == BIN_IDENTITY

    def test_stored_hunks_and_an_uncompressed_image(self, tmp_path):
        stored = write(tmp_path, Chd(TRACK, default="none").build(), "Stored.chd")
        assert read(stored) == BIN_IDENTITY
        plain = write(tmp_path, Chd(TRACK, codecs=()).build_uncompressed(), "Plain.chd")
        assert read(plain) == BIN_IDENTITY

    def test_a_hunk_copied_from_another_of_the_same_file(self, tmp_path):
        # Hunks 0 and 1 hold the same bytes once sector 4 is blanked out of both.
        track = bytearray(TRACK)
        track[8 * 2352 : 16 * 2352] = track[0 : 8 * 2352]
        image = Chd(bytes(track), hunks={1: Hunk("self", offset=0)}).build()
        assert read(write(tmp_path, image)) == psx_disc.identity_of(
            lambda lba: bytes(track[lba * 2352 : (lba + 1) * 2352]) or None
        )

    def test_frames_whose_parity_was_stripped_are_rebuilt_before_the_crc(self, tmp_path):
        image = Chd(TRACK).build()
        unstripped = Chd(TRACK, strip=False).build()
        assert image != unstripped
        assert read(write(tmp_path, image)) == read(write(tmp_path, unstripped, "Whole.chd")) == BIN_IDENTITY

    def test_the_rebuilt_parity_is_the_one_mame_accepts(self):
        # Sector 4's P and Q parity as chdman (MAME 0.289) verifies it before
        # stripping a frame: rebuilt from a sector with the parity zeroed.
        sector = bytearray(TRACK[4 * 2352 : 5 * 2352])
        expected = bytes(sector[0x81C:])
        sector[0x81C:] = bytes(len(expected))
        psx_chd._ecc_generate(sector)  # pyright: ignore[reportPrivateUsage] - the rebuild under test
        assert bytes(sector[0x81C:]) == expected
        assert hashlib.sha1(expected).hexdigest() == "7ac03d61b8ed7c6dafc2344288dd21c842b92d26"


class TestTheFacts:
    def test_the_header_s_codecs_and_parent(self, tmp_path):
        path = write(tmp_path, Chd(TRACK, codecs=("cdlz", "cdzs"), parent_sha1=b"\x22" * 20).build())
        assert psx_chd.read_facts(path, opener) == psx_chd.ChdFacts(("cdlz", "cdzs"), True)

    def test_an_older_version_is_not_read(self, tmp_path):
        image = bytearray(Chd(TRACK).build())
        image[8:16] = struct.pack(">II", 108, 4)
        path = write(tmp_path, bytes(image))
        with pytest.raises(psx_disc.FormatNotRead, match="version 4"):
            psx_chd.read_facts(path, opener)
        with pytest.raises(psx_disc.FormatNotRead, match="version 4"):
            read(path)


def parent_pair(tmp_path: Path, *, child_name: str = "Game.chd", parent_sha1: bytes = b"\x33" * 20) -> str:
    """A parent holding the track, and a child whose every hunk but the first is the parent's."""
    write(tmp_path, Chd(TRACK, sha1=b"\x33" * 20).build(), "Parent.chd")
    hunks = {number: Hunk("parent", offset=number * 8) for number in (1, 2)}
    return write(tmp_path, Chd(TRACK, parent_sha1=parent_sha1, hunks=hunks).build(), child_name)


class TestTheParent:
    def test_a_child_reads_through_the_parent_in_its_folder(self, tmp_path):
        assert read(parent_pair(tmp_path)) == BIN_IDENTITY

    def test_a_unit_offset_inside_a_parent_hunk_spans_two_of_them(self, tmp_path):
        track = bytearray(TRACK)
        shifted = bytes(track[2 * 2352 :]) + bytes(2 * 2352)
        write(tmp_path, Chd(shifted, sha1=b"\x33" * 20).build(), "Parent.chd")
        # Hunk 2 holds the PVD, the root and SYSTEM.CNF: units 14-21 of the
        # shifted parent, the end of its hunk 1 and the start of its hunk 2.
        child = Chd(TRACK, parent_sha1=b"\x33" * 20, hunks={2: Hunk("parent", offset=14)})
        assert read(write(tmp_path, child.build())) == BIN_IDENTITY

    def test_no_parent_in_the_folder(self, tmp_path):
        path = parent_pair(tmp_path)
        os.remove(tmp_path / "Parent.chd")
        with pytest.raises(psx_chd.ParentNotFound):
            read(path)

    def test_a_parent_with_another_hash(self, tmp_path):
        path = parent_pair(tmp_path, parent_sha1=b"\x44" * 20)
        with pytest.raises(psx_chd.ParentNotFound):
            read(path)

    def test_a_parent_in_another_folder_is_not_looked_for(self, tmp_path):
        path = parent_pair(tmp_path)
        (tmp_path / "sub").mkdir()
        os.rename(tmp_path / "Parent.chd", tmp_path / "sub" / "Parent.chd")
        with pytest.raises(psx_chd.ParentNotFound):
            read(path)

    def test_a_file_without_a_dot_in_its_name_is_not_looked_at(self, tmp_path):
        path = parent_pair(tmp_path)
        os.rename(tmp_path / "Parent.chd", tmp_path / "Parent")
        with pytest.raises(psx_chd.ParentNotFound):
            read(path)

    def test_any_extension_is_tried(self, tmp_path):
        path = parent_pair(tmp_path)
        os.rename(tmp_path / "Parent.chd", tmp_path / "Parent.bak")
        assert read(path) == BIN_IDENTITY

    def test_a_null_hash_matches_any_child(self, tmp_path):
        path = parent_pair(tmp_path)
        write(tmp_path, Chd(TRACK, sha1=bytes(20)).build(), "Parent.chd")
        assert read(path) == BIN_IDENTITY

    def test_the_first_matching_file_that_opens_in_directory_order(self, tmp_path):
        path = parent_pair(tmp_path)
        broken = bytearray(Chd(TRACK, sha1=b"\x33" * 20).build())
        broken[-1] ^= 0xFF  # the map's last byte: its CRC fails, so it does not open
        write(tmp_path, bytes(broken), "A-broken.chd")
        older = bytearray(Chd(TRACK).build())
        older[8:16] = struct.pack(">II", 76, 1)  # version 1 keeps no SHA-1, so it matches any child
        write(tmp_path, bytes(older), "Z-older.chd")
        listing = [str(tmp_path / name) for name in ("A-broken.chd", "Game.chd", "Parent.chd", "Z-older.chd")]
        assert read(path, lambda _directory: listing) == BIN_IDENTITY
        with pytest.raises(psx_disc.FormatNotRead, match="version 1"):
            read(path, lambda _directory: list(reversed(listing)))

    def test_a_chain_of_parents(self, tmp_path):
        write(tmp_path, Chd(TRACK, sha1=b"\x55" * 20).build(), "Base.chd")
        middle = Chd(TRACK, sha1=b"\x33" * 20, parent_sha1=b"\x55" * 20, hunks={2: Hunk("parent", offset=16)})
        write(tmp_path, middle.build(), "Parent.chd")
        child = Chd(TRACK, parent_sha1=b"\x33" * 20, hunks={2: Hunk("parent", offset=16)})
        assert read(write(tmp_path, child.build())) == BIN_IDENTITY

    @pytest.mark.parametrize(("links", "found"), [(32, True), (33, False)])
    def test_duckstation_s_depth_limit(self, tmp_path, links, found):
        # The child is level 0 and each parent one more; the file at level 32
        # may not need a parent (cd_image_chd.cpp:124-129).
        for level in range(1, links + 1):
            parent = bytes(20) if level == links else bytes((level + 1,)) * 20
            write(tmp_path, Chd(TRACK, sha1=bytes((level,)) * 20, parent_sha1=parent).build(), f"L{level}.chd")
        path = write(tmp_path, Chd(TRACK, sha1=b"\xee" * 20, parent_sha1=bytes((1,)) * 20).build())
        if found:
            assert read(path) == BIN_IDENTITY
        else:
            with pytest.raises(psx_chd.ParentNotFound):
                read(path)


def same_hunks(track: bytes, hunks: int, copies: range) -> bytes:
    """*track* padded to *hunks* hunks, each hunk in *copies* holding hunk 2's sectors."""
    padded = bytearray(track.ljust(hunks * 8 * 2352, b"\x00"))
    for number in copies:
        padded[number * 8 * 2352 : (number + 1) * 8 * 2352] = padded[2 * 8 * 2352 : 3 * 8 * 2352]
    return bytes(padded)


class TestWhatAChainCosts:
    """One read's work across every parent: bounded, iterative, and done once per file and hunk."""

    def _decodes(self, monkeypatch) -> list[int]:
        calls: list[int] = []
        original = psx_chd._cd_hunk  # pyright: ignore[reportPrivateUsage] - counted, not replaced
        monkeypatch.setattr(psx_chd, "_cd_hunk", lambda *args: calls.append(1) or original(*args))
        return calls

    def test_hunks_two_straddling_reads_share_are_decoded_once(self, tmp_path, monkeypatch):
        # Eight files, each hunk but the first taking units that straddle two
        # of its parent's hunks: read naively, hunk 2 of the child decodes 2^8
        # hunks of the last file; kept per file, each hunk once.
        track = TRACK.ljust(12 * 8 * 2352, b"\x00")
        straddle = {number: Hunk("parent", offset=number * 8 + 1) for number in range(1, 11)}
        write(tmp_path, Chd(track, sha1=b"\x08" * 20).build(), "L8.chd")
        for level in range(7, 0, -1):
            chd = Chd(track, sha1=bytes((level,)) * 20, parent_sha1=bytes((level + 1,)) * 20, hunks=straddle)
            write(tmp_path, chd.build(), f"L{level}.chd")
        path = write(tmp_path, Chd(track, parent_sha1=b"\x01" * 20, hunks=straddle).build())
        decodes = self._decodes(monkeypatch)
        with pytest.raises(psx_disc.FormatNotRead):  # the shifted sectors hold no volume descriptor
            read(path)
        assert len(decodes) < 80

    def test_one_budget_bounds_the_whole_read(self, tmp_path, monkeypatch):
        path = parent_pair(tmp_path)
        assert read(path) == BIN_IDENTITY
        for what, limit in (("bytes of hunks held", 19584), ("map entries", 4), ("candidate files opened", 0)):
            monkeypatch.setitem(psx_chd._BUDGET, what, limit)  # pyright: ignore[reportPrivateUsage] - the budget under test
            with pytest.raises(psx_disc.Unreadable, match=what):
                read(path)
            monkeypatch.undo()

    def _held(self, monkeypatch) -> list[int]:
        held: list[int] = []
        original = psx_chd._Work.spend  # pyright: ignore[reportPrivateUsage] - counted, not replaced

        def counted(work, what, amount=1):
            if what == "bytes of hunks held":
                held.append(amount)
            original(work, what, amount)

        monkeypatch.setattr(psx_chd._Work, "spend", counted)  # pyright: ignore[reportPrivateUsage]
        return held

    def test_the_hunk_a_straddle_joins_is_charged(self, tmp_path, monkeypatch):
        # Hunk 2 of the child joins the end of the parent's hunk 1 to the start
        # of its hunk 2: the child's hunk 0, the parent's two hunks and the
        # joined one are all held.
        write(tmp_path, Chd(TRACK[2 * 2352 :] + bytes(2 * 2352), sha1=b"\x33" * 20).build(), "Parent.chd")
        child = Chd(TRACK, parent_sha1=b"\x33" * 20, hunks={2: Hunk("parent", offset=14)})
        image_path = write(tmp_path, child.build())
        held = self._held(monkeypatch)
        assert read(image_path) == BIN_IDENTITY
        assert held == [19584] * 4

    def test_the_zeros_an_uncompressed_file_stands_for_are_charged(self, tmp_path, monkeypatch):
        # An uncompressed file's map entry of 0 is a hunk of zeros where there
        # is no parent: held like any other.
        image = bytearray(Chd(TRACK, codecs=()).build_uncompressed())
        map_offset = struct.unpack(">Q", image[40:48])[0]
        image[map_offset + 8 : map_offset + 12] = bytes(4)
        image_path = write(tmp_path, bytes(image))
        held = self._held(monkeypatch)
        with pytest.raises(psx_disc.FormatNotRead):  # hunk 2, the PVD's, is zeros
            read(image_path)
        assert held == [19584, 19584]

    def _copy_chain(self, tmp_path: Path, levels: int, copies: int = 3) -> str:
        """*levels* parents below the child; in each, hunk 2 copies hunk 3, and so on *copies* times, and the last
        is the parent's hunk 2 — one step more a level than *copies*, the last file holding them all."""
        last = 2 + copies
        track = same_hunks(TRACK, last + 1, range(3, last + 1))
        hops = {number: Hunk("self", offset=number + 1) for number in range(2, last)}
        hops[last] = Hunk("parent", offset=16)
        write(tmp_path, Chd(track, sha1=bytes((levels,)) * 20).build(), f"L{levels}.chd")
        for level in range(levels - 1, 0, -1):
            chd = Chd(track, sha1=bytes((level,)) * 20, parent_sha1=bytes((level + 1,)) * 20, hunks=hops)
            write(tmp_path, chd.build(), f"L{level}.chd")
        return write(tmp_path, Chd(track, parent_sha1=b"\x01" * 20, hunks=hops).build())

    def test_a_long_walk_of_copies_is_a_loop_not_a_recursion(self, tmp_path):
        # Under a recursion limit thirty frames above this test — fewer than
        # the walk's 45 steps through four files — the read still answers.
        path = self._copy_chain(tmp_path, 3, copies=14)
        limit = sys.getrecursionlimit()
        sys.setrecursionlimit(len(inspect.stack()) + 30)
        try:
            assert read(path) == BIN_IDENTITY
        finally:
            sys.setrecursionlimit(limit)

    def test_a_walk_past_the_step_bound_answers(self, tmp_path):
        image_path = self._copy_chain(tmp_path, 20)
        with pytest.raises(psx_disc.Unreadable, match="steps deep"):
            read(image_path)

    def _maps_decoded(self, monkeypatch) -> list[str]:
        decoded: list[str] = []
        original = psx_chd._Image.__init__  # pyright: ignore[reportPrivateUsage] - counted, not replaced

        def counted(image, handle, header, work):
            decoded.append(os.path.basename(handle.name))
            original(image, handle, header, work)

        monkeypatch.setattr(psx_chd._Image, "__init__", counted)  # pyright: ignore[reportPrivateUsage]
        return decoded

    def test_a_file_that_matches_itself_reads_through_the_parent_behind_it(self, tmp_path):
        # X's null hash matches every child, X's own parent included, so
        # DuckStation stacks X on itself to its depth limit before it takes R;
        # the data read is R's either way.
        write(tmp_path, Chd(TRACK, sha1=b"\x66" * 20).build(), "R.chd")
        x = Chd(TRACK, sha1=bytes(20), parent_sha1=b"\x66" * 20, hunks={2: Hunk("parent", offset=16)})
        write(tmp_path, x.build(), "X.chd")
        path = write(tmp_path, Chd(TRACK, parent_sha1=b"\x99" * 20, hunks={2: Hunk("parent", offset=16)}).build())
        listing = [path, str(tmp_path / "X.chd"), str(tmp_path / "R.chd")]
        assert read(path, lambda _directory: listing) == BIN_IDENTITY

    def test_a_file_reached_again_on_another_branch_is_decoded_once(self, tmp_path, monkeypatch):
        # C1 and C2 match any child; C1's map is broken. The search tries C1,
        # reaching C2 and R beneath it, fails on C1's map, and tries C2, reaching
        # C1 and R again: every map is decoded once all the same.
        write(tmp_path, Chd(TRACK, sha1=b"\x66" * 20).build(), "R.chd")
        middle = Chd(TRACK, sha1=bytes(20), parent_sha1=b"\x66" * 20, hunks={2: Hunk("parent", offset=16)})
        broken = bytearray(middle.build())
        broken[-1] ^= 0xFF
        write(tmp_path, bytes(broken), "C1.chd")
        write(tmp_path, middle.build(), "C2.chd")
        path = write(tmp_path, Chd(TRACK, parent_sha1=b"\x99" * 20, hunks={2: Hunk("parent", offset=16)}).build())
        listing = [path] + [str(tmp_path / name) for name in ("C1.chd", "C2.chd", "R.chd")]
        decoded = self._maps_decoded(monkeypatch)
        assert read(path, lambda _directory: listing) == BIN_IDENTITY
        assert sorted(decoded) == ["C1.chd", "C2.chd", "Game.chd", "R.chd"]

    def test_a_ring_of_files_matching_each_other_finds_no_parent(self, tmp_path, monkeypatch):
        # Three files with null hashes, each naming a parent: every one matches
        # every other, and no chain ends. DuckStation recurses to its depth
        # limit and refuses; this ends there too, opening each file once per
        # level — 3 files at levels 1 to 32 — where DuckStation's walk, which
        # keeps no open, would try 3^32 of them.
        for name in ("A", "B", "C"):
            write(tmp_path, Chd(TRACK, sha1=bytes(20), parent_sha1=b"\x77" * 20).build(), f"{name}.chd")
        path = write(tmp_path, Chd(TRACK, parent_sha1=b"\x77" * 20).build())
        opens: list[str] = []
        original = psx_chd._Work.spend  # pyright: ignore[reportPrivateUsage] - counted, not replaced
        monkeypatch.setattr(
            psx_chd._Work, "spend", lambda work, what, amount=1: opens.append(what) or original(work, what, amount)  # pyright: ignore[reportPrivateUsage]
        )
        with pytest.raises(psx_chd.ParentNotFound):
            read(path)
        assert opens.count("candidate files opened") == 96

    def test_a_folder_too_large_to_walk_at_every_level_ends_in_the_budget(self, tmp_path):
        # The same ring in a folder of 65536 files: every level walks them all,
        # and the walk ends at the read's bound on files looked at.
        for name in ("A", "B", "C"):
            write(tmp_path, Chd(TRACK, sha1=bytes(20), parent_sha1=b"\x77" * 20).build(), f"{name}.chd")
        path = write(tmp_path, Chd(TRACK, parent_sha1=b"\x77" * 20).build())
        listing = [str(tmp_path / f"{name}.chd") for name in "ABC"]
        listing += [str(tmp_path / f"gone{index}.x") for index in range((1 << 16) - 3)]
        with pytest.raises(psx_disc.Unreadable, match="looked at"):
            read(path, lambda _directory: listing)

    def test_a_self_matching_file_reads_its_own_hunks_one_level_down(self, tmp_path):
        # DuckStation chains X under itself to its depth limit (:124-129) and
        # takes R only there, so X's hunk 0, a hunk of the parent at unit 8,
        # is X's own stored hunk 1 one level down — a Japanese licence — and
        # never R's, which holds the North American one.
        japan = chd_writer.with_parity(disc(license=JP))
        x_track = bytes(8 * 2352) + japan[: 8 * 2352] + TRACK[16 * 2352 :]
        r_track = bytes(8 * 2352) + TRACK[: 8 * 2352] + TRACK[16 * 2352 :]
        write(tmp_path, Chd(r_track, sha1=b"\x66" * 20).build(), "R.chd")
        x = Chd(x_track, sha1=bytes(20), parent_sha1=b"\x66" * 20, hunks={0: Hunk("parent", offset=8)})
        write(tmp_path, x.build(), "X.chd")
        path = write(tmp_path, Chd(TRACK, parent_sha1=b"\x99" * 20, hunks={0: Hunk("parent", offset=0)}).build())
        listing = [path, str(tmp_path / "X.chd"), str(tmp_path / "R.chd")]
        assert read(path, lambda _directory: listing).license.startswith(JP)


class TestWhatIsNotRead:
    def test_a_hunk_in_cdfl(self, tmp_path):
        image = Chd(TRACK, codecs=("cdlz", "cdfl"), hunks={0: Hunk("cdfl", payload=b"\x00" * 64)}).build()
        image_path = write(tmp_path, image)
        with pytest.raises(psx_disc.FormatNotRead, match="cdfl"):
            read(image_path)

    @pytest.mark.parametrize(
        ("mode", "subtype", "pregap", "pgtype", "error"),
        [
            ("AUDIO", "NONE", 0, "AUDIO", psx_disc.NotADisc),
            ("MODE1_RAW", "NONE", 0, "MODE1_RAW", psx_disc.FormatNotRead),
            ("MODE2_RAW", "RW", 0, "MODE2_RAW", psx_disc.FormatNotRead),
            ("MODE2_RAW", "NONE", 2, "VMODE2_RAW", psx_disc.FormatNotRead),
        ],
        ids=["audio", "mode1", "subcode", "pregap-in-file"],
    )
    def test_a_track_1_the_emulators_read_apart(self, tmp_path, mode, subtype, pregap, pgtype, error):
        text = CHT2.format(mode=mode, subtype=subtype, pregap=pregap, pgtype=pgtype)
        image_path = write(tmp_path, Chd(TRACK, metadata=[(b"CHT2", text)]).build())
        with pytest.raises(error):
            read(image_path)

    def test_a_pregap_not_in_the_file_is_read(self, tmp_path):
        text = CHT2.format(mode="MODE2_RAW", subtype="NONE", pregap=150, pgtype="MODE2_RAW")
        assert read(write(tmp_path, Chd(TRACK, metadata=[(b"CHT2", text)]).build())) == BIN_IDENTITY

    def test_the_older_track_metadata_is_read(self, tmp_path):
        text = "TRACK:1 TYPE:MODE2_RAW SUBTYPE:NONE FRAMES:24"
        assert read(write(tmp_path, Chd(TRACK, metadata=[(b"CHTR", text)]).build())) == BIN_IDENTITY

    def test_a_hunk_of_no_whole_frames(self, tmp_path):
        image = Chd(TRACK, hunk_bytes=8 * 2448 + 4, default="none").build()
        image_path = write(tmp_path, image)
        with pytest.raises(psx_disc.FormatNotRead, match="no whole number"):
            read(image_path)


class TestWhatOpensAsNoDisc:
    @pytest.mark.parametrize(
        "damage",
        [
            lambda image: image[:100],
            lambda image: b"MComprHX" + image[8:],
            lambda image: image[:8] + struct.pack(">II", 124, 6) + image[16:],
            lambda image: image[:8] + struct.pack(">II", 120, 5) + image[16:],
            lambda image: image[:56] + struct.pack(">I", 0) + image[60:],
        ],
        ids=["truncated-header", "tag", "version-6", "length", "no-hunk-size"],
    )
    def test_a_header_libchdr_refuses(self, tmp_path, damage):
        path = write(tmp_path, damage(Chd(TRACK).build()))
        with pytest.raises(psx_disc.NotADisc):
            read(path)

    def test_a_map_failing_its_crc(self, tmp_path):
        image = bytearray(Chd(TRACK).build())
        image[-1] ^= 0xFF
        image_path = write(tmp_path, bytes(image))
        with pytest.raises(psx_disc.NotADisc, match="CRC"):
            read(image_path)

    def test_a_map_whose_code_lengths_form_no_code(self, tmp_path):
        # Fifteen 4-bit codes and one unused symbol leave the code space short.
        image = bytearray(Chd(TRACK).build())
        tree = struct.unpack(">Q", image[40:48])[0] + 16
        assert image[tree : tree + 8] == b"\x44" * 8
        image[tree + 7] = 0x40
        image_path = write(tmp_path, bytes(image))
        with pytest.raises(psx_disc.NotADisc, match="canonical"):
            read(image_path)

    def test_a_map_cut_short(self, tmp_path):
        image_path = write(tmp_path, Chd(TRACK).build()[:-3])
        with pytest.raises(psx_disc.NotADisc, match="past the end"):
            read(image_path)

    @pytest.mark.parametrize(
        "metadata",
        [
            [],
            [(b"CHT2", "TRACK:2 TYPE:MODE2_RAW SUBTYPE:NONE FRAMES:24 PREGAP:0 PGTYPE:MODE2_RAW PGSUB:RW POSTGAP:0")],
            [(b"CHT2", "TRACK:1 TYPE:MODE9 SUBTYPE:NONE FRAMES:24 PREGAP:0 PGTYPE:MODE2_RAW PGSUB:RW POSTGAP:0")],
            [(b"CHT2", "TRACK:1 TYPE:MODE2_RAW SUBTYPE:NONE FRAMES:24")],
            [(b"CHT2", "TRACK:1 TYPE:MODE2_RAW SUBTYPE:NONE FRAMES:2 PREGAP:5 PGTYPE:VMODE2_RAW PGSUB:RW POSTGAP:0")],
        ],
        ids=["no-tracks", "numbered-wrong", "unknown-mode", "short-line", "pregap-past-track"],
    )
    def test_track_metadata_duckstation_refuses(self, tmp_path, metadata):
        image_path = write(tmp_path, Chd(TRACK, metadata=metadata).build())
        with pytest.raises(psx_disc.NotADisc):
            read(image_path)


class TestWhatDoesNotComeBack:
    def test_a_hunk_failing_its_crc(self, tmp_path):
        image = bytearray(Chd(TRACK, default="none").build())
        image[image.find(US)] ^= 0xFF  # sector 4's licence, stored as is in hunk 0
        image_path = write(tmp_path, bytes(image))
        with pytest.raises(psx_disc.Unreadable, match="CRC"):
            read(image_path)

    def test_a_hunk_that_does_not_decode(self, tmp_path):
        image = Chd(TRACK, hunks={0: Hunk("cdzl", payload=b"\x00" + b"\x00\x10" + b"\xff" * 40)}).build()
        image_path = write(tmp_path, image)
        with pytest.raises(psx_disc.Unreadable):
            read(image_path)

    def test_a_copy_of_itself(self, tmp_path):
        image = Chd(TRACK, hunks={0: Hunk("self", offset=0)}).build()
        image_path = write(tmp_path, image)
        with pytest.raises(psx_disc.Unreadable, match="deep"):
            read(image_path)

    def test_a_parent_hunk_where_no_parent_is_named(self, tmp_path):
        image = Chd(TRACK, hunks={0: Hunk("parent", offset=0)}).build()
        image_path = write(tmp_path, image)
        with pytest.raises(psx_disc.Unreadable, match="names none"):
            read(image_path)


class TestTheBounds:
    def test_a_hunk_past_the_bound(self, tmp_path):
        image = Chd(TRACK, hunk_bytes=2448 * 512, default="none").build()
        image_path = write(tmp_path, image)
        with pytest.raises(psx_disc.Unreadable, match="bounds"):
            read(image_path)

    def test_a_hunk_count_past_the_bound(self, tmp_path):
        image = bytearray(Chd(TRACK).build())
        image[32:40] = struct.pack(">Q", (1 << 19) * 19584 + 1)
        image_path = write(tmp_path, bytes(image))
        with pytest.raises(psx_disc.Unreadable, match="bounds"):
            read(image_path)

    def test_a_compressed_map_past_the_bound(self, tmp_path):
        image = Chd(TRACK).build()
        map_offset = struct.unpack(">Q", image[40:48])[0]
        damaged = image[:map_offset] + struct.pack(">I", (16 << 20) + 1) + image[map_offset + 4 :]
        image_path = write(tmp_path, damaged)
        with pytest.raises(psx_disc.Unreadable, match="compressed map"):
            read(image_path)

    def test_a_map_field_too_wide(self, tmp_path):
        image_path = write(tmp_path, Chd(TRACK, widths=(32, 24, 24)).build())
        with pytest.raises(psx_disc.Unreadable, match="32 bits"):
            read(image_path)

    def test_a_metadata_chain_that_loops(self, tmp_path):
        image = bytearray(Chd(TRACK).build())
        image[124 + 8 : 124 + 16] = struct.pack(">Q", 124)  # the one entry points back at itself
        image_path = write(tmp_path, bytes(image))
        with pytest.raises(psx_disc.Unreadable, match="metadata chain"):
            read(image_path)

    def test_a_folder_past_the_bound(self, tmp_path):
        path = parent_pair(tmp_path)
        listing = [str(tmp_path / f"f{index}.x") for index in range((1 << 16) + 1)]
        with pytest.raises(psx_disc.Unreadable, match="folder"):
            read(path, lambda _directory: listing)

    def test_a_file_the_disc_states_past_the_bound(self, tmp_path):
        # The bound the bin route holds holds for the same track in a CHD.
        track = chd_writer.with_parity(stated_longer(disc(), file=psx_disc.MAX_FILE_BYTES + 1))
        image_path = write(tmp_path, Chd(track).build())
        with pytest.raises(psx_disc.Unreadable, match="a file of"):
            read(image_path)

    def test_a_zstd_stream_that_would_decode_past_the_bound(self, tmp_path, zstd_provider):
        # One RLE block per four bytes: 65 blocks of 128 KiB, one past 8 MiB, declared in 260.
        frame = struct.pack("<IB", 0xFD2FB528, 0x00) + bytes((0x58,))
        for index in range(65):
            frame += (int(index == 64) | 1 << 1 | (128 * 1024) << 3).to_bytes(3, "little") + b"\x00"
        payload = b"\x00" + len(frame).to_bytes(2, "big") + frame + chd_writer.zstd_raw_frame(bytes(8 * 96))
        image = Chd(TRACK, hunks={0: Hunk("cdzs", payload=payload)}).build()
        image_path = write(tmp_path, image)
        with pytest.raises(psx_disc.Unreadable, match="bound"):
            read(image_path)


class TestACodecTheRuntimeLacks:
    @pytest.mark.parametrize(("module", "codec"), [("lzma", "cdlz"), ("zlib", "cdlz"), ("zlib", "cdzl")])
    def test_a_missing_module(self, tmp_path, monkeypatch, module, codec):
        monkeypatch.setattr(psx_chd, module, None)
        image_path = write(tmp_path, Chd(TRACK, default=codec).build())
        with pytest.raises(psx_disc.CodecUnavailable):
            read(image_path)

    def test_no_zstd_provider(self, tmp_path, monkeypatch):
        monkeypatch.setattr(squashfs, "zstd_codec", lambda: None)
        image_path = write(tmp_path, Chd(TRACK, default="cdzs").build())
        with pytest.raises(psx_disc.CodecUnavailable, match="zstd"):
            read(image_path)


class TestDamagedImages:
    def test_no_mutation_ends_in_anything_but_the_documented_answers(self, tmp_path, zstd_provider):
        # Seeded: every run changes the same bytes, in the header, the metadata
        # and the map, where one byte decides how much is read and from where.
        # A read must come back as an identity or one of the reader's own
        # refusals, never as a crash.
        rng = random.Random(661)
        sources = [Chd(TRACK, default=codec).build() for codec in ("cdlz", "cdzl", "cdzs", "none")]
        sources += [Chd(TRACK, codecs=()).build_uncompressed(), Chd(TRACK, widths=(30, 28, 31)).build()]
        expected = (
            psx_disc.NotADisc,
            psx_disc.FormatNotRead,
            psx_disc.Unreadable,
            psx_disc.CodecUnavailable,
            psx_chd.ParentNotFound,
        )
        path = tmp_path / "Mutated.chd"
        for round_number in range(600):
            image = bytearray(sources[round_number % len(sources)])
            map_offset = struct.unpack(">Q", image[40:48])[0]
            for _ in range(rng.randint(1, 3)):
                low, high = rng.choice([(0, 124), (124, 260), (map_offset, len(image))])
                image[rng.randrange(low, high)] = rng.randrange(256)
            path.write_bytes(bytes(image))
            try:
                read(str(path), lambda _directory: [])
            except expected:
                pass


class TestTheSeam:
    """RealMachine.read_disc_identity over a CHD — the header's facts beside every outcome."""

    def test_a_chd_is_ok_with_its_facts(self, tmp_path):
        path = write(tmp_path, Chd(TRACK, codecs=("cdlz", "cdzl")).build())
        result = RealMachine().read_disc_identity(path)
        assert result == DiscIdentityResult(DISC_OK, BIN_IDENTITY, chd=psx_chd.ChdFacts(("cdlz", "cdzl"), False))

    def test_a_parent_not_found_keeps_the_facts(self, tmp_path):
        path = parent_pair(tmp_path)
        os.remove(tmp_path / "Parent.chd")
        result = RealMachine().read_disc_identity(path)
        assert result == DiscIdentityResult(DISC_PARENT_NOT_FOUND, chd=psx_chd.ChdFacts(("cdlz", "cdzl", "cdzs"), True))

    def test_each_refusal_keeps_its_word(self, tmp_path, monkeypatch):
        facts = psx_chd.ChdFacts(("cdlz", "cdzl", "cdzs"), False)
        stored = {number: Hunk("cdfl", payload=b"\x00" * 8) for number in range(3)}
        cdfl = Chd(TRACK, codecs=("cdfl",), hunks=stored).build()
        assert RealMachine().read_disc_identity(write(tmp_path, cdfl, "F.chd")) == DiscIdentityResult(
            DISC_FORMAT_NOT_READ, chd=psx_chd.ChdFacts(("cdfl",), False)
        )
        broken = bytearray(Chd(TRACK).build())
        broken[-1] ^= 0xFF
        assert RealMachine().read_disc_identity(write(tmp_path, bytes(broken), "M.chd")) == DiscIdentityResult(
            DISC_NOT_A_DISC, chd=facts
        )
        assert RealMachine().read_disc_identity(write(tmp_path, b"MComprHD", "H.chd")) == DiscIdentityResult(
            DISC_NOT_A_DISC
        )
        looping = Chd(TRACK, hunks={0: Hunk("self", offset=0)}).build()
        assert RealMachine().read_disc_identity(write(tmp_path, looping, "S.chd")) == DiscIdentityResult(
            DISC_UNREADABLE, chd=facts
        )
        monkeypatch.setattr(psx_chd, "lzma", None)
        assert RealMachine().read_disc_identity(write(tmp_path, Chd(TRACK).build(), "C.chd")) == DiscIdentityResult(
            DISC_CODEC_UNAVAILABLE, chd=facts
        )

    def test_a_parent_unfound_is_only_for_a_header_naming_one(self):
        facts = psx_chd.ChdFacts(("cdlz",), False)
        with pytest.raises(ValueError, match="names one"):
            DiscIdentityResult(DISC_PARENT_NOT_FOUND, chd=facts)
        with pytest.raises(ValueError, match="names one"):
            DiscIdentityResult(DISC_PARENT_NOT_FOUND)


GAME = "/roms/psx/Game.chd"
FACTS = {"license": US.decode("latin-1"), "system_cnf": SYSTEM_CNF.decode("latin-1"), "boot_found": True, "edc_valid": True}


class TestTheFixtureModelsTheAnswer:
    """FixtureMachine.read_disc_identity over a CHD — the header's facts and the read's outcome, as declared."""

    def _machine(self, spec) -> FixtureMachine:
        return FixtureMachine({GAME: "x"}, disc_identities={GAME: spec})

    def test_a_declared_chd_answers_its_facts_and_identity(self):
        spec = {"codecs": ["cdlz", "cdzl"], "requires_parent": False, "identity": FACTS}
        result = self._machine(spec).read_disc_identity(GAME)
        assert result.status == DISC_OK
        assert result.chd == psx_chd.ChdFacts(("cdlz", "cdzl"), False)
        assert result.identity is not None
        assert result.identity.license.startswith(US)

    @pytest.mark.parametrize(
        ("identity", "status"),
        [("codec-unavailable", DISC_CODEC_UNAVAILABLE), ("parent-not-found", DISC_PARENT_NOT_FOUND)],
    )
    def test_a_declared_read_state(self, identity, status):
        spec = {"codecs": ["cdzs"], "requires_parent": True, "identity": identity}
        assert self._machine(spec).read_disc_identity(GAME) == DiscIdentityResult(
            status, chd=psx_chd.ChdFacts(("cdzs",), True)
        )

    def test_a_header_not_read(self):
        assert self._machine("format-not-read").read_disc_identity(GAME) == DiscIdentityResult(DISC_FORMAT_NOT_READ)

    @pytest.mark.parametrize(
        ("spec", "message"),
        [
            ("missing", "a state must be one of"),
            (FACTS, "a CHD is a state or an object"),
            ({"codecs": ["cdlzx"], "requires_parent": False, "identity": FACTS}, "four tags"),
            ({"codecs": ["cdlz"] * 5, "requires_parent": False, "identity": FACTS}, "four tags"),
            ({"codecs": [], "requires_parent": "no", "identity": FACTS}, "requires_parent must be a boolean"),
            ({"codecs": [], "requires_parent": False, "identity": "missing"}, "a state must be one of"),
            ({"codecs": [], "requires_parent": False, "identity": "parent-not-found"}, "only a CHD that names"),
        ],
        ids=["missing", "bare-facts", "codec-tag", "codec-count", "parent-flag", "read-missing", "unnamed-parent"],
    )
    def test_a_declaration_no_real_read_could_make_is_refused(self, spec, message):
        with pytest.raises(ValueError, match=message):
            self._machine(spec)
